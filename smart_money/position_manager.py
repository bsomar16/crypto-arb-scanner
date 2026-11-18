"""Active Position Lifecycle, Monotonic Stop Ratchets & Multi-Candle Exit Engine.

Conforms to SMART_MONEY_PHASE2_SPEC_v2.1.5_FINAL.md (§§ 6, 9, 11)
and SMART_MONEY_PHASE2_SPEC_v2.1.6_AMENDMENT.md.

Strictly Binance Spot Long/BUY only. No live trading, shadow mode only.
"""
from __future__ import annotations

from decimal import Decimal
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


FEE_RATE = Decimal("0.0010")          # Standard 0.100% USDT fee
STOP_SLIPPAGE = Decimal("0.0008")     # 8 bps slippage on market/stop fills
LIMIT_SLIPPAGE = Decimal("0.0000")    # 0 bps slippage on resting limit fills (TP1/TP2)


@dataclass
class CandleBar:
    timestamp_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")


@dataclass
class ExitExecution:
    timestamp_ms: int
    symbol: str
    exit_type: str  # "TP1_EXECUTED_PARTIAL", "TP2_EXECUTED_FULL", "STOPPED_OUT_FULL", etc.
    scenario: str   # "A", "B", "C", "D", "F", "G", "H-1", "H-2", "H-3", "J", "K", "L", "TIME_STOP"
    q_sold: Decimal
    q_remaining: Decimal
    fill_price: Decimal
    cost_basis_released: Decimal
    gross_proceeds: Decimal
    fee_paid: Decimal
    net_proceeds: Decimal
    realized_pnl: Decimal


@dataclass
class ActivePosition:
    symbol: str
    entry_time_ms: int
    entry_price: Decimal
    q_order: Decimal
    q_received: Decimal
    q_remaining: Decimal
    cost_basis_remaining: Decimal
    initial_stop: Decimal
    active_stop: Decimal
    breakeven_stop: Decimal
    tp1_price: Decimal
    tp2_price: Decimal
    tp1_executed: bool = False
    tp2_executed: bool = False
    initial_stop_active: bool = True
    breakeven_stop_active: bool = False
    trailing_stop_active: bool = False
    highest_since_entry: Decimal = Decimal("0")
    bars_held: int = 0
    time_stop_bars: int = 12
    status: str = "POSITION_OPEN"  # "POSITION_OPEN", "FULLY_EXITED"
    exit_reason: Optional[str] = None
    realized_pnl: Decimal = Decimal("0")

    def __post_init__(self):
        if self.highest_since_entry == Decimal("0"):
            self.highest_since_entry = self.entry_price

    def update_active_stop(self, candidate_stop: Decimal) -> None:
        """Enforces the Stop Loss Monotonicity Supur-Invariant (§ 11.3):
        SL_active(t+1) = max(SL_active(t), candidate_stop)
        """
        if candidate_stop > self.active_stop:
            self.active_stop = candidate_stop

    def evaluate_candle(
        self,
        candle: CandleBar,
        atr14: Optional[Decimal] = None
    ) -> List[ExitExecution]:
        """Executes the complete multi-candle exit precedence hierarchy (§ 9.2)."""
        if self.status == "FULLY_EXITED":
            return []

        executions: List[ExitExecution] = []
        self.bars_held += 1
        if candle.high > self.highest_since_entry:
            self.highest_since_entry = candle.high

        # ---------------------------------------------------------------------
        # Step 1: Active Stop Level Resolution
        # ---------------------------------------------------------------------
        # Ensure active_stop is at least initial_stop, or breakeven if armed
        if self.breakeven_stop_active:
            self.update_active_stop(self.breakeven_stop)
        else:
            self.update_active_stop(self.initial_stop)

        # ---------------------------------------------------------------------
        # Step 2: Opening Gap Checks (Evaluated at candle.open)
        # ---------------------------------------------------------------------

        # Gap Below Active Stop (Scenario H-1, H-2, H-3)
        if candle.open <= self.active_stop:
            exit_fill = candle.open * (Decimal("1.0") - STOP_SLIPPAGE)
            q_to_sell = self.q_remaining
            basis_released = self.cost_basis_remaining

            if not self.tp1_executed:
                exit_type = "STOPPED_OUT_FULL"
                scenario = "H-1"
            elif self.trailing_stop_active:
                exit_type = "TRAILING_STOPPED_OUT"
                scenario = "H-3"
            else:
                exit_type = "BREAKEVEN_STOPPED_OUT"
                scenario = "H-2"

            ex = self._execute_exit(
                timestamp_ms=candle.timestamp_ms,
                exit_type=exit_type,
                scenario=scenario,
                q_sold=q_to_sell,
                fill_price=exit_fill,
                basis_released=basis_released,
                terminal=True
            )
            return [ex]

        # Gap Above TP2 (Scenario G)
        if candle.open >= self.tp2_price:
            exit_fill = candle.open * (Decimal("1.0") - LIMIT_SLIPPAGE)
            q_to_sell = self.q_remaining
            basis_released = self.cost_basis_remaining
            self.tp1_executed = True
            self.tp2_executed = True

            ex = self._execute_exit(
                timestamp_ms=candle.timestamp_ms,
                exit_type="TP2_EXECUTED_FULL",
                scenario="G",
                q_sold=q_to_sell,
                fill_price=exit_fill,
                basis_released=basis_released,
                terminal=True
            )
            return [ex]

        # Gap Above TP1 (Scenario F: P_TP1 <= Open < P_TP2, TP1 not yet executed)
        if candle.open >= self.tp1_price and not self.tp1_executed:
            tp1_q = self.q_received * Decimal("0.50")
            tp1_q = min(tp1_q, self.q_remaining)
            tp1_fill = candle.open * (Decimal("1.0") - LIMIT_SLIPPAGE)
            tp1_basis = (self.cost_basis_remaining / self.q_remaining) * tp1_q

            ex1 = self._execute_exit(
                timestamp_ms=candle.timestamp_ms,
                exit_type="TP1_EXECUTED_PARTIAL",
                scenario="F",
                q_sold=tp1_q,
                fill_price=tp1_fill,
                basis_released=tp1_basis,
                terminal=False
            )
            executions.append(ex1)
            self.tp1_executed = True
            self.breakeven_stop_active = True
            self.initial_stop_active = False
            self.update_active_stop(self.breakeven_stop)

        # ---------------------------------------------------------------------
        # Step 3: Intrabar Precedence (Scenarios A through L)
        # ---------------------------------------------------------------------

        # Condition 3A: Conservative Stop-First Precedence (Scenario J, Scenario D)
        # If low reaches active stop, stop executes first!
        if candle.low <= self.active_stop:
            exit_fill = self.active_stop * (Decimal("1.0") - STOP_SLIPPAGE)
            q_to_sell = self.q_remaining
            basis_released = self.cost_basis_remaining

            if not self.tp1_executed:
                if candle.high >= self.tp2_price:
                    scenario = "J"  # Extreme range candle (stop first over TP2)
                elif candle.high >= self.tp1_price:
                    scenario = "D"  # Both stop and TP1 touched, stop executes first
                else:
                    scenario = "L"  # Standard initial stopout
                exit_type = "STOPPED_OUT_FULL"
            elif self.trailing_stop_active:
                scenario = "C"
                exit_type = "TRAILING_STOPPED_OUT"
            else:
                scenario = "B"
                exit_type = "BREAKEVEN_STOPPED_OUT"

            ex = self._execute_exit(
                timestamp_ms=candle.timestamp_ms,
                exit_type=exit_type,
                scenario=scenario,
                q_sold=q_to_sell,
                fill_price=exit_fill,
                basis_released=basis_released,
                terminal=True
            )
            executions.append(ex)
            return executions

        # Condition 3B: TP1 Execution (Scenario K, A)
        if not self.tp1_executed and candle.high >= self.tp1_price:
            tp1_q = self.q_received * Decimal("0.50")
            tp1_q = min(tp1_q, self.q_remaining)
            tp1_fill = self.tp1_price * (Decimal("1.0") - LIMIT_SLIPPAGE)
            tp1_basis = (self.cost_basis_remaining / self.q_remaining) * tp1_q

            ex1 = self._execute_exit(
                timestamp_ms=candle.timestamp_ms,
                exit_type="TP1_EXECUTED_PARTIAL",
                scenario="K",
                q_sold=tp1_q,
                fill_price=tp1_fill,
                basis_released=tp1_basis,
                terminal=False
            )
            executions.append(ex1)
            self.tp1_executed = True
            self.breakeven_stop_active = True
            self.initial_stop_active = False
            self.update_active_stop(self.breakeven_stop)

            # Check if same candle reaches TP2 after TP1 (Scenario A)
            if candle.high >= self.tp2_price:
                tp2_q = self.q_remaining
                tp2_fill = self.tp2_price * (Decimal("1.0") - LIMIT_SLIPPAGE)
                tp2_basis = self.cost_basis_remaining

                ex2 = self._execute_exit(
                    timestamp_ms=candle.timestamp_ms,
                    exit_type="TP2_EXECUTED_FULL",
                    scenario="A",
                    q_sold=tp2_q,
                    fill_price=tp2_fill,
                    basis_released=tp2_basis,
                    terminal=True
                )
                self.tp2_executed = True
                executions.append(ex2)
                return executions

        # Condition 3C: TP2 Execution for post-TP1 position
        if self.tp1_executed and not self.tp2_executed and candle.high >= self.tp2_price:
            tp2_q = self.q_remaining
            tp2_fill = self.tp2_price * (Decimal("1.0") - LIMIT_SLIPPAGE)
            tp2_basis = self.cost_basis_remaining

            ex = self._execute_exit(
                timestamp_ms=candle.timestamp_ms,
                exit_type="TP2_EXECUTED_FULL",
                scenario="A",
                q_sold=tp2_q,
                fill_price=tp2_fill,
                basis_released=tp2_basis,
                terminal=True
            )
            self.tp2_executed = True
            executions.append(ex)
            return executions

        # ---------------------------------------------------------------------
        # Step 4: Close Ratchet (Trailing Stop & Time Stop)
        # ---------------------------------------------------------------------

        # Trailing Stop Ratchet
        if self.tp1_executed and not self.tp2_executed and atr14 is not None:
            r_dist = self.tp1_price - self.entry_price
            trail_threshold = self.entry_price + (r_dist * Decimal("1.33333333"))
            if candle.high >= trail_threshold:
                sl_trail = candle.high - (Decimal("1.5") * atr14)
                if sl_trail > self.active_stop:
                    self.update_active_stop(sl_trail)
                    self.trailing_stop_active = True

        # Time Stop Exit (bars_held >= 12 and TP1 not executed)
        if self.bars_held >= self.time_stop_bars and not self.tp1_executed:
            exit_fill = candle.close * (Decimal("1.0") - STOP_SLIPPAGE)
            q_to_sell = self.q_remaining
            basis_released = self.cost_basis_remaining

            ex = self._execute_exit(
                timestamp_ms=candle.timestamp_ms,
                exit_type="TIME_STOP_EXIT",
                scenario="TIME_STOP",
                q_sold=q_to_sell,
                fill_price=exit_fill,
                basis_released=basis_released,
                terminal=True
            )
            executions.append(ex)
            return executions

        return executions

    def _execute_exit(
        self,
        timestamp_ms: int,
        exit_type: str,
        scenario: str,
        q_sold: Decimal,
        fill_price: Decimal,
        basis_released: Decimal,
        terminal: bool
    ) -> ExitExecution:
        gross = q_sold * fill_price
        fee = gross * FEE_RATE
        net = gross - fee
        pnl = net - basis_released

        self.q_remaining -= q_sold
        self.cost_basis_remaining -= basis_released
        self.realized_pnl += pnl

        if terminal or self.q_remaining <= Decimal("0"):
            self.status = "FULLY_EXITED"
            self.exit_reason = exit_type

        return ExitExecution(
            timestamp_ms=timestamp_ms,
            symbol=self.symbol,
            exit_type=exit_type,
            scenario=scenario,
            q_sold=q_sold,
            q_remaining=self.q_remaining,
            fill_price=fill_price,
            cost_basis_released=basis_released,
            gross_proceeds=gross,
            fee_paid=fee,
            net_proceeds=net,
            realized_pnl=pnl
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_time_ms": self.entry_time_ms,
            "entry_price": str(self.entry_price),
            "q_order": str(self.q_order),
            "q_received": str(self.q_received),
            "q_remaining": str(self.q_remaining),
            "cost_basis_remaining": str(self.cost_basis_remaining),
            "initial_stop": str(self.initial_stop),
            "active_stop": str(self.active_stop),
            "breakeven_stop": str(self.breakeven_stop),
            "tp1_price": str(self.tp1_price),
            "tp2_price": str(self.tp2_price),
            "tp1_executed": self.tp1_executed,
            "tp2_executed": self.tp2_executed,
            "initial_stop_active": self.initial_stop_active,
            "breakeven_stop_active": self.breakeven_stop_active,
            "trailing_stop_active": self.trailing_stop_active,
            "highest_since_entry": str(self.highest_since_entry),
            "bars_held": self.bars_held,
            "time_stop_bars": self.time_stop_bars,
            "status": self.status,
            "exit_reason": self.exit_reason,
            "realized_pnl": str(self.realized_pnl),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActivePosition":
        return cls(
            symbol=d["symbol"],
            entry_time_ms=d["entry_time_ms"],
            entry_price=Decimal(d["entry_price"]),
            q_order=Decimal(d["q_order"]),
            q_received=Decimal(d["q_received"]),
            q_remaining=Decimal(d["q_remaining"]),
            cost_basis_remaining=Decimal(d["cost_basis_remaining"]),
            initial_stop=Decimal(d["initial_stop"]),
            active_stop=Decimal(d["active_stop"]),
            breakeven_stop=Decimal(d["breakeven_stop"]),
            tp1_price=Decimal(d["tp1_price"]),
            tp2_price=Decimal(d["tp2_price"]),
            tp1_executed=bool(d["tp1_executed"]),
            tp2_executed=bool(d["tp2_executed"]),
            initial_stop_active=bool(d["initial_stop_active"]),
            breakeven_stop_active=bool(d["breakeven_stop_active"]),
            trailing_stop_active=bool(d["trailing_stop_active"]),
            highest_since_entry=Decimal(d.get("highest_since_entry", d["entry_price"])),
            bars_held=int(d["bars_held"]),
            time_stop_bars=int(d.get("time_stop_bars", 12)),
            status=d["status"],
            exit_reason=d.get("exit_reason"),
            realized_pnl=Decimal(d.get("realized_pnl", "0")),
        )


def create_active_position(
    symbol: str,
    entry_time_ms: int,
    entry_price: Decimal,
    q_order: Decimal,
    initial_stop: Decimal,
    atr14: Decimal,
    entry_fee_rate: Decimal = Decimal("0.0"),
    fee_rate: Decimal = FEE_RATE,
    slippage: Decimal = STOP_SLIPPAGE
) -> ActivePosition:
    """Factory creating an ActivePosition with exact target and breakeven formulations (§§ 6, 9, 11)."""
    q_received = q_order  # In Spot USDT quote fee model, base asset quantity received == q_order
    cost_basis = entry_price * q_order * (Decimal("1.0") + entry_fee_rate)

    # Risk distance R_dist = P_entry - SL_initial
    r_dist = entry_price - initial_stop

    # TP1: +1.5R, TP2: +3.0R
    tp1_price = entry_price + (Decimal("1.5") * r_dist)
    tp2_price = entry_price + (Decimal("3.0") * r_dist)

    # Breakeven Stop formula (Example 16)
    # P_BE = P_entry / (1 - f_buy - f_sell - s_sell)
    denom_be = Decimal("1.0") - fee_rate - fee_rate - slippage
    breakeven_stop = entry_price / denom_be

    return ActivePosition(
        symbol=symbol,
        entry_time_ms=entry_time_ms,
        entry_price=entry_price,
        q_order=q_order,
        q_received=q_received,
        q_remaining=q_received,
        cost_basis_remaining=cost_basis,
        initial_stop=initial_stop,
        active_stop=initial_stop,
        breakeven_stop=breakeven_stop,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        highest_since_entry=entry_price
    )
