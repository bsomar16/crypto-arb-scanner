"""Unit tests for Pine Script-compatible math primitives."""
import unittest

from sls_pine_math import (
    confirmed_pivots, pine_atr, pine_ema, pine_macd, pine_rma, pine_rsi,
    pine_vwap, delayed_highest, delayed_lowest, roc
)


class PineMathTests(unittest.TestCase):
    def test_ema_seeds_from_first_source_value(self):
        values = pine_ema([10, 13, 13], 3)
        self.assertEqual(values, [10.0, 11.5, 12.25])

    def test_rma_uses_sma_seed_then_wilder_recursion(self):
        values = pine_rma([1, 2, 3, 4], 3)
        self.assertEqual(values[:2], [None, None])
        self.assertAlmostEqual(values[2], 2.0)
        self.assertAlmostEqual(values[3], 8.0 / 3.0)

    def test_rsi_is_na_until_fourteen_changes_are_available(self):
        values = pine_rsi([float(i) for i in range(16)], 14)
        self.assertTrue(all(x is None for x in values[:14]))
        self.assertEqual(values[14], 100.0)
        self.assertEqual(values[15], 100.0)

    def test_atr_uses_true_range_rma_and_warmup(self):
        highs = [11, 12, 12, 13]
        lows = [9, 10, 9, 11]
        closes = [10, 11, 10, 12]
        values = pine_atr(highs, lows, closes, 2)
        self.assertIsNone(values[1])
        self.assertAlmostEqual(values[2], 2.5)
        self.assertAlmostEqual(values[3], 2.75)

    def test_macd_histogram_is_line_minus_signal(self):
        line, signal, hist = pine_macd([float(i * i + i) for i in range(40)])
        for a, b, h in zip(line, signal, hist):
            if a is not None and b is not None and h is not None:
                self.assertAlmostEqual(h, a - b)

    def test_vwap_calculates_cumulative_volume_weight(self):
        hlc3 = [10.0, 12.0, 11.0]
        vols = [100.0, 200.0, 100.0]
        # bar 0: (10 * 100) / 100 = 10.0
        # bar 1: (1000 + 2400) / 300 = 11.333...
        # bar 2: (3400 + 1100) / 400 = 11.25
        vwap = pine_vwap(hlc3, vols)
        self.assertAlmostEqual(vwap[0], 10.0)
        self.assertAlmostEqual(vwap[1], 34.0 / 3.0)
        self.assertAlmostEqual(vwap[2], 11.25)

    def test_pivots_are_confirmed_and_use_asymmetric_tie_rule(self):
        values = [5, 4, 3, 4, 5, 6, 5, 4, 3, 4, 5]
        self.assertEqual(confirmed_pivots(values, 2, 2), [(2, 3.0), (8, 3.0)])
        self.assertEqual(confirmed_pivots(values, 2, 2, low_pivot=False), [(5, 6.0)])

    def test_delayed_highest_and_lowest(self):
        values = [10.0, 20.0, 15.0, 25.0, 30.0, 18.0]
        # at index 5, delay=1 (excluding index 5), length=3 -> window indices 2, 3, 4 -> [15, 25, 30]
        self.assertEqual(delayed_highest(values, 3, 1, 5), 30.0)
        self.assertEqual(delayed_lowest(values, 3, 1, 5), 15.0)

    def test_roc_calculation(self):
        values = [100.0, 110.0, 120.0]
        self.assertAlmostEqual(roc(values, 2, 2), 20.0)
        self.assertIsNone(roc(values, 3, 2))

    def test_mismatched_ohlc_lengths_raise(self):
        with self.assertRaises(ValueError):
            pine_atr([2, 3], [0], [1, 2], 1)


if __name__ == "__main__":
    unittest.main()

