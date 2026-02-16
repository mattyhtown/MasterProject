"""
StrategyAgentBase — abstract base for all directional trade structure agents.

Each strategy agent has 3 sub-components:
  - EntryOptimizer: find_strikes(chain, spot, config) -> strike dict
  - ExecutionModel: simulate_entry(strikes, config) -> fill dict
  - RiskManager: compute_risk(strikes) + check_exit(position, price) -> exit reason

Plus a build_backtest_trade() method for historical simulation.

Execution haircuts are baked into every structure:
  - Slippage: 3% on debit, 3% adverse on credit
  - Commission: $0.65/leg
  - Wide bid-ask penalty: additional cost when spread > 10% of mid
"""

from abc import abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from ..base import BaseAgent
from ...types import AgentResult, TradeStructure


class StrategyAgentBase(BaseAgent):
    """Abstract base for directional trade structure agents."""

    # Subclasses must set these
    STRUCTURE: TradeStructure = None
    NUM_LEGS: int = 0

    def __init__(self, name: str, config: Any = None):
        super().__init__(name, config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Build a trade from chain data.

        Context keys:
            chain: List[Dict] — filtered strikes for target expiry
            spot: float — current spot price
            risk_budget: float — max risk for this trade
            mode: str — 'live' or 'backtest'
            next_close: float — (backtest only) next-day close for P&L
        """
        chain = context["chain"]
        spot = context["spot"]
        risk_budget = context["risk_budget"]
        mode = context.get("mode", "live")

        # Step 1: Find optimal strikes
        strikes = self.find_strikes(chain, spot)
        if strikes is None:
            return self._result(success=False,
                                errors=["No valid strikes found"])

        # Step 2: Simulate execution with realistic fills
        fill = self.simulate_entry(strikes, risk_budget)
        if fill is None:
            return self._result(success=False,
                                errors=["Fill simulation failed"])

        # Step 3: Compute risk metrics
        risk = self.compute_risk(strikes, fill)

        # Step 4: Backtest P&L if applicable
        if mode == "backtest" and "next_close" in context:
            pnl = self.compute_pnl(strikes, fill, context["next_close"])
            fill["pnl"] = pnl

        return self._result(
            success=True,
            data={
                "structure": self.STRUCTURE.value if self.STRUCTURE else self.name,
                "strikes": strikes,
                "fill": fill,
                "risk": risk,
            },
        )

    # -- Sub-components (override in subclasses) -------------------------

    @abstractmethod
    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        """Find optimal strikes from the chain.

        Returns:
            Dict with strike prices and leg details, or None if not found.
        """
        ...

    @abstractmethod
    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        """Simulate trade entry with realistic fills.

        Applies slippage, commissions, and bid-ask spread penalties.

        Returns:
            Dict with entry_cost/credit, qty, max_risk, max_profit, etc.
        """
        ...

    @abstractmethod
    def compute_risk(self, strikes: Dict, fill: Dict) -> Dict:
        """Compute risk metrics for the trade.

        Returns:
            Dict with max_loss, breakeven(s), risk_reward_ratio, greeks est.
        """
        ...

    @abstractmethod
    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        """Compute P&L at a given exit price (for backtest)."""
        ...

    @abstractmethod
    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        """Check if position should be exited.

        Returns:
            Exit reason string, or None if hold.
        """
        ...

    # -- Shared helpers --------------------------------------------------

    @staticmethod
    def _find_calls(strikes: List[Dict], target_delta: float,
                    tol: float) -> List[Dict]:
        """Find call options near target delta."""
        matches = []
        for row in strikes:
            d = row.get("delta")
            if d is not None and d > 0 and abs(d - target_delta) <= tol:
                matches.append(row)
        matches.sort(key=lambda r: abs(r["delta"] - target_delta))
        return matches

    @staticmethod
    def _find_puts(strikes: List[Dict], target_abs_delta: float,
                   tol: float) -> List[Dict]:
        """Find put options near target absolute delta."""
        matches = []
        for row in strikes:
            cd = row.get("delta")
            if cd is None:
                continue
            pd = cd - 1  # Convert call delta to put delta
            if abs(abs(pd) - target_abs_delta) <= tol:
                r = dict(row)
                r["put_delta"] = pd
                matches.append(r)
        matches.sort(key=lambda r: abs(abs(r["put_delta"]) - target_abs_delta))
        return matches

    @staticmethod
    def _bid_ask_penalty(bid: float, ask: float) -> float:
        """Extra cost when bid-ask spread is wide.

        If spread > 10% of mid, add half the excess as execution cost.
        This models real-world fill degradation on illiquid strikes.
        """
        if bid <= 0 or ask <= 0:
            return 0.0
        mid = (bid + ask) / 2
        if mid <= 0:
            return 0.0
        spread_pct = (ask - bid) / mid
        if spread_pct > 0.10:
            excess = spread_pct - 0.10
            return mid * excess * 0.5
        return 0.0
