# crypto-arb-scanner

24/7 cross-exchange arbitrage scanner with Telegram alerts. Runs free on GitHub Actions (cron every 15 min, 24/7) and wires deliver to your Telegram via a bot token.

## What it does

- Pulls USDT spot prices from **8 exchanges**: Binance, Bitget, OKX, Gate, MEXC, Poloniex, KuCoin, HTX
- Compares coins present on **4+ exchanges**
- Alerts to Telegram when a pair clears a **+8% win after taker fees** (new candidate, filtered against known traps with an expiry window)
- Each alert shows just the **BUY & SELL legs**: price, order-book depth and **deposit/withdraw + network status**
- Tracks **virtual positions** on daily BUY/STRONG BUY picks: SL / TP1 / TP2 / TP3 crossings and expiry are alerted separately, and outcomes feed the historical log
- Keeps a **historical log** (`state/run_log.jsonl`) of every spread alert, daily pick and position event — aggregated in a `--mode report`
- Intraday BUY signals come in two tiers: **🟢 EARLY MOVERS** (15m, wide ~120-coin bin, current-hour-volume floored) and **🟩 STRONG / 🟡 NEW** (1h, top-60 by 24h volume) — so a coin rising *now* is caught before it climbs the 24h volume ranking

## Modes

```
python scanner.py --mode daily       # 08:00 UTC report + opens virtual positions
python scanner.py --mode buy         # intraday BUY signals (every hour)
python scanner.py --mode arb         # every 15 min: scan silently, Telegram only on +8% win
python scanner.py --mode check       # follow-up: positions SL/TP + recent-spread recheck
python scanner.py --mode report      # history summary (text or --report-format html)
python scanner.py --mode price       # config price alerts
python scanner.py --mode backtest    # weekly strategy backtest
python scanner.py --mode all         # daily + buy
```

`--json-logs` switches the console output to one JSON object per line.

## Setup

1. **Create the Telegram bot** — message [@BotFather](https://t.me/BotFather) on Telegram: `/newbot`, pick a name + username. Paste the token it gives you.

2. **Get your chat ID** — message your new bot once, then open in a browser:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Your chat id is the `"id"` field in `"chat"`. (Negative numbers are normal for groups.)

3. **Set repo secrets** (the workflow reads these):

   ```
   gh secret set TELEGRAM_BOT_TOKEN -R bsomar16/crypto-arb-scanner
   gh secret set TELEGRAM_CHAT_ID -R bsomar16/crypto-arb-scanner
   ```

4. **First run** — the cron fires every 15 min, but trigger an immediate test via the Actions tab (Workflow dispatch → "Crypto Signal Bot" → Run workflow), or from CLI:

   ```
   gh workflow run scanner.yml -R bsomar16/crypto-arb-scanner -f mode=buy
   ```

## Changing frequency / threshold

- Frequency: edit `cron` in `.github/workflows/scanner.yml`. Note GitHub's cron has 5-min granularity.
- Alert threshold / min exchanges / alert cap: env vars or `config.json`:

  | Setting | Env var | Default |
  | --- | --- | --- |
  | Alert threshold % | `SPREAD_ALERT_PCT` | 1.0 |
  | Min exchanges for a coin | `MIN_EXCHANGES` | 4 |
  | Max alerts per run | `MAX_ALERTS_PER_RUN` | 10 |
  | Trap expiry (days) | `TRAP_EXPIRY_DAYS` | 7 |
  | Position stop-loss | `STOPLOSS_PCT` | 0.05 (5%) |
  | TP1 / TP2 / TP3 | `TP1_PCT` `TP2_PCT` `TP3_PCT` | 5% / 10% / 20% |
  | Position expiry (days) | `POSITION_EXPIRY_DAYS` | 14 |
  | Max open positions | `MAX_OPEN_POSITIONS` | 40 |
  | Per-exchange taker fee | `FEE_BINANCE`, `FEE_GATE`, … | ~0.10-0.25% |
  | Early-mover bin size | `BUY_FAST_TOP_N` | 120 |
  | Early-mover hour-vol floor ($) | `BUY_FAST_MIN_HOUR_VOL` | 300000 |
  | Early-mover vol spike min | `BUY_FAST_MIN_VOL_X` | 1.8 |

  Example: `SPREAD_ALERT_PCT=1.5 MIN_EXCHANGES=5 python scanner.py --mode arb`

## Cost

Free. Public repo = unlimited GitHub Actions minutes. Each scheduled run lasts ~30-60s.

## Limitations

- Binance deposit/withdraw status needs an API key (not available here) — verify chain withdrawals in-app before acting. The alert marks those venues `🔒 API privée`.
- Alerts are candidates to investigate, not guaranteed profits: check real order-book depth + withdrawal fees before executing.
- Virtual positions use Binance pricing as a proxy; fee handling is approximate (taker-fee defaults, no maker rebates / transfer costs).