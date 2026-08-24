"""智能断句：把 ASR 事件流整理成字幕行。

输入为归一化 ASR 事件（partial / sentence），输出两类事件：

- :class:`PartialUpdate`：当前字幕行内容更新（覆盖式，非追加）
- :class:`SentenceDone`：一句完成，可入历史并触发翻译

断句以厂商返回的句子级定稿事件（sentence）为主；若长时间等不到定稿
（例如识别流不返回 definite/sentence_end），按 ``max_sentence_seconds``
强制断开当前行，保证字幕不会无限拉长。

纯逻辑模块，不依赖 asyncio/Qt；时钟可注入，便于测试。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Union


@dataclass(frozen=True)
class PartialUpdate:
    """当前字幕行内容更新。"""

    text: str


@dataclass(frozen=True)
class SentenceDone:
    """一句完成。"""

    text: str
    forced: bool = False  # True 表示由超时兜底强制断开，而非厂商定稿


SegmenterEvent = Union[PartialUpdate, SentenceDone]


class Segmenter:
    def __init__(
        self,
        max_sentence_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_seconds = max_sentence_seconds
        self._clock = clock
        self._current = ""  # 当前行最新 partial 文本
        self._started_at: float | None = None  # 当前行首个 partial 的时刻

    @property
    def current_text(self) -> str:
        return self._current

    def on_partial(self, text: str) -> list[SegmenterEvent]:
        """处理一条 ASR 中间结果。"""
        text = text.strip()
        if not text:
            return []
        events: list[SegmenterEvent] = []
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
        elif now - self._started_at >= self._max_seconds:
            # 超时兜底：强制断开当前行，新 partial 另起一行
            events.append(SentenceDone(text=self._current, forced=True))
            self._current = ""
            self._started_at = now
        self._current = text
        events.append(PartialUpdate(text=text))
        return events

    def on_sentence(self, text: str) -> list[SegmenterEvent]:
        """处理一条 ASR 句子级定稿。"""
        text = text.strip()
        self._current = ""
        self._started_at = None
        return [SentenceDone(text=text)] if text else []

    def flush(self) -> list[SegmenterEvent]:
        """停止时冲刷：当前行若有内容则强制收尾。"""
        if not self._current:
            return []
        text = self._current
        self._current = ""
        self._started_at = None
        return [SentenceDone(text=text, forced=True)]
