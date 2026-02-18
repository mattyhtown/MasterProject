"""
ThetaMaximizer — find optimal strike placement for credit strategies.

For each credit structure (Bull Put Spread, Bear Call Spread, Iron Butterfly,
Short Iron Condor), scans all valid strike combinations and ranks by:

  1. Theta efficiency  = net_theta / max_risk  (theta per dollar of risk)
  2. Theta/Gamma ratio = net_theta / |net_gamma|  (decay vs pin risk)
  3. Credit/Width      = credit / spread_width  (premium density)

Works with both ORATS chain data (historical or live) and IB chain data,
as long as strikes have: strike, delta, theta, gamma, callBidPrice,
callAskPrice, putBidPrice, putAskPrice.

Usage:
    from apex_sharpe.selection.theta_maximizer import ThetaMaximizer
    tm = ThetaMaximizer()
    results = tm.scan(chain, spot, structure="Short Iron Condor")
    tm.print_results(results)
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..types import C, TradeStructure


@dataclass(frozen=True)
class ThetaMaxCfg:
    """Theta maximizer configuration."""
    # Strike filtering
    min_delta: float = 0.05       # Skip deep OTM (low theta, illiquid)
    max_delta: float = 0.55       # Skip deep ITM
    min_credit: float = 0.10      # Minimum credit per contract
    min_bid: float = 0.05         # Skip strikes with no bid
    # Risk limits
    max_risk_per: float = 5000.0  # Max risk per contract ($)
    slippage: float = 0.03        # Assumed fill slippage
    # Ranking
    objective: str = "theta_efficiency"  # theta_efficiency, theta_gamma, credit_risk
    top_n: int = 5                # Return top N combos


# Credit structures to optimize
CREDIT_STRUCTURES = {
    "Bull Put Spread": TradeStructure.BULL_PUT_SPREAD,
    "Bear Call Spread": TradeStructure.BEAR_CALL_SPREAD,
    "Iron Butterfly": TradeStructure.IRON_BUTTERFLY,
    "Short Iron Condor": TradeStructure.SHORT_IRON_CONDOR,
}


class ThetaMaximizer:
    """Find strike combos that maximize theta decay per unit of risk."""

    def __init__(self, config: ThetaMaxCfg = None):
        self.config = config or ThetaMaxCfg()

    def _filter_strikes(self, chain: List[Dict],
                        expiry: str = None) -> List[Dict]:
        """Filter to usable strikes with valid Greeks."""
        cfg = self.config
        filtered = []
        for s in chain:
            if expiry and s.get("expirDate") != expiry:
                continue
            d = s.get("delta")
            theta = s.get("theta")
            if d is None or theta is None:
                continue
            # Call delta range check
            if not (cfg.min_delta <= abs(d) <= cfg.max_delta or
                    cfg.min_delta <= abs(d - 1) <= cfg.max_delta):
                continue
            # Must have some bid
            if (s.get("callBidPrice", 0) < cfg.min_bid and
                    s.get("putBidPrice", 0) < cfg.min_bid):
                continue
            filtered.append(s)
        return filtered

    def _find_nearest_expiry(self, chain: List[Dict],
                             max_dte: int = 2) -> Optional[str]:
        """Find the nearest expiry with good strike coverage."""
        from datetime import datetime, date
        today = date.today().strftime("%Y-%m-%d")
        expiries = sorted(set(s.get("expirDate", "") for s in chain))
        for exp in expiries:
            if exp >= today:
                dte = s.get("dte", 0) if chain else 0
                # Count strikes in this expiry
                n = sum(1 for s in chain if s.get("expirDate") == exp)
                if n >= 10:
                    return exp
        return expiries[0] if expiries else None

    # -- Bull Put Spread -------------------------------------------------------

    def _scan_bull_put(self, strikes: List[Dict],
                       spot: float) -> List[Dict]:
        """Scan all valid Bull Put Spread combos."""
        cfg = self.config
        # Short put = higher strike (closer to ATM), long put = lower
        puts = []
        for s in strikes:
            cd = s.get("delta", 0)
            pd = cd - 1  # put delta
            abs_pd = abs(pd)
            if cfg.min_delta <= abs_pd <= cfg.max_delta:
                puts.append({**s, "put_delta": pd, "abs_put_delta": abs_pd})

        puts.sort(key=lambda s: s["strike"])
        results = []

        for i, short in enumerate(puts):
            for j, long in enumerate(puts):
                if long["strike"] >= short["strike"]:
                    continue
                width = short["strike"] - long["strike"]
                if width <= 0 or width > 200:  # sanity
                    continue

                credit = (short.get("putBidPrice", 0) -
                          long.get("putAskPrice", 0))
                if credit <= cfg.min_credit:
                    continue

                credit_slip = credit * (1 - cfg.slippage)
                risk_per = (width - credit_slip) * 100
                if risk_per <= 0 or risk_per > cfg.max_risk_per:
                    continue

                # Net theta: short put theta is positive for seller
                # ORATS theta is negative (decay), so selling makes it positive
                net_theta = abs(short.get("theta", 0)) - abs(long.get("theta", 0))
                net_gamma = abs(short.get("gamma", 0)) - abs(long.get("gamma", 0))

                theta_eff = net_theta / (risk_per / 100) if risk_per > 0 else 0
                theta_gamma = (net_theta / abs(net_gamma)
                               if net_gamma != 0 else 999)
                credit_risk = credit_slip / width if width > 0 else 0

                results.append({
                    "structure": "Bull Put Spread",
                    "short_strike": short["strike"],
                    "long_strike": long["strike"],
                    "short_delta": short["put_delta"],
                    "long_delta": long["put_delta"],
                    "width": width,
                    "credit": round(credit_slip, 4),
                    "risk_per": round(risk_per, 2),
                    "net_theta": round(net_theta, 4),
                    "net_gamma": round(net_gamma, 6),
                    "theta_efficiency": round(theta_eff, 4),
                    "theta_gamma": round(theta_gamma, 2),
                    "credit_risk": round(credit_risk, 4),
                })

        return results

    # -- Bear Call Spread ------------------------------------------------------

    def _scan_bear_call(self, strikes: List[Dict],
                        spot: float) -> List[Dict]:
        """Scan all valid Bear Call Spread combos."""
        cfg = self.config
        calls = [s for s in strikes
                 if cfg.min_delta <= s.get("delta", 0) <= cfg.max_delta]
        calls.sort(key=lambda s: s["strike"])
        results = []

        for short in calls:
            for long in calls:
                if long["strike"] <= short["strike"]:
                    continue
                width = long["strike"] - short["strike"]
                if width <= 0 or width > 200:
                    continue

                credit = (short.get("callBidPrice", 0) -
                          long.get("callAskPrice", 0))
                if credit <= cfg.min_credit:
                    continue

                credit_slip = credit * (1 - cfg.slippage)
                risk_per = (width - credit_slip) * 100
                if risk_per <= 0 or risk_per > cfg.max_risk_per:
                    continue

                net_theta = abs(short.get("theta", 0)) - abs(long.get("theta", 0))
                net_gamma = abs(short.get("gamma", 0)) - abs(long.get("gamma", 0))

                theta_eff = net_theta / (risk_per / 100) if risk_per > 0 else 0
                theta_gamma = (net_theta / abs(net_gamma)
                               if net_gamma != 0 else 999)
                credit_risk = credit_slip / width if width > 0 else 0

                results.append({
                    "structure": "Bear Call Spread",
                    "short_strike": short["strike"],
                    "long_strike": long["strike"],
                    "short_delta": short.get("delta", 0),
                    "long_delta": long.get("delta", 0),
                    "width": width,
                    "credit": round(credit_slip, 4),
                    "risk_per": round(risk_per, 2),
                    "net_theta": round(net_theta, 4),
                    "net_gamma": round(net_gamma, 6),
                    "theta_efficiency": round(theta_eff, 4),
                    "theta_gamma": round(theta_gamma, 2),
                    "credit_risk": round(credit_risk, 4),
                })

        return results

    # -- Iron Butterfly --------------------------------------------------------

    def _scan_iron_butterfly(self, strikes: List[Dict],
                             spot: float) -> List[Dict]:
        """Scan Iron Butterfly combos: sell ATM straddle + buy OTM wings."""
        cfg = self.config

        # ATM candidates: calls near 0.50 delta
        atm_calls = [s for s in strikes
                     if 0.40 <= s.get("delta", 0) <= 0.60]
        # Wing calls: low delta OTM calls
        wing_calls = [s for s in strikes
                      if cfg.min_delta <= s.get("delta", 0) <= 0.30]
        # Wing puts: low delta OTM puts
        wing_puts = []
        for s in strikes:
            pd = s.get("delta", 0) - 1
            if cfg.min_delta <= abs(pd) <= 0.30:
                wing_puts.append({**s, "put_delta": pd})

        results = []
        for atm in atm_calls:
            atm_s = atm["strike"]
            for wc in wing_calls:
                if wc["strike"] <= atm_s:
                    continue
                for wp in wing_puts:
                    if wp["strike"] >= atm_s:
                        continue

                    call_credit = (atm.get("callBidPrice", 0) -
                                   wc.get("callAskPrice", 0))
                    put_credit = (atm.get("putBidPrice", 0) -
                                  wp.get("putAskPrice", 0))
                    total_credit = call_credit + put_credit
                    if total_credit <= cfg.min_credit:
                        continue

                    credit_slip = total_credit * (1 - cfg.slippage)
                    call_width = wc["strike"] - atm_s
                    put_width = atm_s - wp["strike"]
                    max_wing = max(call_width, put_width)
                    risk_per = (max_wing - credit_slip) * 100
                    if risk_per <= 0 or risk_per > cfg.max_risk_per:
                        continue

                    # Net theta: sell ATM call + ATM put, buy wings
                    # ATM has both call and put theta at same strike
                    atm_theta = abs(atm.get("theta", 0))
                    # ATM straddle theta ≈ 2x single option (call + put at same strike)
                    # But ORATS theta is for the call side. Put theta ≈ similar magnitude.
                    net_theta = (2 * atm_theta -
                                 abs(wc.get("theta", 0)) -
                                 abs(wp.get("theta", 0)))

                    net_gamma = (2 * abs(atm.get("gamma", 0)) -
                                 abs(wc.get("gamma", 0)) -
                                 abs(wp.get("gamma", 0)))

                    theta_eff = net_theta / (risk_per / 100) if risk_per > 0 else 0
                    theta_gamma = (net_theta / net_gamma
                                   if net_gamma != 0 else 999)
                    credit_risk = credit_slip / max_wing if max_wing > 0 else 0

                    results.append({
                        "structure": "Iron Butterfly",
                        "atm_strike": atm_s,
                        "wing_call_strike": wc["strike"],
                        "wing_put_strike": wp["strike"],
                        "atm_delta": atm.get("delta", 0),
                        "call_width": call_width,
                        "put_width": put_width,
                        "credit": round(credit_slip, 4),
                        "risk_per": round(risk_per, 2),
                        "net_theta": round(net_theta, 4),
                        "net_gamma": round(net_gamma, 6),
                        "theta_efficiency": round(theta_eff, 4),
                        "theta_gamma": round(theta_gamma, 2),
                        "credit_risk": round(credit_risk, 4),
                    })

        return results

    # -- Short Iron Condor -----------------------------------------------------

    def _scan_short_iron_condor(self, strikes: List[Dict],
                                spot: float) -> List[Dict]:
        """Scan Short Iron Condor combos: sell OTM call+put, buy wings."""
        cfg = self.config

        # OTM calls for short leg
        short_calls = [s for s in strikes
                       if 0.10 <= s.get("delta", 0) <= 0.40
                       and s["strike"] > spot]
        # OTM puts for short leg
        short_puts = []
        for s in strikes:
            pd = s.get("delta", 0) - 1
            if 0.10 <= abs(pd) <= 0.40 and s["strike"] < spot:
                short_puts.append({**s, "put_delta": pd})

        # Wing calls (further OTM)
        wing_calls = [s for s in strikes
                      if cfg.min_delta <= s.get("delta", 0) <= 0.20
                      and s["strike"] > spot]
        # Wing puts
        wing_puts = []
        for s in strikes:
            pd = s.get("delta", 0) - 1
            if cfg.min_delta <= abs(pd) <= 0.20 and s["strike"] < spot:
                wing_puts.append({**s, "put_delta": pd})

        results = []
        for sc in short_calls:
            for sp in short_puts:
                for lc in wing_calls:
                    if lc["strike"] <= sc["strike"]:
                        continue
                    for lp in wing_puts:
                        if lp["strike"] >= sp["strike"]:
                            continue
                        # Validate ordering: lp < sp < sc < lc
                        if not (lp["strike"] < sp["strike"] <
                                sc["strike"] < lc["strike"]):
                            continue

                        call_credit = (sc.get("callBidPrice", 0) -
                                       lc.get("callAskPrice", 0))
                        put_credit = (sp.get("putBidPrice", 0) -
                                      lp.get("putAskPrice", 0))
                        total_credit = call_credit + put_credit
                        if total_credit <= cfg.min_credit:
                            continue

                        credit_slip = total_credit * (1 - cfg.slippage)
                        call_width = lc["strike"] - sc["strike"]
                        put_width = sp["strike"] - lp["strike"]
                        max_wing = max(call_width, put_width)
                        risk_per = (max_wing - credit_slip) * 100
                        if risk_per <= 0 or risk_per > cfg.max_risk_per:
                            continue

                        net_theta = (abs(sc.get("theta", 0)) +
                                     abs(sp.get("theta", 0)) -
                                     abs(lc.get("theta", 0)) -
                                     abs(lp.get("theta", 0)))

                        net_gamma = (abs(sc.get("gamma", 0)) +
                                     abs(sp.get("gamma", 0)) -
                                     abs(lc.get("gamma", 0)) -
                                     abs(lp.get("gamma", 0)))

                        theta_eff = (net_theta / (risk_per / 100)
                                     if risk_per > 0 else 0)
                        theta_gamma = (net_theta / net_gamma
                                       if net_gamma != 0 else 999)
                        credit_risk = (credit_slip / max_wing
                                       if max_wing > 0 else 0)

                        results.append({
                            "structure": "Short Iron Condor",
                            "short_call_strike": sc["strike"],
                            "short_put_strike": sp["strike"],
                            "long_call_strike": lc["strike"],
                            "long_put_strike": lp["strike"],
                            "short_call_delta": sc.get("delta", 0),
                            "short_put_delta": sp.get("put_delta", 0),
                            "call_width": call_width,
                            "put_width": put_width,
                            "credit": round(credit_slip, 4),
                            "risk_per": round(risk_per, 2),
                            "net_theta": round(net_theta, 4),
                            "net_gamma": round(net_gamma, 6),
                            "theta_efficiency": round(theta_eff, 4),
                            "theta_gamma": round(theta_gamma, 2),
                            "credit_risk": round(credit_risk, 4),
                        })

        return results

    # -- main scanner ----------------------------------------------------------

    def scan(self, chain: List[Dict], spot: float,
             structure: str = None,
             expiry: str = None) -> Dict[str, List[Dict]]:
        """Scan chain for optimal theta setups.

        Args:
            chain: Option chain with Greeks (ORATS or IB format).
            spot: Current spot price.
            structure: Specific structure or None for all credit structures.
            expiry: Filter to specific expiry or None for nearest.

        Returns:
            Dict mapping structure name -> sorted list of combos.
        """
        cfg = self.config
        strikes = self._filter_strikes(chain, expiry)
        if not strikes:
            return {}

        scanners = {
            "Bull Put Spread": self._scan_bull_put,
            "Bear Call Spread": self._scan_bear_call,
            "Iron Butterfly": self._scan_iron_butterfly,
            "Short Iron Condor": self._scan_short_iron_condor,
        }

        targets = ([structure] if structure and structure in scanners
                   else list(scanners.keys()))

        results = {}
        for name in targets:
            combos = scanners[name](strikes, spot)

            # Sort by objective
            if cfg.objective == "theta_gamma":
                combos.sort(key=lambda c: c["theta_gamma"], reverse=True)
            elif cfg.objective == "credit_risk":
                combos.sort(key=lambda c: c["credit_risk"], reverse=True)
            else:  # theta_efficiency
                combos.sort(key=lambda c: c["theta_efficiency"], reverse=True)

            results[name] = combos[:cfg.top_n]

        return results

    def scan_best(self, chain: List[Dict], spot: float,
                  structure: str = None,
                  expiry: str = None) -> Optional[Dict]:
        """Return the single best theta setup across all credit structures."""
        results = self.scan(chain, spot, structure, expiry)
        best = None
        best_score = -999
        obj = self.config.objective

        for name, combos in results.items():
            if combos:
                top = combos[0]
                score = top.get(obj, top.get("theta_efficiency", 0))
                if score > best_score:
                    best_score = score
                    best = top
        return best

    # -- display ---------------------------------------------------------------

    def print_results(self, results: Dict[str, List[Dict]],
                      show_all: bool = False) -> None:
        """Print theta scan results."""
        if not results:
            print(f"  {C.RED}No valid credit setups found{C.RESET}")
            return

        obj = self.config.objective
        obj_label = {"theta_efficiency": "Theta/Risk",
                     "theta_gamma": "Theta/Gamma",
                     "credit_risk": "Credit/Width"}.get(obj, obj)

        print(f"\n  {C.BOLD}{C.CYAN}Theta Maximizer — "
              f"Ranked by {obj_label}{C.RESET}\n")

        for name, combos in results.items():
            if not combos:
                continue

            print(f"  {C.BOLD}{name}{C.RESET} "
                  f"({len(combos)} combo{'s' if len(combos) != 1 else ''})")

            if name in ("Bull Put Spread", "Bear Call Spread"):
                print(f"    {'#':>2} {'Short':>8} {'Long':>8} {'Width':>6} "
                      f"{'Credit':>7} {'Risk':>7} {'Theta':>7} "
                      f"{'Th/Risk':>7} {'Th/Gam':>7} {'Cr/Wd':>6}")
                print(f"    {'-' * 72}")
                n = len(combos) if show_all else min(len(combos), self.config.top_n)
                for i, c in enumerate(combos[:n]):
                    t_clr = C.GREEN if c["theta_efficiency"] > 0 else C.RED
                    print(f"    {i+1:>2} {c['short_strike']:>8.0f} "
                          f"{c['long_strike']:>8.0f} "
                          f"{c['width']:>6.0f} "
                          f"${c['credit']:>6.2f} "
                          f"${c['risk_per']:>6.0f} "
                          f"{t_clr}{c['net_theta']:>7.3f}{C.RESET} "
                          f"{c['theta_efficiency']:>7.3f} "
                          f"{c['theta_gamma']:>7.1f} "
                          f"{c['credit_risk']:>6.2%}")

            elif name == "Iron Butterfly":
                print(f"    {'#':>2} {'ATM':>8} {'W.Call':>8} {'W.Put':>8} "
                      f"{'Credit':>7} {'Risk':>7} {'Theta':>7} "
                      f"{'Th/Risk':>7} {'Cr/Wd':>6}")
                print(f"    {'-' * 76}")
                n = len(combos) if show_all else min(len(combos), self.config.top_n)
                for i, c in enumerate(combos[:n]):
                    t_clr = C.GREEN if c["theta_efficiency"] > 0 else C.RED
                    print(f"    {i+1:>2} {c['atm_strike']:>8.0f} "
                          f"{c['wing_call_strike']:>8.0f} "
                          f"{c['wing_put_strike']:>8.0f} "
                          f"${c['credit']:>6.2f} "
                          f"${c['risk_per']:>6.0f} "
                          f"{t_clr}{c['net_theta']:>7.3f}{C.RESET} "
                          f"{c['theta_efficiency']:>7.3f} "
                          f"{c['credit_risk']:>6.2%}")

            elif name == "Short Iron Condor":
                print(f"    {'#':>2} {'S.Call':>8} {'S.Put':>8} "
                      f"{'L.Call':>8} {'L.Put':>8} "
                      f"{'Credit':>7} {'Risk':>7} {'Theta':>7} "
                      f"{'Th/Risk':>7}")
                print(f"    {'-' * 80}")
                n = len(combos) if show_all else min(len(combos), self.config.top_n)
                for i, c in enumerate(combos[:n]):
                    t_clr = C.GREEN if c["theta_efficiency"] > 0 else C.RED
                    print(f"    {i+1:>2} {c['short_call_strike']:>8.0f} "
                          f"{c['short_put_strike']:>8.0f} "
                          f"{c['long_call_strike']:>8.0f} "
                          f"{c['long_put_strike']:>8.0f} "
                          f"${c['credit']:>6.2f} "
                          f"${c['risk_per']:>6.0f} "
                          f"{t_clr}{c['net_theta']:>7.3f}{C.RESET} "
                          f"{c['theta_efficiency']:>7.3f}")

            print()

    def print_summary(self, results: Dict[str, List[Dict]]) -> None:
        """Print one-line summary of best setup per structure."""
        print(f"\n  {C.BOLD}Best Theta Setup Per Structure:{C.RESET}")
        for name, combos in results.items():
            if not combos:
                print(f"    {name:<24} {C.YELLOW}no valid combos{C.RESET}")
                continue
            best = combos[0]
            print(f"    {name:<24} "
                  f"credit=${best['credit']:.2f}  "
                  f"risk=${best['risk_per']:.0f}  "
                  f"theta={best['net_theta']:.3f}  "
                  f"th/risk={best['theta_efficiency']:.3f}  "
                  f"th/gamma={best['theta_gamma']:.1f}")
