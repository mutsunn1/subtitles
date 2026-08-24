# subtitles

实时字幕工具：监测 macOS 内置音频播放，通过国内 ASR 服务实时识别语音并显示字幕。主要用于直播场景，支持智能断句（识别到完整句子后自动断开并开始下一句）、实时查看本次历史字幕记录，可接入 LLM 实现字幕翻译。

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues; all operations go through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
