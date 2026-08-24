"""历史窗口：列出本次会话所有定稿句子（时间戳 + 原文 + 译文）。"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget


class HistoryWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("字幕历史")
        self.resize(720, 480)

        self._list = QListWidget()
        self._list.setWordWrap(True)
        self._list.setStyleSheet(
            "QListWidget { background-color: #1e1e1e; color: #eeeeee; "
            "font-size: 14px; border: none; }"
            "QListWidget::item { padding: 8px 10px; "
            "border-bottom: 1px solid #333333; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list)

    def add_sentence(self, sentence: str) -> None:
        """追加一条定稿句子，译文待补。"""
        timestamp = time.strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{timestamp}] {sentence}")
        item.setData(Qt.ItemDataRole.UserRole, sentence)
        self._list.addItem(item)
        self._list.scrollToBottom()

    def set_translation(self, sentence: str, translation: str) -> None:
        """给最近一条匹配原文的记录补上译文。"""
        for row in range(self._list.count() - 1, -1, -1):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == sentence:
                # 幂等：同一句重复到达不重复追加
                if f"\n→ {translation}" not in item.text():
                    item.setText(f"{item.text()}\n→ {translation}")
                return

    def clear_history(self) -> None:
        self._list.clear()
