"""Unit and Boundary Test Suite for Smart Money Phase 2 — Stage 2.

Covers:
- state_store.py: 15-stage durability, UUIDv5 identity, snapshot checksums, crash recovery Boundaries 1-9,
  and fail-safe halting (RECOVERY_FAILED_HALTED).
- portfolio.py: 5-slot limit, FIFO confirmation queue, macro surges, 5-key intra-bar ranking,
  fee-adjusted sizing, cash constraints, and double-entry cash ledger invariants.
- position_manager.py: stop-loss monotonicity supremum invariant, master execution matrix (Scenarios A-L),
  gap execution, breakeven formulas, and trailing ratchets.

Strictly Binance Spot Long/BUY only. Zero live trading, shadow mode only.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from decimal import Decimal

from smart_money.state_store import (
    StateStore,
    CashLedger,
    SnapshotCheckpoint,
    EventRecord,
    generate_event_id,
    compute_canonical_hash,
    RecoveryFailedHaltedError,
    SCHEMA_VERSION,
    STRATEGY_VERSION,
)
from smart_money.portfolio import (
    PortfolioCoordinator,
    CandidateRankingEntry,
    MAX_CONCURRENT_POSITIONS,
    BASE_FEE_RATE,
    QUOTE_FEE_RATE,
)
from smart_money.position_manager import (
    ActivePosition,
    CandleBar,
    ExitExecution,
    create_active_position,
    FEE_RATE,
    STOP_SLIPPAGE,
)


class TestStateStoreDurabilityAndRecovery(unittest.TestCase):
    """Verifies § 12 Crash-Safe Event Sourcing, Snapshots, and Crash Recovery."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_uuidv5_deterministic_identity(self):
        """Verifies authoritative UUIDv5 generation formula is deterministic and RFC 4122 compliant."""
        id1 = generate_event_id("2.1.0", "BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", 0)
        id2 = generate_event_id("2.1.0", "BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", 0)
        id_diff_ord = generate_event_id("2.1.0", "BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", 1)
        id_diff_sym = generate_event_id("2.1.0", "ETHUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", 0)

        self.assertEqual(id1, id2)
        self.assertNotEqual(id1, id_diff_ord)
        self.assertNotEqual(id1, id_diff_sym)
        self.assertEqual(len(id1), 36)

    def test_fifteen_stage_durability_and_monotonic_sequence(self):
        """Verifies 15-stage append protocol maintains monotonic sequence numbers."""
        store = StateStore(self.test_dir)
        store.initialize_or_recover()

        ev1 = store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)
        ev2 = store.append_event("ETHUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 80.0}, 0)
        ev3 = store.append_event("SOLUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 70.0}, 0)

        self.assertEqual(ev1.event_sequence, 1)
        self.assertEqual(ev2.event_sequence, 2)
        self.assertEqual(ev3.event_sequence, 3)
        self.assertEqual(store.last_sequence, 3)

        snap = store.get_snapshot()
        self.assertEqual(snap.last_applied_event_sequence, 3)
        self.assertEqual(snap.last_applied_event_id, ev3.event_id)

    def test_idempotent_duplicate_event_skip(self):
        """Verifies duplicate event with identical UUID and payload hash is skipped idempotently."""
        store = StateStore(self.test_dir)
        store.initialize_or_recover()

        ev1 = store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)
        ev2 = store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)

        self.assertEqual(ev1.event_id, ev2.event_id)
        self.assertEqual(store.last_sequence, 1)

    def test_fail_safe_halting_on_payload_rewrite_collision(self):
        """Verifies attempting to append different payload with identical UUID triggers RECOVERY_FAILED_HALTED."""
        store = StateStore(self.test_dir)
        store.initialize_or_recover()

        store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)

        with self.assertRaises(RecoveryFailedHaltedError):
            # Same canonical key, differing payload
            store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 99.9}, 0)

        self.assertTrue(store.halted)

    def test_crash_recovery_boundary_2_eof_line_corruption(self):
        """Verifies Boundary 2: partial/corrupted EOF line in journal is truncated safely and state restored."""
        store = StateStore(self.test_dir)
        store.initialize_or_recover()
        store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)
        store.append_event("ETHUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 80.0}, 0)

        # Simulate crash mid-write at EOF by corrupting the journal tail
        with open(store.journal_path, "a", encoding="utf-8") as f:
            f.write('{"event_id": "corrupted-partial-line-without-close')

        # Re-initialize new store instance to test startup recovery
        recovered_store = StateStore(self.test_dir)
        recovered_store.initialize_or_recover()

        self.assertFalse(recovered_store.halted)
        self.assertEqual(recovered_store.last_sequence, 2)
        self.assertEqual(len(recovered_store.candidates), 2)

    def test_crash_recovery_boundary_6_and_7_orphaned_tmp_files(self):
        """Verifies Boundaries 6 & 7: lingering .tmp files from crashed snapshots are cleaned up on startup."""
        store = StateStore(self.test_dir)
        store.initialize_or_recover()
        store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)

        # Create orphan .tmp snapshot file
        orphan_tmp = os.path.join(self.test_dir, "smart_money_state.json.tmp.deadbeef1234")
        with open(orphan_tmp, "w", encoding="utf-8") as f:
            f.write('{"incomplete": true}')

        self.assertTrue(os.path.exists(orphan_tmp))

        # Re-initialize
        recovered_store = StateStore(self.test_dir)
        recovered_store.initialize_or_recover()

        self.assertFalse(os.path.exists(orphan_tmp))
        self.assertEqual(recovered_store.last_sequence, 1)

    def test_crash_recovery_boundary_5_replay_from_snapshot(self):
        """Verifies Boundary 5: events occurring after last snapshot are replayed from journal on startup."""
        store = StateStore(self.test_dir)
        store.initialize_or_recover()
        store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)

        # Snapshot is currently at sequence 1. Add events 2 and 3 directly to journal without updating snapshot
        ev2_id = generate_event_id("2.1.0", "ETHUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", 0)
        ev2_hash = compute_canonical_hash({"score": 80.0})
        ev2_rec = {
            "event_id": ev2_id,
            "event_sequence": 2,
            "timestamp_ms": 1731600000000,
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "event_type": "BREAKOUT_DETECTED",
            "ordinal": 0,
            "payload": {"score": 80.0},
            "payload_hash": ev2_hash
        }
        with open(store.journal_path, "a", encoding="utf-8") as f:
            import json
            f.write(json.dumps(ev2_rec) + "\n")

        recovered_store = StateStore(self.test_dir)
        recovered_store.initialize_or_recover()

        self.assertEqual(recovered_store.last_sequence, 2)
        self.assertIn("ETHUSDT", recovered_store.candidates)

    def test_fail_safe_halting_on_interior_journal_corruption(self):
        """Verifies interior corruption before EOF triggers RECOVERY_FAILED_HALTED (§ 12.3.3)."""
        store = StateStore(self.test_dir)
        store.initialize_or_recover()
        store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)
        store.append_event("ETHUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 80.0}, 0)
        store.append_event("SOLUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 85.0}, 0)

        # Corrupt the 2nd line (interior line, not EOF)
        with open(store.journal_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        lines[1] = '{"corrupted": "interior data decay"\n'
        with open(store.journal_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        recovered_store = StateStore(self.test_dir)
        with self.assertRaises(RecoveryFailedHaltedError):
            recovered_store.initialize_or_recover()

        self.assertTrue(recovered_store.halted)

    def test_fail_safe_halting_on_sequence_number_gap(self):
        """Verifies a sequence number gap triggers RECOVERY_FAILED_HALTED (§ 12.3.3)."""
        store = StateStore(self.test_dir)
        store.initialize_or_recover()
        store.append_event("BTCUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", {"score": 75.0}, 0)

        # Inject event with sequence 3 directly (skipping sequence 2)
        ev_gap_id = generate_event_id("2.1.0", "ETHUSDT", "1h", 1731600000000, "BREAKOUT_DETECTED", 0)
        payload = {"score": 80.0}
        ev_gap_rec = {
            "event_id": ev_gap_id,
            "event_sequence": 3,  # GAP: 1 -> 3
            "timestamp_ms": 1731600000000,
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "event_type": "BREAKOUT_DETECTED",
            "ordinal": 0,
            "payload": payload,
            "payload_hash": compute_canonical_hash(payload)
        }
        with open(store.journal_path, "a", encoding="utf-8") as f:
            import json
            f.write(json.dumps(ev_gap_rec) + "\n")

        recovered_store = StateStore(self.test_dir)
        with self.assertRaises(RecoveryFailedHaltedError):
            recovered_store.initialize_or_recover()

        self.assertTrue(recovered_store.halted)


class TestPortfolioCoordinatorAndQueue(unittest.TestCase):
    """Verifies § 10 Global Portfolio Allocation, Macro Surges, FIFO Queue, and Risk Gates."""

    def setUp(self):
        self.cl = CashLedger(
            initial_capital=Decimal("10000.00"),
            available_cash=Decimal("10000.00"),
            allocated_cost_basis=Decimal("0.00"),
            realized_pnl=Decimal("0.00")
        )
        self.pc = PortfolioCoordinator(cash_ledger=self.cl, max_positions=5)

    def test_macro_surge_detection_threshold(self):
        """Verifies macro surge activates when candidate count M >= 4 (§ 10.1)."""
        cands_3 = [
            CandidateRankingEntry("BTCUSDT", 1000, 80.0, 75.0, 0.40, 2.5, Decimal("100"), Decimal("98"), "RETEST"),
            CandidateRankingEntry("ETHUSDT", 1000, 75.0, 70.0, 0.35, 2.2, Decimal("50"), Decimal("48"), "RETEST"),
            CandidateRankingEntry("SOLUSDT", 1000, 70.0, 65.0, 0.30, 2.0, Decimal("20"), Decimal("19"), "RETEST"),
        ]
        self.pc.process_closed_bar_candidates(1000, cands_3)
        self.assertFalse(self.pc.macro_surge_active)

        cands_4 = cands_3 + [
            CandidateRankingEntry("BNBUSDT", 1000, 68.0, 60.0, 0.25, 1.8, Decimal("300"), Decimal("295"), "RETEST")
        ]
        self.pc.process_closed_bar_candidates(1000, cands_4)
        self.assertTrue(self.pc.macro_surge_active)

    def test_intra_bar_deterministic_ranking_five_key(self):
        """Verifies 5-key intra-bar candidate ranking hierarchy (§ 10.2)."""
        c1 = CandidateRankingEntry("BTCUSDT", 1000, 80.0, 70.0, 0.30, 2.0, Decimal("100"), Decimal("98"), "RETEST")
        c2 = CandidateRankingEntry("ETHUSDT", 1000, 85.0, 60.0, 0.20, 1.5, Decimal("50"), Decimal("48"), "RETEST")
        c3 = CandidateRankingEntry("SOLUSDT", 1000, 80.0, 75.0, 0.30, 2.0, Decimal("20"), Decimal("19"), "RETEST")
        c4 = CandidateRankingEntry("ADAUSDT", 1000, 80.0, 70.0, 0.30, 2.0, Decimal("1"), Decimal("0.9"), "RETEST")

        sorted_cands = self.pc.process_closed_bar_candidates(1000, [c1, c2, c3, c4])

        # Expected order:
        # 1. c2: S_break=85.0 (highest score)
        # 2. c3: S_break=80.0, S_ready=75.0 (higher readiness than c1/c4)
        # 3. c4: S_break=80.0, S_ready=70.0, delta=0.30, RVOL=2.0, symbol "ADAUSDT" (lexicographical before "BTCUSDT")
        # 4. c1: S_break=80.0, S_ready=70.0, delta=0.30, RVOL=2.0, symbol "BTCUSDT"
        self.assertEqual(sorted_cands[0].symbol, "ETHUSDT")
        self.assertEqual(sorted_cands[1].symbol, "SOLUSDT")
        self.assertEqual(sorted_cands[2].symbol, "ADAUSDT")
        self.assertEqual(sorted_cands[3].symbol, "BTCUSDT")

    def test_fifo_capacity_allocation_and_queue_promotion(self):
        """Verifies FIFO queue holds candidates when 5 slots are full and promotes upon exit."""
        # Fill 5 slots
        for i in range(5):
            cand = CandidateRankingEntry(f"SYM{i}USDT", 1000 + i, 80.0, 70.0, 0.3, 2.0, Decimal("10"), Decimal("9"), "RETEST")
            enqueued = self.pc.enqueue_confirmed_candidate(cand)
            self.assertTrue(enqueued)
            self.pc.commit_position_entry(cand.symbol, Decimal("10"), Decimal("10"))

        self.assertEqual(self.pc.available_slots, 0)

        # 6th and 7th candidates arrive
        cand6 = CandidateRankingEntry("SYM6USDT", 1100, 90.0, 90.0, 0.5, 3.0, Decimal("10"), Decimal("9"), "RETEST")
        cand7 = CandidateRankingEntry("SYM7USDT", 1105, 95.0, 95.0, 0.6, 3.5, Decimal("10"), Decimal("9"), "RETEST")

        self.assertFalse(self.pc.enqueue_confirmed_candidate(cand6))
        self.assertFalse(self.pc.enqueue_confirmed_candidate(cand7))
        self.assertEqual(len(self.pc.fifo_confirmation_queue), 2)

        # Close position SYM0USDT -> SYM6USDT should be promoted first (FIFO order)
        promoted = self.pc.release_closed_position("SYM0USDT")
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.symbol, "SYM6USDT")
        self.assertEqual(len(self.pc.fifo_confirmation_queue), 1)

    def test_fee_adjusted_risk_sizing_numerical_validation(self):
        """Verifies Example 1 (BASE fee) and Example 2 (QUOTE fee) sizing precision."""
        # Example 1: BASE fee mode, R_trade = 200, P_entry = 100, SL = 98
        res1 = self.pc.calculate_order_sizing(Decimal("100.00"), Decimal("98.00"), fee_mode="BASE", r_usd=Decimal("200.00"))
        self.assertTrue(res1.accepted)
        self.assertEqual(round(res1.q_order, 4), Decimal("91.8949"))

        # Example 2: QUOTE fee mode, R_trade = 200, P_entry = 100, SL = 98
        res2 = self.pc.calculate_order_sizing(Decimal("100.00"), Decimal("98.00"), fee_mode="QUOTE", r_usd=Decimal("200.00"))
        self.assertTrue(res2.accepted)
        self.assertEqual(round(res2.q_order, 4), Decimal("87.8580"))

    def test_risk_gate_negative_net_distance_rejection(self):
        """Verifies Example 5: D_net <= 0 triggers rejection."""
        # Entry = 100, SL = 101.00 (Example 5). Net distance is negative.
        res = self.pc.calculate_order_sizing(Decimal("100.00"), Decimal("101.00"), fee_mode="QUOTE", r_usd=Decimal("200.00"))
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_reason, "NEGATIVE_NET_DISTANCE")

    def test_minimum_notional_rejection(self):
        """Verifies Example 6: notional < $10.00 triggers rejection."""
        cl_small = CashLedger(Decimal("5.00"), Decimal("5.00"), Decimal("0.00"), Decimal("0.00"))
        pc_small = PortfolioCoordinator(cash_ledger=cl_small)
        res = pc_small.calculate_order_sizing(Decimal("100.00"), Decimal("98.00"), fee_mode="QUOTE")
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_reason, "BELOW_MINIMUM_NOTIONAL")

    def test_double_entry_cash_ledger_invariants(self):
        """Verifies Examples 19 & 20: cash ledger maintains exact balance across trade lifecycle."""
        # Initial: 10000 cash, 0 cost basis, 0 pnl
        self.assertTrue(self.cl.verify_invariant())

        # Buy 100 units at 100.00 (Cost basis = 10000.00)
        self.pc.commit_position_entry("BTCUSDT", Decimal("100.00"), Decimal("100.0"), fee_mode="BASE")
        self.assertEqual(self.cl.available_cash, Decimal("0.00"))
        self.assertEqual(self.cl.allocated_cost_basis, Decimal("10000.00"))
        self.assertTrue(self.cl.verify_invariant())

        # Exit 50 units at 103.00 (TP1: Proceeds = 50 * 103 * 0.999 = 5144.85, Basis = 5000)
        pnl1 = self.pc.commit_position_exit("BTCUSDT", Decimal("103.00"), Decimal("50.0"), Decimal("5000.00"))
        self.assertEqual(round(pnl1, 2), Decimal("144.85"))
        self.assertEqual(round(self.cl.available_cash, 2), Decimal("5144.85"))
        self.assertTrue(self.cl.verify_invariant())

        # Exit 50 units at 106.00 (TP2: Proceeds = 50 * 106 * 0.999 = 5294.70, Basis = 5000)
        pnl2 = self.pc.commit_position_exit("BTCUSDT", Decimal("106.00"), Decimal("50.0"), Decimal("5000.00"))
        self.assertEqual(round(pnl2, 2), Decimal("294.70"))
        self.assertEqual(round(self.cl.available_cash, 2), Decimal("10439.55"))
        self.assertEqual(self.cl.allocated_cost_basis, Decimal("0.00"))
        self.assertEqual(round(self.cl.realized_pnl, 2), Decimal("439.55"))
        self.assertTrue(self.cl.verify_invariant())


class TestPositionManagerLifecycleAndInvariants(unittest.TestCase):
    """Verifies §§ 6, 9, 11 Master Execution Matrix, Monotonic Stops, and Scenarios A-L."""

    def test_stop_loss_monotonicity_supremum_invariant(self):
        """Mathematically verifies SL_active(t+1) >= SL_active(t) under any candidate input."""
        pos = create_active_position(
            symbol="BTCUSDT",
            entry_time_ms=1000,
            entry_price=Decimal("100.00"),
            q_order=Decimal("100.0"),
            initial_stop=Decimal("98.00"),
            atr14=Decimal("2.00")
        )
        self.assertEqual(pos.active_stop, Decimal("98.00"))

        # Candidate below current stop -> Ignored
        pos.update_active_stop(Decimal("95.00"))
        self.assertEqual(pos.active_stop, Decimal("98.00"))

        # Candidate above current stop -> Ratchet up
        pos.update_active_stop(Decimal("100.28"))
        self.assertEqual(pos.active_stop, Decimal("100.28"))

        # Attempt downward ratchet -> Prevented
        pos.update_active_stop(Decimal("99.00"))
        self.assertEqual(pos.active_stop, Decimal("100.28"))

    def test_opening_gap_below_stop_scenario_h1_example_14(self):
        """Verifies Scenario H-1 and Example 14: Opening gap below initial stop fills at Open with slippage."""
        pos = create_active_position("BTCUSDT", 1000, Decimal("100.00"), Decimal("100.0"), Decimal("98.00"), Decimal("2.00"))

        # Next candle opens at 97.00 < 98.00 stop
        c_gap = CandleBar(2000, Decimal("97.00"), Decimal("97.50"), Decimal("96.50"), Decimal("97.20"))
        exs = pos.evaluate_candle(c_gap)

        self.assertEqual(len(exs), 1)
        ex = exs[0]
        self.assertEqual(ex.scenario, "H-1")
        self.assertEqual(ex.exit_type, "STOPPED_OUT_FULL")
        self.assertEqual(ex.fill_price, Decimal("97.00") * (Decimal("1.0") - STOP_SLIPPAGE))  # 96.9224
        self.assertEqual(pos.status, "FULLY_EXITED")

    def test_opening_gap_above_tp2_scenario_g_example_13(self):
        """Verifies Scenario G and Example 13: Opening gap above TP2 clears entire position at Open."""
        pos = create_active_position("BTCUSDT", 1000, Decimal("100.00"), Decimal("100.0"), Decimal("98.00"), Decimal("2.00"))
        # TP2 is 106.00. Next candle opens at 108.00
        c_gap = CandleBar(2000, Decimal("108.00"), Decimal("109.00"), Decimal("107.50"), Decimal("108.50"))
        exs = pos.evaluate_candle(c_gap)

        self.assertEqual(len(exs), 1)
        ex = exs[0]
        self.assertEqual(ex.scenario, "G")
        self.assertEqual(ex.exit_type, "TP2_EXECUTED_FULL")
        self.assertEqual(ex.fill_price, Decimal("108.00"))
        self.assertEqual(pos.status, "FULLY_EXITED")
        self.assertTrue(pos.tp1_executed)
        self.assertTrue(pos.tp2_executed)

    def test_opening_gap_above_tp1_scenario_f_example_12(self):
        """Verifies Scenario F and Example 12: Opening gap above TP1 executes TP1 at Open and arms breakeven."""
        pos = create_active_position("BTCUSDT", 1000, Decimal("100.00"), Decimal("100.0"), Decimal("98.00"), Decimal("2.00"))
        # TP1 is 103.00, TP2 is 106.00. Next candle opens at 104.00, high 105.00, low 101.00
        c_gap = CandleBar(2000, Decimal("104.00"), Decimal("105.00"), Decimal("101.00"), Decimal("103.50"))
        exs = pos.evaluate_candle(c_gap)

        self.assertEqual(len(exs), 1)
        ex = exs[0]
        self.assertEqual(ex.scenario, "F")
        self.assertEqual(ex.exit_type, "TP1_EXECUTED_PARTIAL")
        self.assertEqual(ex.fill_price, Decimal("104.00"))
        self.assertEqual(pos.q_remaining, Decimal("50.0"))
        self.assertTrue(pos.tp1_executed)
        self.assertTrue(pos.breakeven_stop_active)
        self.assertEqual(pos.active_stop, pos.breakeven_stop)

    def test_conservative_stop_first_priority_scenario_d_and_j(self):
        """Verifies conservative stop-first priority: if intrabar low reaches stop, stop fills first."""
        pos = create_active_position("BTCUSDT", 1000, Decimal("100.00"), Decimal("100.0"), Decimal("98.00"), Decimal("2.00"))

        # Extreme range bar spanning both 97.00 (below 98 stop) and 107.00 (above 106 TP2)
        c_ext = CandleBar(2000, Decimal("100.00"), Decimal("107.00"), Decimal("97.00"), Decimal("105.00"))
        exs = pos.evaluate_candle(c_ext)

        self.assertEqual(len(exs), 1)
        ex = exs[0]
        self.assertEqual(ex.scenario, "J")
        self.assertEqual(ex.exit_type, "STOPPED_OUT_FULL")
        self.assertFalse(pos.tp1_executed)
        self.assertFalse(pos.tp2_executed)
        self.assertEqual(pos.status, "FULLY_EXITED")

    def test_same_candle_both_targets_hit_scenario_a_example_17(self):
        """Verifies Scenario A: Intrabar hits TP1 at 103.00, arms BE, and clears TP2 at 106.00."""
        pos = create_active_position("BTCUSDT", 1000, Decimal("100.00"), Decimal("100.0"), Decimal("98.00"), Decimal("2.00"))

        # Low stays at 100.50 (above BE stop 100.28). High reaches 106.50
        c = CandleBar(2000, Decimal("101.00"), Decimal("106.50"), Decimal("100.50"), Decimal("105.50"))
        exs = pos.evaluate_candle(c)

        self.assertEqual(len(exs), 2)
        self.assertEqual(exs[0].exit_type, "TP1_EXECUTED_PARTIAL")
        self.assertEqual(exs[1].exit_type, "TP2_EXECUTED_FULL")
        self.assertEqual(pos.status, "FULLY_EXITED")
        self.assertEqual(pos.q_remaining, Decimal("0.0"))

    def test_trailing_stop_ratchet_and_exit_scenario_c(self):
        """Verifies trailing stop ratchets on candle close and terminates position when breached."""
        pos = create_active_position("BTCUSDT", 1000, Decimal("100.00"), Decimal("100.0"), Decimal("98.00"), Decimal("2.00"))

        # Candle 1: TP1 hit at 103.50
        c1 = CandleBar(2000, Decimal("101.00"), Decimal("103.50"), Decimal("100.50"), Decimal("103.00"))
        pos.evaluate_candle(c1, atr14=Decimal("2.00"))
        self.assertTrue(pos.tp1_executed)

        # Candle 2: High reaches 105.00, closes at 104.50.
        # Trail threshold is 100 + 3.0 * 1.333 = 104.0. Trailing stop ratchets to 105.0 - 1.5 * 2.0 = 102.0
        c2 = CandleBar(3000, Decimal("103.00"), Decimal("105.00"), Decimal("102.50"), Decimal("104.50"))
        pos.evaluate_candle(c2, atr14=Decimal("2.00"))
        self.assertTrue(pos.trailing_stop_active)
        self.assertGreaterEqual(pos.active_stop, Decimal("102.00"))

        # Candle 3: Pullback breaches trailing stop at 101.80
        c3 = CandleBar(4000, Decimal("104.00"), Decimal("104.20"), Decimal("101.50"), Decimal("101.80"))
        exs = pos.evaluate_candle(c3, atr14=Decimal("2.00"))

        self.assertEqual(len(exs), 1)
        self.assertEqual(exs[0].exit_type, "TRAILING_STOPPED_OUT")
        self.assertEqual(pos.status, "FULLY_EXITED")

    def test_time_stop_exit_after_12_bars(self):
        """Verifies Time Stop: Position held for 12 bars without TP1 exits at close."""
        pos = create_active_position("BTCUSDT", 1000, Decimal("100.00"), Decimal("100.0"), Decimal("98.00"), Decimal("2.00"))

        # 11 bars of sideways consolidation between 99.00 and 101.00
        for b in range(11):
            c = CandleBar(2000 + b * 1000, Decimal("100.00"), Decimal("101.00"), Decimal("99.00"), Decimal("100.20"))
            exs = pos.evaluate_candle(c)
            self.assertEqual(len(exs), 0)
            self.assertEqual(pos.status, "POSITION_OPEN")

        # 12th bar triggers time stop exit
        c12 = CandleBar(2000 + 11 * 1000, Decimal("100.00"), Decimal("101.00"), Decimal("99.00"), Decimal("99.80"))
        exs12 = pos.evaluate_candle(c12)

        self.assertEqual(len(exs12), 1)
        self.assertEqual(exs12[0].exit_type, "TIME_STOP_EXIT")
        self.assertEqual(pos.status, "FULLY_EXITED")


if __name__ == "__main__":
    unittest.main()
