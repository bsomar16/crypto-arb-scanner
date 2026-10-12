#!/usr/bin/env python3
"""Audit realized outcomes for perfect-score BUY signals.

Reads runtime signal_history.jsonl and optionally state/signal_outcomes.json.
No trading logic is changed. The report joins records by signal ID and keeps
WIN/LOSS as the decisive win-rate denominator; EXPIRED/unknown are reported
separately rather than silently counted as losses.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_jsonl(path):
    rows = []
    path = Path(path)
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def load_outcomes(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            result[str(value.get("id", key))] = value
    return result


def is_perfect_score(signal):
    try:
        return float(signal.get("score", -1)) == 100.0 and float(
            signal.get("confidence_score", -1)
        ) == 100.0
    except (TypeError, ValueError):
        return False


def summarize(signals, outcomes):
    selected = [s for s in signals if is_perfect_score(s)]
    groups = defaultdict(list)
    for signal in selected:
        outcome = outcomes.get(str(signal.get("id", "")))
        if not outcome:
            state = "UNRESOLVED"
        else:
            state = str(outcome.get("outcome", "UNKNOWN")).upper()
        key = (str(signal.get("interval") or "UNKNOWN"),
               str(signal.get("setup_type") or "UNKNOWN"))
        groups[key].append((signal, outcome, state))

    def metrics(rows):
        wins = sum(state == "WIN" for _, _, state in rows)
        losses = sum(state == "LOSS" for _, _, state in rows)
        decisive = wins + losses
        tp1_known = [(o or {}).get("staged_target_hits", {}).get("t1")
                     for _, o, _ in rows
                     if isinstance((o or {}).get("staged_target_hits"), dict)
                     and "t1" in (o or {}).get("staged_target_hits", {})]
        tp3_known = [(o or {}).get("staged_target_hits", {}).get("t3")
                     for _, o, _ in rows
                     if isinstance((o or {}).get("staged_target_hits"), dict)
                     and "t3" in (o or {}).get("staged_target_hits", {})]
        return {
            "signals": len(rows), "wins": wins, "losses": losses,
            "decisive": decisive,
            "win_rate_pct": round(100 * wins / decisive, 2) if decisive else None,
            "unresolved_or_nondecisive": len(rows) - decisive,
            "tp1_hits": sum(v is True for v in tp1_known),
            "tp1_observed": len(tp1_known),
            "tp1_hit_rate_pct": round(100 * sum(v is True for v in tp1_known) / len(tp1_known), 2) if tp1_known else None,
            "tp3_hits": sum(v is True for v in tp3_known),
            "tp3_observed": len(tp3_known),
            "tp3_hit_rate_pct": round(100 * sum(v is True for v in tp3_known) / len(tp3_known), 2) if tp3_known else None,
        }

    return {
        "filter": "score == 100 AND confidence_score == 100",
        "score_100_signals": len(selected),
        "overall": metrics([(s, outcomes.get(str(s.get("id", ""))),
                             str((outcomes.get(str(s.get("id", ""))) or {}).get("outcome", "UNRESOLVED")).upper())
                            for s in selected]),
        "by_interval_setup": {
            f"{interval}|{setup}": metrics(rows)
            for (interval, setup), rows in sorted(groups.items())
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", default="state/signal_history.jsonl")
    parser.add_argument("--outcomes", default="state/signal_outcomes.json")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    history = load_jsonl(args.history)
    signals = [row for row in history if row.get("kind") == "signal"]
    outcomes = load_outcomes(args.outcomes)
    for row in history:
        if row.get("kind") == "outcome" and row.get("id") is not None:
            outcomes[str(row["id"])] = row
    report = summarize(signals, outcomes)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    overall = report["overall"]
    print("Perfect-score BUY outcome audit")
    print(f"Filter: {report['filter']}")
    print(f"Matching signals: {report['score_100_signals']}")
    print(f"Resolved WIN/LOSS: {overall['decisive']} "
          f"({overall['wins']} wins, {overall['losses']} losses)")
    print(f"Win rate: {overall['win_rate_pct']}%")
    print(f"Unresolved/non-decisive: {overall['unresolved_or_nondecisive']}")
    print(f"TP1 hit rate: {overall['tp1_hit_rate_pct']}% "
          f"({overall['tp1_hits']}/{overall['tp1_observed']} observed)")
    print(f"TP3 hit rate: {overall['tp3_hit_rate_pct']}% "
          f"({overall['tp3_hits']}/{overall['tp3_observed']} observed)")
    print("\nBy timeframe and setup:")
    for key, value in report["by_interval_setup"].items():
        print(f"  {key}: win rate={value['win_rate_pct']}%, "
              f"wins/losses={value['wins']}/{value['losses']}, "
              f"signals={value['signals']}, TP1={value['tp1_hit_rate_pct']}%, "
              f"TP3={value['tp3_hit_rate_pct']}%")


if __name__ == "__main__":
    main()
