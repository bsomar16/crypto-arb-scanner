import unittest

from sls_engine import evaluate_series


def make_rows(count=260, drift=0.2):
    rows = []
    price = 100.0
    for i in range(count):
        price += drift
        rows.append({
            "t": i * 300_000, "o": price - 0.05, "h": price + 0.3,
            "l": price - 0.3, "c": price, "v": 1000.0 + (i % 7) * 10,
            "q": price * (1000.0 + (i % 7) * 10),
        })
    return rows


class SlsEngineTests(unittest.TestCase):
    def test_missing_btc_context_fails_closed(self):
        rows = make_rows()
        self.assertIsNone(evaluate_series(rows, "5m", htf_rows=make_rows(), btc_rows=None))

    def test_missing_htf_context_fails_closed_for_intraday(self):
        rows = make_rows()
        self.assertIsNone(evaluate_series(rows, "5m", htf_rows=None, btc_rows=make_rows()))

    def test_weekly_skips_htf_but_still_requires_btc(self):
        rows = make_rows()
        self.assertIsNone(evaluate_series(rows, "1w", htf_rows=None, btc_rows=None))

    def test_only_sls_buy_action_can_be_emitted(self):
        rows = make_rows()
        result = evaluate_series(rows, "5m", htf_rows=make_rows(), btc_rows=make_rows(),
                                 sensitivity="Conservative")
        if result is not None:
            self.assertEqual(result["strategy"], "SLS")
            self.assertEqual(result["signal_source"], "SLS")
            self.assertEqual(result["signal_action"], "BUY")
            self.assertEqual(result["strategy_family"], "SLS")


if __name__ == "__main__":
    unittest.main()
