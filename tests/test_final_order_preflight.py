import tempfile
import unittest
from unittest.mock import patch

from exchange_adapter import SpotMarket
from execution_engine import ExecutionEngine, ExecutionIntent
from two_leg_execution import LegState
from two_leg_executor import TwoLegExecutor


class FakeAdapter:
    name = "BINANCE"

    def get_spot_markets(self):
        return [SpotMarket("SOLUSDT", "SOL", "USDT", 0.01, 10.0, 0.01, 0.01)]

    def place_spot_order(self, symbol, side, quantity, *, price=None, order_type="LIMIT", client_order_id=None):
        return {
            "orderId": client_order_id,
            "status": "NEW",
            "executedQty": str(quantity),
            "avgPrice": str(price),
        }


class FinalOrderPreflightTests(unittest.TestCase):
    def _engine(self, root):
        cfg = {
            "execution_live_enabled": True,
            "execution_max_notional_usdt": 300,
            "execution_allow_market_orders": False,
            "execution_allowed_exchanges": ["BINANCE"],
            "execution_max_signal_drift_pct": 0.5,
        }
        return ExecutionEngine(cfg, state_dir=root)

    def _intent(self):
        return ExecutionIntent(
            id="abc123",
            symbol="SOLUSDT",
            buy_exchange="BINANCE",
            sell_exchange="BYBIT",
            notional_usdt=100,
            buy_price=100,
            sell_price=101,
            net_pct=0.5,
            transfer_required=True,
            network="SOL",
            status="READY_FOR_ADAPTER",
        )

    def test_buy_order_passes_central_preflight_before_adapter(self):
        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"EXECUTION_ENABLED": "true"}):
            engine = self._engine(root)
            executor = TwoLegExecutor(engine, state_dir=root)
            coordinator = executor.coordinator(self._intent(), 1.0)
            state = executor.submit_buy(
                self._intent(), coordinator, FakeAdapter(),
                price=100, order_type="LIMIT", revalidate=lambda _: True,
            )
            self.assertEqual(state, LegState.BUY_FILLED)

    def test_market_buy_is_rejected_before_adapter(self):
        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"EXECUTION_ENABLED": "true"}):
            engine = self._engine(root)
            executor = TwoLegExecutor(engine, state_dir=root)
            intent = self._intent()
            coordinator = executor.coordinator(intent, 1.0)
            with self.assertRaises(PermissionError):
                executor.submit_buy(
                    intent, coordinator, FakeAdapter(),
                    price=100, order_type="MARKET", revalidate=lambda _: True,
                )


if __name__ == "__main__":
    unittest.main()
