import unittest
from unittest.mock import patch

import signals
from signal_audit import SignalAudit


def _bars():
    bars = []
    for i in range(80):
        price = 100.0 + i * 0.02
        volume = 100.0 if i < 78 else 92.0
        bars.append([
            i * 14400000,
            price - 0.1,
            price + 0.2,
            price - 0.2,
            price,
            volume,
            0,
            volume * price,
        ])
    return bars


class VolumeSensitivityOverrideTests(unittest.TestCase):
    def _call(self, floor):
        audit = SignalAudit()
        with patch.object(signals, "_trend_filter", return_value={"state": "BULLISH", "score": 12, "interval": "1d"}),              patch.object(signals, "structure_snapshot", return_value={
                 "choch": False, "bos": True, "score": 70.0, "higher_lows": True,
                 "liquidity_sweep": False, "compression": 0.2,
             }),              patch.object(signals, "entry_diagnostics", return_value={}),              patch.object(signals, "evaluate_entry", return_value={
                 "entry_quality": 90.0, "entry_trigger": "BOS_RETEST_CONFIRM",
                 "liquidity_sweep_confirmed": False, "reclaim_confirmed": True,
                 "bos_confirmed": True, "retest_confirmed": True,
                 "confirmation_candle": True, "bos_level": 99.0,
                 "sweep_level": 0.0, "retest_level": 99.0,
                 "retest_distance_pct": 0.1, "extension_pct": 1.0,
                 "confirmation_body": 0.6,
             }),              patch.object(signals, "classify_expansion", return_value={
                 "state": "BASE", "score": 50.0, "volume_ratio": 1.0,
                 "expansion": 0.1, "extension_pct": 1.0,
             }),              patch.object(signals, "build_signal_context", return_value={
                 "zero_inverse": {"bullish_reversal": False, "bullish_reclaim": False},
                 "order_block": {"bullish": False},
                 "volatility": {"state": "STABLE"},
             }),              patch.object(signals, "classify_bullish_potential", return_value={}),              patch.object(signals, "adaptive_thresholds", return_value={
                 "min_score": 50.0, "min_vol_x": floor, "min_rr": 1.5,
                 "mode": "BASE", "sample": 0,
             }),              patch.object(signals, "optimize_targets", return_value={
                 "t1": 101.0, "t2": 102.0, "t3": 103.0, "mode": "BASE",
                 "adjustment_pct": 0.0, "evidence_scope": "none", "evidence_sample": 0,
             }):
            result = signals.intraday_signal(
                "TEST",
                interval="4h",
                historical_data=_bars(),
                historical_trend_data=_bars(),
                min_hour_vol=0,
                min_vol_x=floor,
                min_score=50,
                min_rr=1.5,
                cfg={
                    "sensitivity_mode": True,
                    "signal_require_retest": True,
                    "signal_require_sweep": False,
                    "signal_allow_early_retest": False,
                    "adaptive_thresholds_enabled": False,
                    "target_optimization_enabled": False,
                },
                audit=audit,
                record_history=False,
            )
        return result, audit.snapshot()

    def test_relaxed_sensitivity_floor_is_applied_to_volume_gate(self):
        result, stages = self._call(0.90)
        self.assertNotEqual(stages.get("volume", 0), 1)
        self.assertIsNotNone(result)

    def test_current_profile_floor_still_rejects_same_volume(self):
        result, stages = self._call(0.95)
        self.assertIsNone(result)
        self.assertEqual(stages.get("volume"), 1)


if __name__ == "__main__":
    unittest.main()
