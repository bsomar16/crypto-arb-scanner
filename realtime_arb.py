#!/usr/bin/env python3
"""24/7 real-time SPOT arbitrage monitor.

Run this on a VPS/container, not GitHub Actions.  GitHub Actions remains useful
for scheduled reports/backtests; a websocket daemon is required for low-latency
market monitoring.

Default behavior is ALERT-ONLY / DRY-RUN.  It never submits orders.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict

from botutil import load_json, telegram_msg, log
from realtime_market import build_feeds, BBO
from realtime_opportunity import OpportunityEngine, best_opportunities


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"]
DEFAULT_EXCHANGES = ["binance", "bybit", "okx", "bitget", "mexc"]


def cfg():
    return load_json("config.json", {}) or {}


def env_list(name, default):
    raw = os.environ.get(name, "").strip()
    return [x.strip().upper() for x in raw.split(",") if x.strip()] if raw else list(default)


def format_opp(o):
    transfer = f"transfer={o.network}" if o.transfer_required else "pre-funded"
    return (
        f"⚡ <b>REAL-TIME SPOT ARB</b> · {o.symbol}\n"
        f"🟢 BUY {o.buy_exchange}: ${o.buy_ask:.8g}\n"
        f"🔴 SELL {o.sell_exchange}: ${o.sell_bid:.8g}\n"
        f"Gross: +{o.gross_pct:.3f}% · fees: {o.trading_fee_pct:.3f}% · "
        f"slippage reserve: {o.slippage_reserve_pct:.3f}%\n"
        f"<b>Net model: +{o.net_pct:.3f}%</b> · {transfer}\n"
        f"Data age: {o.stale_ms} ms\n\n"
        "🔐 ALERT ONLY — no order submitted. Explicit confirmation is required "
        "before any future execution step."
    )


def main():
    configuration = cfg()
    symbols = env_list("REALTIME_SYMBOLS", configuration.get("realtime_symbols", DEFAULT_SYMBOLS))
    exchanges = [x.lower() for x in env_list("REALTIME_EXCHANGES", configuration.get("realtime_exchanges", DEFAULT_EXCHANGES))]
    engine = OpportunityEngine(configuration)
    book = defaultdict(dict)
    lock = threading.Lock()
    last_sent = {}
    cooldown_ms = int(configuration.get("realtime_alert_cooldown_ms", 15000))
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    def on_bbo(bbo: BBO):
        with lock:
            book[bbo.symbol][bbo.exchange] = bbo
            opportunities = best_opportunities(book, engine)
        now = int(time.time() * 1000)
        for opp in opportunities[:3]:
            key = f"{opp.symbol}|{opp.buy_exchange}|{opp.sell_exchange}"
            if now - int(last_sent.get(key, 0)) < cooldown_ms:
                continue
            last_sent[key] = now
            text = format_opp(opp)
            log(text.replace("\n", " | "))
            if token and chat_id:
                telegram_msg(token, chat_id, text)

    feeds = build_feeds(exchanges, symbols, on_bbo, logger=log)
    if not feeds:
        raise SystemExit("No valid realtime exchanges configured")

    threads = []
    for feed in feeds:
        t = threading.Thread(target=feed.run_forever, name=f"ws-{feed.name}", daemon=True)
        t.start(); threads.append(t)
        log(f"[REALTIME] started {feed.name}: {len(symbols)} SPOT symbols")

    try:
        while True:
            time.sleep(5)
            alive = sum(1 for t in threads if t.is_alive())
            if alive != len(threads):
                log(f"[REALTIME] feeds alive {alive}/{len(threads)}; dead feeds self-reconnect")
    except KeyboardInterrupt:
        for feed in feeds: feed.stop()
        log("[REALTIME] stopping")


if __name__ == "__main__":
    main()
