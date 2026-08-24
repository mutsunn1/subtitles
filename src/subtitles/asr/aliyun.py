"""阿里云百炼实时语音识别（DashScope WebSocket）客户端。

官方文档：

- 实时语音识别用户指南：https://help.aliyun.com/zh/model-studio/real-time-speech-recognition-user-guide
- WebSocket API：https://help.aliyun.com/zh/model-studio/websocket-for-paraformer-real-time-service
- 服务端事件：https://help.aliyun.com/zh/model-studio/paraformer-server-events

要点（以官方文档为准）：

- 接口地址：``wss://dashscope.aliyuncs.com/api-ws/v1/inference``
- 鉴权：HTTP 头 ``Authorization: Bearer <API-Key>``
- 协议为 JSON 文本帧 + 二进制音频帧：连接后发送 ``run-task`` 指令，
  收到 ``task-started`` 后以二进制帧直接发送音频，结束时发送 ``finish-task``
- 下行 ``result-generated`` 事件：``payload.output.sentence.sentence_end``
  为 true 表示句子级定稿，false 为中间结果
- paraformer 系列与 qwen-audio-3.0-asr-flash-streaming / Fun-ASR-Realtime
  共用同一套 run-task 协议，仅 ``model`` 与 ``parameters`` 取值不同
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from websockets.asyncio.client import connect

from .base import AsrEvent, OnAsrEvent

ENDPOINT = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

# 识别语言（config 的 zh-CN / en-US 等）-> language_hints 取值
_LANGUAGE_HINTS = {
    "zh": "zh",
    "en": "en",
    "ja": "ja",
    "yue": "yue",
    "ko": "ko",
    "de": "de",
    "fr": "fr",
    "ru": "ru",
}


class AliyunAsrProvider:
    """阿里云百炼流式语音识别（paraformer / qwen-audio 系列）。"""

    def __init__(
        self,
        *,
        on_event: OnAsrEvent,
        api_key: str,
        model: str = "paraformer-realtime-v2",
        language: str = "zh-CN",
        endpoint: str = ENDPOINT,
    ) -> None:
        self._on_event = on_event
        self._api_key = api_key
        self._model = model
        self._language = language
        self._endpoint = endpoint
        self._ws: Any = None
        self._task_id = ""
        self._started: asyncio.Event | None = None
        self._recv_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        self._ws = await connect(
            self._endpoint,
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
        )
        self._task_id = uuid.uuid4().hex
        self._started = asyncio.Event()
        self._recv_task = asyncio.create_task(self._recv_loop())
        await self._send_command("run-task", self._run_task_payload())
        # 等 task-started 后再开始送音频（官方要求的发送时机）
        await asyncio.wait_for(self._started.wait(), timeout=10.0)

    def _run_task_payload(self) -> dict:
        # qwen-audio / funasr 系列与 paraformer 共用协议，但支持的 parameters 不同：
        # qwen-audio 自动语种检测、智能过滤非人声，仅传 format/sample_rate；
        # paraformer 系列额外支持 heartbeat / disfluency_removal_enabled / language_hints
        parameters: dict[str, Any] = {
            "format": "pcm",
            "sample_rate": 16000,
        }
        if self._model.startswith("paraformer"):
            # 静音期间保活，避免服务端超时断连
            parameters["heartbeat"] = True
            # 过滤语气词，字幕更干净
            parameters["disfluency_removal_enabled"] = True
            hint = _LANGUAGE_HINTS.get(self._language.split("-")[0].lower())
            if hint:
                parameters["language_hints"] = [hint]
        # 其余（VAD 断句 / 标点 / ITN）沿用官方默认值
        return {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": self._model,
            "parameters": parameters,
            "input": {},
        }

    async def _send_command(self, action: str, payload: dict) -> None:
        message = {
            "header": {
                "action": action,
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": payload,
        }
        await self._ws.send(json.dumps(message, ensure_ascii=False))

    async def send_audio(self, pcm16: bytes) -> None:
        if self._ws is None or self._closed or not (
            self._started and self._started.is_set()
        ):
            return
        # 音频以二进制帧直接发送
        await self._ws.send(pcm16)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._ws is not None and self._started and self._started.is_set():
                await self._send_command("finish-task", {"input": {}})
                await asyncio.sleep(1.0)
        except Exception:
            pass
        if self._recv_task is not None:
            self._recv_task.cancel()
            self._recv_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _recv_loop(self) -> None:
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    continue  # 协议下行为 JSON 文本帧
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                header = data.get("header") or {}
                event = header.get("event")
                if event == "task-started":
                    if self._started is not None:
                        self._started.set()
                elif event == "result-generated":
                    self._handle_result(data.get("payload") or {})
                elif event == "task-failed":
                    code = header.get("error_code", "")
                    msg = header.get("error_message", "")
                    self._on_event(
                        AsrEvent(kind="error", text=f"百炼 ASR 任务失败 ({code}): {msg}")
                    )
                elif event == "task-finished":
                    return
        except asyncio.CancelledError:
            return
        except Exception as e:
            if not self._closed:
                self._on_event(AsrEvent(kind="error", text=f"百炼 ASR 连接中断: {e}"))

    def _handle_result(self, payload: dict) -> None:
        sentence = (payload.get("output") or {}).get("sentence") or {}
        if sentence.get("heartbeat"):
            return  # 心跳包，官方说明可跳过
        text = str(sentence.get("text", "")).strip()
        if not text:
            return
        if sentence.get("sentence_end"):
            self._on_event(AsrEvent(kind="sentence", text=text))
        else:
            self._on_event(AsrEvent(kind="partial", text=text))
