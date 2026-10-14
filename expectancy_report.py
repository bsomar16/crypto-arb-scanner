#!/usr/bin/env python3
"""Expectancy and walk-forward report for logged BUY signals.

Answers the only question that matters for tuning: *is there positive expectancy
after costs, and for which setups / timeframes / regimes?* Win rate alone is not a
goal - a 70% hit rate at 1:0.5 R:R still loses money.

It joins the forward-logged signal features (state/signal_history.jsonl) with the
graded excursions (state/signal_outcomes.json) and simulates exit policies from the
recorded MFE/MAE using the same conservative ordering the grader uses: if a candle's
low pierces the stop, the stop is assumed to have been hit first.

This module makes no network calls and never mutates bot state.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

CONFIGURED_TARGET_PCT = 5.0   # config tp1_pct
CONFIGURED_STOP_PCT = 5.0     # config stoploss_pct


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_rows(history_path: str, outcomes_path: str) -> list[dict]:
    """Join signal features with graded outcomes on the signal id."""
    signals: dict[str, dict] = {}
    with open(history_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == "signal" and row.get("id"):
                signals[row["id"]] = row

    with open(outcomes_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("outcomes file must be a JSON object keyed by signal id")

    rows: list[dict] = []
    for sig_id, outcome in data.items():
        sig = signals.get(sig_id)
        if sig is None or outcome.get("mfe_pct") is None or outcome.get("mae_pct") is None:
            continue
        rows.append({
            "id": sig_id,
            "ts": sig.get("ts") or outcome.get("evaluated_at") or "",
            "coin": sig.get("coin"),
            "interval": sig.get("interval"),
            "setup_type": sig.get("setup_type"),
            "strategy_family": sig.get("strategy_family") or sig.get("strategy") or "STANDARD",
            "score": _f(sig.get("score") if sig.get("score") is not None else sig.get("confidence_score")),
            "potential_pct": _f(sig.get("potential_pct")),
            "rr": _f(sig.get("rr")),
            "trend_4h": sig.get("trend_4h") or "UNKNOWN",
            "expansion_state": sig.get("expansion_state") or "UNKNOWN",
            "entry_quality": _f(sig.get("entry_quality")),
            "mfe": float(outcome["mfe_pct"]),
            "mae": float(outcome["mae_pct"]),
            "graded_outcome": outcome.get("outcome"),
            "bars_held": outcome.get("bars_held"),
        })
    return rows


def cost_pct(fee_bps: float, slippage_bps: float) -> float:
    """Round-trip cost in percent (entry + exit)."""
    return 2.0 * (float(fee_bps) + float(slippage_bps)) / 100.0


def simulate(mfe: float, mae: float, target_pct: float, stop_pct: float) -> float:
    """PnL% for a 'target or stop' policy over the recorded excursion.

    Conservative ordering: a stop breach anywhere in the window counts as a loss,
    even if the target was also touched. If neither is touched the trade is held to
    the horizon and scored flat (0%) - reported separately so the assumption is visible.
    """
    if mae <= -abs(stop_pct):
        return -abs(stop_pct)
    if mfe >= abs(target_pct):
        return abs(target_pct)
    return 0.0


def stats(pnls: list[float], cost: float) -> dict:
    n = len(pnls)
    if not n:
        return {"n": 0, "win_pct": None, "avg_win": None, "avg_loss": None,
                "expectancy_pct": None, "profit_factor": None}
    net = [p - cost for p in pnls]
    wins = [p for p in net if p > 0]
    losses = [p for p in net if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "n": n,
        "win_pct": round(len(wins) / n * 100.0, 1),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "expectancy_pct": round(sum(net) / n, 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
    }


def policy_pnls(rows: list[dict], target_pct: float, stop_pct: float) -> list[float]:
    return [simulate(r["mfe"], r["mae"], target_pct, stop_pct) for r in rows]


def unresolved_pct(rows: list[dict], target_pct: float, stop_pct: float) -> float:
    if not rows:
        return 0.0
    n = sum(1 for r in rows if r["mae"] > -abs(stop_pct) and r["mfe"] < abs(target_pct))
    return round(n / len(rows) * 100.0, 1)


def grid(rows: list[dict], targets: list[float], stops: list[float], cost: float) -> list[list]:
    out = [["target \\ stop"] + [f"-{s:g}%" for s in stops]]
    for t in targets:
        line = [f"+{t:g}%"]
        for s in stops:
            st = stats(policy_pnls(rows, t, s), cost)
            line.append("n/a" if st["expectancy_pct"] is None else f"{st['expectancy_pct']:+.2f}")
        out.append(line)
    return out


def breakdown(rows: list[dict], key: str, target: float, stop: float, cost: float,
              min_n: int = 1) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key) or "UNKNOWN")].append(r)
    out = []
    for name, rs in groups.items():
        if len(rs) < min_n:
            continue
        st = stats(policy_pnls(rs, target, stop), cost)
        st.update({"group": name, "avg_mfe": round(sum(x["mfe"] for x in rs) / len(rs), 2),
                   "avg_mae": round(sum(x["mae"] for x in rs) / len(rs), 2),
                   "unresolved_pct": unresolved_pct(rs, target, stop)})
        out.append(st)
    out.sort(key=lambda d: (d["expectancy_pct"] is None, -(d["expectancy_pct"] or -999)))
    return out


def walk_forward(rows: list[dict], folds: int, target: float, stop: float, cost: float) -> list[dict]:
    ordered = sorted(rows, key=lambda r: r["ts"])
    if folds < 2 or len(ordered) < folds:
        return []
    size = len(ordered) // folds
    out = []
    for i in range(folds):
        chunk = ordered[i * size:(i + 1) * size] if i < folds - 1 else ordered[i * size:]
        st = stats(policy_pnls(chunk, target, stop), cost)
        st.update({"fold": i + 1, "from": chunk[0]["ts"][:10], "to": chunk[-1]["ts"][:10]})
        out.append(st)
    return out


def _print_table(title: str, lines: list[list]) -> None:
    print(f"\n{title}")
    if not lines:
        print("  (no data)")
        return
    widths = [max(len(str(row[i])) for row in lines) for i in range(len(lines[0]))]
    for idx, row in enumerate(lines):
        print("  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
        if idx == 0:
            print("  " + "  ".join("-" * widths[i] for i in range(len(row))))


def _breakdown_table(rows: list[dict]) -> list[list]:
    out = [["group", "n", "exp%", "win%", "PF", "avgMFE", "avgMAE", "unres%"]]
    for d in rows:
        out.append([d["group"], d["n"],
                    "n/a" if d["expectancy_pct"] is None else f"{d['expectancy_pct']:+.2f}",
                    "n/a" if d["win_pct"] is None else f"{d['win_pct']:.1f}",
                    "n/a" if d["profit_factor"] is None else f"{d['profit_factor']:.2f}",
                    f"{d['avg_mfe']:.2f}", f"{d['avg_mae']:.2f}", f"{d['unresolved_pct']:.1f}"])
    return out


def build_report(rows: list[dict], args) -> dict:
    cost = cost_pct(args.fee_bps, args.slippage_bps)
    report = {
        "n_signals": len(rows),
        "cost_pct_round_trip": round(cost, 3),
        "assumptions": {
            "ordering": "stop-first when both touched (matches the outcome grader)",
            "unresolved": "held to horizon, scored 0%",
            "fee_bps_per_side": args.fee_bps,
            "slippage_bps_per_side": args.slippage_bps,
        },
        "configured_policy": stats(policy_pnls(rows, CONFIGURED_TARGET_PCT, CONFIGURED_STOP_PCT), cost),
        "configured_unresolved_pct": unresolved_pct(rows, CONFIGURED_TARGET_PCT, CONFIGURED_STOP_PCT),
        "grid_expectancy_pct": grid(rows, args.targets, args.stops, cost),
        "walk_forward": walk_forward(rows, args.folds, CONFIGURED_TARGET_PCT, CONFIGURED_STOP_PCT, cost),
    }
    for key in ("interval", "setup_type", "strategy_family", "trend_4h", "expansion_state"):
        report[f"by_{key}"] = breakdown(rows, key, CONFIGURED_TARGET_PCT, CONFIGURED_STOP_PCT, cost)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Expectancy / walk-forward report for BUY signals")
    ap.add_argument("--history", default="state/signal_history.jsonl")
    ap.add_argument("--outcomes", default="state/signal_outcomes.json")
    ap.add_argument("--fee-bps", type=float, default=10.0, help="per side, basis points")
    ap.add_argument("--slippage-bps", type=float, default=5.0, help="per side, basis points")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--targets", type=float, nargs="+", default=[1, 2, 3, 5, 10])
    ap.add_argument("--stops", type=float, nargs="+", default=[1, 2, 3, 5])
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    try:
        rows = load_rows(args.history, args.outcomes)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("error: no joinable graded signals found", file=sys.stderr)
        return 1

    report = build_report(rows, args)
    conf = report["configured_policy"]
    print(f"graded signals: {report['n_signals']}   round-trip cost: {report['cost_pct_round_trip']:.2f}%")
    print(f"configured policy (+5% / -5%): expectancy {conf['expectancy_pct']:+.2f}%/trade   "
          f"win {conf['win_pct']}%   PF {conf['profit_factor']}   unresolved {report['configured_unresolved_pct']}%")
    print("\nassumptions: " + "; ".join(f"{k}={v}" for k, v in report["assumptions"].items()))
    _print_table("EXIT POLICY GRID - expectancy %/trade after costs (target x stop)", report["grid_expectancy_pct"])
    for key in ("interval", "setup_type", "strategy_family", "trend_4h", "expansion_state"):
        _print_table(f"BY {key.upper()} (configured +5%/-5%)", _breakdown_table(report[f"by_{key}"]))
    if report["walk_forward"]:
        wf = [["fold", "from", "to", "n", "exp%", "win%", "PF"]]
        for f in report["walk_forward"]:
            wf.append([f["fold"], f["from"], f["to"], f["n"],
                       "n/a" if f["expectancy_pct"] is None else f"{f['expectancy_pct']:+.2f}",
                       "n/a" if f["win_pct"] is None else f"{f['win_pct']:.1f}",
                       "n/a" if f["profit_factor"] is None else f"{f['profit_factor']:.2f}"])
        _print_table("WALK-FORWARD (time folds, configured policy)", wf)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
