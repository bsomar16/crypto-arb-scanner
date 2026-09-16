import os
import tempfile
import unittest

import trade_journal


class TestTradeJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = trade_journal.PATH
        trade_journal.PATH = os.path.join(self.tmp, "journal.jsonl")

    def tearDown(self):
        trade_journal.PATH = self.old

    def test_statistics(self):
        trade_journal.record("open", "A", coin="SOL", status="open")
        trade_journal.record("tp1", "A", tp1_hit=True)
        trade_journal.record("close", "A", status="closed_tp3", outcome="WIN", realized_pnl_pct=4.2, tp1_hit=True, tp2_hit=True, tp3_hit=True)
        trade_journal.record("open", "B", coin="XRP", status="open")
        trade_journal.record("close", "B", status="closed_sl", outcome="LOSS", realized_pnl_pct=-2.0)
        stats = trade_journal.statistics()
        self.assertEqual(stats["tracked"], 2)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertAlmostEqual(stats["win_rate"], 0.5)
        self.assertAlmostEqual(stats["avg_realized_pnl_pct"], 1.1)
        self.assertEqual(stats["tp3_hits"], 1)

    def test_invalid_json_is_ignored(self):
        with open(trade_journal.PATH, "w", encoding="utf-8") as f:
            f.write("not-json\n")
            f.write('{"event":"open","position_id":"X","status":"open"}\n')
        self.assertEqual(trade_journal.statistics()["tracked"], 1)


if __name__ == "__main__":
    unittest.main()
