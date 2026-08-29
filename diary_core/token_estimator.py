"""
token_estimator.py
==================
Single source of truth for token-count estimation across the project.
All callers must use `estimate()` rather than duplicating the heuristic.

Rough token-count estimation before sending requests to the LLM.
Uses a character-to-token heuristic (suitable for English/mixed text):
    tokens ≈ characters / 3.2

Also provides context-window warning utilities.
"""

from __future__ import annotations
from dataclasses import dataclass


# Characters per token — conservative estimate for English, works for most SLMs
CHARS_PER_TOKEN: float = 3.2

CONTEXT_SIZES = {
    "32k":  32_768,
    "64k":  65_536,
    "96k":  98_304,
    "128k": 131_072,
    "160k": 163_840,
    "192k": 196_608,
    "224k": 229_376,
    "256k": 262_144,
}


@dataclass
class TokenEstimate:
    char_count: int
    estimated_tokens: int
    context_window: int
    usage_fraction: float       # 0.0 – 1.0
    status: str                 # "ok" | "warn" | "danger"

    @property
    def status_label(self) -> str:
        return {
            "ok":     "✓ Within budget",
            "warn":   "⚠ Approaching limit",
            "danger": "✗ May exceed context window",
        }.get(self.status, "?")


def estimate(text: str, context_window: int = 32_768, output_budget: int = 1024) -> TokenEstimate:
    """
    Estimate whether `text` fits inside `context_window` given an expected output budget.

    Args:
        text:            The prompt text (system + user combined).
        context_window:  Total context window of the model in tokens.
        output_budget:   Tokens reserved for model output.

    Returns:
        TokenEstimate with usage fraction and a status string.
    """
    chars = len(text)
    tokens = int(chars / CHARS_PER_TOKEN)
    effective_window = context_window - output_budget
    fraction = tokens / effective_window if effective_window > 0 else 1.0

    if fraction < 0.50:
        status = "ok"
    elif fraction < 0.80:
        status = "warn"
    else:
        status = "danger"

    return TokenEstimate(
        char_count=chars,
        estimated_tokens=tokens,
        context_window=context_window,
        usage_fraction=fraction,
        status=status,
    )


def context_window_from_label(label: str) -> int:
    """Convert a human-readable label like '32k' to an integer."""
    return CONTEXT_SIZES.get(label, 32_768)
