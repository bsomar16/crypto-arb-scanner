import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from execution_engine import ExecutionEngine
from exchange_adapter import NetworkInfo, SpotMarket
from two_leg_executor import TwoLegExecutor
from two_leg_execution import LegState
import trade_journal


class FakeAdapter:
    def __init__(self, *, memo_required=False, memo="", balance=10.0, withdraw_fee=0.01, deposit_enabled=True, withdraw_enabled=True):
        self.name = "binance"
        self.orders = {}
        self.withdrawals = []
        self.withdraw_record = {}
        self.deposit_record = {}
        self.memo_required = memo_required
        self.memo = memo
        self.balance = balance
        self.withdraw_fee = withdraw_fee
        self.deposit_enabled = deposit_enabled
        self.withdraw_enabled = withdraw_enabled

    def get_spot_markets(self):
        return [SpotMarket("SOLUSDT", "SOL", "USDT", 0.001, 5.0, 0.001, 0.01)]

    def place_spot_order(self, symbol, side, quantity, *, price=None, order_type="LIMIT", client_order_id=None):
        oid = client_order_id or "order-1"
        self.orders[oid] = {"status": "NEW", "executedQty": 0, "price": price or 0}
        return {"orderId": oid, "status": "NEW", "executedQty": 0}

    def get_order(self, symbol, order_id):
        return self.orders[order_id]

    def get_spot_balances(self):
        return {"SOL": self.balance}

    def get_networks(self, asset):
        return [NetworkInfo("SOL", self.deposit_enabled, self.withdraw_enabled, self.withdraw_fee, 0.001,
                             self.memo_required, raw_chain="SOL")]

    def get_deposit_address(self, asset, network):
        return "destination-address"

    def get_deposit_details(self, asset, network):
        return {"address": self.get_deposit_address(asset, network), "memo": self.memo,
                "memo_type": "tag" if self.memo else ""}

    def withdraw_spot(self, asset, amount, address, network, *, memo=None, memo_type=None, client_withdrawal_id=None):
        transfer_id = client_withdrawal_id or "withdraw-1"
        self.withdrawals.append((asset, amount, address, network, memo, memo_type))
        self.withdraw_record = {"id": transfer_id, "coin": asset, "network": network, "amount": str(amount),
                                "status": 6, "txId": "tx1", "toAddress": address}
        return {"id": transfer_id, "txId": "tx1"}

    def _signed(self, method, path, params):
        if "withdraw/history" in path:
            return [self.withdraw_record] if self.withdraw_record else []
        if "deposit/hisrec" in path:
            return [self.deposit_record] if self.deposit_record else []
        return []

    def _private(self, method, path, params=None, body=None):
        if "withdraw/query-record" in path or "deposit/query-record" in path:
            return {"retCode": 0, "result": {"rows": []}}
        return {"code": "0", "data": []}

    def _request(self, method, path, params=None, body=None, auth=False):
        return []


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

    def _buy_filled(self):
        self.assertEqual(self.executor.submit_buy(self.intent, self.coordinator, self.adapter, revalidate=lambda _: True), LegState.BUY_SUBMITTED)
        self.adapter.orders[self.coordinator.intent.buy_order_id] = {"status": "FILLED", "executedQty": 1.0, "price": 100}
        self.assertEqual(self.executor.reconcile_buy(self.intent, self.coordinator, self.adapter), LegState.BUY_FILLED)

    def test_buy_reconciliation_transfer_and_sell_gate(self):
        self._buy_filled()
        destination = FakeAdapter()
        self.assertEqual(self.executor.submit_transfer(self.intent, self.coordinator, self.adapter, destination, "SOL", revalidate=lambda _: True), LegState.TRANSFER_PENDING)
        tid = self.coordinator.intent.transfer_id
        self.assertIsNotNone(tid)
        with self.assertRaises(ValueError):
            self.executor.confirm_transfer(self.intent, self.coordinator, tid, destination_balance_confirmed=False)
        with self.assertRaises(ValueError):
            self.executor.confirm_transfer(self.intent, self.coordinator, tid, destination_balance_confirmed=True)
        destination.deposit_record = {"id": "d1", "coin": "SOL", "network": "SOL", "amount": "1", "status": 1,
                                     "txId": "tx1", "address": "destination-address"}
        state, result = self.executor.reconcile_transfer(self.intent, self.coordinator, self.adapter, destination, "SOL", tid)
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(state, LegState.TRANSFER_CONFIRMED)
        self.assertEqual(self.executor.submit_sell(self.intent, self.coordinator, destination, revalidate=lambda _: True), LegState.SELL_SUBMITTED)

    def test_base_asset_buy_fee_is_not_withdrawn(self):
        self.executor.submit_buy(self.intent, self.coordinator, self.adapter, revalidate=lambda _: True)
        order_id = self.coordinator.intent.buy_order_id
        self.adapter.orders[order_id] = {"status": "FILLED", "executedQty": 1.0, "avgPrice": 100,
                                         "feeAmount": 0.002, "feeCurrency": "SOL"}
        self.assertEqual(self.executor.reconcile_buy(self.intent, self.coordinator, self.adapter), LegState.BUY_FILLED)
        destination = FakeAdapter()
        self.assertEqual(self.executor.submit_transfer(self.intent, self.coordinator, self.adapter, destination, "SOL", revalidate=lambda _: True), LegState.TRANSFER_PENDING)
        self.assertEqual(self.coordinator.intent.transferred_qty, 0.998)
        self.assertEqual(self.adapter.withdrawals[0][1], 0.998)

    def test_transfer_propagates_destination_memo(self):
        self._buy_filled()
        destination = FakeAdapter(memo_required=True, memo="123456")
        self.assertEqual(self.executor.submit_transfer(self.intent, self.coordinator, self.adapter, destination, "SOL", revalidate=lambda _: True), LegState.TRANSFER_PENDING)
        self.assertEqual(self.adapter.withdrawals[0][4:], ("123456", "tag"))

    def test_transfer_blocks_required_destination_memo(self):
        self._buy_filled()
        destination = FakeAdapter(memo_required=True)
        with self.assertRaises(RuntimeError, msg="memo-required destinations must fail closed"):
            self.executor.submit_transfer(self.intent, self.coordinator, self.adapter, destination, "SOL", revalidate=lambda _: True)
        self.assertEqual(self.adapter.withdrawals, [])

    def test_transfer_blocks_insufficient_balance_for_fee(self):
        self._buy_filled()
        source = FakeAdapter(balance=1.005, withdraw_fee=0.01)
        destination = FakeAdapter()
        with self.assertRaises(RuntimeError, msg="withdrawal must not be submitted when fee cannot be covered"):
            self.executor.submit_transfer(self.intent, self.coordinator, source, destination, "SOL", revalidate=lambda _: True)
        self.assertEqual(source.withdrawals, [])

    def test_transfer_blocks_disabled_network(self):
        self._buy_filled()
        source = FakeAdapter(withdraw_enabled=False)
        destination = FakeAdapter()
        with self.assertRaises(RuntimeError):
            self.executor.submit_transfer(self.intent, self.coordinator, source, destination, "SOL", revalidate=lambda _: True)
        self.assertEqual(source.withdrawals, [])

    def test_stale_revalidation_blocks_adapter_call(self):
        with self.assertRaises(ValueError):
            self.executor.submit_buy(self.intent, self.coordinator, self.adapter, revalidate=lambda _: False)
        self.assertEqual(self.intent.status, "FAILED")
        self.assertEqual(self.adapter.orders, {})

    def test_buy_order_is_quantized_to_spot_rules(self):
        coordinator = self.executor.coordinator(self.intent, 1.0009)
        self.assertEqual(self.executor.submit_buy(self.intent, coordinator, self.adapter, price=100.009, revalidate=lambda _: True), LegState.BUY_SUBMITTED)
        order = self.adapter.orders[coordinator.intent.buy_order_id]
        self.assertEqual(coordinator.intent.requested_qty, 1.0)
        self.assertEqual(order["price"], 100.0)

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


if __name__ == "__main__": unittest.main()
