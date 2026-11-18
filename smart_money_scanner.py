#!/usr/bin/env python3
"""Smart Money Phase 2 — Real-Time Signal Generator & Alert Engine.

Scans Binance Spot public market data across the top liquid USDT universe for:
1. Wyckoff Accumulation & Absorption (EARLY_ACCUMULATION, PRE_BREAKOUT)
2. Bullish Trend & Healthy Pullbacks (STRONG_BULLISH_TREND, BULLISH_TREND, BULLISH_PULLBACK)
3. Resistance Breakouts & N+1 Continuations (BREAKOUT_DETECTED, CONTINUATION_CONFIRMED)
4. Post-Breakout Retest Bounces (RETEST_CONFIRMED)

Dispatches actionable BUY/LONG signals with precise Entry, Stop-Loss, TP1 (+1.5R),
and TP2 (+3.0R) targets to Telegram, Terminal, and Persistent Signal History.

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
from typing import List, Dict, Optional, Tuple, Any

from botutil import http_json, telegram_msg
from smart_money.volume_cvd import compute_volume_cvd_analytics
from smart_money.accumulation import evaluate_accumulation_structure
from smart_money.trend import evaluate_bullish_trend
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

# Stablecoins, fiat-pegged, synthetic USD, and commodity-pegged tickers excluded from scanning
EXCLUDED_SYMBOLS = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT", "BUSDUSDT",
    "EURUSDT", "AEURUSDT", "EURIUSDT", "GBPUSDT", "AUDUSDT", "BRLUSDT",
    "TRYUSDT", "USD1USDT", "BFUSDUSDT", "USDEUSDT", "XUSDUSDT", "RLUSDUSDT",
    "RWUSDUSDT", "PYUSDUSDT", "GUSDUSDT", "SUSDUSDT", "FRAXUSDT", "LUSDUSDT",
    "PAXGUSDT", "XAUTUSDT", "WBTCUSDT", "WBETHUSDT", "BNSOLUSDT", "STETHUSDT",
}

DEFAULT_WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT", "NEARUSDT",
    "SUIUSDT", "TAOUSDT", "TRXUSDT", "INJUSDT", "DOTUSDT",
    "APTUSDT", "ATOMUSDT", "RENDERUSDT", "FETUSDT", "LTCUSDT",
]

SIGNALS_JOURNAL_PATH = os.path.join("state", "smart_money_signals.jsonl")
SCANNER_STATE_PATH = os.path.join("state", "smart_money_scanner_state.json")

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
) -> List[str]:
    """Dynamically discovers the top liquid Binance Spot USDT pairs by 24h volume."""
    try:
        tickers = http_json(f"{BINANCE_DATA_URL}/api/v3/ticker/24hr", timeout=15)
        if not isinstance(tickers, list):
            return list(DEFAULT_WATCHLIST)

        eligible: List[Tuple[float, str]] = []
        for t in tickers:
            sym = str(t.get("symbol", ""))
            if not sym.endswith("USDT") or sym in EXCLUDED_SYMBOLS:
                continue
            # Filter out USD-pegged tokens dynamically by name prefix/suffix
            base = sym[:-4]
            if base.endswith("USD") or base.startswith("USD"):
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


def format_telegram_alert(sig: Dict[str, Any]) -> str:
    """Formats high-impact HTML alert for Telegram across all Smart Money signal types."""
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

    if stype == "CONTINUATION_CONFIRMED":
        header = "🚀 <b>SMART MONEY BREAKOUT: CONTINUATION</b>"
        action = "Confirmed resistance breakout with strong institutional volume & delta."
    elif stype == "RETEST_CONFIRMED":
        header = "🎯 <b>SMART MONEY ENTRY: RETEST BOUNCE</b>"
        action = f"Confirmed support bounce off broken resistance ({fmt_price(r_level)})."
    elif stype == "BREAKOUT_DETECTED":
        header = "⚡ <b>SMART MONEY BREAKOUT DETECTED</b>"
        action = f"Price closed above overhead resistance ({fmt_price(r_level)}). Watch for continuation or retest."
    elif stype == "PRE_BREAKOUT":
        header = "🔥 <b>SMART MONEY ACCUMULATION: PRE-BREAKOUT</b>"
        action = f"Tight Wyckoff base coiling near resistance ({fmt_price(r_level)}). Breakout imminent."
    elif stype == "EARLY_ACCUMULATION":
        header = "📦 <b>SMART MONEY ACCUMULATION DETECTED</b>"
        action = f"Institutional base building detected below resistance ({fmt_price(r_level)})."
    elif stype == "STRONG_BULLISH_TREND":
        if sig.get("accumulation_class") in ("EARLY_ACCUMULATION", "PRE_BREAKOUT"):
            header = "🔥📈 <b>SMART MONEY: ACCUMULATION + STRONG BULLISH TREND</b>"
        else:
            header = "📈 <b>SMART MONEY STRONG BULLISH TREND</b>"
        action = "Institutional-backed uptrend (EMA stack + ADX + positive CVD flow + 4h alignment)."
    elif stype == "BULLISH_PULLBACK":
        header = "🟢 <b>SMART MONEY BULLISH TREND PULLBACK</b>"
        action = "Healthy dip to EMA20 support inside an active bullish uptrend."
    else:
        header = "📈 <b>SMART MONEY BULLISH TREND</b>"
        action = "Confirmed bullish uptrend with positive buying pressure."

    lines = [
        header,
        f"<b>Pair:</b> <code>{sym}</code> (1h/4h Binance Spot)",
        f"<b>Time:</b> {sig['timestamp_utc']}",
        "",
        f"💵 <b>Reference Entry:</b> <code>{fmt_price(entry)}</code>",
        f"🛑 <b>Stop Loss:</b> <code>{fmt_price(sl)}</code> (<b>-{sl_pct:.2f}%</b>)",
        f"🎯 <b>TP1 (1.5R):</b> <code>{fmt_price(tp1)}</code> (<b>+{tp1_pct:.2f}%</b>) <i>[50% + Move SL to BE]</i>",
        f"🎯 <b>TP2 (3.0R):</b> <code>{fmt_price(tp2)}</code> (<b>+{tp2_pct:.2f}%</b>) <i>[Remaining 50%]</i>",
    ]
    if r_level > 0 and stype in ("EARLY_ACCUMULATION", "PRE_BREAKOUT"):
        dist_r = ((r_level - entry) / entry * 100.0) if entry > 0 else 0.0
        lines.append(f"🧱 <b>Breakout Level:</b> <code>{fmt_price(r_level)}</code> (+{dist_r:.2f}% away)")

    lines.extend([
        "",
        "📊 <b>Smart Money Analytics:</b>",
        f"• Accumulation Score: <b>{accum:.1f}/100</b> ({sig.get('accumulation_class', 'NONE')})",
        f"• Bullish Trend Score: <b>{trend_score:.1f}/100</b> (ADX {adx14:.1f} · RSI {rsi14:.1f})",
    ])
    if b_score > 0:
        lines.append(f"• Breakout Conviction: <b>{b_score:.1f}/100</b>")
    lines.append(f"• Volume: <b>{rvol:.2f}x</b> · CVD Slope: <b>{cvd_slope:+.3f}</b> · Delta: <b>{delta * 100:+.1f}%</b>")
    if ob_status == "ASSESSED":
        lines.append(f"• Order Book (±1%): <b>{dir_1pct * 100:.1f}% Bids</b> vs <b>{(1 - dir_1pct) * 100:.1f}% Asks</b>")
    if htf_aligned is not None:
        htf_txt = "✅ Bullish Aligned" if htf_aligned else "⚠️ Neutral/Counter"
        lines.append(f"• 4h Higher-TF Trend: <b>{htf_txt}</b>")

    if reasons:
        lines.append("")
        lines.append("🔍 <b>Key Signals:</b>")
        for r in reasons[:4]:
            lines.append(f"  • {r}")

    lines.extend(["", f"💡 <i>{action}</i>"])
    return "\n".join(lines)


def log_signal_to_journal(sig: Dict[str, Any]) -> None:
    """Appends signal record to persistent JSONL journal."""
    os.makedirs(os.path.dirname(SIGNALS_JOURNAL_PATH), exist_ok=True)
    with open(SIGNALS_JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(sig) + "\n")


def load_scanner_state(state_path: str = SCANNER_STATE_PATH) -> Dict[str, Any]:
    """Loads persistent alert cooldown & active retest tracking state."""
    if not os.path.exists(state_path):
        return {"cooldowns": {}, "retest_candidates": {}}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("cooldowns", {})
            data.setdefault("retest_candidates", {})
            return data
    except Exception:
        pass
    return {"cooldowns": {}, "retest_candidates": {}}


def save_scanner_state(state: Dict[str, Any], state_path: str = SCANNER_STATE_PATH) -> None:
    """Atomically persists scanner cooldown and retest candidate state."""
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
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
        fetch_orderbook_enabled: bool = True,
        max_alerts_per_run: int = 5,
    ):
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.state_path = state_path
        self.fetch_orderbook_enabled = fetch_orderbook_enabled
        self.max_alerts_per_run = max_alerts_per_run
        self.cached_btc_daily: List[Any] = []
        self.state = load_scanner_state(self.state_path)

    def get_btc_macro_regime(self) -> str:
        """Fetches daily BTC candles and computes authoritative macro regime (§ 3.3)."""
        if not self.cached_btc_daily or len(self.cached_btc_daily) < 200:
            self.cached_btc_daily = fetch_klines("BTCUSDT", interval="1d", limit=220)

        if len(self.cached_btc_daily) >= 200:
            closed_btc = self.cached_btc_daily[:-1]
            reg_res = classify_macro_regime(closed_btc)
            return reg_res.regime
        return "WARMUP"

    def should_emit_signal(self, symbol: str, signal_type: str, now_ts: Optional[float] = None) -> bool:
        """Checks cooldown & upgrade logic to prevent duplicate Telegram spam."""
        now = now_ts if now_ts is not None else time.time()
        entry = self.state.get("cooldowns", {}).get(symbol)
        if not entry:
            return True

        last_ts = float(entry.get("timestamp", 0.0))
        last_type = str(entry.get("signal_type", ""))
        cooldown_sec = SIGNAL_COOLDOWN_HOURS.get(signal_type, 4.0) * 3600.0

        # Always allow immediate alert on lifecycle stage upgrade (e.g. EARLY_ACCUMULATION -> PRE_BREAKOUT -> BREAKOUT)
        if SIGNAL_PRIORITY.get(signal_type, 0) > SIGNAL_PRIORITY.get(last_type, 0):
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
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Pure analytical evaluation of a symbol from closed 1h + 4h candles.

        Returns:
            (signal_dict_or_None, metrics_dict)
        """
        if not klines_1h or len(klines_1h) < 45:
            return None, {}

        # Strictly closed candles (exclude currently forming last candle)
        closed_1h = klines_1h[:-1]
        closed_4h = klines_4h[:-1] if (klines_4h and len(klines_4h) >= 40) else None
        n_idx = len(closed_1h) - 1
        candle_n = closed_1h[n_idx]

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

        # 2. Phase 1.1 Wyckoff Accumulation Structure (with optional Order Book DIR)
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

        # 5. Breakout Gates & Conviction Score (if overhead resistance exists)
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

        # 6. Determine Highest-Priority Actionable Signal Type
        signal_type: Optional[str] = None
        stop_loss: float = 0.0
        reasons: List[str] = []

        # Priority A: Check Persistent Retest Bounce Confirmation (from prior breakout)
        saved_retest = self.state.get("retest_candidates", {}).get(symbol)
        if saved_retest:
            try:
                rs = RetestState.from_dict(saved_retest)
                bar_open_ms = int(candle_n[0])
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

        # Priority B: Fresh Resistance Breakout (Phase 2 Stage 1)
        if signal_type is None and primary_cluster is not None and c_n > primary_cluster.r_level:
            if gates_passed and s_break >= 70.0:
                signal_type = "CONTINUATION_CONFIRMED"
                stop_loss = primary_cluster.r_level - (0.5 * atr_ref)
                reasons.append(f"Closed above resistance {fmt_price(primary_cluster.r_level)} with all 7 gates passed")
            elif s_break >= 60.0 and cvd_res.rvol >= 1.3 and cvd_res.delta_norm > 0.0:
                signal_type = "BREAKOUT_DETECTED"
                stop_loss = primary_cluster.r_level - (0.5 * atr_ref)
                reasons.append(f"Resistance breakout above {fmt_price(primary_cluster.r_level)} (RVOL {cvd_res.rvol:.2f}x)")
                # Register candidate for future retest bounce monitoring
                self.state.setdefault("retest_candidates", {})[symbol] = RetestState(
                    symbol=symbol,
                    breakout_bar_index=0,
                    r_level=primary_cluster.r_level,
                    atr_ref=atr_ref,
                    breakout_high=highs_1h[n_idx],
                    breakout_vol=volumes_1h[n_idx],
                    last_processed_open_time=int(candle_n[0]),
                ).to_dict()

        # Priority C: Wyckoff Pre-Breakout Accumulation (Highest-conviction early entry)
        if signal_type is None and accum_res.classification == "PRE_BREAKOUT":
            signal_type = "PRE_BREAKOUT"
            base_stop = accum_res.range_low - (0.4 * atr_ref)
            stop_loss = max(c_n - (2.8 * atr_ref), min(c_n - (1.0 * atr_ref), base_stop))
            reasons.extend(accum_res.reasons[:4])

        # Priority D: Strong Bullish Trend / Bullish Trend / Bullish Pullback
        if signal_type is None and trend_res.trend_state in (
            "STRONG_BULLISH_TREND",
            "BULLISH_PULLBACK",
            "BULLISH_TREND",
        ):
            signal_type = trend_res.trend_state
            stop_loss = trend_res.suggested_stop
            reasons.extend(trend_res.reasons[:4])

        # Priority E: Wyckoff Early Accumulation (requires institutional footprint — CVD/Absorption/Divergence/OrderBook)
        if signal_type is None and accum_res.classification == "EARLY_ACCUMULATION":
            has_institutional_footprint = (
                cvd_res.absorption_detected
                or cvd_res.cvd_bullish_divergence
                or cvd_res.cvd_slope_normalized >= 0.02
                or (accum_res.orderbook_status == "ASSESSED" and accum_res.dir_1pct >= 0.56)
            )
            if has_institutional_footprint and accum_res.accumulation_score >= 52.0:
                signal_type = "EARLY_ACCUMULATION"
                base_stop = accum_res.range_low - (0.4 * atr_ref)
                stop_loss = max(c_n - (2.8 * atr_ref), min(c_n - (1.0 * atr_ref), base_stop))
                reasons.extend(accum_res.reasons[:4])

        if signal_type is None:
            return None, metrics

        # Sanity-check Risk/Reward levels
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

        # Composite ranking score for selecting top signals when multiple trigger
        composite_rank = (
            SIGNAL_PRIORITY.get(signal_type, 1) * 15.0
            + max(s_break, accum_res.accumulation_score, trend_res.trend_score)
            + (10.0 if trend_res.htf_aligned else 0.0)
            + (8.0 if cvd_res.cvd_bullish_divergence else 0.0)
            + (8.0 if cvd_res.absorption_detected else 0.0)
        )

        sig = {
            "signal_id": str(uuid.uuid4()),
            "symbol": symbol,
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
            "gates_passed": gates_passed,
            "failed_gates": failed_gates,
            "reasons": reasons,
            "composite_rank": round(composite_rank, 2),
        }
        return sig, metrics

    def analyze_symbol(self, symbol: str, macro_regime: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Fetches live 1h + 4h candles + orderbook for a symbol and evaluates all Smart Money signals."""
        klines_1h = fetch_klines(symbol, interval="1h", limit=100)
        if not klines_1h or len(klines_1h) < 45:
            return None, {}

        klines_4h = fetch_klines(symbol, interval="4h", limit=60)
        regime = macro_regime if macro_regime is not None else self.get_btc_macro_regime()

        # First pass without orderbook (fast), then enrich with live orderbook if candidate looks promising
        sig, metrics = self.evaluate_symbol_data(symbol, klines_1h, klines_4h, regime, orderbook=None)
        if sig is not None and self.fetch_orderbook_enabled:
            ob = fetch_orderbook(symbol, limit=50)
            if ob is not None:
                sig, metrics = self.evaluate_symbol_data(symbol, klines_1h, klines_4h, regime, orderbook=ob)
        return sig, metrics

    def dispatch_signal(self, sig: Dict[str, Any], dry_run: bool = False) -> bool:
        """Dispatches signal to persistent journal, terminal, and Telegram."""
        if not dry_run:
            log_signal_to_journal(sig)

        print("\n" + "=" * 78)
        print(f"⚡ [SMART MONEY SIGNAL] {sig['symbol']} — {sig['signal_type']}")
        print(f"   Entry: {fmt_price(sig['entry_price'])} | SL: {fmt_price(sig['stop_loss'])} (-{sig['sl_distance_pct']:.2f}%)")
        print(f"   TP1:   {fmt_price(sig['tp1'])} (+{sig['tp1_gain_pct']:.2f}%) | TP2: {fmt_price(sig['tp2'])} (+{sig['tp2_gain_pct']:.2f}%)")
        print(
            f"   Accum: {sig['accumulation_score']:.1f}/100 ({sig['accumulation_class']}) | "
            f"Trend: {sig['trend_score']:.1f}/100 (ADX {sig['adx14']:.1f}) | "
            f"RVOL: {sig['rvol']:.2f}x | CVD: {sig['cvd_slope']:+.3f}"
        )
        if sig.get("reasons"):
            print(f"   Why:   {' | '.join(sig['reasons'][:3])}")
        print("=" * 78)

        if dry_run:
            return True

        if self.telegram_token and self.telegram_chat_id:
            try:
                alert_text = format_telegram_alert(sig)
                telegram_msg(self.telegram_token, self.telegram_chat_id, alert_text)
                print(f"[TELEGRAM] Alert sent for {sig['symbol']} ({sig['signal_type']})")
                return True
            except Exception as e:
                print(f"[TELEGRAM] Failed to send alert for {sig['symbol']}: {e}")
                return False
        else:
            print("[INFO] Telegram credentials not set in env. Signal logged to terminal & journal.")
            return True

    def scan_universe(
        self,
        symbols: Optional[List[str]] = None,
        dry_run: bool = False,
        show_overview: bool = False,
        max_workers: int = 8,
    ) -> List[Dict[str, Any]]:
        """Concurrently scans the liquid Binance Spot universe for Accumulation, Bullish Trend, Breakout & Retest signals."""
        target_symbols = symbols if symbols is not None else discover_liquid_universe(max_symbols=60)
        macro_regime = self.get_btc_macro_regime()
        print(
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Scanning {len(target_symbols)} liquid Binance Spot pairs "
            f"(BTC Macro Regime: {macro_regime})..."
        )

        raw_signals: List[Dict[str, Any]] = []
        all_metrics: List[Dict[str, Any]] = []

        def _worker(sym: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
            try:
                return self.analyze_symbol(sym, macro_regime=macro_regime)
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

        # Apply cooldown deduplication first, then balance top alerts across categories
        # so Wyckoff Accumulations, Bullish Trends, and Breakouts are all surfaced fairly.
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

        # Round-robin selection across active buckets up to max_alerts_per_run
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

        dispatched: List[Dict[str, Any]] = []
        for sig in selected:
            self.dispatch_signal(sig, dry_run=dry_run)
            if not dry_run:
                self.record_signal_emitted(sig["symbol"], sig["signal_type"])
            dispatched.append(sig)

        if not dry_run:
            save_scanner_state(self.state, self.state_path)

        print(
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Scan complete: "
            f"{len(raw_signals)} setup(s) detected, {len(dispatched)} new alert(s) dispatched."
        )
        return dispatched

    def _print_metrics_dashboard(self, metrics_list: List[Dict[str, Any]], macro_regime: str) -> None:
        """Prints a clean 2-section market intelligence dashboard."""
        print("\n" + "=" * 102)
        print(
            f"📊 SMART MONEY INTELLIGENCE DASHBOARD — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} "
            f"| BTC Regime: {macro_regime}"
        )
        print("=" * 102)

        # Section 1: Top Wyckoff Accumulations
        acc_sorted = sorted(metrics_list, key=lambda m: m["accum_score"], reverse=True)
        acc_top = [m for m in acc_sorted if m["accum_score"] >= 45.0][:10]
        print("\n📦 TOP WYCKOFF ACCUMULATION COINS:")
        print(f"{'Symbol':<12} | {'Close':<12} | {'Accum':<10} | {'Class':<18} | {'CVD Slope':<10} | {'Div/Abs':<9} | {'Dist R'}")
        print("-" * 102)
        for m in acc_top:
            flags = ("DIV " if m["cvd_divergence"] else "") + ("ABS" if m["absorption"] else "")
            flags = flags.strip() or "-"
            dist_s = f"+{m['dist_to_r']:.2f}%" if m["dist_to_r"] > 0 else "ABOVE R"
            cvd_s = f"{m['cvd_slope']:+.3f}"
            print(
                f"{m['symbol']:<12} | {fmt_price(m['close']):<12} | {m['accum_score']:<5.1f}/100 | "
                f"{m['accum_class']:<18} | {cvd_s:<10} | {flags:<9} | {dist_s}"
            )

        # Section 2: Top Bullish Trending Coins
        trend_sorted = sorted(metrics_list, key=lambda m: m["trend_score"], reverse=True)
        trend_top = [m for m in trend_sorted if m["trend_score"] >= 50.0][:10]
        print("\n📈 TOP BULLISH TRENDING COINS:")
        print(f"{'Symbol':<12} | {'Close':<12} | {'Trend':<10} | {'State':<20} | {'ADX14':<7} | {'RSI14':<6} | {'24h %':<8} | {'4h'}")
        print("-" * 102)
        for m in trend_top:
            htf_s = "BULL" if m["htf_aligned"] is True else ("NEUT" if m["htf_aligned"] is None else "BEAR")
            pct_s = f"{m['pct_24h']:+.2f}%"
            print(
                f"{m['symbol']:<12} | {fmt_price(m['close']):<12} | {m['trend_score']:<5.1f}/100 | "
                f"{m['trend_state']:<20} | {m['adx14']:<7.1f} | {m['rsi14']:<6.1f} | {pct_s:<8} | {htf_s}"
            )
        print("=" * 102 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Smart Money Signal Scanner (Accumulation + Bullish Trend + Breakouts)")
    parser.add_argument("--scan", action="store_true", help="Run a single scan across top liquid Binance Spot pairs")
    parser.add_argument("--overview", action="store_true", help="Display full Smart Money Intelligence Dashboard")
    parser.add_argument("--loop", action="store_true", help="Run continuous scan loop")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between loop scans (default: 300s)")
    parser.add_argument("--max-symbols", type=int, default=60, help="Max top-volume USDT pairs to scan (default: 60)")
    parser.add_argument("--symbol", type=str, help="Deep scan a single symbol (e.g. SOLUSDT)")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and print signals without Telegram or state mutation")
    parser.add_argument("--test-telegram", action="store_true", help="Send test alert to verify Telegram credentials")
    args = parser.parse_args()

    engine = SmartMoneySignalEngine()

    if args.test_telegram:
        if not engine.telegram_token or not engine.telegram_chat_id:
            print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in environment.")
            sys.exit(1)
        test_sig = {
            "signal_id": str(uuid.uuid4()),
            "symbol": "SOLUSDT",
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
            "reasons": [
                "Tight consolidation base (bandwidth 5.4%)",
                "Confirmed Wyckoff spring bullish divergence",
                "4h higher-timeframe bullish alignment confirmed",
            ],
        }
        print("Sending test Smart Money Telegram alert...")
        telegram_msg(engine.telegram_token, engine.telegram_chat_id, format_telegram_alert(test_sig))
        print("Test alert sent successfully!")
        return

    if args.symbol:
        sym = args.symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"
        print(f"Performing deep Smart Money scan on {sym}...")
        sig, metrics = engine.analyze_symbol(sym)
        if metrics:
            engine._print_metrics_dashboard([metrics], engine.get_btc_macro_regime())
        if sig:
            engine.dispatch_signal(sig, dry_run=args.dry_run)
        else:
            print(f"No actionable Accumulation, Bullish Trend, Breakout, or Retest signal on {sym} right now.")
        return

    if args.overview:
        symbols = discover_liquid_universe(max_symbols=args.max_symbols)
        engine.scan_universe(symbols=symbols, dry_run=True, show_overview=True)
        return

    if args.loop:
        print(f"Starting continuous Smart Money scan loop (interval: {args.interval}s)...")
        while True:
            try:
                symbols = discover_liquid_universe(max_symbols=args.max_symbols)
                engine.scan_universe(symbols=symbols, dry_run=args.dry_run, show_overview=False)
            except KeyboardInterrupt:
                print("\nScanner stopped by user.")
                break
            except Exception as e:
                print(f"Loop scan exception: {e}")
            time.sleep(args.interval)
    else:
        symbols = discover_liquid_universe(max_symbols=args.max_symbols)
        engine.scan_universe(symbols=symbols, dry_run=args.dry_run, show_overview=True)


if __name__ == "__main__":
    main()
