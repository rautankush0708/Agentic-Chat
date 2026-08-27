"""Thin, provider-agnostic chat-completion client.

Mirrors AIDataQueryService.cs's CallAIAsyncResponse: one system prompt + one user
prompt in, plain text content out. Reads everything (key/model/base URL/provider)
from Config, i.e. environment variables — nothing hardcoded.
"""

import requests


class LLMError(RuntimeError):
    pass


class LLMProvider:
    def __init__(self, config):
        self.provider = config["AI_PROVIDER"]
        self.api_key = config["AI_API_KEY"]
        self.model = config["AI_MODEL"]
        self.base_url = config["AI_BASE_URL"]
        self.timeout = config["AI_TIMEOUT_SECONDS"]

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            raise LLMError(
                "No AI_API_KEY (or OPENAI_API_KEY/ANTHROPIC_API_KEY) configured on the server. "
                "Set one in backend/.env to enable the AI assistant."
            )

        if self.provider == "anthropic":
            return self._chat_anthropic(system_prompt, user_prompt)
        return self._chat_openai_compatible(system_prompt, user_prompt)

    def _chat_openai_compatible(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.model,
            "temperature": 1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"AI provider request failed: {exc}") from exc

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected AI provider response shape: {data}") from exc

    def _chat_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url.rstrip('/')}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"AI provider request failed: {exc}") from exc

        data = resp.json()
        try:
            return "".join(block.get("text", "") for block in data["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected AI provider response shape: {data}") from exc
