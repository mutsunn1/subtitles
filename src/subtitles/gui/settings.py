"""设置对话框：以 GUI 方式编辑全部配置项。

覆盖 ASR 厂商密钥、翻译服务与识别参数，保存到
``~/.subtitles/config.json``（见 config.py）。API Key 输入框默认密文显示。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, save_config

_RESOURCE_IDS = [
    "volc.bigasr.sauc.duration",
    "volc.bigasr.sauc.concurrent",
    "volc.seedasr.sauc.duration",
    "volc.seedasr.sauc.concurrent",
]

_VOLC_MODELS = ["bigmodel"]

_ALIYUN_MODELS = [
    "qwen-audio-3.0-asr-flash-streaming",
    "paraformer-realtime-v2",
    "paraformer-realtime-v1",
    "paraformer-realtime-8k-v2",
]

_LLM_BASE_URLS = [
    "https://api.deepseek.com/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://ark.cn-beijing.volces.com/api/v3",
    "https://api.openai.com/v1",
]

_LLM_MODELS = [
    "deepseek-chat",
    "qwen-plus",
    "qwen-max",
    "doubao-pro-32k",
    "gpt-4o-mini",
]

_TARGET_LANGUAGES = ["中文", "English", "日本語", "한국어"]


def _preset_combo(items: list[str], current: str) -> QComboBox:
    """预设下拉 + 可自由编辑的输入框（模型、base_url 等）。"""
    combo = QComboBox()
    combo.setEditable(True)
    combo.addItems(items)
    combo.setCurrentText(current)
    return combo


class _SecretEdit(QWidget):
    """密文输入框 + 显示/隐藏切换按钮。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.edit = QLineEdit(text)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        toggle = QPushButton("显示")
        toggle.setCheckable(True)
        toggle.setFixedWidth(48)
        toggle.toggled.connect(
            lambda checked: self.edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)
        layout.addWidget(toggle)

    def text(self) -> str:
        return self.edit.text().strip()


class SettingsDialog(QDialog):
    """设置对话框：编辑并保存 Config。"""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        self._config = config

        # ---- 火山引擎 ----
        volc_box = QGroupBox("火山引擎（大模型流式语音识别）")
        volc_form = QFormLayout(volc_box)
        self.volc_key = _SecretEdit(config.volc.api_key)
        self.volc_app_id = QLineEdit(config.volc.app_id)
        self.volc_app_id.setPlaceholderText("旧版控制台 App ID（可选）")
        self.volc_resource = _preset_combo(_RESOURCE_IDS, config.volc.resource_id)
        self.volc_model = _preset_combo(_VOLC_MODELS, config.volc.model_name)
        volc_form.addRow("API Key:", self.volc_key)
        volc_form.addRow("App ID:", self.volc_app_id)
        volc_form.addRow("资源 ID:", self.volc_resource)
        volc_form.addRow("模型:", self.volc_model)

        # ---- 阿里云百炼 ----
        aliyun_box = QGroupBox("阿里云百炼（paraformer / qwen-audio 实时识别）")
        aliyun_form = QFormLayout(aliyun_box)
        self.aliyun_key = _SecretEdit(config.aliyun.api_key)
        self.aliyun_model = _preset_combo(_ALIYUN_MODELS, config.aliyun.model)
        aliyun_form.addRow("API Key:", self.aliyun_key)
        aliyun_form.addRow("模型:", self.aliyun_model)

        # ---- 翻译 ----
        translate_box = QGroupBox("LLM 翻译（OpenAI 兼容接口）")
        translate_form = QFormLayout(translate_box)
        self.translate_enabled = QCheckBox("启用翻译")
        self.translate_enabled.setChecked(config.translate.enabled)
        self.translate_base_url = _preset_combo(
            _LLM_BASE_URLS, config.translate.base_url
        )
        self.translate_key = _SecretEdit(config.translate.api_key)
        self.translate_model = _preset_combo(_LLM_MODELS, config.translate.model)
        self.translate_model.lineEdit().setPlaceholderText(
            "如 deepseek-chat / qwen-plus"
        )
        self.translate_target = QComboBox()
        self.translate_target.setEditable(True)
        self.translate_target.addItems(_TARGET_LANGUAGES)
        self.translate_target.setCurrentText(config.translate.target_language)
        translate_form.addRow(self.translate_enabled)
        translate_form.addRow("Base URL:", self.translate_base_url)
        translate_form.addRow("API Key:", self.translate_key)
        translate_form.addRow("模型:", self.translate_model)
        translate_form.addRow("目标语言:", self.translate_target)

        # ---- 识别参数 ----
        asr_box = QGroupBox("识别参数")
        asr_form = QFormLayout(asr_box)
        self.max_sentence = QDoubleSpinBox()
        self.max_sentence.setRange(3.0, 60.0)
        self.max_sentence.setSuffix(" 秒")
        self.max_sentence.setValue(config.max_sentence_seconds)
        asr_form.addRow("断句最长时长:", self.max_sentence)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(volc_box)
        layout.addWidget(aliyun_box)
        layout.addWidget(translate_box)
        layout.addWidget(asr_box)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        cfg = self._config
        cfg.volc.api_key = self.volc_key.text()
        cfg.volc.app_id = self.volc_app_id.text().strip()
        cfg.volc.resource_id = self.volc_resource.currentText().strip()
        cfg.volc.model_name = self.volc_model.currentText().strip() or "bigmodel"
        cfg.aliyun.api_key = self.aliyun_key.text()
        cfg.aliyun.model = (
            self.aliyun_model.currentText().strip() or "paraformer-realtime-v2"
        )
        cfg.translate.enabled = self.translate_enabled.isChecked()
        cfg.translate.base_url = self.translate_base_url.currentText().strip()
        cfg.translate.api_key = self.translate_key.text()
        cfg.translate.model = self.translate_model.currentText().strip()
        cfg.translate.target_language = (
            self.translate_target.currentText().strip() or "中文"
        )
        cfg.max_sentence_seconds = self.max_sentence.value()
        save_config(cfg)
        self.accept()
