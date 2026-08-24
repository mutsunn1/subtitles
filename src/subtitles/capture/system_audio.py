"""ScreenCaptureKit 系统音频采集（macOS 13+）。

只采集系统输出音频（不含本进程声音），统一重采样为 16kHz 单声道 PCM16，
经 ``on_pcm`` 回调投递到 asyncio 事件循环。

线程模型：ScreenCaptureKit 的回调发生在系统派发队列线程，投递 asyncio
一律用 ``loop.call_soon_threadsafe``。采集的创建与销毁在本类私有的
工作线程中完成，不阻塞事件循环。

权限：需要「系统设置 → 隐私与安全性 → 屏幕录制」授权（系统音频采集与
屏幕录制共用该权限），未授权时拿不到可采集的显示器。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Callable

import numpy as np

# CoreAudioTypes.h 中的 AudioStreamBasicDescription 标志位（硬编码以免多引依赖）
_FLAG_IS_FLOAT = 1 << 0
_FLAG_IS_SIGNED_INTEGER = 1 << 2
_FLAG_IS_NON_INTERLEAVED = 1 << 5

TARGET_SAMPLE_RATE = 16000

OnPcm = Callable[[bytes], None]
OnError = Callable[[str], None]


def _resample_to_16k_mono(sample_buffer) -> bytes | None:
    """把一路 CMSampleBuffer 的 LPCM 音频转成 16kHz 单声道 PCM16。"""
    import CoreMedia

    block = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
    if block is None:
        return None
    length = CoreMedia.CMBlockBufferGetDataLength(block)
    if length <= 0:
        return None
    raw = bytearray(length)
    status = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, raw)
    if status != 0:
        return None

    fmt = CoreMedia.CMSampleBufferGetFormatDescription(sample_buffer)
    asbd = CoreMedia.CMAudioFormatDescriptionGetStreamBasicDescription(fmt)
    if asbd is None:
        return None
    sample_rate = int(asbd.mSampleRate) or 48000
    channels = max(int(asbd.mChannelsPerFrame), 1)
    flags = asbd.mFormatFlags

    dtype = np.float32 if flags & _FLAG_IS_FLOAT else np.int16
    samples = np.frombuffer(bytes(raw), dtype=dtype)
    frames = len(samples) // channels
    if frames == 0:
        return None
    samples = samples[: frames * channels]
    if flags & _FLAG_IS_NON_INTERLEAVED:
        # 非交错：数据按声道分段存放 [ch0][ch1]...
        per_channel = samples.reshape(channels, frames)
    else:
        per_channel = samples.reshape(frames, channels).T
    mono = per_channel.astype(np.float64).mean(axis=0)
    if not flags & _FLAG_IS_FLOAT:
        mono /= float(1 << 15)

    if sample_rate != TARGET_SAMPLE_RATE:
        n_out = int(len(mono) * TARGET_SAMPLE_RATE / sample_rate)
        if n_out == 0:
            return None
        mono = np.interp(
            np.linspace(0.0, len(mono) - 1, n_out), np.arange(len(mono)), mono
        )
    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16)
    return pcm.tobytes()


class SystemAudioCapture:
    """系统音频采集器。start/stop 线程安全，可在任意线程调用。"""

    def __init__(self, on_pcm: OnPcm, on_error: OnError) -> None:
        self._on_pcm = on_pcm
        self._on_error = on_error
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stream = None
        self._output = None
        self._lock = threading.Lock()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """在后台线程中完成采集链路搭建；失败经 on_error 上报。"""
        with self._lock:
            if self._thread is not None:
                return
            self._loop = loop
            self._thread = threading.Thread(
                target=self._run, name="subtitles-capture", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread, stream, self._thread = self._thread, self._stream, None
            self._output = None
        if stream is not None:
            done = threading.Event()
            stream.stopCaptureWithCompletionHandler_(lambda error: done.set())
            done.wait(timeout=5.0)
        if thread is not None:
            thread.join(timeout=5.0)

    # ---- 以下均运行在工作线程 / 系统派发队列 ----

    def _run(self) -> None:
        try:
            self._setup()
        except Exception as e:  # noqa: BLE001 - 统一上报给 UI
            self._emit_error(f"系统音频采集启动失败: {e}")

    def _setup(self) -> None:
        import ScreenCaptureKit

        content = self._shareable_content()
        displays = content.displays()
        if not displays:
            raise RuntimeError(
                "未获取到可采集的显示器，请在「系统设置 → 隐私与安全性 → "
                "屏幕录制」中授权后重试"
            )
        content_filter = (
            ScreenCaptureKit.SCContentFilter.alloc()
            .initWithDisplay_excludingApplications_exceptingWindows_(displays[0], [], [])
        )
        stream_config = ScreenCaptureKit.SCStreamConfiguration.alloc().init()
        # 视频只要最小画面（字幕工具不需要画面），音频 48kHz 双声道
        stream_config.setWidth_(2)
        stream_config.setHeight_(2)
        stream_config.setCapturesAudio_(True)
        stream_config.setExcludesCurrentProcessAudio_(True)
        stream_config.setSampleRate_(48000)
        stream_config.setChannelCount_(2)

        self._output = _get_output_class().alloc().initWithCallback_(self._handle_buffer)
        self._stream = ScreenCaptureKit.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, stream_config, None
        )
        ok, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output, ScreenCaptureKit.SCStreamOutputTypeAudio, None, None
        )
        if not ok:
            raise RuntimeError(f"addStreamOutput 失败: {error}")

        started = threading.Event()
        holder: dict[str, object] = {}

        def completion(error) -> None:
            holder["error"] = error
            started.set()

        self._stream.startCaptureWithCompletionHandler_(completion)
        if not started.wait(timeout=10.0):
            raise RuntimeError("startCapture 超时")
        if holder.get("error") is not None:
            raise RuntimeError(f"startCapture 失败: {holder['error']}")

    def _shareable_content(self):
        import ScreenCaptureKit

        done = threading.Event()
        holder: dict[str, object] = {}

        def completion(content, error) -> None:
            holder["content"] = content
            holder["error"] = error
            done.set()

        ScreenCaptureKit.SCShareableContent.getShareableContentWithCompletionHandler_(
            completion
        )
        if not done.wait(timeout=10.0):
            raise RuntimeError("获取可采集内容超时")
        if holder.get("error") is not None:
            raise RuntimeError(f"获取可采集内容失败: {holder['error']}")
        return holder["content"]

    def _handle_buffer(self, sample_buffer) -> None:
        """采集回调（系统派发队列线程）。"""
        try:
            pcm = _resample_to_16k_mono(sample_buffer)
        except Exception:  # noqa: BLE001 - 单帧失败不影响采集
            return
        if not pcm:
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._on_pcm, pcm)

    def _emit_error(self, message: str) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._on_error, message)


def _objc_stream_output_class():
    import objc
    from Foundation import NSObject

    class _AudioOutput(NSObject):
        """SCStreamOutput 协议实现：把音频帧转发给 Python 回调。"""

        def initWithCallback_(self, callback):
            self = objc.super(_AudioOutput, self).init()
            if self is None:
                return None
            self._callback = callback
            return self

        def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, kind):
            import ScreenCaptureKit

            if kind == ScreenCaptureKit.SCStreamOutputTypeAudio:
                self._callback(sample_buffer)

    return _AudioOutput


# 惰性定义 PyObjC 类（模块 import 时不依赖 PyObjC 运行时）
_AudioOutput = None


def _get_output_class():
    global _AudioOutput
    if _AudioOutput is None:
        _AudioOutput = _objc_stream_output_class()
    return _AudioOutput
