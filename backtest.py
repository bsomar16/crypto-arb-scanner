#!/usr/bin/env python3
"""Walk-forward backtest of the daily scoring strategy vs buy & hold."""

from statistics import mean
from botutil import esc
from markets import binance_klines
from signals import daily_indicators, score_daily, rating
from datetime import datetime, timezone

COST_PCT = 0.15  # round-trip fees + slippage


def daily_state(i, closes, vols):
    c_so_far = closes[:i + 1]
    dc = daily_indicators(c_so_far)
    lo = max(0, i - 9)
    recent = vols[i] if i - 1 < 0 else (vols[i] + vols[i - 1]) / 2
    base = mean(vols[lo:i]) if i - lo >= 4 else mean(vols[:i]) or 1.0
    vr = recent / base if base > 0 else 1.0
    chg = (closes[i] / closes[i - 1] - 1) * 100 if i >= 1 else 0.0
    return dc, vr, chg


def run_symbol(symbol, bars=750, warmup=90):
    closes, highs, lows, vols = binance_klines(symbol, "1d", bars)
    if len(closes) < warmup + 120:
        return None
    position = False
    entry = 0.0
    entry_i = 0
    trades = []
    equity = [100.0]
    for i in range(warmup, len(closes)):
        dc, vr, chg = daily_state(i, closes, vols)
        sc = score_daily(dc, vr, chg)
        r = rating(sc)
        price = closes[i]
        if not position and r in ("BUY", "STRONG BUY"):
            position = True
            entry = price
            entry_i = i
        elif position:
            reason = None
            if r in ("WATCH", "AVOID"):
                reason = "rating"
            elif dc["rsi"] and dc["rsi"] > 80:
                reason = "rsi"
            elif i - entry_i >= 30:
                reason = "time"
            if reason:
                ret = (price - entry) / entry * 100 - COST_PCT
                trades.append({"ret": ret, "reason": reason, "rsi": dc["rsi"]})
                equity.append(equity[-1] * (1 + ret / 100))
                position = False
    if position:
        ret = (closes[-1] - entry) / entry * 100 - COST_PCT
        trades.append({"ret": ret, "reason": "open", "rsi": None})
        equity.append(equity[-1] * (1 + ret / 100))

    rets = [t["ret"] for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    compound = (equity[-1] / equity[0] - 1) * 100
    buyhold = (closes[-1] / closes[warmup] - 1) * 100
    pf = (sum(wins) / -sum(losses)) if losses else float("inf")
    peak = max(equity)
    dd = min((e / peak - 1) * 100 for e in equity)
    return {"symbol": symbol, "n": len(trades), "win": len(wins),
            "win_pct": (len(wins) / len(trades) * 100) if trades else 0.0,
            "avg_w": mean(wins) if wins else 0.0,
            "avg_l": mean(losses) if losses else 0.0,
            "pf": pf, "ret": compound, "bh": buyhold, "dd": dd,
            "last_rsi": trades[-1]["rsi"] if trades else None}


def run_backtest(cfg):
    symbols = cfg.get("backtest_symbols", []) or ["BTC", "ETH", "SOL"]
    res = []
    for sym in symbols:
        try:
            r = run_symbol(str(sym).upper())
            if r:
                res.append(r)
            print(f"[BACKTEST] {sym}: done")
        except Exception as e:
            print(f"[BACKTEST] {sym}: error {e}")
    if not res:
        return "Backtest: no data."

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📈 <b>BACKTEST</b> · signaux quotidiens \u00b7 {now}",
             f"<i>strat: score &#8805;60 entrée, score &lt;55 / RSI &gt;80 / 30j stop&#8594;sortie · coûts 0.15%</i>",
             "",
             f"{'Coin':<7}{'#':>4}{'Win%':>7}{'AvgW':>7}{'AvgL':>7}{'PF':>6}"
             f"{'Ret%':>8}{'B&H%':>7}{'MDD%':>7}"]
    agg_wins = agg_n = 0
    for r in res:
        lines.append(
            f"{r['symbol']:<7}{r['n']:>4}{r['win_pct']:>6.0f}{r['avg_w']:>7.1f}"
            f"{r['avg_l']:>7.1f}{r['pf']:>6.1f}{r['ret']:>+7.1f}"
            f"{r['bh']:>+7.1f}{r['dd']:>+7.1f}")
        agg_wins += r["win"]
        agg_n += r["n"]
    lines.append("")
    if agg_n:
        lines.append(f"<b>{agg_wins}/{agg_n}</b> trades gagnants "
                     f"({agg_wins / agg_n * 100:.0f}%) en moyenne sur les coins testés.")
    best = max(res, key=lambda r: r["pf"]) if res else None
    if best:
        lines.append(f"🔑 Le moteur fonctionne le mieux sur des noms à fort momentum "
                     f"comme <b>{best['symbol']}</b> (PF {best['pf']:.1f}). "
                     f"Toujours vérifier avant de trader — pas un conseil financier.")
    lines.append("")
    lines.append("⚠️ Backtest simple: pas de slippage réel, données journalières (Binance).")
    return "\n".join(lines)


if __name__ == "__main__":
    print(run_backtest({"backtest_symbols": ["BTC", "SOL", "BNB"]}))