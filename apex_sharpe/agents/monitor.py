"""
MonitorAgent — position valuation and alert generation.

Extracted from trading_pipeline.py (estimate_from_chain, estimate_from_model,
generate_alerts functions).

Optionally uses greeks.GreeksCalculator for enhanced valuation with
Black-Scholes Greeks when financepy is available.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import MonitorCfg
from ..types import AgentResult

# Optional: GreeksCalculator for enhanced valuation
try:
    from ..greeks.greeks_calculator import (
        GreeksCalculator,
        OptionContract as GKContract,
        OptionType as GKOptionType,
    )
    _HAS_GREEKS = True
except ImportError:
    _HAS_GREEKS = False


def calculate_dte(expiration_str: str) -> int:
    exp = datetime.strptime(expiration_str, "%Y-%m-%d").date()
    return (exp - date.today()).days


def estimate_from_chain(position: Dict, chain_data: Dict) -> Dict:
    """Value a position using live chain data."""
    strikes_data = chain_data["data"]
    strike_lookup: Dict[float, Dict] = {s["strike"]: s for s in strikes_data}

    current_value = 0.0
    leg_details: List[Dict] = []

    for leg in position["legs"]:
        strike = leg["strike"]
        chain_strike = strike_lookup.get(strike)
        if not chain_strike:
            chain_strike = min(strikes_data, key=lambda s: abs(s["strike"] - strike))

        if leg["type"] == "PUT":
            bid = chain_strike.get("putBidPrice", 0)
            ask = chain_strike.get("putAskPrice", 0)
            mid = (bid + ask) / 2
            delta = chain_strike.get("delta", 0.5) - 1
        else:
            bid = chain_strike.get("callBidPrice", 0)
            ask = chain_strike.get("callAskPrice", 0)
            mid = (bid + ask) / 2
            delta = chain_strike.get("delta", 0.5)

        if leg["action"] == "BUY":
            current_value += mid * 100
        else:
            current_value -= mid * 100

        leg_details.append({
            "strike": strike,
            "type": leg["type"],
            "action": leg["action"],
            "entry_price": leg["entry_price"],
            "current_mid": round(mid, 2),
            "current_delta": round(delta, 4),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
        })

    entry_credit = position["entry_credit"] * 100
    pnl = entry_credit + current_value

    return {
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / position["max_profit"] * 100, 1) if position["max_profit"] else 0,
        "leg_details": leg_details,
        "data_source": "LIVE_CHAIN",
    }


def estimate_from_model(position: Dict, current_price: float) -> Dict:
    """Simple theta/price estimate when chain data unavailable."""
    dte = calculate_dte(position["expiration"])
    entry_dte = (
        datetime.strptime(position["expiration"], "%Y-%m-%d").date()
        - datetime.strptime(position["entry_date"], "%Y-%m-%d").date()
    ).days
    time_passed_pct = max(0, 1 - dte / entry_dte) if entry_dte > 0 else 0
    theta_profit = position["entry_credit"] * 100 * time_passed_pct * 0.6

    lower_be = position["breakeven_lower"]
    upper_be = position["breakeven_upper"]
    price_impact = 0.0
    if current_price < lower_be:
        price_impact = -(lower_be - current_price) * 50
    elif current_price > upper_be:
        price_impact = -(current_price - upper_be) * 50

    pnl = theta_profit + price_impact
    pnl = max(-position["max_loss"], min(position["max_profit"], pnl))

    return {
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / position["max_profit"] * 100, 1) if position["max_profit"] else 0,
        "leg_details": [],
        "data_source": "ESTIMATED",
    }


def generate_alerts(position: Dict, current_price: float, valuation: Dict,
                    config: MonitorCfg = None) -> List[Dict]:
    """Generate alerts based on position state."""
    cfg = config or MonitorCfg()
    alerts: List[Dict] = []
    dte = calculate_dte(position["expiration"])

    # Profit target
    if valuation["pnl"] >= position["max_profit"] * cfg.profit_target_pct:
        alerts.append({
            "level": "ACTION",
            "message": (
                f"PROFIT TARGET: P&L ${valuation['pnl']:.0f} >= "
                f"50% max profit (${position['max_profit'] * cfg.profit_target_pct:.0f})"
            ),
            "action": "CLOSE POSITION - Take profit",
        })

    # Loss
    if valuation["pnl"] <= -position["max_loss"] * cfg.loss_exit_pct:
        alerts.append({
            "level": "ACTION",
            "message": f"MAX LOSS: P&L ${valuation['pnl']:.0f} at max loss limit",
            "action": "CLOSE POSITION - Cut loss",
        })
    elif valuation["pnl"] <= -position["max_loss"] * cfg.loss_warning_pct:
        alerts.append({
            "level": "WARNING",
            "message": f"LOSS WARNING: P&L ${valuation['pnl']:.0f} approaching max loss",
            "action": "Consider closing or rolling",
        })

    # DTE
    if dte <= cfg.dte_exit:
        alerts.append({
            "level": "ACTION",
            "message": f"DTE EXIT: {dte} DTE <= {cfg.dte_exit} threshold",
            "action": "CLOSE POSITION - DTE rule",
        })
    elif dte <= cfg.dte_warning:
        alerts.append({
            "level": "WARNING",
            "message": f"DTE WARNING: {dte} DTE approaching exit threshold ({cfg.dte_exit})",
            "action": "Prepare to close",
        })

    # Breakeven proximity
    if current_price <= position["breakeven_lower"] + cfg.breakeven_buffer:
        dist = current_price - position["breakeven_lower"]
        alerts.append({
            "level": "WARNING" if dist > 0 else "ACTION",
            "message": (
                f"LOWER BREAKEVEN: ${current_price:.2f} is "
                f"${dist:.2f} from lower BE (${position['breakeven_lower']})"
            ),
            "action": "Monitor closely" if dist > 0 else "CLOSE - Breakeven breached",
        })

    if current_price >= position["breakeven_upper"] - cfg.breakeven_buffer:
        dist = position["breakeven_upper"] - current_price
        alerts.append({
            "level": "WARNING" if dist > 0 else "ACTION",
            "message": (
                f"UPPER BREAKEVEN: ${current_price:.2f} is "
                f"${dist:.2f} from upper BE (${position['breakeven_upper']})"
            ),
            "action": "Monitor closely" if dist > 0 else "CLOSE - Breakeven breached",
        })

    # Delta check
    if valuation.get("leg_details"):
        portfolio_delta = 0.0
        for ld in valuation["leg_details"]:
            d = ld.get("current_delta", 0)
            if ld["action"] == "SELL":
                portfolio_delta -= d
            else:
                portfolio_delta += d

        if abs(portfolio_delta) >= cfg.delta_exit:
            alerts.append({
                "level": "ACTION",
                "message": f"DELTA BREACH: Portfolio delta {portfolio_delta:.4f} exceeds {cfg.delta_exit}",
                "action": "CLOSE POSITION - Delta too high",
            })
        elif abs(portfolio_delta) >= cfg.delta_warning:
            alerts.append({
                "level": "WARNING",
                "message": f"DELTA WARNING: Portfolio delta {portfolio_delta:.4f} approaching limit",
                "action": "Monitor closely",
            })

    return alerts


def estimate_greeks(position: Dict, current_price: float,
                    chain_data: Optional[Dict] = None) -> Optional[Dict]:
    """Calculate position Greeks using GreeksCalculator (if available).

    When chain_data is provided, uses actual implied volatilities from the
    live chain for each leg. Otherwise falls back to a flat 20% IV estimate.

    Returns dict with greeks data, or None if financepy unavailable.
    """
    if not _HAS_GREEKS:
        return None

    try:
        calc = GreeksCalculator()
        greeks_by_leg = []
        total_delta = total_gamma = total_theta = total_vega = 0.0

        # Build IV lookup from chain if available (ORATS uses callMidIv/putMidIv)
        iv_lookup_call: Dict[float, float] = {}
        iv_lookup_put: Dict[float, float] = {}
        if chain_data and chain_data.get("data"):
            for row in chain_data["data"]:
                s = row["strike"]
                civ = row.get("callMidIv") or row.get("smvVol") or 0.20
                piv = row.get("putMidIv") or row.get("smvVol") or 0.20
                iv_lookup_call[s] = civ if civ > 0 else 0.20
                iv_lookup_put[s] = piv if piv > 0 else 0.20

        for leg in position["legs"]:
            opt_type = GKOptionType.PUT if leg["type"] == "PUT" else GKOptionType.CALL
            qty = -1 if leg["action"] == "SELL" else 1
            exp_date = datetime.strptime(position["expiration"], "%Y-%m-%d").date()

            # Use chain IV if available, else position IV, else 20% default
            if leg["type"] == "PUT":
                iv = iv_lookup_put.get(leg["strike"], leg.get("iv", 0.20))
            else:
                iv = iv_lookup_call.get(leg["strike"], leg.get("iv", 0.20))

            contract = GKContract(
                option_type=opt_type,
                strike=Decimal(str(leg["strike"])),
                expiration_date=exp_date,
                quantity=qty,
                implied_volatility=Decimal(str(abs(iv))),
            )

            gd = calc.calculate_greeks(
                contract,
                Decimal(str(current_price)),
            )
            total_delta += float(gd.delta) * qty * 100
            total_gamma += float(gd.gamma) * qty * 100
            total_theta += float(gd.theta) * qty * 100
            total_vega += float(gd.vega) * qty * 100

            greeks_by_leg.append({
                "strike": leg["strike"],
                "type": leg["type"],
                "action": leg["action"],
                "iv": round(float(iv), 4),
                "delta": round(float(gd.delta), 4),
                "gamma": round(float(gd.gamma), 6),
                "theta": round(float(gd.theta), 4),
                "vega": round(float(gd.vega), 4),
            })

        return {
            "portfolio_delta": round(total_delta, 4),
            "portfolio_gamma": round(total_gamma, 6),
            "portfolio_theta": round(total_theta, 4),
            "portfolio_vega": round(total_vega, 4),
            "legs": greeks_by_leg,
        }
    except Exception:
        return None


class MonitorAgent(BaseAgent):
    """Monitor open positions — valuate and generate alerts.

    If GreeksCalculator (financepy) is available, enriches valuation
    with Black-Scholes Greeks for each leg.
    """

    def __init__(self, config: MonitorCfg = None):
        config = config or MonitorCfg()
        super().__init__("Monitor", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Monitor a single position.

        Context keys:
            position: Dict — the position to monitor
            current_price: float
            chain: Optional[Dict] — live chain data
        """
        pos = context["position"]
        current_price = context["current_price"]
        chain = context.get("chain")

        # Valuate
        if chain and chain.get("data"):
            valuation = estimate_from_chain(pos, chain)
        else:
            valuation = estimate_from_model(pos, current_price)

        # Enrich with Greeks if available (pass chain for accurate IV)
        greeks = estimate_greeks(pos, current_price, chain)
        if greeks:
            valuation["greeks"] = greeks

        # Generate alerts
        alerts = generate_alerts(pos, current_price, valuation, self.config)

        return self._result(
            success=True,
            data={
                "position": pos,
                "current_price": current_price,
                "valuation": valuation,
                "alerts": alerts,
            },
        )
