import os
import tempfile
import unittest
from types import SimpleNamespace

from execution_engine import ExecutionEngine
from exchange_adapter import NetworkInfo, SpotMarket
from two_leg_executor import TwoLegExecutor
from two_leg_execution import LegState
import trade_journal


class PaperAdapter:
    def __init__(self, name, *, sell=False):
        self.name = name
        self.sell = sell
        self.orders = {}
        self.withdrawals = []
        self.deposit_record = {}
        self.balance = 10.0
        self.withdraw_fee = 0.01

    def get_spot_markets(self):
        return [SpotMarket("SOLUSDT", "SOL", "USDT", 0.001, 5.0, 0.001, 0.01)]

    def place_spot_order(self, symbol, side, quantity, *, price=None, order_type="LIMIT", client_order_id=None):
        oid = client_order_id or f"{self.name}-{len(self.orders)+1}"
        if oid in self.orders:
            return {"orderId": oid, **self.orders[oid]}
        self.orders[oid] = {"status": "NEW", "executedQty": 0.0, "avgPrice": price or 0.0}
        return {"orderId": oid, **self.orders[oid]}

    def get_order(self, symbol, order_id):
        return dict(self.orders[order_id]) | {"orderId": order_id}

    def get_spot_balances(self):
        return {"SOL": self.balance}

    def get_networks(self, asset):
        return [NetworkInfo("SOL", True, True, self.withdraw_fee, 0.001, False, raw_chain="SOL")]

    def get_deposit_details(self, asset, network):
        return {"address": "paper-destination"}

    def withdraw_spot(self, asset, amount, address, network, *, memo=None, memo_type=None, client_withdrawal_id=None):
        transfer_id = client_withdrawal_id or "paper-transfer"
        self.withdrawals.append((transfer_id, amount))
        return {"id": transfer_id, "txId": "paper-tx"}

    def _signed(self, method, path, params):
        if "withdraw/history" in path:
            return [{"id": self.withdrawals[0][0], "coin": "SOL", "network": "SOL", "amount": str(self.withdrawals[0][1]),
                     "status": 6, "txId": "paper-tx", "toAddress": "paper-destination"}] if self.withdrawals else []
        if "deposit/hisrec" in path:
            return [self.deposit_record] if self.deposit_record else []
        return []

    def _private(self, method, path, params=None, body=None):
        if "deposit/query-record" in path:
            rows = [self.deposit_record] if self.deposit_record else []
            return {"retCode": 0, "result": {"rows": rows}}
        if "withdraw/query-record" in path:
            rows = [{"withdrawId": self.withdrawals[0][0], "coin": "SOL", "chain": "SOL", "amount": str(self.withdrawals[0][1]),
                     "status": "success", "txID": "paper-tx", "toAddress": "paper-destination"}] if self.withdrawals else []
            return {"retCode": 0, "result": {"rows": rows}}
        return {"retCode": 0, "result": {"rows": []}}

    def _request(self, method, path, params=None, body=None, auth=False):
        return []


class FullExecutionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.old_enabled = os.environ.get("EXECUTION_ENABLED")
        os.environ["EXECUTION_ENABLED"] = "true"
        self.tmp = tempfile.TemporaryDirectory()
        self.old_journal = trade_journal.PATH
        trade_journal.PATH = os.path.join(self.tmp.name, "trade_journal.jsonl")
        self.cfg = {"execution_live_enabled": True, "execution_max_notional_usdt": 300, "realtime_min_net_pct": 0.5}
        self.opportunity = SimpleNamespace(
            symbol="SOLUSDT", buy_exchange="binance", sell_exchange="bybit",
            executable_notional_usdt=300, buy_ask=100, sell_bid=104, net_pct=2.8,
            transfer_required=True, network="SOL",
        )

    def tearDown(self):
        trade_journal.PATH = self.old_journal
        if self.old_enabled is None:
            os.environ.pop("EXECUTION_ENABLED", None)
        else:
            os.environ["EXECUTION_ENABLED"] = self.old_enabled
        self.tmp.cleanup()

    def _intent(self):
        engine = ExecutionEngine(self.cfg, self.tmp.name)
        intent = engine.create_intent(self.opportunity)
        engine.confirm(intent, True, lambda _: True)
        return engine, intent

    def test_full_300_usdt_paper_flow_and_restart_recovery(self):
        engine, intent = self._intent()
        source = PaperAdapter("binance")
        destination = PaperAdapter("bybit", sell=True)
        executor = TwoLegExecutor(engine, self.tmp.name)
        coordinator = executor.coordinator(intent, 3.0)

        self.assertEqual(executor.submit_buy(intent, coordinator, source, price=100, revalidate=lambda _: True), LegState.BUY_SUBMITTED)
        buy_id = coordinator.intent.buy_order_id
        self.assertEqual(len(source.orders), 1)

        engine2 = ExecutionEngine(self.cfg, self.tmp.name)
        intent2 = type(intent)(**engine2._intents[intent.id])
        executor2 = TwoLegExecutor(engine2, self.tmp.name)
        coordinator2 = executor2.coordinator(intent2, 3.0)
        self.assertEqual(coordinator2.intent.buy_order_id, buy_id)
        source.orders[buy_id].update({"status": "FILLED", "executedQty": 3.0, "avgPrice": 100.0,
                                      "feeAmount": 0.003, "feeCurrency": "SOL"})
        self.assertEqual(executor2.reconcile_buy(intent2, coordinator2, source), LegState.BUY_FILLED)
        self.assertEqual(coordinator2.intent.filled_qty, 3.0)

        engine2.confirm_withdrawal(intent2, True)
        self.assertEqual(executor2.submit_transfer(intent2, coordinator2, source, destination, "SOL", revalidate=lambda _: True), LegState.TRANSFER_PENDING)
        transfer_id = coordinator2.intent.transfer_id
        self.assertEqual(len(source.withdrawals), 1)

        engine3 = ExecutionEngine(self.cfg, self.tmp.name)
        intent3 = type(intent)(**engine3._intents[intent.id])
        executor3 = TwoLegExecutor(engine3, self.tmp.name)
        coordinator3 = executor3.coordinator(intent3, 3.0)
        self.assertEqual(coordinator3.intent.transfer_id, transfer_id)
        self.assertEqual(len(source.withdrawals), 1)

        destination.deposit_record = {"id": "deposit-1", "coin": "SOL", "network": "SOL",
                                      "amount": "2.997", "status": 3, "txId": "paper-tx",
                                      "address": "paper-destination"}
        state, result = executor3.reconcile_transfer(intent3, coordinator3, source, destination, "SOL", transfer_id)
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(state, LegState.TRANSFER_CONFIRMED)
        self.assertTrue(coordinator3.intent.destination_deposit_confirmed)

        # Simulate a process restart after destination credit was independently confirmed.
        engine4 = ExecutionEngine(self.cfg, self.tmp.name)
        intent4 = type(intent)(**engine4._intents[intent.id])
        executor4 = TwoLegExecutor(engine4, self.tmp.name)
        coordinator4 = executor4.coordinator(intent4, 3.0)
        self.assertEqual(coordinator4.intent.state, LegState.TRANSFER_CONFIRMED)
        self.assertTrue(coordinator4.intent.destination_deposit_confirmed)
        self.assertEqual(coordinator4.intent.transfer_id, transfer_id)

        self.assertEqual(executor4.submit_sell(intent4, coordinator4, destination, price=104, revalidate=lambda _: True), LegState.SELL_SUBMITTED)
        sell_id = coordinator4.intent.sell_order_id
        self.assertEqual(len(destination.orders), 1)
        self.assertEqual(executor4.submit_sell(intent4, coordinator4, destination, price=104, revalidate=lambda _: True), LegState.SELL_SUBMITTED)
        self.assertEqual(len(destination.orders), 1)
        self.assertEqual(sell_id, coordinator4.intent.sell_order_id)

        destination.orders[sell_id].update({"status": "FILLED", "executedQty": 2.997, "avgPrice": 104.0,
                                             "feeAmount": 0.001, "feeCurrency": "USDT"})
        self.assertEqual(executor4.reconcile_sell(intent4, coordinator4, destination), LegState.COMPLETED)

    def test_no_sell_before_destination_credit(self):
        engine, intent = self._intent()
        source = PaperAdapter("binance")
        destination = PaperAdapter("bybit", sell=True)
        executor = TwoLegExecutor(engine, self.tmp.name)
        coordinator = executor.coordinator(intent, 3.0)
        executor.submit_buy(intent, coordinator, source, price=100, revalidate=lambda _: True)
        buy_id = coordinator.intent.buy_order_id
        source.orders[buy_id].update({"status": "FILLED", "executedQty": 3.0, "avgPrice": 100.0})
        executor.reconcile_buy(intent, coordinator, source)
        engine.confirm_withdrawal(intent, True)
        executor.submit_transfer(intent, coordinator, source, destination, "SOL", revalidate=lambda _: True)
        with self.assertRaises(ValueError):
            executor.submit_sell(intent, coordinator, destination, price=104, revalidate=lambda _: True)
        self.assertEqual(destination.orders, {})


if __name__ == "__main__":
    unittest.main()
