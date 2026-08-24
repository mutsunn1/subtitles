"""配置层测试：读写往返、缺省回退、环境变量兜底优先级。"""

from __future__ import annotations

from subtitles.config import (
    Config,
    load_config,
    resolve_aliyun_api_key,
    resolve_translate_api_key,
    resolve_volc_api_key,
    save_config,
)


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config()
    cfg.asr_provider = "aliyun"
    cfg.language = "en-US"
    cfg.volc.api_key = "volc-key"
    cfg.aliyun.api_key = "aliyun-key"
    cfg.aliyun.model = "paraformer-realtime-v2"
    cfg.translate.enabled = False
    cfg.translate.model = "qwen-plus"
    cfg.translate.target_language = "English"
    save_config(cfg, path)

    loaded = load_config(path)
    assert loaded.asr_provider == "aliyun"
    assert loaded.language == "en-US"
    assert loaded.volc.api_key == "volc-key"
    assert loaded.aliyun.api_key == "aliyun-key"
    assert loaded.translate.enabled is False
    assert loaded.translate.model == "qwen-plus"
    assert loaded.translate.target_language == "English"


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.json")
    assert cfg.asr_provider == "volc"
    assert cfg.max_sentence_seconds == 15.0


def test_load_corrupted_file_returns_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json")
    cfg = load_config(path)
    assert cfg.asr_provider == "volc"


def test_config_value_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VOLC_API_KEY", "env-key")
    cfg = Config()
    cfg.volc.api_key = "config-key"
    assert resolve_volc_api_key(cfg) == "config-key"


def test_env_fallback_when_config_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VOLC_API_KEY", "env-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-dashscope")
    cfg = Config()
    assert resolve_volc_api_key(cfg) == "env-key"
    assert resolve_aliyun_api_key(cfg) == "env-dashscope"


def test_no_key_anywhere_returns_empty(monkeypatch):
    monkeypatch.delenv("VOLC_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    cfg = Config()
    assert resolve_volc_api_key(cfg) == ""
    assert resolve_aliyun_api_key(cfg) == ""


def test_translate_key_env_chain(monkeypatch):
    monkeypatch.delenv("SUBTITLES_LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    cfg = Config()
    assert resolve_translate_api_key(cfg) == "env-openai"
    monkeypatch.setenv("SUBTITLES_LLM_API_KEY", "env-llm")
    assert resolve_translate_api_key(cfg) == "env-llm"
    cfg.translate.api_key = "config-llm"
    assert resolve_translate_api_key(cfg) == "config-llm"
