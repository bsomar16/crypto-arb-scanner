import os
import unittest
from unittest.mock import patch

from adapter_factory import create_spot_adapter
from adapters.binance import BinanceSpotAdapter
from adapters.bybit import BybitSpotAdapter
from adapters.okx import OKXSpotAdapter
from adapters.bitget_spot import BitgetSpotAdapter
from adapters.mexc_spot import MexcSpotAdapter


class SpotAdapterSafetyTests(unittest.TestCase):
    def test_all_five_adapters_construct_without_network_access(self):
        self.assertEqual(create_spot_adapter("binance").name, "binance")
        self.assertEqual(create_spot_adapter("bybit").name, "bybit")
        self.assertEqual(create_spot_adapter("okx").name, "okx")
        self.assertEqual(create_spot_adapter("bitget").name, "bitget")
        self.assertEqual(create_spot_adapter("mexc").name, "mexc")

    def test_live_order_is_disabled_by_default(self):
        adapters = [
            (BinanceSpotAdapter(), "BTCUSDT"),
            (BybitSpotAdapter(), "BTCUSDT"),
            (OKXSpotAdapter(), "BTC-USDT"),
            (BitgetSpotAdapter(), "BTCUSDT"),
            (MexcSpotAdapter(), "BTCUSDT"),
        ]
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "false"}, clear=False):
            for adapter, symbol in adapters:
                with self.assertRaises(RuntimeError):
                    adapter.place_spot_order(symbol, "BUY", 0.001, price=1.0)

    def test_public_orderbook_methods_pin_spot_routes(self):
        fixtures = {
            "binance": {"bids": [["100", "2"]], "asks": [["101", "2"]]},
            "bybit": {"b": [["100", "2"]], "a": [["101", "2"]]},
            "okx": {"bids": [["100", "2"]], "asks": [["101", "2"]]},
        }
        with (
            patch("adapters.binance.request_json", return_value=fixtures["binance"]) as b,
            patch("adapters.bybit.request_json", return_value={"retCode": 0, "result": fixtures["bybit"]}) as y,
            patch("adapters.okx.request_json", return_value={"code": "0", "data": [fixtures["okx"]]}) as o,
        ):
            BinanceSpotAdapter().get_order_book("BTCUSDT")
            BybitSpotAdapter().get_order_book("BTCUSDT")
            OKXSpotAdapter().get_order_book("BTC-USDT")
            self.assertEqual(b.call_args.kwargs["params"]["symbol"], "BTCUSDT")
            self.assertEqual(y.call_args.kwargs["params"]["category"], "spot")
            self.assertEqual(o.call_args.kwargs["params"]["instId"], "BTC-USDT")


if __name__ == "__main__":
    unittest.main()
