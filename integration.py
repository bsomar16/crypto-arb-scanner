#!/usr/bin/env python3
"""Integration layer - ties all advanced modules together.

This module initializes all components and provides a unified interface
for the scanner to access all enhanced features.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from botutil import log, load_json

# Import all enhanced modules
from derivatives import (
    fetch_funding_rates, fetch_open_interest, fetch_liquidations,
    aggregate_funding, aggregate_oi, enrich_discovery_with_derivatives,
    get_derivatives_signal
)
from ws_feeds import create_ws_manager, MarketData
from ml_classifier import get_ml_classifier, enhance_signal_with_ml
from regime_strategies import RegimeDetector, enhance_signal_with_regime
from risk_management import DynamicRiskManager, PositionManager
from correlation_clustering import CorrelationClusterer, enhance_signals_with_correlation
from narrative_tracker import NarrativeTracker, enhance_signal_with_narrative
from attribution_dashboard import get_attribution_engine


@dataclass
class EnhancedSignal:
    """Signal enriched with all advanced features."""
    # Base signal
    base: Dict
    # Derivatives
    funding_rate: float = 0.0
    funding_apy: float = 0.0
    open_interest: float = 0.0
    basis: float = 0.0
    basis_signal: str = "NEUTRAL"
    funding_signal: str = "NEUTRAL"
    oi_signal: str = "NEUTRAL"
    derivatives_score: float = 0.0
    derivatives_signals: List[str] = None
    # ML
    ml_win_prob: float = 0.5
    ml_prediction: str = "UNKNOWN"
    ml_boost: float = 0.0
    # Regime
    market_regime: str = "NEUTRAL"
    regime_confidence: float = 0.0
    regime_weight: float = 1.0
    regime_preferred: bool = False
    regime_avoided: bool = False
    regime_position_mult: float = 1.0
    regime_stop_mult: float = 1.0
    regime_target_mult: float = 1.0
    regime_min_score_adj: float = 0.0
    regime_min_vol_x_adj: float = 0.0
    regime_min_rr_adj: float = 0.0
    # Correlation
    correlation_cluster: int = -1
    cluster_representative: str = ""
    cluster_avg_corr: float = 0.0
    cluster_sector: str = ""
    is_cluster_rep: bool = True
    # Narrative
    narrative_sector: str = "other"
    narrative_category: str = ""
    narrative_active: bool = False
    narrative_momentum: float = 0.0
    narrative_boost: float = 0.0
    active_narrative: str = ""
    narrative_catalysts: List[str] = None
    # Risk
    position_size_usdt: float = 0.0
    risk_usdt: float = 0.0
    kelly_fraction: float = 0.0
    trailing_stop: float = 0.0
    # Attribution
    attribution_factors: Dict = None
    
    def __post_init__(self):
        if self.derivatives_signals is None:
            self.derivatives_signals = []
        if self.narrative_catalysts is None:
            self.narrative_catalysts = []
        if self.attribution_factors is None:
            self.attribution_factors = {}

    def to_dict(self) -> Dict:
        out = dict(self.base)
        for k, v in self.__dict__.items():
            if k != "base":
                out[k] = v
        return out

    def get(self, key, default=None):
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None:
                return val
        return self.base.get(key, default)

    def __getitem__(self, key):
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None:
                return val
        return self.base[key]

    def __setitem__(self, key, value):
        if hasattr(self, key):
            setattr(self, key, value)
        self.base[key] = value

    def __contains__(self, key):
        return hasattr(self, key) or key in self.base


class SignalEnricher:
    """Enriches base signals with all advanced features."""
    
    def __init__(self, config: Dict):
        self.config = config
        
        # Initialize all components
        self.regime_detector = RegimeDetector(config)
        self.ml_classifier = get_ml_classifier(config)
        self.risk_manager = DynamicRiskManager(config)
        self.position_manager = PositionManager(self.risk_manager)
        self.clusterer = CorrelationClusterer(config)
        self.narrative_tracker = NarrativeTracker(config)
        self.attribution_engine = get_attribution_engine(config)
        
        # Data caches
        self._funding_data: Dict = {}
        self._oi_data: Dict = {}
        self._liquidation_data: Dict = {}
        self._spot_prices: Dict = {}
        self._spot_changes: Dict = {}
        self._last_derivatives_fetch = 0
        self._derivatives_ttl = 300  # 5 minutes
        
        # WS Manager (initialized lazily)
        self._ws_manager = None
        self._ws_symbols = set()
    
    def refresh_derivatives(self, symbols: List[str] = None):
        """Refresh derivatives data."""
        now = datetime.now(timezone.utc).timestamp()
        if now - self._last_derivatives_fetch < self._derivatives_ttl:
            return
        
        try:
            self._funding_data = aggregate_funding(fetch_funding_rates())
            self._oi_data = aggregate_oi(fetch_open_interest())
            self._liquidation_data = fetch_liquidations()
            self._last_derivatives_fetch = now
            log("ENRICH", f"Refreshed derivatives: {len(self._funding_data)} funding, {len(self._oi_data)} OI")
        except Exception as e:
            log("ENRICH", f"Derivatives refresh failed: {e}")
    
    def update_spot_data(self, tickers_24h: List[Dict]):
        """Update spot prices and changes."""
        self._spot_prices = {}
        self._spot_changes = {}
        for t in tickers_24h:
            sym = t.get("symbol", "")
            if sym.endswith("USDT") and sym != "USDTUSDT":
                coin = sym[:-4]
                try:
                    self._spot_prices[coin] = float(t.get("lastPrice", 0))
                    self._spot_changes[coin] = float(t.get("priceChangePercent", 0))
                except (TypeError, ValueError):
                    pass
    
    def enrich_signal(self, base_signal: Dict) -> EnhancedSignal:
        """Apply all enrichments to a base signal."""
        if not base_signal:
            return None
        
        # Start with base
        enriched = EnhancedSignal(base=base_signal.copy())
        
        # 1. Derivatives enrichment
        coin = base_signal.get("coin", "")
        if coin:
            spot_price = self._spot_prices.get(coin, base_signal.get("entry", 0))
            spot_change = self._spot_changes.get(coin, base_signal.get("chg24", 0))
            
            deriv_signal = get_derivatives_signal(
                coin, self._funding_data, self._oi_data, spot_price, spot_change
            )
            
            enriched.funding_rate = deriv_signal["funding_rate"] or 0
            enriched.funding_apy = deriv_signal["funding_apy"] or 0
            enriched.open_interest = deriv_signal["open_interest"] or 0
            enriched.basis = deriv_signal["basis"] or 0
            enriched.basis_signal = deriv_signal["basis_signal"]
            enriched.funding_signal = deriv_signal["funding_signal"]
            enriched.oi_signal = deriv_signal["oi_signal"]
            enriched.derivatives_score = deriv_signal["composite_score"]
            enriched.derivatives_signals = deriv_signal["signals"]
        
        # 2. ML enrichment
        enhance_signal_with_ml(enriched.base, self.ml_classifier)
        enriched.ml_win_prob = enriched.base.get("ml_win_prob", 0.5)
        enriched.ml_prediction = enriched.base.get("ml_prediction", "UNKNOWN")
        enriched.ml_boost = enriched.base.get("ml_boost", 0)
        
        # 3. Regime enrichment
        regime_info = self.regime_detector.detect_regime()
        enhance_signal_with_regime(enriched.base, self.regime_detector)
        enriched.market_regime = enriched.base.get("market_regime", "NEUTRAL")
        enriched.regime_confidence = enriched.base.get("regime_confidence", 0)
        enriched.regime_weight = enriched.base.get("regime_weight", 1.0)
        enriched.regime_preferred = enriched.base.get("regime_preferred", False)
        enriched.regime_avoided = enriched.base.get("regime_avoided", False)
        enriched.regime_position_mult = enriched.base.get("regime_position_mult", 1.0)
        enriched.regime_stop_mult = enriched.base.get("regime_stop_mult", 1.0)
        enriched.regime_target_mult = enriched.base.get("regime_target_mult", 1.0)
        enriched.regime_min_score_adj = enriched.base.get("regime_min_score_adj", 0)
        enriched.regime_min_vol_x_adj = enriched.base.get("regime_min_vol_x_adj", 0)
        enriched.regime_min_rr_adj = enriched.base.get("regime_min_rr_adj", 0)
        
        # 3b. Adjust risk manager for regime
        self.risk_manager.adjust_for_regime(enriched.market_regime)
        
        # 4. Correlation enrichment (will be done in batch)
        # Placeholder - actual clustering done in batch_enrich
        
        # 5. Narrative enrichment
        enhance_signal_with_narrative(enriched.base, self.narrative_tracker)
        enriched.narrative_sector = enriched.base.get("narrative_sector", "other")
        enriched.narrative_category = enriched.base.get("narrative_category", "")
        enriched.narrative_active = enriched.base.get("narrative_active", False)
        enriched.narrative_momentum = enriched.base.get("narrative_momentum", 0)
        enriched.narrative_boost = enriched.base.get("narrative_boost", 0)
        enriched.active_narrative = enriched.base.get("active_narrative", "")
        enriched.narrative_catalysts = enriched.base.get("narrative_catalysts", [])
        
        # 6. Risk sizing
        position_risk = self.risk_manager.calculate_position_size(enriched.base)
        if position_risk:
            enriched.position_size_usdt = position_risk.size_usdt
            enriched.risk_usdt = position_risk.risk_usdt
            enriched.kelly_fraction = position_risk.kelly_fraction
        
        # 7. Attribution factors
        enriched.attribution_factors = {
            "trend": 0, "momentum": 0, "volume": 0, "regime": 0,
            "narrative": 0, "ml": 0, "derivatives": 0, "risk_mgmt": 0
        }
        
        return enriched
    
    def batch_enrich(self, signals: List[Dict]) -> List[EnhancedSignal]:
        """Enrich multiple signals with batch operations."""
        if not signals:
            return []
        
        # Refresh derivatives for all symbols
        symbols = [s.get("coin") for s in signals if s.get("coin")]
        self.refresh_derivatives(symbols)
        
        # Update narrative tracker
        self.narrative_tracker.scan_narratives()
        
        # Correlation clustering (batch)
        enriched_signals = []
        for sig in signals:
            enriched = self.enrich_signal(sig)
            if enriched:
                enriched_signals.append(enriched)
        
        # Apply correlation clustering
        base_signals = [e.base for e in enriched_signals]
        clustered = enhance_signals_with_correlation(base_signals, self.clusterer)
        
        # Merge cluster info back
        cluster_map = {s.get("coin"): s for s in clustered}
        for enriched in enriched_signals:
            coin = enriched.base.get("coin")
            if coin in cluster_map:
                c = cluster_map[coin]
                enriched.correlation_cluster = c.get("correlation_cluster", -1)
                enriched.cluster_representative = c.get("cluster_representative", "")
                enriched.cluster_avg_corr = c.get("cluster_avg_corr", 0)
                enriched.cluster_sector = c.get("cluster_sector", "")
                enriched.is_cluster_rep = c.get("is_cluster_rep", True)
        
        return enriched_signals
    
    def record_trade_outcome(self, trade: Dict, signal: Dict = None):
        """Record completed trade for all learning systems."""
        self.attribution_engine.record_trade(trade, signal)
        self.risk_manager.record_trade(trade)
        self.ml_classifier.retrain_if_needed()
    
    def get_dashboard_data(self) -> Dict:
        """Get all data for dashboard."""
        return {
            "regime": self.regime_detector.detect_regime(),
            "regime_persistence": self.regime_detector.get_regime_persistence(),
            "narratives": self.narrative_tracker.get_narrative_summary(),
            "risk_report": self.risk_manager.get_risk_report(),
            "attribution": self.attribution_engine.generate_report(30),
            "ml_status": {
                "trained": self.ml_classifier.is_trained,
                "model_type": self.ml_classifier.model_type,
                "metadata": self.ml_classifier.metadata
            },
            "active_positions": self.position_manager.get_all_positions(),
            "derivatives": {
                "funding_count": len(self._funding_data),
                "oi_count": len(self._oi_data),
                "last_update": self._last_derivatives_fetch
            }
        }


def create_enricher(config: Dict) -> SignalEnricher:
    """Factory function."""
    return SignalEnricher(config)


# Backward compatibility functions
def enrich_discovery_with_all(discovery_results: List[Dict], 
                             config: Dict) -> List[Dict]:
    """Legacy function for discovery enrichment."""
    enricher = create_enricher(config)
    
    # Convert discovery results to signal format
    signals = []
    for r in discovery_results:
        sig = {
            "coin": r.get("coin"),
            "entry": r.get("entry", 0),
            "stop": r.get("stop", 0),
            "target": r.get("target", 0),
            "chg24": r.get("chg24", 0),
            "vol_x": r.get("vol_x", 1),
            "rr": r.get("rr", 0),
            "setup_type": r.get("setup_type", "MOMENTUM"),
            "interval": r.get("interval", "1h"),
            "score": r.get("score", 0),
            "potential_pct": r.get("potential_pct", 0)
        }
        signals.append(sig)
    
    # Batch enrich
    enriched = enricher.batch_enrich(signals)
    
    # Merge back to discovery results
    enriched_map = {e.base.get("coin"): e for e in enriched}
    for r in discovery_results:
        coin = r.get("coin")
        if coin in enriched_map:
            e = enriched_map[coin]
            r["funding_rate"] = e.funding_rate
            r["funding_apy"] = e.funding_apy
            r["open_interest"] = e.open_interest
            r["basis"] = e.basis
            r["basis_signal"] = e.basis_signal
            r["funding_signal"] = e.funding_signal
            r["oi_signal"] = e.oi_signal
            r["derivatives_score"] = e.derivatives_score
            r["derivatives_signals"] = e.derivatives_signals
            r["ml_win_prob"] = e.ml_win_prob
            r["ml_prediction"] = e.ml_prediction
            r["ml_boost"] = e.ml_boost
            r["market_regime"] = e.market_regime
            r["regime_confidence"] = e.regime_confidence
            r["regime_weight"] = e.regime_weight
            r["regime_preferred"] = e.regime_preferred
            r["regime_avoided"] = e.regime_avoided
            r["regime_position_mult"] = e.regime_position_mult
            r["regime_stop_mult"] = e.regime_stop_mult
            r["regime_target_mult"] = e.regime_target_mult
            r["narrative_sector"] = e.narrative_sector
            r["narrative_category"] = e.narrative_category
            r["narrative_active"] = e.narrative_active
            r["narrative_momentum"] = e.narrative_momentum
            r["narrative_boost"] = e.narrative_boost
            r["active_narrative"] = e.active_narrative
            r["position_size_usdt"] = e.position_size_usdt
            r["risk_usdt"] = e.risk_usdt
            r["kelly_fraction"] = e.kelly_fraction
    
    return discovery_results