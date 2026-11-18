"""Crash-Safe Event Sourcing, Checkpoint Snapshot & Durability Engine.

Conforms to SMART_MONEY_PHASE2_SPEC_v2.1.5_FINAL.md (§ 12)
and SMART_MONEY_PHASE2_SPEC_v2.1.6_AMENDMENT.md.

Strictly Binance Spot Long/BUY only. No live trading, shadow mode only.
"""
from __future__ import annotations

import os
import sys
import json
import uuid
import hashlib
from decimal import Decimal
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple


SCHEMA_VERSION = "2.1.0"
STRATEGY_VERSION = "2.1.0"


class StateStoreError(Exception):
    """Base exception for state store operations."""
    pass


class RecoveryFailedHaltedError(StateStoreError):
    """Raised when an unrecoverable structural anomaly or security violation occurs.
    Enforces the RECOVERY_FAILED_HALTED fail-safe halting contract (§ 12.3.3).
    """
    pass


class PortableFileLock:
    """Advisory OS file lock with cross-platform support (Windows msvcrt / POSIX fcntl)."""
    def __init__(self, lockfile_path: str):
        self.lockfile_path = lockfile_path
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.lockfile_path)), exist_ok=True)
        self._fd = os.open(self.lockfile_path, os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                # If non-blocking lock fails, try blocking lock
                try:
                    msvcrt.locking(self._fd, msvcrt.LK_LOCK, 1)
                except Exception as e:
                    os.close(self._fd)
                    self._fd = None
                    raise StateStoreError(f"Failed to acquire Windows file lock: {e}") from e
        else:
            import fcntl
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            except Exception as e:
                os.close(self._fd)
                self._fd = None
                raise StateStoreError(f"Failed to acquire POSIX file lock: {e}") from e

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(self._fd)
        finally:
            self._fd = None

    def __enter__(self) -> "PortableFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


def compute_canonical_hash(payload: Dict[str, Any]) -> str:
    """Canonical JSON serialization and SHA-256 hash."""
    canonical_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()


def generate_event_id(
    version: str,
    symbol: str,
    timeframe: str,
    timestamp_ms: int,
    event_type: str,
    ordinal: int = 0
) -> str:
    """Authoritative UUIDv5 generation formula (§ 12.1)."""
    canonical_key = f"smart_money:{version}:{symbol}:{timeframe}:{timestamp_ms}:{event_type}:{ordinal}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_key))


@dataclass
class EventRecord:
    event_id: str
    event_sequence: int
    timestamp_ms: int
    symbol: str
    timeframe: str
    event_type: str
    ordinal: int
    payload: Dict[str, Any]
    payload_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_sequence": self.event_sequence,
            "timestamp_ms": self.timestamp_ms,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "event_type": self.event_type,
            "ordinal": self.ordinal,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventRecord":
        return cls(
            event_id=data["event_id"],
            event_sequence=data["event_sequence"],
            timestamp_ms=data["timestamp_ms"],
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            event_type=data["event_type"],
            ordinal=data["ordinal"],
            payload=data["payload"],
            payload_hash=data["payload_hash"],
        )


@dataclass
class CashLedger:
    initial_capital: Decimal = Decimal("100000.00")
    available_cash: Decimal = Decimal("100000.00")
    allocated_cost_basis: Decimal = Decimal("0.00")
    realized_pnl: Decimal = Decimal("0.00")
    currency: str = "USDT"

    def to_dict(self) -> Dict[str, str]:
        return {
            "initial_capital": str(self.initial_capital),
            "available_cash": str(self.available_cash),
            "allocated_cost_basis": str(self.allocated_cost_basis),
            "realized_pnl": str(self.realized_pnl),
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CashLedger":
        return cls(
            initial_capital=Decimal(str(d.get("initial_capital", "100000.00"))),
            available_cash=Decimal(str(d.get("available_cash", "100000.00"))),
            allocated_cost_basis=Decimal(str(d.get("allocated_cost_basis", "0.00"))),
            realized_pnl=Decimal(str(d.get("realized_pnl", "0.00"))),
            currency=str(d.get("currency", "USDT")),
        )

    def verify_invariant(self, tolerance: Decimal = Decimal("0.0001")) -> bool:
        """Double-Entry Cash Ledger Invariant (§ 9.3):
        InitialCapital + RealizedPnL == AvailableCash + AllocatedCostBasis
        """
        left = self.initial_capital + self.realized_pnl
        right = self.available_cash + self.allocated_cost_basis
        diff = abs(left - right)
        return diff <= tolerance


@dataclass
class SnapshotCheckpoint:
    schema_version: str = SCHEMA_VERSION
    strategy_version: str = STRATEGY_VERSION
    snapshot_timestamp_ms: int = 0
    last_applied_event_sequence: int = 0
    last_applied_event_id: str = ""
    last_applied_event_hash: str = ""
    positions: Dict[str, Any] = field(default_factory=dict)
    candidates: Dict[str, Any] = field(default_factory=dict)
    cash_ledger: CashLedger = field(default_factory=CashLedger)
    checksum: str = ""

    def compute_checksum(self) -> str:
        d = {
            "schema_version": self.schema_version,
            "strategy_version": self.strategy_version,
            "snapshot_timestamp_ms": self.snapshot_timestamp_ms,
            "last_applied_event_sequence": self.last_applied_event_sequence,
            "last_applied_event_id": self.last_applied_event_id,
            "last_applied_event_hash": self.last_applied_event_hash,
            "positions": self.positions,
            "candidates": self.candidates,
            "cash_ledger": self.cash_ledger.to_dict(),
        }
        return compute_canonical_hash(d)

    def to_dict(self) -> Dict[str, Any]:
        chk = self.compute_checksum()
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "schema_version": self.schema_version,
            "strategy_version": self.strategy_version,
            "snapshot_timestamp_ms": self.snapshot_timestamp_ms,
            "last_applied_event_sequence": self.last_applied_event_sequence,
            "last_applied_event_id": self.last_applied_event_id,
            "last_applied_event_hash": self.last_applied_event_hash,
            "positions": self.positions,
            "candidates": self.candidates,
            "cash_ledger": self.cash_ledger.to_dict(),
            "checksum": chk,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SnapshotCheckpoint":
        snapshot = cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            strategy_version=data.get("strategy_version", STRATEGY_VERSION),
            snapshot_timestamp_ms=int(data.get("snapshot_timestamp_ms", 0)),
            last_applied_event_sequence=int(data.get("last_applied_event_sequence", 0)),
            last_applied_event_id=str(data.get("last_applied_event_id", "")),
            last_applied_event_hash=str(data.get("last_applied_event_hash", "")),
            positions=dict(data.get("positions", {})),
            candidates=dict(data.get("candidates", {})),
            cash_ledger=CashLedger.from_dict(data.get("cash_ledger", {})),
            checksum=str(data.get("checksum", "")),
        )
        expected_chk = snapshot.compute_checksum()
        if snapshot.checksum and snapshot.checksum != expected_chk:
            raise RecoveryFailedHaltedError(
                f"Snapshot checksum mismatch: file={snapshot.checksum}, expected={expected_chk}"
            )
        return snapshot


class StateStore:
    """Fifteen-Stage Crash-Consistent Event Sourcing & Snapshot Store (§ 12.3)."""

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.journal_path = os.path.join(self.data_dir, "smart_money_events.jsonl")
        self.snapshot_path = os.path.join(self.data_dir, "smart_money_state.json")
        self.lock_path = os.path.join(self.data_dir, "smart_money.lock")
        self.lock = PortableFileLock(self.lock_path)

        # In-memory RAM state
        self.last_sequence: int = 0
        self.last_event_id: str = ""
        self.last_event_hash: str = ""
        self.processed_event_ids: Dict[str, str] = {}  # event_id -> payload_hash
        self.positions: Dict[str, Any] = {}
        self.candidates: Dict[str, Any] = {}
        self.cash_ledger: CashLedger = CashLedger()
        self.halted: bool = False
        self.halt_reason: Optional[str] = None

    def initialize_or_recover(self) -> None:
        """Execute Comprehensive Crash Recovery across Boundaries 1 through 9 (§ 12.3.2)."""
        with self.lock:
            # Stage 1: Clean up any orphaned .tmp snapshot files (Boundaries 6 & 7)
            self._cleanup_orphaned_tmp_files()

            # Stage 2: Recover / repair event journal if corrupted at EOF (Boundary 2)
            journal_events, journal_tail_seq = self._recover_and_load_journal()

            # Stage 3: Load snapshot if available
            snapshot = None
            if os.path.exists(self.snapshot_path):
                try:
                    with open(self.snapshot_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    snapshot = SnapshotCheckpoint.from_dict(data)
                except Exception as e:
                    if isinstance(e, RecoveryFailedHaltedError):
                        self.halted = True
                        self.halt_reason = str(e)
                        raise
                    raise RecoveryFailedHaltedError(f"Corrupted snapshot file: {e}") from e

            # Stage 4: Reconcile snapshot against journal (Boundary 5 / 8 / 9)
            if snapshot is not None:
                if snapshot.last_applied_event_sequence > journal_tail_seq:
                    self.halted = True
                    self.halt_reason = (
                        f"Snapshot sequence ({snapshot.last_applied_event_sequence}) "
                        f"exceeds journal tail ({journal_tail_seq})"
                    )
                    raise RecoveryFailedHaltedError(self.halt_reason)

                # Initialize RAM state from snapshot
                self.last_sequence = snapshot.last_applied_event_sequence
                self.last_event_id = snapshot.last_applied_event_id
                self.last_event_hash = snapshot.last_applied_event_hash
                self.positions = snapshot.positions
                self.candidates = snapshot.candidates
                self.cash_ledger = snapshot.cash_ledger

                # Replay any journal events that occurred after the snapshot
                for event in journal_events:
                    self.processed_event_ids[event.event_id] = event.payload_hash
                    if event.event_sequence > self.last_sequence:
                        self._apply_event_to_ram(event)
                        self.last_sequence = event.event_sequence
                        self.last_event_id = event.event_id
                        self.last_event_hash = event.payload_hash

                # If replayed events occurred, write updated snapshot
                if journal_tail_seq > snapshot.last_applied_event_sequence:
                    self._write_snapshot_atomic()
            else:
                # No snapshot exists; replay all events from journal if journal exists
                for event in journal_events:
                    self.processed_event_ids[event.event_id] = event.payload_hash
                    self._apply_event_to_ram(event)
                    self.last_sequence = event.event_sequence
                    self.last_event_id = event.event_id
                    self.last_event_hash = event.payload_hash

                if journal_events:
                    self._write_snapshot_atomic()

    def _cleanup_orphaned_tmp_files(self) -> None:
        """Deletes any abandoned .tmp files from prior interrupted writes."""
        for fname in os.listdir(self.data_dir):
            if fname.startswith("smart_money_state.json.tmp."):
                tmp_file = os.path.join(self.data_dir, fname)
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass

    def _recover_and_load_journal(self) -> Tuple[List[EventRecord], int]:
        """Loads and verifies the journal. Recovers EOF corruption; halts on interior corruption."""
        if not os.path.exists(self.journal_path):
            return [], 0

        with open(self.journal_path, "rb") as f:
            raw_bytes = f.read()

        if not raw_bytes:
            return [], 0

        lines = raw_bytes.split(b"\n")
        # Trailing empty byte after final newline
        if lines and lines[-1] == b"":
            lines = lines[:-1]

        events: List[EventRecord] = []
        expected_seq = 1
        corrupted_eof_detected = False

        for idx, line in enumerate(lines):
            is_last_line = (idx == len(lines) - 1)
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                record_dict = json.loads(line_str)
                event = EventRecord.from_dict(record_dict)
            except Exception as e:
                if is_last_line:
                    # Boundary 2: EOF corruption detected -> truncate EOF line
                    corrupted_eof_detected = True
                    break
                else:
                    # Interior journal corruption -> FAIL-SAFE HALT (§ 12.3.3)
                    self.halted = True
                    self.halt_reason = f"Interior journal corruption at line {idx+1}: {e}"
                    raise RecoveryFailedHaltedError(self.halt_reason) from e

            # Verify monotonic sequence
            if event.event_sequence != expected_seq:
                self.halted = True
                self.halt_reason = (
                    f"Sequence number gap at line {idx+1}: expected {expected_seq}, "
                    f"got {event.event_sequence}"
                )
                raise RecoveryFailedHaltedError(self.halt_reason)

            # Verify payload hash
            computed_hash = compute_canonical_hash(event.payload)
            if computed_hash != event.payload_hash:
                self.halted = True
                self.halt_reason = (
                    f"Payload hash mismatch at line {idx+1}: record={event.payload_hash}, "
                    f"computed={computed_hash}"
                )
                raise RecoveryFailedHaltedError(self.halt_reason)

            events.append(event)
            expected_seq += 1

        if corrupted_eof_detected:
            # Re-write journal with valid events up to EOF-1
            self._rewrite_clean_journal(events)

        tail_seq = events[-1].event_sequence if events else 0
        return events, tail_seq

    def _rewrite_clean_journal(self, events: List[EventRecord]) -> None:
        """Truncates journal safely to clean valid events."""
        tmp_journal = self.journal_path + ".recover.tmp"
        with open(tmp_journal, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev.to_dict(), separators=(',', ':')) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_journal, self.journal_path)

    def append_event(
        self,
        symbol: str,
        timeframe: str,
        timestamp_ms: int,
        event_type: str,
        payload: Dict[str, Any],
        ordinal: int = 0
    ) -> EventRecord:
        """Executes the authoritative 15-Stage Durability Protocol (§ 12.3)."""
        if self.halted:
            raise RecoveryFailedHaltedError(f"System halted: {self.halt_reason}")

        with self.lock:
            # Stage 2: Schema validation
            if not symbol or not event_type:
                raise StateStoreError("Invalid event parameters: symbol and event_type required")

            payload_hash = compute_canonical_hash(payload)

            # Stage 3: UUIDv5 Generation
            event_id = generate_event_id(
                version=STRATEGY_VERSION,
                symbol=symbol,
                timeframe=timeframe,
                timestamp_ms=timestamp_ms,
                event_type=event_type,
                ordinal=ordinal
            )

            # Idempotency check: Matching event_id
            if event_id in self.processed_event_ids:
                existing_hash = self.processed_event_ids[event_id]
                if existing_hash == payload_hash:
                    # Idempotent skip: already processed identical event
                    for ev_line in self._load_recent_events():
                        if ev_line.event_id == event_id:
                            return ev_line
                else:
                    # Payload mismatch on duplicate UUID -> History rewrite -> HALT
                    self.halted = True
                    self.halt_reason = (
                        f"Collision/rewrite detected for event_id {event_id}: "
                        f"existing_hash={existing_hash}, new_hash={payload_hash}"
                    )
                    raise RecoveryFailedHaltedError(self.halt_reason)

            # Stage 4: Monotonic sequence allocation
            next_sequence = self.last_sequence + 1

            record = EventRecord(
                event_id=event_id,
                event_sequence=next_sequence,
                timestamp_ms=timestamp_ms,
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                ordinal=ordinal,
                payload=payload,
                payload_hash=payload_hash,
            )

            # Stage 5, 6, 7: Append to journal, flush, fsync
            serialized_line = json.dumps(record.to_dict(), separators=(',', ':')) + "\n"
            with open(self.journal_path, "a", encoding="utf-8") as f_journal:
                f_journal.write(serialized_line)
                f_journal.flush()
                os.fsync(f_journal.fileno())

            # Stage 8: Mutate in-memory RAM state
            self._apply_event_to_ram(record)
            self.last_sequence = next_sequence
            self.last_event_id = event_id
            self.last_event_hash = payload_hash
            self.processed_event_ids[event_id] = payload_hash

            # Stages 9 through 14: Atomic Snapshot Checkpoint
            self._write_snapshot_atomic()

            return record

    def _apply_event_to_ram(self, record: EventRecord) -> None:
        """Applies event payload to in-memory state objects."""
        etype = record.event_type
        payload = record.payload
        sym = record.symbol

        if etype in ("BREAKOUT_DETECTED", "RETEST_PENDING"):
            self.candidates[sym] = payload
        elif etype in ("CONTINUATION_CONFIRMED", "RETEST_CONFIRMED"):
            self.candidates[sym] = payload
        elif etype in ("STRUCTURE_INVALIDATED", "EXPIRED", "INVALIDATED_RISK_REJECTED"):
            self.candidates.pop(sym, None)
        elif etype in ("POSITION_ACCEPTED", "POSITION_OPEN"):
            self.candidates.pop(sym, None)
            self.positions[sym] = payload
        elif etype in ("TP1_EXECUTED", "TRAILING_STOP_RATCHETED"):
            if sym in self.positions:
                self.positions[sym].update(payload)
        elif etype in (
            "STOPPED_OUT_FULL",
            "BREAKEVEN_STOPPED_OUT",
            "TRAILING_STOPPED_OUT",
            "TP2_EXECUTED_FULL",
            "TIME_STOP_EXIT",
            "CLOSED_TERMINAL"
        ):
            self.positions.pop(sym, None)

        # Update cash ledger if present in payload
        if "cash_ledger_update" in payload:
            cl_data = payload["cash_ledger_update"]
            if "available_cash" in cl_data:
                self.cash_ledger.available_cash = Decimal(str(cl_data["available_cash"]))
            if "allocated_cost_basis" in cl_data:
                self.cash_ledger.allocated_cost_basis = Decimal(str(cl_data["allocated_cost_basis"]))
            if "realized_pnl" in cl_data:
                self.cash_ledger.realized_pnl = Decimal(str(cl_data["realized_pnl"]))

    def _write_snapshot_atomic(self) -> None:
        """Stages 9-14: Write snapshot to temporary file, fsync, close handle, atomic replace."""
        snapshot = SnapshotCheckpoint(
            schema_version=SCHEMA_VERSION,
            strategy_version=STRATEGY_VERSION,
            snapshot_timestamp_ms=int(Decimal(str(self.last_sequence)) * 1000),
            last_applied_event_sequence=self.last_sequence,
            last_applied_event_id=self.last_event_id,
            last_applied_event_hash=self.last_event_hash,
            positions=self.positions,
            candidates=self.candidates,
            cash_ledger=self.cash_ledger,
        )

        snapshot_dict = snapshot.to_dict()
        snapshot_json = json.dumps(snapshot_dict, indent=2, sort_keys=True)

        tmp_filename = f"smart_money_state.json.tmp.{uuid.uuid4().hex}"
        tmp_path = os.path.join(self.data_dir, tmp_filename)

        # Stage 10, 11, 12, 13: Write, flush, fsync, and close handle before rename
        with open(tmp_path, "w", encoding="utf-8") as f_tmp:
            f_tmp.write(snapshot_json)
            f_tmp.flush()
            os.fsync(f_tmp.fileno())

        # Stage 14: Atomic rename
        os.replace(tmp_path, self.snapshot_path)

    def _load_recent_events(self) -> List[EventRecord]:
        if not os.path.exists(self.journal_path):
            return []
        events = []
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    events.append(EventRecord.from_dict(json.loads(line_str)))
        return events

    def get_snapshot(self) -> SnapshotCheckpoint:
        """Returns in-memory snapshot state."""
        return SnapshotCheckpoint(
            schema_version=SCHEMA_VERSION,
            strategy_version=STRATEGY_VERSION,
            snapshot_timestamp_ms=0,
            last_applied_event_sequence=self.last_sequence,
            last_applied_event_id=self.last_event_id,
            last_applied_event_hash=self.last_event_hash,
            positions=dict(self.positions),
            candidates=dict(self.candidates),
            cash_ledger=self.cash_ledger,
        )
