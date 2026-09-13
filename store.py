#!/usr/bin/env python3
"""Append-only JSONL event log + report aggregations (stdlib only).

One store for (a) spread alerts, (b) daily picks, (c) position lifecycle
events. `--report` aggregates from here. JSONL keeps it diff-friendly,
which matters because the bot auto-commits state on every run.
"""

import json
from datetime import datetime, timezone
from statistics import mean, median

LOG_PATH = "state/run_log.jsonl"


def _now():
    return datetime.now(timezone.utc)


def _ts():
    return _now().isoformat(timespec="seconds")


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def log_row(row):
    row = dict(row)
    row.setdefault("ts", _ts())
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def spread_log(coin, gross, net, low_ex, high_ex, low, high, median_price):
    log_row({"kind": "spread", "coin": coin, "gross": round(float(gross), 3),
             "net": round(float(net), 3), "low_ex": low_ex, "high_ex": high_ex,
             "low": low, "high": high, "median": median_price})


def daily_log(coin, score, rating, price, chg, rsi, vol_x, qv):
    log_row({"kind": "daily", "coin": coin, "score": score, "rating": rating,
             "price": price, "chg": chg, "rsi": rsi, "vol_x": vol_x, "qv": qv})


def position_event(event, coin, entry, price=None, outcome=None, note=None):
    row = {"kind": "position", "event": event, "coin": coin, "entry": entry}
    if price is not None:
        row["price"] = price
    if outcome:
        row["outcome"] = outcome
    if note:
        row["note"] = note
    log_row(row)


def read(kind=None):
    rows = []
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict):
                    continue
                if kind is None or r.get("kind") == kind:
                    rows.append(r)
    except (FileNotFoundError, OSError):
        pass
    return rows


# ─────────────────────────── aggregation ───────────────────────────

def aggregate(rows, now=None):
    """Pure aggregation over log rows -> dict of report sections."""
    now = now if now is not None else _now()
    spreads = [r for r in rows if r.get("kind") == "spread"]
    dailies = [r for r in rows if r.get("kind") == "daily"]
    pos = [r for r in rows if r.get("kind") == "position"]

    spread_stats = {"count": 0, "coins": {}, "gross_avg": None, "net_avg": None}
    for r in spreads:
        ts = _parse_ts(r.get("ts"))
        if ts is None or (now - ts).days > 90:
            continue
        spread_stats["count"] += 1
        c = r.get("coin", "?")
        cst = spread_stats["coins"].setdefault(c, {"count": 0, "nets": [], "last": None})
        cst["count"] += 1
        try:
            cst["nets"].append(float(r.get("net", 0)))
        except (TypeError, ValueError):
            pass
        cst["last"] = r.get("ts")
    nets_all = [v for st in spread_stats["coins"].values() for v in st["nets"]]
    if nets_all:
        spread_stats["net_avg"] = mean(nets_all)
        spread_stats["net_median"] = median(nets_all)
    gross_all = []
    for r in spreads:
        try:
            gross_all.append(float(r.get("gross", 0)))
        except (TypeError, ValueError):
            pass
    if gross_all:
        spread_stats["gross_avg"] = mean(gross_all)
        spread_stats["gross_median"] = median(gross_all)

    rating_counts = {}
    scores_sum = 0.0
    scores_n = 0
    for r in dailies:
        rt = r.get("rating", "?")
        rating_counts[rt] = rating_counts.get(rt, 0) + 1
        try:
            scores_sum += float(r.get("score", 0))
            scores_n += 1
        except (TypeError, ValueError):
            pass

    opens = [r for r in pos if r.get("event") == "open"]
    wins = [r for r in pos if r.get("event") == "tp3"]
    losses = [r for r in pos if r.get("event") == "sl"]
    closes = [r for r in pos if r.get("event") == "close"]
    expired = [r for r in pos if r.get("event") == "expired"]
    tp1s = [r for r in pos if r.get("event") == "tp1"]

    win_rate = None
    if (len(wins) + len(losses)) > 0:
        win_rate = len(wins) / (len(wins) + len(losses))

    open_by = {}
    for r in opens:
        try:
            open_by[(r.get("coin"), float(r.get("entry", 0)))] = _parse_ts(r.get("ts"))
        except (TypeError, ValueError):
            pass
    tp1_times = []
    for r in tp1s:
        key = (r.get("coin"), float(r.get("entry", 0)))
        ot = open_by.get(key)
        tt = _parse_ts(r.get("ts"))
        if ot and tt:
            hrs = (tt - ot).total_seconds() / 3600
            if 0 <= hrs <= 90 * 24:
                tp1_times.append(hrs)

    return {"spreads": spread_stats, "ratings": rating_counts,
            "dailies_count": len(dailies),
            "daily_score_avg": (scores_sum / scores_n) if scores_n else None,
            "positions": {"opened": len(opens), "tp3": len(wins), "sl": len(losses),
                          "closed": len(closes), "expired": len(expired),
                          "win_rate": win_rate,
                          "tp1_avg_hours": mean(tp1_times) if tp1_times else None}}


def build_report_text(rows=None, now=None):
    rows = rows if rows is not None else read()
    a = aggregate(rows, now)
    lines = [f"\U0001f4ca <b>SIGNAL REPORT</b> \u00b7 {_ts()}", ""]
    sp = a["spreads"]
    lines.append(f"\U0001f6e1\ufe0f <b>Spread alerts (90d)</b> \u00b7 {sp['count']} total")
    top = sorted(sp["coins"].items(), key=lambda kv: -kv[1]["count"])[:8]
    for c, st in top:
        last = (st["last"] or "")[11:16]
        if st["nets"]:
            lines.append(f"   {c}: <b>{st['count']}</b> alerts \u00b7 last {last}"
                         f" \u00b7 avg net {mean(st['nets']):.2f}%")
        else:
            lines.append(f"   {c}: <b>{st['count']}</b> alerts \u00b7 last {last}")
    if sp["net_avg"] is not None:
        lines.append(f"  avg net spread {sp['net_avg']:.2f}% \u00b7 "
                     f"median {sp['net_median']:.2f}% \u00b7 gross avg {sp['gross_avg']:.2f}%")
    lines.append("")
    lines.append(f"\U0001f4c8 <b>Daily picks</b> \u00b7 {a['dailies_count']} logged")
    for rt in ("STRONG BUY", "BUY", "WATCH"):
        if a["ratings"].get(rt):
            lines.append(f"   {rt}: {a['ratings'][rt]}")
    if a["daily_score_avg"]:
        lines.append(f"   avg score {a['daily_score_avg']:.1f}")
    p = a["positions"]
    lines.append("")
    lines.append("\U0001f501 <b>Virtual positions</b>")
    lines.append(f"   opened {p['opened']} \u00b7 TP3 {p['tp3']} \u00b7 SL {p['sl']}"
                 f" \u00b7 expired {p['expired']}")
    if p["win_rate"] is not None:
        lines.append(f"   win rate (TP3/total) <b>{p['win_rate'] * 100:.0f}%</b>")
    if p["tp1_avg_hours"] is not None:
        lines.append(f"   avg time-to-TP1 {p['tp1_avg_hours']:.1f}h")
    return "\n".join(lines)


def build_report_html(rows=None, now=None):
    rows = rows if rows is not None else read()
    a = aggregate(rows, now)
    sp = a["spreads"]
    top = sorted(sp["coins"].items(), key=lambda kv: -kv[1]["count"])[:20]

    def esc_h(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    trs = ["<tr><th>Coin</th><th>Alerts</th><th>Avg net %</th><th>Last seen (UTC)</th></tr>"]
    for c, st in top:
        avg = f"{mean(st['nets']):.2f}" if st["nets"] else "-"
        trs.append(f"<tr><td>{esc_h(c)}</td><td>{st['count']}</td><td>{avg}</td>"
                   f"<td>{esc_h(st['last'] or '-')}</td></tr>")

    p = a["positions"]
    winrate = f"{p['win_rate'] * 100:.0f}%" if p["win_rate"] is not None else "-"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Crypto Signal Report</title>
<style>body{{font-family:system-ui,Segoe UI,Arial;margin:2rem;background:#0d1117;color:#e6edf3}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}
th,td{{border:1px solid #30363d;padding:.4rem .6rem;text-align:left}}
th{{background:#161b22}}h1{{font-size:1.3rem}}</style></head><body>
<h1>Crypto Signal Report</h1>
<p>Generated {_ts()}</p>
<h2>Spread alerts (90d)</h2>
<p>Total: {sp['count']} · avg net {sp['net_avg']:.2f}% · gross avg {sp['gross_avg']:.2f}%</p>
<table>{''.join(trs)}</table>
<h2>Virtual positions</h2>
<p>Opened {p['opened']} · TP3 {p['tp3']} · SL {p['sl']} · expired {p['expired']}
· win rate {winrate}</p>
<p>Ratings: {esc_h(a['ratings'])}</p>
</body></html>"""


def followup_spreads(hours_back=6, threshold=1.0):
    """Re-check recent spread-alert coins against live prices.

    Returns list of (coin, latest_alert_ts, net_pct, still_open).
    Uses the same Binance ticker endpoint as the rest of the bot.
    """
    from markets import binance_price
    now = _now()
    out = []
    for r in read("spread"):
        ts = _parse_ts(r.get("ts"))
        if ts is None or (now - ts).total_seconds() > hours_back * 3600:
            continue
        coin = r.get("coin")
        price = binance_price(coin) if coin else None
        if price is None:
            continue
        try:
            high = float(r.get("high", 0))
            low = float(r.get("low", 0)) or price
            still = (high / low - 1) * 100 >= threshold if high and low else None
        except (TypeError, ValueError):
            still = None
        out.append((coin, r.get("ts"), float(r.get("net", 0)), still))
    return out