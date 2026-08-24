"""LLM 翻译：OpenAI 兼容 chat completions 接口，httpx 异步调用。

每句定稿后异步翻译，上一句原文作为上下文帮助消歧；失败静默返回 None，
不阻塞字幕主流程。
"""

from __future__ import annotations

import httpx

from .config import TranslateConfig

_SYSTEM_PROMPT = (
    "你是实时字幕翻译引擎。把用户给出的{source}句子翻译成{target}。"
    "只输出译文本身，不要解释、不要加注、不要引号。"
    "若提供了上一句原文，仅作为上下文帮助消歧，不要翻译它。"
)


class Translator:
    def __init__(
        self,
        config: TranslateConfig,
        api_key: str,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        source_language: str = "原文",
    ) -> None:
        self._config = config
        self._api_key = api_key
        self._base_url = (base_url or config.base_url).rstrip("/")
        self._source_language = source_language
        self._client = client

    @property
    def available(self) -> bool:
        """翻译是否可用（开关开启且配齐了 key 和模型）。"""
        return bool(
            self._config.enabled and self._api_key and self._config.model.strip()
        )

    async def translate(self, text: str, context: str = "") -> str | None:
        """翻译一句定稿；context 为上一句原文。失败返回 None。"""
        if not self.available or not text.strip():
            return None
        prompt = _SYSTEM_PROMPT.format(
            source=self._source_language, target=self._config.target_language
        )
        user = f"请翻译：{text.strip()}"
        if context.strip():
            user = f"上一句原文：{context.strip()}\n{user}"
        request = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if self._client is not None:
                response = await self._client.post(
                    f"{self._base_url}/chat/completions", json=request, headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        f"{self._base_url}/chat/completions",
                        json=request,
                        headers=headers,
                    )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except Exception:
            return None
        translated = str(content or "").strip()
        return translated or None
