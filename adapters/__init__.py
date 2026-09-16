"""Authenticated SPOT exchange adapters."""

from .binance import BinanceSpotAdapter
from .okx import OKXSpotAdapter

__all__ = ["BinanceSpotAdapter", "OKXSpotAdapter"]
