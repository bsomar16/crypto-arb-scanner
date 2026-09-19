import tempfile
import unittest

import shadow_trading


class ShadowTradingTests(unittest.TestCase):
    def signal(self):
        return {"coin":"SOL","interval":"15m","setup_type":"MOMENTUM","rating":"BUY",
                "entry":100,"stop":98,"t1":102,"t2":104,"t3":106,"score":75}

    def test_open_never_calls_exchange(self):
        with tempfile.NamedTemporaryFile() as f:
            row = shadow_trading.open_trade(self.signal(), price=100, path=f.name)
            self.assertEqual(row["outcome_source"], "shadow")
            self.assertEqual(row["status"], "OPEN")

    def test_shadow_closes_at_stop(self):
        with tempfile.NamedTemporaryFile() as f:
            shadow_trading.open_trade(self.signal(), path=f.name)
            result = shadow_trading.run_once([ ], lambda coin: 97, path=f.name)
            self.assertEqual(result["closed"], 1)
            stats = shadow_trading.statistics(f.name)
            self.assertEqual(stats["losses"], 1)
            self.assertEqual(stats["outcome_source"], "shadow")

    def test_shadow_closes_at_tp3(self):
        with tempfile.NamedTemporaryFile() as f:
            shadow_trading.open_trade(self.signal(), path=f.name)
            result = shadow_trading.run_once([], lambda coin: 107, path=f.name)
            self.assertEqual(result["closed"], 1)
            self.assertEqual(shadow_trading.statistics(f.name)["wins"], 1)

    def test_non_buy_is_ignored(self):
        s = self.signal()
        s["rating"] = "WATCH"
        with tempfile.NamedTemporaryFile() as f:
            result = shadow_trading.run_once([s], lambda coin: 100, path=f.name)
            self.assertEqual(result["opened"], 0)


if __name__ == "__main__":
    unittest.main()
