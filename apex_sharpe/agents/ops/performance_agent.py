"""
PerformanceAgent — validates strategy performance against expectations.

Tracks rolling metrics per strategy, detects drift from backtest baselines,
and measures execution quality (actual slippage vs modeled).
"""

import math
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from ...types import AgentResult, C


class PerformanceAgent(BaseAgent):
    """Strategy performance monitoring and drift detection."""

    def __init__(self, config=None):
        super().__init__("Performance", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "report")
        trades = context.get("trades", [])
        positions = context.get("positions", [])

        if action == "validate_strategy":
            return self._validate_strategy(trades, context.get("strategy"))
        elif action == "drift_check":
            return self._drift_check(trades)
        elif action == "execution_quality":
            return self._execution_quality(trades)
        else:
            return self._full_report(trades, positions)

    def _validate_strategy(self, trades: List[Dict],
                           strategy: Optional[str]) -> AgentResult:
        """Compare live results vs backtest baseline for a strategy."""
        if strategy:
            trades = [t for t in trades if t.get("structure") == strategy]

        if len(trades) < 5:
            return self._result(
                success=True,
                data={"status": "INSUFFICIENT_DATA", "trade_count": len(trades)},
                messages=[f"Need >= 5 trades for validation, have {len(trades)}"],
            )

        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        pnls = [t.get("pnl", 0) for t in trades]

        win_rate = len(wins) / len(trades) if trades else 0
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        total_pnl = sum(pnls)
        sharpe = self._rolling_sharpe(pnls)

        # Drawdown
        peak = 0
        max_dd = 0
        running = 0
        for pnl in pnls:
            running += pnl
            peak = max(peak, running)
            dd = peak - running
            max_dd = max(max_dd, dd)

        status = "OK"
        warnings = []
        if win_rate < 0.50:
            warnings.append(f"Win rate {win_rate:.0%} below 50%")
            status = "WARNING"
        if sharpe < 1.0:
            warnings.append(f"Sharpe {sharpe:.2f} below 1.0 threshold")
            status = "WARNING"
        if max_dd > total_pnl * 0.5 and total_pnl > 0:
            warnings.append(f"Max drawdown ${max_dd:.0f} > 50% of total P&L")
            status = "WARNING"

        return self._result(
            success=True,
            data={
                "status": status,
                "strategy": strategy or "ALL",
                "trade_count": len(trades),
                "win_rate": round(win_rate, 3),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "total_pnl": round(total_pnl, 2),
                "sharpe": round(sharpe, 3),
                "max_drawdown": round(max_dd, 2),
            },
            messages=warnings,
        )

    def _drift_check(self, trades: List[Dict]) -> AgentResult:
        """Detect when rolling metrics diverge from baseline."""
        window = 20
        if len(trades) < window:
            return self._result(
                success=True,
                data={"status": "INSUFFICIENT_DATA"},
                messages=[f"Need {window}+ trades for drift check"],
            )

        # Compare last `window` trades vs all prior
        recent = trades[-window:]
        prior = trades[:-window]

        if len(prior) < window:
            return self._result(
                success=True,
                data={"status": "INSUFFICIENT_BASELINE"},
            )

        recent_pnls = [t.get("pnl", 0) for t in recent]
        prior_pnls = [t.get("pnl", 0) for t in prior]

        recent_mean = sum(recent_pnls) / len(recent_pnls)
        prior_mean = sum(prior_pnls) / len(prior_pnls)
        prior_std = self._std(prior_pnls)

        drift_z = (recent_mean - prior_mean) / prior_std if prior_std > 0 else 0

        drifting = abs(drift_z) > 2.0
        direction = "degrading" if drift_z < -2.0 else "improving" if drift_z > 2.0 else "stable"

        return self._result(
            success=True,
            data={
                "drifting": drifting,
                "drift_z": round(drift_z, 2),
                "direction": direction,
                "recent_mean_pnl": round(recent_mean, 2),
                "baseline_mean_pnl": round(prior_mean, 2),
                "baseline_std": round(prior_std, 2),
            },
            messages=[f"Strategy drift: {direction} (z={drift_z:.2f})"] if drifting else [],
        )

    def _execution_quality(self, trades: List[Dict]) -> AgentResult:
        """Measure actual vs modeled execution quality."""
        scored = [t for t in trades if "actual_slippage" in t and "modeled_slippage" in t]
        if not scored:
            return self._result(
                success=True,
                data={"status": "NO_EXECUTION_DATA"},
                messages=["No trades have actual_slippage / modeled_slippage fields"],
            )

        ratios = []
        for t in scored:
            mod = t["modeled_slippage"]
            act = t["actual_slippage"]
            if mod > 0:
                ratios.append(act / mod)

        avg_ratio = sum(ratios) / len(ratios) if ratios else 1.0
        status = "OK" if avg_ratio <= 1.5 else "WARNING"

        return self._result(
            success=True,
            data={
                "status": status,
                "avg_slippage_ratio": round(avg_ratio, 3),
                "trades_scored": len(scored),
                "worst_ratio": round(max(ratios), 3) if ratios else 0,
            },
        )

    def _full_report(self, trades: List[Dict],
                     positions: List[Dict]) -> AgentResult:
        """Full performance dashboard."""
        # Per-structure breakdown
        structures = {}
        for t in trades:
            s = t.get("structure", "unknown")
            structures.setdefault(s, []).append(t)

        breakdown = {}
        for s, s_trades in structures.items():
            pnls = [t.get("pnl", 0) for t in s_trades]
            wins = sum(1 for p in pnls if p > 0)
            breakdown[s] = {
                "count": len(s_trades),
                "win_rate": round(wins / len(s_trades), 3) if s_trades else 0,
                "total_pnl": round(sum(pnls), 2),
                "sharpe": round(self._rolling_sharpe(pnls), 3),
            }

        all_pnls = [t.get("pnl", 0) for t in trades]

        return self._result(
            success=True,
            data={
                "total_trades": len(trades),
                "open_positions": len([p for p in positions if p.get("status") == "OPEN"]),
                "total_pnl": round(sum(all_pnls), 2),
                "overall_sharpe": round(self._rolling_sharpe(all_pnls), 3),
                "by_structure": breakdown,
            },
        )

    @staticmethod
    def _rolling_sharpe(pnls: List[float], risk_free: float = 0.0) -> float:
        if len(pnls) < 2:
            return 0.0
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(variance) if variance > 0 else 0
        if std == 0:
            return 0.0
        return (mean - risk_free) / std

    @staticmethod
    def _std(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(variance)

    def print_report(self, result: AgentResult) -> None:
        """Pretty-print performance report."""
        d = result.data
        print(f"\n{C.BOLD}{'='*60}")
        print(f"  PERFORMANCE REPORT")
        print(f"{'='*60}{C.RESET}")
        print(f"  Total trades: {d.get('total_trades', 0)}")
        print(f"  Open positions: {d.get('open_positions', 0)}")
        print(f"  Total P&L: {C.GREEN if d.get('total_pnl', 0) >= 0 else C.RED}"
              f"${d.get('total_pnl', 0):,.2f}{C.RESET}")
        print(f"  Overall Sharpe: {d.get('overall_sharpe', 0):.3f}")

        by_structure = d.get("by_structure", {})
        if by_structure:
            print(f"\n  {'Structure':<25} {'Count':>6} {'Win%':>6} {'P&L':>10} {'Sharpe':>7}")
            print(f"  {'-'*55}")
            for s, m in by_structure.items():
                clr = C.GREEN if m["total_pnl"] >= 0 else C.RED
                print(f"  {s:<25} {m['count']:>6} {m['win_rate']:>5.0%}"
                      f" {clr}${m['total_pnl']:>9,.2f}{C.RESET} {m['sharpe']:>7.3f}")
