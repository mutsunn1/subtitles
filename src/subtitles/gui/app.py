"""GUI 主入口：控制条 + 悬浮字幕窗 + 历史窗口。

Qt 与 asyncio 桥接：asyncio 事件循环跑在独立线程，Engine 的所有事件
经 Qt Signal 转发回 GUI 线程更新界面。
"""

from __future__ import annotations

import asyncio
import sys
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    Config,
    load_config,
    resolve_aliyun_api_key,
    resolve_translate_api_key,
    resolve_volc_api_key,
    save_config,
)
from ..engine import Engine
from .history import HistoryWindow
from .overlay import SubtitleOverlay

_PROVIDERS = [("volc", "火山引擎"), ("aliyun", "阿里云百炼")]
_LANGUAGES = [
    ("zh-CN", "中文"),
    ("en-US", "English"),
    ("ja-JP", "日本語"),
    ("ko-KR", "한국어"),
    ("auto", "自动"),
]


class _Bridge(QObject):
    """Engine 事件 -> GUI 线程的 Qt 信号桥。"""

    sig_partial = Signal(str)
    sig_sentence = Signal(str)
    sig_translation = Signal(str, str)
    sig_error = Signal(str)
    sig_stopped = Signal()


class ControlWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("subtitles 控制台")
        self._config = load_config()

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, name="subtitles-asyncio", daemon=True
        )
        self._loop_thread.start()

        self._engine: Engine | None = None
        self._overlay = SubtitleOverlay()
        self._history = HistoryWindow()

        self._bridge = _Bridge()
        self._bridge.sig_partial.connect(self._overlay.show_partial)
        self._bridge.sig_sentence.connect(self._on_sentence)
        self._bridge.sig_translation.connect(self._on_translation)
        self._bridge.sig_error.connect(self._on_error)
        self._bridge.sig_stopped.connect(self._on_stopped)

        self._build_ui()
        self._load_ui_state()

    # ---- UI ----

    def _build_ui(self) -> None:
        self.provider_combo = QComboBox()
        for value, label in _PROVIDERS:
            self.provider_combo.addItem(label, value)

        self.language_combo = QComboBox()
        for value, label in _LANGUAGES:
            self.language_combo.addItem(label, value)

        self.translate_check = QCheckBox("翻译")
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.target_combo.addItems(["中文", "English", "日本語", "한국어"])

        self.toggle_button = QPushButton("开始")
        self.toggle_button.clicked.connect(self._on_toggle)
        self.history_button = QPushButton("历史")
        self.history_button.clicked.connect(self._show_history)

        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #888888;")

        row = QHBoxLayout()
        row.addWidget(QLabel("ASR:"))
        row.addWidget(self.provider_combo)
        row.addWidget(QLabel("语言:"))
        row.addWidget(self.language_combo)
        row.addWidget(self.translate_check)
        row.addWidget(self.target_combo)
        row.addWidget(self.toggle_button)
        row.addWidget(self.history_button)

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.status_label)

    def _load_ui_state(self) -> None:
        cfg = self._config
        index = self.provider_combo.findData(cfg.asr_provider)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)
        index = self.language_combo.findData(cfg.language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        self.translate_check.setChecked(cfg.translate.enabled)
        self.target_combo.setCurrentText(cfg.translate.target_language)

    def _apply_ui_state(self) -> Config:
        cfg = self._config
        cfg.asr_provider = self.provider_combo.currentData()
        cfg.language = self.language_combo.currentData()
        cfg.translate.enabled = self.translate_check.isChecked()
        cfg.translate.target_language = self.target_combo.currentText().strip() or "中文"
        try:
            save_config(cfg)
        except OSError:
            pass  # 配置保存失败不影响本次运行
        return cfg

    # ---- 开始 / 停止 ----

    def _on_toggle(self) -> None:
        if self._engine is not None:
            self._request_stop()
        else:
            self._request_start()

    def _request_start(self) -> None:
        cfg = self._apply_ui_state()
        missing = self._missing_key_message(cfg)
        if missing:
            QMessageBox.warning(self, "缺少 API Key", missing)
            return
        self.status_label.setText("正在连接…")
        self.toggle_button.setEnabled(False)
        self._history.clear_history()

        engine = Engine(cfg)
        engine.on_partial = self._bridge.sig_partial.emit
        engine.on_sentence = self._bridge.sig_sentence.emit
        engine.on_translation = self._bridge.sig_translation.emit
        engine.on_error = self._bridge.sig_error.emit
        self._engine = engine
        asyncio.run_coroutine_threadsafe(self._start_engine(engine), self._loop)

    async def _start_engine(self, engine: Engine) -> None:
        try:
            await engine.start()
        except Exception as e:  # noqa: BLE001 - 统一以 error 事件提示
            self._engine = None
            self._bridge.sig_error.emit(f"启动失败: {e}")
            self._bridge.sig_stopped.emit()
            return
        self._bridge.sig_stopped.emit()  # 连接就绪，复用该信号刷新按钮状态

    def _request_stop(self) -> None:
        engine = self._engine
        if engine is None:
            return
        self.status_label.setText("正在停止…")
        self.toggle_button.setEnabled(False)
        asyncio.run_coroutine_threadsafe(self._stop_engine(engine), self._loop)

    async def _stop_engine(self, engine: Engine) -> None:
        try:
            await engine.stop()
        finally:
            self._engine = None
            self._bridge.sig_stopped.emit()

    def _missing_key_message(self, cfg: Config) -> str:
        if cfg.asr_provider == "volc" and not resolve_volc_api_key(cfg):
            return (
                "未找到火山引擎 API Key。\n\n"
                "请编辑 ~/.subtitles/config.json 填入 volc.api_key，"
                "或设置环境变量 VOLC_API_KEY 后重启。\n"
                "申请入口：https://console.volcengine.com/speech"
            )
        if cfg.asr_provider == "aliyun" and not resolve_aliyun_api_key(cfg):
            return (
                "未找到阿里云百炼 API Key。\n\n"
                "请编辑 ~/.subtitles/config.json 填入 aliyun.api_key，"
                "或设置环境变量 DASHSCOPE_API_KEY 后重启。\n"
                "申请入口：https://bailian.console.aliyun.com/"
            )
        if (
            cfg.translate.enabled
            and not resolve_translate_api_key(cfg)
            or cfg.translate.enabled
            and not cfg.translate.model.strip()
        ):
            # 翻译为可选能力：仅提示，不阻塞启动
            self.status_label.setText("提示：未配置翻译服务，仅显示原文")
        return ""

    # ---- Engine 事件（GUI 线程） ----

    def _on_sentence(self, text: str) -> None:
        self._overlay.show_sentence(text)
        self._history.add_sentence(text)

    def _on_translation(self, sentence: str, translation: str) -> None:
        self._overlay.show_translation(translation)
        self._history.set_translation(sentence, translation)

    def _on_error(self, message: str) -> None:
        self.status_label.setText(message)
        QMessageBox.warning(self, "subtitles", message)

    def _on_stopped(self) -> None:
        running = self._engine is not None and self._engine.running
        self.toggle_button.setEnabled(True)
        self.toggle_button.setText("停止" if running else "开始")
        self.status_label.setText("识别中…" if running else "就绪")
        if not running and self._engine is None:
            self._overlay.clear()

    def _show_history(self) -> None:
        self._history.show()
        self._history.raise_()

    def closeEvent(self, event) -> None:
        if self._engine is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._engine.stop(), self._loop
            )
            try:
                future.result(timeout=5.0)
            except Exception:  # noqa: BLE001 - 退出时尽力而为
                pass
            self._engine = None
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=2.0)
        super().closeEvent(event)


def run() -> int:
    app = QApplication(sys.argv)
    window = ControlWindow()
    window.show()
    return app.exec()
