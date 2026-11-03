"""SLS long-only position lifecycle matching Pine's confirmed-bar ordering.

This is a signal/outcome state machine only. It never submits orders.
"""
from dataclasses import dataclass, field


@dataclass
class SlsPosition:
    entry: float
    stop: float
    targets: tuple[float, float, float, float, float]
    opened_bar: int
    lifetime_bars: int
    hit: list[bool] = field(default_factory=lambda: [False] * 5)
    closed: bool = False
    close_reason: str | None = None
    close_bar: int | None = None


class SlsPositionManager:
    def __init__(self):
        self.position: SlsPosition | None = None

    @property
    def in_position(self):
        return self.position is not None and not self.position.closed

    def open(self, entry, atr, bar_index, lifetime_bars):
        if self.in_position or entry <= 0 or atr <= 0:
            return False
        risk = 1.5 * atr
        self.position = SlsPosition(
            entry=float(entry), stop=float(entry-risk),
            targets=tuple(float(entry+risk*r) for r in (1.5,2.5,4,6,8)),
            opened_bar=int(bar_index), lifetime_bars=int(lifetime_bars))
        return True

    def on_confirmed_bar(self, high, low, bar_index):
        p=self.position
        if p is None or p.closed or bar_index <= p.opened_bar:
            return []
        events=[]
        # Pine ordering: stop wins if stop and any target are touched on same bar.
        if float(low) <= p.stop:
            p.closed=True; p.close_reason="STOP"; p.close_bar=int(bar_index)
            return [{"event":"STOP","price":p.stop,"bar":bar_index,
                     "signal_source":"SLS","strategy":"SLS"}]
        for idx,target in enumerate(p.targets):
            if not p.hit[idx] and float(high) >= target:
                p.hit[idx]=True
                events.append({"event":f"TP{idx+1}","price":target,"bar":bar_index,
                               "signal_source":"SLS","strategy":"SLS"})
        if all(p.hit):
            p.closed=True; p.close_reason="ALL_TARGETS"; p.close_bar=int(bar_index)
            events.append({"event":"CLOSE","reason":"ALL_TARGETS","bar":bar_index,
                           "signal_source":"SLS","strategy":"SLS"})
        elif int(bar_index)-p.opened_bar > p.lifetime_bars:
            p.closed=True; p.close_reason="TIMEOUT"; p.close_bar=int(bar_index)
            events.append({"event":"CLOSE","reason":"TIMEOUT","bar":bar_index,
                           "signal_source":"SLS","strategy":"SLS"})
        return events

    def reset_closed(self):
        if self.position is not None and self.position.closed:
            self.position=None
