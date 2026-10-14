# crypto-arb-scanner

Crypto-only **SPOT** BUY signal scanner with Telegram notifications and paper position tracking.
Scheduled by GitHub Actions — no external cron service required.

> **Scope:** this repository has been reduced to the BUY signal path. Arbitrage/spread scanning,
> the daily report, backtesting and OOS research, and the (disabled) live-execution
> infrastructure were removed.

## Core safety policy

**SPOT ONLY.** The system does not support margin, futures, perpetuals, leverage, options,
borrowing, shorting, or any other derivatives. Live execution is not part of this repository.

## Pipeline

`.github/workflows/scanner.yml` runs every 15 minutes (`2,17,32,47 * * * *`):

1. `python position_status.py` — paper position tracking: stop-loss / TP milestones / expiry, and Telegram lifecycle alerts.
2. `python scanner.py --mode buy` — multi-timeframe BUY scan.
3. Commits the updated `state/` back to the repository.

For faster stop handling you can additionally run the persistent monitor on a host
(`python position_monitor.py`, default 15s loop) — see `deploy/position-monitor.service.example`.

## BUY signal engine

Two rule-based engines are evaluated for every (coin, timeframe) candidate:

- **`signals.py::intraday_signal()`** — the standard engine. Indicator + structure scoring
  (EMA 9/20/21, MACD, RSI 14, ATR, volume acceleration, BOS / liquidity-sweep / retest entry
  confirmation), with a per-timeframe profile for `5m`, `15m`, `1h`, `4h`, `1d`, `1w`.
- **`abt_strategy.py::evaluate_abt_coin()`** — the Auto Breakout Toolkit family:
  `ABT~` (shakeout + absorption, **WATCH only — never traded**), `ABT*` (demand-zone reversal),
  `ABT` (descending-trendline breakout).

Qualification is a hard gate, not just a score. Rejections are recorded per stage in
`state/buy_audit_latest.json`:

`data_fetch` → `liquidity` → `volume` → `trend_data` → `entry_confirmation` →
`target_potential` → `bearish_trend` → `late_extension` → `adaptive_thresholds` → `target_quality`

- **Entry confirmation** (`entry_engine.py`) requires a structural trigger, not just bullish indicators.
- **Adaptive thresholds** (`adaptive.py`) tighten or relax screening by at most a small bounded
  amount from *live* outcome history (min 20 samples).
- **Target optimization** (`target_quality.py`) makes a bounded adjustment to staged targets from live TP evidence.
- **Ranking modifiers** (market regime, multi-exchange dispersion, component evidence,
  bullish potential) only reorder candidates — they never create or remove a signal.

### Position and TP management

Signals carry technical SL / TP1 / TP2 / TP3 levels into paper tracking. After TP milestones the
protective stop ratchets upward (`move_sl_to_breakeven`), never downward. The paper tracker records
TP milestones; it does not claim partial fills at TP1/TP2.

### Stop-loss semantics

The live/paper tracker evaluates the stop against the exchange's **current last price** at each poll
(`positions._public_price`), and closes at the observed price — not at the stop level. It is neither a
candle-close rule nor an intrabar-low rule, so a wick that pierces the stop between polls is not
detected. Poll cadence is 15 minutes via GitHub Actions, or 15 seconds if `position_monitor.py` runs.

## Configuration

All thresholds and limits live in `config.json` (watchlist, intervals, score/R:R/potential gates,
position sizing, risk caps). `scanner.py` falls back to its internal `DEFAULTS` for any missing key.

Required GitHub secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Tests

```
python -m unittest discover -s tests -v
```
