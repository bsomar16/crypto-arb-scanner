"""Unit Test Suite for Smart Money Trend Engine & Signal Scanner.

Verifies:
- smart_money/trend.py: EMA alignment, Wilder ADX/+DI/-DI, Higher Highs & Higher Lows,
  Smart Money CVD flow confirmation, 4h HTF alignment, and anti-chasing overextension guard.
- smart_money_scanner.py: Accumulation (EARLY_ACCUMULATION, PRE_BREAKOUT), Bullish Trend,
  Breakout & Retest signal emission, cooldown & upgrade deduplication, stablecoin exclusion,
  and Telegram HTML alert formatting.

Strictly Binance Spot Long/BUY only. Zero live exchange orders.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from typing import List

from smart_money.trend import evaluate_bullish_trend
from smart_money.volume_cvd import compute_volume_cvd_analytics
from smart_money.signal_tracker import SmartMoneySignalTracker, format_outcome_telegram_update
import scanner
from smart_money_scanner import (
    SmartMoneySignalEngine,
    format_telegram_alert,
    format_telegram_digest,
    fmt_price,
    is_tokenized_equity,
    load_recent_classic_coins,
    EXCLUDED_SYMBOLS,
)


def _make_kline(
    open_ms: int,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float,
    tbr: float = 0.55,
) -> List:
    close_ms = open_ms + 3_599_999
    v_tb = v * tbr
    return [open_ms, str(o), str(h), str(l), str(c), str(v), close_ms, str(v * c), 100, str(v_tb), str(v_tb * c), "0"]


def _generate_bullish_trend_candles(n: int = 80, start_price: float = 100.0, step: float = 0.50) -> List[List]:
    """Generates a realistic, healthy, institutional-backed bullish uptrend (RSI ~60, HH/HL)."""
    candles = []
    base_ms = 1730000000000
    price = start_price
    for i in range(n):
        # 6-bar realistic swing wave: 3 expansion bars up, 3 healthy pullback bars down (net +0.8 step/cycle)
        phase = i % 6
        if phase in (0, 1, 2):
            delta_p = step * 1.1
            tbr = 0.62
            vol = 1500.0
        else:
            delta_p = -step * 0.72
            tbr = 0.48
            vol = 900.0
        o = price
        c = max(1.0, o + delta_p)
        h = max(o, c) + step * 0.9
        l = min(o, c) - step * 0.8
        candles.append(_make_kline(base_ms + i * 3_600_000, o, h, l, c, vol, tbr=tbr))
        price = c
    return candles


def _generate_bearish_candles(n: int = 80, start_price: float = 200.0, step: float = 0.8) -> List[List]:
    """Generates a steady bearish downtrend."""
    candles = []
    base_ms = 1730000000000
    price = start_price
    for i in range(n):
        o = price
        c = max(1.0, o - step)
        h = o + 0.3
        l = c - 0.3
        candles.append(_make_kline(base_ms + i * 3_600_000, o, h, l, c, 1200.0, tbr=0.38))
        price = c
    return candles


def _generate_parabolic_overextended_candles(n: int = 80) -> List[List]:
    """Generates a flat base followed by a sudden +60% parabolic spike (chase trap)."""
    candles = []
    base_ms = 1730000000000
    price = 100.0
    for i in range(n):
        if i < n - 8:
            o, c = price, price + (0.05 if i % 2 == 0 else -0.05)
            h, l = max(o, c) + 0.2, min(o, c) - 0.2
            vol = 1000.0
        else:
            o = price
            c = price * 1.07  # +7% per hour for 8 hours -> +71% blow-off
            h = c * 1.01
            l = o * 0.995
            vol = 5000.0
        candles.append(_make_kline(base_ms + i * 3_600_000, o, h, l, c, vol, tbr=0.68))
        price = c
    return candles


def _generate_wyckoff_accumulation_candles(n: int = 70) -> List[List]:
    """Generates tight Wyckoff accumulation base with ascending higher lows, volume dry-up, and positive CVD."""
    candles = []
    base_ms = 1730000000000
    # Range tightly constrained between ~98.0 and ~103.5 (bandwidth ~5.6%) with rising support lows
    for i in range(n):
        # Create 3 higher-low pullbacks inside the tight base
        cycle = i % 10
        support_floor = 98.0 + (i / n) * 3.0  # Rising support: 98.0 -> 101.0
        ceiling = 103.2
        if cycle in (0, 1, 2):
            # Pullback toward rising support on low volume (supply exhaustion)
            o = min(ceiling, support_floor + 1.5 - cycle * 0.4)
            c = max(support_floor, o - 0.4)
            h = o + 0.2
            l = support_floor
            vol = 600.0
            tbr = 0.56  # Smart money absorbing dips
        elif cycle == 3:
            # Support bounce on strong demand
            o = support_floor + 0.1
            c = support_floor + 1.4
            h = c + 0.3
            l = support_floor
            vol = 1800.0
            tbr = 0.68
        else:
            # Steady grind near upper range
            o = support_floor + 1.2
            c = min(ceiling - 0.1, o + 0.4)
            h = min(ceiling, c + 0.2)
            l = o - 0.2
            vol = 1100.0
            tbr = 0.60
        candles.append(_make_kline(base_ms + i * 3_600_000, o, h, l, c, vol, tbr=tbr))
    # Final bar near top of range (pre-breakout tension)
    candles.append(_make_kline(base_ms + n * 3_600_000, 102.5, 103.1, 102.3, 103.0, 1600.0, tbr=0.66))
    return candles


class TestBullishTrendEngine(unittest.TestCase):
    """Tests smart_money/trend.py mathematical and behavioral invariants."""

    def test_detects_strong_bullish_trend_with_htf_confluence(self):
        candles_1h = _generate_bullish_trend_candles(n=80, start_price=100.0, step=0.35)
        candles_4h = _generate_bullish_trend_candles(n=60, start_price=80.0, step=0.60)
        res = evaluate_bullish_trend(candles_1h, htf_candles=candles_4h)

        self.assertIn(res.trend_state, ("STRONG_BULLISH_TREND", "BULLISH_TREND"))
        self.assertGreaterEqual(res.trend_score, 62.0)
        self.assertGreater(res.ema20, res.ema50)
        self.assertGreater(res.plus_di14, res.minus_di14)
        self.assertTrue(res.htf_aligned)
        self.assertFalse(res.overextended)
        self.assertLess(res.suggested_stop, res.close)

    def test_rejects_bearish_downtrend(self):
        candles = _generate_bearish_candles(n=80)
        res = evaluate_bullish_trend(candles)

        self.assertEqual(res.trend_state, "BEARISH")
        self.assertLess(res.trend_score, 30.0)

    def test_anti_chasing_blocks_parabolic_spike(self):
        candles = _generate_parabolic_overextended_candles(n=80)
        res = evaluate_bullish_trend(candles)

        self.assertTrue(res.overextended)
        self.assertEqual(res.trend_state, "OVEREXTENDED_BULL")
        self.assertNotIn(res.trend_state, ("STRONG_BULLISH_TREND", "BULLISH_TREND", "BULLISH_PULLBACK"))

    def test_anti_chasing_blocks_rolled_48h_spike(self):
        """A coin that jumped +48% 30 hours ago (now flat for 24h) must still be caught as overextended."""
        candles = []
        base_ms = 1730000000000
        price = 100.0
        for i in range(80):
            if 40 <= i < 48:
                o, c = price, price * 1.052  # +50% surge 32 hours ago
                h, l = c * 1.005, o * 0.998
            else:
                o = price
                c = price * (1.002 if i % 2 == 0 else 0.999)
                h, l = max(o, c) * 1.004, min(o, c) * 0.996
            candles.append(_make_kline(base_ms + i * 3_600_000, o, h, l, c, 1200.0, tbr=0.58))
            price = c
        res = evaluate_bullish_trend(candles)
        self.assertTrue(res.overextended)
        self.assertEqual(res.trend_state, "OVEREXTENDED_BULL")

    def test_insufficient_history_returns_neutral(self):
        candles = _generate_bullish_trend_candles(n=15)
        res = evaluate_bullish_trend(candles)
        self.assertEqual(res.trend_state, "NEUTRAL")
        self.assertEqual(res.trend_score, 0.0)


class TestSmartMoneyScannerSignals(unittest.TestCase):
    """Tests end-to-end signal detection, cooldowns, upgrades, and formatting."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmp_dir, "scanner_state.json")
        self.engine = SmartMoneySignalEngine(
            telegram_token=None,
            telegram_chat_id=None,
            state_path=self.state_path,
            fetch_orderbook_enabled=False,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_emits_wyckoff_accumulation_signal(self):
        # Append extra dummy forming bar at the end since evaluate_symbol_data excludes last unclosed bar
        candles = _generate_wyckoff_accumulation_candles(n=70)
        candles.append(_make_kline(candles[-1][0] + 3_600_000, 103.0, 103.1, 102.9, 103.0, 500.0, tbr=0.55))

        orderbook = {
            "bids": [["102.9", "500"], ["102.5", "800"], ["102.0", "1000"]],
            "asks": [["103.1", "200"], ["103.5", "300"], ["104.0", "300"]],
        }
        sig, metrics = self.engine.evaluate_symbol_data(
            "SOLUSDT", candles, klines_4h=None, macro_regime="BULL", orderbook=orderbook
        )

        self.assertIsNotNone(sig, f"Expected accumulation signal, got None. Metrics: {metrics}")
        self.assertIn(sig["signal_type"], ("PRE_BREAKOUT", "EARLY_ACCUMULATION", "BREAKOUT_DETECTED", "CONTINUATION_CONFIRMED"))
        self.assertGreaterEqual(sig["accumulation_score"], 50.0)
        self.assertLess(sig["stop_loss"], sig["entry_price"])
        self.assertGreater(sig["tp1"], sig["entry_price"])
        self.assertGreater(sig["tp2"], sig["tp1"])

        # Verify Telegram formatting works without error
        msg = format_telegram_alert(sig)
        self.assertIn("SOL/USDT", msg)
        self.assertIn("Accum", msg)

    def test_emits_bullish_trend_signal(self):
        candles_1h = _generate_bullish_trend_candles(n=81, start_price=100.0, step=0.35)
        candles_4h = _generate_bullish_trend_candles(n=61, start_price=80.0, step=0.60)
        sig, metrics = self.engine.evaluate_symbol_data(
            "TAOUSDT", candles_1h, klines_4h=candles_4h, macro_regime="BULL",
            recent_classic_coins={"TAOUSDT"},
        )

        self.assertIsNotNone(sig, f"Expected bullish trend signal, got None. Metrics: {metrics}")
        self.assertIn(
            sig["signal_type"],
            ("STRONG_BULLISH_TREND", "BULLISH_TREND", "BULLISH_PULLBACK", "BREAKOUT_DETECTED", "CONTINUATION_CONFIRMED"),
        )
        self.assertTrue(sig["double_confluence"])
        self.assertLess(sig["stop_loss"], sig["entry_price"])
        self.assertGreater(sig["tp2"], sig["tp1"])

        digest = format_telegram_digest([sig], macro_regime="BEAR")
        self.assertIn("TAO/USDT", digest)
        self.assertIn("DOUBLE CONFLUENCE", digest)
        self.assertIn("BTC Macro Regime: BEAR", digest)

    def test_cooldown_deduplication_and_stage_upgrade(self):
        t0 = 1_700_000_000.0
        self.assertTrue(self.engine.should_emit_signal("AVAXUSDT", "EARLY_ACCUMULATION", now_ts=t0))
        self.engine.record_signal_emitted("AVAXUSDT", "EARLY_ACCUMULATION", now_ts=t0)

        # 10 minutes later: same signal blocked by 6h cooldown
        self.assertFalse(self.engine.should_emit_signal("AVAXUSDT", "EARLY_ACCUMULATION", now_ts=t0 + 600))

        # 10 minutes later: UPGRADE to PRE_BREAKOUT is allowed immediately!
        self.assertTrue(self.engine.should_emit_signal("AVAXUSDT", "PRE_BREAKOUT", now_ts=t0 + 600))
        self.engine.record_signal_emitted("AVAXUSDT", "PRE_BREAKOUT", now_ts=t0 + 600)

        # Another 10 minutes later: PRE_BREAKOUT blocked, but CONTINUATION_CONFIRMED allowed!
        self.assertFalse(self.engine.should_emit_signal("AVAXUSDT", "PRE_BREAKOUT", now_ts=t0 + 1200))
        self.assertTrue(self.engine.should_emit_signal("AVAXUSDT", "CONTINUATION_CONFIRMED", now_ts=t0 + 1200))

        # After 7 hours: cooldown expired, can re-alert
        self.assertTrue(self.engine.should_emit_signal("AVAXUSDT", "EARLY_ACCUMULATION", now_ts=t0 + 7 * 3600))

        # Intra-trend flip (BULLISH_PULLBACK -> STRONG_BULLISH_TREND) must NOT bypass cooldown
        self.engine.record_signal_emitted("XLMUSDT", "BULLISH_PULLBACK", now_ts=t0)
        self.assertFalse(self.engine.should_emit_signal("XLMUSDT", "BULLISH_TREND", now_ts=t0 + 900))
        self.assertFalse(self.engine.should_emit_signal("XLMUSDT", "STRONG_BULLISH_TREND", now_ts=t0 + 900))

        # Active tracked signal lock: NEVER re-alert while open in tracker, even after cooldown expires
        self.engine.tracker.tracked["ZKPUSDT"] = {"symbol": "ZKPUSDT"}
        self.assertFalse(self.engine.should_emit_signal("ZKPUSDT", "CONTINUATION_CONFIRMED", now_ts=t0 + 24 * 3600))

    def test_adaptive_price_formatting_and_tokenized_equity_exclusion(self):
        self.assertEqual(fmt_price(64448.32), "$64,448.32")
        self.assertEqual(fmt_price(71.384), "$71.384")
        self.assertEqual(fmt_price(0.1542), "$0.1542")
        self.assertEqual(fmt_price(0.0000184), "$0.0000184")
        self.assertIn("USDCUSDT", EXCLUDED_SYMBOLS)
        self.assertIn("RWUSDUSDT", EXCLUDED_SYMBOLS)
        self.assertIn("PAXGUSDT", EXCLUDED_SYMBOLS)

        # Tokenized equities excluded
        for eq in ("NVDABUSDT", "GOOGLBUSDT", "SOXLBUSDT", "MUBUSDT", "SPCXBUSDT", "SNXXB", "TSLAB"):
            self.assertTrue(is_tokenized_equity(eq), f"Should exclude tokenized stock {eq}")
            self.assertTrue(scanner._is_tokenized_equity(eq), f"scanner.py should exclude {eq}")

        # Real crypto ending in 'B' MUST be kept
        for crypto in ("BNBUSDT", "ARBUSDT", "SHIBUSDT", "TRBUSDT", "CKBUSDT", "BBUSDT", "BNB", "ARB", "SHIB"):
            self.assertFalse(is_tokenized_equity(crypto), f"Must NOT exclude real crypto {crypto}")
            self.assertFalse(scanner._is_tokenized_equity(crypto), f"scanner.py must NOT exclude {crypto}")

    def test_rvol_floor_blocks_dead_volume_signals(self):
        candles_1h = _generate_bullish_trend_candles(n=81, start_price=100.0, step=0.35)
        # Crush final closed bar's volume to 0.15x baseline (dead volume)
        candles_1h[-2][5] = "150.0"
        candles_1h[-2][9] = "90.0"
        sig, _ = self.engine.evaluate_symbol_data("LOWVOLUSDT", candles_1h, macro_regime="BULL")
        self.assertIsNone(sig, "Dead RVOL (< 0.65x) setup should be filtered out")

    def test_signal_tracker_tp1_then_tp2_lifecycle_and_stats(self):
        tracked_path = os.path.join(self.tmp_dir, "tracked.json")
        outcomes_path = os.path.join(self.tmp_dir, "outcomes.jsonl")
        tracker = SmartMoneySignalTracker(tracked_path=tracked_path, outcomes_path=outcomes_path)

        base_ms = 1730000000000
        sig = {
            "signal_id": "sig-001",
            "symbol": "SOLUSDT",
            "signal_type": "EARLY_ACCUMULATION",
            "timestamp_utc": "2026-11-18 09:00:00 UTC",
            "entry_price": 100.0,
            "stop_loss": 98.0,
            "tp1": 103.0,
            "tp2": 106.0,
        }
        self.assertTrue(tracker.register_signal(sig, candle_open_ms=base_ms))

        # Build 25 historical bars + 2 new closed bars hitting TP1 then TP2 + 1 unclosed bar
        bars = [_make_kline(base_ms - (25 - i) * 3_600_000, 99.0, 100.5, 98.5, 100.0, 1000.0) for i in range(25)]
        bars.append(_make_kline(base_ms + 3_600_000, 100.2, 103.5, 99.8, 102.8, 1500.0))      # Hits TP1 (103.0)
        bars.append(_make_kline(base_ms + 2 * 3_600_000, 102.8, 106.5, 102.0, 106.1, 1800.0))  # Hits TP2 (106.0)
        bars.append(_make_kline(base_ms + 3 * 3_600_000, 106.1, 106.2, 105.9, 106.0, 400.0))   # Forming bar

        events = tracker.advance_all(lambda sym: bars)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "TP1_HIT")
        self.assertEqual(events[1]["event_type"], "TP2_EXECUTED_FULL")
        self.assertNotIn("SOLUSDT", tracker.tracked)

        stats = tracker.compute_stats()
        self.assertEqual(stats["total_closed"], 1)
        self.assertEqual(stats["win_rate_pct"], 100.0)
        self.assertEqual(stats["tp1_hit_rate_pct"], 100.0)
        self.assertIn("EARLY_ACCUMULATION", stats["by_signal_type"])

        msg = format_outcome_telegram_update(events, stats)
        self.assertIn("SOL/USDT", msg)
        self.assertIn("TP1 HIT", msg)
        self.assertIn("TP2 FULL TARGET HIT", msg)


if __name__ == "__main__":
    unittest.main()
