"""ASR 厂商适配层契约。

每厂商实现一个 :class:`AsrProvider`：connect/send_audio/close 三个生命周期方法，
识别结果经构造时注入的 ``on_event`` 回调以归一化事件（:class:`AsrEvent`）抛出：

- ``partial``：中间结果，实时更新当前字幕行（文本会被后续 partial 覆盖）
- ``sentence``：句子级定稿（火山的 ``definite`` 分句 / 百炼的 ``sentence_end``）
- ``error``：错误信息（连接失败、鉴权失败、服务端错误帧等）

所有回调均在 provider 所属的 asyncio 事件循环线程内触发。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Protocol

AsrEventKind = Literal["partial", "sentence", "error"]


@dataclass(frozen=True)
class AsrEvent:
    """归一化后的 ASR 事件。"""

    kind: AsrEventKind
    text: str = ""


# 事件回调：在 asyncio 事件循环线程内同步调用，实现者需自行保证轻量
OnAsrEvent = Callable[[AsrEvent], None]


class AsrProvider(Protocol):
    """流式 ASR 厂商适配器契约。实现者为协议各异的各家厂商。"""

    def __init__(self, *, on_event: OnAsrEvent, **kwargs: object) -> None: ...

    async def connect(self) -> None:
        """建立连接并完成会话初始化；失败时抛出异常或回调 error 事件。"""
        ...

    async def send_audio(self, pcm16: bytes) -> None:
        """发送一段 16kHz 单声道 PCM16 音频。"""
        ...

    async def close(self) -> None:
        """收尾（发送最后一包/结束指令）并关闭连接；幂等。"""
        ...
