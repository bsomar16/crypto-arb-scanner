#!/usr/bin/env python3
"""Correlation clustering and signal deduplication.

Groups correlated assets to prevent overexposure and duplicate signals.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone

import numpy as np
from botutil import http_json, log


@dataclass
class CorrelationCluster:
    """A cluster of correlated assets."""
    id: int
    assets: List[str]
    avg_correlation: float
    max_correlation: float
    representative: str  # Highest volume/liquidity asset
    sector: str
    signals: List[Dict] = None
    
    def __post_init__(self):
        if self.signals is None:
            self.signals = []


class CorrelationClusterer:
    """Clusters correlated assets and selects best signal per cluster."""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.correlation_threshold = self.config.get("correlation_threshold", 0.75)
        self.lookback_days = self.config.get("correlation_lookback_days", 30)
        self.min_cluster_size = self.config.get("min_cluster_size", 2)
        self.max_clusters = self.config.get("max_clusters", 20)
        
        # Cache
        self._correlation_cache: Dict[Tuple[str, str], float] = {}
        self._price_cache: Dict[str, List[float]] = {}
        self._cache_time = 0
        self._cache_ttl = 3600  # 1 hour
    
    def fetch_correlation_matrix(self, symbols: List[str]) -> Dict[Tuple[str, str], float]:
        """Fetch pairwise correlations for symbols."""
        cache_key = tuple(sorted(symbols))
        now = datetime.now(timezone.utc).timestamp()
        
        if now - self._cache_time < self._cache_ttl and self._correlation_cache:
            # Return cached subset
            result = {}
            for pair, corr in self._correlation_cache.items():
                if pair[0] in symbols and pair[1] in symbols:
                    result[pair] = corr
            return result
        
        # Fetch price histories
        prices = {}
        for sym in symbols:
            try:
                data = http_json(
                    f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval=1h&limit={self.lookback_days * 24}",
                    timeout=15
                )
                if data and len(data) >= 24:
                    closes = [float(k[4]) for k in data]
                    prices[sym] = closes[-self.lookback_days * 24:]
            except Exception as e:
                log("CORR", f"Failed to fetch {sym}: {e}")
        
        # Calculate pairwise correlations
        correlations = {}
        sym_list = list(prices.keys())
        for i, s1 in enumerate(sym_list):
            for s2 in sym_list[i+1:]:
                p1, p2 = prices[s1], prices[s2]
                min_len = min(len(p1), len(p2))
                if min_len >= 24:
                    # Align to same length
                    p1_arr = np.array(p1[-min_len:])
                    p2_arr = np.array(p2[-min_len:])
                    # Returns
                    r1 = np.diff(np.log(p1_arr))
                    r2 = np.diff(np.log(p2_arr))
                    if len(r1) > 1 and np.std(r1) > 0 and np.std(r2) > 0:
                        corr = np.corrcoef(r1, r2)[0, 1]
                        if not np.isnan(corr):
                            pair = tuple(sorted([s1, s2]))
                            correlations[pair] = float(corr)
        
        self._correlation_cache = correlations
        self._cache_time = now
        return correlations
    
    def build_clusters(self, signals: List[Dict]) -> List[CorrelationCluster]:
        """Build correlation clusters from signals."""
        if len(signals) < 2:
            return []
        
        symbols = [s.get("coin") for s in signals if s.get("coin")]
        symbols = list(set(symbols))
        
        correlations = self.fetch_correlation_matrix(symbols)
        
        # Build adjacency graph
        adj = defaultdict(set)
        for (s1, s2), corr in correlations.items():
            if abs(corr) >= self.correlation_threshold:
                adj[s1].add(s2)
                adj[s2].add(s1)
        
        # Find connected components (clusters)
        visited = set()
        clusters = []
        cluster_id = 0
        
        for sym in symbols:
            if sym in visited:
                continue
            
            # BFS to find cluster
            cluster_assets = []
            queue = [sym]
            while queue:
                curr = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                cluster_assets.append(curr)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        queue.append(neighbor)
            
            if len(cluster_assets) >= self.min_cluster_size:
                # Calculate cluster metrics
                cluster_corrs = []
                for a in cluster_assets:
                    for b in cluster_assets:
                        if a < b:
                            pair = tuple(sorted([a, b]))
                            if pair in correlations:
                                cluster_corrs.append(abs(correlations[pair]))
                
                avg_corr = np.mean(cluster_corrs) if cluster_corrs else 0
                max_corr = max(cluster_corrs) if cluster_corrs else 0
                
                # Select representative (highest volume signal)
                cluster_signals = [s for s in signals if s.get("coin") in cluster_assets]
                rep = max(cluster_signals, key=lambda s: s.get("qv", 0)).get("coin") if cluster_signals else cluster_assets[0]
                
                # Determine sector
                sector = self._get_sector(rep)
                
                clusters.append(CorrelationCluster(
                    id=cluster_id,
                    assets=cluster_assets,
                    avg_correlation=round(avg_corr, 4),
                    max_correlation=round(max_corr, 4),
                    representative=rep,
                    sector=sector,
                    signals=cluster_signals
                ))
                cluster_id += 1
        
        # Sort by cluster quality (avg correlation desc)
        clusters.sort(key=lambda c: -c.avg_correlation)
        
        return clusters[:self.max_clusters]
    
    def _get_sector(self, coin: str) -> str:
        sectors = {
            "BTC": "store_of_value", "ETH": "smart_contract",
            "SOL": "smart_contract", "AVAX": "smart_contract", "ADA": "smart_contract",
            "DOT": "interoperability", "LINK": "oracle", "ATOM": "interoperability",
            "UNI": "defi", "AAVE": "defi", "COMP": "defi", "CRV": "defi", "MKR": "defi",
            "DOGE": "meme", "SHIB": "meme", "PEPE": "meme", "FLOKI": "meme",
            "ARB": "l2", "OP": "l2", "MATIC": "l2", "IMX": "l2",
            "INJ": "dex", "TIA": "da", "RUNE": "dex", "SEI": "dex",
            "FET": "ai", "RNDR": "ai", "TAO": "ai", "AKT": "ai",
            "TON": "l1", "TRX": "l1", "BNB": "cex", "XRP": "payments",
            "LTC": "payments", "BCH": "payments", "XLM": "payments"
        }
        return sectors.get(coin, "other")
    
    def select_best_signals(self, signals: List[Dict], 
                           max_per_cluster: int = 1) -> List[Dict]:
        """Select best signal from each correlation cluster."""
        clusters = self.build_clusters(signals)
        
        selected = []
        clustered_assets = set()
        
        for cluster in clusters:
            # Sort signals by quality
            cluster_signals = cluster.signals
            cluster_signals.sort(key=lambda s: (
                -s.get("trade_quality", s.get("score", 0)),
                -s.get("rr", 0),
                -s.get("potential_pct", 0)
            ))
            
            # Take best N from cluster
            for sig in cluster_signals[:max_per_cluster]:
                selected.append(sig)
                clustered_assets.add(sig.get("coin"))
        
        # Add unclustered signals (no correlation above threshold)
        for sig in signals:
            coin = sig.get("coin")
            if coin and coin not in clustered_assets:
                selected.append(sig)
        
        return selected
    
    def get_cluster_report(self, clusters: List[CorrelationCluster]) -> Dict:
        """Generate cluster analysis report."""
        return {
            "num_clusters": len(clusters),
            "total_assets": sum(len(c.assets) for c in clusters),
            "clusters": [
                {
                    "id": c.id,
                    "assets": c.assets,
                    "avg_correlation": c.avg_correlation,
                    "max_correlation": c.max_correlation,
                    "representative": c.representative,
                    "sector": c.sector,
                    "num_signals": len(c.signals)
                }
                for c in clusters
            ]
        }


def enhance_signals_with_correlation(signals: List[Dict], 
                                    clusterer: CorrelationClusterer,
                                    max_per_cluster: int = 1) -> List[Dict]:
    """Add cluster info and select best signals."""
    clusters = clusterer.build_clusters(signals)
    selected = clusterer.select_best_signals(signals, max_per_cluster)
    
    # Add cluster metadata to selected signals
    for sig in selected:
        coin = sig.get("coin")
        for cluster in clusters:
            if coin in cluster.assets:
                sig["correlation_cluster"] = cluster.id
                sig["cluster_representative"] = cluster.representative
                sig["cluster_avg_corr"] = cluster.avg_correlation
                sig["cluster_sector"] = cluster.sector
                sig["is_cluster_rep"] = (coin == cluster.representative)
                break
        else:
            sig["correlation_cluster"] = -1
            sig["is_cluster_rep"] = True
    
    return selected