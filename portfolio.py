#!/usr/bin/env python3
"""Crypto-only portfolio P&L tracking from config.json holdings."""

from botutil import esc
from markets import binance_price


def portfolio_rows(holdings):
    """Return crypto-only holding rows."""
    rows = []
    for h in holdings:
        sym = str(h.get("symbol", "")).upper()
        if not sym:
            continue
        qty = float(h.get("qty", 0))
        avg = float(h.get("avg_price", 0))
        price = binance_price(sym)
        if price is None or price <= 0:
            continue
        val = price * qty
        pnl_usd = (price - avg) * qty if avg > 0 else 0.0
        pnl_pct = (price - avg) / avg * 100 if avg > 0 else 0.0
        rows.append({"symbol": sym, "market": "crypto", "qty": qty, "avg": avg,
                     "price": price, "val": val, "pnl_usd": pnl_usd,
                     "pnl_pct": pnl_pct})
    rows.sort(key=lambda x: x["val"], reverse=True)
    return rows


def format_portfolio(rows):
    lines = []
    if not rows:
        return ["   (no crypto holdings configured — edit config.json → holdings)"]
    lines.append("")
    lines.append("💰 <b>CRYPTO PORTFOLIO</b>")
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
