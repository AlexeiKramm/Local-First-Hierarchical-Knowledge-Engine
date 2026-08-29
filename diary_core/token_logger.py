"""
token_logger.py
===============
Logs estimated vs actual token counts for every LLM API call to a CSV file.
Over time this data helps calibrate the CHARS_PER_TOKEN heuristic in
token_estimator.py (currently 3.2 chars/token).

Records one row per API call with:
  - timestamp, model name
  - estimated_input_tokens: pre-flight estimate (chars / 3.2), if provided by caller
  - actual_input_tokens:   ground-truth prompt tokens from API usage block
  - actual_output_tokens:  ground-truth completion tokens from API usage block
  - char_count:            combined length of system + user prompt text
  - empirical_ratio:       char_count / actual_input_tokens (the key calibration value)

The CSV is append-only — never overwritten. Header is written only on first call.
Thread-safe: writes are serialised via a module-level ``threading.Lock`` so that
concurrent calls from sync and async contexts do not interleave rows.
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


# Default log path — under logs/ directory
DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "token_usage.csv"

# Module-level lock serialises CSV writes from sync and async callers
_LOG_LOCK = threading.Lock()

CSV_COLUMNS = [
    "timestamp",
    "model",
    "estimated_input_tokens",
    "actual_input_tokens",
    "actual_output_tokens",
    "char_count",
    "empirical_ratio",
]


def log_api_call(
    *,
    estimated_input_tokens: Optional[int] = None,
    actual_input_tokens: Optional[int] = None,
    actual_output_tokens: Optional[int] = None,
    char_count: Optional[int] = None,
    model: str = "unknown",
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> None:
    """Append one row to the token usage CSV. Creates file + header on first call.

    Args:
        estimated_input_tokens: Pre-flight estimate (chars / 3.2), if available.
        actual_input_tokens: Ground-truth prompt tokens from API usage block.
        actual_output_tokens: Ground-truth completion tokens from API usage block.
        char_count: Length of the combined system + user prompt text in characters.
        model: Model name as returned by the API.
        log_path: Path to the CSV file. Defaults to ``token_usage_log.csv``
            in the project root.
    """
    # Compute empirical ratio only when both values are available and sensible
    if actual_input_tokens and actual_input_tokens > 0 and char_count and char_count > 0:
        empirical_ratio = round(char_count / actual_input_tokens, 4)
    else:
        empirical_ratio = ""

    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "estimated_input_tokens": estimated_input_tokens if estimated_input_tokens is not None else "",
        "actual_input_tokens": actual_input_tokens if actual_input_tokens is not None else "",
        "actual_output_tokens": actual_output_tokens if actual_output_tokens is not None else "",
        "char_count": char_count if char_count is not None else "",
        "empirical_ratio": empirical_ratio,
    }

    # Thread-safe: lock guards the TOCTOU gap between exists-check and write,
    # and prevents interleaved rows when multiple concurrent complete_async()
    # calls fire log_api_call() simultaneously.
    _lock: threading.Lock = _LOG_LOCK
    with _lock:
        file_path = Path(log_path)
        file_exists = file_path.exists()

        with open(file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
