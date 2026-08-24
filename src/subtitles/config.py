"""配置存储：ASR 厂商、翻译服务与识别参数。

配置持久化到本地 JSON（默认 ``~/.subtitles/config.json``，不入 git）。
API Key 可留空，解析时优先配置值，其次环境变量。支持的环境变量：

- ``VOLC_API_KEY``：火山引擎新版控制台 APP Key（X-Api-Key）
- ``VOLC_APP_ID``：火山引擎旧版控制台 App ID（可选）
- ``DASHSCOPE_API_KEY``：阿里云百炼 API Key
- ``SUBTITLES_LLM_API_KEY``：翻译服务 API Key（兜底 ``OPENAI_API_KEY``）
- ``SUBTITLES_LLM_BASE_URL``：翻译服务 base_url（兜底 ``OPENAI_BASE_URL``）
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("~/.subtitles/config.json")


def _as_path(path) -> Path:
    value = path if isinstance(path, Path) else Path(path)
    return value.expanduser()


@dataclass
class VolcConfig:
    """火山引擎大模型流式语音识别（SAUC bigmodel）配置。"""

    app_id: str = ""  # 旧版控制台 App ID（可选，新版控制台不需要）
    api_key: str = ""  # 新版控制台 APP Key（X-Api-Key）
    # 资源 ID：ASR 1.0 小时版 volc.bigasr.sauc.duration / 并发版 volc.bigasr.sauc.concurrent，
    # ASR 2.0 小时版 volc.seedasr.sauc.duration / 并发版 volc.seedasr.sauc.concurrent
    resource_id: str = "volc.bigasr.sauc.duration"


@dataclass
class AliyunConfig:
    """阿里云百炼 paraformer 实时语音识别配置。"""

    api_key: str = ""  # 百炼 API Key（Authorization: Bearer）
    model: str = "paraformer-realtime-v2"


@dataclass
class TranslateConfig:
    """LLM 翻译配置（OpenAI 兼容 chat completions 接口）。"""

    enabled: bool = True
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    target_language: str = "中文"


@dataclass
class Config:
    asr_provider: str = "volc"  # "volc" | "aliyun"
    language: str = "zh-CN"  # 识别语言，如 zh-CN / en-US / ja-JP / auto
    max_sentence_seconds: float = 15.0  # 智能断句最长时长兜底
    volc: VolcConfig = field(default_factory=VolcConfig)
    aliyun: AliyunConfig = field(default_factory=AliyunConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)


def default_config() -> Config:
    return Config()


def save_config(config: Config, path=DEFAULT_CONFIG_PATH) -> None:
    path = _as_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = path.with_name(f"{path.name}.bak")
    if path.is_file():
        shutil.copyfile(path, backup_path)
        backup_path.chmod(0o600)

    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2))
    temp_path.chmod(0o600)
    temp_path.replace(path)


def load_config(path=DEFAULT_CONFIG_PATH) -> Config:
    """读取配置；文件缺失或损坏时回退默认配置。"""
    path = _as_path(path)
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default_config()
    if not isinstance(data, dict):
        return default_config()

    cfg = default_config()
    cfg.asr_provider = str(data.get("asr_provider", cfg.asr_provider))
    cfg.language = str(data.get("language", cfg.language))
    try:
        cfg.max_sentence_seconds = float(
            data.get("max_sentence_seconds", cfg.max_sentence_seconds)
        )
    except (TypeError, ValueError):
        pass

    volc = data.get("volc")
    if isinstance(volc, dict):
        cfg.volc = VolcConfig(
            app_id=str(volc.get("app_id", "")),
            api_key=str(volc.get("api_key", "")),
            resource_id=str(volc.get("resource_id", VolcConfig.resource_id)),
        )
    aliyun = data.get("aliyun")
    if isinstance(aliyun, dict):
        cfg.aliyun = AliyunConfig(
            api_key=str(aliyun.get("api_key", "")),
            model=str(aliyun.get("model", AliyunConfig.model)),
        )
    translate = data.get("translate")
    if isinstance(translate, dict):
        cfg.translate = TranslateConfig(
            enabled=bool(translate.get("enabled", True)),
            base_url=str(translate.get("base_url", TranslateConfig.base_url)),
            api_key=str(translate.get("api_key", "")),
            model=str(translate.get("model", "")),
            target_language=str(
                translate.get("target_language", TranslateConfig.target_language)
            ),
        )
    return cfg


def resolve_volc_api_key(config: Config) -> str:
    """火山引擎 API Key（X-Api-Key）：配置值优先，其次 ``VOLC_API_KEY``。"""
    key = config.volc.api_key.strip()
    return key or os.environ.get("VOLC_API_KEY", "").strip()


def resolve_volc_app_id(config: Config) -> str:
    """火山引擎 App ID：配置值优先，其次 ``VOLC_APP_ID``。"""
    app_id = config.volc.app_id.strip()
    return app_id or os.environ.get("VOLC_APP_ID", "").strip()


def resolve_aliyun_api_key(config: Config) -> str:
    """阿里云百炼 API Key：配置值优先，其次 ``DASHSCOPE_API_KEY``。"""
    key = config.aliyun.api_key.strip()
    return key or os.environ.get("DASHSCOPE_API_KEY", "").strip()


def resolve_translate_api_key(config: Config) -> str:
    """翻译服务 API Key：配置值优先，其次 ``SUBTITLES_LLM_API_KEY``/``OPENAI_API_KEY``。"""
    key = config.translate.api_key.strip()
    if key:
        return key
    return (
        os.environ.get("SUBTITLES_LLM_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )


def resolve_translate_base_url(config: Config) -> str:
    """翻译服务 base_url：配置值优先，其次 ``SUBTITLES_LLM_BASE_URL``/``OPENAI_BASE_URL``。"""
    base_url = config.translate.base_url.strip()
    if base_url and base_url != TranslateConfig.base_url:
        return base_url
    return (
        os.environ.get("SUBTITLES_LLM_BASE_URL", "").strip()
        or os.environ.get("OPENAI_BASE_URL", "").strip()
        or base_url
        or TranslateConfig.base_url
    )
