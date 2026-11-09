#!/usr/bin/env python3
"""Thread-safe BUY-signal rejection accounting for diagnostics."""
from __future__ import annotations
from collections import Counter
from threading import Lock

class SignalAudit:
    def __init__(self):
        self._counts = Counter()
        self._lock = Lock()

    def reject(self, stage):
        with self._lock:
            self._counts[str(stage)] += 1

    def accept(self, stage="qualified"):
        with self._lock:
            self._counts[str(stage)] += 1

    def snapshot(self):
        with self._lock:
            return dict(sorted(self._counts.items()))

    def total_rejections(self):
        with self._lock:
            return sum(self._counts.values()) - self._counts.get("qualified", 0)

    def format_line(self):
        parts = [f"{k}={v}" for k, v in self.snapshot().items()]
        return " ".join(parts)
