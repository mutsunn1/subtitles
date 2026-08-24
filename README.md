# subtitles

macOS 实时字幕工具：采集系统内建音频（ScreenCaptureKit）→ 流式 ASR（火山引擎 / 阿里云百炼）→ 智能断句 → PySide6 原生悬浮窗显示双语字幕（原文 + LLM 译文），可查看本次会话历史记录。主要面向直播场景。

## 安装

需要 Python ≥ 3.11 和 [uv](https://docs.astral.sh/uv/)，仅支持 macOS 13+。

```bash
cd subtitles
uv sync
```

## 运行

```bash
uv run subtitles
```

控制条上选择 ASR 厂商、识别语言、翻译开关与目标语言，点「开始」。识别期间
字幕悬浮窗可用鼠标拖动，「历史」按钮查看本次会话所有定稿句子。

### 权限

系统音频采集使用 ScreenCaptureKit，需要「系统设置 → 隐私与安全性 →
屏幕录制」权限（系统音频与屏幕录制共用该权限）。首次启动时系统会弹窗
请求授权；若未授权，点开始后会提示采集失败，授权后重启本工具即可。

### OBS 直播

字幕悬浮窗是无边框半透明置顶窗口，可在 OBS 中添加「窗口采集」源选中
`subtitles` 的悬浮窗，把字幕合成进直播画面。

## 配置

配置文件位于 `~/.subtitles/config.json`（首次保存控制条选项时自动生成，
可直接编辑）。结构：

```json
{
  "asr_provider": "volc",
  "language": "zh-CN",
  "max_sentence_seconds": 15.0,
  "volc": {
    "app_id": "",
    "api_key": "",
    "resource_id": "volc.bigasr.sauc.duration"
  },
  "aliyun": {
    "api_key": "",
    "model": "paraformer-realtime-v2"
  },
  "translate": {
    "enabled": true,
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "",
    "target_language": "中文"
  }
}
```

所有 Key 均可在配置中留空，改为读环境变量（配置值优先）：

| 用途 | 配置字段 | 环境变量 |
| --- | --- | --- |
| 火山引擎 APP Key（X-Api-Key） | `volc.api_key` | `VOLC_API_KEY` |
| 火山引擎 App ID（旧版控制台，可选） | `volc.app_id` | `VOLC_APP_ID` |
| 阿里云百炼 API Key | `aliyun.api_key` | `DASHSCOPE_API_KEY` |
| 翻译服务 API Key | `translate.api_key` | `SUBTITLES_LLM_API_KEY`（兜底 `OPENAI_API_KEY`） |
| 翻译服务 base_url | `translate.base_url` | `SUBTITLES_LLM_BASE_URL`（兜底 `OPENAI_BASE_URL`） |

### Key 申请入口

- 火山引擎（豆包语音 · 大模型流式语音识别）：
  <https://console.volcengine.com/speech> ，开通后在新版控制台获取 APP Key。
  `resource_id` 按已开通资源填写：ASR 1.0 为 `volc.bigasr.sauc.duration`（小时版）/
  `volc.bigasr.sauc.concurrent`（并发版），ASR 2.0 为 `volc.seedasr.sauc.*`。
  协议文档：<https://www.volcengine.com/docs/6561/1354869>
- 阿里云百炼（paraformer / qwen-audio 实时语音识别）：
  <https://bailian.console.aliyun.com/> ，获取 API Key（即 DashScope Key）。
  模型可选 `qwen-audio-3.0-asr-flash-streaming`（自动语种检测，推荐）或
  `paraformer-realtime-v2` 等，在设置界面下拉选择或自行填写。
  协议文档：<https://help.aliyun.com/zh/model-studio/real-time-speech-recognition-user-guide>
- 翻译：任意 OpenAI 兼容 chat completions 服务（DeepSeek、通义、Kimi 等），
  填 `translate.base_url` / `translate.api_key` / `translate.model` 即可。
  未配置翻译时仅显示原文，不影响识别主流程。

## 智能断句

以厂商返回的句子级定稿事件为主（火山的 `definite` 分句 / 百炼的
`sentence_end`），另加最长时长兜底（默认 15s，`max_sentence_seconds`
可调）：一句 partial 持续超过该时长会被强制断开另起一行，避免字幕
无限拉长。定稿句子入历史并（若开启翻译）触发异步翻译。

## 开发

```bash
uv sync            # 安装依赖（含 dev 组 pytest）
uv run pytest      # 跑测试
```

目录结构：

```
src/subtitles/
├── cli.py                # 入口 `subtitles`
├── config.py             # ~/.subtitles/config.json + 环境变量兜底
├── capture/system_audio.py  # ScreenCaptureKit 系统音频采集
├── asr/                  # base.py 契约 + volc.py / aliyun.py 两家实现
├── segmenter.py          # 智能断句（纯逻辑）
├── translator.py         # LLM 翻译（httpx 异步）
├── engine.py             # 编排层
└── gui/                  # app.py 控制条 / overlay.py 悬浮窗 / history.py 历史
tests/                    # test_segmenter.py / test_config.py
```

注意：ASR 真实联调需要有效 API Key；识别语言参数目前仅阿里云侧生效
（`language_hints`），火山双向流式接口不支持指定语种，由模型自动识别。
