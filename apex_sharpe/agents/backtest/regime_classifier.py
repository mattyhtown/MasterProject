"""
RegimeClassifier — classifies market days into vol/trend regimes.

Uses VIX level + price vs SMA_200 to assign one of 4 regimes:
  LOW_VOL_BULL:  VIX < 18, price > SMA_200
  LOW_VOL_BEAR:  VIX < 18, price < SMA_200
  HIGH_VOL_BULL: VIX >= 18, price > SMA_200
  HIGH_VOL_BEAR: VIX >= 18, price < SMA_200

Optionally splits at VIX=25 for extreme regimes.
"""

import math
from enum import Enum
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from ...types import AgentResult, C


class Regime(Enum):
    LOW_VOL_BULL = "low_vol_bull"
    LOW_VOL_BEAR = "low_vol_bear"
    HIGH_VOL_BULL = "high_vol_bull"
    HIGH_VOL_BEAR = "high_vol_bear"
    EXTREME_VOL_BULL = "extreme_vol_bull"
    EXTREME_VOL_BEAR = "extreme_vol_bear"


class RegimeClassifier(BaseAgent):
    """Market regime classification and analysis."""

    def __init__(self, config=None):
        super().__init__("RegimeClassifier", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "classify")

        if action == "classify":
            return self._classify(
                context.get("daily_data", []),
                context.get("vix_data", {}),
                context.get("vix_threshold", 18.0),
                context.get("extreme_threshold", 25.0),
            )
        elif action == "analyze":
            return self._analyze_regimes(
                context.get("daily_data", []),
                context.get("vix_data", {}),
                context.get("vix_threshold", 18.0),
                context.get("extreme_threshold", 25.0),
                context.get("credit_data", []),
            )
        elif action == "transitions":
            return self._transition_matrix(
                context.get("daily_data", []),
                context.get("vix_data", {}),
            )
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def classify_day(self, close: float, sma_200: float,
                     vix: float, vix_thresh: float = 18.0,
                     extreme_thresh: float = 25.0) -> Regime:
        """Classify a single day into a regime."""
        bull = close > sma_200 if sma_200 > 0 else True

        if vix >= extreme_thresh:
            return Regime.EXTREME_VOL_BULL if bull else Regime.EXTREME_VOL_BEAR
        elif vix >= vix_thresh:
            return Regime.HIGH_VOL_BULL if bull else Regime.HIGH_VOL_BEAR
        else:
            return Regime.LOW_VOL_BULL if bull else Regime.LOW_VOL_BEAR

    def _classify(self, daily_data: List[Dict], vix_data: Dict[str, float],
                  vix_thresh: float, extreme_thresh: float) -> AgentResult:
        """Classify each day and return regime labels."""
        classified = []
        for row in daily_data:
            dt = row["date"]
            vix = vix_data.get(dt, 0)
            regime = self.classify_day(
                row["close"], row.get("sma_200", 0),
                vix, vix_thresh, extreme_thresh,
            )
            classified.append({
                **row,
                "regime": regime.value,
                "vix": vix,
            })

        # Count per regime
        counts = {}
        for r in classified:
            counts[r["regime"]] = counts.get(r["regime"], 0) + 1

        return self._result(
            success=True,
            data={
                "classified": classified,
                "counts": counts,
                "total_days": len(classified),
            },
        )

    def _analyze_regimes(self, daily_data: List[Dict],
                         vix_data: Dict[str, float],
                         vix_thresh: float, extreme_thresh: float,
                         credit_data: List[Dict]) -> AgentResult:
        """Full regime analysis with forward returns."""
        # Classify all days
        classified = []
        for row in daily_data:
            dt = row["date"]
            vix = vix_data.get(dt, 0)
            regime = self.classify_day(
                row["close"], row.get("sma_200", 0),
                vix, vix_thresh, extreme_thresh,
            )
            classified.append({**row, "regime": regime.value, "vix": vix})

        # Build date→index for forward returns
        date_idx = {c["date"]: i for i, c in enumerate(classified)}

        # Credit spread lookup
        credit_by_date = {}
        if credit_data:
            credit_by_date = {c["date"]: c for c in credit_data}

        # Per-regime stats
        regime_stats = {}
        for regime in Regime:
            days = [c for c in classified if c["regime"] == regime.value]
            if not days:
                continue

            fwd_1d = []
            fwd_5d = []
            fwd_20d = []
            credit_signals = 0
            credit_hits = 0

            for d in days:
                idx = date_idx[d["date"]]

                # Forward returns
                if idx + 1 < len(classified):
                    ret_1 = (classified[idx + 1]["close"] - d["close"]) / d["close"]
                    fwd_1d.append(ret_1)
                if idx + 5 < len(classified):
                    ret_5 = (classified[idx + 5]["close"] - d["close"]) / d["close"]
                    fwd_5d.append(ret_5)
                if idx + 20 < len(classified):
                    ret_20 = (classified[idx + 20]["close"] - d["close"]) / d["close"]
                    fwd_20d.append(ret_20)

                # Credit spread signal check
                cs = credit_by_date.get(d["date"])
                if cs and cs["spread_change"] < -0.005:
                    credit_signals += 1
                    if idx + 1 < len(classified):
                        if classified[idx + 1]["close"] > d["close"]:
                            credit_hits += 1

            regime_stats[regime.value] = {
                "days": len(days),
                "pct": round(len(days) / len(classified) * 100, 1),
                "avg_vix": round(sum(d["vix"] for d in days) / len(days), 1),
                "fwd_1d_mean": round(self._mean(fwd_1d) * 100, 3) if fwd_1d else 0,
                "fwd_5d_mean": round(self._mean(fwd_5d) * 100, 3) if fwd_5d else 0,
                "fwd_20d_mean": round(self._mean(fwd_20d) * 100, 3) if fwd_20d else 0,
                "fwd_1d_win_rate": round(
                    sum(1 for r in fwd_1d if r > 0) / len(fwd_1d) * 100, 1
                ) if fwd_1d else 0,
                "credit_signals": credit_signals,
                "credit_hit_rate": round(
                    credit_hits / credit_signals * 100, 1
                ) if credit_signals > 0 else 0,
            }

        return self._result(
            success=True,
            data={
                "regime_stats": regime_stats,
                "total_days": len(classified),
                "date_range": (classified[0]["date"], classified[-1]["date"]) if classified else ("", ""),
            },
        )

    def _transition_matrix(self, daily_data: List[Dict],
                           vix_data: Dict[str, float]) -> AgentResult:
        """Compute regime transition probabilities (Markov chain)."""
        regimes = []
        for row in daily_data:
            vix = vix_data.get(row["date"], 0)
            r = self.classify_day(row["close"], row.get("sma_200", 0), vix)
            regimes.append(r.value)

        transitions = {}
        for i in range(len(regimes) - 1):
            from_r = regimes[i]
            to_r = regimes[i + 1]
            transitions.setdefault(from_r, {})
            transitions[from_r][to_r] = transitions[from_r].get(to_r, 0) + 1

        # Normalize to probabilities
        matrix = {}
        for from_r, targets in transitions.items():
            total = sum(targets.values())
            matrix[from_r] = {
                to_r: round(count / total, 3)
                for to_r, count in sorted(targets.items())
            }

        return self._result(
            success=True,
            data={"transition_matrix": matrix},
        )

    @staticmethod
    def _mean(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def print_report(self, result: AgentResult) -> None:
        d = result.data
        stats = d.get("regime_stats", {})

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  REGIME ANALYSIS")
        print(f"{'='*74}{C.RESET}")

        if d.get("date_range"):
            print(f"  Period: {d['date_range'][0]} to {d['date_range'][1]}")
        print(f"  Total days: {d.get('total_days', 0)}")

        print(f"\n  {'Regime':<22} {'Days':>6} {'%':>6} {'VIX':>6}"
              f" {'1d%':>7} {'5d%':>7} {'20d%':>7} {'Win%':>6}"
              f" {'Cred':>5} {'CHit%':>6}")
        print(f"  {'-'*72}")

        for regime_name, s in stats.items():
            clr = C.GREEN if "bull" in regime_name else C.RED
            print(f"  {clr}{regime_name:<22}{C.RESET}"
                  f" {s['days']:>6} {s['pct']:>5.1f}%"
                  f" {s['avg_vix']:>5.1f}"
                  f" {s['fwd_1d_mean']:>+6.3f}"
                  f" {s['fwd_5d_mean']:>+6.3f}"
                  f" {s['fwd_20d_mean']:>+6.3f}"
                  f" {s['fwd_1d_win_rate']:>5.1f}%"
                  f" {s['credit_signals']:>5}"
                  f" {s['credit_hit_rate']:>5.1f}%")
