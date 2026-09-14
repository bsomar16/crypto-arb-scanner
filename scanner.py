#!/usr/bin/env python3
"""Crypto signal bot orchestration.
Modes: arb | daily | buy | price | backtest | portfolio | check | report | all
"""

import os
import sys
import time
import concurrent.futures
from argparse import ArgumentParser
from datetime import datetime, timezone
from statistics import median

from botutil import (esc, telegram_msg, load_json, save_json, fmt_price,
                     env_float, env_int, log, set_json_logs)
import markets
from markets import (EXCHANGES, fetch_exchange, fetch_binance_24h, crypto_quote,
                     volume_spike, coin_status, yahoo_chart,
                     calc_net, taker_fee, depth_estimate, currency_name,
                     names_diverge, API_PRIVATE_STATUS)
from signals import daily_indicators, score_daily, analyze_coin_daily, intraday_signal, rating
import sentiment
import portfolio as portfolio_mod
import alerts as alerts_mod
import backtest as backtest_mod
import traps as traps_mod
import store as store_mod
import positions as positions_mod

MIN_EXCHANGES = 4
SPREAD_ALERT_PCT = 8.0
MAX_ALERTS_PER_RUN = 10
TRAP_EXPIRY_DAYS = 7.0
TRAP_FILE = "traps.json"
STATE_DIR = "state"

DEFAULTS = {
    "timezone_label": "UTC",
    "daily_hour_utc": 8,
    "daily_minute_utc": 0,
    "daily_top_n": 20,
    "daily_scan_top": 80,
    "min_daily_qv": 1500000,
    "buy_scan_top_n": 60,
    "buy_min_vol": 5000000,
    "buy_min_vol_x": 1.25,
    "buy_interval": "1h",
    "buy_fast_top_n": 120,
    "buy_fast_top_n_shown": 10,
    "buy_fast_min_hour_vol": 300000,
    "buy_fast_min_vol_x": 1.8,
    "daily_news_top": 8,
    "watchlist": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX",
                  "LINK", "TON", "TRX", "DOT", "LTC", "MATIC", "PEPE", "FET",
                  "APT", "ARB", "OP", "INJ"],
    "holdings": [],
    "stocks": [{"symbol": "NVDA", "market": "NASDAQ"},
               {"symbol": "COMI.CA", "market": "EGX"},
               {"symbol": "TSLA", "market": "NASDAQ"}],
    "price_alerts": [],
    "backtest_symbols": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"],
    "min_exchanges": MIN_EXCHANGES,
    "spread_alert_pct": SPREAD_ALERT_PCT,
    "max_alerts_per_run": MAX_ALERTS_PER_RUN,
    "trap_expiry_days": TRAP_EXPIRY_DAYS,
    "stoploss_pct": 0.05,
    "tp1_pct": 0.05,
    "tp2_pct": 0.10,
    "tp3_pct": 0.20,
    "position_expiry_days": 14,
    "max_open_positions": 40,
}


def load_cfg():
    d = load_json("config.json", {}) or {}
    out = dict(DEFAULTS)
    for k, v in d.items():
        out[k] = v
    return out


def now_s():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def arb_limits(cfg):
    """Env-overridable thresholds for the arb scanner."""
    return {
        "min_ex": env_int("MIN_EXCHANGES", cfg.get("min_exchanges", MIN_EXCHANGES)),
        "spread": env_float("SPREAD_ALERT_PCT", cfg.get("spread_alert_pct",
                                                        SPREAD_ALERT_PCT)),
        "max_alerts": env_int("MAX_ALERTS_PER_RUN", cfg.get("max_alerts_per_run",
                                                            MAX_ALERTS_PER_RUN)),
        "trap_days": env_float("TRAP_EXPIRY_DAYS", cfg.get("trap_expiry_days",
                                                           TRAP_EXPIRY_DAYS)),
    }


# ────────────────────────── ARB SCANNER ──────────────────────────

def fmt_dollar(v):
    v = float(v or 0)
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.1f}k"
    return f"{v:.0f}"


def dw_status_line(ex, st):
    """Deposit/withdraw status for one leg: 'D/W Active (net)' or 'Private API'."""
    info = (st or {}).get(ex)
    if not info or (info.get("dep") is None and info.get("wd") is None):
        return "\U0001f512 Private API"
    nets = info.get("net") or []
    net_s = f" ({nets[0]})" if nets and nets[0] else ""
    dep, wd = info.get("dep"), info.get("wd")
    if dep and wd:
        return f"\u2705 D/W Active{net_s}"
    sides = ("D off" if not dep else "D on")
    sides += " \u00b7 " + ("W off" if not wd else "W on")
    return f"\u26a0\ufe0f {sides}{net_s}"


def _alert_block(r, idx, total, net):
    """Per-alert body: BUY & SELL legs only (price, depth, D/W status)."""
    lines = ["", f"\U0001f6a8 <b>ARB ALERT: {esc(r['coin'])} (+{net:.1f}%)</b>",
             f"\u23f1\ufe0f Run: {idx} of {total}", ""]
    if not total:
        return lines
    st = coin_status(r["coin"])
    d1 = (depth_estimate(r["low_ex"], r["coin"]) or {}).get("full_usd", 0)
    d2 = (depth_estimate(r["high_ex"], r["coin"]) or {}).get("full_usd", 0)
    lines += [f"\U0001f7e2 BUY: {r['low_ex']}",
              f"\u2022 Price: {fmt_price(r['low'])}",
              f"\u2022 Depth: ~${fmt_dollar(d1)}",
              f"\u2022 Status: {dw_status_line(r['low_ex'], st)}",
              "",
              f"\U0001f534 SELL: {r['high_ex']}",
              f"\u2022 Price: {fmt_price(r['high'])}",
              f"\u2022 Depth: ~${fmt_dollar(d2)}",
              f"\u2022 Status: {dw_status_line(r['high_ex'], st)}"]
    return lines


def run_arb(token, chat_id):
    cfg = load_cfg()
    lim = arb_limits(cfg)

    try:
        positions_mod.check_positions(token, chat_id, cfg)
    except Exception as e:
        log("ARB", "positions check error:", e)

    traps = traps_mod.load_traps(TRAP_FILE, lim["trap_days"])

    maps = {}
    offline = []
    for ex in EXCHANGES:
        maps[ex] = fetch_exchange(ex)
        log(ex, f"{len(maps[ex])} pairs")
        if not maps[ex]:
            offline.append(ex)

    counts = {}
    for ex, m in maps.items():
        for c in m:
            counts[c] = counts.get(c, 0) + 1
    universe = [c for c, n in counts.items() if n >= lim["min_ex"]]

    new_alerts, all_flagged = [], []
    for c in sorted(universe):
        prices = {ex: p for ex, m in maps.items() if c in m and (p := m[c]) > 0}
        if len(prices) < lim["min_ex"]:
            continue
        vals = list(prices.values())
        hi, lo = max(vals), min(vals)
        hi_ex, lo_ex = max(prices, key=prices.get), min(prices, key=prices.get)
        spread = (hi - lo) / lo * 100
        net = calc_net(hi, lo, hi_ex, lo_ex)
        if net >= lim["spread"]:
            all_flagged.append(c)
            if c in traps:
                continue
            if len(new_alerts) >= lim["max_alerts"]:
                continue
            new_alerts.append({"coin": c, "spread": spread, "net": net,
                               "low": lo, "low_ex": lo_ex,
                               "high": hi, "high_ex": hi_ex,
                               "median": median(vals)})
    new_alerts.sort(key=lambda r: r["net"], reverse=True)

    traps_mod.save_traps(TRAP_FILE, traps_mod.mark_flagged(traps, all_flagged))

    limited = len(all_flagged) > lim["max_alerts"]
    for r in new_alerts:
        try:
            store_mod.spread_log(r["coin"], r["spread"], r["net"],
                                 r["low_ex"], r["high_ex"], r["low"], r["high"],
                                 r["median"])
        except Exception as e:
            log("ARB", "spread_log error:", e)

    lines = [f"\U0001f4ca <b>ARB SCAN</b> \u00b7 {now_s()}",
             f"\U0001f310 {len(universe)} coins \u00b7 {len(EXCHANGES)} exchanges"
             f" \u00b7 {len(EXCHANGES) - len(offline)}/{len(EXCHANGES)} feeds"]

    if not new_alerts:
        log(f"[ARB] {len(new_alerts)} alerts, 0 gainers")
        return False

    lines.append("")
    label = f"\U0001f6a8 <b>NEW ALERTS ({len(new_alerts)})</b>"
    if limited:
        label += f" \u00b7 max {lim['max_alerts']}/run"
    lines.append(label)
    total = len(new_alerts)
    for i, r in enumerate(new_alerts, 1):
        lines.extend(_alert_block(r, i, total, r["net"]))
    lines.append("\u2501" * 18)

    movers = fetch_binance_24h()
    gainers = []
    if movers:
        for t in movers:
            s = t["symbol"]
            if not (s.endswith("USDT") and s != "USDTUSDT"):
                continue
            b = s[:-4]
            if "USDT" in b or "FDUSD" in b or "BUSD" in b:
                continue
            try:
                q = float(t["quoteVolume"])
                ch = float(t["priceChangePercent"])
                if q >= 500000 and ch > 0:
                    gainers.append([b, ch, q])
            except (ValueError, KeyError):
                continue
        gainers.sort(key=lambda r: r[1], reverse=True)
        if gainers:
            lines.append("")
            lines.append("\U0001f4c8 <b>TOP GAINERS 24h</b> (BN)")
            for i, (b, ch, q) in enumerate(gainers[:5], 1):
                spike = volume_spike(b)
                v = f" \u00b7 <b>vol x{spike:.1f}</b>" if spike else ""
                lines.append(f"   {i}. {b}  +{ch:.1f}%{v}")

    if offline:
        lines.append("")
        lines.append(f"\u26a0\ufe0f offline feeds: {', '.join(offline)}")

    log(f"[ARB] {len(new_alerts)} alerts, {len(gainers)} gainers")
    telegram_msg(token, chat_id, "\n".join(lines))
    return True


# ────────────────────────── INTRADAY BUY SIGNALS ──────────────────────────

def run_buy(token, chat_id):
    cfg = load_cfg()
    interval = cfg.get("buy_interval", "1h")
    fast_interval = "15m"
    fast_top = int(cfg.get("buy_fast_top_n", 120))
    fast_hour_vol = int(cfg.get("buy_fast_min_hour_vol", 300000))
    fast_vol_x = float(cfg.get("buy_fast_min_vol_x", 1.8))
    base_floor = int(cfg.get("buy_min_vol", 5000000)) * 0.1

    t24 = fetch_binance_24h()
    q = crypto_quote(t24)
    chg = {}
    for x in t24:
        s = x.get("symbol", "")
        if s.endswith("USDT") and s != "USDTUSDT":
            b = s[:-4]
            try:
                chg[b] = float(x.get("priceChangePercent", 0))
            except (ValueError, TypeError):
                pass

    rank = sorted(q.items(), key=lambda kv: -kv[1])
    cands = [sym for sym, _ in rank[:cfg.get("buy_scan_top_n", 60)]]

    star = set(str(w).upper() for w in cfg.get("watchlist", []))
    star |= set(str(h.get("symbol", "")).upper() for h in cfg.get("holdings", [])
                if h.get("symbol"))
    for e in sorted(star - set(cands)):
        if q.get(e, 0) >= base_floor * 2:
            cands.append(e)

    fast_cands = [sym for sym, _ in rank[:fast_top] if sym not in cands]
    for e in sorted(star - set(cands) - set(fast_cands)):
        if q.get(e, 0) >= base_floor:
            fast_cands.append(e)

    fired = load_json("state/fired_signals.json", {}) or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updates = {}

    def scan(sym, interval, min_hour_vol, min_vol_x):
        s = intraday_signal(sym, interval=interval, min_vol_x=min_vol_x,
                            min_hour_vol=min_hour_vol, chg24=chg.get(sym))
        if not s:
            return None
        key = f"{sym}|{interval}"
        rec = fired.get(key, {})
        if rec.get("date") == today and rec.get("entry"):
            if abs(s["price"] - rec["entry"]) / rec["entry"] < 0.035:
                return None
        updates[key] = {"date": today, "entry": s["price"]}
        s["star"] = sym in star
        return s

    tasks = ([(interval, sym, 0, float(cfg.get("buy_min_vol_x", 1.25)))
              for sym in cands] +
             [(fast_interval, sym, fast_hour_vol, fast_vol_x)
              for sym in fast_cands])
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        hits = [r for r in ex.map(lambda t: scan(t[0], t[1], t[2], t[3]),
                                  tasks) if r]

    confirmed = [r for r in hits if r["interval"] == interval]
    known = {r["coin"] for r in confirmed}
    early = [r for r in hits if r["interval"] == fast_interval
             and r["coin"] not in known]
    early.sort(key=lambda r: (-r["st"], -r["vol_x"]))
    early = early[:cfg.get("buy_fast_top_n_shown", 10)]
    confirmed.sort(key=lambda r: -r["st"])
    strong = [r for r in confirmed if r["st"] >= 5 and r["vol_x"] >= 1.5]
    normal = [r for r in confirmed if r["st"] < 5 or r["vol_x"] < 1.5]
    all_setups = confirmed + early
    fired_alerts, changed = alerts_mod.check_price_alerts(cfg)

    if not (all_setups or fired_alerts):
        log("[BUY] no new signals")
        return False

    for k, v in updates.items():
        fired[k] = v
    if updates:
        save_json("state/fired_signals.json", fired)

    opened = 0
    try:
        opened = positions_mod.open_picks(all_setups, cfg, source="buy")
    except Exception as e:
        log("BUY", "positions open error:", e)

    extra = f" \u00b7 \U0001f3af {opened} suivis" if opened else ""
    lines = [f"\U0001f680 <b>BUY SIGNALS</b> \u00b7 {now_s()}",
             f"\U0001f310 Binance {interval}+{fast_interval}"
             f" \u00b7 {len(tasks)} coins scanned"
             f" \u00b7 {len(all_setups)} new setups{extra}", ""]

    def rows(bucket, head):
        if not bucket:
            return
        lines.append(head)
        for i, r in enumerate(bucket, 1):
            sflag = " \u2b50" if r.get("star") else ""
            hv = f"  \U0001f4b0${r['hour_vol']/1e6:.1f}M/h" \
                if r.get("hour_vol") else ""
            lines.append(f"{i:>2}. <b>{esc(r['coin'])}</b>{sflag}  "
                         f"{fmt_price(r['price'])}  RSI {r['rsi']:.0f}  "
                         f"{r['chg24']:+.1f}%  vol\u00d7{r['vol_x']:.1f}{hv}")
            flags = []
            if r.get("e9_e21"):
                flags.append("EMA9>21")
            if r.get("macd_rising"):
                flags.append("MACD\u2191")
            extra = (" \u00b7 " + ", ".join(flags)) if flags else ""
            lines.append(f"     ENTRY {fmt_price(r['entry'])}  STOP "
                         f"{fmt_price(r['stop'])}  "
                         f"T1 {fmt_price(r['t1'])} T2 {fmt_price(r['t2'])}"
                         f" T3 {fmt_price(r['t3'])}{extra}")
        lines.append("")

    rows(early, "\U0001f7e2 <b>EARLY MOVERS 15m ({})</b>".format(len(early)))
    rows(strong, "\U0001f7e9 <b>STRONG ({})</b>".format(len(strong)))
    rows(normal, "\U0001f7e1 <b>NEW ({})</b>".format(len(normal)))

    if fired_alerts:
        lines.append("\U0001f514 <b>PRICE ALERTS</b>")
        lines.extend(fired_alerts[:6])
        lines.append("")

    lines.append("\u2501" * 20)
    lines.append("Règle: EMA9>EMA21 + MACD \u2191 + RSI 40-72 + volume. "
                 "STOP = entrée - 1.5\u00b7ATR, T1/T2/T3 = 1R/2R/3R.")
    lines.append("\u2b50 = sur ta watchlist / portefeuille \u00b7 "
                 "Signaux seulement, vérifie avant de trader.")

    log(f"[BUY] {len(strong)} strong, {len(normal)} normal, "
        f"{len(early)} early, {len(fired_alerts)} alerts")
    telegram_msg(token, chat_id, "\n".join(lines))
    return True


# ────────────────────────── DAILY REPORT ──────────────────────────

def analyze_stock(cfg_sym):
    try:
        sym = str(cfg_sym).strip()
        c = yahoo_chart(sym, "1d", "2y")
        if not c:
            return None
        closes = [x for x in c["close"] if x]
        if len(closes) < 70:
            return None
        work = closes[:-1] if len(closes) > 70 else closes
        dc = daily_indicators(work)
        chg = (closes[-1] / closes[-2] - 1) * 100 if len(closes) > 1 else 0.0
        vols = [x for x in c.get("volume", []) if x]
        vr = (vols[-1] / (sum(vols[-11:-1]) / 10)) if len(vols) >= 11 else 1.0
        s = score_daily(dc, max(vr, 1.0), chg)
        return {"symbol": str(c.get("symbol", sym)).replace(".", ""),
                "name": c.get("chart_name", sym), "price": c["price"] or dc["close"],
                "rsi": round(dc["rsi"], 1), "score": round(s, 1),
                "rating": rating(s), "chg": round(chg, 2)}
    except Exception:
        return None


def run_daily(token, chat_id):
    cfg = load_cfg()
    fng, fngc = sentiment.fear_greed()
    btc_dom, eth_dom, total_mcap = sentiment.btc_dominance()
    fng_nudge = 0.0
    if fng is not None:
        fng_nudge = (fng - 50) / 50 * 3.0

    t24 = fetch_binance_24h()
    q = crypto_quote(t24)
    pool = [sym for sym, qv in sorted(q.items(), key=lambda kv: -kv[1])
            if qv >= cfg.get("min_daily_qv", 1500000)]
    top = pool[:cfg.get("daily_scan_top", 80)]
    star = set(str(w).upper() for w in cfg.get("watchlist", []))
    star |= set(str(h.get("symbol", "")).upper() for h in cfg.get("holdings", [])
                if h.get("symbol"))
    for e in sorted(star - set(top)):
        if q.get(e, 0) >= int(cfg.get("min_daily_qv", 1500000)) * 0.5:
            top.append(e)

    def _scan(c):
        r = analyze_coin_daily(c, fng_nudge, vol_map=None)
        time.sleep(0.06)
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        res = [r for r in ex.map(_scan, top) if r]
    res = [r for r in res if r["rating"] in ("BUY", "STRONG BUY")]
    res.sort(key=lambda r: -r["score"])
    res = res[: cfg.get("daily_top_n", 20)]

    # log picks + open virtual positions (19:00-style daily)
    for r in res:
        try:
            store_mod.daily_log(r["coin"], r["score"], r["rating"],
                                r["price"], r["chg"], r["rsi"], r["vol_x"],
                                r.get("qv"))
        except Exception as e:
            log("DAILY", "daily_log error:", e)
    positions_mod.open_picks(res, cfg)

    news_targets = set(r["coin"] for r in res[: cfg.get("daily_news_top", 8)])
    news_targets |= {"BTC", "ETH"}
    news_targets |= set(str(h.get("symbol", "")).upper() for h in cfg.get("holdings", []))

    news = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        nn = dict(zip(news_targets, ex.map(lambda s: sentiment.news_score(s), news_targets)))
    news = {k: v for k, v in nn.items() if v and v[0] not in ("?", "No news")}

    stk_rows = [r for r in (analyze_stock(s["symbol"]) for s in cfg.get("stocks", [])) if r]
    stk_good = [r for r in stk_rows if r["rating"] in ("BUY", "STRONG BUY")]
    stk_good.sort(key=lambda r: -r["score"])

    holdings_rows = portfolio_mod.portfolio_rows(cfg.get("holdings", []))
    fired_alerts, _ = alerts_mod.check_price_alerts(cfg)

    lines = [f"\U0001f4c8 <b>DAILY REPORT</b> \u00b7 {now_s()}"]
    mkt = (f"\U0001f310 Binance \u00b7 {len(top)} coins scannés "
           f"(vol > ${cfg.get('min_daily_qv', 1500000) / 1e6:.1f}M)")
    if fng is not None:
        mkt = (f"\U0001f300 Fear&Greed <b>{fng}</b> ({esc(fngc)})"
               f" \u00b7 {mkt}")
        if btc_dom:
            mkt += f" \u00b7 BTC dom {btc_dom:.1f}%"
        if total_mcap:
            mkt += f" \u00b7 MCap ${total_mcap / 1e12:.2f}T"
    lines.append(mkt)
    lines.append("")

    sb = [r for r in res if r["rating"] == "STRONG BUY"]
    b = [r for r in res if r["rating"] == "BUY"]

    def drow(i, r, badge, star_flag):
        news_s = ""
        if r["coin"] in news:
            lbl = news[r["coin"]][0]
            icon = {"Bullish": "\U0001f44d", "Mild bullish": "\U0001f44f",
                    "Bearish": "\U0001f44e", "Mild bearish": "\U0001f53d"}.get(lbl, "\u26aa")
            news_s = f"  {icon} {esc(lbl)}"
        price = fmt_price(r["price"])
        vol = (f"\U0001f4c8 vol\u00d7{r['vol_x']:.1f}" if r["vol_x"] >= 1.2
               else f"\U0001f4c9 vol\u00d7{r['vol_x']:.1f}")
        dir3 = "\u2191" if r["chg"] >= 0 else "\u2193"
        trend = "E20\u2191 " if r["above_e20"] else ""
        macd = " MACD\u2191" if r["macd_bull"] else ""
        sfl = " \u2b50" if star_flag else ""
        lines.append(f"{i:>2}. {badge} <b>{esc(r['coin'])}</b>{sfl}  {price}  "
                     f"RSI {r['rsi']:>4}  {vol}  {dir3}{r['chg']:+.1f}%  "
                     f"score {r['score']:>3}{trend}{macd}{news_s}")

    lines.append(f"\U0001f525 <b>STRONG BUY ({len(sb)})</b>")
    for i, r in enumerate(sb, 1):
        drow(i, r, "\U0001f7e9", r["coin"] in star)
    lines.append("")
    lines.append(f"\U0001f44d <b>BUY ({len(b)})</b>")
    for i, r in enumerate(b, len(sb) + 1):
        drow(i, r, "\U0001f7e1", r["coin"] in star)

    if holdings_rows:
        pnl = portfolio_mod.format_portfolio(holdings_rows)
        lines.append("")
        lines.extend(pnl)

    if stk_good:
        lines.append("")
        lines.append(f"\U0001f4c9 <b>STOCKS BUY ({len(stk_good)})</b>")
        for r in stk_good[:5]:
            lines.append(f"   {esc(r['symbol'])} ({esc(r['name'][:24])})  "
                         f"{fmt_price(r['price'])}  RSI {r['rsi']:.0f}  "
                         f"{'BUY' if r['rating']=='BUY' else 'STRONG'} "
                         f"score {r['score']:.0f}")

    if fired_alerts:
        lines.append("")
        lines.append("\U0001f514 <b>PRICE ALERTS</b>")
        lines.extend(fired_alerts[:6])

    lines.append("")
    lines.append("\u2501" * 20)
    lines.append("RSI \u00b7 volume (1h vs 24h moy) \u00b7 MACD/EMA \u00b7 "
                 "score 0-100 \u00b7 multicoins.")
    lines.append(f"{len(sb)} strong, {len(b)} buy sur {len(pool)} coins. "
                 "Signaux seulement — vérifie avant de trader.")

    log(f"[DAILY] {len(sb)} STRONG BUY, {len(b)} BUY, {len(stk_good)} stocks")
    telegram_msg(token, chat_id, "\n".join(lines))
    return True


# ────────────────────────── OTHER MODES ──────────────────────────

def run_price(token, chat_id):
    cfg = load_cfg()
    fired, changed = alerts_mod.check_price_alerts(cfg)
    if not fired:
        log("[PRICE] no alerts")
        return False
    lines = [f"\U0001f514 <b>PRICE ALERTS</b> \u00b7 {now_s()}", ""]
    lines.extend(fired[:10])
    telegram_msg(token, chat_id, "\n".join(lines))
    return True


def run_backtest(token, chat_id):
    cfg = load_cfg()
    text = backtest_mod.run_backtest(cfg)
    telegram_msg(token, chat_id, text)
    return True


def run_portfolio(token, chat_id):
    cfg = load_cfg()
    rows = portfolio_mod.portfolio_rows(cfg.get("holdings", []))
    lines = [f"\U0001f4b0 <b>PORTFOLIO SNAPSHOT</b> \u00b7 {now_s()}"]
    lines.extend(portfolio_mod.format_portfolio(rows))
    telegram_msg(token, chat_id, "\n".join(lines))
    return True


def run_check(token, chat_id):
    """Follow-up pass: position SL/TP crossings + recent-spread recheck."""
    cfg = load_cfg()
    sent = 0
    try:
        sent = positions_mod.check_positions(token, chat_id, cfg)
    except Exception as e:
        log("CHECK", "positions error:", e)
    try:
        fu = store_mod.followup_spreads(hours_back=6,
                                        threshold=cfg.get("spread_alert_pct", 8.0))
        if fu and token and chat_id:
            lines = [f"\U0001f504 <b>SPREAD FOLLOW-UP (6h)</b> \u00b7 {now_s()}", ""]
            for coin, ts, net, still in fu[-8:]:
                flag = "encore ouvert" if still else "referm\u00e9"
                lines.append(f"   {coin}  net {net:+.2f}% \u00b7 {flag} \u00b7 alerte {ts[11:16]}")
            telegram_msg(token, chat_id, "\n".join(lines))
            sent += 1
    except Exception as e:
        log("CHECK", "followup error:", e)
    log(f"[CHECK] {sent} message(s)")
    return sent > 0


def run_report(fmt):
    """Summary of the historical log (no telegram required; tries anyway)."""
    text = store_mod.build_report_text()
    try:
        with open("state/report.html", "w", encoding="utf-8") as f:
            f.write(store_mod.build_report_html())
        log(f"[REPORT] state/report.html written (format={fmt})")
    except Exception as e:
        log("REPORT", "html write error:", e)
    print(text)
    return text


# ────────────────────────── MAIN ──────────────────────────

MODES = ["arb", "daily", "buy", "price", "backtest", "portfolio",
         "check", "report", "all"]


def main():
    ap = ArgumentParser()
    ap.add_argument("--mode", choices=MODES, default="buy")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--report-format", choices=["text", "html"], default="text")
    ap.add_argument("--json-logs", action="store_true")
    args = ap.parse_args()

    if args.json_logs:
        set_json_logs(True)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    mode = "all" if args.all else args.mode

    if mode == "report":
        run_report(args.report_format)
        return

    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        sys.exit(1)

    os.makedirs(STATE_DIR, exist_ok=True)
    if mode == "all":
        run_daily(token, chat_id)
        run_buy(token, chat_id)
    elif mode == "daily":
        run_daily(token, chat_id)
    elif mode == "buy":
        run_buy(token, chat_id)
    elif mode == "arb":
        run_arb(token, chat_id)
    elif mode == "price":
        run_price(token, chat_id)
    elif mode == "backtest":
        run_backtest(token, chat_id)
    elif mode == "portfolio":
        run_portfolio(token, chat_id)
    elif mode == "check":
        run_check(token, chat_id)


if __name__ == "__main__":
    main()