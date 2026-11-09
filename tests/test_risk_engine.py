import unittest
from datetime import datetime, timezone

import risk_engine


class RiskEngineTests(unittest.TestCase):
    def test_position_and_total_exposure_caps(self):
        cfg = {"max_position_notional_usdt": 300, "max_total_exposure_usdt": 600, "max_daily_loss_usdt": 30}
        positions = [{"status": "open", "notional_usdt": 300}]
        self.assertEqual(risk_engine.check_new_position(positions, 300, cfg, rows=[])[0], True)
        self.assertEqual(risk_engine.check_new_position(positions, 301, cfg, rows=[])[0], False)
        self.assertEqual(risk_engine.check_new_position(positions + [{"status": "open", "notional_usdt": 300}], 1, cfg, rows=[])[0], False)

    def test_kill_switch_blocks(self):
        self.assertFalse(risk_engine.check_new_position([], 100, {"risk_kill_switch": True})[0])

    def test_daily_loss_only_counts_today(self):
        now = datetime.now(timezone.utc)
        rows = [
            {"event": "close", "outcome": "LOSS", "ts": now.isoformat(), "realized_pnl_usdt": -12},
            {"event": "close", "outcome": "LOSS", "ts": "2020-01-01T00:00:00+00:00", "realized_pnl_usdt": -100},
            {"event": "close", "outcome": "WIN", "ts": now.isoformat(), "realized_pnl_usdt": 50},
        ]
        self.assertEqual(risk_engine.daily_realized_loss_usdt(rows, now), 12)

    def test_suggested_notional_respects_cap(self):
        cfg = {"risk_per_trade_pct": 1.0, "max_position_notional_usdt": 300}
        # 1% of 10,000 USDT is 100 USDT risk. A 5% stop permits
        # 2,000 USDT notional by risk sizing, so the 300 USDT hard cap wins.
        self.assertAlmostEqual(risk_engine.suggested_notional(100, 95, 10000, cfg), 300.0)
        self.assertAlmostEqual(risk_engine.suggested_notional(100, 99, 10000, cfg), 300.0)


if __name__ == "__main__":
    unittest.main()
