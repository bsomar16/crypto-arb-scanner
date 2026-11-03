import unittest

import indicators
import sls_strategy


class SlsRsiParityTests(unittest.TestCase):
    def test_wilder_series_final_value_matches_shared_rsi(self):
        series = [
            100 + i * 0.17 + ((i % 7) - 3) * 0.31 + (i % 11) * 0.07
            for i in range(260)
        ]
        expected = indicators.rsi(series, 14)
        actual = sls_strategy._rsi_series(series, 14)[-1]
        self.assertAlmostEqual(actual, expected, places=12)

    def test_wilder_series_matches_shared_rsi_on_downtrend(self):
        series = [250 - i * 0.42 + (i % 5) * 0.08 for i in range(240)]
        self.assertAlmostEqual(
            sls_strategy._rsi_series(series, 14)[-1],
            indicators.rsi(series, 14),
            places=12,
        )

    def test_rsi_alignment_starts_at_period_index(self):
        values = [float(i + 1) for i in range(20)]
        result = sls_strategy._rsi_series(values, 14)
        self.assertEqual(len(result), len(values))
        self.assertEqual(result[:14], [None] * 14)
        self.assertEqual(result[14], 100.0)

    def test_insufficient_history_remains_na_like_pine(self):
        self.assertEqual(sls_strategy._rsi_series([1, 2, 3], 14), [None, None, None])


if __name__ == "__main__":
    unittest.main()
