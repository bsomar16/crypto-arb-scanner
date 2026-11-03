import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import download_sls_history as downloader


def kline(open_ms, close="10"):
    return [open_ms, "9", "11", "8", close, "100", open_ms + 299999,
            "900", 5, "40", "360", "0"]


class SlsHistoryDownloaderTests(unittest.TestCase):

    def test_utc_parser_treats_naive_dates_as_utc(self):
        self.assertEqual(downloader._utc_ms("1970-01-01"), 0)
        self.assertEqual(downloader._utc_ms("1970-01-01T01:00:00+01:00"), 0)

    def test_download_pair_paginates_and_keeps_requested_range(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            query = parse_qs(urlparse(url).query)
            start = int(query["startTime"][0])
            if len(calls) == 1:
                return [kline(i * 300000) for i in range(downloader.LIMIT)]
            return [kline(start)]

        end_ms = downloader.LIMIT * 300000 + 300000
        rows = downloader._download_pair("ETHUSDT", "5m", 0, end_ms,
                                         fetch_json=fake_fetch, pause=0)
        self.assertEqual(len(rows), downloader.LIMIT + 1)
        self.assertEqual(rows[0]["t"], 0)
        self.assertEqual(rows[-1]["t"], (downloader.LIMIT - 1) * 300000 + 1)
        self.assertEqual(rows[0]["q"], 900.0)
        self.assertEqual(len(calls), 2)
        first_start = int(parse_qs(urlparse(calls[0]).query)["startTime"][0])
        second_start = int(parse_qs(urlparse(calls[1]).query)["startTime"][0])
        self.assertEqual(first_start, 0)
        self.assertEqual(second_start, (downloader.LIMIT - 1) * 300000 + 1)

    def test_download_dataset_adds_btc_and_writes_replay_schema(self):
        def fake_fetch(url):
            if url.endswith("/api/v3/exchangeInfo"):
                return {"symbols": [
                    {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT",
                     "isSpotTradingAllowed": True},
                    {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT",
                     "isSpotTradingAllowed": True},
                ]}
            query = parse_qs(urlparse(url).query)
            start = int(query["startTime"][0])
            return [kline(start)]

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "candles.json"
            result = downloader.download_dataset(
                ["ETH"], ["5m"], 0, 300000, output, fetch_json=fake_fetch, pause=0
            )
            self.assertEqual(result["symbols"], ["BTC", "ETH"])
            import json
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(set(payload["candles"]), {"BTC", "ETH"})
            self.assertEqual(payload["candles"]["ETH"]["5m"][0]["q"], 900.0)

    def test_rejects_non_spot_symbol(self):
        def fake_fetch(url):
            return {"symbols": []}
        with self.assertRaisesRegex(ValueError, "not an active"):
            downloader.download_dataset(["FAKE"], ["5m"], 0, 300000,
                                        "unused.json", fetch_json=fake_fetch, pause=0)


if __name__ == "__main__":
    unittest.main()
