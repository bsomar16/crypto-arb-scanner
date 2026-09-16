import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from execution_engine import ExecutionEngine
from two_leg_executor import TwoLegExecutor
from two_leg_execution import LegState
import trade_journal


class FakeAdapter:
    def __init__(self):
        self.orders = {}
        self.withdrawals = []

    def place_spot_order(self, symbol, side, quantity, *, price=None, order_type="LIMIT", client_order_id=None):
        oid = client_order_id or "order-1"
        self.orders[oid] = {"status": "NEW", "executedQty": 0, "price": price or 0}
        return {"orderId": oid, "status": "NEW", "executedQty": 0}

    def get_order(self, symbol, order_id):
        return self.orders[order_id]

    def get_deposit_address(self, asset, network):
        return "destination-address"

    def withdraw_spot(self, asset, amount, address, network, *, client_withdrawal_id=None):
        self.withdrawals.append((asset, amount, address, network))
        return {"id": client_withdrawal_id or "withdraw-1"}


class TwoLegExecutorTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("EXECUTION_ENABLED")
        os.environ["EXECUTION_ENABLED"] = "true"
        self.tmp = tempfile.TemporaryDirectory()
        self.old_journal = trade_journal.PATH
        trade_journal.PATH = os.path.join(self.tmp.name, "trade_journal.jsonl")
        self.engine = ExecutionEngine({"execution_max_notional_usdt": 300}, self.tmp.name)
        self.opportunity = SimpleNamespace(
            symbol="SOLUSDT", buy_exchange="binance", sell_exchange="bybit",
            executable_notional_usdt=100, buy_ask=100, sell_bid=102, net_pct=1.5,
            transfer_required=True, network="SOL",
        )
        self.intent = self.engine.create_intent(self.opportunity)
        self.engine.confirm(self.intent, True, lambda _: True)
        self.executor = TwoLegExecutor(self.engine, self.tmp.name)
        self.coordinator = self.executor.coordinator(self.intent, 1.0)
        self.adapter = FakeAdapter()

    def tearDown(self):
        trade_journal.PATH = self.old_journal
        if self.old is None:
            os.environ.pop("EXECUTION_ENABLED", None)
        else:
            os.environ["EXECUTION_ENABLED"] = self.old
        self.tmp.cleanup()

    def test_buy_reconciliation_transfer_and_sell_gate(self):
        self.assertEqual(self.executor.submit_buy(self.intent, self.coordinator, self.adapter, revalidate=lambda _: True), LegState.BUY_SUBMITTED)
        self.adapter.orders[self.coordinator.intent.buy_order_id] = {"status": "FILLED", "executedQty": 1.0, "price": 100}
        self.assertEqual(self.executor.reconcile_buy(self.intent, self.coordinator, self.adapter), LegState.BUY_FILLED)
        destination = FakeAdapter()
        self.assertEqual(self.executor.submit_transfer(self.intent, self.coordinator, self.adapter, destination, "SOL", revalidate=lambda _: True), LegState.TRANSFER_PENDING)
        tid = self.coordinator.intent.transfer_id
        self.assertIsNotNone(tid)
        with self.assertRaises(ValueError):
            self.executor.confirm_transfer(self.intent, self.coordinator, tid, destination_balance_confirmed=False)
        self.assertEqual(self.executor.confirm_transfer(self.intent, self.coordinator, tid, destination_balance_confirmed=True), LegState.TRANSFER_CONFIRMED)
        self.assertEqual(self.executor.submit_sell(self.intent, self.coordinator, destination, revalidate=lambda _: True), LegState.SELL_SUBMITTED)

    def test_stale_revalidation_blocks_adapter_call(self):
        with self.assertRaises(ValueError):
            self.executor.submit_buy(self.intent, self.coordinator, self.adapter, revalidate=lambda _: False)
        self.assertEqual(self.intent.status, "FAILED")
        self.assertEqual(self.adapter.orders, {})

    def test_journal_records_real_state_transitions_without_poll_duplicates(self):
        self.executor.submit_buy(self.intent, self.coordinator, self.adapter, revalidate=lambda _: True)
        self.adapter.orders[self.coordinator.intent.buy_order_id] = {"status": "FILLED", "executedQty": 1.0, "price": 100}
        self.executor.reconcile_buy(self.intent, self.coordinator, self.adapter)
        self.executor.reconcile_buy(self.intent, self.coordinator, self.adapter)
        rows = trade_journal.read()
        transitions = [r for r in rows if r.get("event") == "execution_transition" and r.get("leg") == "buy"]
        orders = [r for r in rows if r.get("event") == "buy_order"]
        self.assertEqual([(r.get("state_from"), r.get("state_to")) for r in transitions], [("READY_FOR_ADAPTER", "BUY_SUBMITTED"), ("BUY_SUBMITTED", "BUY_FILLED")])
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[-1]["executed_qty"], 1.0)


if __name__ == "__main__":
    unittest.main()
