#!/usr/bin/env python3
"""Phase 5 real-time SPOT market-data layer.

This module is deliberately market-data only: it never places an order and it
never subscribes to derivatives/margin channels.  Each adapter normalizes its
best bid/ask into the same structure so the opportunity engine can compare
exchanges without knowing exchange-specific wire formats.

The websocket-client dependency is optional for the rest of the project; it is
required only when running this daemon.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

try:
    import websocket
except ImportError:  # pragma: no cover - exercised only without runtime deps
    websocket = None


@dataclass(frozen=True)
class BBO:
    exchange: str
    symbol: str
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float
    ts_ms: int
    source: str = "websocket"


class SpotFeed:
    name = "base"

    def __init__(self, symbols: Iterable[str], on_bbo: Callable[[BBO], None], logger=print):
        self.symbols = [s.upper() for s in symbols]
        self.on_bbo = on_bbo
        self.log = logger
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run_forever(self):
        if websocket is None:
            raise RuntimeError("websocket-client is required; install requirements-runtime.txt")
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception as exc:
                self.log(f"[{self.name}] websocket error: {exc}")
            if not self._stop.is_set():
                self._stop.wait(2.0)

    def _run_once(self):
        raise NotImplementedError

    @staticmethod
    def _emit(feed: "SpotFeed", exchange: str, symbol: str, bid, bid_qty, ask, ask_qty, ts=None):
        try:
            bid, bid_qty = float(bid), float(bid_qty)
            ask, ask_qty = float(ask), float(ask_qty)
        except (TypeError, ValueError):
            return
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        feed.on_bbo(BBO(exchange, symbol.upper(), bid, bid_qty, ask, ask_qty,
                        int(ts or time.time() * 1000)))


class BinanceSpotFeed(SpotFeed):
    name = "binance"
    url = "wss://stream.binance.com:9443/stream"

    def _run_once(self):
        streams = "/".join(f"{s.lower()}@bookTicker" for s in self.symbols)
        url = f"{self.url}?streams={streams}"

        def on_message(ws, raw):
            msg = json.loads(raw)
            d = msg.get("data", msg)
            self._emit(self, self.name, d.get("s", ""), d.get("b"), d.get("B"),
                       d.get("a"), d.get("A"), d.get("E"))

        def on_error(ws, err):
            raise RuntimeError(err)

        ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error)
        ws.run_forever(ping_interval=120, ping_timeout=30)


class BybitSpotFeed(SpotFeed):
    name = "bybit"
    url = "wss://stream.bybit.com/v5/public/spot"

    def _run_once(self):
        def on_open(ws):
            ws.send(json.dumps({"op": "subscribe", "args": [f"orderbook.1.{s}" for s in self.symbols]}))

        def on_message(ws, raw):
            msg = json.loads(raw)
            d = msg.get("data") or {}
            symbol = d.get("s") or (msg.get("topic", "").split(".")[-1])
            bids, asks = d.get("b") or [], d.get("a") or []
            if bids and asks:
                self._emit(self, self.name, symbol, bids[0][0], bids[0][1], asks[0][0], asks[0][1], msg.get("ts"))

        def on_error(ws, err):
            raise RuntimeError(err)

        ws = websocket.WebSocketApp(self.url, on_open=on_open, on_message=on_message, on_error=on_error)
        ws.run_forever(ping_interval=20, ping_timeout=10)


class OKXSpotFeed(SpotFeed):
    name = "okx"
    url = "wss://ws.okx.com:8443/ws/v5/public"

    def _run_once(self):
        def on_open(ws):
            ws.send(json.dumps({"op": "subscribe", "args": [
                {"channel": "books5", "instId": s} for s in self.symbols
            ]}))

        def on_message(ws, raw):
            msg = json.loads(raw)
            for row in msg.get("data", []) or []:
                bids, asks = row.get("bids") or [], row.get("asks") or []
                if bids and asks:
                    self._emit(self, self.name, row.get("instId", ""), bids[0][0], bids[0][1],
                               asks[0][0], asks[0][1], row.get("ts"))

        def on_error(ws, err):
            raise RuntimeError(err)

        ws = websocket.WebSocketApp(self.url, on_open=on_open, on_message=on_message, on_error=on_error)
        ws.run_forever(ping_interval=20, ping_timeout=10)


class MEXCSpotFeed(SpotFeed):
    name = "mexc"
    url = "wss://wbs-api.mexc.com/ws"

    def _run_once(self):
        def on_open(ws):
            params = [f"spot@public.aggre.bookTicker.v3.api.pb@100ms@{s}" for s in self.symbols]
            ws.send(json.dumps({"method": "SUBSCRIPTION", "params": params, "id": int(time.time())}))

        def on_message(ws, raw):
            # MEXC's current aggregated streams may be protobuf encoded.  The
            # JSON book-ticker variant is handled when the gateway emits JSON;
            # protobuf decoding is intentionally isolated for a later adapter.
            if isinstance(raw, bytes):
                return
            msg = json.loads(raw)
            t = msg.get("publicbookticker") or {}
            if t:
                self._emit(self, self.name, msg.get("symbol", ""), t.get("bidprice"), t.get("bidquantity"),
                           t.get("askprice"), t.get("askquantity"), msg.get("sendtime"))

        def on_error(ws, err):
            raise RuntimeError(err)

        ws = websocket.WebSocketApp(self.url, on_open=on_open, on_message=on_message, on_error=on_error)
        ws.run_forever(ping_interval=20, ping_timeout=10)


class BitgetSpotFeed(SpotFeed):
    """Bitget v2 public SPOT book feed.

    Kept behind the same normalized contract.  If Bitget changes its public
    channel schema, only this adapter needs adjustment.
    """
    name = "bitget"
    url = "wss://ws.bitget.com/v2/ws/public"

    def _run_once(self):
        def on_open(ws):
            ws.send(json.dumps({"op": "subscribe", "args": [
                {"instType": "SPOT", "channel": "books", "instId": s} for s in self.symbols
            ]}))

        def on_message(ws, raw):
            msg = json.loads(raw)
            for row in msg.get("data", []) or []:
                bids, asks = row.get("bids") or [], row.get("asks") or []
                if bids and asks:
                    self._emit(self, self.name, msg.get("arg", {}).get("instId", ""),
                               bids[0][0], bids[0][1], asks[0][0], asks[0][1], msg.get("ts"))

        def on_error(ws, err):
            raise RuntimeError(err)

        ws = websocket.WebSocketApp(self.url, on_open=on_open, on_message=on_message, on_error=on_error)
        ws.run_forever(ping_interval=20, ping_timeout=10)


FEEDS = {
    "binance": BinanceSpotFeed,
    "bybit": BybitSpotFeed,
    "okx": OKXSpotFeed,
    "bitget": BitgetSpotFeed,
    "mexc": MEXCSpotFeed,
}


def build_feeds(names: Iterable[str], symbols: Iterable[str], on_bbo, logger=print):
    out = []
    for name in names:
        cls = FEEDS.get(name.lower())
        if cls:
            out.append(cls(symbols, on_bbo, logger))
    return out
