"""火山引擎大模型流式语音识别（SAUC bigmodel）WebSocket 客户端。

官方文档：https://www.volcengine.com/docs/6561/1354869

要点（以官方文档为准）：

- 接口地址（双向流式优化版，官方推荐）：
  ``wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async``
- 鉴权（新版控制台）：HTTP 头 ``X-Api-Key`` + ``X-Api-Resource-Id``
  （如 ``volc.bigasr.sauc.duration``）+ ``X-Api-Request-Id``（随机 UUID）
- 协议：4 字节自描述 header + payload size(4B 大端) + payload 的二进制帧，
  payload 统一用 gzip 压缩（服务端沿用客户端的压缩方式）
- 首包 full client request（JSON），随后 audio only request（裸音频），
  最后一包用 message type specific flags=0b0010 标记
- 下行 full server response 的 ``result.utterances`` 中
  ``definite: true`` 表示句子级定稿（需请求参数 ``show_utterances: true``；
  定稿时机由 ``end_window_size``（静音判停）控制）
"""

from __future__ import annotations

import asyncio
import gzip
import json
import struct
import uuid
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.client import connect

from .base import AsrEvent, OnAsrEvent

ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"

# ---- 协议常量（见官方文档「WebSocket 二进制协议」一节） ----
_FULL_CLIENT_REQUEST = 0b0001
_AUDIO_ONLY_REQUEST = 0b0010
_FULL_SERVER_RESPONSE = 0b1001
_SERVER_ERROR = 0b1111

_NO_SERIALIZATION = 0b0000
_JSON = 0b0001

_NO_COMPRESSION = 0b0000
_GZIP = 0b0001

# message type specific flags
_FLAG_NONE = 0b0000  # header 后无 sequence
_FLAG_POS_SEQUENCE = 0b0001  # header 后 4 字节为正 sequence
_FLAG_LAST_NO_SEQUENCE = 0b0010  # 最后一包（负包），不带 sequence
_FLAG_NEG_SEQUENCE = 0b0011  # header 后 4 字节为负 sequence（最后一包）

_HEADER = 0x11  # version=0b0001, header size=0b0001 (4 字节)


@dataclass(frozen=True)
class _Frame:
    """解码后的下行帧。"""

    message_type: int
    is_last: bool
    payload: bytes  # 已按帧声明的压缩方式解压
    error_code: int | None = None


def _encode_frame(
    *, message_type: int, flags: int, serialization: int, payload: bytes
) -> bytes:
    """编码一个上行帧，payload 一律 gzip 压缩。"""
    compressed = gzip.compress(payload)
    header = bytes(
        [_HEADER, (message_type << 4) | flags, (serialization << 4) | _GZIP, 0]
    )
    return header + struct.pack(">I", len(compressed)) + compressed


def _decode_frame(data: bytes) -> _Frame:
    """解码一个下行帧。"""
    if len(data) < 8 or data[0] != _HEADER:
        raise ValueError("无效的火山帧头")
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    compression = data[2] & 0x0F
    header_size = (data[0] & 0x0F) * 4
    offset = header_size

    if message_type == _SERVER_ERROR:
        error_code, offset = _read_u32(data, offset)
        size, offset = _read_u32(data, offset)
        message = data[offset : offset + size]
        if compression == _GZIP:
            message = gzip.decompress(message)
        return _Frame(
            message_type=message_type,
            is_last=True,
            payload=message,
            error_code=error_code,
        )

    is_last = False
    if flags & _FLAG_POS_SEQUENCE:
        sequence, offset = _read_i32(data, offset)
        is_last = sequence < 0  # 负 sequence 表示最后一包
    if flags == _FLAG_LAST_NO_SEQUENCE:
        is_last = True
    size, offset = _read_u32(data, offset)
    payload = data[offset : offset + size]
    if compression == _GZIP:
        payload = gzip.decompress(payload)
    return _Frame(message_type=message_type, is_last=is_last, payload=payload)


def _read_u32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ValueError("火山帧 optional 字段不完整")
    return struct.unpack_from(">I", data, offset)[0], offset + 4


def _read_i32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ValueError("火山帧 optional 字段不完整")
    return struct.unpack_from(">i", data, offset)[0], offset + 4


class VolcAsrProvider:
    """火山引擎 SAUC bigmodel 流式语音识别。"""

    def __init__(
        self,
        *,
        on_event: OnAsrEvent,
        api_key: str,
        resource_id: str = "volc.bigasr.sauc.duration",
        model_name: str = "bigmodel",
        endpoint: str = ENDPOINT,
        # 静音判停时长（ms）：超过即输出 definite 分句，官方默认 800
        end_window_size: int = 800,
    ) -> None:
        self._on_event = on_event
        self._api_key = api_key
        self._resource_id = resource_id
        self._model_name = model_name
        self._endpoint = endpoint
        self._end_window_size = end_window_size
        self._ws: Any = None
        self._recv_task: asyncio.Task | None = None
        self._definite_count = 0  # 已发射的定稿分句数（utterances 为全量列表）
        self._closed = False

    async def connect(self) -> None:
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        self._ws = await connect(self._endpoint, additional_headers=headers)
        await self._ws.send(self._full_client_request())
        self._recv_task = asyncio.create_task(self._recv_loop())

    def _full_client_request(self) -> bytes:
        # 注意：language 参数仅流式输入模式（bigmodel_nostream）支持，双向流式不支持，
        # 这里不传，由模型自动识别中英文等语种
        payload = {
            "user": {"uid": "subtitles"},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": self._model_name,
                "enable_punc": True,
                "enable_itn": True,
                "show_utterances": True,
                "result_type": "full",
                # 静音判停：尽早拿到 definite 分句，满足字幕实时性要求
                "end_window_size": self._end_window_size,
                # 官方建议配合 end_window_size 使用，过短的音频不做判停
                "force_to_speech_time": 1000,
            },
        }
        return _encode_frame(
            message_type=_FULL_CLIENT_REQUEST,
            flags=_FLAG_NONE,
            serialization=_JSON,
            payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    async def send_audio(self, pcm16: bytes) -> None:
        if self._ws is None or self._closed:
            return
        await self._ws.send(
            _encode_frame(
                message_type=_AUDIO_ONLY_REQUEST,
                flags=_FLAG_NONE,
                serialization=_NO_SERIALIZATION,
                payload=pcm16,
            )
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._ws is not None:
                # 发送最后一包（空音频 + 负包标记），等一小会儿拿最终识别结果
                await self._ws.send(
                    _encode_frame(
                        message_type=_AUDIO_ONLY_REQUEST,
                        flags=_FLAG_LAST_NO_SEQUENCE,
                        serialization=_NO_SERIALIZATION,
                        payload=b"",
                    )
                )
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
                if isinstance(message, str):
                    continue  # 协议下行均为二进制帧
                try:
                    frame = _decode_frame(message)
                except (ValueError, struct.error) as e:
                    self._on_event(AsrEvent(kind="error", text=f"火山帧解码失败: {e}"))
                    continue
                if frame.message_type == _SERVER_ERROR:
                    self._on_event(
                        AsrEvent(
                            kind="error",
                            text=(
                                f"火山 ASR 服务端错误 (code={frame.error_code}): "
                                f"{frame.payload.decode('utf-8', errors='replace')}"
                            ),
                        )
                    )
                    continue
                if frame.message_type == _FULL_SERVER_RESPONSE:
                    self._handle_response(frame.payload)
        except asyncio.CancelledError:
            return
        except Exception as e:
            if not self._closed:
                self._on_event(AsrEvent(kind="error", text=f"火山 ASR 连接中断: {e}"))

    def _handle_response(self, payload_bytes: bytes) -> None:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        result = payload.get("result")
        if not isinstance(result, dict):
            return
        utterances = result.get("utterances") or []
        if utterances:
            # utterances 为全量列表；definite=true 的分句按序定稿，只发射一次
            while self._definite_count < len(utterances) and utterances[
                self._definite_count
            ].get("definite"):
                text = str(utterances[self._definite_count].get("text", "")).strip()
                if text:
                    self._on_event(AsrEvent(kind="sentence", text=text))
                self._definite_count += 1
            partial = "".join(
                str(u.get("text", "")) for u in utterances[self._definite_count :]
            ).strip()
            if partial:
                self._on_event(AsrEvent(kind="partial", text=partial))
        else:
            # 未开 show_utterances 时兜底：整段文本作为 partial，
            # 断句完全交给 segmenter 的超时兜底
            text = str(result.get("text", "")).strip()
            if text:
                self._on_event(AsrEvent(kind="partial", text=text))
