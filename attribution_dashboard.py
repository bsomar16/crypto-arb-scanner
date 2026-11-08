#!/usr/bin/env python3
"""Performance attribution dashboard.

Decomposes P&L into factor contributions: Trend, Momentum, Volume, 
Regime, Narrative, ML, Derivatives, Risk Management.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
import json
import os

from botutil import load_json, save_json, log


@dataclass
class TradeAttribution:
    """Attribution for a single trade."""
    coin: str
    entry_time: str
    exit_time: str
    pnl_usdt: float
    pnl_pct: float
    # Factor contributions (sum ~= pnl_pct)
    trend_contrib: float = 0.0
    momentum_contrib: float = 0.0
    volume_contrib: float = 0.0
    regime_contrib: float = 0.0
    narrative_contrib: float = 0.0
    ml_contrib: float = 0.0
    derivatives_contrib: float = 0.0
    risk_mgmt_contrib: float = 0.0
    # Metadata
    setup_type: str = ""
    interval: str = ""
    regime: str = ""
    narrative_active: bool = False
    ml_score: float = 0.0
    holding_hours: float = 0.0


@dataclass
class FactorPerformance:
    """Performance of a single factor."""
    name: str
    total_contrib: float = 0.0
    avg_contrib_per_trade: float = 0.0
    win_rate_when_positive: float = 0.0
    win_rate_when_negative: float = 0.0
    trades_positive: int = 0
    trades_negative: int = 0
    avg_holding_hours: float = 0.0
    sharpe: float = 0.0


class AttributionEngine:
    """Calculates and tracks performance attribution."""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.trades_file = "state/attribution_trades.jsonl"
        self.summary_file = "state/attribution_summary.json"
        self.attributions: List[TradeAttribution] = []
        self._load_history()
    
    def _load_history(self):
        """Load attribution history."""
        if os.path.exists(self.trades_file):
            try:
                with open(self.trades_file, "r") as f:
                    for line in f:
                        try:
                            data = json.loads(line)
                            self.attributions.append(TradeAttribution(**data))
                        except:
                            pass
            except Exception as e:
                log("ATTRIB", f"Failed to load history: {e}")
    
    def _save_trade(self, attr: TradeAttribution):
        """Save single trade attribution."""
        try:
            os.makedirs(os.path.dirname(self.trades_file), exist_ok=True)
            with open(self.trades_file, "a") as f:
                f.write(json.dumps(attr.__dict__) + "\n")
        except Exception as e:
            log("ATTRIB", f"Failed to save trade: {e}")
    
    def record_trade(self, trade: Dict, signal: Dict = None):
        """Record trade with full attribution."""
        pnl_usdt = trade.get("pnl_usdt", 0)
        pnl_pct = trade.get("pnl_pct", 0)
        
        if not signal:
            signal = {}
        
        # Calculate factor contributions
        contributions = self._calculate_contributions(trade, signal, pnl_pct)
        
        attr = TradeAttribution(
            coin=trade.get("coin", ""),
            entry_time=trade.get("entry_time", ""),
            exit_time=trade.get("timestamp", ""),
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
            trend_contrib=contributions["trend"],
            momentum_contrib=contributions["momentum"],
            volume_contrib=contributions["volume"],
            regime_contrib=contributions["regime"],
            narrative_contrib=contributions["narrative"],
            ml_contrib=contributions["ml"],
            derivatives_contrib=contributions["derivatives"],
            risk_mgmt_contrib=contributions["risk_mgmt"],
            setup_type=signal.get("setup_type", ""),
            interval=signal.get("interval", ""),
            regime=signal.get("market_regime", ""),
            narrative_active=signal.get("narrative_active", False),
            ml_score=signal.get("ml_win_prob", 0.5),
            holding_hours=trade.get("duration_hours", 0)
        )
        
        self.attributions.append(attr)
        self._save_trade(attr)
    
    def _calculate_contributions(self, trade: Dict, signal: Dict, pnl_pct: float) -> Dict:
        """Decompose PnL into factor contributions."""
        # This is a simplified attribution model
        # In production, would use regression or factor models
        
        contribs = {
            "trend": 0.0,
            "momentum": 0.0,
            "volume": 0.0,
            "regime": 0.0,
            "narrative": 0.0,
            "ml": 0.0,
            "derivatives": 0.0,
            "risk_mgmt": 0.0
        }
        
        # Trend contribution
        trend_4h = signal.get("trend_4h", "MIXED")
        mtf_bullish = sum(1 for k in ["mtf_1h", "mtf_4h", "mtf_1d", "mtf_1w"] 
                         if signal.get(k) == "BULLISH")
        if trend_4h == "BULLISH" and mtf_bullish >= 3:
            contribs["trend"] = pnl_pct * 0.25
        elif trend_4h == "BEARISH":
            contribs["trend"] = pnl_pct * -0.15
        
        # Momentum contribution
        chg24 = signal.get("chg24", 0)
        if abs(chg24) > 10:
            contribs["momentum"] = pnl_pct * 0.2 * (1 if chg24 > 0 else -1)
        
        # Volume contribution
        vol_x = signal.get("vol_x", 1.0)
        if vol_x > 2:
            contribs["volume"] = pnl_pct * 0.15
        elif vol_x < 0.8:
            contribs["volume"] = pnl_pct * -0.1
        
        # Regime contribution
        regime = signal.get("market_regime", "NEUTRAL")
        regime_weights = {
            "STRONG_BULL": 0.2, "BULL": 0.15, "NEUTRAL_BULL": 0.1,
            "NEUTRAL": 0.0, "NEUTRAL_BEAR": -0.05,
            "BEAR": -0.1, "STRONG_BEAR": -0.15,
            "HIGH_VOL": 0.05, "LOW_VOL": 0.05
        }
        contribs["regime"] = pnl_pct * regime_weights.get(regime, 0)
        
        # Narrative contribution
        if signal.get("narrative_active"):
            momentum = signal.get("narrative_momentum", 0)
            contribs["narrative"] = pnl_pct * (momentum / 100) * 0.15
        
        # ML contribution
        ml_prob = signal.get("ml_win_prob", 0.5)
        if ml_prob > 0.6:
            contribs["ml"] = pnl_pct * (ml_prob - 0.5) * 0.2
        elif ml_prob < 0.45:
            contribs["ml"] = pnl_pct * (ml_prob - 0.5) * 0.2
        
        # Derivatives contribution
        funding = signal.get("funding_rate", 0)
        basis = signal.get("basis", 0)
        if funding > 0.05 and pnl_pct > 0:
            contribs["derivatives"] = abs(pnl_pct) * 0.1
        elif funding < -0.02 and pnl_pct < 0:
            contribs["derivatives"] = abs(pnl_pct) * 0.1
        elif abs(basis) > 2:
            contribs["derivatives"] = pnl_pct * 0.05
        
        # Risk management contribution
        rr = signal.get("rr", 1.5)
        tp1_hit = trade.get("tp1_hit", False)
        tp2_hit = trade.get("tp2_hit", False)
        trailing = trade.get("trailing_activated", False)
        
        risk_contrib = 0
        if trailing:
            risk_contrib += 0.1
        if tp1_hit:
            risk_contrib += 0.05
        if tp2_hit:
            risk_contrib += 0.05
        if rr > 2:
            risk_contrib += 0.05
        
        contribs["risk_mgmt"] = pnl_pct * risk_contrib
        
        # Normalize so sum ~= pnl_pct (approximate)
        total_attr = sum(contribs.values())
        if abs(total_attr) > 0.01 and abs(pnl_pct) > 0.01:
            scale = pnl_pct / total_attr
            contribs = {k: v * scale for k, v in contribs.items()}
        
        return contribs
    
    def analyze_factors(self, days: int = 30) -> Dict[str, FactorPerformance]:
        """Analyze factor performance over period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [
            a for a in self.attributions
            if datetime.fromisoformat(a.exit_time.replace("Z", "+00:00")) > cutoff
        ]
        
        if not recent:
            return {}
        
        factors = ["trend", "momentum", "volume", "regime", 
                  "narrative", "ml", "derivatives", "risk_mgmt"]
        
        results = {}
        for factor in factors:
            pos_contribs = [getattr(a, f"{factor}_contrib") for a in recent 
                          if getattr(a, f"{factor}_contrib") > 0]
            neg_contribs = [getattr(a, f"{factor}_contrib") for a in recent 
                          if getattr(a, f"{factor}_contrib") < 0]
            
            pos_wins = sum(1 for a in recent 
                         if getattr(a, f"{factor}_contrib") > 0 and a.pnl_pct > 0)
            neg_wins = sum(1 for a in recent 
                         if getattr(a, f"{factor}_contrib") < 0 and a.pnl_pct > 0)
            
            all_contribs = pos_contribs + neg_contribs
            
            results[factor] = FactorPerformance(
                name=factor,
                total_contrib=sum(all_contribs),
                avg_contrib_per_trade=sum(all_contribs) / len(all_contribs) if all_contribs else 0,
                win_rate_when_positive=pos_wins / len(pos_contribs) * 100 if pos_contribs else 0,
                win_rate_when_negative=neg_wins / len(neg_contribs) * 100 if neg_contribs else 0,
                trades_positive=len(pos_contribs),
                trades_negative=len(neg_contribs),
                avg_holding_hours=sum(a.holding_hours for a in recent) / len(recent) if recent else 0,
                sharpe=0  # Would need returns series
            )
        
        return results
    
    def get_setup_performance(self, days: int = 30) -> Dict:
        """Performance by setup type."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [
            a for a in self.attributions
            if datetime.fromisoformat(a.exit_time.replace("Z", "+00:00")) > cutoff
        ]
        
        by_setup = defaultdict(list)
        for a in recent:
            by_setup[a.setup_type].append(a)
        
        results = {}
        for setup, trades in by_setup.items():
            wins = [t for t in trades if t.pnl_pct > 0]
            losses = [t for t in trades if t.pnl_pct <= 0]
            
            results[setup] = {
                "total_trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": len(wins) / len(trades) * 100 if trades else 0,
                "avg_pnl_pct": sum(t.pnl_pct for t in trades) / len(trades) if trades else 0,
                "avg_win_pct": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
                "avg_loss_pct": sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
                "avg_rr": sum(t.pnl_pct / abs(t.pnl_pct) * 1.5 for t in trades if t.pnl_pct != 0) / len(trades) if trades else 0,
                "total_pnl_usdt": sum(t.pnl_usdt for t in trades),
                "avg_holding_hours": sum(t.holding_hours for t in trades) / len(trades) if trades else 0
            }
        
        return dict(sorted(results.items(), key=lambda x: -x[1]["win_rate"]))
    
    def get_regime_performance(self, days: int = 30) -> Dict:
        """Performance by market regime."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [
            a for a in self.attributions
            if datetime.fromisoformat(a.exit_time.replace("Z", "+00:00")) > cutoff
        ]
        
        by_regime = defaultdict(list)
        for a in recent:
            by_regime[a.regime].append(a)
        
        results = {}
        for regime, trades in by_regime.items():
            wins = [t for t in trades if t.pnl_pct > 0]
            results[regime] = {
                "trades": len(trades),
                "win_rate": len(wins) / len(trades) * 100 if trades else 0,
                "avg_pnl_pct": sum(t.pnl_pct for t in trades) / len(trades) if trades else 0,
                "total_pnl_usdt": sum(t.pnl_usdt for t in trades)
            }
        
        return results
    
    def get_interval_performance(self, days: int = 30) -> Dict:
        """Performance by timeframe."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [
            a for a in self.attributions
            if datetime.fromisoformat(a.exit_time.replace("Z", "+00:00")) > cutoff
        ]
        
        by_interval = defaultdict(list)
        for a in recent:
            by_interval[a.interval].append(a)
        
        results = {}
        for interval, trades in by_interval.items():
            wins = [t for t in trades if t.pnl_pct > 0]
            results[interval] = {
                "trades": len(trades),
                "win_rate": len(wins) / len(trades) * 100 if trades else 0,
                "avg_pnl_pct": sum(t.pnl_pct for t in trades) / len(trades) if trades else 0,
                "total_pnl_usdt": sum(t.pnl_usdt for t in trades)
            }
        
        return results
    
    def get_narrative_performance(self, days: int = 30) -> Dict:
        """Performance when narrative active vs not."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [
            a for a in self.attributions
            if datetime.fromisoformat(a.exit_time.replace("Z", "+00:00")) > cutoff
        ]
        
        with_narrative = [a for a in recent if a.narrative_active]
        without_narrative = [a for a in recent if not a.narrative_active]
        
        def calc(trades):
            if not trades:
                return {"trades": 0}
            wins = [t for t in trades if t.pnl_pct > 0]
            return {
                "trades": len(trades),
                "win_rate": len(wins) / len(trades) * 100,
                "avg_pnl_pct": sum(t.pnl_pct for t in trades) / len(trades),
                "total_pnl_usdt": sum(t.pnl_usdt for t in trades)
            }
        
        return {
            "with_narrative": calc(with_narrative),
            "without_narrative": calc(without_narrative),
            "narrative_edge": calc(with_narrative)["win_rate"] - calc(without_narrative)["win_rate"]
        }
    
    def get_ml_performance(self, days: int = 30) -> Dict:
        """Performance by ML prediction."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent = [
            a for a in self.attributions
            if datetime.fromisoformat(a.exit_time.replace("Z", "+00:00")) > cutoff
        ]
        
        # Bin by ML score
        bins = {
            "high_ml": [a for a in recent if a.ml_score >= 0.7],
            "med_ml": [a for a in recent if 0.5 <= a.ml_score < 0.7],
            "low_ml": [a for a in recent if a.ml_score < 0.5]
        }
        
        results = {}
        for label, trades in bins.items():
            if trades:
                wins = [t for t in trades if t.pnl_pct > 0]
                results[label] = {
                    "trades": len(trades),
                    "win_rate": len(wins) / len(trades) * 100,
                    "avg_pnl_pct": sum(t.pnl_pct for t in trades) / len(trades),
                    "avg_ml_score": sum(t.ml_score for t in trades) / len(trades)
                }
            else:
                results[label] = {"trades": 0}
        
        return results
    
    def generate_report(self, days: int = 30) -> Dict:
        """Generate full attribution report."""
        return {
            "period_days": days,
            "total_trades": len([a for a in self.attributions 
                               if datetime.fromisoformat(a.exit_time.replace("Z", "+00:00")) > 
                               datetime.now(timezone.utc) - timedelta(days=days)]),
            "factors": {k: v.__dict__ for k, v in self.analyze_factors(days).items()},
            "by_setup": self.get_setup_performance(days),
            "by_regime": self.get_regime_performance(days),
            "by_interval": self.get_interval_performance(days),
            "narrative": self.get_narrative_performance(days),
            "ml": self.get_ml_performance(days),
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
    
    def save_summary(self, days: int = 30):
        """Save summary to file."""
        report = self.generate_report(days)
        save_json(self.summary_file, report)
        log("ATTRIB", f"Saved attribution report for {days} days")


def get_attribution_engine(config: Dict = None) -> AttributionEngine:
    """Factory function."""
    return AttributionEngine(config)