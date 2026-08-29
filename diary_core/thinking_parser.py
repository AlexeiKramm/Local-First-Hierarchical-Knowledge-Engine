"""
thinking_parser.py
==================
Strips <think>...</think> blocks from LLM output.
Returns both the clean response and the raw thinking trace for storage.
"""

from __future__ import annotations
import re
from dataclasses import dataclass


_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


@dataclass
class ParsedResponse:
    """Container for a split LLM response."""
    clean_text: str           # model output with <think> blocks removed
    thinking_trace: str | None  # content of the first <think> block, or None


def strip_thinking(raw_output: str) -> ParsedResponse:
    """
    Remove all <think>...</think> blocks from `raw_output`.
    The first block's content is preserved as `thinking_trace`.
    Remaining text is stripped of leading/trailing whitespace.
    """
    traces = _THINK_RE.findall(raw_output)
    thinking_trace = traces[0].strip() if traces else None
    clean_text = _THINK_RE.sub("", raw_output).strip()
    return ParsedResponse(clean_text=clean_text, thinking_trace=thinking_trace)
