#!/usr/bin/env python3
"""Dynamic risk management with Kelly sizing, trailing stops, and portfolio heat.

Implements adaptive position sizing and risk controls per signal/regime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime, timezone

from botutil import log


@dataclass
class PositionRisk:
    """Risk parameters for a single position."""
    coin: str
    entry: float
    stop: float
    target: float
    size_usdt: float
    risk_usdt: float
    risk_pct: float
    rr: float
    kelly_fraction: float
    max_size_usdt: float
    correlation_risk: float = 0.0
    regime_adj: float = 1.0


@dataclass 
class PortfolioRisk:
    """Portfolio-level risk metrics."""
    total_exposure: float = 0.0
    total_risk: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    open_positions: int = 0
    max_positions: int = 5
    sector_exposure: Dict[str, float] = field(default_factory=dict)
    correlation_matrix: Dict = field(default_factory=dict)
    daily_pnl: float = 0.0
    daily_loss_limit: float = 30.0
    kill_switch: bool = False


class DynamicRiskManager:
    """Manages dynamic risk for signal generation and position sizing."""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.account_equity = self.config.get("account_equity_usdt", 10000)
        self.max_total_exposure = self.config.get("max_total_exposure_usdt", 3000)
        self.max_position_size = self.config.get("max_position_notional_usdt", 500)
        self.risk_per_trade = self.config.get("risk_per_trade_pct", 1.0) / 100
        self.max_daily_loss = self.config.get("max_daily_loss_usdt", 50)
        self.kelly_enabled = self.config.get("kelly_sizing_enabled", True)
        self.kelly_lookback = self.config.get("kelly_lookback_trades", 100)
        self.kelly_max_fraction = self.config.get("kelly_max_fraction", 0.25)
        self.trailing_enabled = self.config.get("trailing_stop_enabled", True)
        self.trailing_activation_r = self.config.get("trailing_activation_r", 1.0)
        self.trailing_distance_r = self.config.get("trailing_distance_r", 0.5)
        self.correlation_limit = self.config.get("max_pairwise_correlation", 0.8)
        self.sector_limit = self.config.get("max_same_sector_positions", 2)
        
        self.portfolio = PortfolioRisk(
            max_positions=self.config.get("max_open_positions", 5),
            daily_loss_limit=self.max_daily_loss
        )
        self.position_history: List[Dict] = []
        self.sector_map = self._load_sector_map()
    
    def _load_sector_map(self) -> Dict[str, str]:
        """Load coin -> sector mapping."""
        # Simplified sector mapping
        return {
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
    
    def get_sector(self, coin: str) -> str:
        return self.sector_map.get(coin, "other")
    
    def calculate_kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Calculate Kelly fraction from historical performance."""
        if win_rate <= 0 or win_rate >= 1 or avg_loss <= 0:
            return 0.0
        
        # Kelly: f = (p * b - q) / b where b = avg_win/avg_loss
        b = avg_win / avg_loss
        p = win_rate
        q = 1 - p
        
        kelly = (p * b - q) / b if b > 0 else 0
        
        # Apply safety caps
        kelly = max(0, min(kelly, self.kelly_max_fraction))
        return kelly
    
    def get_historical_kelly(self) -> float:
        """Calculate Kelly from position history."""
        if len(self.position_history) < 20:
            return self.risk_per_trade  # Default
        
        wins = [t for t in self.position_history if t.get("outcome") == "WIN"]
        losses = [t for t in self.position_history if t.get("outcome") == "LOSS"]
        
        if not wins or not losses:
            return self.risk_per_trade
        
        win_rate = len(wins) / len(self.position_history)
        avg_win = sum(t.get("pnl_pct", 0) for t in wins) / len(wins) / 100
        avg_loss = abs(sum(t.get("pnl_pct", 0) for t in losses) / len(losses)) / 100
        
        return self.calculate_kelly_fraction(win_rate, avg_win, avg_loss)
    
    def calculate_position_size(self, signal: Dict, 
                               current_price: float = None) -> PositionRisk:
        """Calculate optimal position size for a signal."""
        entry = signal.get("entry", current_price or 0)
        stop = signal.get("stop", 0)
        target = signal.get("target", signal.get("t3", 0))
        
        if entry <= 0 or stop <= 0:
            return None
        
        # Risk per share
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0:
            return None
        
        # Target R:R
        reward_per_share = abs(target - entry) if target > 0 else 0
        rr = reward_per_share / risk_per_share if risk_per_share > 0 else 0
        
        # Base risk amount
        base_risk_usdt = self.account_equity * self.risk_per_trade
        
        # Kelly adjustment
        kelly_frac = self.get_historical_kelly() if self.kelly_enabled else self.risk_per_trade
        kelly_risk_usdt = self.account_equity * kelly_frac
        
        # Use more conservative of base and Kelly
        risk_usdt = min(base_risk_usdt, kelly_risk_usdt)
        
        # Regime adjustment
        regime_mult = signal.get("regime_position_mult", 1.0)
        risk_usdt *= regime_mult
        
        # ML adjustment
        ml_boost = signal.get("ml_boost", 0)
        risk_usdt *= (1 + ml_boost / 100)
        
        # Win rate adjustment
        win_rate = signal.get("historical_win_pct", 50) / 100
        if win_rate > 0.6:
            risk_usdt *= 1.2
        elif win_rate < 0.45:
            risk_usdt *= 0.7
        
        # Calculate size
        size_usdt = risk_usdt / (risk_per_share / entry) if risk_per_share > 0 else 0
        
        # Apply caps
        size_usdt = min(size_usdt, self.max_position_size)
        size_usdt = min(size_usdt, self.max_total_exposure * 0.3)  # Max 30% per position
        
        # Recalculate actual risk
        actual_risk_usdt = size_usdt * (risk_per_share / entry)
        risk_pct = (actual_risk_usdt / self.account_equity) * 100
        
        # Correlation risk (would need portfolio positions)
        correlation_risk = signal.get("correlation_risk", 0)
        if correlation_risk > self.correlation_limit:
            size_usdt *= 0.5
            actual_risk_usdt *= 0.5
        
        return PositionRisk(
            coin=signal.get("coin", ""),
            entry=entry,
            stop=stop,
            target=target,
            size_usdt=round(size_usdt, 2),
            risk_usdt=round(actual_risk_usdt, 2),
            risk_pct=round(risk_pct, 2),
            rr=round(rr, 2),
            kelly_fraction=round(kelly_frac, 4),
            max_size_usdt=self.max_position_size,
            correlation_risk=correlation_risk,
            regime_adj=regime_mult
        )
    
    def calculate_trailing_stop(self, position: PositionRisk, 
                               current_price: float, 
                               highest_price: float) -> Tuple[float, bool]:
        """Calculate trailing stop level."""
        if not self.trailing_enabled:
            return position.stop, False
        
        # Activation: price moved in favor by activation_r * risk
        risk_dist = position.entry - position.stop
        activation_price = position.entry + (self.trailing_activation_r * risk_dist)
        
        if current_price < activation_price:
            return position.stop, False
        
        # Trail: distance = trailing_distance_r * risk
        trail_dist = self.trailing_distance_r * risk_dist
        new_stop = highest_price - trail_dist
        
        # Only move stop up
        if new_stop > position.stop:
            return round(new_stop, 8), True
        
        return position.stop, False
    
    def check_portfolio_limits(self, new_position: PositionRisk,
                              current_positions: List[Dict]) -> Tuple[bool, str]:
        """Check if new position violates portfolio limits."""
        # Position count
        if len(current_positions) >= self.portfolio.max_positions:
            return False, f"Max positions ({self.portfolio.max_positions}) reached"
        
        # Total exposure
        new_exposure = sum(p.get("size_usdt", 0) for p in current_positions) + new_position.size_usdt
        if new_exposure > self.max_total_exposure:
            return False, f"Max exposure ({self.max_total_exposure}) exceeded"
        
        # Sector limit
        sector = self.get_sector(new_position.coin)
        sector_count = sum(1 for p in current_positions 
                          if self.get_sector(p.get("coin", "")) == sector)
        if sector_count >= self.sector_limit:
            return False, f"Sector limit ({self.sector_limit}) reached for {sector}"
        
        # Daily loss limit
        if self.portfolio.daily_pnl <= -self.max_daily_loss:
            return False, f"Daily loss limit ({self.max_daily_loss}) hit"
        
        # Kill switch
        if self.portfolio.kill_switch:
            return False, "Kill switch active"
        
        return True, "OK"
    
    def calculate_correlation_risk(self, new_coin: str,
                                  current_positions: List[Dict],
                                  correlation_data: Dict) -> float:
        """Calculate max correlation with existing positions."""
        max_corr = 0.0
        for pos in current_positions:
            pair = tuple(sorted([new_coin, pos.get("coin", "")]))
            corr = correlation_data.get(pair, 0)
            max_corr = max(max_corr, abs(corr))
        return max_corr
    
    def update_portfolio(self, positions: List[Dict], 
                        correlation_data: Dict = None,
                        daily_pnl: float = 0):
        """Update portfolio risk metrics."""
        self.portfolio.open_positions = len(positions)
        self.portfolio.total_exposure = sum(p.get("size_usdt", 0) for p in positions)
        self.portfolio.total_risk = sum(p.get("risk_usdt", 0) for p in positions)
        self.portfolio.daily_pnl = daily_pnl
        
        # Sector exposure
        sector_exp = defaultdict(float)
        for p in positions:
            sector = self.get_sector(p.get("coin", ""))
            sector_exp[sector] += p.get("size_usdt", 0)
        self.portfolio.sector_exposure = dict(sector_exp)
        
        # Correlation matrix
        if correlation_data and len(positions) > 1:
            self.portfolio.correlation_matrix = {}
            for i, p1 in enumerate(positions):
                for p2 in positions[i+1:]:
                    pair = tuple(sorted([p1.get("coin", ""), p2.get("coin", "")]))
                    corr = correlation_data.get(pair, 0)
                    if corr != 0:
                        self.portfolio.correlation_matrix[f"{p1['coin']}-{p2['coin']}"] = corr
        
        # Check drawdown
        if self.portfolio.daily_pnl < self.portfolio.max_drawdown:
            self.portfolio.max_drawdown = self.portfolio.daily_pnl
    
    def get_risk_report(self) -> Dict:
        """Generate risk report."""
        return {
            "portfolio": {
                "open_positions": self.portfolio.open_positions,
                "max_positions": self.portfolio.max_positions,
                "total_exposure": round(self.portfolio.total_exposure, 2),
                "max_exposure": self.max_total_exposure,
                "exposure_pct": round(self.portfolio.total_exposure / self.max_total_exposure * 100, 1),
                "total_risk": round(self.portfolio.total_risk, 2),
                "daily_pnl": round(self.portfolio.daily_pnl, 2),
                "daily_loss_limit": self.max_daily_loss,
                "kill_switch": self.portfolio.kill_switch,
                "max_drawdown": round(self.portfolio.max_drawdown, 2)
            },
            "sectors": self.portfolio.sector_exposure,
            "correlations": self.portfolio.correlation_matrix,
            "kelly_fraction": round(self.get_historical_kelly(), 4),
            "risk_per_trade_pct": round(self.risk_per_trade * 100, 2)
        }
    
    def record_trade(self, trade: Dict):
        """Record completed trade for Kelly calculation."""
        self.position_history.append(trade)
        # Keep last N trades
        if len(self.position_history) > self.kelly_lookback:
            self.position_history = self.position_history[-self.kelly_lookback:]
    
    def emergency_liquidation_check(self, positions: List[Dict]) -> List[str]:
        """Check which positions need emergency liquidation."""
        to_liquidate = []
        for p in positions:
            # Stop loss hit
            if p.get("current_price", 0) <= p.get("stop", 0):
                to_liquidate.append(p.get("coin", ""))
            # Time expiry
            if p.get("expiry_time", 0) < datetime.now(timezone.utc).timestamp():
                to_liquidate.append(p.get("coin", ""))
        return to_liquidate
    
    def set_kill_switch(self, enabled: bool):
        """Enable/disable kill switch."""
        self.portfolio.kill_switch = enabled
        log("RISK", f"Kill switch {'enabled' if enabled else 'disabled'}")
    
    def adjust_for_regime(self, regime: str):
        """Adjust risk parameters for market regime."""
        regime_configs = {
            "STRONG_BULL": {"risk_mult": 1.2, "max_positions": 7},
            "BULL": {"risk_mult": 1.1, "max_positions": 6},
            "NEUTRAL": {"risk_mult": 1.0, "max_positions": 5},
            "NEUTRAL_BEAR": {"risk_mult": 0.8, "max_positions": 4},
            "BEAR": {"risk_mult": 0.6, "max_positions": 3},
            "STRONG_BEAR": {"risk_mult": 0.4, "max_positions": 2},
            "HIGH_VOL": {"risk_mult": 0.7, "max_positions": 3},
            "LOW_VOL": {"risk_mult": 1.1, "max_positions": 5}
        }
        
        adj = regime_configs.get(regime, {"risk_mult": 1.0, "max_positions": 5})
        self.risk_per_trade = self.config.get("risk_per_trade_pct", 1.0) / 100 * adj["risk_mult"]
        self.portfolio.max_positions = adj["max_positions"]
        log("RISK", f"Regime adjustment: risk_mult={adj['risk_mult']}, max_positions={adj['max_positions']}")


class PositionManager:
    """Manages active positions with trailing stops and exits."""
    
    def __init__(self, risk_manager: DynamicRiskManager):
        self.risk = risk_manager
        self.positions: Dict[str, Dict] = {}
    
    def open_position(self, signal: Dict, size: PositionRisk) -> Dict:
        """Open new position from signal."""
        position = {
            "coin": signal.get("coin"),
            "entry": size.entry,
            "stop": size.stop,
            "target": size.target,
            "size_usdt": size.size_usdt,
            "risk_usdt": size.risk_usdt,
            "rr": size.rr,
            "entry_time": datetime.now(timezone.utc).timestamp(),
            "highest_price": size.entry,
            "status": "OPEN",
            "setup_type": signal.get("setup_type"),
            "interval": signal.get("interval"),
            "trailing_activated": False,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "expiry_time": signal.get("expiry_time", 0)
        }
        self.positions[signal.get("coin")] = position
        log("POSITION", f"Opened {signal.get('coin')} @ {size.entry} size={size.size_usdt}")
        return position
    
    def update_position(self, coin: str, current_price: float) -> Optional[Dict]:
        """Update position with current price, manage trailing stops."""
        pos = self.positions.get(coin)
        if not pos or pos["status"] != "OPEN":
            return None
        
        # Update highest price
        if current_price > pos["highest_price"]:
            pos["highest_price"] = current_price
        
        # Check trailing stop
        risk_pos = PositionRisk(
            coin=coin,
            entry=pos["entry"],
            stop=pos["stop"],
            target=pos["target"],
            size_usdt=pos["size_usdt"],
            risk_usdt=pos["risk_usdt"],
            risk_pct=0, rr=pos["rr"],
            kelly_fraction=0, max_size_usdt=0
        )
        
        new_stop, moved = self.risk.calculate_trailing_stop(risk_pos, current_price, pos["highest_price"])
        if moved:
            pos["stop"] = new_stop
            pos["trailing_activated"] = True
            log("POSITION", f"Trailing stop moved for {coin}: {new_stop}")
        
        # Check TP levels
        self._check_targets(pos, current_price)
        
        # Check stop loss
        if current_price <= pos["stop"]:
            return self.close_position(coin, "STOP_LOSS", current_price)
        
        # Check expiry
        if pos.get("expiry_time", 0) and pos["expiry_time"] < datetime.now(timezone.utc).timestamp():
            return self.close_position(coin, "EXPIRED", current_price)
        
        return pos
    
    def _check_targets(self, pos: Dict, current_price: float):
        """Check and handle TP levels."""
        entry = pos["entry"]
        target = pos["target"]
        risk_dist = entry - pos["stop"]
        
        tp1 = entry + risk_dist * 1.0  # 1R
        tp2 = entry + risk_dist * 2.0  # 2R
        tp3 = entry + risk_dist * 3.0  # 3R
        
        if not pos["tp1_hit"] and current_price >= tp1:
            pos["tp1_hit"] = True
            log("POSITION", f"TP1 hit for {pos['coin']} @ {tp1}")
            # Move stop to breakeven
            pos["stop"] = max(pos["stop"], entry)
        
        if not pos["tp2_hit"] and current_price >= tp2:
            pos["tp2_hit"] = True
            log("POSITION", f"TP2 hit for {pos['coin']} @ {tp2}")
            # Trail more aggressively
            pos["stop"] = max(pos["stop"], entry + risk_dist * 0.5)
        
        if not pos["tp3_hit"] and current_price >= tp3:
            pos["tp3_hit"] = True
            log("POSITION", f"TP3 hit for {pos['coin']} @ {tp3}")
    
    def close_position(self, coin: str, reason: str, exit_price: float) -> Dict:
        """Close position and record trade."""
        pos = self.positions.pop(coin, None)
        if not pos:
            return {}
        
        # Calculate PnL
        entry = pos["entry"]
        size = pos["size_usdt"]
        pnl_pct = ((exit_price - entry) / entry) * 100
        pnl_usdt = size * pnl_pct / 100
        
        trade = {
            "coin": coin,
            "entry": entry,
            "exit": exit_price,
            "size_usdt": size,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usdt": round(pnl_usdt, 2),
            "outcome": "WIN" if pnl_usdt > 0 else "LOSS",
            "reason": reason,
            "duration_hours": round((datetime.now(timezone.utc).timestamp() - pos["entry_time"]) / 3600, 2),
            "setup_type": pos.get("setup_type"),
            "interval": pos.get("interval"),
            "rr": pos.get("rr"),
            "tp1_hit": pos.get("tp1_hit"),
            "tp2_hit": pos.get("tp2_hit"),
            "tp3_hit": pos.get("tp3_hit"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        self.risk.record_trade(trade)
        log("POSITION", f"Closed {coin} @ {exit_price} PnL={pnl_usdt:.2f} ({reason})")
        
        return trade
    
    def get_all_positions(self) -> List[Dict]:
        """Get all active positions."""
        return list(self.positions.values())
    
    def get_position(self, coin: str) -> Optional[Dict]:
        """Get specific position."""
        return self.positions.get(coin)