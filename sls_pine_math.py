"""Pine Script-compatible indicator series primitives for Spot Long Signal Engine v9.

Implements Pine Script v6 exact indicator behavior:
- None represents na during warm-up.
- EMA seeds from first non-missing source value.
- RMA uses SMA seed over length observations, then Wilder recursion.
- Wilder ATR is RMA of True Range.
- RSI uses RMA of gains and losses.
- MACD calculates fast, slow EMA, signal line EMA, and histogram.
- VWAP calculates volume-weighted average price.
- Confirmed pivots enforce right-bars confirmation delay.
"""
from __future__ import annotations
from typing import Iterable, Optional, List, Tuple, Sequence
from datetime import datetime, timezone

Number = Optional[float]


def pine_ema(values: Iterable[Number], length: int) -> list[Number]:
    if length < 1:
        raise ValueError("length must be >= 1")
    alpha = 2.0 / (length + 1.0)
    out: list[Number] = []
    previous = None
    for value in values:
        if value is None:
            out.append(None)
            continue
        current = float(value)
        previous = current if previous is None else alpha * current + (1.0 - alpha) * previous
        out.append(previous)
    return out


def pine_rma(values: Iterable[Number], length: int) -> list[Number]:
    if length < 1:
        raise ValueError("length must be >= 1")
    src = list(values)
    out: list[Number] = [None] * len(src)
    seed = []
    previous = None
    for i, value in enumerate(src):
        if value is None:
            continue
        current = float(value)
        if previous is None:
            seed.append(current)
            if len(seed) == length:
                previous = sum(seed) / length
                out[i] = previous
        else:
            previous = (previous * (length - 1) + current) / length
            out[i] = previous
    return out


def pine_rsi(closes: Iterable[Number], length: int = 14) -> list[Number]:
    src = list(closes)
    changes = [None]
    for i in range(1, len(src)):
        changes.append(None if src[i] is None or src[i-1] is None else float(src[i]) - float(src[i-1]))
    gains = [None if x is None else max(x, 0.0) for x in changes]
    losses = [None if x is None else max(-x, 0.0) for x in changes]
    avg_gain = pine_rma(gains, length)
    avg_loss = pine_rma(losses, length)
    out = []
    for gain, loss in zip(avg_gain, avg_loss):
        if gain is None or loss is None:
            out.append(None)
        elif loss == 0:
            out.append(100.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + gain / loss))
    return out


def pine_macd(closes: Iterable[Number], fast: int = 12, slow: int = 26,
              signal: int = 9) -> tuple[list[Number], list[Number], list[Number]]:
    src = list(closes)
    fast_ema = pine_ema(src, fast)
    slow_ema = pine_ema(src, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(fast_ema, slow_ema)]
    signal_line = pine_ema(line, signal)
    hist = [None if a is None or b is None else a - b for a, b in zip(line, signal_line)]
    return line, signal_line, hist


def pine_atr(highs: Iterable[Number], lows: Iterable[Number],
             closes: Iterable[Number], length: int = 14) -> list[Number]:
    high, low, close = list(highs), list(lows), list(closes)
    if not (len(high) == len(low) == len(close)):
        raise ValueError("OHLC series must have equal lengths")
    tr = [None]
    for i in range(1, len(close)):
        if high[i] is None or low[i] is None or close[i-1] is None:
            tr.append(None)
        else:
            tr.append(max(float(high[i]) - float(low[i]),
                          abs(float(high[i]) - float(close[i-1])),
                          abs(float(low[i]) - float(close[i-1]))))
    return pine_rma(tr, length)


def pine_vwap(hlc3: Iterable[Number], volumes: Iterable[Number],
              timestamps: Optional[Iterable[int]] = None) -> list[Number]:
    """Calculate Pine ta.vwap(hlc3) with daily session resets if timestamps provided."""
    h_list = list(hlc3)
    v_list = list(volumes)
    t_list = list(timestamps) if timestamps is not None else None
    n = len(h_list)
    out: list[Number] = [None] * n
    
    cum_pv = 0.0
    cum_vol = 0.0
    last_day = None
    
    for i in range(n):
        p = h_list[i]
        v = v_list[i]
        if p is None or v is None:
            out[i] = None
            continue
        
        # Check daily reset at 00:00 UTC if timestamps available
        if t_list and t_list[i] is not None:
            dt = datetime.fromtimestamp(t_list[i] / 1000.0, tz=timezone.utc)
            day = (dt.year, dt.month, dt.day)
            if last_day is not None and day != last_day:
                cum_pv = 0.0
                cum_vol = 0.0
            last_day = day
            
        cum_pv += float(p) * float(v)
        cum_vol += float(v)
        out[i] = (cum_pv / cum_vol) if cum_vol > 0 else float(p)
        
    return out


def confirmed_pivots(values: Iterable[Number], left: int, right: int,
                     low_pivot: bool = True) -> list[tuple[int, float]]:
    """Return pivots strictly using right-side confirmation window.

    A pivot at index p is confirmed only when p + right == current bar.
    """
    if left < 1 or right < 1:
        raise ValueError("left and right must be >= 1")
    src = list(values)
    result = []
    for p in range(left, len(src) - right):
        window = src[p - left : p + right + 1]
        if any(x is None for x in window):
            continue
        value = float(src[p])
        before = window[:left]
        after = window[left + 1:]
        if low_pivot:
            valid = all(value < float(x) for x in before) and all(value <= float(x) for x in after)
        else:
            valid = all(value > float(x) for x in before) and all(value >= float(x) for x in after)
        if valid:
            result.append((p, value))
    return result


def delayed_highest(values: Sequence[Number], length: int, delay: int, index: int) -> Optional[float]:
    """Pine ta.highest(values, length)[delay] at the requested index."""
    end = index - delay
    start = end - length + 1
    if start < 0 or end < 0 or end >= len(values):
        return None
    window = [v for v in values[start : end + 1] if v is not None]
    return max(window) if window else None


def delayed_lowest(values: Sequence[Number], length: int, delay: int, index: int) -> Optional[float]:
    """Pine ta.lowest(values, length)[delay] at the requested index."""
    end = index - delay
    start = end - length + 1
    if start < 0 or end < 0 or end >= len(values):
        return None
    window = [v for v in values[start : end + 1] if v is not None]
    return min(window) if window else None


def roc(values: Sequence[Number], length: int, index: int) -> Optional[float]:
    """Pine ta.roc(values, length) at the requested index."""
    if index < length or index >= len(values):
        return None
    curr = values[index]
    past = values[index - length]
    if curr is None or past is None or past == 0:
        return None
    return (float(curr) / float(past) - 1.0) * 100.0
