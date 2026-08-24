"""编排层：capture → asr → segmenter → translator → 对外事件。

Engine 的 start/stop 与所有事件回调都运行在同一个 asyncio 事件循环上
（由调用方提供，GUI 场景下跑在独立线程）。对外暴露四个回调：

- ``on_partial(text)``：当前字幕行更新
- ``on_sentence(text)``：一句定稿
- ``on_translation(sentence, translation)``：某句的译文到达
- ``on_error(message)``：错误提示（不抛出，方便 GUI 直接展示）
"""

from __future__ import annotations

import asyncio
from typing import Callable

from .asr.base import AsrEvent
from .config import (
    Config,
    resolve_aliyun_api_key,
    resolve_translate_api_key,
    resolve_translate_base_url,
    resolve_volc_api_key,
)
from .segmenter import PartialUpdate, Segmenter, SentenceDone
from .translator import Translator

OnText = Callable[[str], None]
OnTranslation = Callable[[str, str], None]


def create_provider(config: Config, on_event) -> object:
    """按配置创建 ASR 厂商适配器；缺 Key 时抛出 ValueError 由上层提示。"""
    if config.asr_provider == "volc":
        from .asr.volc import VolcAsrProvider

        api_key = resolve_volc_api_key(config)
        if not api_key:
            raise ValueError(
                "未配置火山引擎 API Key：请编辑 ~/.subtitles/config.json "
                "的 volc.api_key，或设置环境变量 VOLC_API_KEY"
            )
        return VolcAsrProvider(
            on_event=on_event,
            api_key=api_key,
            resource_id=config.volc.resource_id,
        )
    if config.asr_provider == "aliyun":
        from .asr.aliyun import AliyunAsrProvider

        api_key = resolve_aliyun_api_key(config)
        if not api_key:
            raise ValueError(
                "未配置阿里云百炼 API Key：请编辑 ~/.subtitles/config.json "
                "的 aliyun.api_key，或设置环境变量 DASHSCOPE_API_KEY"
            )
        return AliyunAsrProvider(
            on_event=on_event,
            api_key=api_key,
            model=config.aliyun.model,
            language=config.language,
        )
    raise ValueError(f"未知的 ASR 厂商: {config.asr_provider!r}")


class Engine:
    def __init__(self, config: Config) -> None:
        self._config = config
        self.on_partial: OnText = lambda text: None
        self.on_sentence: OnText = lambda text: None
        self.on_translation: OnTranslation = lambda sentence, translation: None
        self.on_error: OnText = lambda message: None

        self._segmenter = Segmenter(max_sentence_seconds=config.max_sentence_seconds)
        self._provider = None
        self._capture = None
        self._translator: Translator | None = None
        self._prev_sentence = ""  # 上一句原文，作为翻译上下文
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        # ScreenCaptureKit 仅 macOS 可用，且体积大，惰性导入
        from .capture.system_audio import SystemAudioCapture

        self._provider = create_provider(self._config, self._on_asr_event)
        self._translator = Translator(
            self._config.translate,
            api_key=resolve_translate_api_key(self._config),
            base_url=resolve_translate_base_url(self._config),
            source_language=self._config.language,
        )
        await self._provider.connect()

        loop = asyncio.get_running_loop()
        self._capture = SystemAudioCapture(
            on_pcm=self._on_pcm, on_error=self.on_error
        )
        self._capture.start(loop)
        self._running = True

    async def stop(self) -> None:
        self._running = False
        if self._capture is not None:
            try:
                await asyncio.to_thread(self._capture.stop)
            except Exception as e:  # noqa: BLE001
                self.on_error(f"停止采集失败: {e}")
            self._capture = None
        if self._provider is not None:
            try:
                await self._provider.close()
            except Exception as e:  # noqa: BLE001
                self.on_error(f"关闭 ASR 连接失败: {e}")
            self._provider = None
        # 冲刷断句器：当前行未收尾的内容强制定稿
        for event in self._segmenter.flush():
            self._dispatch(event)

    # ---- 内部：均在 asyncio 事件循环线程 ----

    def _on_pcm(self, pcm16: bytes) -> None:
        provider = self._provider
        if self._running and provider is not None:
            asyncio.ensure_future(provider.send_audio(pcm16))

    def _on_asr_event(self, event: AsrEvent) -> None:
        if event.kind == "error":
            self.on_error(event.text)
            return
        if event.kind == "partial":
            for out in self._segmenter.on_partial(event.text):
                self._dispatch(out)
        elif event.kind == "sentence":
            for out in self._segmenter.on_sentence(event.text):
                self._dispatch(out)

    def _dispatch(self, event) -> None:
        if isinstance(event, PartialUpdate):
            self.on_partial(event.text)
        elif isinstance(event, SentenceDone):
            self.on_sentence(event.text)
            if self._translator is not None and self._translator.available:
                # 上下文是上一句原文；须在更新 _prev_sentence 之前取出
                asyncio.ensure_future(self._translate(event.text, self._prev_sentence))
            self._prev_sentence = event.text

    async def _translate(self, sentence: str, context: str) -> None:
        assert self._translator is not None
        translation = await self._translator.translate(sentence, context=context)
        if translation:
            self.on_translation(sentence, translation)
