# crypto-arb-scanner

Crypto-only SPOT signal scanner, cross-exchange SPOT arbitrage intelligence, Telegram notifications, paper/shadow tracking, backtesting, and controlled execution infrastructure.

## Core safety policy

**SPOT ONLY.** The system does not support margin, futures, perpetuals, leverage, options, borrowing, shorting, or other derivatives.

Live execution is fail-closed and requires both the repository/runtime policy gate and environment gate. The repository baseline keeps live execution, market orders, and withdrawals disabled.

## Signal engine

BUY signals use **Scalp (5m)**, **Scalp (15m)**, and **Small Trade (1h)** profiles. The engine combines multi-timeframe structure, EMA/MACD/RSI/ATR, volume acceleration, entry confirmation, early-expansion classification, market regime, market quality, cross-exchange context, historical outcomes, adaptive thresholds, correlation controls, and staged target quality.

The configured target-potential range is **5%–80%**. This is a target-distance filter, not a guaranteed return.

There is **no artificial daily signal quota**. Every candidate that passes the configured quality gates can be emitted; when none qualifies, no BUY signal is sent.

## Telegram

Telegram is used for signal notifications and controlled execution confirmation. A Telegram button does not authorize an order by itself; the backend must still validate the intent, safety policy, freshness, and execution constraints.

The established BUY signal format is intentionally stable.

## Development and operations

Run tests with `python -m unittest discover -s tests -v`.
Run the production baseline audit with `python production_readiness.py`.
See `docs/PRODUCTION_OPERATIONS.md` for the controlled-live checklist, emergency stop, reconciliation, observability, and rollback procedure.

## Important limitation

Signals and historical/backtest statistics are analytical outputs, not guarantees of profit. Real execution depends on exchange state, liquidity, fees, slippage, network availability, and transfer timing.
