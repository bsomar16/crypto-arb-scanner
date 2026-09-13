# crypto-arb-scanner

24/7 cross-exchange arbitrage scanner with Telegram alerts. Runs free on GitHub Actions (cron every 15 min, 24/7) and wires deliver to your Telegram via a bot token.

## What it does

- Pulls USDT spot prices from **9 exchanges**: Binance, Bitget, OKX, Gate, MEXC, Bybit, KuCoin, HTX, CoinEx
- Compares coins present on **4+ exchanges**
- Alerts to Telegram when a coin clears a **1% cross-exchange spread** (new candidate, filtered against known stale/collision traps)
- Includes a 0.5-1% watchlist section in the same message

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

4. **First run** — the cron fires every 15 min, but trigger an immediate test via the Actions tab (Workflow dispatch → "Arb Scanner" → Run workflow), or from CLI:

   ```
   gh workflow run scanner.yml -R bsomar16/crypto-arb-scanner
   ```

## Changing frequency / threshold

- Frequency: edit `cron` in `.github/workflows/scanner.yml`. Note GitHub's cron has 5-min granularity.
- Alert threshold: `SPREAD_ALERT_PCT` in `scanner.py`.
- Known traps list: `KNOWN_TRAPS` in `scanner.py` — coins flagged once are parked here to avoid repeat alerts on the same stale artifact.

## Cost

Free. Public repo = unlimited GitHub Actions minutes. Each scheduled run lasts ~30-60s.

## Limitations

- Binance deposit/withdraw status needs an API key (not available here) — verify chain withdrawals in-app before acting.
- Alerts are candidates to investigate, not guaranteed profits: check real order-book depth + withdrawal fees before executing.