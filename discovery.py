#!/usr/bin/env python3
"""Wide-market crypto discovery and price-structure helpers.

The discovery layer is intentionally cheap: 24h ticker data narrows the Binance
USDT SPOT universe, then 1h + 15m acceleration finds candidates before expensive
multi-timeframe analysis.

IMPROVED: Now supports multi-exchange discovery (Binance, Bybit, OKX, MEXC, 
Gate, KuCoin, HTX, Bitget) and pump potential filtering (>40%).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean
from typing import Dict, List, Optional, Tuple

import indicators as ind
from botutil import http_json, log

BN = "https://data-api.binance.vision"

# Exchange endpoints for 24h ticker data
EXCHANGE_TICKER_ENDPOINTS = {
    "BINANCE": "https://data-api.binance.vision/api/v3/ticker/24hr",
    "BYBIT": "https://api.bybit.com/v5/market/tickers?category=spot",
    "OKX": "https://www.okx.com/api/v5/market/tickers?instType=SPOT",
    "MEXC": "https://api.mexc.com/api/v3/ticker/24hr",
    "GATE": "https://api.gateio.ws/api/v4/spot/tickers",
    "KUCOIN": "https://api.kucoin.com/api/v1/market/allTickers",
    "HTX": "https://api.huobi.pro/market/tickers",
    "BITGET": "https://api.bitget.com/api/v2/spot/market/tickers",
}

# Exchange klines endpoints (for structure analysis)
EXCHANGE_KLINES_ENDPOINTS = {
    "BINANCE": "https://data-api.binance.vision/api/v3/klines",
    "BYBIT": "https://api.bybit.com/v5/market/kline",
    "OKX": "https://www.okx.com/api/v5/market/candles",
    "MEXC": "https://api.mexc.com/api/v3/klines",
    "GATE": "https://api.gateio.ws/api/v4/spot/candlesticks",
    "KUCOIN": "https://api.kucoin.com/api/v1/market/candles",
    "HTX": "https://api.huobi.pro/market/history/kline",
    "BITGET": "https://api.bitget.com/api/v2/spot/market/candles",
}

# Interval mappings for each exchange
INTERVAL_MAP = {
    "BINANCE": {"1h": "1h", "15m": "15m", "5m": "5m", "4h": "4h", "1d": "1d", "1w": "1w"},
    "BYBIT": {"1h": "60", "15m": "15", "5m": "5", "4h": "240", "1d": "D", "1w": "W"},
    "OKX": {"1h": "1H", "15m": "15m", "5m": "5m", "4h": "4H", "1d": "1D", "1w": "1W"},
    "MEXC": {"1h": "1h", "15m": "15m", "5m": "5m", "4h": "4h", "1d": "1d", "1w": "1w"},
    "GATE": {"1h": "1h", "15m": "15m", "5m": "5m", "4h": "4h", "1d": "1d", "1w": "1w"},
    "KUCOIN": {"1h": "1hour", "15m": "15min", "5m": "5min", "4h": "4hour", "1d": "1day", "1w": "1week"},
    "HTX": {"1h": "60min", "15m": "15min", "5m": "5min", "4h": "4hour", "1d": "1day", "1w": "1week"},
    "BITGET": {"1h": "1H", "15m": "15m", "5m": "5m", "4h": "4H", "1d": "1D", "1w": "1W"},
}

# Symbol format for each exchange
SYMBOL_FORMAT = {
    "BINANCE": "{coin}USDT",
    "BYBIT": "{coin}USDT",
    "OKX": "{coin}-USDT",
    "MEXC": "{coin}USDT",
    "GATE": "{coin}_USDT",
    "KUCOIN": "{coin}-USDT",
    "HTX": "{coin}usdt",
    "BITGET": "{coin}USDT",
}

# Stablecoins to exclude
STABLECOINS = {"USDT", "FDUSD", "BUSD", "USDC", "USDP", "TUSD", "DAI", "FRAX", "LUSD", "UST", "USDD", "USDN", "USDK", "USDP", "GUSD", "HUSD", "XUSD", "MIM", "ALUSD", "MAI", "EURS", "EURT", "EURC"}


def _normalize_symbol(exchange: str, coin: str) -> str:
    """Normalize symbol format for a given exchange."""
    fmt = SYMBOL_FORMAT.get(exchange, "{coin}USDT")
    return fmt.format(coin=coin.upper())


def _parse_exchange_tickers(exchange: str, data) -> Dict[str, Tuple[float, float]]:
    """Parse ticker data from exchange response. Returns {coin: (quote_volume, price_change_pct)}."""
    result = {}
    try:
        if exchange == "BINANCE":
            for t in data:
                s = t.get("symbol", "")
                if s.endswith("USDT") and s != "USDTUSDT":
                    coin = s[:-4]
                    if coin not in STABLECOINS and "USDT" not in coin:
                        try:
                            qv = float(t.get("quoteVolume", 0))
                            chg = float(t.get("priceChangePercent", 0))
                            result[coin] = (qv, chg)
                        except (TypeError, ValueError):
                            pass
        elif exchange == "BYBIT":
            for t in data.get("result", {}).get("list", []):
                s = str(t.get("symbol", ""))
                if s.endswith("USDT"):
                    coin = s[:-4]
                    if coin not in STABLECOINS:
                        try:
                            qv = float(t.get("turnover24h", 0))
                            chg = float(t.get("price24hPcnt", 0)) * 100
                            result[coin] = (qv, chg)
                        except (TypeError, ValueError):
                            pass
        elif exchange == "OKX":
            for t in data.get("data", []):
                s = t.get("instId", "")
                if s.endswith("-USDT"):
                    coin = s[:-5]
                    if coin not in STABLECOINS:
                        try:
                            qv = float(t.get("volCcy24h", 0))
                            chg = float(t.get("chg24h", 0)) * 100
                            result[coin] = (qv, chg)
                        except (TypeError, ValueError):
                            pass
        elif exchange == "MEXC":
            for t in data:
                s = t.get("symbol", "")
                if s.endswith("USDT") and s != "USDTUSDT":
                    coin = s[:-4]
                    if coin not in STABLECOINS and "USDT" not in coin:
                        try:
                            qv = float(t.get("quoteVolume", 0))
                            chg = float(t.get("priceChangePercent", 0))
                            result[coin] = (qv, chg)
                        except (TypeError, ValueError):
                            pass
        elif exchange == "GATE":
            for t in data:
                cp = t.get("currency_pair", "")
                if cp.endswith("_USDT") and "_" not in cp[:-5]:
                    coin = cp[:-5]
                    if coin not in STABLECOINS:
                        try:
                            qv = float(t.get("quote_volume", 0))
                            chg = float(t.get("change_percentage", 0))
                            result[coin] = (qv, chg)
                        except (TypeError, ValueError):
                            pass
        elif exchange == "KUCOIN":
            for t in data.get("data", {}).get("ticker", []):
                s = t.get("symbol", "")
                if s.endswith("-USDT"):
                    coin = s[:-5]
                    if coin not in STABLECOINS:
                        try:
                            qv = float(t.get("quoteVolume", 0))
                            chg = float(t.get("changeRate", 0)) * 100
                            result[coin] = (qv, chg)
                        except (TypeError, ValueError):
                            pass
        elif exchange == "HTX":
            for t in data.get("data", []):
                s = t.get("symbol", "")
                if s.endswith("usdt"):
                    coin = s[:-4].upper()
                    if coin not in STABLECOINS:
                        try:
                            qv = float(t.get("amount", 0)) * float(t.get("close", 0))
                            chg = float(t.get("pct_change", 0)) * 100
                            result[coin] = (qv, chg)
                        except (TypeError, ValueError):
                            pass
        elif exchange == "BITGET":
            for t in data.get("data", []):
                s = t.get("symbol", "")
                if s.endswith("USDT"):
                    coin = s[:-4]
                    if coin not in STABLECOINS:
                        try:
                            qv = float(t.get("quoteVolume", 0))
                            chg = float(t.get("change24h", 0)) * 100
                            result[coin] = (qv, chg)
                        except (TypeError, ValueError):
                            pass
    except Exception as e:
        log("DISCOVERY", f"Error parsing {exchange} tickers:", e)
    return result


def fetch_exchange_24h(exchange: str) -> Dict[str, Tuple[float, float]]:
    """Fetch 24h ticker data from a specific exchange."""
    url = EXCHANGE_TICKER_ENDPOINTS.get(exchange)
    if not url:
        return {}
    try:
        data = http_json(url, timeout=20)
        return _parse_exchange_tickers(exchange, data)
    except Exception as e:
        log("DISCOVERY", f"Error fetching {exchange} 24h data:", e)
        return {}


def fetch_all_exchanges_24h(cfg) -> Dict[str, Dict[str, Tuple[float, float]]]:
    """Fetch 24h data from all configured exchanges."""
    exchanges = cfg.get("discovery_exchanges", ["BINANCE", "BYBIT", "OKX", "MEXC", "GATE", "KUCOIN", "HTX", "BITGET"])
    results = {}
    with ThreadPoolExecutor(max_workers=len(exchanges)) as ex:
        futures = {ex.submit(fetch_exchange_24h, ex_name): ex_name for ex_name in exchanges}
        for future in as_completed(futures):
            ex_name = futures[future]
            try:
                results[ex_name] = future.result()
                log("DISCOVERY", f"{ex_name}: {len(results[ex_name])} USDT pairs")
            except Exception as e:
                log("DISCOVERY", f"{ex_name} fetch failed:", e)
                results[ex_name] = {}
    return results


def _klines(exchange: str, symbol: str, interval: str = "1h", limit: int = 32):
    """Fetch klines from a specific exchange."""
    url = EXCHANGE_KLINES_ENDPOINTS.get(exchange)
    if not url:
        return None
    mapped_interval = INTERVAL_MAP.get(exchange, {}).get(interval, interval)
    formatted_symbol = _normalize_symbol(exchange, symbol)
    
    try:
        if exchange == "BINANCE":
            params = f"?symbol={formatted_symbol}&interval={mapped_interval}&limit={limit}"
        elif exchange == "BYBIT":
            params = f"?category=spot&symbol={formatted_symbol}&interval={mapped_interval}&limit={limit}"
        elif exchange == "OKX":
            params = f"?instId={formatted_symbol}&bar={mapped_interval}&limit={limit}"
        elif exchange == "MEXC":
            params = f"?symbol={formatted_symbol}&interval={mapped_interval}&limit={limit}"
        elif exchange == "GATE":
            params = f"?currency_pair={formatted_symbol}&interval={mapped_interval}&limit={limit}"
        elif exchange == "KUCOIN":
            params = f"?symbol={formatted_symbol}&type={mapped_interval}&limit={limit}"
        elif exchange == "HTX":
            params = f"?symbol={formatted_symbol}&period={mapped_interval}&size={limit}"
        elif exchange == "BITGET":
            params = f"?symbol={formatted_symbol}&granularity={mapped_interval}&limit={limit}"
        else:
            return None
            
        d = http_json(url + params, timeout=15)
        
        # Normalize response format
        if exchange == "BINANCE":
            return d if len(d) >= 24 else None
        elif exchange == "BYBIT":
            result = d.get("result", {}).get("list", [])
            # BYBIT returns [startTime, open, high, low, close, volume, turnover]
            return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), 0, float(k[6])] for k in result] if len(result) >= 24 else None
        elif exchange == "OKX":
            result = d.get("data", [])
            # OKX returns [ts, o, h, l, c, vol, volCcy, confirm]
            return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), 0, float(k[6])] for k in result] if len(result) >= 24 else None
        elif exchange == "MEXC":
            return d if len(d) >= 24 else None
        elif exchange == "GATE":
            # GATE returns [timestamp, volume, close, high, low, open, ...]
            return [[int(k[0]), float(k[5]), float(k[3]), float(k[4]), float(k[2]), float(k[1]), 0, float(k[1])*float(k[2])] for k in d] if len(d) >= 24 else None
        elif exchange == "KUCOIN":
            result = d.get("data", [])
            return [[int(k[0])*1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), 0, float(k[5])*float(k[4])] for k in result] if len(result) >= 24 else None
        elif exchange == "HTX":
            result = d.get("data", [])
            return [[int(k["id"])*1000, float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"]), float(k["vol"]), 0, float(k["vol"])*float(k["close"])] for k in result] if len(result) >= 24 else None
        elif exchange == "BITGET":
            result = d.get("data", [])
            return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), 0, float(k[5])*float(k[4])] for k in result] if len(result) >= 24 else None
    except Exception as e:
        log("DISCOVERY", f"Error fetching klines for {symbol} on {exchange}:", e)
    return None


def _early_move_score(price_1h: float, price_6h: float, vol_x: float,
                      micro_change_pct: float, micro_vol_x: float,
                      relative: float, compression: float) -> float:
    """Score early expansion without requiring a large 24h move."""
    score = 0.0
    if price_1h > 0:
        score += 14
    if price_6h > 0:
        score += 8
    if micro_change_pct > 0:
        score += min(14, micro_change_pct * 4)
    if micro_vol_x >= 1.15:
        score += min(16, (micro_vol_x - 1) * 16)
    if vol_x >= 1.2:
        score += min(12, (vol_x - 1) * 8)
    if relative > 0:
        score += min(10, relative * 0.7)
    if compression >= 0.15:
        score += min(10, compression * 25)
    return max(0.0, min(100.0, score))


def _pump_potential_score(coin: str, data_1h, data_15m, chg24: float, vol_x: float, 
                          compression: float, rsi: float, cfg) -> Tuple[float, Dict]:
    """
    Calculate pump potential score for >40% moves.
    Returns (score, details_dict)
    """
    if not data_1h or len(data_1h) < 20:
        return 0.0, {"reason": "insufficient_data"}
    
    closed = data_1h[:-1]
    closes = [float(k[4]) for k in closed]
    highs = [float(k[2]) for k in closed]
    lows = [float(k[3]) for k in closed]
    vols = [float(k[5]) for k in closed]
    
    price = closes[-1]
    
    # Calculate key metrics for pump detection
    # 1. Distance from recent high (room to grow)
    recent_high = max(highs[-20:])
    distance_from_high = (recent_high / price - 1) * 100
    
    # 2. Volatility expansion (ATR %)
    atr = ind.atr(highs, lows, closes) or price * 0.01
    atr_pct = (atr / price) * 100
    
    # 3. Volume trend (accelerating volume)
    recent_vol = mean(vols[-3:]) if len(vols) >= 3 else vols[-1]
    base_vol = mean(vols[-20:-3]) if len(vols) >= 23 else mean(vols[:-3])
    vol_acceleration = recent_vol / base_vol if base_vol > 0 else 1.0
    
    # 4. Price compression/consolidation
    recent_range = (max(highs[-10:]) - min(lows[-10:])) / price * 100
    prior_range = (max(highs[-30:-10]) - min(lows[-30:-10])) / price * 100 if len(highs) >= 30 else recent_range
    compression_ratio = max(0.0, 1.0 - recent_range / prior_range) if prior_range > 0 else 0
    
    # 5. RSI position (not overbought)
    rsi_score = 0
    if 30 <= rsi <= 60:
        rsi_score = 20  # Ideal accumulation zone
    elif 60 < rsi <= 70:
        rsi_score = 10  # Still room
    elif rsi < 30:
        rsi_score = 15  # Oversold bounce potential
    else:
        rsi_score = -10  # Overbought
    
    # 6. 24h change context (not already pumped)
    chg24_score = 0
    if chg24 < 10:
        chg24_score = 15  # Haven't moved much yet
    elif chg24 < 25:
        chg24_score = 5
    else:
        chg24_score = -10  # Already pumped
    
    # 7. Microstructure (15m momentum)
    micro_score = 0
    if data_15m and len(data_15m) >= 8:
        mc = data_15m[:-1]
        mclose = [float(k[4]) for k in mc]
        mvol = [float(k[5]) for k in mc]
        if len(mclose) >= 3:
            micro_momentum = (mclose[-1] / mclose[-3] - 1) * 100
            if micro_momentum > 0:
                micro_score = min(15, micro_momentum * 2)
    
    # Pump potential score (0-100)
    pump_score = 0
    pump_score += min(25, distance_from_high * 1.5)  # Room to high
    pump_score += min(20, atr_pct * 2)  # Volatility
    pump_score += min(15, (vol_acceleration - 1) * 20) if vol_acceleration > 1 else 0  # Volume surge
    pump_score += min(15, compression_ratio * 50)  # Compression
    pump_score += rsi_score
    pump_score += chg24_score
    pump_score += micro_score
    
    pump_score = max(0.0, min(100.0, pump_score))
    
    details = {
        "distance_from_high_pct": round(distance_from_high, 2),
        "atr_pct": round(atr_pct, 2),
        "vol_acceleration": round(vol_acceleration, 2),
        "compression_ratio": round(compression_ratio, 3),
        "rsi": round(rsi, 1),
        "chg24": round(chg24, 2),
        "pump_score": round(pump_score, 1),
        "pump_tier": "HIGH" if pump_score >= 70 else ("MEDIUM" if pump_score >= 50 else "LOW")
    }
    
    return pump_score, details


def _fast_one(exchange: str, symbol: str, qv: float, chg24: float, btc_chg24: float, cfg):
    """Analyze a single symbol on a specific exchange."""
    data = _klines(exchange, symbol)
    if not data or len(data) < 20:
        return None
    closed = data[:-1]
    if len(closed) < 20:
        return None
    closes = [float(k[4]) for k in closed]
    highs = [float(k[2]) for k in closed]
    lows = [float(k[3]) for k in closed]
    vols = [float(k[5]) for k in closed]

    last = closes[-1]
    prev1 = closes[-2]
    prev6 = closes[-7] if len(closes) >= 7 else closes[0]
    price_1h = (last / prev1 - 1) * 100 if prev1 else 0
    price_6h = (last / prev6 - 1) * 100 if prev6 else 0
    accel = price_1h - price_6h / 6.0

    recent_v = mean(vols[-2:])
    base_v = mean(vols[-22:-2]) if len(vols) >= 22 else mean(vols[:-2])
    vol_x = recent_v / base_v if base_v > 0 else 0

    recent_range = (max(highs[-6:]) - min(lows[-6:])) / last * 100 if last else 0
    prior_range = (max(highs[-18:-6]) - min(lows[-18:-6])) / last * 100 if last else 0
    compression = max(0.0, 1.0 - recent_range / prior_range) if prior_range > 0 else 0

    e9 = ind.ema(closes, 9)[-1]
    e20 = ind.ema(closes, 20)[-1]
    rsi = ind.rsi(closes, 14) or 50
    atr = ind.atr(highs, lows, closes) or last * 0.01
    atr_pct = atr / last * 100 if last else 0
    relative = chg24 - btc_chg24

    base_score = 0.0
    if e9 > e20:
        base_score += 18
    if price_1h > 0:
        base_score += 12
    if price_6h > 0:
        base_score += 10
    if relative > 0:
        base_score += min(12, relative * 0.8)
    if vol_x >= 1.2:
        base_score += min(18, (vol_x - 1) * 12)
    if compression >= 0.15:
        base_score += min(12, compression * 30)
    if 45 <= rsi <= 68:
        base_score += 10
    elif 68 < rsi <= 75:
        base_score += 5
    if 0 < atr_pct < 8:
        base_score += 5

    micro = _klines(exchange, symbol, "15m", 12)
    micro_change = 0.0
    micro_vol_x = 0.0
    if micro and len(micro) >= 8:
        mc = micro[:-1]
        mclose = [float(k[4]) for k in mc]
        mvol = [float(k[5]) for k in mc]
        if len(mclose) >= 2 and mclose[-2]:
            micro_change = (mclose[-1] / mclose[-2] - 1) * 100
        if len(mvol) >= 8:
            micro_base = mean(mvol[-8:-2])
            micro_recent = mean(mvol[-2:])
            micro_vol_x = micro_recent / micro_base if micro_base > 0 else 0.0

    early_score = _early_move_score(
        price_1h, price_6h, vol_x, micro_change, micro_vol_x, relative, compression
    )
    score = 0.75 * min(100.0, base_score) + 0.25 * early_score

    # Pump potential analysis
    pump_score, pump_details = _pump_potential_score(symbol, data, micro, chg24, vol_x, compression, rsi, cfg)

    return {
        "coin": symbol,
        "exchange": exchange,
        "qv": qv,
        "chg24": chg24,
        "price_1h_pct": price_1h,
        "price_6h_pct": price_6h,
        "acceleration_pct": accel,
        "vol_x": vol_x,
        "compression": compression,
        "relative_strength": relative,
        "rsi": rsi,
        "micro_15m_change_pct": micro_change,
        "micro_15m_vol_x": micro_vol_x,
        "early_move_score": early_score,
        "score": max(0.0, min(100.0, score)),
        "pump_score": pump_score,
        "pump_details": pump_details,
    }


def discover(t24, cfg):
    """Return a broad, ranked discovery universe plus the final deep-scan pool.
    
    IMPROVED: Now supports multi-exchange discovery and pump potential filtering.
    """
    # If t24 is provided (legacy Binance-only), use it; otherwise fetch from all exchanges
    if t24 and isinstance(t24, list):
        # Legacy mode: Binance only
        rows = []
        for x in t24 or []:
            symbol = str(x.get("symbol", ""))
            if not symbol.endswith("USDT") or symbol == "USDTUSDT":
                continue
            coin = symbol[:-4]
            if any(q in coin for q in STABLECOINS):
                continue
            try:
                qv = float(x.get("quoteVolume", 0))
                chg = float(x.get("priceChangePercent", 0))
            except (TypeError, ValueError):
                continue
            if qv >= float(cfg.get("discovery_min_quote_volume", 500000)):
                rows.append((coin, qv, chg))
        
        rows.sort(key=lambda x: x[1], reverse=True)
        max_universe = int(cfg.get("discovery_universe_size", 600))
        fast_scan_size = max(1, min(max_universe, int(cfg.get("discovery_fast_scan_size", 300))))
        rows = rows[:fast_scan_size]
        btc_chg = next((r[2] for r in rows if r[0] == "BTC"), 0.0)

        workers = int(cfg.get("discovery_workers", 24))
        with ThreadPoolExecutor(max_workers=max(4, workers)) as ex:
            futures = [ex.submit(_fast_one, "BINANCE", c, qv, chg, btc_chg, cfg) for c, qv, chg in rows]
            results = []
            for f in futures:
                result = f.result()
                if result:
                    results.append(result)
    else:
        # New multi-exchange mode
        all_exchange_data = fetch_all_exchanges_24h(cfg)
        
        # Aggregate across exchanges: keep highest volume per coin
        coin_best = {}
        for ex_name, ex_data in all_exchange_data.items():
            for coin, (qv, chg) in ex_data.items():
                if coin not in coin_best or qv > coin_best[coin][0]:
                    coin_best[coin] = (qv, chg, ex_name)
        
        # Filter by minimum quote volume
        min_qv = float(cfg.get("discovery_min_quote_volume", 500000))
        rows = [(coin, qv, chg, ex) for coin, (qv, chg, ex) in coin_best.items() if qv >= min_qv]
        
        rows.sort(key=lambda x: x[1], reverse=True)
        max_universe = int(cfg.get("discovery_universe_size", 600))
        fast_scan_size = max(1, min(max_universe, int(cfg.get("discovery_fast_scan_size", 300))))
        rows = rows[:fast_scan_size]
        btc_chg = next((r[2] for r in rows if r[0] == "BTC"), 0.0)

        workers = int(cfg.get("discovery_workers", 24))
        with ThreadPoolExecutor(max_workers=max(4, workers)) as ex:
            futures = [ex.submit(_fast_one, ex, c, qv, chg, btc_chg, cfg) for c, qv, chg, ex in rows]
            results = []
            for f in futures:
                result = f.result()
                if result:
                    results.append(result)

    # Apply pump potential filter if enabled
    min_pump_score = cfg.get("discovery_min_pump_score", 0)
    if min_pump_score > 0:
        results = [r for r in results if r.get("pump_score", 0) >= min_pump_score]
        log("DISCOVERY", f"After pump filter (>= {min_pump_score}): {len(results)} candidates")

    # Sort by combined score (discovery + pump potential)
    pump_weight = cfg.get("discovery_pump_weight", 0.3)
    results.sort(key=lambda r: (
        -(r["score"] * (1 - pump_weight) + r.get("pump_score", 0) * pump_weight),
        -r.get("pump_score", 0),
        -r["qv"]
    ))
    
    deep_n = int(cfg.get("discovery_deep_candidates", 100))
    return results, results[:deep_n]


def structure_snapshot(closes, highs, lows):
    """Detect simple swing structure, BOS/CHOCH and liquidity levels."""
    if len(closes) < 30:
        return {"state": "UNKNOWN", "bos": False, "choch": False,
                "liquidity_sweep": False, "higher_lows": False,
                "compression": 0.0, "score": 0.0}

    def pivots(values, left=2, right=2, high=True):
        out = []
        for i in range(left, len(values) - right):
            window = values[i-left:i+right+1]
            v = values[i]
            if v == (max(window) if high else min(window)):
                out.append((i, v))
        return out

    ph = pivots(highs, high=True)
    pl = pivots(lows, high=False)
    last_ph = ph[-3:]
    last_pl = pl[-3:]

    higher_highs = len(last_ph) >= 2 and last_ph[-1][1] > last_ph[-2][1]
    higher_lows = len(last_pl) >= 2 and last_pl[-1][1] > last_pl[-2][1]
    if not higher_lows and len(lows) >= 8:
        earlier_low = min(lows[-8:-4])
        recent_low = min(lows[-4:])
        higher_lows = recent_low > earlier_low
    lower_highs = len(last_ph) >= 2 and last_ph[-1][1] < last_ph[-2][1]
    lower_lows = len(last_pl) >= 2 and last_pl[-1][1] < last_pl[-2][1]

    prior_high = max(highs[-13:-3])
    prior_low = min(lows[-13:-3])
    price = closes[-1]
    bos = price > prior_high
    sweep = lows[-3] < prior_low and price > prior_low
    bullish = higher_lows and (higher_highs or bos)
    bearish = lower_highs and lower_lows
    choch = sweep and bullish

    recent_range = (max(highs[-6:]) - min(lows[-6:])) / price if price else 0
    prior_range = (max(highs[-18:-6]) - min(lows[-18:-6])) / price if price else 0
    compression = max(0.0, 1.0 - recent_range / prior_range) if prior_range else 0.0

    score = 0.0
    if higher_lows:
        score += 18
    if higher_highs:
        score += 10
    if bos:
        score += 22
    if choch:
        score += 18
    if sweep:
        score += 15
    if compression > 0.15:
        score += min(12, compression * 30)
    if bearish:
        score -= 15

    state = "BULLISH" if bullish else ("BEARISH" if bearish else "MIXED")
    return {
        "state": state,
        "bos": bos,
        "choch": choch,
        "liquidity_sweep": sweep,
        "higher_lows": higher_lows,
        "higher_highs": higher_highs,
        "compression": round(compression, 4),
        "prior_high": prior_high,
        "prior_low": prior_low,
        "score": max(0.0, min(100.0, score)),
    }