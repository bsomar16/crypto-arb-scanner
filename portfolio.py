#!/usr/bin/env python3
"""Portfolio P&L tracking from config.json holdings."""

from botutil import esc
from markets import binance_price, yahoo_quote


def portfolio_rows(holdings):
    """Return list of {symbol, market, qty, avg, price, val, pnl_usd, pnl_pct}."""
    rows = []
    for h in holdings:
        sym = str(h.get("symbol", "")).upper()
        if not sym:
            continue
        qty = float(h.get("qty", 0))
        avg = float(h.get("avg_price", 0))
        market = h.get("market", "crypto")
        price = binance_price(sym) if market == "crypto" else yahoo_quote(h.get("symbol"))
        if price is None or price <= 0:
            continue
        val = price * qty
        pnl_usd = (price - avg) * qty if avg > 0 else 0.0
        pnl_pct = (price - avg) / avg * 100 if avg > 0 else 0.0
        rows.append({"symbol": sym, "market": market, "qty": qty, "avg": avg,
                     "price": price, "val": val, "pnl_usd": pnl_usd,
                     "pnl_pct": pnl_pct})
    rows.sort(key=lambda x: x["val"], reverse=True)
    return rows


def format_portfolio(rows):
    lines = []
    if not rows:
        return ["   (no holdings configured — edit config.json → holdings)"]
    lines.append("")
    lines.append("💰 <b>PORTFOLIO</b>")
    total_val = 0.0
    total_pnl = 0.0
    for r in rows:
        total_val += r["val"]
        total_pnl += r["pnl_usd"]
        emo = "🟢" if r["pnl_pct"] >= 0 else "🔴"
        p = f"${r['price']:,.4f}" if r["price"] < 1 else f"${r['price']:,.2f}"
        lines.append(
            f"   {emo} <b>{esc(r['symbol'])}</b>  {r['qty']:g} @ {p}  "
            f"{r['pnl_pct']:+.1f}%  (${r['pnl_usd']:+,.0f})")
    lines.append(f"   ── total value ${total_val:,.0f} · P&L ${total_pnl:+,.0f}")
    return lines