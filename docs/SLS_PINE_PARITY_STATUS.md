# SLS Pine port — implementation status

The scanner now routes SLS evaluation through `sls_engine.py`, using the Pine-compatible EMA, RSI, MACD and ATR series from `sls_pine_math.py`.

## Implemented in this iteration
- Closed-candle-only primary input.
- Previous-closed HTF candle filtering using candle close boundaries (including calendar-month boundaries).
- BTC benchmark context is mandatory; missing BTC data fails closed.
- Sensitivity thresholds and C1–C8 point schedules from the supplied SLS specification.
- Regime classification, pivot-based HL/HH structure, BOS detection, OB/FVG zone tracking, equal-low/sweep tracking, divergence state, liquidity/extension/stop gates.
- BUY payload remains explicitly attributed to SLS and contains the five target levels.
- SPOT BUY only; no order execution or short-side logic.

## Known parity limitations — do not treat as exact TradingView equivalence yet
- The original Pine source file is not committed in this repository, so the port is based on the supplied rule specification. Input defaults not included in that specification (notably `sepReq`, exact pivot selection details and any Pine-specific NA/tie semantics) must be checked against the original script.
- Added `sls_position.py` as a deterministic long-only lifecycle component: entry lock, stop-first intrabar ordering, independent TP1–TP5 events, all-target close, and timeout. It is not yet persisted or wired into the live scanner across process restarts or historical batches.
- The C8 RR confluence interpretation and C7 resistance-room calculation need fixture comparison against Pine output.
- The strategy needs executable CI evidence and exported TradingView fixtures before parity can be claimed.
- No win-rate, profitability, or expected-return claim is made.

## Required parity acceptance
1. Export OHLCV + Pine values for multiple symbols/timeframes, including warm-up, pivots, equal lows, BOS, OB/FVG, divergence, stop/target collisions, and timeout cases.
2. Compare each C1–C8 component, regime, mandatory gate, BUY event, stop and target to Python.
3. Resolve every mismatch and retain regression fixtures.
4. Run historical out-of-sample validation with fees, slippage, gaps and survivorship controls before publishing performance metrics.


## Persistent position lifecycle update
- `sls_position_store.py` persists SLS-only positions in `state/sls_positions.json`.
- `scanner.py` advances each open position using newly closed candles, records TP1-TP5, gives STOP priority, enforces the configured bar lifetime, and suppresses repeated BUY candidates for the same coin/timeframe while the position remains open.
- State is research/outcome tracking only; no order placement or exchange execution is performed.
- The deployment must retain the `state/` directory between scheduled runs. If the workflow does not persist this file (for example via a durable volume or state artifact), lifecycle state and the lock reset on the next run.
- TP/STOP events are logged and persisted; this integration does not send exit Telegram alerts.
- Tests cover registration/locking, stop priority, target progression, and duplicate candle replay. Execution is pending CI.
