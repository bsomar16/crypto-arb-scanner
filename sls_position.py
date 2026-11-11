"""SLS long-only position lifecycle matching Pine Script v9 confirmed-bar ordering.

This is a research signal / outcome state machine only. It never submits orders.
Matches Pine v9:
- Realistic fills (entry at open of bar following buyEvent or candle close).
- Stop priority (low <= stop checked before checking target hits).
- Trailing stop progression:
  * 1 TP hit -> SL moved to Break-Even (entry).
  * 2 TPs hit -> SL moved to TP1.
  * >=3 TPs hit -> SL moved to TP2.
- 5 take-profit targets (each 20% slot).
- Lifetime timeout enforcement (120 bars intraday, 40 bars daily, 12 bars weekly).
- R-multiple outcome tracking accounting for trading fees.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Sequence

C_FEE = 0.002  # 0.2% round-trip fee model
C_SLOT = 0.2   # 20% position per TP level


@dataclass
class SlsPosition:
    entry: float
    stop: float
    targets: Tuple[float, float, float, float, float]
    opened_bar: int
    lifetime_bars: int
    setup_idx: int = -1
    setup_tag: str = ""
    hit: List[bool] = field(default_factory=lambda: [False] * 5)
    realized_r: float = 0.0
    remaining_fraction: float = 1.0
    closed: bool = False
    close_reason: Optional[str] = None
    close_bar: Optional[int] = None
    final_r: float = 0.0


class SlsPositionManager:
    """Manages an active SLS position according to Pine v9 rules."""

    def __init__(self):
        self.position: Optional[SlsPosition] = None

    @property
    def in_position(self) -> bool:
        return self.position is not None and not self.position.closed

    def open(self, entry: float, stop: float, targets: Sequence[float],
             bar_index: int, lifetime_bars: int = 120, setup_idx: int = -1,
             setup_tag: str = "") -> bool:
        if self.in_position or entry <= 0 or stop >= entry or len(targets) != 5:
            return False
        self.position = SlsPosition(
            entry=float(entry),
            stop=float(stop),
            targets=(float(targets[0]), float(targets[1]), float(targets[2]),
                     float(targets[3]), float(targets[4])),
            opened_bar=int(bar_index),
            lifetime_bars=int(lifetime_bars),
            setup_idx=setup_idx,
            setup_tag=setup_tag,
        )
        return True

    def on_confirmed_bar(self, open_p: float, high_p: float, low_p: float,
                         close_p: float, bar_index: int) -> List[Dict[str, Any]]:
        p = self.position
        if p is None or p.closed or bar_index <= p.opened_bar:
            return []

        risk = p.entry - p.stop if (p.entry - p.stop) > 0 else (p.entry * 0.01)
        cost_r = (C_FEE * p.entry / risk) if risk > 0 else 0.0
        events: List[Dict[str, Any]] = []

        # 1. Stop loss check (Pine ordering: Stop wins over targets if touched on same bar)
        if low_p <= p.stop:
            p.closed = True
            is_breakeven = p.stop >= p.entry
            p.close_reason = "TRAIL" if is_breakeven else "STOP"
            p.close_bar = bar_index
            p.final_r = p.realized_r + p.remaining_fraction * ((p.stop - p.entry) / risk) - cost_r
            return [{
                "event": p.close_reason, "price": p.stop, "bar": bar_index,
                "strategy": "SLS", "r_mult": round(p.final_r, 2),
                "setup_idx": p.setup_idx, "setup_tag": p.setup_tag
            }]

        # 2. Targets check (TP1 to TP5)
        n_hit = sum(1 for h in p.hit if h)
        for idx in range(5):
            target = p.targets[idx]
            if not p.hit[idx] and high_p >= target:
                p.hit[idx] = True
                p.realized_r += C_SLOT * ((target - p.entry) / risk)
                p.remaining_fraction -= C_SLOT
                events.append({
                    "event": f"TP{idx + 1}", "price": target, "bar": bar_index,
                    "strategy": "SLS", "setup_idx": p.setup_idx, "setup_tag": p.setup_tag
                })
        
        n_hit_now = sum(1 for h in p.hit if h)
        
        # 3. Dynamic trailing stop update (Pine v9 lines 929-938)
        new_sl = p.targets[1] if n_hit_now >= 3 else p.targets[0] if n_hit_now >= 2 else p.entry if n_hit_now >= 1 else p.stop
        if new_sl > p.stop:
            p.stop = new_sl

        # 4. All targets reached
        if n_hit_now >= 5:
            p.closed = True
            p.close_reason = "ALL_TARGETS"
            p.close_bar = bar_index
            p.final_r = p.realized_r - cost_r
            events.append({
                "event": "CLOSE", "reason": "ALL_TARGETS", "bar": bar_index,
                "strategy": "SLS", "r_mult": round(p.final_r, 2),
                "setup_idx": p.setup_idx, "setup_tag": p.setup_tag
            })
            return events

        # 5. Position lifetime timeout
        if (bar_index - p.opened_bar) > p.lifetime_bars:
            p.closed = True
            p.close_reason = "TIMEOUT"
            p.close_bar = bar_index
            p.final_r = p.realized_r + p.remaining_fraction * ((close_p - p.entry) / risk) - cost_r
            events.append({
                "event": "CLOSE", "reason": "TIMEOUT", "bar": bar_index,
                "strategy": "SLS", "price": close_p, "r_mult": round(p.final_r, 2),
                "setup_idx": p.setup_idx, "setup_tag": p.setup_tag
            })

        return events

    def reset_closed(self):
        if self.position is not None and self.position.closed:
            self.position = None
