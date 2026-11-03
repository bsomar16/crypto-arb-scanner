"""Pine Script-compatible indicator series primitives for the SLS port.

Series use None where Pine would produce na during warm-up. EMA starts
from the first non-missing source value; RMA uses an SMA seed over length
observations and Wilder recursion thereafter.
"""
from __future__ import annotations
from typing import Iterable, Optional

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
        changes.append(None if src[i] is None or src[i-1] is None else float(src[i])-float(src[i-1]))
    gains = [None if x is None else max(x, 0.0) for x in changes]
    losses = [None if x is None else max(-x, 0.0) for x in changes]
    avg_gain, avg_loss = pine_rma(gains, length), pine_rma(losses, length)
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
    fast_ema, slow_ema = pine_ema(src, fast), pine_ema(src, slow)
    line = [None if a is None or b is None else a-b for a,b in zip(fast_ema, slow_ema)]
    signal_line = pine_ema(line, signal)
    hist = [None if a is None or b is None else a-b for a,b in zip(line, signal_line)]
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
            tr.append(max(float(high[i])-float(low[i]),
                          abs(float(high[i])-float(close[i-1])),
                          abs(float(low[i])-float(close[i-1]))))
    return pine_rma(tr, length)

def confirmed_pivots(values: Iterable[Number], left: int, right: int,
                     low_pivot: bool = True) -> list[tuple[int, float]]:
    if left < 1 or right < 1:
        raise ValueError("left and right must be >= 1")
    src = list(values)
    result = []
    for p in range(left, len(src)-right):
        window = src[p-left:p+right+1]
        if any(x is None for x in window):
            continue
        value = float(src[p])
        before, after = window[:left], window[left+1:]
        if low_pivot:
            valid = all(value < float(x) for x in before) and all(value <= float(x) for x in after)
        else:
            valid = all(value > float(x) for x in before) and all(value >= float(x) for x in after)
        if valid:
            result.append((p, value))
    return result
