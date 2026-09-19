# Production Operations Runbook

## Safety baseline

The repository baseline is intentionally **SPOT-only and fail-closed**.

Live execution requires both `config.json`: `execution_live_enabled=true` and runtime `EXECUTION_ENABLED=true`.

A kill switch blocks live execution: `EXECUTION_KILL_SWITCH=true` or `execution_kill_switch=true`.

The baseline keeps live execution, market orders, and withdrawals disabled.

## Pre-live checklist

1. Run `python production_readiness.py`.
2. Run `python -m unittest discover -s tests -v`.
3. Confirm CI is green on the target commit.
4. Confirm exchange API credentials use minimum required permissions.
5. Keep withdrawal permissions disabled unless separately reviewed.
6. Confirm the exchange allowlist contains only intended SPOT venues.
7. Review per-order, active-intent, and daily-notional caps.
8. Keep market orders disabled unless separately reviewed.
9. Confirm Telegram credentials and authorized chat/user controls.
10. Start with the smallest approved notional and observe reconciliation.

## Emergency stop

Set `EXECUTION_KILL_SWITCH=true` in the runtime environment. Execution preflight must then reject live order and withdrawal validation.

After an emergency stop, do not restart orders automatically. Reconcile persisted provider order IDs and transfers, verify destination balances before a second leg, and inspect execution intents and the trade journal before resuming.

## Unknown order state

An exchange order with an unknown provider status must not be resubmitted automatically. Reconcile the existing provider order first.

## Transfer state

A transfer must not release the sell leg until destination deposit/balance is independently confirmed and the transferred amount covers the intended sell quantity.

## Observability

Important state files include `state/execution_intents.jsonl`, `state/two_leg_intents.jsonl`, `state/trade_journal.jsonl`, `state/signal_history.jsonl`, and `state/shadow_trades.jsonl`.

Telegram is a notification/control channel, not a substitute for backend authorization or revalidation.

## Signal policy

There is **no daily BUY-signal quota**. The scanner emits every signal that passes the configured quality gates and emits none when no setup qualifies.

The configured target-potential range is 5%–80%. This is a target-distance filter, not a promise of profit or win probability.

Supported BUY labels are `Scalp (5m)`, `Scalp (15m)`, and `Small Trade (1h)`.

Do not change the established Telegram BUY message format without an explicit product decision.

## Safe rollback

1. Stop the live process.
2. Activate the execution kill switch.
3. Reconcile open provider orders and transfers.
4. Restore the reviewed commit.
5. Run the readiness audit and tests.
6. Only then consider reactivation.

Rollback must never rely on simply restarting the process because persisted execution state and provider state require reconciliation.
