"""
RegimeClassifier — classify vol surface into actionable market regimes.

Expands beyond FEAR_BOUNCE_STRONG as sole signal indicator. Classifies every
trading day into one of 5 regimes, each with distinct trade implications:

    FEAR        — skewing spike, contango collapse, high RIP
                  → bullish (contrarian bounce). ~11% of days.
    NERVOUS     — elevated vol but not full fear
                  → sell premium (rich IV). ~15% of days.
    FLAT        — low vol, tight range, stable metrics
                  → neutral structures (ICs, IFly). ~40% of days.
    COMPLACENT  — very low IV rank, high contango, zero skewing
                  → sell premium + buy tail protection. ~20% of days.
    GREED       — multi-day complacency + rising prices + compressed vol
                  → bearish reversal setups. ~5% of days.

Uses ORATS summary fields: iv30d, rVol30, ivRank1m, contango, skewing, rip,
dlt25Iv30d, dlt75Iv30d. All thresholds derived from backtest analysis.
"""

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from ..types import AgentResult, C, TradeStructure


class Regime(Enum):
    """Market regime classifications."""
    FEAR = "FEAR"
    NERVOUS = "NERVOUS"
    FLAT = "FLAT"
    COMPLACENT = "COMPLACENT"
    GREED = "GREED"


# Composite signal names per regime
REGIME_COMPOSITES = {
    Regime.FEAR: {
        "strong": "FEAR_BOUNCE_STRONG",
        "moderate": "FEAR_BOUNCE_LONG",
        "direction": "bullish",
        "structures": [
            TradeStructure.CALL_DEBIT_SPREAD,
            TradeStructure.BULL_PUT_SPREAD,
            TradeStructure.LONG_CALL,
        ],
    },
    Regime.NERVOUS: {
        "strong": "ELEVATED_VOL_SELL",
        "moderate": "ELEVATED_VOL_WATCH",
        "direction": "neutral",
        "structures": [
            TradeStructure.IRON_BUTTERFLY,
            TradeStructure.SHORT_IRON_CONDOR,
            TradeStructure.BULL_PUT_SPREAD,
        ],
    },
    Regime.FLAT: {
        "strong": "RANGE_BOUND_STRONG",
        "moderate": "RANGE_BOUND",
        "direction": "neutral",
        "structures": [
            TradeStructure.SHORT_IRON_CONDOR,
            TradeStructure.IRON_BUTTERFLY,
        ],
    },
    Regime.COMPLACENT: {
        "strong": "COMPLACENT_SELL",
        "moderate": "COMPLACENT_WATCH",
        "direction": "neutral",
        "structures": [
            TradeStructure.SHORT_IRON_CONDOR,
            TradeStructure.BEAR_CALL_SPREAD,
            TradeStructure.IRON_BUTTERFLY,
        ],
    },
    Regime.GREED: {
        "strong": "GREED_REVERSAL",
        "moderate": "GREED_WATCH",
        "direction": "bearish",
        "structures": [
            TradeStructure.PUT_DEBIT_SPREAD,
            TradeStructure.BEAR_CALL_SPREAD,
            TradeStructure.LONG_PUT,
        ],
    },
}


@dataclass(frozen=True)
class RegimeThresholds:
    """Thresholds for regime classification.

    Derived from 6-month backtest analysis of ORATS vol surface.
    """
    # FEAR triggers
    fear_skewing: float = 0.05       # skewing > this = put demand surge
    fear_contango: float = 0.02      # contango < this = near-term fear
    fear_rip: float = 70.0           # RIP > this = risk premium spike
    fear_min_triggers: int = 2       # need 2+ fear metrics for FEAR regime

    # NERVOUS triggers (elevated but not full fear)
    nervous_iv_rv_spread: float = 0.03   # iv30d - rVol30 > this
    nervous_skewing: float = 0.025       # moderate skewing
    nervous_iv_rank_min: float = 40.0    # IV rank above this

    # FLAT triggers
    flat_iv_rank_max: float = 40.0       # IV rank below this
    flat_iv_rank_min: float = 20.0       # IV rank above this
    flat_skewing_max: float = 0.025      # low skewing
    flat_contango_min: float = 0.02      # stable term structure

    # COMPLACENT triggers
    complacent_iv_rank_max: float = 20.0   # very low IV rank
    complacent_contango_min: float = 0.05  # elevated contango (complacency)
    complacent_skewing_max: float = 0.015  # near-zero skewing

    # GREED triggers (multi-day complacency)
    greed_iv_rank_max: float = 15.0      # very compressed vol
    greed_contango_min: float = 0.08     # steep contango (overconfidence)
    greed_days_complacent: int = 3       # consecutive complacent days


class VolSurfaceRegimeClassifier(BaseAgent):
    """Classify market regime from vol surface and produce actionable signals.

    This agent runs on ORATS summary data (same input as ZeroDTEAgent) and
    classifies into 5 regimes. Each regime maps to specific trade structures
    and composite signal names for the backtest and live pipeline.

    Unlike ZeroDTEAgent which only fires on FEAR days (~14%), this classifier
    produces a classification (and potentially a signal) for EVERY day.

    Distinct from backtest.RegimeClassifier which uses VIX + SMA200. This
    classifier uses the vol surface directly (skewing, contango, RIP, IV rank).
    """

    def __init__(self, thresholds: RegimeThresholds = None):
        super().__init__("RegimeClassifier", thresholds or RegimeThresholds())
        self.thresholds = thresholds or RegimeThresholds()
        self._history: List[Dict] = []   # rolling window for GREED detection

    @staticmethod
    def _safe(d: Dict, key: str, default: float = 0.0) -> float:
        v = d.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    def classify(self, summary: Dict,
                 prev_summary: Optional[Dict] = None,
                 iv_rank: Optional[float] = None) -> Dict[str, Any]:
        """Classify current vol surface into a market regime.

        Args:
            summary: ORATS summary dict.
            prev_summary: Previous day's summary (for change detection).
            iv_rank: IV rank (0-100). If None, uses ivRank1m from summary.

        Returns:
            Dict with regime, confidence, metrics, composite, direction,
            structures, and all underlying metric values.
        """
        sf = self._safe
        th = self.thresholds

        # Extract core metrics
        iv30 = sf(summary, "iv30d")
        rv30 = sf(summary, "rVol30")
        iv_rv = iv30 - rv30
        skewing = sf(summary, "skewing")
        contango = sf(summary, "contango")
        rip = sf(summary, "rip")
        skew_25d = sf(summary, "dlt25Iv30d") - sf(summary, "dlt75Iv30d")

        if iv_rank is None:
            iv_rank = sf(summary, "ivRank1m", 50.0)

        metrics = {
            "iv30d": round(iv30, 4),
            "rv30d": round(rv30, 4),
            "iv_rv_spread": round(iv_rv, 4),
            "iv_rank": round(iv_rank, 1),
            "skewing": round(skewing, 4),
            "contango": round(contango, 4),
            "rip": round(rip, 1),
            "skew_25d": round(skew_25d, 4),
        }

        # Score each regime
        scores = {}

        # --- FEAR ---
        fear_triggers = 0
        if skewing > th.fear_skewing:
            fear_triggers += 1
        if contango < th.fear_contango:
            fear_triggers += 1
        if rip > th.fear_rip:
            fear_triggers += 1
        if iv_rv < -0.05:  # IV well below RV = vol selling was wrong
            fear_triggers += 1
        scores[Regime.FEAR] = fear_triggers / 4.0

        # --- NERVOUS ---
        nervous_triggers = 0
        if iv_rv > th.nervous_iv_rv_spread:
            nervous_triggers += 1
        if skewing > th.nervous_skewing:
            nervous_triggers += 1
        if iv_rank > th.nervous_iv_rank_min:
            nervous_triggers += 1
        if skew_25d > 0.015:  # put skew elevated
            nervous_triggers += 1
        scores[Regime.NERVOUS] = nervous_triggers / 4.0

        # --- FLAT ---
        flat_triggers = 0
        if th.flat_iv_rank_min <= iv_rank <= th.flat_iv_rank_max:
            flat_triggers += 1
        if skewing < th.flat_skewing_max:
            flat_triggers += 1
        if contango > th.flat_contango_min:
            flat_triggers += 1
        if abs(iv_rv) < 0.03:  # IV and RV close together
            flat_triggers += 1
        scores[Regime.FLAT] = flat_triggers / 4.0

        # --- COMPLACENT ---
        complacent_triggers = 0
        if iv_rank < th.complacent_iv_rank_max:
            complacent_triggers += 1
        if contango > th.complacent_contango_min:
            complacent_triggers += 1
        if skewing < th.complacent_skewing_max:
            complacent_triggers += 1
        if rip < 40:  # low risk premium
            complacent_triggers += 1
        scores[Regime.COMPLACENT] = complacent_triggers / 4.0

        # --- GREED ---
        greed_triggers = 0
        if iv_rank < th.greed_iv_rank_max:
            greed_triggers += 1
        if contango > th.greed_contango_min:
            greed_triggers += 1
        # Check rolling window for multi-day complacency
        recent_complacent = sum(
            1 for h in self._history[-th.greed_days_complacent:]
            if h.get("regime") == Regime.COMPLACENT
        )
        if recent_complacent >= th.greed_days_complacent:
            greed_triggers += 2  # strong boost for sustained complacency
        scores[Regime.GREED] = greed_triggers / 4.0

        # Priority resolution: FEAR > GREED > NERVOUS > COMPLACENT > FLAT
        # FEAR is highest priority (danger), GREED next (reversal), etc.
        if scores[Regime.FEAR] >= 0.5 and fear_triggers >= th.fear_min_triggers:
            regime = Regime.FEAR
            confidence = scores[Regime.FEAR]
        elif scores[Regime.GREED] >= 0.75:
            regime = Regime.GREED
            confidence = scores[Regime.GREED]
        elif scores[Regime.NERVOUS] >= 0.5:
            regime = Regime.NERVOUS
            confidence = scores[Regime.NERVOUS]
        elif scores[Regime.COMPLACENT] >= 0.75:
            regime = Regime.COMPLACENT
            confidence = scores[Regime.COMPLACENT]
        else:
            regime = Regime.FLAT
            confidence = scores[Regime.FLAT]

        # Map to composite signal
        regime_info = REGIME_COMPOSITES[regime]
        if confidence >= 0.75:
            composite = regime_info["strong"]
        elif confidence >= 0.5:
            composite = regime_info["moderate"]
        else:
            composite = None

        result = {
            "regime": regime,
            "regime_name": regime.value,
            "confidence": round(confidence, 2),
            "scores": {r.value: round(s, 2) for r, s in scores.items()},
            "metrics": metrics,
            "composite": composite,
            "direction": regime_info["direction"],
            "structures": [s.value for s in regime_info["structures"]],
        }

        # Update rolling history
        self._history.append({"regime": regime, "metrics": metrics})
        if len(self._history) > 30:
            self._history = self._history[-30:]

        return result

    def classify_series(self, summaries: Dict[str, Dict],
                        iv_ranks: Optional[Dict[str, float]] = None,
                        ) -> Dict[str, Dict]:
        """Classify a time series of summaries (for backtest).

        Args:
            summaries: {date_str: summary_dict} sorted by date.
            iv_ranks: Optional {date_str: iv_rank} overrides.

        Returns:
            {date_str: classification_dict} for each date.
        """
        self._history = []  # reset rolling window
        sorted_dates = sorted(summaries.keys())
        results = {}
        prev = None

        for dt in sorted_dates:
            summary = summaries[dt]
            ivr = (iv_ranks or {}).get(dt)
            result = self.classify(summary, prev_summary=prev, iv_rank=ivr)
            results[dt] = result
            prev = summary

        return results

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Classify regime for a single summary.

        Context keys:
            summary: ORATS summary dict
            iv_rank: Optional IV rank override
        """
        summary = context["summary"]
        iv_rank = context.get("iv_rank")
        result = self.classify(summary, iv_rank=iv_rank)
        return self._result(success=True, data=result)

    # -- terminal display ---------------------------------------------------

    @staticmethod
    def print_regime(result: Dict) -> None:
        """Print regime classification to terminal."""
        regime = result["regime_name"]
        confidence = result["confidence"]
        composite = result.get("composite", "—")
        direction = result["direction"]
        metrics = result["metrics"]
        scores = result["scores"]

        regime_colors = {
            "FEAR": C.RED,
            "NERVOUS": C.YELLOW,
            "FLAT": C.BLUE,
            "COMPLACENT": C.GREEN,
            "GREED": C.MAGENTA,
        }
        clr = regime_colors.get(regime, C.RESET)

        print(f"\n  {C.BOLD}Regime Classification:{C.RESET}")
        print(f"  {clr}{C.BOLD}{regime}{C.RESET} "
              f"(confidence: {confidence:.0%}) → {composite or 'no signal'}")
        print(f"  Direction: {direction} | "
              f"Structures: {', '.join(result['structures'][:3])}")

        print(f"\n  {C.DIM}Scores: ", end="")
        for name, score in scores.items():
            s_clr = regime_colors.get(name, "")
            bar = "=" * int(score * 10)
            print(f"{s_clr}{name}:{score:.0%}{C.RESET}[{bar:<10}] ", end="")
        print(C.RESET)

        print(f"  {C.DIM}Metrics: IV rank={metrics['iv_rank']:.0f} | "
              f"skewing={metrics['skewing']:.4f} | "
              f"contango={metrics['contango']:.4f} | "
              f"RIP={metrics['rip']:.0f} | "
              f"IV-RV={metrics['iv_rv_spread']:+.4f}{C.RESET}")

    def print_regime_summary(self, classifications: Dict[str, Dict]) -> None:
        """Print summary of regime distribution over a period."""
        if not classifications:
            return

        total = len(classifications)
        regime_counts = {}
        regime_composites = {}

        for dt, cls in classifications.items():
            regime = cls["regime_name"]
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            comp = cls.get("composite")
            if comp:
                regime_composites[comp] = regime_composites.get(comp, 0) + 1

        print(f"\n  {C.BOLD}Regime Distribution ({total} days):{C.RESET}")
        regime_colors = {
            "FEAR": C.RED, "NERVOUS": C.YELLOW, "FLAT": C.BLUE,
            "COMPLACENT": C.GREEN, "GREED": C.MAGENTA,
        }
        for regime in ["FEAR", "NERVOUS", "FLAT", "COMPLACENT", "GREED"]:
            count = regime_counts.get(regime, 0)
            pct = count / total if total else 0
            clr = regime_colors.get(regime, "")
            bar = "=" * int(pct * 40)
            print(f"  {clr}{regime:<12}{C.RESET} {count:>4}d ({pct:>5.1%}) {bar}")

        if regime_composites:
            print(f"\n  {C.BOLD}Signal Days by Composite:{C.RESET}")
            for comp, count in sorted(regime_composites.items(),
                                      key=lambda x: -x[1]):
                pct = count / total
                print(f"    {comp:<30} {count:>3}d ({pct:.1%})")
