"""Smart Money Shadow Signal Outcome & TP1/TP2/SL Tracker.

Reuses the verified Stage 2 ActivePosition exit engine (`smart_money/position_manager.py`)
to track every dispatched Smart Money Telegram signal across subsequent closed 1h candles:
- Detects TP1 (+1.5R) -> notifies user to take 50% and move Stop Loss to Breakeven.
- Detects TP2 (+3.0R), Stop Loss, Breakeven Stop, Trailing Stop, and 12-bar Time Stop.
- Persists open tracked signals atomically in `state/smart_money_tracked_signals.json`.
- Appends closed trade outcomes to `state/smart_money_outcomes.jsonl` for win-rate & Profit Factor analytics.

Strictly Shadow Mode / Signal Tracking Only. Zero live Binance orders.
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from .position_manager import ActivePosition, CandleBar, create_active_position
from .breakout import _atr_series


TRACKED_SIGNALS_PATH = os.path.join("state", "smart_money_tracked_signals.json")
OUTCOMES_JOURNAL_PATH = os.path.join("state", "smart_money_outcomes.jsonl")


def _fmt_p(price: float) -> str:
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


class SmartMoneySignalTracker:
    """Tracks shadow outcomes of dispatched Smart Money signals on closed 1h bars."""

    def __init__(
        self,
        tracked_path: str = TRACKED_SIGNALS_PATH,
        outcomes_path: str = OUTCOMES_JOURNAL_PATH,
    ):
        self.tracked_path = tracked_path
        self.outcomes_path = outcomes_path
        self.tracked: Dict[str, Dict[str, Any]] = self._load_tracked()

    def _load_tracked(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self.tracked_path):
            return {}
        try:
            with open(self.tracked_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_tracked(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.tracked_path)), exist_ok=True)
        tmp_path = f"{self.tracked_path}.tmp.{uuid.uuid4().hex}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.tracked, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.tracked_path)

    def _append_outcome(self, record: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.outcomes_path)), exist_ok=True)
        with open(self.outcomes_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def register_signal(self, sig: Dict[str, Any], candle_open_ms: int) -> bool:
        """Registers a newly dispatched Smart Money signal for shadow TP1/TP2/SL tracking."""
        sym = str(sig.get("symbol", "")).upper()
        if not sym or sym in self.tracked:
            return False

        entry = Decimal(str(sig["entry_price"]))
        stop = Decimal(str(sig["stop_loss"]))
        if entry <= Decimal("0") or stop <= Decimal("0") or stop >= entry:
            return False

        # Normalize notional to $100 so realized_pnl directly represents net % return
        q_order = Decimal("100.0") / entry
        r_dist = entry - stop
        pos = create_active_position(
            symbol=sym,
            entry_time_ms=candle_open_ms,
            entry_price=entry,
            q_order=q_order,
            initial_stop=stop,
            atr14=r_dist,
        )
        # Override targets to exact signal TP1/TP2 if provided
        if sig.get("tp1"):
            pos.tp1_price = Decimal(str(sig["tp1"]))
        if sig.get("tp2"):
            pos.tp2_price = Decimal(str(sig["tp2"]))
        # Accumulation & Trend signals get a 24h time horizon before time-stop
        if sig.get("signal_type") in (
            "EARLY_ACCUMULATION", "PRE_BREAKOUT",
            "STRONG_BULLISH_TREND", "BULLISH_TREND", "BULLISH_PULLBACK",
        ):
            pos.time_stop_bars = 24

        self.tracked[sym] = {
            "signal_id": sig.get("signal_id", str(uuid.uuid4())),
            "symbol": sym,
            "signal_type": sig.get("signal_type", "UNKNOWN"),
            "opened_utc": sig.get("timestamp_utc", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")),
            "last_evaluated_open_ms": int(candle_open_ms),
            "tp1_notified": False,
            "position": pos.to_dict(),
        }
        return True

    def advance_all(
        self,
        kline_fetcher: Callable[[str], List[Any]],
    ) -> List[Dict[str, Any]]:
        """Advances all open tracked signals against newly closed 1h candles.

        Returns list of human-readable event dicts (TP1 hit, TP2 hit, Stop hit, etc.).
        """
        events: List[Dict[str, Any]] = []
        closed_symbols: List[str] = []

        for sym, record in list(self.tracked.items()):
            klines = kline_fetcher(sym)
            if not klines or len(klines) < 20:
                continue

            closed_bars = klines[:-1]  # Exclude active unclosed candle
            last_ms = int(record.get("last_evaluated_open_ms", 0))
            pos = ActivePosition.from_dict(record["position"])

            highs = [float(k[2]) for k in closed_bars]
            lows = [float(k[3]) for k in closed_bars]
            closes = [float(k[4]) for k in closed_bars]
            atrs = _atr_series(highs, lows, closes, period=14)

            for idx, k in enumerate(closed_bars):
                bar_open_ms = int(k[0])
                if bar_open_ms <= last_ms:
                    continue

                bar = CandleBar(
                    timestamp_ms=bar_open_ms,
                    open=Decimal(str(k[1])),
                    high=Decimal(str(k[2])),
                    low=Decimal(str(k[3])),
                    close=Decimal(str(k[4])),
                    volume=Decimal(str(k[5])),
                )
                atr_val = Decimal(str(atrs[idx])) if (atrs and idx < len(atrs) and atrs[idx] > 0) else None
                execs = pos.evaluate_candle(bar, atr14=atr_val)
                last_ms = bar_open_ms

                for ex in execs:
                    if ex.exit_type == "TP1_EXECUTED_PARTIAL" and not record.get("tp1_notified"):
                        record["tp1_notified"] = True
                        events.append({
                            "symbol": sym,
                            "signal_type": record["signal_type"],
                            "event_type": "TP1_HIT",
                            "fill_price": float(ex.fill_price),
                            "entry_price": float(pos.entry_price),
                            "new_stop": float(pos.active_stop),
                            "pnl_pct": float(pos.realized_pnl),
                            "bars_held": pos.bars_held,
                        })
                    elif pos.status == "FULLY_EXITED":
                        r_dist = float(pos.entry_price - pos.initial_stop)
                        entry_f = float(pos.entry_price)
                        pnl_pct = float(pos.realized_pnl)  # $100 notional => directly % return
                        risk_pct = (r_dist / entry_f * 100.0) if entry_f > 0 else 1.0
                        r_mult = (pnl_pct / risk_pct) if risk_pct > 1e-6 else 0.0

                        outcome_rec = {
                            "signal_id": record["signal_id"],
                            "symbol": sym,
                            "signal_type": record["signal_type"],
                            "opened_utc": record["opened_utc"],
                            "closed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                            "entry_price": round(entry_f, 6),
                            "exit_price": round(float(ex.fill_price), 6),
                            "exit_reason": ex.exit_type,
                            "tp1_hit": bool(pos.tp1_executed),
                            "tp2_hit": bool(pos.tp2_executed),
                            "pnl_pct": round(pnl_pct, 2),
                            "r_multiple": round(r_mult, 2),
                            "bars_held": pos.bars_held,
                        }
                        self._append_outcome(outcome_rec)
                        events.append({
                            "symbol": sym,
                            "signal_type": record["signal_type"],
                            "event_type": ex.exit_type,
                            "fill_price": float(ex.fill_price),
                            "entry_price": entry_f,
                            "pnl_pct": round(pnl_pct, 2),
                            "r_multiple": round(r_mult, 2),
                            "tp1_hit": bool(pos.tp1_executed),
                            "bars_held": pos.bars_held,
                        })
                        closed_symbols.append(sym)
                        break

                if pos.status == "FULLY_EXITED":
                    break

            record["last_evaluated_open_ms"] = last_ms
            record["position"] = pos.to_dict()

        for sym in closed_symbols:
            self.tracked.pop(sym, None)

        return events

    def compute_stats(self) -> Dict[str, Any]:
        """Computes overall and per-signal-type win rate, TP1 hit rate, and Profit Factor."""
        records: List[Dict[str, Any]] = []
        if os.path.exists(self.outcomes_path):
            try:
                with open(self.outcomes_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            records.append(json.loads(line))
            except Exception:
                pass

        total = len(records)
        if total == 0:
            return {
                "total_closed": 0,
                "open_tracked": len(self.tracked),
                "win_rate_pct": 0.0,
                "tp1_hit_rate_pct": 0.0,
                "avg_pnl_pct": 0.0,
                "profit_factor": 0.0,
                "by_signal_type": {},
            }

        wins = [r for r in records if float(r.get("pnl_pct", 0.0)) > 0.05]
        losses = [r for r in records if float(r.get("pnl_pct", 0.0)) < -0.05]
        tp1_hits = [r for r in records if r.get("tp1_hit")]

        gross_win = sum(float(r["pnl_pct"]) for r in wins)
        gross_loss = abs(sum(float(r["pnl_pct"]) for r in losses))
        pf = (gross_win / gross_loss) if gross_loss > 1e-6 else (gross_win if gross_win > 0 else 0.0)

        by_type: Dict[str, Dict[str, Any]] = {}
        for r in records:
            st = str(r.get("signal_type", "UNKNOWN"))
            b = by_type.setdefault(st, {"count": 0, "wins": 0, "tp1_hits": 0, "total_pnl": 0.0})
            b["count"] += 1
            if float(r.get("pnl_pct", 0.0)) > 0.05:
                b["wins"] += 1
            if r.get("tp1_hit"):
                b["tp1_hits"] += 1
            b["total_pnl"] += float(r.get("pnl_pct", 0.0))

        for st, b in by_type.items():
            c = max(1, b["count"])
            b["win_rate_pct"] = round(b["wins"] / c * 100.0, 1)
            b["tp1_hit_rate_pct"] = round(b["tp1_hits"] / c * 100.0, 1)
            b["avg_pnl_pct"] = round(b["total_pnl"] / c, 2)

        return {
            "total_closed": total,
            "open_tracked": len(self.tracked),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / total * 100.0, 1),
            "tp1_hit_rate_pct": round(len(tp1_hits) / total * 100.0, 1),
            "avg_pnl_pct": round(sum(float(r.get("pnl_pct", 0.0)) for r in records) / total, 2),
            "profit_factor": round(pf, 2),
            "by_signal_type": by_type,
        }


def format_outcome_telegram_update(events: List[Dict[str, Any]], stats: Dict[str, Any]) -> str:
    """Formats a compact Telegram status alert when tracked Smart Money signals hit TP1/TP2/SL."""
    lines = [
        f"🔔 <b>SMART MONEY SIGNAL UPDATES</b> · {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
        "",
    ]
    for ev in events:
        sym = ev["symbol"].replace("USDT", "/USDT")
        et = ev["event_type"]
        fill = _fmt_p(ev["fill_price"])
        if et == "TP1_HIT":
            be = _fmt_p(ev["new_stop"])
            lines.append(
                f"🎯 <b>{sym}</b> ({ev['signal_type']}) — <b>TP1 HIT at {fill}</b>\n"
                f"   <i>Action: Take 50% profit & move SL to Breakeven ({be})</i>"
            )
        elif et == "TP2_EXECUTED_FULL":
            lines.append(
                f"🏆 <b>{sym}</b> ({ev['signal_type']}) — <b>TP2 FULL TARGET HIT at {fill}</b> "
                f"(<b>{ev['pnl_pct']:+.2f}%</b> / {ev['r_multiple']:+.2f}R)"
            )
        elif et == "BREAKEVEN_STOPPED_OUT":
            lines.append(
                f"🛡️ <b>{sym}</b> — Remainder closed at <b>Breakeven ({fill})</b> after TP1 "
                f"(Net <b>{ev['pnl_pct']:+.2f}%</b>)"
            )
        elif et == "TRAILING_STOPPED_OUT":
            lines.append(
                f"🔒 <b>{sym}</b> — <b>Trailing Stop Hit at {fill}</b> "
                f"(Net <b>{ev['pnl_pct']:+.2f}%</b> / {ev['r_multiple']:+.2f}R)"
            )
        elif et == "TIME_STOP_EXIT":
            lines.append(
                f"⏱️ <b>{sym}</b> — <b>Time Stop ({ev['bars_held']}h) at {fill}</b> "
                f"(Net <b>{ev['pnl_pct']:+.2f}%</b>)"
            )
        else:
            lines.append(
                f"🛑 <b>{sym}</b> ({ev['signal_type']}) — <b>Stop Loss Hit at {fill}</b> "
                f"(<b>{ev['pnl_pct']:+.2f}%</b>)"
            )

    if stats.get("total_closed", 0) >= 3:
        lines.extend([
            "",
            f"📈 <i>Smart Money Track Record ({stats['total_closed']} closed): "
            f"Win {stats['win_rate_pct']:.0f}% · TP1 Hit {stats['tp1_hit_rate_pct']:.0f}% · "
            f"PF {stats['profit_factor']:.2f}</i>",
        ])
    return "\n".join(lines)
