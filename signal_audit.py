#!/usr/bin/env python3
"""Thread-safe BUY-signal rejection accounting and diagnostic breakdowns."""
from __future__ import annotations

from collections import Counter, defaultdict
from threading import Lock


class SignalAudit:
    """Collect stage, interval, and optional setup diagnostics for one scan run."""

    def __init__(self, near_miss_limit=200):
        self._counts = Counter()
        self._by_interval = defaultdict(Counter)
        self._by_setup = defaultdict(Counter)
        self._near_misses = []
        self._near_miss_shadows = []
        self._next_event_id = 0
        self._near_miss_limit = max(1, int(near_miss_limit))
        self._lock = Lock()

    def _record(self, stage, interval=None, setup=None):
        stage = str(stage)
        with self._lock:
            self._counts[stage] += 1
            if interval:
                self._by_interval[str(interval)][stage] += 1
            if setup:
                self._by_setup[str(setup)][stage] += 1

    def reject(self, stage, interval=None, setup=None, details=None):
        self._record(stage, interval=interval, setup=setup)
        if details:
            with self._lock:
                self._next_event_id += 1
                self._near_misses.append({
                    "event_id": self._next_event_id,
                    "stage": str(stage),
                    "interval": str(interval) if interval else None,
                    "setup": str(setup) if setup else None,
                    **dict(details),
                })
                self._near_misses.sort(key=lambda x: float(x.get("near_miss_score", 0.0)), reverse=True)
                del self._near_misses[self._near_miss_limit:]

    def record_near_miss_shadow(self, details):
        with self._lock:
            self._near_miss_shadows.append(dict(details))

    def near_miss_shadow_snapshot(self):
        with self._lock:
            return [dict(item) for item in self._near_miss_shadows]

    def accept(self, stage="qualified", interval=None, setup=None):
        self._record(stage, interval=interval, setup=setup)

    def snapshot(self):
        with self._lock:
            return dict(sorted(self._counts.items()))

    def interval_snapshot(self):
        with self._lock:
            return {
                interval: dict(sorted(counts.items()))
                for interval, counts in sorted(self._by_interval.items())
            }

    def setup_snapshot(self):
        with self._lock:
            return {
                setup: dict(sorted(counts.items()))
                for setup, counts in sorted(self._by_setup.items())
            }

    def total_rejections(self):
        with self._lock:
            return sum(self._counts.values()) - self._counts.get("qualified", 0)

    def total_attempts(self):
        with self._lock:
            return sum(self._counts.values())

    def near_miss_snapshot(self):
        with self._lock:
            return [dict(item) for item in self._near_misses]

    def summary(self, scans=0, hits=0, fresh=0, selected=0):
        with self._lock:
            counts = dict(sorted(self._counts.items()))
            intervals = {
                k: dict(sorted(v.items()))
                for k, v in sorted(self._by_interval.items())
            }
            setups = {
                k: dict(sorted(v.items()))
                for k, v in sorted(self._by_setup.items())
            }
        rejected = sum(counts.values()) - counts.get("qualified", 0)
        qualified = counts.get("qualified", 0)
        attempts = rejected + qualified
        return {
            "scans": int(scans),
            "hits": int(hits),
            "fresh": int(fresh),
            "selected": int(selected),
            "attempts": attempts,
            "qualified": qualified,
            "rejected": rejected,
            "qualification_rate_pct": round(qualified / attempts * 100.0, 2) if attempts else 0.0,
            "stages": counts,
            "by_interval": intervals,
            "by_setup": setups,
            "near_misses": [dict(item) for item in self._near_misses],
            "near_miss_shadows": [dict(item) for item in self._near_miss_shadows],
        }

    def format_line(self):
        parts = [f"{k}={v}" for k, v in self.snapshot().items()]
        return " ".join(parts)
