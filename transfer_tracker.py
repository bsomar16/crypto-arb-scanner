#!/usr/bin/env python3
"""Restart-safe transfer state tracking for SPOT arbitrage legs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Optional


@dataclass
class Transfer:
    id: str
    asset: str
    amount: float
    source_exchange: str
    destination_exchange: str
    network: str
    status: str = "PENDING"
    txid: Optional[str] = None
    created_ms: int = 0
    updated_ms: int = 0
    error: Optional[str] = None


TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
_ALLOWED = {
    "PENDING": {"SUBMITTED", "FAILED", "CANCELLED"},
    "SUBMITTED": {"CONFIRMING", "COMPLETED", "FAILED"},
    "CONFIRMING": {"COMPLETED", "FAILED"},
}


class TransferTracker:
    def __init__(self, state_dir: str = "state"):
        self.path = Path(state_dir) / "transfers.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.transfers = self._recover()

    def create(self, transfer: Transfer) -> Transfer:
        if transfer.id in self.transfers:
            return self.transfers[transfer.id]
        self.transfers[transfer.id] = transfer
        self._write(transfer)
        return transfer

    def transition(self, transfer_id: str, status: str, *, txid: Optional[str] = None,
                   error: Optional[str] = None, updated_ms: int = 0) -> Transfer:
        t = self.transfers[transfer_id]
        if status not in _ALLOWED.get(t.status, set()):
            raise ValueError(f"invalid transfer transition: {t.status} -> {status}")
        t.status = status
        if txid:
            t.txid = txid
        if error:
            t.error = error
        t.updated_ms = updated_ms
        self._write(t)
        return t

    def active(self):
        return [t for t in self.transfers.values() if t.status not in TERMINAL]

    def _recover(self):
        latest = {}
        if not self.path.exists():
            return latest
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                latest[row["id"]] = Transfer(**row)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return latest

    def _write(self, transfer: Transfer):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(transfer), separators=(",", ":")) + "\n")
