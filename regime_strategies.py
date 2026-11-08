#!/usr/bin/env python3
"""Market regime detection and regime-specific strategy selection.

Implements adaptive strategy weighting based on market conditions.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

import indicators as ind
from botutil import http_json, log


@dataclass
class RegimeConfig:
    """Configuration for a specific market regime."""
    name: str
    # Strategy weights (sum should be ~1.0)
    breakout_weight: float = 1.0
    pullback_weight: float = 1.0
    reversal_weight: float = 1.0
    momentum_weight: float = 1.0
    # Risk parameters
    position_size_mult: float = 1.0
    stop_atr_mult: float = 1.0
    target_atr_mult: float = 1.0
    max_positions: int = 5
    # Threshold adjustments
    min_score_adj: float = 0.0
    min_vol_x_adj: float = 0.0
    min_rr_adj: float = 0.0
    # Setup preferences
    preferred_setups: List[str] = None
    avoided_setups: List[str] = None
    
    def __post_init__(self):
        if self.preferred_setups is None:
            self.preferred_setups = []
        if self.avoided_setups is None:
            self.avoided_setups = []


# Regime-specific configurations
REGIME_CONFIGS = {
    "STRONG_BULL": RegimeConfig(
        name="STRONG_BULL",
        breakout_weight=1.5,
        pullback_weight=1.2,
        reversal_weight=0.3,
        momentum_weight=1.3,
        position_size_mult=1.2,
        stop_atr_mult=1.2,
        target_atr_mult=1.3,
        max_positions=7,
        min_score_adj=-3,
        min_vol_x_adj=-0.1,
        preferred_setups=["BREAKOUT", "MOMENTUM", "PULLBACK"],
        avoided_setups=["REVERSAL"]
    ),
    "BULL": RegimeConfig(
        name="BULL",
        breakout_weight=1.2,
        pullback_weight=1.1,
        reversal_weight=0.5,
        momentum_weight=1.1,
        position_size_mult=1.1,
        stop_atr_mult=1.1,
        target_atr_mult=1.1,
        max_positions=6,
        min_score_adj=-2,
        preferred_setups=["BREAKOUT", "MOMENTUM", "PULLBACK"],
        avoided_setups=["REVERSAL"]
    ),
    "NEUTRAL_BULL": RegimeConfig(
        name="NEUTRAL_BULL",
        breakout_weight=1.0,
        pullback_weight=1.0,
        reversal_weight=0.7,
        momentum_weight=1.0,
        position_size_mult=1.0,
        stop_atr_mult=1.0,
        target_atr_mult=1.0,
        max_positions=5,
        preferred_setups=["PULLBACK", "MOMENTUM"],
        avoided_setups=["REVERSAL"]
    ),
    "NEUTRAL": RegimeConfig(
        name="NEUTRAL",
        breakout_weight=0.8,
        pullback_weight=1.0,
        reversal_weight=1.0,
        momentum_weight=0.8,
        position_size_mult=0.9,
        stop_atr_mult=0.9,
        target_atr_mult=0.9,
        max_positions=4,
        min_score_adj=2,
        preferred_setups=["PULLBACK", "REVERSAL"],
        avoided_setups=["BREAKOUT"]
    ),
    "NEUTRAL_BEAR": RegimeConfig(
        name="NEUTRAL_BEAR",
        breakout_weight=0.5,
        pullback_weight=0.8,
        reversal_weight=1.2,
        momentum_weight=0.6,
        position_size_mult=0.8,
        stop_atr_mult=1.1,
        target_atr_mult=0.8,
        max_positions=3,
        min_score_adj=3,
        min_vol_x_adj=0.1,
        preferred_setups=["REVERSAL", "PULLBACK"],
        avoided_setups=["BREAKOUT", "MOMENTUM"]
    ),
    "BEAR": RegimeConfig(
        name="BEAR",
        breakout_weight=0.3,
        pullback_weight=0.5,
        reversal_weight=1.5,
        momentum_weight=0.4,
        position_size_mult=0.6,
        stop_atr_mult=1.3,
        target_atr_mult=0.7,
        max_positions=2,
        min_score_adj=5,
        min_vol_x_adj=0.15,
        preferred_setups=["REVERSAL"],
        avoided_setups=["BREAKOUT", "MOMENTUM", "PULLBACK"]
    ),
    "STRONG_BEAR": RegimeConfig(
        name="STRONG_BEAR",
        breakout_weight=0.1,
        pullback_weight=0.3,
        reversal_weight=1.3,
        momentum_weight=0.2,
        position_size_mult=0.4,
        stop_atr_mult=1.5,
        target_atr_mult=0.5,
        max_positions=1,
        min_score_adj=8,
        min_vol_x_adj=0.2,
        preferred_setups=["REVERSAL"],
        avoided_setups=["BREAKOUT", "MOMENTUM", "PULLBACK"]
    ),
    "HIGH_VOL": RegimeConfig(
        name="HIGH_VOL",
        breakout_weight=1.2,
        pullback_weight=0.8,
        reversal_weight=0.8,
        momentum_weight=1.0,
        position_size_mult=0.7,
        stop_atr_mult=1.5,
        target_atr_mult=1.2,
        max_positions=3,
        min_score_adj=3,
        min_vol_x_adj=0.1,
        min_rr_adj=0.2,
        preferred_setups=["BREAKOUT", "REVERSAL"],
        avoided_setups=["PULLBACK"]
    ),
    "LOW_VOL": RegimeConfig(
        name="LOW_VOL",
        breakout_weight=0.8,
        pullback_weight=1.2,
        reversal_weight=0.8,
        momentum_weight=0.8,
        position_size_mult=1.1,
        stop_atr_mult=0.8,
        target_atr_mult=0.9,
        max_positions=5,
        min_score_adj=-1,
        min_vol_x_adj=-0.05,
        preferred_setups=["PULLBACK", "MOMENTUM"],
        avoided_setups=["BREAKOUT"]
    )
}


class RegimeDetector:
    """Multi-timeframe market regime detection."""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.lookback_days = self.config.get("regime_lookback_days", 30)
        self.breadth_min_qv = self.config.get("regime_breadth_min_qv", 1_000_000)
        self.breadth_limit = self.config.get("regime_breadth_limit", 100)
        self._regime_history = deque(maxlen=100)
        self._current_regime = "NEUTRAL"
        self._regime_confidence = 0.0
    
    def detect_regime(self, tickers_24h: List[Dict] = None) -> Dict:
        """
        Detect current market regime from breadth and trend data.
        Returns regime info with confidence.
        """
        if not tickers_24h:
            tickers_24h = self._fetch_binance_24h()
        
        # Filter liquid USDT pairs
        pairs = []
        for t in tickers_24h:
            sym = t.get("symbol", "")
            if sym.endswith("USDT") and sym != "USDTUSDT":
                coin = sym[:-4]
                if coin in {"USDT", "FDUSD", "BUSD", "USDC", "USDP", "TUSD"}:
                    continue
                try:
                    qv = float(t.get("quoteVolume", 0))
                    chg = float(t.get("priceChangePercent", 0))
                    if qv >= self.breadth_min_qv:
                        pairs.append({"coin": coin, "qv": qv, "chg": chg})
                except (TypeError, ValueError):
                    pass
        
        pairs.sort(key=lambda x: x["qv"], reverse=True)
        pairs = pairs[:self.breadth_limit]
        
        if not pairs:
            return self._default_regime()
        
        # Breadth metrics
        total = len(pairs)
        advancers = sum(1 for p in pairs if p["chg"] > 0)
        decliners = sum(1 for p in pairs if p["chg"] < 0)
        unchanged = total - advancers - decliners
        
        breadth_pct = (advancers - decliners) / total * 100 if total > 0 else 0
        
        # Strong movers
        strong_up = sum(1 for p in pairs if p["chg"] > 5)
        strong_down = sum(1 for p in pairs if p["chg"] < -5)
        
        # Average change weighted by volume
        total_qv = sum(p["qv"] for p in pairs)
        weighted_chg = sum(p["chg"] * p["qv"] for p in pairs) / total_qv if total_qv > 0 else 0
        
        # Get BTC/ETH trend (market leaders)
        btc_chg = next((p["chg"] for p in pairs if p["coin"] == "BTC"), 0)
        eth_chg = next((p["chg"] for p in pairs if p["coin"] == "ETH"), 0)
        
        # Fetch BTC trend data for regime confirmation
        btc_trend = self._get_btc_trend()
        
        # Determine regime
        regime, confidence = self._classify_regime(
            breadth_pct, weighted_chg, btc_chg, eth_chg,
            strong_up, strong_down, btc_trend
        )
        
        # Volatility regime
        vol_regime = self._detect_volatility_regime(pairs)
        
        # Combined regime
        final_regime = regime
        if vol_regime in ("HIGH_VOL", "LOW_VOL") and regime not in ("HIGH_VOL", "LOW_VOL"):
            # Vol regime overrides in extreme cases
            if vol_regime == "HIGH_VOL" and abs(breadth_pct) < 20:
                final_regime = "HIGH_VOL"
            elif vol_regime == "LOW_VOL" and abs(breadth_pct) < 10:
                final_regime = "LOW_VOL"
        
        result = {
            "state": final_regime,
            "confidence": round(confidence, 2),
            "breadth_pct": round(breadth_pct, 2),
            "weighted_chg": round(weighted_chg, 2),
            "advancers": advancers,
            "decliners": decliners,
            "strong_up": strong_up,
            "strong_down": strong_down,
            "btc_chg": btc_chg,
            "eth_chg": eth_chg,
            "vol_regime": vol_regime,
            "btc_trend": btc_trend,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Track history
        self._regime_history.append({
            "regime": final_regime,
            "confidence": confidence,
            "timestamp": result["timestamp"]
        })
        self._current_regime = final_regime
        self._regime_confidence = confidence
        
        return result
    
    def _default_regime(self) -> Dict:
        return {
            "state": "NEUTRAL",
            "confidence": 0.0,
            "breadth_pct": 0.0,
            "weighted_chg": 0.0,
            "advancers": 0,
            "decliners": 0,
            "vol_regime": "NORMAL",
            "error": "No data"
        }
    
    def _fetch_binance_24h(self) -> List[Dict]:
        try:
            return http_json("https://data-api.binance.vision/api/v3/ticker/24hr", timeout=25)
        except Exception as e:
            log("REGIME", "Failed to fetch 24h tickers:", e)
            return []
    
    def _get_btc_trend(self) -> Dict:
        """Get BTC multi-timeframe trend."""
        try:
            data = http_json(
                "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=100",
                timeout=15
            )
            if not data or len(data) < 50:
                return {"state": "UNKNOWN", "score": 0}
            
            closes = [float(k[4]) for k in data]
            highs = [float(k[2]) for k in data]
            lows = [float(k[3]) for k in data]
            
            # EMAs
            e20 = ind.ema(closes, 20)
            e50 = ind.ema(closes, 50)
            e200 = ind.ema(closes, 200)
            
            # MACD
            macd, signal, hist = ind.macd(closes)
            
            # ADX for trend strength
            adx = ind.adx(highs, lows, closes)
            
            current = closes[-1]
            
            # Trend scoring
            score = 0
            if current > e20[-1]: score += 1
            if current > e50[-1]: score += 1
            if current > e200[-1]: score += 2
            if e20[-1] > e50[-1]: score += 1
            if e50[-1] > e200[-1]: score += 1
            if macd[-1] > signal[-1]: score += 1
            if macd[-1] > 0: score += 1
            if adx and adx[-1] > 25: score += 1
            
            if score >= 7:
                state = "STRONG_BULL"
            elif score >= 5:
                state = "BULL"
            elif score >= 3:
                state = "NEUTRAL_BULL"
            elif score >= 1:
                state = "NEUTRAL"
            elif score >= -1:
                state = "NEUTRAL_BEAR"
            elif score >= -3:
                state = "BEAR"
            else:
                state = "STRONG_BEAR"
            
            return {"state": state, "score": score, "adx": adx[-1] if adx else 0}
            
        except Exception as e:
            log("REGIME", "BTC trend fetch error:", e)
            return {"state": "UNKNOWN", "score": 0}
    
    def _classify_regime(self, breadth_pct: float, weighted_chg: float,
                        btc_chg: float, eth_chg: float,
                        strong_up: int, strong_down: int,
                        btc_trend: Dict) -> Tuple[str, float]:
        """Classify regime from metrics."""
        
        btc_state = btc_trend.get("state", "UNKNOWN")
        btc_score = btc_trend.get("score", 0)
        
        # Weight factors
        breadth_weight = 0.3
        weighted_chg_weight = 0.2
        btc_weight = 0.3
        eth_weight = 0.1
        strong_weight = 0.1
        
        # Normalize scores (-1 to 1)
        breadth_score = breadth_pct / 50  # 50% breadth = 1.0
        weighted_score = weighted_chg / 10  # 10% avg = 1.0
        btc_score_norm = btc_score / 8  # max 8
        eth_score = eth_chg / 10
        strong_score = (strong_up - strong_down) / 20
        
        composite = (
            breadth_weight * breadth_score +
            weighted_chg_weight * weighted_score +
            btc_weight * btc_score_norm +
            eth_weight * eth_score +
            strong_weight * strong_score
        )
        
        # Classify
        if composite >= 1.2:
            regime = "STRONG_BULL"
            confidence = min(100, composite * 40)
        elif composite >= 0.6:
            regime = "BULL"
            confidence = min(100, composite * 50)
        elif composite >= 0.2:
            regime = "NEUTRAL_BULL"
            confidence = min(100, composite * 60)
        elif composite >= -0.2:
            regime = "NEUTRAL"
            confidence = min(100, (1 - abs(composite)) * 50)
        elif composite >= -0.6:
            regime = "NEUTRAL_BEAR"
            confidence = min(100, abs(composite) * 60)
        elif composite >= -1.2:
            regime = "BEAR"
            confidence = min(100, abs(composite) * 50)
        else:
            regime = "STRONG_BEAR"
            confidence = min(100, abs(composite) * 40)
        
        return regime, confidence
    
    def _detect_volatility_regime(self, pairs: List[Dict]) -> str:
        """Detect volatility regime from pair movements."""
        if len(pairs) < 20:
            return "NORMAL"
        
        # Use top 20 by volume
        top20 = pairs[:20]
        changes = [abs(p["chg"]) for p in top20]
        avg_change = sum(changes) / len(changes)
        
        # Count extreme moves
        extreme_up = sum(1 for c in changes if c > 15)
        extreme_down = sum(1 for c in changes if c > 15)
        extreme = extreme_up + extreme_down
        
        if avg_change > 8 or extreme > 5:
            return "HIGH_VOL"
        elif avg_change < 3 and extreme == 0:
            return "LOW_VOL"
        return "NORMAL"
    
    def get_regime_config(self, regime: str = None) -> RegimeConfig:
        """Get configuration for current or specified regime."""
        regime = regime or self._current_regime
        return REGIME_CONFIGS.get(regime, REGIME_CONFIGS["NEUTRAL"])
    
    def get_strategy_weights(self, regime: str = None) -> Dict[str, float]:
        """Get strategy weights for regime."""
        config = self.get_regime_config(regime)
        return {
            "breakout": config.breakout_weight,
            "pullback": config.pullback_weight,
            "reversal": config.reversal_weight,
            "momentum": config.momentum_weight
        }
    
    def get_risk_params(self, regime: str = None) -> Dict:
        """Get risk parameters for regime."""
        config = self.get_regime_config(regime)
        return {
            "position_size_mult": config.position_size_mult,
            "stop_atr_mult": config.stop_atr_mult,
            "target_atr_mult": config.target_atr_mult,
            "max_positions": config.max_positions
        }
    
    def get_threshold_adjustments(self, regime: str = None) -> Dict:
        """Get threshold adjustments for regime."""
        config = self.get_regime_config(regime)
        return {
            "min_score": config.min_score_adj,
            "min_vol_x": config.min_vol_x_adj,
            "min_rr": config.min_rr_adj
        }
    
    def is_setup_preferred(self, setup: str, regime: str = None) -> bool:
        """Check if setup is preferred in regime."""
        config = self.get_regime_config(regime)
        return setup in config.preferred_setups
    
    def is_setup_avoided(self, setup: str, regime: str = None) -> bool:
        """Check if setup is avoided in regime."""
        config = self.get_regime_config(regime)
        return setup in config.avoided_setups
    
    def get_regime_history(self, limit: int = 20) -> List[Dict]:
        """Get recent regime history."""
        return list(self._regime_history)[-limit:]
    
    def get_regime_persistence(self) -> Dict:
        """Analyze regime persistence."""
        if len(self._regime_history) < 2:
            return {"current": self._current_regime, "persistent": False}
        
        recent = list(self._regime_history)[-10:]
        current = self._current_regime
        
        same_count = sum(1 for r in recent if r["regime"] == current)
        persistence = same_count / len(recent)
        
        return {
            "current": current,
            "confidence": self._regime_confidence,
            "persistence": persistence,
            "persistent": persistence > 0.7,
            "recent_regimes": [r["regime"] for r in recent]
        }


def enhance_signal_with_regime(signal: Dict, detector: RegimeDetector) -> Dict:
    """Add regime-based adjustments to signal."""
    regime_info = detector.detect_regime()
    regime = regime_info.get("state", "NEUTRAL")
    config = detector.get_regime_config(regime)
    
    signal["market_regime"] = regime
    signal["regime_confidence"] = regime_info.get("confidence", 0)
    signal["regime_config"] = {
        "strategy_weights": detector.get_strategy_weights(regime),
        "risk_params": detector.get_risk_params(regime),
        "threshold_adj": detector.get_threshold_adjustments(regime),
        "preferred": config.preferred_setups,
        "avoided": config.avoided_setups
    }
    
    # Apply strategy weight
    setup = signal.get("setup_type", "MOMENTUM")
    weights = detector.get_strategy_weights(regime)
    setup_weight = weights.get(setup.lower(), 1.0)
    signal["regime_weight"] = setup_weight
    
    # Apply risk params
    risk = detector.get_risk_params(regime)
    signal["regime_position_mult"] = risk["position_size_mult"]
    signal["regime_stop_mult"] = risk["stop_atr_mult"]
    signal["regime_target_mult"] = risk["target_atr_mult"]
    
    # Check if setup is preferred/avoided
    signal["regime_preferred"] = detector.is_setup_preferred(setup, regime)
    signal["regime_avoided"] = detector.is_setup_avoided(setup, regime)
    
    # Apply threshold adjustments
    thresholds = detector.get_threshold_adjustments(regime)
    signal["regime_min_score_adj"] = thresholds["min_score"]
    signal["regime_min_vol_x_adj"] = thresholds["min_vol_x"]
    signal["regime_min_rr_adj"] = thresholds["min_rr"]
    
    return signal