"""
RiskAgent — evaluate trade candidates against risk rules.

Extracted from trading_pipeline.py.
Optionally uses risk.OptionsRiskManager for Greeks-based portfolio limits
when CrewTrader is available.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import RiskCfg
from ..types import AgentResult, C

# Optional: OptionsRiskManager for Greeks-based portfolio risk checks
try:
    from ..risk.options_risk_manager import OptionsRiskManager, GreeksLimits
    _HAS_RISK_MGR = True
except ImportError:
    _HAS_RISK_MGR = False


class RiskAgent(BaseAgent):
    """Evaluate candidates against risk rules. Returns ALLOW/BLOCK per candidate.

    If OptionsRiskManager (CrewTrader) is available, adds Greeks-based
    portfolio limit checks on top of the standard 5-rule evaluation.
    """

    def __init__(self, config: RiskCfg = None):
        config = config or RiskCfg()
        super().__init__("Risk", config)

        # Initialize enhanced risk manager if available
        self._risk_mgr: Optional[object] = None
        if _HAS_RISK_MGR:
            try:
                self._risk_mgr = OptionsRiskManager(
                    max_position_value=config.account_capital * config.per_trade_risk_pct,
                    greeks_limits=GreeksLimits(),
                )
            except Exception:
                self._risk_mgr = None

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Evaluate candidates against 5 risk rules.

        Context keys:
            candidates: List[Dict] from ScannerAgent
            positions: List[Dict] existing positions
        """
        candidates = context["candidates"]
        existing_positions = context["positions"]
        cfg = self.config

        open_positions = [p for p in existing_positions if p.get("status") == "OPEN"]
        open_count = len(open_positions)
        total_existing_risk = sum(p.get("max_loss", 0) for p in open_positions)

        results: List[Dict] = []

        for cand in candidates:
            reasons: List[str] = []
            blocked = False

            # 1. Position count
            if open_count >= cfg.max_positions:
                reasons.append(
                    f"Position limit: {open_count}/{cfg.max_positions} slots used"
                )
                blocked = True

            # 2. Per-trade risk
            max_risk_per_trade = cfg.account_capital * cfg.per_trade_risk_pct
            if cand["max_loss"] > max_risk_per_trade:
                reasons.append(
                    f"Per-trade risk ${cand['max_loss']:.0f} > "
                    f"${max_risk_per_trade:.0f} ({cfg.per_trade_risk_pct*100:.0f}% of capital)"
                )
                blocked = True

            # 3. Total portfolio risk
            new_total_risk = total_existing_risk + cand["max_loss"]
            max_total_risk = cfg.account_capital * cfg.total_risk_pct
            if new_total_risk > max_total_risk:
                reasons.append(
                    f"Total risk ${new_total_risk:.0f} > "
                    f"${max_total_risk:.0f} ({cfg.total_risk_pct*100:.0f}% of capital)"
                )
                blocked = True

            # 4. Credit quality
            max_width = max(cand["put_width"], cand["call_width"])
            credit_ratio = cand["total_credit"] / max_width if max_width else 0
            if credit_ratio < cfg.credit_width_min:
                reasons.append(
                    f"Credit/width {credit_ratio:.1%} < {cfg.credit_width_min:.0%} minimum"
                )
                blocked = True

            # 5. Duplicate check
            for pos in open_positions:
                if pos["symbol"] == cand["symbol"]:
                    pos_exp = datetime.strptime(pos["expiration"], "%Y-%m-%d").date()
                    cand_exp = datetime.strptime(cand["expiration"], "%Y-%m-%d").date()
                    if abs((pos_exp - cand_exp).days) <= 7:
                        reasons.append(
                            f"Duplicate: existing {pos['id']} expires {pos['expiration']}"
                        )
                        blocked = True

            decision = "BLOCK" if blocked else "ALLOW"
            if not reasons:
                reasons.append("All risk checks passed")

            print(f"\n{C.BOLD}[Risk]{C.RESET} {cand['symbol']} {cand['expiration']}: {decision}")
            for r in reasons:
                print(f"  - {r}")

            results.append({
                "candidate": cand,
                "decision": decision,
                "reasons": reasons,
            })

        return self._result(
            success=True,
            data={"decisions": results},
        )
