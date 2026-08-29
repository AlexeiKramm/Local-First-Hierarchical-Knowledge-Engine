"""
llm_client.py
=============
OpenAI-compatible API client.
Supports local llama.cpp servers, OpenRouter (or any OpenAI-compatible endpoint).

Output token counts are read from the API response's `usage` block when available,
giving ground-truth billing data for OpenRouter runs.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .thinking_parser import ParsedResponse, strip_thinking
from .token_logger import log_api_call


# ─────────────────────────────────────────────
#  Response parsing strategies
# ─────────────────────────────────────────────

SECTION_LABELS = {
    # ── Fast-filter fields (new) ──
    "OVERALL_VIBE":             "overall_vibe",
    "TIME_OF_DAY_TEXTURE":      "time_of_day_texture",

    # ── Human-readable narrative (mapped to SummaryUnit fields) ──
    "SUMMARY":                      "summary",
    "EMOTIONAL_TONE":               "emotional_tone",
    "EMOTIONAL TONE":               "emotional_tone",       # legacy alias
    "ENERGY LEVEL":                 "energy_level",         # legacy alias
    "SOCIAL CONNECTEDNESS":         "social_connectedness", # legacy alias
    "FORWARD MOMENTUM":             "forward_momentum",     # legacy alias
    "KEY EVENTS":                   "key_events",           # legacy alias
    "QUESTIONS RAISED":             "questions_raised",     # legacy alias
    "QUESTIONS_RAISED":             "questions_raised",
    "ENTITIES":                     "entities",

    # ── Machine-readable index (stored as raw strings in SummaryUnit.raw_index) ──
    "KEY_EVENTS":                   "key_events",
    "PEAK_MOMENT":                  "peak_moment",
    "SCALAR_METRICS":               "scalar_metrics",
    "NARRATIVE_THREADS":            "narrative_threads",
    "SIGNIFICANT_DELTA":            "significant_delta",
    "PHYSIOLOGICAL_FLAGS":          "physiological_flags",
    "RELATIONAL_MAP":               "relational_map",
    "AVOIDANCE_SIGNALS":            "avoidance_signals",
    "GROWTH_MARKERS":               "growth_markers",
    "COPING_MECHANISMS":            "coping_mechanisms",
    "SELF_PERCEPTION_SNAPSHOT":     "self_perception_snapshot",
    "VALUES_IN_TENSION":             "values_in_tension",
    "CONTEXT_BRIDGE":               "context_bridge",
    "ENTITY_MENTIONS":              "entity_mentions",

    # ── Section dividers (ignored gracefully) ──
    "=== MACHINE-READABLE INDEX ===":   None,
    "=== HUMAN-READABLE NARRATIVE ===": None,

    # ── Entity synthesis fields ──
    "RELATIONSHIP_ARC":     "relationship_arc",
    "EMOTIONAL_PATTERN":    "emotional_pattern",
    "KEY_MOMENTS":          "key_moments",
    "VALENCE_TREND":        "valence_trend",
    "UNRESOLVED_QUESTIONS": "unresolved_questions",
    "OVERALL_ASSESSMENT":   "overall_assessment",
}

# Fields that should be stored as raw text blocks (not forced into lists or ints)
_RAW_BLOCK_FIELDS = {
    "scalar_metrics", "narrative_threads", "significant_delta",
    "physiological_flags", "relational_map", "avoidance_signals",
    "growth_markers", "coping_mechanisms", "self_perception_snapshot",
    "values_in_tension", "context_bridge", "peak_moment", "entity_mentions",
    "relationship_arc", "emotional_pattern", "valence_trend", "overall_assessment",
    "retrospective_note", "summary", "emotional_tone",
    "overall_vibe", "time_of_day_texture",
}

# Enum/keyword fields — single string, no list splitting
_ENUM_FIELDS = {"overall_vibe", "time_of_day_texture"}

# List fields that come back as comma-separated or bullet lines
_LIST_FIELDS = {"key_events", "questions_raised"}


def _parse_section_format(text: str) -> dict:
    """
    Parse the model's section-header output format into a dict.
    Handles:
    - New fast-filter headers: OVERALL_VIBE, TIME_OF_DAY_TEXTURE
    - Old-format headers: ENERGY LEVEL, CENTRAL THEMES, etc. (legacy aliases kept)
    - New-format headers: SCALAR_METRICS, NARRATIVE_THREADS, etc.
    - Fuzzy matching: SCALAR_METRICS and SCALAR METRICS treated identically.
    - Scalar sub-field extraction from inside the SCALAR_METRICS raw block.
    """
    import re

    result: dict = {}

    # Build a normalised lookup: both "SCALAR_METRICS" and "SCALAR METRICS" → same key.
    expanded_labels: dict[str, str | None] = {}
    for raw_key, val in SECTION_LABELS.items():
        expanded_labels[raw_key] = val
        if "_" in raw_key:
            expanded_labels[raw_key.replace("_", " ")] = val

    known_headers = [k for k, v in expanded_labels.items() if v is not None]
    known_headers_sorted = sorted(known_headers, key=len, reverse=True)

    header_pat = re.compile(
        r"^(" + "|".join(re.escape(k) for k in known_headers_sorted) + r")\s*[:\-]\s*",
        re.IGNORECASE | re.MULTILINE,
    )

    positions = [(m.start(), m.group(1).upper(), m.end()) for m in header_pat.finditer(text)]

    for i, (start, label, content_start) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        raw_content = text[content_start:end].strip()

        normalised = label.replace(" ", "_")
        key = expanded_labels.get(label) or expanded_labels.get(normalised)
        if key is None:
            continue  # section divider — skip

        scalar_fields = {"energy_level", "social_connectedness", "forward_momentum"}
        scalar_sub_list = {"central_themes"}  # legacy — ignored now

        if key in scalar_fields:
            m = re.search(r"\d", raw_content)
            result[key] = int(m.group()) if m else None

        elif key in _ENUM_FIELDS:
            # Single keyword — take first word/token
            first_token = raw_content.strip().split()[0].upper() if raw_content.strip() else None
            result[key] = first_token

        elif key in _LIST_FIELDS:
            items = [re.sub(r"^[-•*]\s*", "", ln).strip()
                     for ln in raw_content.splitlines() if ln.strip()]
            if len(items) == 1 and "," in items[0]:
                items = [x.strip() for x in items[0].split(",") if x.strip()]
            result[key] = [i for i in items if i]

        elif key == "entities":
            entity_dict = {}
            current_name = None
            current_lines = []
            for line in raw_content.splitlines():
                m_head = re.match(r"^[-•*]?\s*\[?(.+?)\]?\s*[:\-]\s*(.*)$", line.strip())
                if m_head and not line.startswith(" "):
                    if current_name:
                        entity_dict[current_name] = " ".join(current_lines).strip()
                    current_name = m_head.group(1).strip()
                    current_lines = [m_head.group(2).strip()] if m_head.group(2).strip() else []
                elif current_name and line.strip():
                    current_lines.append(line.strip())
            if current_name:
                entity_dict[current_name] = " ".join(current_lines).strip()
            result[key] = entity_dict

        elif key == "scalar_metrics":
            result[key] = raw_content
            _scalar_sub = {
                "energy_level":         re.compile(r"ENERGY[_ ]LEVEL\s*[:\-]\s*(\d)", re.I),
                "social_connectedness": re.compile(r"SOCIAL[_ ]CONNECTEDNESS\s*[:\-]\s*(\d)", re.I),
                "forward_momentum":     re.compile(r"FORWARD[_ ]MOMENTUM\s*[:\-]\s*(\d)", re.I),
            }
            for sub_key, sub_pat in _scalar_sub.items():
                if sub_key not in result:
                    sm = sub_pat.search(raw_content)
                    if sm:
                        result[sub_key] = int(sm.group(1))

        elif key in _RAW_BLOCK_FIELDS:
            result[key] = raw_content

        else:
            result[key] = raw_content

    # Strip stray section-banner lines
    import re as _re
    _banner = _re.compile(r"\s*===\s*.+?\s*===\s*$", _re.MULTILINE)
    for k in list(result.keys()):
        if isinstance(result[k], str):
            result[k] = _banner.sub("", result[k]).rstrip()

    return result


def _parse_json_format(text: str) -> dict:
    """
    Attempt to extract a JSON object from the model output.
    Tries full parse first, then scans for the first {...} block.
    """
    import re
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    return {}


# ─────────────────────────────────────────────
#  API client
# ─────────────────────────────────────────────

@dataclass
class LLMResponse:
    parsed: ParsedResponse
    structured: dict           # best-effort parsed structured fields
    raw_completion: str        # raw text before thinking strip
    elapsed_seconds: float
    input_tokens: Optional[int] = None    # from API usage block (ground truth)
    output_tokens: Optional[int] = None   # from API usage block (ground truth)


class LLMClient:
    """
    Thin wrapper around any OpenAI-compatible API endpoint.
    Supports:
    - Local llama.cpp servers (no auth)
    - OpenRouter and other cloud APIs (Bearer token auth via api_key)
    - Model list probing (/v1/models)
    - Chat completions (/v1/chat/completions)
    - Section-header (default) or JSON output parsing
    - Automatic thinking token stripping
    - Ground-truth token logging from API usage block
    """

    def __init__(
        self,
        api_base: str,
        model_name: str,
        timeout: int = 600,
        api_key: str = "",
        app_title: str = "Diary Analyzer",
    ):
        self.api_base = api_base.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout
        self.api_key = api_key
        self.app_title = app_title

    def _make_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            # OpenRouter ranking / attribution headers
            headers["HTTP-Referer"] = "http://localhost"
            headers["X-Title"] = self.app_title
        return headers

    # ── Connection utilities ───────────────────────────────────────────────

    def fetch_models(self) -> list[str]:
        """Query /v1/models and return a list of model id strings."""
        url = self.api_base + "/v1/models"
        req = urllib.request.Request(url, method="GET", headers=self._make_headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["id"] for m in data.get("data", [])]
        except Exception as exc:
            raise RuntimeError(f"Cannot reach model list endpoint: {exc}") from exc

    def fetch_models_detailed(self) -> list[dict]:
        """
        Query /v1/models and return full model metadata objects.

        Each dict contains at minimum:
          id, name, description, context_length,
          pricing.prompt (cost per token), pricing.completion (cost per token)
          top_provider.max_completion_tokens  (may be absent for local servers)

        OpenRouter returns all of these; local llama.cpp servers return only id.
        """
        url = self.api_base + "/v1/models"
        req = urllib.request.Request(url, method="GET", headers=self._make_headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("data", [])
        except Exception as exc:
            raise RuntimeError(f"Cannot reach model list endpoint: {exc}") from exc

    def test_connection(self) -> bool:
        """Return True if the server is reachable, raise RuntimeError otherwise."""
        self.fetch_models()
        return True

    # ── Core completion ────────────────────────────────────────────────────

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        output_format: str = "section",   # "section" | "json"
        estimated_input_tokens: Optional[int] = None,
    ) -> "LLMResponse":
        """
        Send a chat completion request and return a structured LLMResponse.

        Token counts are read from the API response's ``usage`` block when present,
        giving ground-truth billing data for OpenRouter and other metered APIs.
        Both estimated and actual token counts are logged to ``token_usage_log.csv``
        via :func:`log_api_call`.

        Args:
            system_prompt: The system instruction string.
            user_prompt: The user prompt containing the input text.
            max_tokens: Maximum tokens in the completion (default 1024).
            temperature: Sampling temperature (default 0.3).
            output_format: ``"section"`` (default) for section-header parsing,
                or ``"json"`` for JSON output.
            estimated_input_tokens: Pre-flight token count estimate from the caller,
                forwarded to the token usage logger. If ``None``, the CSV row will
                have an empty estimated column.
        """
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
        req = urllib.request.Request(
            url,
            data=payload,
            headers=self._make_headers(),
            method="POST",
        )

        import http.client
        import socket

        MAX_RETRIES = 3
        BACKOFF_SECONDS = [5, 15, 45]   # wait between attempts

        t0 = time.perf_counter()
        last_exc: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break   # success — exit retry loop
            except urllib.error.HTTPError as exc:
                # HTTP 4xx/5xx — don't retry, surface immediately
                body = exc.read().decode("utf-8")
                try:
                    err_data = json.loads(body)
                    msg = err_data.get("error", {}).get("message", body)
                except Exception:
                    msg = body
                raise RuntimeError(f"API Error ({exc.code}): {msg}") from exc
            except (
                ConnectionResetError,
                http.client.RemoteDisconnected,
                ConnectionAbortedError,
                BrokenPipeError,
            ) as exc:
                # Server dropped the connection (common with llama.cpp under load)
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    wait = BACKOFF_SECONDS[attempt]
                    import sys
                    print(
                        f"  ⚠ Server dropped connection (attempt {attempt + 1}/{MAX_RETRIES}): {exc}. "
                        f"Retrying in {wait}s…",
                        file=sys.stderr, flush=True,
                    )
                    time.sleep(wait)
                    # Rebuild the request object (urllib reuses the socket — must recreate)
                    req = urllib.request.Request(
                        url, data=payload, headers=self._make_headers(), method="POST"
                    )
                else:
                    raise RuntimeError(
                        f"Server dropped connection after {MAX_RETRIES} attempts: {exc}"
                    ) from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Network error: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Server returned invalid JSON: {exc}") from exc


        elapsed = time.perf_counter() - t0

        try:
            raw_completion = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected API response shape: {data}") from exc

        if raw_completion is None:
            err_msg = data.get("error", {}).get("message")
            ref = f": {err_msg}" if err_msg else ""
            raise RuntimeError(
                f"API returned empty completion (null content){ref}. "
                f"This can happen due to moderation triggers, provider timeouts under high concurrency, or rate limits. "
                f"Full Response: {json.dumps(data)}"
            )

        # Extract ground-truth token counts from usage block (present on OpenRouter, llama.cpp)
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")

        parsed = strip_thinking(raw_completion)

        if output_format == "json":
            structured = _parse_json_format(parsed.clean_text)
            if not structured:
                structured = _parse_section_format(parsed.clean_text)
        else:
            structured = _parse_section_format(parsed.clean_text)

        # Log estimated vs actual token usage
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
