"""
AdaptiveSelector — choose best trade structure based on vol surface + composite.

Uses ORATS summary data (IV rank, skew, contango) AND the composite signal type
to rank structures by expected edge.

Composite-aware logic:
    FUNDING_STRESS  → favor credit strategies (sell rich premium from stress)
    WING_PANIC      → favor long convexity (crash skew = cheap opposite wing)
    VOL_ACCELERATION→ favor vol-selling (momentum often reverts intraday)
    MULTI_SIGNAL    → size up, keep standard vol-surface logic
    FEAR_BOUNCE     → original regime-based scoring
"""

from typing import Dict, List, Optional, Tuple

from ..config import AdaptiveSelectorCfg
from ..types import TradeStructure


class AdaptiveSelector:
    """Select and rank trade structures based on vol surface + composite."""

    def __init__(self, config: AdaptiveSelectorCfg = None):
        self.config = config or AdaptiveSelectorCfg()

    def select(self, summary: Dict, core_count: int,
               iv_rank: Optional[float] = None,
               composite: Optional[str] = None,
               groups_firing: int = 0,
               wing_count: int = 0,
               fund_count: int = 0,
               mom_count: int = 0) -> List[Tuple[TradeStructure, str]]:
        """Select ranked trade structures for current conditions.

        Args:
            summary: ORATS summary dict with vol surface fields.
            core_count: Number of core signals firing.
            iv_rank: IV rank (0-100). If None, extracted from summary.
            composite: Composite signal name driving this trade.
            groups_firing: Number of signal groups firing (0-4).
            wing_count: Wing signals firing (0-2).
            fund_count: Funding signals firing (0-2).
            mom_count: Momentum signals firing (0-3).

        Returns:
            List of (TradeStructure, reason) tuples, best first.
        """
        cfg = self.config

        # Extract vol surface metrics
        if iv_rank is None:
            iv_rank = self._safe(summary, "ivRank1m", 50.0)
        skew = (self._safe(summary, "dlt25Iv30d") -
                self._safe(summary, "dlt75Iv30d"))
        contango = self._safe(summary, "contango")

        # Get composite-specific score bonuses
        bonuses = self._composite_bonuses(
            composite, iv_rank, skew, contango,
            wing_count, fund_count, mom_count)

        ranked: List[Tuple[TradeStructure, str, float]] = []

        # --- Bullish structures ---

        # Bull Put Spread: sell rich put premium
        bps_score = bonuses.get(TradeStructure.BULL_PUT_SPREAD, 0.0)
        if iv_rank > cfg.high_iv_rank:
            bps_score += 3.0
        if skew > cfg.high_skew:
            bps_score += 2.0
        if contango > 0.05:
            bps_score += 1.0
        ranked.append((TradeStructure.BULL_PUT_SPREAD,
                        f"IV rank {iv_rank:.0f}, skew {skew:.4f}", bps_score))

        # Long Call: cheap convexity
        lc_score = bonuses.get(TradeStructure.LONG_CALL, 0.0)
        if iv_rank < cfg.low_iv_rank:
            lc_score += 3.0
        if core_count >= 4:
            lc_score += 2.0
        if contango < 0.03:
            lc_score += 1.0
        ranked.append((TradeStructure.LONG_CALL,
                        f"IV rank {iv_rank:.0f}, {core_count} signals", lc_score))

        # Call Debit Spread: balanced
        cds_score = 2.5 + bonuses.get(TradeStructure.CALL_DEBIT_SPREAD, 0.0)
        if cfg.low_iv_rank <= iv_rank <= cfg.high_iv_rank:
            cds_score += 1.5
        if 0.01 <= skew <= cfg.high_skew:
            cds_score += 1.0
        ranked.append((TradeStructure.CALL_DEBIT_SPREAD,
                        f"IV rank {iv_rank:.0f}, balanced", cds_score))

        # Call Ratio Spread: leverage with strong signal
        crs_score = bonuses.get(TradeStructure.CALL_RATIO_SPREAD, 0.0)
        if core_count >= cfg.strong_signal_min or groups_firing >= 3:
            crs_score += 3.0
        if iv_rank > 40:
            crs_score += 1.5
        if skew > 0.01:
            crs_score += 0.5
        ranked.append((TradeStructure.CALL_RATIO_SPREAD,
                        f"{core_count} core + {groups_firing} groups, "
                        f"IV rank {iv_rank:.0f}", crs_score))

        # Broken Wing Butterfly: targeting pin, very strong only
        bwb_score = bonuses.get(TradeStructure.BROKEN_WING_BUTTERFLY, 0.0)
        if core_count >= 5 or (core_count >= 4 and groups_firing >= 3):
            bwb_score += 3.0
        elif core_count >= 4:
            bwb_score += 1.5
        if 30 < iv_rank < 60:
            bwb_score += 1.0
        ranked.append((TradeStructure.BROKEN_WING_BUTTERFLY,
                        f"{core_count} core + {groups_firing} groups, pin",
                        bwb_score))

        # --- Bearish structures ---

        # Put Debit Spread
        pds_score = bonuses.get(TradeStructure.PUT_DEBIT_SPREAD, 0.0)
        if iv_rank > cfg.high_iv_rank:
            pds_score += 2.0
        if skew > cfg.high_skew:
            pds_score += 2.0
        if contango < 0.02:
            pds_score += 2.0
        ranked.append((TradeStructure.PUT_DEBIT_SPREAD,
                        f"IV rank {iv_rank:.0f}, skew {skew:.4f}", pds_score))

        # Long Put
        lput_score = bonuses.get(TradeStructure.LONG_PUT, 0.0)
        if iv_rank < cfg.low_iv_rank:
            lput_score += 3.0
        if core_count >= 4 or groups_firing >= 3:
            lput_score += 2.0
        if contango < 0.02:
            lput_score += 1.0
        ranked.append((TradeStructure.LONG_PUT,
                        f"IV rank {iv_rank:.0f}, {core_count} signals",
                        lput_score))

        # Bear Call Spread
        bcs_score = bonuses.get(TradeStructure.BEAR_CALL_SPREAD, 0.0)
        if iv_rank > cfg.high_iv_rank:
            bcs_score += 2.5
        if skew > cfg.high_skew:
            bcs_score += 1.5
        if contango < 0.02:
            bcs_score += 2.0
        ranked.append((TradeStructure.BEAR_CALL_SPREAD,
                        f"IV rank {iv_rank:.0f}, bearish credit", bcs_score))

        # --- Neutral / vol-sell ---

        # Iron Butterfly
        ifly_score = bonuses.get(TradeStructure.IRON_BUTTERFLY, 0.0)
        if iv_rank > cfg.high_iv_rank:
            ifly_score += 3.0
        if abs(skew) < 0.01:
            ifly_score += 1.5
        if contango > 0.05:
            ifly_score += 1.0
        ranked.append((TradeStructure.IRON_BUTTERFLY,
                        f"IV rank {iv_rank:.0f}, ATM theta", ifly_score))

        # Short Iron Condor
        sic_score = bonuses.get(TradeStructure.SHORT_IRON_CONDOR, 0.0)
        if iv_rank > 40:
            sic_score += 2.0
        if abs(skew) < cfg.high_skew:
            sic_score += 1.0
        if contango > 0.03:
            sic_score += 1.0
        if core_count <= 3 and groups_firing <= 1:
            sic_score += 1.0  # range-bound likely when signals are weak
        ranked.append((TradeStructure.SHORT_IRON_CONDOR,
                        f"IV rank {iv_rank:.0f}, range-bound", sic_score))

        # Sort by score descending
        ranked.sort(key=lambda x: x[2], reverse=True)

        return [(s, r) for s, r, _ in ranked]

    def select_top(self, summary: Dict, core_count: int,
                   iv_rank: Optional[float] = None,
                   composite: Optional[str] = None,
                   groups_firing: int = 0,
                   wing_count: int = 0,
                   fund_count: int = 0,
                   mom_count: int = 0) -> Tuple[TradeStructure, str]:
        """Return the single best structure."""
        choices = self.select(
            summary, core_count, iv_rank=iv_rank, composite=composite,
            groups_firing=groups_firing, wing_count=wing_count,
            fund_count=fund_count, mom_count=mom_count)
        return choices[0]

    @staticmethod
    def _composite_bonuses(
        composite: Optional[str],
        iv_rank: float, skew: float, contango: float,
        wing_count: int, fund_count: int, mom_count: int,
    ) -> Dict[TradeStructure, float]:
        """Return per-structure score bonuses based on which composite fired.

        Each composite type has a different "theory of the trade" that favors
        certain structures over others.
        """
        bonuses: Dict[TradeStructure, float] = {}
        if not composite:
            return bonuses

        if composite == "FUNDING_STRESS":
            # Funding stress = liquidity premium = sell rich premium
            # Credit strategies benefit most from elevated borrow rates
            bonuses[TradeStructure.BULL_PUT_SPREAD] = 3.0
            bonuses[TradeStructure.BEAR_CALL_SPREAD] = 2.5
            bonuses[TradeStructure.SHORT_IRON_CONDOR] = 2.0
            bonuses[TradeStructure.IRON_BUTTERFLY] = 1.5
            # Debit strategies get penalized (paying inflated premium)
            bonuses[TradeStructure.LONG_CALL] = -1.0
            bonuses[TradeStructure.LONG_PUT] = -1.0

        elif composite == "WING_PANIC":
            # Wing skew spike = crash demand on puts = call wing is cheap
            # Buy convexity on the opposite side
            bonuses[TradeStructure.LONG_CALL] = 3.0  # cheap calls during put panic
            bonuses[TradeStructure.CALL_DEBIT_SPREAD] = 2.5
            bonuses[TradeStructure.CALL_RATIO_SPREAD] = 2.0
            # Selling put premium during wing panic is risky
            bonuses[TradeStructure.BULL_PUT_SPREAD] = -1.5
            bonuses[TradeStructure.SHORT_IRON_CONDOR] = -2.0

        elif composite == "VOL_ACCELERATION":
            # Vol momentum often reverts — vol-selling structures benefit
            # But only if IV is already elevated (don't sell cheap vol)
            if iv_rank > 40:
                bonuses[TradeStructure.IRON_BUTTERFLY] = 3.0
                bonuses[TradeStructure.SHORT_IRON_CONDOR] = 2.5
                bonuses[TradeStructure.BULL_PUT_SPREAD] = 2.0
                bonuses[TradeStructure.BEAR_CALL_SPREAD] = 1.5
            else:
                # Vol accelerating from low base — buy convexity
                bonuses[TradeStructure.LONG_CALL] = 2.0
                bonuses[TradeStructure.CALL_DEBIT_SPREAD] = 1.5

        elif composite == "MULTI_SIGNAL_STRONG":
            # Highest conviction — favor leverage structures
            bonuses[TradeStructure.CALL_RATIO_SPREAD] = 2.5
            bonuses[TradeStructure.BROKEN_WING_BUTTERFLY] = 2.0
            bonuses[TradeStructure.LONG_CALL] = 1.5

        elif composite in ("FEAR_BOUNCE_STRONG", "FEAR_BOUNCE_STRONG_OPEX"):
            # Standard fear bounce — original logic works well
            # Slight boost to convexity buys since fear = cheap calls
            bonuses[TradeStructure.LONG_CALL] = 1.0
            bonuses[TradeStructure.CALL_DEBIT_SPREAD] = 0.5

        elif composite == "FEAR_BOUNCE_LONG":
            # Weaker signal — favor defined-risk structures
            bonuses[TradeStructure.CALL_DEBIT_SPREAD] = 1.5
            bonuses[TradeStructure.BULL_PUT_SPREAD] = 1.0
            # Penalize high-leverage structures
            bonuses[TradeStructure.CALL_RATIO_SPREAD] = -1.0
            bonuses[TradeStructure.BROKEN_WING_BUTTERFLY] = -1.0

        return bonuses

    @staticmethod
    def _safe(d: Dict, key: str, default: float = 0.0) -> float:
        v = d.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default
