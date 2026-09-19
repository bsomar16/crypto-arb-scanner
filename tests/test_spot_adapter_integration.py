import os
import unittest
from unittest.mock import patch

from adapters.binance import BinanceSpotAdapter
from adapters.bybit import BybitSpotAdapter
from adapters.okx import OKXSpotAdapter
from adapters.bitget_spot import BitgetSpotAdapter
from adapters.mexc_spot import MexcSpotAdapter


class SpotAdapterIntegrationHarnessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EXECUTION_ENABLED": "true"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_authenticated_spot_order_path_for_http_json_adapters(self):
        cases = [
            (BinanceSpotAdapter("k", "s"), "adapters.binance.request_json", {"orderId": "b1", "status": "NEW"}),
            (BybitSpotAdapter("k", "s"), "adapters.bybit.request_json",
             {"retCode": 0, "result": {"orderId": "b1", "orderStatus": "New"}}),
            (OKXSpotAdapter("k", "s", "p"), "adapters.okx.request_json",
             {"code": "0", "data": [{"ordId": "o1", "state": "live"}]}),
        ]
        for adapter, target, response in cases:
            with self.subTest(adapter=adapter.name), patch(target, return_value=response) as request:
                result = adapter.place_spot_order("SOLUSDT", "BUY", 1.0, price=100.0,
                                                  order_type="LIMIT", client_order_id="arb-test")
                self.assertEqual(result, response)
                self.assertEqual(request.call_count, 1)
                kwargs = request.call_args.kwargs
                payload = kwargs.get("params") or kwargs.get("body") or {}
                serialized = repr(payload)
                self.assertIn("SOLUSDT", serialized)
                self.assertIn("arb-test", serialized)

    def test_bitget_spot_order_path_reaches_authenticated_transport(self):
        adapter = BitgetSpotAdapter("k", "s", "p")
        with patch("adapters.bitget_spot.urlopen") as urlopen:
            response = urlopen.return_value.__enter__.return_value
            response.read.return_value = b'{"code":"00000","data":{"orderId":"bg1"}}'
            result = adapter.place_spot_order("SOLUSDT", "SELL", 1.0, price=100.0,
                                              order_type="LIMIT", client_order_id="arb-test")
            self.assertEqual(result["orderId"], "bg1")
            self.assertTrue(urlopen.called)

    def test_mexc_spot_order_path_reaches_authenticated_transport(self):
        adapter = MexcSpotAdapter("k", "s")
        with patch("adapters.mexc_spot.urlopen") as urlopen:
            response = urlopen.return_value.__enter__.return_value
            response.read.return_value = b'{"orderId":"mx1","status":"NEW"}'
            result = adapter.place_spot_order("SOLUSDT", "BUY", 1.0, price=100.0,
                                              order_type="LIMIT", client_order_id="arb-test")
            self.assertEqual(result["orderId"], "mx1")
            self.assertTrue(urlopen.called)

    def test_derivative_or_invalid_requests_are_rejected_before_transport(self):
        for module in ("adapters.binance", "adapters.bybit", "adapters.okx",
                       "adapters.bitget_spot", "adapters.mexc_spot"):
            with self.subTest(module=module):
                if module in ("adapters.binance", "adapters.bybit", "adapters.okx"):
                    target = module + ".request_json"
                else:
                    target = module + ".urlopen"
                with patch(target) as transport:
                    cls = {"adapters.binance": BinanceSpotAdapter,
                           "adapters.bybit": BybitSpotAdapter,
                           "adapters.okx": OKXSpotAdapter,
                           "adapters.bitget_spot": BitgetSpotAdapter,
                           "adapters.mexc_spot": MexcSpotAdapter}[module]
                    adapter = cls("k", "s", "p") if module == "adapters.okx" else cls("k", "s", "p") if module == "adapters.bitget_spot" else cls("k", "s")
                    with self.assertRaises((ValueError, RuntimeError)):
                        adapter.place_spot_order("SOLUSDT", "BUY", 1.0, price=100.0,
                                                 order_type="FUTURES", client_order_id="arb-test")
                    self.assertFalse(transport.called)


if __name__ == "__main__":
    unittest.main()
