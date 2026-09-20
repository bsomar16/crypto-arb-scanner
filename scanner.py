#!/usr/bin/env python3
"""Crypto-only signal and arbitrage bot orchestration.

Modes: arb | daily | buy | price | backtest | portfolio | check | report | all
"""

import concurrent.futures
import os
import sys
import time
from argparse import ArgumentParser
from datetime import datetime, timezone
from statistics import median

from botutil import esc, telegram_msg, load_json, save_json, fmt_price, env_float, env_int, log, set_json_logs
from signal_audit import SignalAudit
from markets import (EXCHANGES, fetch_exchange, fetch_binance_24h, crypto_quote,
                     coin_status, calc_net, depth_estimate, volume_spike)
from signals import analyze_coin_daily, intraday_signal
import sentiment
import portfolio as portfolio_mod
import alerts as alerts_mod
import backtest as backtest_mod
import traps as traps_mod
import store as store_mod
import positions as positions_mod
import exposure as exposure_mod
from realtime import BinanceKlineCache
import signal_outcomes
import component_quality
import outcome_attribution
import signal_history
import market_regime
import signal_lifecycle
import validation as validation_mod
import multi_exchange
import shadow_trading

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
    "buy_scan_top_n": 100,
    "buy_min_vol": 2000000,
    "buy_intervals": ["5m", "15m", "1h"],
    "buy_scan_every_minutes": 15,
    "buy_min_vol_x": 1.15,
    "buy_fast_top_n": 150,
    "buy_fast_top_n_shown": 15,
    "buy_fast_min_hour_vol": 100000,
    "buy_fast_min_vol_x": 1.35,
    "signal_min_potential_pct": 5.0,
    "signal_max_potential_pct": 80.0,
    "signal_min_score": 55,
    "signal_min_rr": 1.5,
    "watchlist": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "TON", "TRX", "DOT", "LTC", "MATIC", "PEPE", "FET", "APT", "ARB", "OP", "INJ"],
    "holdings": [],
    "price_alerts": [],
    "backtest_symbols": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"],
    "min_exchanges": MIN_EXCHANGES,
    "spread_alert_pct": SPREAD_ALERT_PCT,
    "max_alerts_per_run": MAX_ALERTS_PER_RUN,
    "trap_expiry_days": TRAP_EXPIRY_DAYS,
    "position_expiry_days": 14,
    "max_open_positions": 5,
    "realtime_scan_interval_seconds": 60,
    "realtime_discovery_refresh_seconds": 900,
    "realtime_monitor_candidates": 25,
    "realtime_kline_enabled": True,
    "realtime_kline_intervals": ["5m", "15m", "1h"],
    "realtime_kline_max_symbols": 100,
    "realtime_kline_max_bars": 120,
    "outcome_tracking_enabled": True,
    "outcome_refresh_seconds": 300,
    "outcome_max_pending": 60,
    "outcome_horizon_bars": {"5m": 288, "15m": 96, "1h": 48},
    "market_regime_enabled": True,
    "market_regime_breadth_min_quote_volume": 1000000,
    "market_regime_breadth_limit": 100,
    "multi_exchange_enabled": True,
    "multi_exchange_max_dispersion_pct": 1.5,
    "multi_exchange_max_candidates": 30,
    # Portfolio correlation is execution/risk context, not signal generation.
    "correlation_interval": "1h",
    "correlation_lookback_bars": 72,
    "max_pairwise_correlation": 0.88,
    "correlation_workers": 8,
    "correlation_risk_hard_block": False,
    "shadow_trading_enabled": False,
    "shadow_notional_usdt": 300.0,
    "shadow_state_path": "state/shadow_trades.jsonl",
}

def load_cfg():
    d = load_json("config.json", {}) or {}
    out = dict(DEFAULTS)
    out.update(d)
    return out

def now_s():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def arb_limits(cfg):
    return {"min_ex": env_int("MIN_EXCHANGES", cfg.get("min_exchanges", MIN_EXCHANGES)), "spread": env_float("SPREAD_ALERT_PCT", cfg.get("spread_alert_pct", SPREAD_ALERT_PCT)), "max_alerts": env_int("MAX_ALERTS_PER_RUN", cfg.get("max_alerts_per_run", MAX_ALERTS_PER_RUN)), "trap_days": env_float("TRAP_EXPIRY_DAYS", cfg.get("trap_expiry_days", TRAP_EXPIRY_DAYS))}

def fmt_dollar(v):
    v = float(v or 0)
    if v >= 1e6: return f"{v / 1e6:.1f}M"
    if v >= 1e3: return f"{v / 1e3:.1f}k"
    return f"{v:.0f}"

def dw_status_line(ex, st):
    info = (st or {}).get(ex)
    if not info or (info.get("dep") is None and info.get("wd") is None): return "🔒 Private API"
    nets = info.get("net") or []
    net_s = f" ({nets[0]})" if nets and nets[0] else ""
    dep, wd = info.get("dep"), info.get("wd")
    if dep and wd: return f"✅ D/W Active{net_s}"
    return f"⚠️ {'D on' if dep else 'D off'} · {'W on' if wd else 'W off'}{net_s}"

def _alert_block(r, idx, total, net):
    st = coin_status(r["coin"])
    d1 = (depth_estimate(r["low_ex"], r["coin"]) or {}).get("full_usd", 0)
    d2 = (depth_estimate(r["high_ex"], r["coin"]) or {}).get("full_usd", 0)
    return ["", f"🚨 <b>ARB ALERT: {esc(r['coin'])} (+{net:.1f}%)</b>", f"⏱ Run: {idx} of {total}", "", f"🟢 BUY: {r['low_ex']}", f"• Price: {fmt_price(r['low'])}", f"• Depth: ~${fmt_dollar(d1)}", f"• Status: {dw_status_line(r['low_ex'], st)}", "", f"🔴 SELL: {r['high_ex']}", f"• Price: {fmt_price(r['high'])}", f"• Depth: ~${fmt_dollar(d2)}", f"• Status: {dw_status_line(r['high_ex'], st)}"]

ARB_ALLOWED_EXCHANGES = frozenset({"BINANCE", "BYBIT", "OKX", "BITGET", "MEXC", "GATE", "KUCOIN", "HTX"})
ARB_BLOCKED_EXCHANGES = frozenset({"POLONIEX"})


def run_arb(token, chat_id):
    cfg = load_cfg(); lim = arb_limits(cfg)
    # Defense-in-depth: even if a stale config/environment reintroduces an
    # exchange name, ARB must never query or report a blocked venue.
    configured = [str(ex).upper() for ex in EXCHANGES]
    blocked = [ex for ex in configured if ex in ARB_BLOCKED_EXCHANGES]
    if blocked:
        log("ARB", "blocked exchanges removed:", ",".join(blocked))
    arb_exchanges = [ex for ex in configured if ex in ARB_ALLOWED_EXCHANGES and ex not in ARB_BLOCKED_EXCHANGES]
    try: positions_mod.check_positions(token, chat_id, cfg)
    except Exception as e: log("ARB", "positions check error:", e)
    traps = traps_mod.load_traps(TRAP_FILE, lim["trap_days"]); maps, offline = {}, []
    for ex in arb_exchanges:
        maps[ex] = fetch_exchange(ex); log(ex, f"{len(maps[ex])} pairs")
        if not maps[ex]: offline.append(ex)
    counts = {}
    for m in maps.values():
        for c in m: counts[c] = counts.get(c, 0) + 1
    universe = [c for c, n in counts.items() if n >= lim["min_ex"]]; new_alerts, all_flagged = [], []
    for c in sorted(universe):
        prices = {ex: p for ex, m in maps.items() if c in m and (p := m[c]) > 0}
        if len(prices) < lim["min_ex"]: continue
        vals = list(prices.values()); hi, lo = max(vals), min(vals); hi_ex, lo_ex = max(prices, key=prices.get), min(prices, key=prices.get)
        spread = (hi - lo) / lo * 100; net = calc_net(hi, lo, hi_ex, lo_ex)
        if net >= lim["spread"]:
            all_flagged.append(c)
            if c not in traps and len(new_alerts) < lim["max_alerts"]: new_alerts.append({"coin": c, "spread": spread, "net": net, "low": lo, "low_ex": lo_ex, "high": hi, "high_ex": hi_ex, "median": median(vals)})
    new_alerts.sort(key=lambda r: r["net"], reverse=True); traps_mod.save_traps(TRAP_FILE, traps_mod.mark_flagged(traps, all_flagged))
    for r in new_alerts:
        try: store_mod.spread_log(r["coin"], r["spread"], r["net"], r["low_ex"], r["high_ex"], r["low"], r["high"], r["median"])
        except Exception as e: log("ARB", "spread_log error:", e)
    if not new_alerts: log("[ARB] no new alerts"); return False
    lines = [f"📊 <b>ARB SCAN</b> · {now_s()}", f"🌐 {len(universe)} coins · {len(arb_exchanges)} exchanges · {len(arb_exchanges)-len(offline)}/{len(arb_exchanges)} feeds", "", f"🚨 <b>NEW ALERTS ({len(new_alerts)})</b>"]
    for i, r in enumerate(new_alerts, 1): lines.extend(_alert_block(r, i, len(new_alerts), r["net"]))
    if offline: lines.extend(["", f"⚠️ offline feeds: {', '.join(offline)}"])
    telegram_msg(token, chat_id, "\n".join(lines)); return True

def _candidate_universe(cfg, q, star, t24=None):
    """Build a wide discovery universe instead of volume-rank-only candidates."""
    from discovery import discover
    results, deep = discover(t24 or [], cfg)
    discovered = [r["coin"] for r in deep]
    floor = float(cfg.get("discovery_min_quote_volume", 500000))
    for sym in sorted(star - set(discovered)):
        if q.get(sym, 0) >= floor:
            discovered.append(sym)
    return discovered, results

def _rank_trade_candidates(hits, cfg):
    """Rank qualified BUY signals by setup quality; no daily signal quota."""
    attribution = outcome_attribution.aggregate(signal_history._read(), min_samples=30)
    try:
        regime = market_regime.detect_regime(
            fetch_binance_24h(),
            breadth_min_quote_volume=float(cfg.get("market_regime_breadth_min_quote_volume", 1000000)),
            breadth_limit=int(cfg.get("market_regime_breadth_limit", 100)),
        ) if bool(cfg.get("market_regime_enabled", True)) else {"state": "UNKNOWN", "score_modifier": 0.0}
    except Exception as e:
        log("BUY", "market regime unavailable:", e)
        regime = {"state": "UNKNOWN", "score_modifier": 0.0}
    scored = []
    for r in hits:
        historical = r.get("historical_win_pct")
        sample = int(r.get("historical_sample", 0) or 0)
        rr_component = min(100.0, float(r.get("rr", 0)) / 3.0 * 100.0)
        base_quality = (
            float(r.get("entry_quality", 50.0)) * 0.28
            + float(r.get("score", 0)) * 0.24
            + float(r.get("expansion_score", 0)) * 0.20
            + rr_component * 0.10
        )
        outcome_quality = 0.0
        if historical is not None and sample >= 20:
            rates = r.get("historical_milestone_rates", {}) or {}
            early = float(rates.get("5", 0.0)) * 0.35 + float(rates.get("10", 0.0)) * 0.25 + float(rates.get("20", 0.0)) * 0.15
            mfe = min(100.0, max(0.0, float(r.get("historical_avg_mfe_pct", 0.0))) * 2.0)
            mae = min(100.0, max(0.0, -float(r.get("historical_avg_mae_pct", 0.0))) * 8.0)
            outcome_quality = float(historical) * 0.45 + early * 0.35 + mfe * 0.10 + (100.0 - mae) * 0.10
            quality = base_quality * 0.72 + outcome_quality * 0.28
        else:
            quality = base_quality / 0.82
        row = dict(r)
        component_modifier = component_quality.ranking_modifier(row, attribution, min_samples=30)
        row["component_quality_modifier"] = component_modifier
        row["market_regime"] = regime.get("state", "UNKNOWN")
        row["market_regime_volatility"] = regime.get("volatility", "UNKNOWN")
        row["market_regime_breadth_pct"] = regime.get("breadth_pct", 0.0)
        row["market_regime_modifier"] = float(regime.get("score_modifier", 0.0) or 0.0)
        row["trade_quality"] = round(max(0.0, min(100.0, quality + component_modifier + row["market_regime_modifier"])), 1)
        scored.append(row)
    scored.sort(key=lambda r: (-r["trade_quality"], -r["score"], -r["rr"], -r["potential_pct"]))
    return scored


def _interval_due(interval, now=None):
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    if interval in ("5m", "15m"):
        return True
    if interval == "1h":
        return now.minute < 15
    if interval == "4h":
        return now.minute < 15 and now.hour % 4 == 0
    if interval == "1d":
        return now.minute < 15 and now.hour == 0
    if interval == "1w":
        return now.minute < 15 and now.hour == 0 and now.weekday() == 0
    return False


def _format_hold_window(r):
    lo, hi = r.get("estimated_hold_min_hours"), r.get("estimated_hold_max_hours")
    if lo is None or hi is None:
        return "n/a"
    def fmt(hours):
        return f"{hours:.0f}h" if hours < 24 else f"{hours / 24:.0f}d"
    return f"{fmt(float(lo))}–{fmt(float(hi))}"

def _signal_message(r):
    setup = r.get("setup_type", "MOMENTUM"); kind = r.get("trade_horizon", "Scalp" if r["interval"] in ("5m", "15m") else "Medium"); reasons = ", ".join(r.get("reasons", [])[:5])
    return [f"🟢 <b>CONFIRMED BUY SIGNAL</b>", f"🚀 <b>{esc(r['coin'])}</b> · {kind} · {r['interval']}", f"Setup: <b>{setup}</b> · {r.get('trend_interval', '4h')}: {r.get('trend_4h', '?')}", "Action: <b>BUY</b>", f"Entry: <b>{fmt_price(r['entry'])}</b> · Stop: {fmt_price(r['stop'])}", f"T1: {fmt_price(r['t1'])} · T2: {fmt_price(r['t2'])} · T3: {fmt_price(r['t3'])}", f"Potential: <b>+{r['potential_pct']:.1f}%</b> · Risk: {r['risk_pct']:.2f}% · R:R {r['rr']:.2f}", f"⏱ Estimated trade time: <b>{_format_hold_window(r)}</b>", f"Score: <b>{r['score']:.0f}/100</b> · RSI {r['rsi']:.0f} · volume ×{r['vol_x']:.2f} · 24h {r['chg24']:+.1f}%", f"Why: {esc(reasons)}" if reasons else "Why: structure + momentum confirmation"]

def _dedupe_buy_signals(hits, fired, now_ts, active_coins=None, entry_change_pct=0.02, limit=None, audit=None):
    """Return one BUY notification per coin.

    A coin is silent while it has an open tracked position. After that signal
    closes, the same coin can alert again only when the new setup is materially
    different (entry moved, setup changed, or timeframe changed).
    """
    updates = {}
    fresh = []
    active_coins = {str(c).upper() for c in (active_coins or set())}
    best_by_coin = {}

    for r in hits:
        coin = str(r.get("coin", "")).upper()
        if not coin:
            continue
        current = best_by_coin.get(coin)
        if current is None or (r["score"], r["rr"], r["potential_pct"]) > (
            current["score"], current["rr"], current["potential_pct"]
        ):
            best_by_coin[coin] = r

    for coin, r in best_by_coin.items():
        # Never emit another BUY while this coin's previous signal is active.
        if coin in active_coins:
            if audit is not None:
                audit.reject("dedupe_active_position")
            continue

        old = fired.get(coin, {})
        old_entry = float(old.get("entry", 0) or 0)
        old_setup = str(old.get("setup_type", "") or "")
        old_interval = str(old.get("interval", "") or "")
        old_candle = int(old.get("candle_open_time", 0) or 0)
        old_score = float(old.get("score", 0) or 0)
        candle_changed = int(r.get("candle_open_time", 0) or 0) > old_candle if old_candle else False
        if old_entry <= 0:
            is_new_signal = True
        else:
            entry_changed = abs(float(r["entry"]) - old_entry) / old_entry >= entry_change_pct
            setup_changed = bool(old_setup and old_setup != r.get("setup_type", ""))
            interval_changed = bool(old_interval and old_interval != r.get("interval", ""))
            score_strengthened = float(r.get("score", 0) or 0) - old_score >= 5.0
            is_new_signal = entry_changed or setup_changed or interval_changed or (candle_changed and score_strengthened)

        if old and not is_new_signal:
            if audit is not None:
                audit.reject("dedupe_unchanged")
            continue

        updates[coin] = {
            "ts": now_ts,
            "entry": r["entry"],
            "score": r["score"],
            "setup_type": r.get("setup_type", ""),
            "interval": r.get("interval", ""),
            "candle_open_time": int(r.get("candle_open_time", 0) or 0),
        }
        fresh.append(r)

    fresh.sort(key=lambda r: (-r["score"], -r["rr"], -r["potential_pct"]))
    return (fresh if limit is None else fresh[:limit]), updates

def run_buy(token, chat_id, realtime_cache=None):
    cfg = load_cfg()
    intervals = [i for i in cfg.get("buy_intervals", ["5m", "15m", "1h"]) if i in ("5m", "15m", "1h")]
    if not intervals:
        intervals = ["5m", "15m", "1h"]

    t24 = fetch_binance_24h()
    q = crypto_quote(t24)
    chg = {}
    for x in t24:
        s = x.get("symbol", "")
        if s.endswith("USDT") and s != "USDTUSDT":
            try:
                chg[s[:-4]] = float(x.get("priceChangePercent", 0))
            except (ValueError, TypeError):
                pass

    star = {str(w).upper() for w in cfg.get("watchlist", [])}
    star |= {str(h.get("symbol", "")).upper() for h in cfg.get("holdings", []) if h.get("symbol")}

    # Stage 1: scan a broad liquid Binance universe with a cheap 1h discovery pass.
    cands, discovery_rows = _candidate_universe(cfg, q, star, t24=t24)
    deep_n = int(cfg.get("discovery_deep_candidates", 200))
    if realtime_cache is not None:
        cands = cands[:max(1, int(cfg.get("realtime_monitor_candidates", 25)))]
    else:
        cands = cands[:max(deep_n, len(star))]
    tasks = [(sym, interval) for interval in intervals if _interval_due(interval) for sym in cands]

    audit = SignalAudit()

    def scan(task):
        sym, interval = task
        return intraday_signal(
            sym,
            interval=interval,
            min_vol_x=None,
            min_hour_vol=float(cfg.get("buy_fast_min_hour_vol", 100000)),
            chg24=chg.get(sym),
            min_potential_pct=float(cfg.get("signal_min_potential_pct", 5.0)),
            max_potential_pct=float(cfg.get("signal_max_potential_pct", 80.0)),
            min_score=None,
            min_rr=None,
            realtime_bars=(realtime_cache.get(sym, interval) if realtime_cache is not None else None),
            cfg=cfg,
            audit=audit,
        )

    workers = int(cfg.get("deep_scan_workers", 16))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, workers)) as ex:
        hits = [r for r in ex.map(scan, tasks) if r]

    fired = load_json("state/fired_signals.json", {}) or {}
    migrated_fired = False
    if any("|" in str(k) for k in fired):
        migrated = {}
        for key, value in fired.items():
            coin = str(key).split("|", 1)[0].upper()
            if not coin or not isinstance(value, dict):
                continue
            previous = migrated.get(coin)
            if previous is None or float(value.get("ts", 0) or 0) > float(previous.get("ts", 0) or 0):
                migrated[coin] = value
        fired = migrated
        migrated_fired = True

    now_ts = datetime.now(timezone.utc).timestamp()
    active_coins = {
        str(p.get("coin", "")).upper()
        for p in positions_mod.load()
        if p.get("status") == "open" and p.get("coin")
    }
    fresh, updates = _dedupe_buy_signals(
        hits,
        fired,
        now_ts,
        active_coins=active_coins,
        entry_change_pct=float(cfg.get("buy_signal_entry_change_pct", 0.02)),
        limit=None,
        audit=audit,
    )

    # Signal generation is quality-gated, not quota-gated.
    # Portfolio exposure limits must not suppress a valid market signal.
    ranked = _rank_trade_candidates(fresh, cfg)

    # Cross-exchange SPOT price consensus is informational/ranking context.
    # Only a bounded set is queried; it never creates or suppresses signals.
    mx_limit = max(0, int(cfg.get("multi_exchange_max_candidates", 30)))
    for idx, r in enumerate(ranked):
        if not bool(cfg.get("multi_exchange_enabled", True)) or idx >= mx_limit:
            r["multi_exchange_state"] = "UNASSESSED"
            r["multi_exchange_data_available"] = False
            r["multi_exchange_modifier"] = 0.0
            continue
        try:
            mx = multi_exchange.assess(r.get("coin"), cfg=cfg)
        except Exception as e:
            log("BUY", f"multi-exchange intelligence unavailable for {r.get('coin')}:", e)
            mx = {"state": "UNKNOWN", "data_available": False, "modifier": 0.0}
        r["multi_exchange_state"] = mx.get("state", "UNKNOWN")
        r["multi_exchange_data_available"] = bool(mx.get("data_available", False))
        r["multi_exchange_count"] = int(mx.get("exchange_count", 0) or 0)
        r["multi_exchange_low_exchange"] = mx.get("low_exchange")
        r["multi_exchange_high_exchange"] = mx.get("high_exchange")
        r["multi_exchange_dispersion_pct"] = mx.get("price_dispersion_pct")
        r["multi_exchange_fee_adjusted_gap_pct"] = mx.get("fee_adjusted_gap_pct")
        r["multi_exchange_modifier"] = float(mx.get("modifier", 0.0) or 0.0)
        r["multi_exchange_reason"] = mx.get("reason", "")
        r["trade_quality"] = round(max(0.0, min(100.0, float(r.get("trade_quality", 0.0)) + r["multi_exchange_modifier"])), 1)
    ranked.sort(key=lambda r: (-r["trade_quality"], -r["score"], -r["rr"], -r["potential_pct"]))
    selected = ranked
    for r in selected:
        r["star"] = r["coin"] in star

    fired_alerts, _ = alerts_mod.check_price_alerts(cfg)
    audit_snapshot = audit.snapshot()
    audit_snapshot.update({
        "scans": len(tasks),
        "hits": len(hits),
        "fresh": len(fresh),
        "selected": len(selected),
        "deep_candidates": len(cands),
        "mtf_scans": len(tasks),
        "generated_at": now_s(),
    })
    save_json("state/buy_audit_latest.json", audit_snapshot)
    if not selected and not fired_alerts:
        log("[BUY] no new signals")
        log("[BUY AUDIT]", f"scans={len(tasks)} hits={len(hits)} " + audit.format_line())
        return False

    opened = 0
    lines = [
        f"🎯 <b>CRYPTO BUY SIGNALS</b> · {now_s()}",
        f"🔎 Wide discovery: {len(discovery_rows)} candidates · deep: {len(cands)} · {len(tasks)} MTF scans",
        f"🎯 Quality-qualified signals: {len(selected)} of {len(fresh)}",
        "",
    ]
    for i, r in enumerate(selected, 1):
        if i > 1:
            lines.append("")
        lines.extend([f"<b>#{i}</b>" + (" ⭐" if r.get("star") else "")])
        lines.extend(_signal_message(r))

    if fired_alerts:
        lines.extend(["", "🔔 <b>PRICE ALERTS</b>"])
        lines.extend(fired_alerts[:6])
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "Potential = model target, not guaranteed profit.",
        "24h volume is a liquidity filter only; BUY requires multi-factor confirmation.",
        "Signals are quality-gated; there is no daily BUY quota.",
    ])
    log(f"[BUY] {len(selected)} quality signals / {len(fresh)} qualified signals / {len(tasks)} deep scans")
    audit_snapshot = audit.snapshot()
    audit_snapshot.update({
        "scans": len(tasks),
        "hits": len(hits),
        "fresh": len(fresh),
        "selected": len(selected),
        "deep_candidates": len(cands),
        "mtf_scans": len(tasks),
        "generated_at": now_s(),
    })
    save_json("state/buy_audit_latest.json", audit_snapshot)
    log("[BUY AUDIT]", f"scans={len(tasks)} hits={len(hits)} " + audit.format_line())

    # Telegram delivery is the notification commit point. Do not persist a
    # signal as fired or open paper/shadow positions until notification succeeds.
    telegram_msg(token, chat_id, "\n".join(lines))

    if updates:
        fired.update(updates)
    if updates or migrated_fired:
        save_json("state/fired_signals.json", fired)

    for r in selected:
        try:
            signal_lifecycle.register(r)
        except Exception as e:
            log("BUY", "signal lifecycle register error:", e)

    shadow_result = {"opened": 0, "closed": 0, "outcome_source": "shadow"}
    if bool(cfg.get("shadow_trading_enabled", False)):
        try:
            prices = {str(r.get("coin", "")).upper(): float(r.get("entry", r.get("price", 0))) for r in selected}
            shadow_result = shadow_trading.run_once(
                selected,
                lambda coin: prices.get(str(coin).upper()),
                cfg,
                path=str(cfg.get("shadow_state_path", "state/shadow_trades.jsonl")),
            )
            log("SHADOW", f"opened {shadow_result.get('opened', 0)} / closed {shadow_result.get('closed', 0)}")
        except Exception as e:
            log("SHADOW", "shadow trading error:", e)

    try:
        opened = positions_mod.open_picks(selected, cfg, source="buy")
    except Exception as e:
        log("BUY", "positions open error:", e)
    return True


def run_realtime(token, chat_id):
    """Run a long-lived closed-candle confirmation monitor."""
    cfg = load_cfg()
    if not bool(cfg.get("realtime_kline_enabled", True)):
        log("[REALTIME] disabled by config")
        return False
    intervals = [i for i in cfg.get("realtime_kline_intervals", ["5m", "15m", "1h"]) if i in ("5m", "15m", "1h")]
    if not intervals:
        intervals = ["5m", "15m", "1h"]
    cache = None
    last_discovery = 0.0
    scan_interval = max(15, int(cfg.get("realtime_scan_interval_seconds", 60)))
    refresh_interval = max(300, int(cfg.get("realtime_discovery_refresh_seconds", 900)))
    try:
        while True:
            now = time.time()
            if cache is None or now - last_discovery >= refresh_interval:
                t24 = fetch_binance_24h()
                q = crypto_quote(t24)
                star = {str(w).upper() for w in cfg.get("watchlist", [])}
                star |= {str(h.get("symbol", "")).upper() for h in cfg.get("holdings", []) if h.get("symbol")}
                monitored, _ = _candidate_universe(cfg, q, star, t24=t24)
                monitored = set(monitored[:int(cfg.get("realtime_kline_max_symbols", 100))])
                if cache is not None:
                    cache.stop()
                cache = BinanceKlineCache(monitored, intervals=intervals, max_bars=int(cfg.get("realtime_kline_max_bars", 120)))
                cache.start()
                last_discovery = now
                log(f"[REALTIME] monitoring {len(monitored)} symbols across {len(intervals)} intervals")
            run_buy(token, chat_id, realtime_cache=cache)
            time.sleep(scan_interval)
    except KeyboardInterrupt:
        log("[REALTIME] stopped")
        return True
    finally:
        if cache is not None:
            cache.stop()

def run_daily(token, chat_id):
    cfg = load_cfg(); fng, fngc = sentiment.fear_greed(); btc_dom, eth_dom, total_mcap = sentiment.btc_dominance(); fng_nudge = (fng - 50) / 50 * 3.0 if fng is not None else 0.0
    t24 = fetch_binance_24h(); q = crypto_quote(t24); pool = [sym for sym, qv in sorted(q.items(), key=lambda kv: -kv[1]) if qv >= cfg.get("min_daily_qv", 1500000)]; top = pool[:int(cfg.get("daily_scan_top", 80))]
    star = {str(w).upper() for w in cfg.get("watchlist", [])}; star |= {str(h.get("symbol", "")).upper() for h in cfg.get("holdings", []) if h.get("symbol")}
    for e in sorted(star - set(top)):
        if q.get(e, 0) >= int(cfg.get("min_daily_qv", 1500000)) * 0.5: top.append(e)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex: res = [r for r in ex.map(lambda c: analyze_coin_daily(c, fng_nudge), top) if r]
    res = [r for r in res if r["rating"] in ("BUY", "STRONG BUY")]; res.sort(key=lambda r: -r["score"]); res = res[:int(cfg.get("daily_top_n", 20))]
    for r in res:
        try: store_mod.daily_log(r["coin"], r["score"], r["rating"], r["price"], r["chg"], r["rsi"], r["vol_x"], r.get("qv"))
        except Exception as e: log("DAILY", "daily_log error:", e)
    # Daily report is informational; it must not create trade entries or consume the BUY daily budget.
    news_targets = {r["coin"] for r in res[:int(cfg.get("daily_news_top", 8))]} | {"BTC", "ETH"} | {str(h.get("symbol", "")).upper() for h in cfg.get("holdings", [])}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex: nn = dict(zip(news_targets, ex.map(lambda s: sentiment.news_score(s), news_targets)))
    news = {k: v for k, v in nn.items() if v and v[0] not in ("?", "No news")}; holdings_rows = portfolio_mod.portfolio_rows(cfg.get("holdings", [])); fired_alerts, _ = alerts_mod.check_price_alerts(cfg)
    sb = [r for r in res if r["rating"] == "STRONG BUY"]; b = [r for r in res if r["rating"] == "BUY"]; lines = [f"📈 <b>CRYPTO DAILY REPORT</b> · {now_s()}"]
    mkt = f"🌐 Binance · {len(top)} crypto assets scanned (volume > ${cfg.get('min_daily_qv', 1500000)/1e6:.1f}M)"
    if fng is not None: mkt = f"🌀 Fear&Greed <b>{fng}</b> ({esc(fngc)}) · {mkt}"
    if btc_dom: mkt += f" · BTC dom {btc_dom:.1f}%"
    if total_mcap: mkt += f" · MCap ${total_mcap/1e12:.2f}T"
    lines += [mkt, ""]
    def row(i, r, badge):
        ns = ""
        if r["coin"] in news: ns = f" · {esc(news[r['coin']][0])}"
        star_flag = " ⭐" if r["coin"] in star else ""; lines.append(f"{i:>2}. {badge} <b>{esc(r['coin'])}</b>{star_flag} {fmt_price(r['price'])} RSI {r['rsi']:.0f} vol×{r['vol_x']:.1f} {r['chg']:+.1f}% score {r['score']:.0f}{ns}")
    lines.append(f"🔥 <b>STRONG BUY ({len(sb)})</b>")
    for i, r in enumerate(sb, 1): row(i, r, "🟩")
    lines.append(""); lines.append(f"👍 <b>BUY ({len(b)})</b>")
    for i, r in enumerate(b, len(sb)+1): row(i, r, "🟨")
    if holdings_rows: lines.extend(["", *portfolio_mod.format_portfolio(holdings_rows)])
    if fired_alerts: lines.extend(["", "🔔 <b>PRICE ALERTS</b>", *fired_alerts[:6]])
    lines.extend(["", "━━━━━━━━━━━━━━━━━━━━", f"{len(sb)} strong, {len(b)} buy across {len(pool)} crypto assets. Signals only — verify before trading."])
    telegram_msg(token, chat_id, "\n".join(lines)); return True

def run_price(token, chat_id):
    cfg = load_cfg(); fired, _ = alerts_mod.check_price_alerts(cfg)
    if not fired: log("[PRICE] no alerts"); return False
    telegram_msg(token, chat_id, "\n".join([f"🔔 <b>PRICE ALERTS</b> · {now_s()}", "", *fired[:10]])); return True

def run_backtest(token, chat_id):
    text = backtest_mod.run_backtest(load_cfg()); telegram_msg(token, chat_id, text); return True

def run_validate(token, chat_id):
    report = validation_mod.run(load_cfg())
    text = validation_mod.format_report(report)
    telegram_msg(token, chat_id, text)
    return True

def run_portfolio(token, chat_id):
    cfg = load_cfg(); rows = portfolio_mod.portfolio_rows(cfg.get("holdings", [])); telegram_msg(token, chat_id, "\n".join([f"💰 <b>CRYPTO PORTFOLIO</b> · {now_s()}", *portfolio_mod.format_portfolio(rows)])); return True

def run_check(token, chat_id):
    cfg = load_cfg(); sent = 0
    try: sent = positions_mod.check_positions(token, chat_id, cfg)
    except Exception as e: log("CHECK", "positions error:", e)
    try:
        fu = store_mod.followup_spreads(hours_back=6, threshold=cfg.get("spread_alert_pct", 8.0))
        if fu and token and chat_id:
            lines = [f"🔄 <b>SPREAD FOLLOW-UP (6h)</b> · {now_s()}", ""]
            for coin, ts, net, still in fu[-8:]: lines.append(f"   {coin} net {net:+.2f}% · {'open' if still else 'closed'} · alert {ts[11:16]}")
            telegram_msg(token, chat_id, "\n".join(lines)); sent += 1
    except Exception as e: log("CHECK", "followup error:", e)
    return sent > 0

def run_report(fmt):
    text = store_mod.build_report_text()
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open("state/report.html", "w", encoding="utf-8") as f: f.write(store_mod.build_report_html())
    except Exception as e: log("REPORT", "html write error:", e)
    print(text); return text

MODES = ["arb", "daily", "buy", "realtime", "price", "backtest", "validate", "portfolio", "check", "report", "all"]

def main():
    ap = ArgumentParser(); ap.add_argument("--mode", choices=MODES, default="buy"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--report-format", choices=["text", "html"], default="text"); ap.add_argument("--json-logs", action="store_true")
    args = ap.parse_args()
    if args.json_logs: set_json_logs(True)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", ""); chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    mode = "all" if args.all else args.mode
    if mode == "report": run_report(args.report_format); return
    if not token or not chat_id: print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"); sys.exit(1)
    os.makedirs(STATE_DIR, exist_ok=True)
    if mode == "all": run_daily(token, chat_id); run_buy(token, chat_id)
    elif mode == "daily": run_daily(token, chat_id)
    elif mode == "buy": run_buy(token, chat_id)
    elif mode == "realtime": run_realtime(token, chat_id)
    elif mode == "arb": run_arb(token, chat_id)
    elif mode == "price": run_price(token, chat_id)
    elif mode == "backtest": run_backtest(token, chat_id)
    elif mode == "validate": run_validate(token, chat_id)
    elif mode == "portfolio": run_portfolio(token, chat_id)
    elif mode == "check": run_check(token, chat_id)

if __name__ == "__main__": main()
