import unittest

from two_leg_execution import LegFill, LegState, TransferStatus, TwoLegCoordinator, TwoLegIntent


class TwoLegExecutionTests(unittest.TestCase):
    def make(self):
        return TwoLegCoordinator(TwoLegIntent("i1", "SOLUSDT", "BINANCE", "BYBIT", 1.0, "SOL"))

    def test_full_binance_bybit_dry_run_flow(self):
        c = self.make()
        self.assertEqual(c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0)), LegState.BUY_FILLED)
        self.assertEqual(c.begin_transfer(), LegState.TRANSFER_PENDING)
        self.assertEqual(c.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0, "tx1")), LegState.TRANSFER_CONFIRMED)
        self.assertEqual(c.accept_sell(LegFill("s1", "FILLED", 1.0, 1.0)), LegState.COMPLETED)

    def test_partial_first_leg_never_allows_transfer(self):
        c = self.make()
        self.assertEqual(c.accept_buy(LegFill("b1", "PARTIALLY_FILLED", 1.0, 0.4)), LegState.BUY_PARTIAL)
        with self.assertRaises(ValueError):
            c.begin_transfer()

    def test_rejected_second_leg_is_failure_recovery_state(self):
        c = self.make()
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        c.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0))
        self.assertEqual(c.accept_sell(LegFill("s1", "REJECTED", 1.0, 0.0)), LegState.FAILED)
        self.assertIn("sell leg rejected", c.intent.error)

    def test_restart_persisted_state_can_continue(self):
        saved = {}
        c = TwoLegCoordinator(TwoLegIntent("i1", "SOLUSDT", "BINANCE", "BYBIT", 1.0), saved.__setitem__)
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        self.assertEqual(saved["i1"].state, LegState.TRANSFER_PENDING)
        restored = TwoLegCoordinator(saved["i1"])
        self.assertEqual(restored.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0)), LegState.TRANSFER_CONFIRMED)

    def test_stale_or_incomplete_transfer_cannot_cover_buy(self):
        c = self.make()
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        self.assertEqual(c.accept_transfer(TransferStatus("w1", "CONFIRMED", 0.8)), LegState.FAILED)

    def test_sell_cannot_exceed_transferred_quantity(self):
        c = self.make()
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        c.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0))
        self.assertEqual(c.accept_sell(LegFill("s1", "PARTIALLY_FILLED", 1.2, 1.1)), LegState.FAILED)


if __name__ == "__main__":
    unittest.main()
