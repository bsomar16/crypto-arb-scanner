"""Factory for the supported authenticated SPOT adapters.

All five supported exchanges are selected explicitly; no exchange is silently substituted.
"""
from __future__ import annotations

from typing import Optional

from exchange_adapter import ExchangeAdapter


def create_spot_adapter(name: str, *, api_key: Optional[str] = None, api_secret: Optional[str] = None, passphrase: Optional[str] = None) -> ExchangeAdapter:
    key = name.strip().lower()
    if key == "binance":
        from adapters.binance import BinanceSpotAdapter
        return BinanceSpotAdapter(api_key, api_secret)
    if key == "bybit":
        from adapters.bybit import BybitSpotAdapter
        return BybitSpotAdapter(api_key, api_secret)
    if key == "okx":
        from adapters.okx import OKXSpotAdapter
        return OKXSpotAdapter(api_key, api_secret, passphrase)
    if key == "bitget":
        from adapters.bitget_spot import BitgetSpotAdapter
        return BitgetSpotAdapter(api_key, api_secret, passphrase)
    if key == "mexc":
        from adapters.mexc_spot import MexcSpotAdapter
        return MexcSpotAdapter(api_key, api_secret)
    raise ValueError(f"No concrete SPOT adapter is installed for exchange: {name}")
