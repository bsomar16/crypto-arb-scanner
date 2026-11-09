import unittest

from two_leg_execution import LegFill, LegState, TransferStatus, TwoLegCoordinator, TwoLegIntent


class TwoLegExecutionTests(unittest.TestCase):
    def make(self, **kwargs):
        return TwoLegCoordinator(TwoLegIntent("i1", "SOLUSDT", "BINANCE", "BYBIT", 1.0, "SOL"), **kwargs)

    def confirm_transfer(self, c):
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        return c.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0, "tx1", True))

    def test_full_binance_bybit_dry_run_flow(self):
        c = self.make()
        self.assertEqual(c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0)), LegState.BUY_FILLED)
        self.assertEqual(c.begin_transfer(), LegState.TRANSFER_PENDING)
        self.assertEqual(c.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0, "tx1", True)), LegState.TRANSFER_CONFIRMED)
        self.assertEqual(c.accept_sell(LegFill("s1", "FILLED", 1.0, 1.0)), LegState.COMPLETED)

    def test_partial_first_leg_never_allows_transfer(self):
        c = self.make()
        self.assertEqual(c.accept_buy(LegFill("b1", "PARTIALLY_FILLED", 1.0, 0.4)), LegState.BUY_PARTIAL)
        with self.assertRaises(ValueError):
            c.begin_transfer()

    def test_rejected_second_leg_is_failure_recovery_state(self):
        c = self.make()
        self.assertEqual(self.confirm_transfer(c), LegState.TRANSFER_CONFIRMED)
        self.assertEqual(c.accept_sell(LegFill("s1", "REJECTED", 1.0, 0.0)), LegState.FAILED)
        self.assertIn("sell leg rejected", c.intent.error)

    def test_restart_persisted_state_can_continue(self):
        saved = {}
        def persist(intent):
            saved[intent.intent_id] = intent
        c = TwoLegCoordinator(TwoLegIntent("i1", "SOLUSDT", "BINANCE", "BYBIT", 1.0), persist)
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        self.assertEqual(saved["i1"].state, LegState.TRANSFER_PENDING)
        restored = TwoLegCoordinator(saved["i1"])
        self.assertEqual(restored.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0, "tx1", True)), LegState.TRANSFER_CONFIRMED)

    def test_destination_deposit_must_be_confirmed(self):
        c = self.make()
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        self.assertEqual(c.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0, "tx1", False)), LegState.FAILED)

    def test_stale_or_incomplete_transfer_cannot_cover_buy(self):
        c = self.make()
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        self.assertEqual(c.accept_transfer(TransferStatus("w1", "CONFIRMED", 0.8, "tx1", True)), LegState.FAILED)

    def test_sell_cannot_exceed_transferred_quantity(self):
        c = self.make()
        self.confirm_transfer(c)
        self.assertEqual(c.accept_sell(LegFill("s1", "PARTIALLY_FILLED", 1.2, 1.1)), LegState.FAILED)

    def test_sell_filled_must_cover_confirmed_transfer(self):
        c = self.make()
        self.confirm_transfer(c)
        self.assertEqual(c.accept_sell(LegFill("s1", "FILLED", 1.0, 0.99)), LegState.FAILED)

    def test_each_leg_can_require_fresh_revalidation(self):
        calls = []
        def ok(intent):
            calls.append(intent.state)
            return True
        c = self.make(revalidate_buy=ok, revalidate_transfer=ok, revalidate_sell=ok)
        self.assertEqual(c.prepare_buy(), LegState.READY_FOR_ADAPTER)
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        c.begin_transfer()
        self.assertEqual(c.intent.state, LegState.TRANSFER_PENDING)
        c.accept_transfer(TransferStatus("w1", "CONFIRMED", 1.0, "tx1", True))
        self.assertEqual(c.prepare_sell(), LegState.TRANSFER_CONFIRMED)
        self.assertEqual(len(calls), 3)

    def test_failed_revalidation_stops_before_next_leg(self):
        c = self.make(revalidate_transfer=lambda _: False)
        c.accept_buy(LegFill("b1", "FILLED", 1.0, 1.0))
        self.assertEqual(c.begin_transfer(), LegState.FAILED)
        self.assertIsNotNone(c.intent.error)


if __name__ == "__main__":
    unittest.main()
