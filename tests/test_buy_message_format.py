import unittest

from botutil import _format_buy_message


class BuyMessageFormatTests(unittest.TestCase):
    def test_formats_confirmed_buy_signal(self):
        raw = "\n".join([
            "🎯 <b>CRYPTO BUY SIGNALS</b> · 2026-09-17 17:00 UTC",
            "🔎 100 liquid coins · 300 timeframe scans · 5m/15m/1h",
            "",
            "<b>#1</b>",
            "🟢 <b>CONFIRMED BUY SIGNAL</b>",
            "🚀 <b>GALA</b> · SCALP · 15m",
            "Setup: <b>MOMENTUM</b> · 4h: Mixed",
            "Action: <b>BUY</b>",
            "Entry: <b>$0.00183</b> · Stop: $0.00178489",
            "T1: $0.00187511 · T2: $0.00192021 · T3: $0.00195029",
            "Potential: <b>+6.6%</b> · Risk: 2.53% · R:R 2.60",
            "Score: <b>73/100</b> · RSI 65 · volume ×3.09 · 24h +16.0%",
            "Why: EMA structure &amp; MACD bullish, RSI healthy, strong volume.",
        ])
        out = _format_buy_message(raw)
        self.assertIn("🟢 <b>CONFIRMED BUY SIGNAL | $GALA</b> 🚀", out)
        self.assertIn("⏱️ <b>Scalp (15m)</b> | ⚡ <b>Setup:</b> MOMENTUM", out)
        self.assertIn("<b>SL:</b> $0.00178489 ⛔", out)
        self.assertIn("⏳ <b>TP1:</b> $0.00187511", out)
        self.assertIn("📈 <b>Potential:</b> +6.6% | 📉 <b>Risk:</b> 2.53% | ⚖️ <b>R:R:</b> 2.60", out)
        self.assertIn("⭐ <b>Score:</b> 73/100", out)
        self.assertIn("RSI 65 | Vol ×3.09 | 24h +16.0% | 4h: Mixed", out)

    def test_non_buy_messages_are_unchanged(self):
        text = "📊 <b>ARB SCAN</b>"
        self.assertEqual(_format_buy_message(text), text)


if __name__ == "__main__":
    unittest.main()
