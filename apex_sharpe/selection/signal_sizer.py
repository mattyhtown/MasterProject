"""
SignalSizer — map composite signal strength to position risk budget.

Core logic:
    base_risk = account_capital * risk_pct_per_trade
    multiplier = f(core_count)
    risk_budget = min(base_risk * multiplier, max_risk)
"""

from typing import Dict, Optional

from ..config import SignalSizingCfg
from ..types import SignalStrength


class SignalSizer:
    """Compute risk budget from signal strength."""

    def __init__(self, config: SignalSizingCfg = None):
        self.config = config or SignalSizingCfg()
        # Build lookup: {core_count: multiplier}
        self._multipliers = {int(k): float(v)
                             for k, v in self.config.multipliers}

    def compute(self, core_count: int,
                capital_override: Optional[float] = None) -> Dict:
        """Compute risk budget for a given signal strength.

        Args:
            core_count: Number of core signals firing (2-5).
            capital_override: Override account capital (for backtest).

        Returns:
            Dict with risk_budget, multiplier, base_risk, strength.
        """
        cfg = self.config
        capital = capital_override or cfg.account_capital
        base_risk = capital * cfg.base_risk_pct
        max_risk = capital * cfg.max_risk_pct

        # Lookup multiplier — default to 1.0 for unknown counts
        multiplier = self._multipliers.get(core_count, 1.0)

        risk_budget = min(base_risk * multiplier, max_risk)

        # Map to strength enum
        if core_count >= 5:
            strength = SignalStrength.EXTREME
        elif core_count >= 4:
            strength = SignalStrength.VERY_STRONG
        elif core_count >= 3:
            strength = SignalStrength.STRONG
        elif core_count >= 2:
            strength = SignalStrength.MODERATE
        else:
            strength = SignalStrength.NONE

        return {
            "risk_budget": round(risk_budget, 2),
            "base_risk": round(base_risk, 2),
            "multiplier": multiplier,
            "core_count": core_count,
            "strength": strength,
            "capital": capital,
        }

    def max_daily_budget(self,
                         capital_override: Optional[float] = None) -> float:
        """Maximum total risk deployable in one day."""
        capital = capital_override or self.config.account_capital
        return capital * self.config.max_daily_risk_pct
