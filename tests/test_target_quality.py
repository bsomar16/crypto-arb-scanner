import unittest

import target_quality


class TargetQualityTests(unittest.TestCase):
    def _signal(self):
        return {
            "entry": 100.0,
            "t1": 104.2,
            "t2": 107.8,
            "t3": 112.0,
            "risk_pct": 2.0,
            "rr": 6.0,
            "interval": "15m",
            "setup_type": "MOMENTUM",
        }

    def _stats(self, sample=30, rates=None):
        return {
            "overall": {
                "sample": sample,
                "hit_rates_pct": rates or {"t1": 70.0, "t2": 45.0, "t3": 25.0},
            },
            "by_scope": {},
        }

    def test_sparse_evidence_keeps_base_targets(self):
        signal = self._signal()
        result = target_quality.optimize_targets(signal, self._stats(sample=29))
        self.assertEqual(result["mode"], "BASE")
        self.assertEqual(result["t3"], signal["t3"])

    def test_mature_exact_scope_can_adjust_with_bounded_change(self):
        signal = self._signal()
        stats = self._stats(rates={"t1": 95.0, "t2": 85.0, "t3": 70.0})
        stats["by_scope"]["15m|MOMENTUM"] = stats["overall"]
        result = target_quality.optimize_targets(signal, stats)
        self.assertEqual(result["evidence_scope"], "exact")
        self.assertEqual(result["mode"], "EXTEND")
        self.assertLessEqual(abs(result["adjustment_pct"]), 12.0)
        self.assertTrue(result["t1"] < result["t2"] < result["t3"])

    def test_weak_evidence_tightens_without_breaking_rr_or_floor(self):
        signal = self._signal()
        stats = self._stats(rates={"t1": 25.0, "t2": 10.0, "t3": 5.0})
        stats["by_scope"]["15m|MOMENTUM"] = stats["overall"]
        result = target_quality.optimize_targets(signal, stats)
        self.assertEqual(result["mode"], "TIGHTEN")
        self.assertGreaterEqual((result["t3"] / signal["entry"] - 1) * 100, 5.0)
        self.assertGreaterEqual(
            (result["t3"] / signal["entry"] - 1) * 100,
            signal["risk_pct"] * signal["rr"],
        )

    def test_overall_fallback_is_weaker(self):
        signal = self._signal()
        stats = self._stats(rates={"t1": 95.0, "t2": 85.0, "t3": 70.0})
        result = target_quality.optimize_targets(signal, stats)
        self.assertEqual(result["evidence_scope"], "overall")
        self.assertLessEqual(abs(result["adjustment_pct"]), 6.0)

    def test_max_potential_is_preserved(self):
        signal = self._signal()
        stats = self._stats(rates={"t1": 100.0, "t2": 100.0, "t3": 100.0})
        stats["by_scope"]["15m|MOMENTUM"] = stats["overall"]
        signal["max_potential_pct"] = 13.0
        result = target_quality.optimize_targets(signal, stats)
        self.assertLessEqual((result["t3"] / signal["entry"] - 1) * 100, 13.0)


if __name__ == "__main__":
    unittest.main()
