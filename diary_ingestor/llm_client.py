"""
llm_client.py
=============
Shared LLM client for diary_ingestor.
Supports:
  - Local llama.cpp / OpenAI-compatible endpoint
  - OpenRouter (https://openrouter.ai/api/v1)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional


class LLMClient:
    def __init__(
        self,
        api_base: str = "http://localhost:8080",
        model_name: str = "local-model",
        api_key: Optional[str] = None,
        timeout: int = 60,
    ):
        self.api_base = api_base.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        self.timeout = timeout

    # ── Public interface ──────────────────────────────────────────────────

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 20,
        temperature: float = 0.0,
    ) -> str:
        """Send a chat completion request. Returns the raw text response."""
        payload = json.dumps({
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode("utf-8")

        url = self.api_base + "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            # OpenRouter extras
            headers["HTTP-Referer"] = "https://diary-ingestor"
            headers["X-Title"] = "Diary Ingestor"

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error reaching {url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Server returned invalid JSON: {exc}") from exc

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected API response shape: {data}") from exc

    def test_connection(self) -> str:
        """Try GET /v1/models. Returns short status string."""
        url = self.api_base + "/v1/models"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            # For OpenRouter the list may be large; just show first model id
            if isinstance(data, dict) and "data" in data:
                ids = [m.get("id", "?") for m in data["data"][:3]]
                return f"OK — models: {', '.join(ids)}"
            return f"OK — {body[:120]}"
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
