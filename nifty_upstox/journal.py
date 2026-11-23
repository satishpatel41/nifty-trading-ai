"""Append-only JSONL trade journal (trader-memory-core style): every plan, gate result, order and exit."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class Journal:
    def __init__(self, state_dir: str | Path):
        self.path = Path(state_dir) / "journal.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields) -> None:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
        with self.path.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
