# SLS Historical Research Runbook

This runbook describes how to collect Binance Spot candles and evaluate the native SLS BUY-only strategy. It does not imply that SLS is profitable or that any target win rate has been achieved.

## 1. Data collection

The downloader writes the JSON schema consumed by `sls_backtest.py`, stores UTC candle-open timestamps in milliseconds, includes BTCUSDT automatically, resumes existing files, and is intended to retain closed candles only.

Example: collect a broad five-year daily/weekly/monthly dataset:

```bash
python download_sls_history.py \
  --start 2021-01-01 --end 2026-01-01 \
  --symbols BTC ETH SOL BNB XRP ADA DOGE \
  --intervals 1d 1w 1M \
  --output data/sls_candles.json
```

Collect intraday data in separate passes so the date range and resulting file size can be controlled:

```bash
python download_sls_history.py \
  --start 2025-01-01 --end 2026-01-01 \
  --symbols BTC ETH SOL BNB XRP ADA DOGE \
  --intervals 5m 15m 1h 4h \
  --output data/sls_candles.json
```

The end date is exclusive. Reuse the same output path to add intervals or extend the requested range. The downloader validates symbols against Binance Spot exchange metadata. It does not use futures data.

For robust evaluation, choose the period and symbol universe before looking at the results. Do not remove losing symbols or unfavorable dates after seeing OOS performance.

## 2. Data quality checks

Before interpreting performance, inspect the `dataset_quality` section of the replay report:

- Per-symbol and per-interval candle counts and observed date range.
- Internal missing-candle counts and sample gap locations.
- BTC benchmark history availability.
- Primary timeframe EMA200 history readiness and HTF EMA50 readiness.

The quality report detects internal gaps. It cannot establish whether the dataset is missing candles before its first or after its last timestamp unless the requested date bounds are also supplied and compared.

Do not treat an interval as validated when its BTC benchmark or required HTF data is unavailable. Missing benchmark data must fail closed.

## 3. Run the replay

```bash
python sls_backtest.py data/sls_candles.json \
  --sensitivity Balanced \
  --fee-rate 0.001 \
  --slippage-rate 0.0005 \
  --train-fraction 0.7 \
  --output reports/sls_replay_report.json
```

Fee and slippage rates are fractions per side. The defaults model 0.10% fees and 0.05% slippage per side; replace them with realistic assumptions for the account, exchange, order type, and historical period being studied.

The replay uses one global chronological split across symbols and timeframes. Signals at the same timestamp stay on the same side of the split. Training observations whose outcome horizon crosses the cutoff are purged.

## 4. Execution and outcome assumptions

- Signals are evaluated only after the signal candle has closed.
- Historical execution is modelled at the next candle open, not the signal candle close.
- A gap through the stop is filled at the worse opening price.
- If a stop and target are both touched inside one OHLC candle, the stop is assumed to occur first.
- TP1/TP2/TP3 hit rates are reach statistics. The current replay does not model partial exits or a portfolio allocation ladder.
- Expired trades are marked using the final close in their horizon. Insufficient future history is censored and excluded from resolved outcome denominators.
- The replay is a signal-level study, not a full portfolio backtest. Overlapping signals, capital constraints, correlation, order-book depth, market impact, and exchange execution are not fully simulated.

## 5. Read the results correctly

Review each timeframe independently and include:

- Number of signals and resolved observations.
- TP3-versus-stop win rate and TP1/TP2/TP3 hit rates.
- Stop, expiry, and censored counts.
- Net average return and expectancy after fees/slippage.
- Average win, average loss, and profit factor.
- Training and out-of-sample results separately.

A high win rate with a small sample, negative expectancy, or poor profit factor is not evidence of a robust strategy. Do not tune thresholds against the OOS set and then continue to call that set out-of-sample. If parameters change after OOS inspection, reserve a new untouched period for the next evaluation.

No 70–80% win-rate claim should be made unless it is supported by a sufficiently large, representative, independently validated sample and positive net expectancy.

## 6. Remaining parity requirement

The Python implementation is a native approximation of the Pine Script indicator, not yet certified as a line-by-line equivalent. A complete parity audit requires the exact source version of the Pine Script and a set of matched candle fixtures. Compare every component, mandatory gate, timeframe rule, confirmed-pivot behavior, alert edge, stop, and target on identical closed candles before treating replay results as evidence for the TradingView indicator itself.
