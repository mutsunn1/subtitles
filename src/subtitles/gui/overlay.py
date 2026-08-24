"""悬浮字幕窗：无边框、半透明、置顶，上行原文下行译文。

可鼠标拖动；宽度随文字自适应并设上限。深色半透明底 + 白字 + 圆角，
可被 OBS 以「窗口采集」方式捕捉。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

_MAX_WIDTH = 1000

_CONTAINER_STYLE = """
QWidget#container {
    background-color: rgba(20, 20, 20, 180);
    border-radius: 12px;
}
"""

_ORIGINAL_STYLE = """
QLabel {
    color: #ffffff;
    font-size: 20px;
    font-weight: 600;
    padding: 8px 16px 2px 16px;
}
"""

_TRANSLATION_STYLE = """
QLabel {
    color: #ffd966;
    font-size: 18px;
    padding: 2px 16px 10px 16px;
}
"""

_ORIGINAL_ONLY_STYLE = """
QLabel {
    color: #ffffff;
    font-size: 20px;
    font-weight: 600;
    padding: 8px 16px 10px 16px;
}
"""


class SubtitleOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # 不出现在程序坞/任务切换器
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMaximumWidth(_MAX_WIDTH)

        self._container = QWidget(self)
        self._container.setObjectName("container")
        self._container.setStyleSheet(_CONTAINER_STYLE)

        self._original = QLabel("")
        self._original.setWordWrap(True)
        self._original.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._original.setStyleSheet(_ORIGINAL_ONLY_STYLE)

        self._translation = QLabel("")
        self._translation.setWordWrap(True)
        self._translation.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._translation.setStyleSheet(_TRANSLATION_STYLE)
        self._translation.hide()

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(self._original)
        container_layout.addWidget(self._translation)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._container)

        self._drag_pos: QPoint | None = None
        self.hide()

    # ---- 内容更新（GUI 线程） ----

    def show_partial(self, text: str) -> None:
        """更新当前行（原文），清空上一句译文。"""
        self._original.setText(text)
        self._set_translation("")
        self._relayout()

    def show_sentence(self, text: str) -> None:
        """一句定稿：原文固定下来，等待译文。"""
        self._original.setText(text)
        self._set_translation("")
        self._relayout()

    def show_translation(self, translation: str) -> None:
        """补上当前句的译文。"""
        self._set_translation(translation)
        self._relayout()

    def clear(self) -> None:
        self._original.setText("")
        self._set_translation("")
        self.hide()

    def _set_translation(self, text: str) -> None:
        self._translation.setText(text)
        self._translation.setVisible(bool(text))
        self._original.setStyleSheet(
            _ORIGINAL_STYLE if text else _ORIGINAL_ONLY_STYLE
        )

    def _relayout(self) -> None:
        self.adjustSize()
        if not self.isVisible() and self._original.text():
            self._move_to_default_position()
            self.show()

    def _move_to_default_position(self) -> None:
        """默认位置：主屏幕底部居中。"""
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.adjustSize()
        x = area.x() + (area.width() - self.width()) // 2
        y = area.y() + area.height() - self.height() - 80
        self.move(x, y)

    # ---- 拖动 ----

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
