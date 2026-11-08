#!/usr/bin/env python3
"""Narrative and sector rotation tracking.

Detects emerging narratives, sector momentum, and capital rotation.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone, timedelta

import numpy as np
from botutil import http_json, log


@dataclass
class NarrativeSignal:
    """Detected narrative with supporting evidence."""
    name: str
    category: str  # ai, defi, l2, meme, gaming, rwa, depin, etc.
    coins: List[str]
    momentum_score: float  # 0-100
    volume_surge: float    # % vs avg
    price_momentum: float  # weighted avg change
    mindshare_rank: int    # social rank if available
    catalysts: List[str]   # upcoming events
    confidence: float      # 0-100
    first_detected: str
    last_updated: str


@dataclass
class SectorRotation:
    """Sector rotation signal."""
    from_sector: str
    to_sector: str
    strength: float  # -100 to 100
    coins_rotating: List[str]
    trigger: str
    timestamp: str


class NarrativeTracker:
    """Tracks crypto narratives and sector rotations."""
    
    SECTORS = {
        # Layer 1
        "BTC": "store_of_value", "ETH": "smart_contract", "SOL": "smart_contract",
        "AVAX": "smart_contract", "ADA": "smart_contract", "TON": "l1",
        "TRX": "l1", "NEAR": "smart_contract", "ATOM": "interoperability",
        
        # Layer 2
        "ARB": "l2", "OP": "l2", "MATIC": "l2", "IMX": "l2", "ZKSYNC": "l2",
        "STARK": "l2", "BASE": "l2", "LINEA": "l2", "SCROLL": "l2", "MANTA": "l2",
        
        # DeFi
        "UNI": "defi", "AAVE": "defi", "COMP": "defi", "CRV": "defi", "MKR": "defi",
        "LDO": "defi", "PENDLE": "defi", "GMX": "defi", "GNS": "defi", "SNX": "defi",
        "JUP": "dex", "DYDX": "dex", "INJ": "dex", "TIA": "da", "SEI": "dex",
        
        # AI & Data
        "FET": "ai", "RNDR": "ai", "TAO": "ai", "AKT": "ai", "AGIX": "ai",
        "OCEAN": "ai", "NMR": "ai", "ALI": "ai", "CGPT": "ai", "PAAL": "ai",
        
        # Gaming/Metaverse
        "AXS": "gaming", "SAND": "gaming", "MANA": "gaming", "GALA": "gaming",
        "ILV": "gaming", "BEAM": "gaming", "PIXEL": "gaming", "RON": "gaming",
        
        # Memes
        "DOGE": "meme", "SHIB": "meme", "PEPE": "meme", "FLOKI": "meme",
        "WIF": "meme", "BONK": "meme", "POPCAT": "meme", "MEW": "meme",
        
        # RWA & DePIN
        "ONDO": "rwa", "PENDLE": "rwa", "MANTLE": "rwa", "HNT": "depin",
        "IOTX": "depin", "DIMO": "depin", "ARKM": "depin", "WICC": "depin",
        
        # Infrastructure
        "LINK": "oracle", "BAND": "oracle", "PYTH": "oracle",
        "FIL": "storage", "AR": "storage", "STORJ": "storage",
        "RENDER": "compute", "GPU": "compute", "AKASH": "compute",
        
        # Payments
        "XRP": "payments", "XLM": "payments", "LTC": "payments", "BCH": "payments",
        
        # CEX
        "BNB": "cex", "OKB": "cex", "KCS": "cex", "GT": "cex", "CRO": "cex",
    }
    
    NARRATIVE_KEYWORDS = {
        "ai": ["ai", "artificial intelligence", "gpt", "llm", "agent", "autonomous"],
        "defi": ["defi", "yield", "lending", "borrowing", "amm", "dex", "perps"],
        "l2": ["layer 2", "l2", "rollup", "zk", "optimistic", "arbitrum", "optimism"],
        "rwa": ["rwa", "real world asset", "tokenized", "treasury", "bond"],
        "depin": ["depin", "physical infrastructure", "wireless", "sensor", "compute"],
        "gaming": ["gaming", "play to earn", "nft game", "metaverse", "virtual world"],
        "meme": ["meme", "community", "viral", "culture", "dog", "cat", "frog"],
        "restaking": ["restaking", "eigenlayer", "liquid staking", "lrt", "points"],
        "modular": ["modular", "celestia", "da", "data availability", "settlement"],
        "intent": ["intent", "account abstraction", "erc4337", "smart wallet"],
        "btcfi": ["btcfi", "bitcoin defi", "ordinals", "runes", "layer 2 bitcoin"]
    }
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.lookback_hours = self.config.get("narrative_lookback_hours", 24)
        self.min_coins = self.config.get("narrative_min_coins", 3)
        self.momentum_threshold = self.config.get("narrative_momentum_threshold", 50)
        
        # State
        self.active_narratives: Dict[str, NarrativeSignal] = {}
        self.sector_performance: Dict[str, Dict] = {}
        self.rotation_history: deque = deque(maxlen=100)
        self._last_scan = 0
    
    def scan_narratives(self, tickers_24h: List[Dict] = None,
                       funding_data: Dict = None,
                       oi_data: Dict = None) -> List[NarrativeSignal]:
        """Scan for emerging narratives."""
        if not tickers_24h:
            tickers_24h = self._fetch_tickers()
        
        # Group by sector
        sector_data = defaultdict(list)
        for t in tickers_24h:
            sym = t.get("symbol", "")
            if sym.endswith("USDT"):
                coin = sym[:-4]
                sector = self.SECTORS.get(coin, "other")
                try:
                    qv = float(t.get("quoteVolume", 0))
                    chg = float(t.get("priceChangePercent", 0))
                    sector_data[sector].append({"coin": coin, "qv": qv, "chg": chg})
                except (TypeError, ValueError):
                    pass
        
        # Calculate sector metrics
        sector_metrics = {}
        for sector, coins in sector_data.items():
            if len(coins) < self.min_coins:
                continue
            
            total_qv = sum(c["qv"] for c in coins)
            weighted_chg = sum(c["chg"] * c["qv"] for c in coins) / total_qv if total_qv > 0 else 0
            advancers = sum(1 for c in coins if c["chg"] > 0)
            strong = sum(1 for c in coins if c["chg"] > 10)
            
            # Volume surge
            avg_qv = total_qv / len(coins)
            volume_surge = 0  # Would need historical comparison
            
            sector_metrics[sector] = {
                "coins": [c["coin"] for c in coins],
                "weighted_chg": weighted_chg,
                "advancers_pct": advancers / len(coins) * 100,
                "strong_pct": strong / len(coins) * 100,
                "total_volume": total_qv,
                "coin_count": len(coins)
            }
        
        # Detect narratives from sector metrics
        narratives = []
        for sector, metrics in sector_metrics.items():
            if metrics["weighted_chg"] > 5 and metrics["advancers_pct"] > 60:
                narrative = self._create_narrative(sector, metrics, tickers_24h, funding_data, oi_data)
                if narrative and narrative.momentum_score >= self.momentum_threshold:
                    narratives.append(narrative)
        
        # Check for narrative keywords in top movers
        keyword_narratives = self._detect_keyword_narratives(tickers_24h)
        narratives.extend(keyword_narratives)
        
        # Deduplicate
        unique = {}
        for n in narratives:
            key = n.category
            if key not in unique or n.momentum_score > unique[key].momentum_score:
                unique[key] = n
        
        result = list(unique.values())
        result.sort(key=lambda n: -n.momentum_score)
        
        self.active_narratives = {n.name: n for n in result}
        self._last_scan = datetime.now(timezone.utc).timestamp()
        
        return result
    
    def _create_narrative(self, sector: str, metrics: Dict,
                         tickers_24h: List[Dict],
                         funding_data: Dict,
                         oi_data: Dict) -> Optional[NarrativeSignal]:
        """Create narrative signal from sector metrics."""
        coins = metrics["coins"]
        
        # Calculate momentum score
        momentum = 0
        momentum += min(30, metrics["weighted_chg"] * 2)  # Price momentum
        momentum += min(25, metrics["advancers_pct"] / 4)  # Breadth
        momentum += min(20, metrics["strong_pct"] * 2)     # Conviction
        momentum += min(15, metrics["coin_count"] * 2)     # Participation
        
        # Funding/OI boost
        funding_boost = 0
        oi_boost = 0
        if funding_data:
            for coin in coins:
                f = funding_data.get(coin)
                if f and f.get("funding_rate", 0) > 0.03:
                    funding_boost += 5
        if oi_data:
            for coin in coins:
                if oi_data.get(coin, 0) > 50_000_000:
                    oi_boost += 3
        
        momentum += min(10, funding_boost + oi_boost)
        momentum = min(100, momentum)
        
        # Identify catalysts (simplified)
        catalysts = self._get_catalysts(sector, coins)
        
        # Map sector to narrative name
        narrative_name = self._sector_to_narrative(sector)
        
        return NarrativeSignal(
            name=narrative_name,
            category=sector,
            coins=coins[:10],  # Top 10
            momentum_score=round(momentum, 1),
            volume_surge=0,  # Would need historical
            price_momentum=round(metrics["weighted_chg"], 2),
            mindshare_rank=0,  # Would need social data
            catalysts=catalysts,
            confidence=min(100, momentum),
            first_detected=datetime.now(timezone.utc).isoformat(),
            last_updated=datetime.now(timezone.utc).isoformat()
        )
    
    def _sector_to_narrative(self, sector: str) -> str:
        mapping = {
            "ai": "AI & Machine Learning",
            "defi": "DeFi Renaissance",
            "l2": "Layer 2 Scaling",
            "rwa": "Real World Assets",
            "depin": "DePIN Infrastructure",
            "gaming": "Web3 Gaming",
            "meme": "Meme Culture",
            "restaking": "Restaking & LRTs",
            "modular": "Modular Blockchain",
            "intent": "Intent-Centric",
            "btcfi": "Bitcoin DeFi",
            "oracle": "Oracle Networks",
            "storage": "Decentralized Storage",
            "compute": "Decentralized Compute",
            "interoperability": "Cross-Chain",
            "cex": "CEX Tokens",
            "payments": "Payments",
            "dex": "DEX & Trading"
        }
        return mapping.get(sector, f"{sector.title()} Narrative")
    
    def _detect_keyword_narratives(self, tickers_24h: List[Dict]) -> List[NarrativeSignal]:
        """Detect narratives from keyword matching in top movers."""
        # Get top 50 by volume
        top = sorted(tickers_24h, key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)[:50]
        
        # Collect keywords from symbols/names (would need coin metadata)
        # For now, use sector-based detection on strong movers
        strong_movers = [t for t in top if float(t.get("priceChangePercent", 0)) > 15]
        
        if not strong_movers:
            return []
        
        # Group by sector
        sector_counts = defaultdict(int)
        for t in strong_movers:
            coin = t.get("symbol", "")[:-4]
            sector = self.SECTORS.get(coin, "other")
            if sector != "other":
                sector_counts[sector] += 1
        
        narratives = []
        for sector, count in sector_counts.items():
            if count >= 3:
                coins = [t.get("symbol", "")[:-4] for t in strong_movers if self.SECTORS.get(t.get("symbol", "")[:-4]) == sector]
                metrics = {
                    "weighted_chg": np.mean([float(t.get("priceChangePercent", 0)) for t in strong_movers if self.SECTORS.get(t.get("symbol", "")[:-4]) == sector]),
                    "advancers_pct": 100,
                    "strong_pct": 100,
                    "coin_count": count
                }
                narrative = self._create_narrative(sector, metrics, tickers_24h, None, None)
                if narrative:
                    narrative.name = f"{narrative.name} (Momentum)"
                    narratives.append(narrative)
        
        return narratives
    
    def _get_catalysts(self, sector: str, coins: List[str]) -> List[str]:
        """Get upcoming catalysts for sector."""
        catalysts_map = {
            "ai": ["NVIDIA earnings", "AI agent launches", "Compute demand"],
            "defi": ["Fee switch votes", "New pool launches", "Incentive programs"],
            "l2": ["Mainnet upgrades", "Airdrop announcements", "Bridge launches"],
            "rwa": ["Treasury launches", "Regulatory clarity", "Institutional adoption"],
            "depin": ["Network milestones", "Hardware shipments", "Partnership announcements"],
            "gaming": ["Game launches", "Token unlocks", "Tournament seasons"],
            "meme": ["Viral trends", "Exchange listings", "Community events"],
            "restaking": ["Eigenlayer AVS launches", "Point campaigns", "Token launches"],
            "modular": ["Testnet launches", "Blob space pricing", "Rollup deployments"],
            "intent": ["ERC-4337 upgrades", "Wallet launches", "Bundler integrations"]
        }
        return catalysts_map.get(sector, ["Sector rotation", "Technical breakout"])
    
    def _fetch_tickers(self) -> List[Dict]:
        try:
            return http_json("https://data-api.binance.vision/api/v3/ticker/24hr", timeout=20)
        except Exception as e:
            log("NARRATIVE", "Failed to fetch tickers:", e)
            return []
    
    def get_sector_performance(self) -> Dict[str, Dict]:
        """Get current sector performance rankings."""
        tickers = self._fetch_tickers()
        self.scan_narratives(tickers)
        
        # Build performance dict
        perf = {}
        for name, narrative in self.active_narratives.items():
            perf[narrative.category] = {
                "momentum_score": narrative.momentum_score,
                "price_momentum": narrative.price_momentum,
                "top_coins": narrative.coins[:5],
                "confidence": narrative.confidence,
                "catalysts": narrative.catalysts
            }
        
        return dict(sorted(perf.items(), key=lambda x: -x[1]["momentum_score"]))
    
    def detect_rotations(self, previous_sectors: Dict[str, float] = None) -> List[SectorRotation]:
        """Detect sector rotations."""
        current = self.get_sector_performance()
        
        rotations = []
        if previous_sectors:
            for sector, momentum in current.items():
                prev = previous_sectors.get(sector, 0)
                change = momentum["momentum_score"] - prev
                
                if abs(change) > 20:  # Significant rotation
                    direction = "inflow" if change > 0 else "outflow"
                    rotations.append(SectorRotation(
                        from_sector="various" if change > 0 else sector,
                        to_sector=sector if change > 0 else "various",
                        strength=change,
                        coins_rotating=momentum["top_coins"],
                        trigger=f"{direction} detected",
                        timestamp=datetime.now(timezone.utc).isoformat()
                    ))
        
        # Store for next comparison
        self.sector_performance = {k: v["momentum_score"] for k, v in current.items()}
        
        return rotations
    
    def get_narrative_summary(self) -> Dict:
        """Get summary of active narratives."""
        return {
            "active_narratives": len(self.active_narratives),
            "top_narrative": max(self.active_narratives.values(), key=lambda n: n.momentum_score).name if self.active_narratives else None,
            "narratives": {
                name: {
                    "category": n.category,
                    "momentum": n.momentum_score,
                    "coins": n.coins[:5],
                    "confidence": n.confidence,
                    "catalysts": n.catalysts[:3]
                }
                for name, n in self.active_narratives.items()
            },
            "sector_rankings": self.get_sector_performance()
        }


def enhance_signal_with_narrative(signal: Dict, tracker: NarrativeTracker) -> Dict:
    """Add narrative context to signal."""
    coin = signal.get("coin", "")
    sector = tracker.SECTORS.get(coin, "other")
    
    signal["narrative_sector"] = sector
    signal["narrative_category"] = tracker._sector_to_narrative(sector)
    
    # Check if coin's narrative is active
    active = False
    momentum = 0
    for narrative in tracker.active_narratives.values():
        if coin in narrative.coins:
            active = True
            momentum = narrative.momentum_score
            signal["active_narrative"] = narrative.name
            signal["narrative_momentum"] = momentum
            signal["narrative_catalysts"] = narrative.catalysts
            break
    
    signal["narrative_active"] = active
    signal["narrative_momentum"] = momentum
    
    # Boost if in hot narrative
    if active and momentum > 70:
        signal["narrative_boost"] = min(15, momentum / 5)
    else:
        signal["narrative_boost"] = 0
    
    return signal