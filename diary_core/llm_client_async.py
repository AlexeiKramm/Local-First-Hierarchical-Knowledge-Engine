"""
llm_client_async.py
===================
Async counterpart to LLMClient using aiohttp.
Used by Summarizer.summarize_days_parallel() for concurrent OpenRouter requests.

Features:
  - asyncio-native: non-blocking HTTP via aiohttp
  - Agent-mode file queue: non-blocking polling via asyncio.sleep (no aiohttp needed)
  - Bearer auth passthrough from LLMClient.api_key
  - Ground-truth token counts from API ``usage`` block, logged to CSV via :func:`log_api_call`
  - HTTP 429 rate-limit retry with exponential backoff (max 3 retries)
  - Logs elapsed time, input and output tokens per request
  - One aiohttp.ClientSession per complete_async() call (shared across retries)
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from .llm_client import (
    LLMClient,
    LLMResponse,
    _parse_json_format,
    _parse_section_format,
)
from .thinking_parser import strip_thinking
from .token_logger import log_api_call

try:
    import aiohttp as _aiohttp_module
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False


class AsyncLLMClient(LLMClient):
    """
    Async-capable extension of LLMClient.
    Falls back to the synchronous client run in an executor if aiohttp is not installed.
    Logs estimated vs actual token usage to ``token_usage_log.csv`` via :func:`log_api_call`
    on every completion.
    """

    async def complete_async(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        output_format: str = "section",
        max_retries: int = 3,
        estimated_input_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        Async version of complete(). Returns the same LLMResponse.

        Retries on HTTP 429 with exponential backoff.

        Logs estimated and actual token counts to ``token_usage_log.csv`` via
        :func:`log_api_call` on every completion.

        Args:
            system_prompt: The system instruction string.
            user_prompt: The user prompt containing the input text.
            max_tokens: Maximum tokens in the completion (default 1024).
            temperature: Sampling temperature (default 0.3).
            output_format: ``"section"`` (default) for section-header parsing,
                or ``"json"`` for JSON output.
            max_retries: Maximum number of retries on HTTP 429 (default 3).
            estimated_input_tokens: Pre-flight token count estimate from the caller,
                forwarded to the token usage logger. If ``None``, the CSV row will
                have an empty estimated column.
        """
        # ── Normal HTTP mode ───────────────────────────────────────────────
        if not _AIOHTTP_AVAILABLE:
            # Graceful fallback: run sync complete() in thread pool
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self.complete(
                    system_prompt, user_prompt, max_tokens, temperature, output_format,
                    estimated_input_tokens=estimated_input_tokens,
                ),
            )

        import json as _json

        import aiohttp

        payload = _json.dumps({
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        })

        url = self.api_base + "/v1/chat/completions"
        headers = self._make_headers()

        # One session for the whole call (all retries share the same connection pool).

        delay = 2.0
        data: dict = {}
        elapsed: float = 0.0
        async with aiohttp.ClientSession() as session:
            for attempt in range(max_retries + 1):
                t0 = time.perf_counter()
                try:
                    async with session.post(
                        url,
                        data=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ) as resp:
                        if resp.status == 429:
                            if attempt < max_retries:
                                retry_after = float(resp.headers.get("Retry-After", delay))
                                wait = max(retry_after, delay)
                                await asyncio.sleep(wait)
                                delay *= 2
                                continue
                            else:
                                raise RuntimeError(f"Rate limited after {max_retries} retries (HTTP 429)")

                        if resp.status >= 400:
                            body = await resp.text()
                            raise RuntimeError(f"API error {resp.status}: {body[:500]}")

                        data = await resp.json(content_type=None)

                except aiohttp.ClientError as exc:
                    if attempt < max_retries:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    raise RuntimeError(f"Network error: {exc}") from exc

                elapsed = time.perf_counter() - t0
                break
            else:
                raise RuntimeError("Exhausted all retries")

        try:
            import json as _json
            raw_completion = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected API response shape: {data}") from exc

        usage = data.get("usage", {})
        input_tokens: Optional[int] = usage.get("prompt_tokens") or usage.get("input_tokens")
        output_tokens: Optional[int] = usage.get("completion_tokens") or usage.get("output_tokens")

        parsed = strip_thinking(raw_completion)

        if output_format == "json":
            structured = _parse_json_format(parsed.clean_text)
            if not structured:
                structured = _parse_section_format(parsed.clean_text)
        else:
            structured = _parse_section_format(parsed.clean_text)

        log_api_call(
            estimated_input_tokens=estimated_input_tokens,
            actual_input_tokens=input_tokens,
            actual_output_tokens=output_tokens,
            char_count=(len(system_prompt) + 1 + len(user_prompt))
                       if system_prompt or user_prompt else None,
            model=self.model_name,
        )

        return LLMResponse(
            parsed=parsed,
            structured=structured,
            raw_completion=raw_completion,
            elapsed_seconds=elapsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def fetch_models_async(self) -> list[str]:
        """
        Async version of fetch_models().
        Returns a list of model id strings from /v1/models.
        """
        if not _AIOHTTP_AVAILABLE:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.fetch_models)

        import aiohttp

        url = self.api_base + "/v1/models"
        headers = self._make_headers()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"Model list error {resp.status}: {body[:300]}")
                result = await resp.json(content_type=None)
                return [m["id"] for m in result.get("data", [])]
