#!/usr/bin/env python3
"""Smart Money Phase 2 — Real-Time Signal Generator, Tracker & Alert Engine.

Scans genuine liquid Binance Spot crypto pairs (excluding stablecoins & tokenized stocks) for:
1. Wyckoff Accumulation & Absorption (EARLY_ACCUMULATION, PRE_BREAKOUT)
2. Bullish Trend & Healthy Pullbacks (STRONG_BULLISH_TREND, BULLISH_TREND, BULLISH_PULLBACK)
3. Resistance Breakouts & N+1 Continuations (BREAKOUT_DETECTED, CONTINUATION_CONFIRMED)
4. Post-Breakout Retest Bounces (RETEST_CONFIRMED)

Features:
- BTC Macro Regime-aware filtering & Telegram risk banner (tightened thresholds in BEAR)
- Minimum RVOL & positive institutional CVD flow floors
- Cross-scanner Double Confluence detection (Classic/SLS + Smart Money)
- Single consolidated Telegram digest (top 3 setups max, zero message flood)
- Automatic shadow TP1/TP2/SL outcome tracking & win-rate analytics (`smart_money/signal_tracker.py`)
- Closed-1h-candle deduplication (fast skip when no new hourly bar closed)

Strictly Binance Spot Read-Only. Zero live order placement. Zero trading keys required.
"""
from __future__ import annotations

import os
import sys
import time
import json
import uuid
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any, Set

from botutil import http_json, telegram_msg
from smart_money.volume_cvd import compute_volume_cvd_analytics
from smart_money.accumulation import evaluate_accumulation_structure
from smart_money.trend import evaluate_bullish_trend
from smart_money.signal_tracker import (
    SmartMoneySignalTracker,
    format_outcome_telegram_update,
    TRACKED_SIGNALS_PATH,
    OUTCOMES_JOURNAL_PATH,
)
from smart_money.breakout import (
    extract_resistance_pivots,
    cluster_resistance_pivots,
    select_primary_resistance_cluster,
    evaluate_breakout_gates,
    calculate_breakout_score,
    evaluate_pre_breakout_readiness,
    evaluate_retest_candle,
    classify_macro_regime,
    _atr_series,
    RetestState,
)

BINANCE_DATA_URL = "https://data-api.binance.vision"

# Stablecoins, fiat-pegged, synthetic USD, and commodity/wrapped tickers excluded from scanning
EXCLUDED_SYMBOLS = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT", "BUSDUSDT",
    "EURUSDT", "AEURUSDT", "EURIUSDT", "GBPUSDT", "AUDUSDT", "BRLUSDT",
    "TRYUSDT", "USD1USDT", "BFUSDUSDT", "USDEUSDT", "XUSDUSDT", "RLUSDUSDT",
    "RWUSDUSDT", "PYUSDUSDT", "GUSDUSDT", "SUSDUSDT", "FRAXUSDT", "LUSDUSDT",
    "PAXGUSDT", "XAUTUSDT", "WBTCUSDT", "WBETHUSDT", "BNSOLUSDT", "STETHUSDT",
}

# Verified genuine crypto base assets on Binance whose symbol ends in 'B'.
# All other 3+ char *BUSDT tickers (NVDAB, TSLAB, GOOGLB, SOXLB, MUB, SPCXB, SNXXB, etc.)
# are Binance tokenized equities/ETFs that trade with equity hours and distorted crypto volume.
REAL_CRYPTO_ENDING_IN_B = {
    "AMB", "ARB", "BB", "BNB", "CKB", "DGB", "MOB", "PHB", "SHIB", "TRB", "VIB", "YB",
}

DEFAULT_WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT", "NEARUSDT",
    "SUIUSDT", "TAOUSDT", "TRXUSDT", "INJUSDT", "DOTUSDT",
    "APTUSDT", "ATOMUSDT", "RENDERUSDT", "FETUSDT", "LTCUSDT",
]

SIGNALS_JOURNAL_PATH = os.path.join("state", "smart_money_signals.jsonl")
SCANNER_STATE_PATH = os.path.join("state", "smart_money_scanner_state.json")
CLASSIC_FIRED_SIGNALS_PATH = os.path.join("state", "fired_signals.json")

# Signal priority hierarchy (higher number = stronger / later lifecycle stage)
SIGNAL_PRIORITY: Dict[str, int] = {
    "EARLY_ACCUMULATION": 1,
    "BULLISH_PULLBACK": 2,
    "BULLISH_TREND": 3,
    "PRE_BREAKOUT": 4,
    "STRONG_BULLISH_TREND": 5,
    "BREAKOUT_DETECTED": 6,
    "RETEST_CONFIRMED": 7,
    "CONTINUATION_CONFIRMED": 8,
}

# Cooldown hours per signal type before re-alerting the exact same (symbol, signal_type)
SIGNAL_COOLDOWN_HOURS: Dict[str, float] = {
    "EARLY_ACCUMULATION": 6.0,
    "PRE_BREAKOUT": 4.0,
    "BULLISH_PULLBACK": 4.0,
    "BULLISH_TREND": 6.0,
    "STRONG_BULLISH_TREND": 4.0,
    "BREAKOUT_DETECTED": 3.0,
    "RETEST_CONFIRMED": 2.0,
    "CONTINUATION_CONFIRMED": 2.0,
}


def is_tokenized_equity(symbol_or_coin: str) -> bool:
    """Returns True if ticker is a Binance tokenized equity/ETF (e.g. NVDABUSDT, SOXLB, MUBUSDT)."""
    s = str(symbol_or_coin or "").upper().strip()
    base = s[:-4] if s.endswith("USDT") else s
    if len(base) >= 3 and base.endswith("B") and base not in REAL_CRYPTO_ENDING_IN_B:
        return True
    return False


def fmt_price(price: float) -> str:
    """Smart adaptive price formatting for BTC down to micro-cap memecoins."""
    if price <= 0:
        return "$0.00"
    if price >= 1000:
        return f"${price:,.2f}"
    if price >= 10:
        return f"${price:,.3f}"
    if price >= 0.1:
        return f"${price:,.4f}"
    if price >= 0.001:
        return f"${price:,.5f}"
    return f"${price:,.7f}"


def fetch_klines(symbol: str, interval: str = "1h", limit: int = 100) -> List[Any]:
    """Fetches klines from Binance Vision public read-only API."""
    url = f"{BINANCE_DATA_URL}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        data = http_json(url, timeout=12)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_orderbook(symbol: str, limit: int = 50) -> Optional[Dict[str, Any]]:
    """Fetches public Spot order book snapshot for DIR_1pct / DIR_2pct imbalance."""
    url = f"{BINANCE_DATA_URL}/api/v3/depth?symbol={symbol}&limit={limit}"
    try:
        data = http_json(url, timeout=8)
        if isinstance(data, dict) and data.get("bids") and data.get("asks"):
            return data
    except Exception:
        pass
    return None


def discover_liquid_universe(
    max_symbols: int = 60,
    min_quote_volume_usdt: float = 5_000_000.0,
    exclude_tokenized_stocks: bool = True,
) -> List[str]:
    """Dynamically discovers the top liquid genuine crypto Binance Spot USDT pairs."""
    try:
        tickers = http_json(f"{BINANCE_DATA_URL}/api/v3/ticker/24hr", timeout=15)
        if not isinstance(tickers, list):
            return list(DEFAULT_WATCHLIST)

        eligible: List[Tuple[float, str]] = []
        for t in tickers:
            sym = str(t.get("symbol", "")).upper()
            if not sym.endswith("USDT") or sym in EXCLUDED_SYMBOLS:
                continue
            base = sym[:-4]
            if base.endswith("USD") or base.startswith("USD"):
                continue
            if exclude_tokenized_stocks and is_tokenized_equity(sym):
                continue
            try:
                qv = float(t.get("quoteVolume", 0.0))
                high = float(t.get("highPrice", 0.0))
                low = float(t.get("lowPrice", 0.0))
            except (ValueError, TypeError):
                continue
            if qv < min_quote_volume_usdt or low <= 0:
                continue
            # Skip ultra-flat pegged assets (< 0.4% 24h high-low range)
            if ((high - low) / low) * 100.0 < 0.4:
                continue
            eligible.append((qv, sym))

        eligible.sort(key=lambda x: x[0], reverse=True)
        discovered = [sym for _, sym in eligible[:max_symbols]]
        return discovered if discovered else list(DEFAULT_WATCHLIST)
    except Exception:
        return list(DEFAULT_WATCHLIST)


def load_recent_classic_coins(
    fired_path: str = CLASSIC_FIRED_SIGNALS_PATH,
    lookback_hours: float = 6.0,
    now_ts: Optional[float] = None,
) -> Set[str]:
    """Reads state/fired_signals.json (read-only) to detect Classic/SLS + Smart Money double confluence."""
    if not os.path.exists(fired_path):
        return set()
    now = now_ts if now_ts is not None else time.time()
    cutoff = now - (lookback_hours * 3600.0)
    recent: Set[str] = set()
    try:
        with open(fired_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, val in data.items():
                if not isinstance(val, dict):
                    continue
                ts = float(val.get("ts", 0) or 0)
                if ts >= cutoff:
                    coin = str(key).split("|", 1)[0].upper()
                    if coin:
                        recent.add(f"{coin}USDT" if not coin.endswith("USDT") else coin)
    except Exception:
        pass
    return recent


def _regime_banner(macro_regime: str) -> str:
    reg = str(macro_regime or "SIDEWAYS").upper()
    if reg == "BULL":
        return "🟢 <b>BTC Macro Regime: BULL</b>"
    if reg == "BEAR":
        return "⚠️ <b>BTC Macro Regime: BEAR</b> <i>(Elevated Risk — Tightened Filters, Reduce Size)</i>"
    return f"🟡 <b>BTC Macro Regime: {reg}</b>"


def format_telegram_alert(sig: Dict[str, Any]) -> str:
    """Formats a single high-impact HTML alert block for Telegram."""
    sym = sig["symbol"].replace("USDT", "/USDT")
    stype = sig["signal_type"]
    entry = sig["entry_price"]
    sl = sig["stop_loss"]
    sl_pct = sig["sl_distance_pct"]
    tp1 = sig["tp1"]
    tp1_pct = sig["tp1_gain_pct"]
    tp2 = sig["tp2"]
    tp2_pct = sig["tp2_gain_pct"]
    accum = sig.get("accumulation_score", 0.0)
    trend_score = sig.get("trend_score", 0.0)
    b_score = sig.get("breakout_score", 0.0)
    rvol = sig.get("rvol", 1.0)
    delta = sig.get("normalized_delta", 0.0)
    cvd_slope = sig.get("cvd_slope", 0.0)
    r_level = sig.get("resistance_level", 0.0)
    dir_1pct = sig.get("dir_1pct", 0.5)
    ob_status = sig.get("orderbook_status", "UNASSESSED")
    htf_aligned = sig.get("htf_aligned")
    adx14 = sig.get("adx14", 0.0)
    rsi14 = sig.get("rsi14", 50.0)
    reasons = sig.get("reasons", [])
    double_conf = bool(sig.get("double_confluence", False))

    if stype == "CONTINUATION_CONFIRMED":
        badge = "🚀 <b>BREAKOUT CONTINUATION</b>"
    elif stype == "RETEST_CONFIRMED":
        badge = "🎯 <b>RETEST BOUNCE</b>"
    elif stype == "BREAKOUT_DETECTED":
        badge = "⚡ <b>RESISTANCE BREAKOUT</b>"
    elif stype == "PRE_BREAKOUT":
        badge = "🔥 <b>PRE-BREAKOUT ACCUMULATION</b>"
    elif stype == "EARLY_ACCUMULATION":
        badge = "📦 <b>WYCKOFF ACCUMULATION</b>"
    elif stype == "STRONG_BULLISH_TREND":
        if sig.get("accumulation_class") in ("EARLY_ACCUMULATION", "PRE_BREAKOUT"):
            badge = "🔥📈 <b>ACCUMULATION + STRONG BULLISH TREND</b>"
        else:
            badge = "📈 <b>STRONG BULLISH TREND</b>"
    elif stype == "BULLISH_PULLBACK":
        badge = "🟢 <b>BULLISH TREND PULLBACK</b>"
    else:
        badge = "📈 <b>BULLISH TREND</b>"

    lines = [
        f"{badge} · <code>{sym}</code>",
    ]
    if double_conf:
        lines.append("🔥 <b>DOUBLE CONFLUENCE:</b> Confirmed by BOTH Classic/SLS & Smart Money!")

    lines.extend([
        f"💵 Entry: <code>{fmt_price(entry)}</code> · 🛑 SL: <code>{fmt_price(sl)}</code> (<b>-{sl_pct:.2f}%</b>)",
        f"🎯 TP1 (1.5R): <code>{fmt_price(tp1)}</code> (<b>+{tp1_pct:.2f}%</b>) · TP2 (3.0R): <code>{fmt_price(tp2)}</code> (<b>+{tp2_pct:.2f}%</b>)",
    ])
    if r_level > 0 and stype in ("EARLY_ACCUMULATION", "PRE_BREAKOUT"):
        dist_r = ((r_level - entry) / entry * 100.0) if entry > 0 else 0.0
        lines.append(f"🧱 Breakout Level: <code>{fmt_price(r_level)}</code> (+{dist_r:.2f}% away)")

    analytics = (
        f"📊 Accum <b>{accum:.0f}</b> ({sig.get('accumulation_class', 'NONE')}) · "
        f"Trend <b>{trend_score:.0f}</b> (ADX {adx14:.0f}, RSI {rsi14:.0f})"
    )
    if b_score > 0:
        analytics += f" · Break <b>{b_score:.0f}</b>"
    lines.append(analytics)

    flow_line = f"💧 RVOL <b>{rvol:.2f}x</b> · CVD <b>{cvd_slope:+.3f}</b> · Delta <b>{delta * 100:+.1f}%</b>"
    if ob_status == "ASSESSED":
        flow_line += f" · Bids ±1%: <b>{dir_1pct * 100:.0f}%</b>"
    if htf_aligned is not None:
        flow_line += f" · 4h: <b>{'✅ Bull' if htf_aligned else '⚠️ Neut'}</b>"
    lines.append(flow_line)

    if reasons:
        lines.append(f"🔍 <i>{' · '.join(reasons[:2])}</i>")

    return "\n".join(lines)


def format_telegram_digest(
    signals: List[Dict[str, Any]],
    macro_regime: str,
    stats: Optional[Dict[str, Any]] = None,
) -> str:
    """Bundles up to top 3 Smart Money signals into 1 clean, readable Telegram digest."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"🧠 <b>SMART MONEY SIGNALS</b> · {now_str}",
        _regime_banner(macro_regime),
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for idx, sig in enumerate(signals, 1):
        if idx > 1:
            lines.append("")
        lines.append(f"<b>#{idx}</b> {format_telegram_alert(sig)}")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if stats and stats.get("total_closed", 0) >= 3:
        lines.append(
            f"📈 <i>Track Record ({stats['total_closed']} closed): "
            f"Win {stats['win_rate_pct']:.0f}% · TP1 Hit {stats['tp1_hit_rate_pct']:.0f}% · "
            f"PF {stats['profit_factor']:.2f}</i>"
        )
    lines.append("<i>Spot BUY signals only · TP1 = take 50% & move SL to Breakeven.</i>")
    return "\n".join(lines)


def log_signal_to_journal(sig: Dict[str, Any]) -> None:
    """Appends signal record to persistent JSONL journal."""
    os.makedirs(os.path.dirname(SIGNALS_JOURNAL_PATH), exist_ok=True)
    with open(SIGNALS_JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(sig) + "\n")


def load_scanner_state(state_path: str = SCANNER_STATE_PATH) -> Dict[str, Any]:
    """Loads persistent alert cooldown, last closed 1h candle timestamp, & active retest tracking state."""
    if not os.path.exists(state_path):
        return {"cooldowns": {}, "retest_candidates": {}, "last_scanned_1h_ms": 0}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("cooldowns", {})
            data.setdefault("retest_candidates", {})
            data.setdefault("last_scanned_1h_ms", 0)
            return data
    except Exception:
        pass
    return {"cooldowns": {}, "retest_candidates": {}, "last_scanned_1h_ms": 0}


def save_scanner_state(state: Dict[str, Any], state_path: str = SCANNER_STATE_PATH) -> None:
    """Atomically persists scanner cooldown and retest candidate state."""
    os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
    tmp_path = f"{state_path}.tmp.{uuid.uuid4().hex}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, state_path)


class SmartMoneySignalEngine:
    """Evaluates real-time market data and generates Accumulation, Bullish Trend, Breakout, and Retest signals."""

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        state_path: str = SCANNER_STATE_PATH,
        tracked_path: str = TRACKED_SIGNALS_PATH,
        outcomes_path: str = OUTCOMES_JOURNAL_PATH,
        classic_fired_path: str = CLASSIC_FIRED_SIGNALS_PATH,
        fetch_orderbook_enabled: bool = True,
        max_alerts_per_run: int = 3,
    ):
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.state_path = state_path
        self.classic_fired_path = classic_fired_path
        self.fetch_orderbook_enabled = fetch_orderbook_enabled
        self.max_alerts_per_run = max_alerts_per_run
        self.cached_btc_daily: List[Any] = []
        self.state = load_scanner_state(self.state_path)
        self.tracker = SmartMoneySignalTracker(tracked_path=tracked_path, outcomes_path=outcomes_path)

    def get_btc_macro_regime(self) -> str:
        """Fetches daily BTC candles and computes authoritative macro regime (§ 3.3)."""
        if not self.cached_btc_daily or len(self.cached_btc_daily) < 200:
            self.cached_btc_daily = fetch_klines("BTCUSDT", interval="1d", limit=220)

        if len(self.cached_btc_daily) >= 200:
            closed_btc = self.cached_btc_daily[:-1]
            reg_res = classify_macro_regime(closed_btc)
            return reg_res.regime
        return "WARMUP"

    def get_latest_closed_1h_ms(self) -> int:
        """Returns open timestamp (ms) of the latest closed 1h BTC candle."""
        klines = fetch_klines("BTCUSDT", interval="1h", limit=3)
        if klines and len(klines) >= 2:
            return int(klines[-2][0])
        return 0

    def should_emit_signal(self, symbol: str, signal_type: str, now_ts: Optional[float] = None) -> bool:
        """Checks active tracked lock, cooldown, and strict breakout upgrade rules."""
        # Never re-alert a coin while an active Smart Money signal is still open & tracked
        if symbol in self.tracker.tracked:
            return False

        now = now_ts if now_ts is not None else time.time()
        entry = self.state.get("cooldowns", {}).get(symbol)
        if not entry:
            return True

        last_ts = float(entry.get("timestamp", 0.0))
        last_type = str(entry.get("signal_type", ""))
        cooldown_sec = SIGNAL_COOLDOWN_HOURS.get(signal_type, 4.0) * 3600.0

        # Only allow cooldown bypass on a true structural breakout upgrade from Accumulation
        is_breakout_upgrade = (
            last_type in ("EARLY_ACCUMULATION", "PRE_BREAKOUT")
            and signal_type in ("PRE_BREAKOUT", "BREAKOUT_DETECTED", "CONTINUATION_CONFIRMED", "RETEST_CONFIRMED")
            and SIGNAL_PRIORITY.get(signal_type, 0) > SIGNAL_PRIORITY.get(last_type, 0)
        )
        if is_breakout_upgrade:
            return True

        return (now - last_ts) >= cooldown_sec

    def record_signal_emitted(self, symbol: str, signal_type: str, now_ts: Optional[float] = None) -> None:
        now = now_ts if now_ts is not None else time.time()
        self.state.setdefault("cooldowns", {})[symbol] = {
            "signal_type": signal_type,
            "timestamp": now,
        }

    def evaluate_symbol_data(
        self,
        symbol: str,
        klines_1h: List[Any],
        klines_4h: Optional[List[Any]] = None,
        macro_regime: str = "SIDEWAYS",
        orderbook: Optional[Dict[str, Any]] = None,
        recent_classic_coins: Optional[Set[str]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Pure analytical evaluation of a symbol from closed 1h + 4h candles."""
        if not klines_1h or len(klines_1h) < 45:
            return None, {}

        closed_1h = klines_1h[:-1]
        closed_4h = klines_4h[:-1] if (klines_4h and len(klines_4h) >= 40) else None
        n_idx = len(closed_1h) - 1
        candle_n = closed_1h[n_idx]
        bar_open_ms = int(candle_n[0])

        highs_1h = [float(k[2]) for k in closed_1h]
        lows_1h = [float(k[3]) for k in closed_1h]
        closes_1h = [float(k[4]) for k in closed_1h]
        volumes_1h = [float(k[5]) for k in closed_1h]

        atrs_1h = _atr_series(highs_1h, lows_1h, closes_1h, period=14)
        atr_ref = atrs_1h[n_idx] if atrs_1h and n_idx < len(atrs_1h) else max(closes_1h[n_idx] * 0.01, 1e-9)
        c_prev = closes_1h[n_idx - 1]
        c_n = closes_1h[n_idx]

        # 1. Phase 1.1 Volume & CVD Intelligence
        cvd_res = compute_volume_cvd_analytics(closed_1h, lookback=40)

        # 2. Phase 1.1 Wyckoff Accumulation Structure
        accum_res = evaluate_accumulation_structure(
            candles=closed_1h,
            cvd_result=cvd_res,
            lookback=40,
            orderbook=orderbook,
        )

        # 3. Bullish Trend & Momentum Structure (1h + 4h confluence)
        trend_res = evaluate_bullish_trend(
            candles=closed_1h,
            cvd_result=cvd_res,
            htf_candles=closed_4h,
        )

        # 4. Phase 2 Stage 1 Overhead Resistance Clustering
        pivots = extract_resistance_pivots(closed_1h, candidate_bar_index=n_idx, lookback=60)
        clusters = cluster_resistance_pivots(pivots, atr_ref=atr_ref, c_prev=c_prev, candles=closed_1h)
        primary_cluster = select_primary_resistance_cluster(clusters, c_prev)
        r_level = primary_cluster.r_level if primary_cluster else accum_res.range_high
        dist_to_r = ((r_level - c_n) / c_n * 100.0) if (r_level > 0 and c_n > 0) else 0.0

        # 5. Breakout Gates & Conviction Score
        s_break = 0.0
        gates_passed = False
        failed_gates: List[str] = []
        s_ready = 0.0

        if primary_cluster is not None:
            bbw_prev = (atr_ref / c_prev) * 2.0 if c_prev > 0 else 0.05
            readiness = evaluate_pre_breakout_readiness(
                s_accum=accum_res.accumulation_score,
                r_level=primary_cluster.r_level,
                c_prev=c_prev,
                atr_ref=atr_ref,
                bbw_prev=bbw_prev,
                dir_1pct=accum_res.dir_1pct if accum_res.orderbook_status == "ASSESSED" else None,
            )
            s_ready = readiness.s_ready

            gate_res = evaluate_breakout_gates(
                candle_n=candle_n,
                r_level=primary_cluster.r_level,
                atr_ref=atr_ref,
                rvol_n=cvd_res.rvol,
                z_vol_n=cvd_res.vol_zscore,
                delta_norm_n=cvd_res.delta_norm,
                tbr_n=cvd_res.taker_buy_ratio,
                prior_state="WATCHING",
                s_accum_prev=accum_res.accumulation_score,
                daily_btc_regime=macro_regime,
                data_confidence=cvd_res.data_confidence / 100.0,
            )
            gates_passed = gate_res.passed
            failed_gates = list(gate_res.failed_gates)

            score_res = calculate_breakout_score(
                candle_n=candle_n,
                r_level=primary_cluster.r_level,
                atr_ref=atr_ref,
                rvol_n=cvd_res.rvol,
                z_vol_n=cvd_res.vol_zscore,
                delta_norm_n=cvd_res.delta_norm,
                s_context=accum_res.accumulation_score,
                regime=macro_regime,
            )
            s_break = score_res.s_break

        metrics = {
            "symbol": symbol,
            "close": c_n,
            "candle_open_ms": bar_open_ms,
            "resistance": r_level,
            "dist_to_r": dist_to_r,
            "accum_score": accum_res.accumulation_score,
            "accum_class": accum_res.classification,
            "trend_score": trend_res.trend_score,
            "trend_state": trend_res.trend_state,
            "breakout_score": s_break,
            "rvol": cvd_res.rvol,
            "cvd_slope": cvd_res.cvd_slope_normalized,
            "delta_norm": cvd_res.delta_norm,
            "absorption": cvd_res.absorption_detected,
            "cvd_divergence": cvd_res.cvd_bullish_divergence,
            "dir_1pct": accum_res.dir_1pct,
            "orderbook_status": accum_res.orderbook_status,
            "adx14": trend_res.adx14,
            "rsi14": trend_res.rsi14,
            "pct_24h": trend_res.pct_24h,
            "htf_aligned": trend_res.htf_aligned,
        }

        # Regime-aware conviction thresholds (Item 3: tightened during BTC BEAR regime)
        is_bear = str(macro_regime).upper() == "BEAR"
        min_early_accum = 60.0 if is_bear else 52.0
        min_strong_trend = 78.0 if is_bear else 72.0
        min_bull_trend = 72.0 if is_bear else 62.0
        min_pullback = 66.0 if is_bear else 58.0

        # 6. Determine Highest-Priority Actionable Signal Type with RVOL & CVD Flow Floors (Item 2)
        signal_type: Optional[str] = None
        stop_loss: float = 0.0
        reasons: List[str] = []

        # Priority A: Persistent Retest Bounce Confirmation
        saved_retest = self.state.get("retest_candidates", {}).get(symbol)
        if saved_retest:
            try:
                rs = RetestState.from_dict(saved_retest)
                if bar_open_ms > rs.last_processed_open_time:
                    next_bar_idx = rs.breakout_bar_index + max(
                        1, int((bar_open_ms - rs.last_processed_open_time) // 3_600_000)
                    )
                    updated_rs = evaluate_retest_candle(
                        retest_state=rs,
                        candle_t=candle_n,
                        bar_index=next_bar_idx,
                        delta_norm_t=cvd_res.delta_norm,
                        all_retest_candles=closed_1h[-8:],
                    )
                    if updated_rs.status == "RETEST_CONFIRMED":
                        signal_type = "RETEST_CONFIRMED"
                        stop_loss = updated_rs.r_level - (0.5 * updated_rs.atr_ref)
                        reasons.append(f"Confirmed retest bounce off {fmt_price(updated_rs.r_level)} support")
                        self.state["retest_candidates"].pop(symbol, None)
                    elif updated_rs.status in ("STRUCTURE_INVALIDATED", "EXPIRED"):
                        self.state["retest_candidates"].pop(symbol, None)
                    else:
                        self.state["retest_candidates"][symbol] = updated_rs.to_dict()
            except Exception:
                self.state["retest_candidates"].pop(symbol, None)

        # Priority B: Fresh Resistance Breakout
        if signal_type is None and primary_cluster is not None and c_n > primary_cluster.r_level:
            if gates_passed and s_break >= 70.0:
                signal_type = "CONTINUATION_CONFIRMED"
                stop_loss = primary_cluster.r_level - (0.5 * atr_ref)
                reasons.append(f"Closed above resistance {fmt_price(primary_cluster.r_level)} with all 7 gates passed")
            elif s_break >= 60.0 and cvd_res.rvol >= 1.3 and cvd_res.delta_norm > 0.0:
                signal_type = "BREAKOUT_DETECTED"
                stop_loss = primary_cluster.r_level - (0.5 * atr_ref)
                reasons.append(f"Resistance breakout above {fmt_price(primary_cluster.r_level)} (RVOL {cvd_res.rvol:.2f}x)")
                self.state.setdefault("retest_candidates", {})[symbol] = RetestState(
                    symbol=symbol,
                    breakout_bar_index=0,
                    r_level=primary_cluster.r_level,
                    atr_ref=atr_ref,
                    breakout_high=highs_1h[n_idx],
                    breakout_vol=volumes_1h[n_idx],
                    last_processed_open_time=bar_open_ms,
                ).to_dict()

        # Priority C: Wyckoff Pre-Breakout Accumulation (with RVOL >= 0.70 & positive flow floor)
        if signal_type is None and accum_res.classification == "PRE_BREAKOUT":
            if cvd_res.rvol >= 0.70 and (cvd_res.cvd_slope_normalized >= 0.0 or cvd_res.absorption_detected):
                signal_type = "PRE_BREAKOUT"
                base_stop = accum_res.range_low - (0.4 * atr_ref)
                stop_loss = max(c_n - (2.8 * atr_ref), min(c_n - (1.0 * atr_ref), base_stop))
                reasons.extend(accum_res.reasons[:4])

        # Priority D: Bullish Trend / Pullback (with RVOL >= 0.85 & positive CVD slope floor)
        if signal_type is None:
            if (
                trend_res.trend_state == "STRONG_BULLISH_TREND"
                and trend_res.trend_score >= min_strong_trend
                and cvd_res.rvol >= 0.85
                and cvd_res.cvd_slope_normalized > 0.0
            ):
                signal_type = "STRONG_BULLISH_TREND"
                stop_loss = trend_res.suggested_stop
                reasons.extend(trend_res.reasons[:4])
            elif (
                trend_res.trend_state == "BULLISH_PULLBACK"
                and trend_res.trend_score >= min_pullback
                and cvd_res.rvol >= 0.65
                and (cvd_res.cvd_slope_normalized > 0.0 or cvd_res.absorption_detected or cvd_res.cvd_bullish_divergence)
            ):
                signal_type = "BULLISH_PULLBACK"
                stop_loss = trend_res.suggested_stop
                reasons.extend(trend_res.reasons[:4])
            elif (
                trend_res.trend_state == "BULLISH_TREND"
                and trend_res.trend_score >= min_bull_trend
                and cvd_res.rvol >= 0.85
                and cvd_res.cvd_slope_normalized > 0.0
            ):
                signal_type = "BULLISH_TREND"
                stop_loss = trend_res.suggested_stop
                reasons.extend(trend_res.reasons[:4])

        # Priority E: Wyckoff Early Accumulation (with RVOL >= 0.65 & positive institutional flow floor)
        if signal_type is None and accum_res.classification == "EARLY_ACCUMULATION":
            has_positive_flow = (
                cvd_res.absorption_detected
                or cvd_res.cvd_slope_normalized >= 0.02
                or (cvd_res.cvd_bullish_divergence and cvd_res.cvd_slope_normalized >= -0.02)
                or (accum_res.orderbook_status == "ASSESSED" and accum_res.dir_1pct >= 0.56 and cvd_res.cvd_slope_normalized >= 0.0)
            )
            if (
                accum_res.accumulation_score >= min_early_accum
                and cvd_res.rvol >= 0.65
                and has_positive_flow
            ):
                signal_type = "EARLY_ACCUMULATION"
                base_stop = accum_res.range_low - (0.4 * atr_ref)
                stop_loss = max(c_n - (2.8 * atr_ref), min(c_n - (1.0 * atr_ref), base_stop))
                reasons.extend(accum_res.reasons[:4])

        if signal_type is None:
            return None, metrics

        entry_price = c_n
        if stop_loss <= 0 or stop_loss >= entry_price:
            stop_loss = entry_price - (1.5 * atr_ref)
        r_dist = entry_price - stop_loss
        if r_dist <= 0:
            return None, metrics

        tp1 = entry_price + (1.5 * r_dist)
        tp2 = entry_price + (3.0 * r_dist)
        sl_dist_pct = (r_dist / entry_price) * 100.0
        tp1_gain_pct = ((tp1 - entry_price) / entry_price) * 100.0
        tp2_gain_pct = ((tp2 - entry_price) / entry_price) * 100.0

        # Cross-scanner double confluence check (Classic/SLS + Smart Money)
        double_confluence = bool(recent_classic_coins and symbol in recent_classic_coins)

        composite_rank = (
            SIGNAL_PRIORITY.get(signal_type, 1) * 15.0
            + max(s_break, accum_res.accumulation_score, trend_res.trend_score)
            + (12.0 if double_confluence else 0.0)
            + (10.0 if trend_res.htf_aligned else 0.0)
            + (8.0 if cvd_res.cvd_bullish_divergence else 0.0)
            + (8.0 if cvd_res.absorption_detected else 0.0)
        )

        sig = {
            "signal_id": str(uuid.uuid4()),
            "symbol": symbol,
            "candle_open_ms": bar_open_ms,
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "signal_type": signal_type,
            "entry_price": round(entry_price, 6),
            "stop_loss": round(stop_loss, 6),
            "sl_distance_pct": round(sl_dist_pct, 2),
            "tp1": round(tp1, 6),
            "tp1_gain_pct": round(tp1_gain_pct, 2),
            "tp2": round(tp2, 6),
            "tp2_gain_pct": round(tp2_gain_pct, 2),
            "resistance_level": round(r_level, 6),
            "breakout_score": round(s_break, 1),
            "accumulation_score": round(accum_res.accumulation_score, 1),
            "accumulation_class": accum_res.classification,
            "trend_score": round(trend_res.trend_score, 1),
            "trend_state": trend_res.trend_state,
            "readiness_score": round(s_ready, 1),
            "rvol": round(cvd_res.rvol, 2),
            "normalized_delta": round(cvd_res.delta_norm, 3),
            "cvd_slope": round(cvd_res.cvd_slope_normalized, 4),
            "absorption_detected": cvd_res.absorption_detected,
            "cvd_bullish_divergence": cvd_res.cvd_bullish_divergence,
            "dir_1pct": round(accum_res.dir_1pct, 4),
            "orderbook_status": accum_res.orderbook_status,
            "adx14": round(trend_res.adx14, 1),
            "rsi14": round(trend_res.rsi14, 1),
            "htf_aligned": trend_res.htf_aligned,
            "macro_regime": macro_regime,
            "double_confluence": double_confluence,
            "gates_passed": gates_passed,
            "failed_gates": failed_gates,
            "reasons": reasons,
            "composite_rank": round(composite_rank, 2),
        }
        return sig, metrics

    def analyze_symbol(
        self,
        symbol: str,
        macro_regime: Optional[str] = None,
        recent_classic_coins: Optional[Set[str]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Fetches live 1h + 4h candles + orderbook for a symbol and evaluates all Smart Money signals."""
        klines_1h = fetch_klines(symbol, interval="1h", limit=100)
        if not klines_1h or len(klines_1h) < 45:
            return None, {}

        klines_4h = fetch_klines(symbol, interval="4h", limit=60)
        regime = macro_regime if macro_regime is not None else self.get_btc_macro_regime()

        sig, metrics = self.evaluate_symbol_data(
            symbol, klines_1h, klines_4h, regime, orderbook=None, recent_classic_coins=recent_classic_coins
        )
        if sig is not None and self.fetch_orderbook_enabled:
            ob = fetch_orderbook(symbol, limit=50)
            if ob is not None:
                sig, metrics = self.evaluate_symbol_data(
                    symbol, klines_1h, klines_4h, regime, orderbook=ob, recent_classic_coins=recent_classic_coins
                )
        return sig, metrics

    def advance_tracked_signals(self, dry_run: bool = False) -> List[Dict[str, Any]]:
        """Checks all open shadow-tracked Smart Money signals for TP1 / TP2 / SL hits."""
        if not self.tracker.tracked:
            return []

        events = self.tracker.advance_all(lambda sym: fetch_klines(sym, interval="1h", limit=36))
        if not events:
            if not dry_run:
                self.tracker.save_tracked()
            return []

        stats = self.tracker.compute_stats()
        for ev in events:
            print(
                f"🔔 [TRACKER] {ev['symbol']} ({ev['signal_type']}) -> {ev['event_type']} "
                f"@ {fmt_price(ev['fill_price'])} (PnL: {ev.get('pnl_pct', 0.0):+.2f}%)"
            )

        if not dry_run:
            self.tracker.save_tracked()
            if self.telegram_token and self.telegram_chat_id:
                try:
                    msg = format_outcome_telegram_update(events, stats)
                    telegram_msg(self.telegram_token, self.telegram_chat_id, msg)
                    print(f"[TELEGRAM] Sent Smart Money outcome update ({len(events)} event(s))")
                except Exception as e:
                    print(f"[TELEGRAM] Failed to send outcome update: {e}")

        return events

    def dispatch_digest(
        self,
        signals: List[Dict[str, Any]],
        macro_regime: str,
        dry_run: bool = False,
    ) -> bool:
        """Dispatches 1 clean consolidated Telegram digest + journal entries + shadow tracking."""
        if not signals:
            return True

        for sig in signals:
            if not dry_run:
                log_signal_to_journal(sig)
                self.tracker.register_signal(sig, int(sig.get("candle_open_ms", 0)))

            conf_tag = " [🔥 DOUBLE CONFLUENCE]" if sig.get("double_confluence") else ""
            print("\n" + "=" * 82)
            print(f"⚡ [SMART MONEY SIGNAL] {sig['symbol']} — {sig['signal_type']}{conf_tag}")
            print(f"   Entry: {fmt_price(sig['entry_price'])} | SL: {fmt_price(sig['stop_loss'])} (-{sig['sl_distance_pct']:.2f}%)")
            print(f"   TP1:   {fmt_price(sig['tp1'])} (+{sig['tp1_gain_pct']:.2f}%) | TP2: {fmt_price(sig['tp2'])} (+{sig['tp2_gain_pct']:.2f}%)")
            print(
                f"   Accum: {sig['accumulation_score']:.1f}/100 ({sig['accumulation_class']}) | "
                f"Trend: {sig['trend_score']:.1f}/100 (ADX {sig['adx14']:.1f}) | "
                f"RVOL: {sig['rvol']:.2f}x | CVD: {sig['cvd_slope']:+.3f} | BTC: {macro_regime}"
            )
            if sig.get("reasons"):
                print(f"   Why:   {' | '.join(sig['reasons'][:3])}")
            print("=" * 82)

        if dry_run:
            return True

        self.tracker.save_tracked()

        if self.telegram_token and self.telegram_chat_id:
            try:
                stats = self.tracker.compute_stats()
                digest_text = format_telegram_digest(signals, macro_regime=macro_regime, stats=stats)
                telegram_msg(self.telegram_token, self.telegram_chat_id, digest_text)
                syms = ", ".join(f"{s['symbol']}({s['signal_type']})" for s in signals)
                print(f"[TELEGRAM] Consolidated digest sent ({len(signals)} signal(s)): {syms}")
                return True
            except Exception as e:
                print(f"[TELEGRAM] Failed to send digest: {e}")
                return False
        else:
            print("[INFO] Telegram credentials not set in env. Signals logged to terminal & journal.")
            return True

    def scan_universe(
        self,
        symbols: Optional[List[str]] = None,
        dry_run: bool = False,
        show_overview: bool = False,
        force: bool = False,
        max_workers: int = 8,
    ) -> List[Dict[str, Any]]:
        """Concurrently scans liquid crypto Binance Spot pairs and tracks active signal outcomes."""
        # Step 1: Always check open tracked signals for TP1 / TP2 / SL updates
        self.advance_tracked_signals(dry_run=dry_run)

        # Step 2: Closed 1h candle early-exit check (Item 6: avoid redundant 60-coin scans within same hour)
        latest_1h_ms = self.get_latest_closed_1h_ms()
        last_scanned_ms = int(self.state.get("last_scanned_1h_ms", 0) or 0)
        if not force and not dry_run and not show_overview and latest_1h_ms > 0 and latest_1h_ms <= last_scanned_ms:
            print(
                f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                f"No new closed 1h candle since last scan (ts={latest_1h_ms}). Skipping redundant universe scan."
            )
            return []

        target_symbols = symbols if symbols is not None else discover_liquid_universe(max_symbols=60)
        macro_regime = self.get_btc_macro_regime()
        recent_classic = load_recent_classic_coins(self.classic_fired_path, lookback_hours=6.0)
        print(
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Scanning {len(target_symbols)} liquid crypto Spot pairs "
            f"(BTC Macro Regime: {macro_regime} | Active tracked: {len(self.tracker.tracked)})..."
        )

        raw_signals: List[Dict[str, Any]] = []
        all_metrics: List[Dict[str, Any]] = []

        def _worker(sym: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
            try:
                return self.analyze_symbol(sym, macro_regime=macro_regime, recent_classic_coins=recent_classic)
            except Exception as e:
                print(f"[{sym}] Scan error: {e}")
                return None, {}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_worker, sym) for sym in target_symbols]
            for fut in as_completed(futures):
                sig, metrics = fut.result()
                if metrics:
                    all_metrics.append(metrics)
                if sig is not None:
                    raw_signals.append(sig)

        if show_overview and all_metrics:
            self._print_metrics_dashboard(all_metrics, macro_regime)

        # Apply cooldown deduplication and category-balanced selection (max 3 signals in 1 clean digest)
        eligible_signals = [
            s for s in raw_signals
            if dry_run or self.should_emit_signal(s["symbol"], s["signal_type"])
        ]

        breakout_bucket = sorted(
            [s for s in eligible_signals if s["signal_type"] in ("CONTINUATION_CONFIRMED", "RETEST_CONFIRMED", "BREAKOUT_DETECTED")],
            key=lambda s: s["composite_rank"],
            reverse=True,
        )
        accum_bucket = sorted(
            [s for s in eligible_signals if s["signal_type"] in ("PRE_BREAKOUT", "EARLY_ACCUMULATION")],
            key=lambda s: s["composite_rank"],
            reverse=True,
        )
        trend_bucket = sorted(
            [s for s in eligible_signals if s["signal_type"] in ("STRONG_BULLISH_TREND", "BULLISH_PULLBACK", "BULLISH_TREND")],
            key=lambda s: s["composite_rank"],
            reverse=True,
        )

        selected: List[Dict[str, Any]] = []
        seen_syms = set()
        buckets = [breakout_bucket, accum_bucket, trend_bucket]
        while len(selected) < self.max_alerts_per_run and any(buckets):
            progressed = False
            for b in buckets:
                while b and b[0]["symbol"] in seen_syms:
                    b.pop(0)
                if b and len(selected) < self.max_alerts_per_run:
                    cand = b.pop(0)
                    seen_syms.add(cand["symbol"])
                    selected.append(cand)
                    progressed = True
            if not progressed:
                break

        if selected:
            sent_ok = self.dispatch_digest(selected, macro_regime=macro_regime, dry_run=dry_run)
            if not dry_run and sent_ok:
                for sig in selected:
                    self.record_signal_emitted(sig["symbol"], sig["signal_type"])

        if not dry_run:
            if latest_1h_ms > 0:
                self.state["last_scanned_1h_ms"] = latest_1h_ms
            save_scanner_state(self.state, self.state_path)

        print(
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Scan complete: "
            f"{len(raw_signals)} setup(s) detected, {len(selected)} signal(s) dispatched."
        )
        return selected

    def _print_metrics_dashboard(self, metrics_list: List[Dict[str, Any]], macro_regime: str) -> None:
        """Prints a clean 2-section crypto market intelligence dashboard."""
        print("\n" + "=" * 104)
        print(
            f"📊 SMART MONEY CRYPTO INTELLIGENCE — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} "
            f"| BTC Regime: {macro_regime}"
        )
        print("=" * 104)

        acc_sorted = sorted(metrics_list, key=lambda m: m["accum_score"], reverse=True)
        acc_top = [m for m in acc_sorted if m["accum_score"] >= 45.0][:10]
        print("\n📦 TOP WYCKOFF ACCUMULATION CRYPTO COINS:")
        print(f"{'Symbol':<12} | {'Close':<12} | {'Accum':<10} | {'Class':<18} | {'RVOL':<6} | {'CVD Slope':<10} | {'Div/Abs':<7} | {'Dist R'}")
        print("-" * 104)
        for m in acc_top:
            flags = ("DIV " if m["cvd_divergence"] else "") + ("ABS" if m["absorption"] else "")
            flags = flags.strip() or "-"
            dist_s = f"+{m['dist_to_r']:.2f}%" if m["dist_to_r"] > 0 else "ABOVE R"
            cvd_s = f"{m['cvd_slope']:+.3f}"
            print(
                f"{m['symbol']:<12} | {fmt_price(m['close']):<12} | {m['accum_score']:<5.1f}/100 | "
                f"{m['accum_class']:<18} | {m['rvol']:<4.2f}x | {cvd_s:<10} | {flags:<7} | {dist_s}"
            )

        trend_sorted = sorted(metrics_list, key=lambda m: m["trend_score"], reverse=True)
        trend_top = [m for m in trend_sorted if m["trend_score"] >= 50.0][:10]
        print("\n📈 TOP BULLISH TRENDING CRYPTO COINS:")
        print(f"{'Symbol':<12} | {'Close':<12} | {'Trend':<10} | {'State':<20} | {'ADX14':<6} | {'RVOL':<6} | {'24h %':<8} | {'4h'}")
        print("-" * 104)
        for m in trend_top:
            htf_s = "BULL" if m["htf_aligned"] is True else ("NEUT" if m["htf_aligned"] is None else "BEAR")
            pct_s = f"{m['pct_24h']:+.2f}%"
            print(
                f"{m['symbol']:<12} | {fmt_price(m['close']):<12} | {m['trend_score']:<5.1f}/100 | "
                f"{m['trend_state']:<20} | {m['adx14']:<6.1f} | {m['rvol']:<4.2f}x | {pct_s:<8} | {htf_s}"
            )
        print("=" * 104 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Smart Money Signal Scanner (Accumulation + Bullish Trend + Breakouts)")
    parser.add_argument("--scan", action="store_true", help="Run scan across top liquid crypto Binance Spot pairs")
    parser.add_argument("--overview", action="store_true", help="Display full Smart Money Intelligence Dashboard")
    parser.add_argument("--stats", action="store_true", help="Display Smart Money tracked signal win-rate & Profit Factor stats")
    parser.add_argument("--loop", action="store_true", help="Run continuous scan loop")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between loop scans (default: 300s)")
    parser.add_argument("--max-symbols", type=int, default=60, help="Max top-volume USDT crypto pairs to scan (default: 60)")
    parser.add_argument("--symbol", type=str, help="Deep scan a single symbol (e.g. SOLUSDT)")
    parser.add_argument("--force", action="store_true", help="Force full scan even if closed 1h candle timestamp is unchanged")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and print signals without Telegram or state mutation")
    parser.add_argument("--test-telegram", action="store_true", help="Send test digest alert to verify Telegram credentials")
    args = parser.parse_args()

    engine = SmartMoneySignalEngine()

    if args.stats:
        stats = engine.tracker.compute_stats()
        print(json.dumps(stats, indent=2))
        return

    if args.test_telegram:
        if not engine.telegram_token or not engine.telegram_chat_id:
            print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in environment.")
            sys.exit(1)
        test_sig = {
            "signal_id": str(uuid.uuid4()),
            "symbol": "SOLUSDT",
            "candle_open_ms": int(time.time() * 1000),
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "signal_type": "PRE_BREAKOUT",
            "entry_price": 182.50,
            "stop_loss": 176.20,
            "sl_distance_pct": 3.45,
            "tp1": 191.95,
            "tp1_gain_pct": 5.18,
            "tp2": 201.40,
            "tp2_gain_pct": 10.36,
            "resistance_level": 184.00,
            "breakout_score": 0.0,
            "accumulation_score": 78.4,
            "accumulation_class": "PRE_BREAKOUT",
            "trend_score": 74.0,
            "trend_state": "STRONG_BULLISH_TREND",
            "rvol": 1.85,
            "normalized_delta": 0.34,
            "cvd_slope": 0.142,
            "dir_1pct": 0.642,
            "orderbook_status": "ASSESSED",
            "adx14": 26.8,
            "rsi14": 61.5,
            "htf_aligned": True,
            "double_confluence": True,
            "reasons": [
                "Tight consolidation base (bandwidth 5.4%)",
                "Confirmed Wyckoff spring bullish divergence",
            ],
        }
        print("Sending test Smart Money Telegram digest...")
        telegram_msg(
            engine.telegram_token,
            engine.telegram_chat_id,
            format_telegram_digest([test_sig], macro_regime=engine.get_btc_macro_regime()),
        )
        print("Test digest sent successfully!")
        return

    if args.symbol:
        sym = args.symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"
        print(f"Performing deep Smart Money scan on {sym}...")
        regime = engine.get_btc_macro_regime()
        recent_classic = load_recent_classic_coins(engine.classic_fired_path)
        sig, metrics = engine.analyze_symbol(sym, macro_regime=regime, recent_classic_coins=recent_classic)
        if metrics:
            engine._print_metrics_dashboard([metrics], regime)
        if sig:
            engine.dispatch_digest([sig], macro_regime=regime, dry_run=args.dry_run)
        else:
            print(f"No actionable Accumulation, Bullish Trend, Breakout, or Retest signal on {sym} right now.")
        return

    if args.overview:
        symbols = discover_liquid_universe(max_symbols=args.max_symbols)
        engine.scan_universe(symbols=symbols, dry_run=True, show_overview=True, force=True)
        return

    if args.loop:
        print(f"Starting continuous Smart Money scan loop (interval: {args.interval}s)...")
        while True:
            try:
                symbols = discover_liquid_universe(max_symbols=args.max_symbols)
                engine.scan_universe(symbols=symbols, dry_run=args.dry_run, show_overview=False, force=args.force)
            except KeyboardInterrupt:
                print("\nScanner stopped by user.")
                break
            except Exception as e:
                print(f"Loop scan exception: {e}")
            time.sleep(args.interval)
    else:
        symbols = discover_liquid_universe(max_symbols=args.max_symbols)
        engine.scan_universe(symbols=symbols, dry_run=args.dry_run, show_overview=True, force=args.force)


if __name__ == "__main__":
    main()
