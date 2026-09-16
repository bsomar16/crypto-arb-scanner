# Phase 5 — Real-time SPOT market data and controlled-arbitrage foundation

## What is now implemented

- Persistent websocket market-data daemon: `realtime_arb.py`
- Normalized SPOT BBO feeds for Binance, Bybit, OKX, Bitget and MEXC: `realtime_market.py`
- Cross-exchange opportunity engine: `realtime_opportunity.py`
- Executable-notional check using live best bid/ask quantities
- Freshness guard (default 1500 ms)
- Configurable SPOT taker fees and slippage reserve
- Optional transfer-route checks for deposit/withdraw enablement and compatible networks
- Hard SPOT-only execution guard: `execution_guard.py`
- Alert-only behavior by default; no order is submitted in Phase 5
- Runtime dependency isolated in `requirements-runtime.txt`
- Example systemd service: `deploy/realtime-arb.service.example`
- Unit coverage: `tests/test_realtime_phase5.py`

## Run locally

```bash
python -m pip install -r requirements-runtime.txt
python realtime_arb.py
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` if Telegram alerts are wanted. Without them, opportunities are logged locally.

## Important deployment distinction

GitHub Actions is still used for scheduled scans, reports and backtests. It is not the 24/7 websocket runtime. The real-time daemon should run on a VPS/container/VM with automatic restart.

## Safety model

Phase 5 is **monitoring + dry-run only**. Future execution must pass the SPOT-only guard and require explicit confirmation for each specific opportunity. A withdrawal must additionally require explicit withdrawal confirmation and a live check of deposit/withdraw status, supported networks and network compatibility.

The fee values in `config.json` are configurable assumptions, not a statement of the user's account-specific fee tier. They should be replaced by authenticated exchange fee data before live execution.

## Current market-data scope

The normalized feed consumes best-bid/best-ask data. This is sufficient for fast spread detection but is not a full-depth execution simulation. Before live execution, Phase 6 should add synchronized depth snapshots/deltas, slippage-by-notional, min-notional/precision filters, authenticated balances, order acknowledgements, and live deposit/withdraw/network metadata.
