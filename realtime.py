#!/usr/bin/env python3
"""Lightweight Binance Spot public WebSocket kline confirmation cache.

This module is deliberately signal-only: it never places orders and never
changes the SPOT execution guard. REST remains the source for historical
discovery; this cache supplies fresh closed candles for shortlisted symbols.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from urllib.parse import quote

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None

STREAM_BASE = "wss://stream.binance.com:9443/stream"
INTERVALS = ("5m", "15m", "1h")


class BinanceKlineCache:
    def __init__(self, symbols, intervals=INTERVALS, max_bars=120):
        self.symbols = sorted({str(s).upper() for s in symbols if s})
        self.intervals = tuple(i for i in intervals if i in INTERVALS)
        self.max_bars = max(50, int(max_bars))
        self._bars = defaultdict(lambda: deque(maxlen=self.max_bars))
        self._lock = threading.RLock()
        self._ws = None
        self._thread = None
        self._stop = threading.Event()
        self.last_event_ts = 0.0

    def _streams(self):
        return [
            f"{s.lower()}@kline_{interval}"
            for s in self.symbols
            for interval in self.intervals
        ]

    def _url(self):
        streams = "/".join(self._streams())
        return f"{STREAM_BASE}?streams={quote(streams, safe='@_/-')}"

    def _on_message(self, _ws, raw):
        try:
            payload = json.loads(raw)
            k = payload.get("data", {}).get("k", {})
            if not k or not k.get("x"):
                return
            symbol = str(k.get("s", "")).upper()
            if symbol.endswith("USDT"):
                symbol = symbol[:-4]
            interval = str(k.get("i", ""))
            if not symbol or interval not in INTERVALS:
                return
            bar = {
                "open_time": int(k["t"]),
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": float(k["c"]),
                "volume": float(k["v"]),
                "quote_volume": float(k["q"]),
            }
            with self._lock:
                bars = self._bars[(symbol, interval)]
                if bars and bars[-1]["open_time"] == bar["open_time"]:
                    bars[-1] = bar
                else:
                    bars.append(bar)
                self.last_event_ts = time.time()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return

    def start(self):
        if websocket is None:
            raise RuntimeError("websocket-client is required for realtime confirmation")
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def run():
            while not self._stop.is_set():
                try:
                    self._ws = websocket.WebSocketApp(
                        self._url(),
                        on_message=self._on_message,
                    )
                    self._ws.run_forever(ping_interval=60, ping_timeout=20)
                except Exception:
                    pass
                finally:
                    self._ws = None
                if not self._stop.is_set():
                    self._stop.wait(3)

        self._thread = threading.Thread(target=run, name="binance-kline-ws", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    def get(self, symbol, interval):
        with self._lock:
            return list(self._bars.get((str(symbol).upper(), interval), ()))

    def health(self):
        return {
            "connected": self._ws is not None,
            "last_event_ts": self.last_event_ts,
            "symbols": len(self.symbols),
            "intervals": list(self.intervals),
        }
