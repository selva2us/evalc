"""
state.py
========
A tiny JSON-backed checkpoint store. Each pipeline stage marks per-item
progress here (per PDF page, per curriculum page id, per module) so that
re-running the pipeline on a large PDF or a large subject skips work
that's already done, instead of starting over from page 1.

State file layout (work/state.json):
{
  "skeleton": {"status": "done", "path": "work/skeleton.json"},
  "extract":  {"page-12": "done", "page-13": "failed:reason"},
  "rewrite":  {"2802": "done", "2803": "pending"},
  "images":   {"2802": "done"},
  "upload":   {"2802": "done"},
  "pdf":      {"status": "done", "path": "work/final.pdf"}
}
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        if self.path.exists():
            self.data: dict[str, Any] = json.loads(self.path.read_text())
        else:
            self.data = {}
            self._flush()

    def _flush(self):
        self.path.write_text(json.dumps(self.data, indent=2))

    def get_stage(self, stage: str) -> dict:
        return self.data.setdefault(stage, {})

    def is_done(self, stage: str, key: str) -> bool:
        return self.get_stage(stage).get(key) == "done"

    def mark(self, stage: str, key: str, status: str = "done"):
        with self._lock:
            self.get_stage(stage)[key] = status
            self._flush()

    def set_meta(self, stage: str, **kwargs):
        with self._lock:
            self.get_stage(stage).update(kwargs)
            self._flush()

    def get_meta(self, stage: str) -> dict:
        return self.get_stage(stage)
