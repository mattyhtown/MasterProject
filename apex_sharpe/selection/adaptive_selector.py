"""
AdaptiveSelector — choose best trade structure based on vol surface conditions.

Uses ORATS summary data (IV rank, skew, contango) to rank structures by
expected edge given current market regime.

Decision matrix:
    High IV (rank > 50) + high skew  -> Bull Put Spread (sell rich puts)
    Low IV (rank < 30) + strong sig  -> Long Call (cheap convexity)
    Moderate IV + moderate skew      -> Call Debit Spread (balanced R:R)
    Very strong (4+ sigs) + any IV   -> Call Ratio Spread or BWB (leverage)
"""

from typing import Dict, List, Optional, Tuple

from ..config import AdaptiveSelectorCfg
from ..types import TradeStructure


class AdaptiveSelector:
    """Select and rank trade structures based on vol surface conditions."""

    def __init__(self, config: AdaptiveSelectorCfg = None):
        self.config = config or AdaptiveSelectorCfg()

    def select(self, summary: Dict, core_count: int,
               iv_rank: Optional[float] = None) -> List[Tuple[TradeStructure, str]]:
        """Select ranked trade structures for current conditions.

        Args:
            summary: ORATS summary dict with vol surface fields.
            core_count: Number of core signals firing.
            iv_rank: IV rank (0-100). If None, extracted from summary.

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

        ranked: List[Tuple[TradeStructure, str, float]] = []

        # Score each structure based on conditions
        # Bull Put Spread: best when IV is high and skew is steep
        bps_score = 0.0
        if iv_rank > cfg.high_iv_rank:
            bps_score += 3.0
        if skew > cfg.high_skew:
            bps_score += 2.0
        if contango > 0.05:
            bps_score += 1.0
        ranked.append((TradeStructure.BULL_PUT_SPREAD,
                        f"IV rank {iv_rank:.0f}, skew {skew:.4f}", bps_score))

        # Long Call: best when IV is low (cheap premium) and strong signal
        lc_score = 0.0
        if iv_rank < cfg.low_iv_rank:
            lc_score += 3.0
        if core_count >= 4:
            lc_score += 2.0
        if contango < 0.03:  # low contango = near-term stress = bounce likely
            lc_score += 1.0
        ranked.append((TradeStructure.LONG_CALL,
                        f"IV rank {iv_rank:.0f}, {core_count} signals", lc_score))

        # Call Debit Spread: balanced — works in moderate conditions
        cds_score = 2.5  # baseline default
        if cfg.low_iv_rank <= iv_rank <= cfg.high_iv_rank:
            cds_score += 1.5  # sweet spot
        if 0.01 <= skew <= cfg.high_skew:
            cds_score += 1.0
        ranked.append((TradeStructure.CALL_DEBIT_SPREAD,
                        f"IV rank {iv_rank:.0f}, balanced", cds_score))

        # Call Ratio Spread: best with strong signal + moderate-high IV
        crs_score = 0.0
        if core_count >= cfg.strong_signal_min:
            crs_score += 3.0
        if iv_rank > 40:  # need some IV to sell
            crs_score += 1.5
        if skew > 0.01:
            crs_score += 0.5
        ranked.append((TradeStructure.CALL_RATIO_SPREAD,
                        f"{core_count} signals, IV rank {iv_rank:.0f}",
                        crs_score))

        # Broken Wing Butterfly: best with very strong signal, targeting pin
        bwb_score = 0.0
        if core_count >= 5:
            bwb_score += 3.0
        elif core_count >= 4:
            bwb_score += 1.5
        if 30 < iv_rank < 60:  # moderate IV
            bwb_score += 1.0
        ranked.append((TradeStructure.BROKEN_WING_BUTTERFLY,
                        f"{core_count} signals, targeting pin", bwb_score))

        # Put Debit Spread (bearish): best when high IV + steep skew
        # (same conditions as BPS but opposite direction — for bearish signals)
        pds_score = 0.0
        if iv_rank > cfg.high_iv_rank:
            pds_score += 2.0
        if skew > cfg.high_skew:
            pds_score += 2.0  # steep skew = rich put premium, but we're buying
        if contango < 0.02:   # collapsing contango = near-term fear
            pds_score += 2.0
        ranked.append((TradeStructure.PUT_DEBIT_SPREAD,
                        f"IV rank {iv_rank:.0f}, skew {skew:.4f}", pds_score))

        # Long Put (bearish): best when IV low + strong bearish conviction
        lput_score = 0.0
        if iv_rank < cfg.low_iv_rank:
            lput_score += 3.0  # cheap premium
        if core_count >= 4:
            lput_score += 2.0
        if contango < 0.02:
            lput_score += 1.0
        ranked.append((TradeStructure.LONG_PUT,
                        f"IV rank {iv_rank:.0f}, {core_count} signals", lput_score))

        # Sort by score descending
        ranked.sort(key=lambda x: x[2], reverse=True)

        return [(s, r) for s, r, _ in ranked]

    def select_top(self, summary: Dict, core_count: int,
                   iv_rank: Optional[float] = None) -> Tuple[TradeStructure, str]:
        """Return the single best structure."""
        choices = self.select(summary, core_count, iv_rank)
        return choices[0]

    @staticmethod
    def _safe(d: Dict, key: str, default: float = 0.0) -> float:
        v = d.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default
