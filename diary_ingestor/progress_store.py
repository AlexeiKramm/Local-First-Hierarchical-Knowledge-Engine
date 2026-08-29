"""
progress_store.py
=================
Tracks which source files have been ingested, maintains per-file
classification save-states, and manages the processed_sources.json manifest.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


_MANIFEST_FILE = "processed_sources.json"


class ProgressStore:
    """
    Manages the processed_sources.json manifest file in a chosen output directory.
    Also provides helpers for per-file classification checkpoints.
    """

    def __init__(self, store_dir: str):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.store_dir / _MANIFEST_FILE
        self._data: dict = self._load()

    # ── Manifest ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._manifest_path.exists():
            try:
                with open(self._manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"sources": {}}

    def _save(self):
        with open(self._manifest_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def is_processed(self, filepath: str) -> bool:
        """Return True if this file was already processed and hasn't changed."""
        key = str(filepath)
        if key not in self._data["sources"]:
            return False
        stored_mtime = self._data["sources"][key].get("mtime", -1)
        try:
            current_mtime = os.path.getmtime(filepath)
        except OSError:
            return False
        return abs(stored_mtime - current_mtime) < 1.0   # 1-second tolerance

    def mark_processed(self, filepath: str, source_type: str, entries_added: int,
                       extra: Optional[dict] = None):
        from datetime import datetime
        rec = {
            "type": source_type,
            "mtime": os.path.getmtime(filepath),
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "entries_added": entries_added,
        }
        if extra:
            rec.update(extra)
        self._data["sources"][str(filepath)] = rec
        self._save()

    # ── Classification checkpoints ────────────────────────────────────────

    def _checkpoint_path(self, filepath: str) -> Path:
        stem = Path(filepath).stem
        return self.store_dir / f"{stem}_classify_checkpoint.json"

    def load_classify_checkpoint(self, filepath: str) -> dict[int, str]:
        """
        Returns {message_index: category} for already-classified messages.
        Returns empty dict if no checkpoint or checkpoint doesn't exist.
        """
        cp = self._checkpoint_path(filepath)
        if not cp.exists():
            return {}
        try:
            with open(cp, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Keys are stored as strings in JSON; convert back to int
            return {int(k): v for k, v in raw.items()}
        except Exception:
            return {}

    def save_classify_checkpoint(self, filepath: str, checkpoint: dict[int, str]):
        """Persist {index: category} checkpoint."""
        cp = self._checkpoint_path(filepath)
        with open(cp, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in checkpoint.items()}, f, indent=2)

    def clear_classify_checkpoint(self, filepath: str):
        """Delete an existing checkpoint (force-reclassify mode)."""
        cp = self._checkpoint_path(filepath)
        if cp.exists():
            cp.unlink()

    # ── Date-extraction checkpoints (Old Diary) ───────────────────────────

    def _date_cache_path(self, filepath: str) -> Path:
        stem = Path(filepath).stem
        return self.store_dir / f"{stem}_dates_cache.json"

    def load_date_cache(self, filepath: str) -> list[dict]:
        cp = self._date_cache_path(filepath)
        if not cp.exists():
            return []
        try:
            with open(cp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_date_cache(self, filepath: str, entries: list[dict]):
        cp = self._date_cache_path(filepath)
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)

    def clear_date_cache(self, filepath: str):
        cp = self._date_cache_path(filepath)
        if cp.exists():
            cp.unlink()
