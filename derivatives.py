#!/usr/bin/env python3
"""Derivatives data: funding rates, open interest, liquidations, basis.

Provides leading indicators for spot signals from perp markets.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from botutil import http_json, log
from statistics import mean

# Perp endpoints for funding rates & open interest
FUNDING_ENDPOINTS = {
    "BINANCE": "https://fapi.binance.com/fapi/v1/premiumIndex",
    "BYBIT": "https://api.bybit.com/v5/market/tickers?category=linear",
    "OKX": "https://www.okx.com/api/v5/public/funding-rate",
    "MEXC": "https://contract.mexc.com/api/v1/contract/funding_rate_v2",
    "BITGET": "https://api.bitget.com/api/v2/mix/market/funding-rate",
}

OI_ENDPOINTS = {
    "BINANCE": "https://fapi.binance.com/fapi/v1/openInterest",
    "BYBIT": "https://api.bybit.com/v5/market/open-interest",
    "OKX": "https://www.okx.com/api/v5/public/open-interest",
    "MEXC": "https://contract.mexc.com/api/v1/contract/open_interest",
    "BITGET": "https://api.bitget.com/api/v2/mix/market/open-interest",
}

LIQUIDATION_ENDPOINTS = {
    "BINANCE": "https://fapi.binance.com/futures/data/liquidationOrders",
    "BYBIT": "https://api.bybit.com/v5/market/liquidation-records",
    "OKX": "https://www.okx.com/api/v5/public/liquidation-orders",
}

# Stablecoins to exclude
STABLECOINS = {"USDT", "FDUSD", "BUSD", "USDC", "USDP", "TUSD", "DAI", "FRAX", "LUSD"}


def _parse_binance_funding(data) -> Dict[str, Dict]:
    """Parse Binance funding rate response."""
    result = {}
    for item in data:
        symbol = item.get("symbol", "")
        if symbol.endswith("USDT") and symbol != "USDTUSDT":
            coin = symbol[:-4]
            if coin not in STABLECOINS:
                try:
                    result[coin] = {
                        "funding_rate": float(item.get("lastFundingRate", 0)) * 100,  # to %
                        "mark_price": float(item.get("markPrice", 0)),
                        "index_price": float(item.get("indexPrice", 0)),
                        "next_funding_time": int(item.get("nextFundingTime", 0)),
                        "exchange": "BINANCE"
                    }
                except (TypeError, ValueError):
                    pass
    return result


def _parse_bybit_funding(data) -> Dict[str, Dict]:
    """Parse Bybit funding rate response."""
    result = {}
    for item in data.get("result", {}).get("list", []):
        symbol = item.get("symbol", "")
        if symbol.endswith("USDT"):
            coin = symbol[:-4]
            if coin not in STABLECOINS:
                try:
                    result[coin] = {
                        "funding_rate": float(item.get("fundingRate", 0)) * 100,
                        "mark_price": float(item.get("markPrice", 0)),
                        "index_price": float(item.get("indexPrice", 0)),
                        "next_funding_time": int(item.get("nextFundingTime", 0)),
                        "exchange": "BYBIT"
                    }
                except (TypeError, ValueError):
                    pass
    return result


def _parse_okx_funding(data) -> Dict[str, Dict]:
    """Parse OKX funding rate response."""
    result = {}
    for item in data.get("data", []):
        inst_id = item.get("instId", "")
        if inst_id.endswith("-USDT"):
            coin = inst_id[:-5]
            if coin not in STABLECOINS:
                try:
                    result[coin] = {
                        "funding_rate": float(item.get("fundingRate", 0)) * 100,
                        "mark_price": float(item.get("markPx", 0)),
                        "index_price": float(item.get("indexPx", 0)),
                        "next_funding_time": int(item.get("nextFundingTime", 0)),
                        "exchange": "OKX"
                    }
                except (TypeError, ValueError):
                    pass
    return result


def _parse_mexc_funding(data) -> Dict[str, Dict]:
    """Parse MEXC funding rate response."""
    result = {}
    for item in data.get("data", []):
        symbol = item.get("symbol", "")
        if symbol.endswith("_USDT"):
            coin = symbol[:-5]
            if coin not in STABLECOINS:
                try:
                    result[coin] = {
                        "funding_rate": float(item.get("fundingRate", 0)) * 100,
                        "mark_price": float(item.get("markPrice", 0)),
                        "index_price": float(item.get("indexPrice", 0)),
                        "next_funding_time": int(item.get("nextFundingTime", 0)),
                        "exchange": "MEXC"
                    }
                except (TypeError, ValueError):
                    pass
    return result


def _parse_bitget_funding(data) -> Dict[str, Dict]:
    """Parse Bitget funding rate response."""
    result = {}
    for item in data.get("data", []):
        symbol = item.get("symbol", "")
        if symbol.endswith("USDT"):
            coin = symbol[:-4]
            if coin not in STABLECOINS:
                try:
                    result[coin] = {
                        "funding_rate": float(item.get("fundingRate", 0)) * 100,
                        "mark_price": float(item.get("markPrice", 0)),
                        "index_price": float(item.get("indexPrice", 0)),
                        "next_funding_time": int(item.get("nextFundingTime", 0)),
                        "exchange": "BITGET"
                    }
                except (TypeError, ValueError):
                    pass
    return result


FUNDING_PARSERS = {
    "BINANCE": _parse_binance_funding,
    "BYBIT": _parse_bybit_funding,
    "OKX": _parse_okx_funding,
    "MEXC": _parse_mexc_funding,
    "BITGET": _parse_bitget_funding,
}


def fetch_funding_rates(exchanges: List[str] = None) -> Dict[str, Dict[str, Dict]]:
    """Fetch funding rates from all configured exchanges."""
    exchanges = exchanges or ["BINANCE", "BYBIT", "OKX", "MEXC", "BITGET"]
    results = {}
    
    with ThreadPoolExecutor(max_workers=len(exchanges)) as ex:
        futures = {}
        for ex_name in exchanges:
            url = FUNDING_ENDPOINTS.get(ex_name)
            if not url:
                continue
            futures[ex.submit(http_json, url, 15)] = ex_name
        
        for future in as_completed(futures):
            ex_name = futures[future]
            try:
                data = future.result()
                parser = FUNDING_PARSERS.get(ex_name)
                if parser:
                    results[ex_name] = parser(data)
                    log("DERIVATIVES", f"{ex_name}: {len(results[ex_name])} funding rates")
            except Exception as e:
                log("DERIVATIVES", f"{ex_name} funding fetch failed:", e)
                results[ex_name] = {}
    
    return results


def _parse_binance_oi(data) -> Dict[str, float]:
    """Parse Binance open interest."""
    result = {}
    for item in data:
        symbol = item.get("symbol", "")
        if symbol.endswith("USDT") and symbol != "USDTUSDT":
            coin = symbol[:-4]
            if coin not in STABLECOINS:
                try:
                    result[coin] = float(item.get("openInterest", 0))
                except (TypeError, ValueError):
                    pass
    return result


def _parse_bybit_oi(data) -> Dict[str, float]:
    """Parse Bybit open interest."""
    result = {}
    for item in data.get("result", {}).get("list", []):
        symbol = item.get("symbol", "")
        if symbol.endswith("USDT"):
            coin = symbol[:-4]
            if coin not in STABLECOINS:
                try:
                    result[coin] = float(item.get("openInterest", 0))
                except (TypeError, ValueError):
                    pass
    return result


def _parse_okx_oi(data) -> Dict[str, float]:
    """Parse OKX open interest."""
    result = {}
    for item in data.get("data", []):
        inst_id = item.get("instId", "")
        if inst_id.endswith("-USDT"):
            coin = inst_id[:-5]
            if coin not in STABLECOINS:
                try:
                    result[coin] = float(item.get("oi", 0)) * float(item.get("oiCcy", 0))
                except (TypeError, ValueError):
                    pass
    return result


def _parse_mexc_oi(data) -> Dict[str, float]:
    """Parse MEXC open interest."""
    result = {}
    for item in data.get("data", []):
        symbol = item.get("symbol", "")
        if symbol.endswith("_USDT"):
            coin = symbol[:-5]
            if coin not in STABLECOINS:
                try:
                    result[coin] = float(item.get("openInterest", 0))
                except (TypeError, ValueError):
                    pass
    return result


def _parse_bitget_oi(data) -> Dict[str, float]:
    """Parse Bitget open interest."""
    result = {}
    for item in data.get("data", []):
        symbol = item.get("symbol", "")
        if symbol.endswith("USDT"):
            coin = symbol[:-4]
            if coin not in STABLECOINS:
                try:
                    result[coin] = float(item.get("openInterest", 0))
                except (TypeError, ValueError):
                    pass
    return result


OI_PARSERS = {
    "BINANCE": _parse_binance_oi,
    "BYBIT": _parse_bybit_oi,
    "OKX": _parse_okx_oi,
    "MEXC": _parse_mexc_oi,
    "BITGET": _parse_bitget_oi,
}


def fetch_open_interest(exchanges: List[str] = None) -> Dict[str, Dict[str, float]]:
    """Fetch open interest from all configured exchanges."""
    exchanges = exchanges or ["BINANCE", "BYBIT", "OKX", "MEXC", "BITGET"]
    results = {}
    
    with ThreadPoolExecutor(max_workers=len(exchanges)) as ex:
        futures = {}
        for ex_name in exchanges:
            url = OI_ENDPOINTS.get(ex_name)
            if not url:
                continue
            futures[ex.submit(http_json, url, 15)] = ex_name
        
        for future in as_completed(futures):
            ex_name = futures[future]
            try:
                data = future.result()
                parser = OI_PARSERS.get(ex_name)
                if parser:
                    results[ex_name] = parser(data)
                    log("DERIVATIVES", f"{ex_name}: {len(results[ex_name])} OI symbols")
            except Exception as e:
                log("DERIVATIVES", f"{ex_name} OI fetch failed:", e)
                results[ex_name] = {}
    
    return results


def fetch_liquidations(exchanges: List[str] = None, hours: int = 4) -> Dict[str, List[Dict]]:
    """Fetch recent liquidation data."""
    exchanges = exchanges or ["BINANCE", "BYBIT", "OKX"]
    results = {}
    
    with ThreadPoolExecutor(max_workers=len(exchanges)) as ex:
        futures = {}
        for ex_name in exchanges:
            url = LIQUIDATION_ENDPOINTS.get(ex_name)
            if not url:
                continue
            # Add time parameter for recent liquidations
            if ex_name == "BINANCE":
                url += f"?limit=1000"
            elif ex_name == "BYBIT":
                url += f"?category=linear&limit=1000"
            elif ex_name == "OKX":
                url += f"?instType=SWAP&limit=1000"
            futures[ex.submit(http_json, url, 20)] = ex_name
        
        for future in as_completed(futures):
            ex_name = futures[future]
            try:
                data = future.result()
                results[ex_name] = _parse_liquidations(ex_name, data)
                log("DERIVATIVES", f"{ex_name}: {len(results[ex_name])} liquidations")
            except Exception as e:
                log("DERIVATIVES", f"{ex_name} liquidation fetch failed:", e)
                results[ex_name] = []
    
    return results


def _parse_liquidations(exchange: str, data) -> List[Dict]:
    """Parse liquidation data into standardized format."""
    result = []
    try:
        if exchange == "BINANCE":
            for item in data:
                symbol = item.get("s", "")
                if symbol.endswith("USDT"):
                    coin = symbol[:-4]
                    result.append({
                        "coin": coin,
                        "side": item.get("S", ""),  # BUY/SELL
                        "quantity": float(item.get("q", 0)),
                        "price": float(item.get("p", 0)),
                        "time": int(item.get("T", 0)),
                        "exchange": exchange
                    })
        elif exchange == "BYBIT":
            for item in data.get("result", {}).get("list", []):
                symbol = item.get("symbol", "")
                if symbol.endswith("USDT"):
                    coin = symbol[:-4]
                    result.append({
                        "coin": coin,
                        "side": item.get("side", ""),
                        "quantity": float(item.get("qty", 0)),
                        "price": float(item.get("price", 0)),
                        "time": int(item.get("time", 0)),
                        "exchange": exchange
                    })
        elif exchange == "OKX":
            for item in data.get("data", []):
                inst_id = item.get("instId", "")
                if inst_id.endswith("-USDT"):
                    coin = inst_id[:-5]
                    result.append({
                        "coin": coin,
                        "side": item.get("side", ""),
                        "quantity": float(item.get("sz", 0)),
                        "price": float(item.get("px", 0)),
                        "time": int(item.get("ts", 0)),
                        "exchange": exchange
                    })
    except Exception as e:
        log("DERIVATIVES", f"Error parsing {exchange} liquidations:", e)
    return result


def aggregate_funding(funding_data: Dict[str, Dict[str, Dict]]) -> Dict[str, Dict]:
    """Aggregate funding rates across exchanges, keeping most liquid."""
    aggregated = {}
    for ex_name, ex_data in funding_data.items():
        for coin, data in ex_data.items():
            if coin not in aggregated or abs(data.get("funding_rate", 0)) > abs(aggregated[coin].get("funding_rate", 0)):
                aggregated[coin] = data
    return aggregated


def aggregate_oi(oi_data: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """Aggregate open interest across exchanges (sum)."""
    aggregated = {}
    for ex_name, ex_data in oi_data.items():
        for coin, oi in ex_data.items():
            aggregated[coin] = aggregated.get(coin, 0) + oi
    return aggregated


def compute_basis(spot_price: float, mark_price: float) -> float:
    """Compute basis (futures premium/discount) in %."""
    if spot_price <= 0:
        return 0.0
    return ((mark_price / spot_price) - 1) * 100


def compute_funding_apy(funding_rate: float, intervals_per_day: int = 3) -> float:
    """Convert 8h funding rate to APY."""
    # funding_rate is in % (e.g., 0.01% per 8h)
    rate_per_interval = funding_rate / 100  # to decimal
    apy = (1 + rate_per_interval) ** (intervals_per_day * 365) - 1
    return apy * 100  # back to %


def get_derivatives_signal(coin: str, 
                          funding_data: Dict[str, Dict],
                          oi_data: Dict[str, float],
                          spot_price: float,
                          spot_change_24h: float) -> Dict:
    """
    Generate derivatives-based signal for a coin.
    Returns dict with signal components.
    """
    signal = {
        "coin": coin,
        "funding_rate": None,
        "funding_apy": None,
        "open_interest": None,
        "oi_change_24h": None,
        "basis": None,
        "basis_signal": "NEUTRAL",
        "funding_signal": "NEUTRAL",
        "oi_signal": "NEUTRAL",
        "composite_score": 0,
        "signals": []
    }
    
    # Get funding rate (use aggregated)
    funding = funding_data.get(coin)
    if funding:
        fr = funding.get("funding_rate", 0)
        signal["funding_rate"] = fr
        signal["funding_apy"] = compute_funding_apy(fr)
        signal["basis"] = compute_basis(spot_price, funding.get("mark_price", spot_price))
        
        # Funding signal
        if fr > 0.05:  # > 0.05% per 8h = bullish leverage
            signal["funding_signal"] = "LONG_BIAS"
            signal["composite_score"] += 15
            signal["signals"].append(f"High funding ({fr:.3f}%)")
        elif fr < -0.02:  # negative funding = shorts pay longs
            signal["funding_signal"] = "SHORT_BIAS"
            signal["composite_score"] -= 10
            signal["signals"].append(f"Negative funding ({fr:.3f}%)")
    
    # Basis signal
    basis = signal["basis"]
    if basis > 1.0:  # futures > 1% premium
        signal["basis_signal"] = "RICH"
        signal["composite_score"] -= 10  # mean reversion risk
        signal["signals"].append(f"Rich basis ({basis:.2f}%)")
    elif basis < -0.5:  # futures discount
        signal["basis_signal"] = "CHEAP"
        signal["composite_score"] += 10  # potential short squeeze
        signal["signals"].append(f"Cheap basis ({basis:.2f}%)")
    
    # Open interest signal
    oi = oi_data.get(coin, 0)
    signal["open_interest"] = oi
    
    # OI + price divergence analysis (would need historical OI)
    # For now, use OI magnitude
    if oi > 100_000_000:  # > $100M OI
        signal["oi_signal"] = "HIGH_LIQUIDITY"
        signal["composite_score"] += 5
    elif oi > 50_000_000:
        signal["oi_signal"] = "GOOD_LIQUIDITY"
        signal["composite_score"] += 3
    
    # Spot + derivatives confluence
    if spot_change_24h > 10 and signal["funding_signal"] == "LONG_BIAS":
        signal["composite_score"] += 20
        signal["signals"].append("Spot rally + bullish funding")
    elif spot_change_24h < -10 and signal["funding_signal"] == "SHORT_BIAS":
        signal["composite_score"] -= 15
        signal["signals"].append("Spot drop + bearish funding")
    
    # Clamp score
    signal["composite_score"] = max(-100, min(100, signal["composite_score"]))
    
    return signal


def enrich_discovery_with_derivatives(discovery_results: List[Dict],
                                      funding_data: Dict[str, Dict],
                                      oi_data: Dict[str, float],
                                      spot_prices: Dict[str, float],
                                      spot_changes: Dict[str, float]) -> List[Dict]:
    """Add derivatives signals to discovery results."""
    enriched = []
    for result in discovery_results:
        coin = result.get("coin")
        if not coin:
            continue
        
        spot_price = spot_prices.get(coin, 0)
        spot_change = spot_changes.get(coin, 0)
        
        deriv_signal = get_derivatives_signal(coin, funding_data, oi_data, spot_price, spot_change)
        
        # Add derivatives fields to discovery result
        result["funding_rate"] = deriv_signal["funding_rate"]
        result["funding_apy"] = deriv_signal["funding_apy"]
        result["open_interest"] = deriv_signal["open_interest"]
        result["basis"] = deriv_signal["basis"]
        result["basis_signal"] = deriv_signal["basis_signal"]
        result["funding_signal"] = deriv_signal["funding_signal"]
        result["oi_signal"] = deriv_signal["oi_signal"]
        result["derivatives_score"] = deriv_signal["composite_score"]
        result["derivatives_signals"] = deriv_signal["signals"]
        
        # Boost discovery score with derivatives
        if deriv_signal["composite_score"] > 0:
            result["score"] = min(100, result["score"] + deriv_signal["composite_score"] * 0.2)
        
        enriched.append(result)
    
    # Re-sort by new score
    enriched.sort(key=lambda r: (-r.get("score", 0), -r.get("qv", 0)))
    return enriched