"""Global Portfolio Allocation Coordinator, Macro Surge Limits & FIFO Queue.

Conforms to SMART_MONEY_PHASE2_SPEC_v2.1.5_FINAL.md (§§ 5, 10)
and SMART_MONEY_PHASE2_SPEC_v2.1.6_AMENDMENT.md.

Strictly Binance Spot Long/BUY only. No live trading, shadow mode only.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from .state_store import CashLedger, StateStore


MAX_CONCURRENT_POSITIONS = 5
DEFAULT_RISK_FRACTION = Decimal("0.01")  # 1% equity risk default
MINIMUM_NOTIONAL_USDT = Decimal("10.00")
BASE_FEE_RATE = Decimal("0.00075")       # 0.075% BNB fee
QUOTE_FEE_RATE = Decimal("0.00100")      # 0.100% USDT standard fee
STOP_SLIPPAGE_RATE = Decimal("0.0008")   # 8 bps slippage on stop-market fill


@dataclass
class SizingResult:
    accepted: bool
    rejection_reason: Optional[str] = None
    q_order: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")
    required_cash: Decimal = Decimal("0")
    d_gross: Decimal = Decimal("0")
    d_net: Decimal = Decimal("0")
    fee_mode: str = "QUOTE"


@dataclass
class CandidateRankingEntry:
    symbol: str
    timestamp_ms: int
    breakout_score: float
    readiness_score: float
    normalized_delta: float
    rvol: float
    entry_price: Decimal
    stop_price: Decimal
    setup_type: str  # "CONTINUATION" or "RETEST"
    payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def ranking_key(self) -> Tuple[float, float, float, float, str]:
        """Deterministic 5-key intra-bar ranking hierarchy (§ 10.2).
        1. Breakout Conviction Score DESC
        2. Accumulation Quality / Readiness Score DESC
        3. Normalized Delta DESC
        4. RVOL DESC
        5. Symbol ASC (lexicographical)
        """
        return (
            -self.breakout_score,
            -self.readiness_score,
            -self.normalized_delta,
            -self.rvol,
            self.symbol
        )


class PortfolioCoordinator:
    """Manages slot allocation, FIFO queue, macro surges, and capital commitment."""

    def __init__(
        self,
        cash_ledger: Optional[CashLedger] = None,
        state_store: Optional[StateStore] = None,
        max_positions: int = MAX_CONCURRENT_POSITIONS,
        risk_fraction: Decimal = DEFAULT_RISK_FRACTION
    ):
        self.max_positions = max_positions
        self.risk_fraction = risk_fraction
        self.cash_ledger = cash_ledger if cash_ledger is not None else CashLedger()
        self.state_store = state_store

        # Active allocated symbols
        self.active_positions: Dict[str, Any] = {}
        # Reserved slots for confirmed signals pending open
        self.reserved_slots: Dict[str, CandidateRankingEntry] = {}
        # FIFO confirmation queue for confirmed signals waiting for capacity
        self.fifo_confirmation_queue: List[CandidateRankingEntry] = []
        # Macro surge state
        self.macro_surge_active: bool = False

    @property
    def total_equity(self) -> Decimal:
        """Total portfolio equity: available_cash + allocated_cost_basis + realized_pnl adjustment."""
        return self.cash_ledger.initial_capital + self.cash_ledger.realized_pnl

    @property
    def available_slots(self) -> int:
        used = len(self.active_positions) + len(self.reserved_slots)
        return max(0, self.max_positions - used)

    def process_closed_bar_candidates(
        self,
        timestamp_ms: int,
        candidates: List[CandidateRankingEntry]
    ) -> List[CandidateRankingEntry]:
        """Evaluates macro surge and sorts intra-bar candidates (§ 10.1, § 10.2)."""
        if not candidates:
            return []

        # Step 3: Macro Surge Detection: M >= 4 candidates
        if len(candidates) >= 4:
            self.macro_surge_active = True
        else:
            self.macro_surge_active = False

        # Step 4: Deterministic Intra-Bar Ranking
        sorted_candidates = sorted(candidates, key=lambda c: c.ranking_key)
        return sorted_candidates

    def enqueue_confirmed_candidate(self, candidate: CandidateRankingEntry) -> bool:
        """Enqueues confirmed candidate into FIFO queue or reserves immediate slot (§ 10.1)."""
        if self.available_slots > 0:
            # Immediate slot reservation
            self.reserved_slots[candidate.symbol] = candidate
            return True
        else:
            # Enqueue into FIFO waiting queue
            self.fifo_confirmation_queue.append(candidate)
            return False

    def release_closed_position(self, symbol: str) -> Optional[CandidateRankingEntry]:
        """Releases position slot and promotes oldest eligible candidate from FIFO queue."""
        self.active_positions.pop(symbol, None)
        self.reserved_slots.pop(symbol, None)

        if self.fifo_confirmation_queue and self.available_slots > 0:
            promoted = self.fifo_confirmation_queue.pop(0)
            self.reserved_slots[promoted.symbol] = promoted
            return promoted
        return None

    def calculate_order_sizing(
        self,
        entry_price: Decimal,
        stop_price: Decimal,
        fee_mode: str = "QUOTE",
        r_usd: Optional[Decimal] = None,
        f_buy: Decimal = QUOTE_FEE_RATE,
        f_sell: Decimal = QUOTE_FEE_RATE,
        s_sell: Decimal = STOP_SLIPPAGE_RATE
    ) -> SizingResult:
        """Calculates fee-adjusted risk sizing and validates risk gates (§ 5.2, § 5.3, Examples 1 & 2)."""
        if entry_price <= Decimal("0") or stop_price <= Decimal("0"):
            return SizingResult(accepted=False, rejection_reason="INVALID_PRICES")

        d_gross = entry_price - stop_price

        # Canonical D_net formulation (Examples 1, 2, 5)
        if fee_mode == "BASE":
            # Example 1: D_net = P_entry - SL * (1 - f_sell - s_sell)
            d_net = entry_price - (stop_price * (Decimal("1.0") - f_sell - s_sell))
        else:
            # Example 2: D_net = P_entry * (1 + f_buy) - SL * (1 - f_sell - s_sell)
            d_net = (entry_price * (Decimal("1.0") + f_buy)) - (stop_price * (Decimal("1.0") - f_sell - s_sell))

        if d_gross <= Decimal("0") or d_net <= Decimal("0"):
            return SizingResult(
                accepted=False,
                rejection_reason="NEGATIVE_NET_DISTANCE",
                d_gross=d_gross,
                d_net=d_net
            )

        # Risk capital in USD
        if r_usd is None:
            r_usd = self.total_equity * self.risk_fraction

        # Sizing: Q = R_usd / D_net
        q_order = r_usd / d_net

        # Cash constraint check (§ 5.3, Examples 3 & 4)
        f_cash = Decimal("0.0") if fee_mode == "BASE" else f_buy
        available = self.cash_ledger.available_cash
        required_cash = entry_price * q_order * (Decimal("1.0") + f_cash)

        if required_cash > available:
            # Scale quantity down to available cash
            q_order = available / (entry_price * (Decimal("1.0") + f_cash))
            required_cash = available

        # Minimum notional check (§ 5.5, Example 6)
        notional = entry_price * q_order
        if notional < MINIMUM_NOTIONAL_USDT:
            return SizingResult(
                accepted=False,
                rejection_reason="BELOW_MINIMUM_NOTIONAL",
                q_order=q_order,
                d_gross=d_gross,
                d_net=d_net
            )

        cost_basis = entry_price * q_order * (Decimal("1.0") + f_cash)

        return SizingResult(
            accepted=True,
            q_order=q_order,
            cost_basis=cost_basis,
            required_cash=required_cash,
            d_gross=d_gross,
            d_net=d_net,
            fee_mode=fee_mode
        )

    def commit_position_entry(
        self,
        symbol: str,
        entry_price: Decimal,
        q_order: Decimal,
        fee_mode: str = "QUOTE"
    ) -> bool:
        """Atomically commits position cash and transitions slot to active (§ 9.3, § 10.1)."""
        f_cash = Decimal("0.0") if fee_mode == "BASE" else QUOTE_FEE_RATE
        cost = entry_price * q_order * (Decimal("1.0") + f_cash)

        if cost > self.cash_ledger.available_cash:
            return False

        # Double-entry cash ledger mutation
        self.cash_ledger.available_cash -= cost
        self.cash_ledger.allocated_cost_basis += cost

        # Verify ledger invariant
        if not self.cash_ledger.verify_invariant():
            raise RuntimeError("Cash ledger invariant broken upon entry commitment")

        # Move from reserved to active
        self.reserved_slots.pop(symbol, None)
        self.active_positions[symbol] = {
            "symbol": symbol,
            "entry_price": entry_price,
            "q_order": q_order,
            "cost_basis": cost,
            "fee_mode": fee_mode
        }
        return True

    def commit_position_exit(
        self,
        symbol: str,
        exit_price: Decimal,
        q_sold: Decimal,
        cost_basis_released: Decimal,
        fee_rate: Decimal = QUOTE_FEE_RATE
    ) -> Decimal:
        """Atomically releases position cash and records realized PnL (§ 9.3)."""
        gross_proceeds = exit_price * q_sold
        net_proceeds = gross_proceeds * (Decimal("1") - fee_rate)
        realized_pnl = net_proceeds - cost_basis_released

        # Double-entry cash ledger mutation
        self.cash_ledger.available_cash += net_proceeds
        self.cash_ledger.allocated_cost_basis -= cost_basis_released
        self.cash_ledger.realized_pnl += realized_pnl

        # Verify ledger invariant
        if not self.cash_ledger.verify_invariant():
            raise RuntimeError("Cash ledger invariant broken upon exit commitment")

        return realized_pnl
