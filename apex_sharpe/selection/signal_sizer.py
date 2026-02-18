"""
SignalSizer — map composite signal strength to position risk budget.

Sizing uses THREE inputs:
    1. core_count → base multiplier (3→1.0x, 4→1.5x, 5→2.0x)
    2. composite → composite multiplier (MULTI_SIGNAL_STRONG→1.5x, etc.)
    3. groups_firing → group bonus (+15% per extra group beyond core)

    risk_budget = base_risk × core_mult × composite_mult × (1 + group_bonus) × calendar
"""

from typing import Dict, Optional

from ..config import SignalSizingCfg
from ..types import SignalStrength


class SignalSizer:
    """Compute risk budget from full signal context."""

    def __init__(self, config: SignalSizingCfg = None):
        self.config = config or SignalSizingCfg()
        # Build lookups
        self._multipliers = {int(k): float(v)
                             for k, v in self.config.multipliers}
        self._composite_mult = {str(k): float(v)
                                for k, v in self.config.composite_multipliers}

    def compute(self, core_count: int,
                capital_override: Optional[float] = None,
                calendar_modifier: float = 1.0,
                composite: Optional[str] = None,
                groups_firing: int = 0,
                wing_count: int = 0,
                fund_count: int = 0,
                mom_count: int = 0) -> Dict:
        """Compute risk budget from full signal context.

        Args:
            core_count: Number of core signals firing (2-5).
            capital_override: Override account capital (for backtest).
            calendar_modifier: Calendar overlay multiplier.
            composite: Composite signal name (MULTI_SIGNAL_STRONG, etc.).
            groups_firing: Number of signal groups firing (0-4).
            wing_count: Wing signals firing (0-2).
            fund_count: Funding signals firing (0-2).
            mom_count: Momentum signals firing (0-3).

        Returns:
            Dict with risk_budget, multiplier, base_risk, strength, details.
        """
        cfg = self.config
        capital = capital_override or cfg.account_capital
        base_risk = capital * cfg.base_risk_pct
        max_risk = capital * cfg.max_risk_pct

        # 1. Core multiplier
        core_mult = self._multipliers.get(core_count, 1.0)
        # For composites that don't need core (FUNDING_STRESS with 0-1 core),
        # use a floor multiplier based on total signal activity
        total_signals = core_count + wing_count + fund_count + mom_count
        if core_count < 2 and total_signals >= 3:
            core_mult = max(core_mult, 0.8)

        # 2. Composite multiplier
        composite_mult = self._composite_mult.get(composite, 1.0) if composite else 1.0

        # 3. Group bonus: extra groups beyond core add to conviction
        extra_groups = max(0, groups_firing - 1)  # core is the baseline group
        group_bonus = 1.0 + extra_groups * cfg.group_bonus_pct

        # Combined multiplier
        multiplier = core_mult * composite_mult * group_bonus * calendar_modifier

        risk_budget = min(base_risk * multiplier, max_risk)

        # Map to strength enum using total signal picture
        strength = self._classify_strength(
            core_count, groups_firing, composite)

        return {
            "risk_budget": round(risk_budget, 2),
            "base_risk": round(base_risk, 2),
            "multiplier": round(multiplier, 3),
            "core_mult": round(core_mult, 2),
            "composite_mult": round(composite_mult, 2),
            "group_bonus": round(group_bonus, 2),
            "core_count": core_count,
            "groups_firing": groups_firing,
            "composite": composite,
            "strength": strength,
            "capital": capital,
        }

    @staticmethod
    def _classify_strength(core_count: int, groups_firing: int,
                           composite: Optional[str]) -> SignalStrength:
        """Classify overall signal strength from full context."""
        if composite == "MULTI_SIGNAL_STRONG" or groups_firing >= 3:
            return SignalStrength.EXTREME
        if core_count >= 5:
            return SignalStrength.EXTREME
        if core_count >= 4 or (core_count >= 3 and groups_firing >= 2):
            return SignalStrength.VERY_STRONG
        if (core_count >= 3
                or composite in ("FUNDING_STRESS", "WING_PANIC",
                                 "VOL_ACCELERATION")):
            return SignalStrength.STRONG
        if core_count >= 2 or groups_firing >= 2:
            return SignalStrength.MODERATE
        return SignalStrength.NONE

    def max_daily_budget(self,
                         capital_override: Optional[float] = None) -> float:
        """Maximum total risk deployable in one day."""
        capital = capital_override or self.config.account_capital
        return capital * self.config.max_daily_risk_pct
