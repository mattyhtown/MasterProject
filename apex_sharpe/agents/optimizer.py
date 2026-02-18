"""
OptimizerAgent — parameter optimization for trade structure backtests.

Loads cached signal days and option chains, then sweeps delta parameters
for each structure to find the combination that maximizes Sharpe ratio.

Usage:
    python -m apex_sharpe optimize [months]
    python -m apex_sharpe optimize 6 --structure "Call Debit Spread"

Design:
    - One-time data load from cache (no API calls during optimization)
    - Per-structure grid search (structures are independent)
    - Objective: maximize Sharpe ratio (configurable)
    - Walk-forward validation: optimize on first 2/3, validate on last 1/3
    - Reports optimal params, improvement vs default, and in-sample vs OOS
"""

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import product
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from .zero_dte import ZeroDTEAgent
from ..config import (TradeBacktestCfg, ZeroDTECfg, SignalSizingCfg,
                       CallRatioSpreadCfg, BrokenWingButterflyCfg)
from ..selection.signal_sizer import SignalSizer
from ..types import AgentResult, C


@dataclass(frozen=True)
class OptimizerCfg:
    """Optimizer configuration."""
    # Grid resolution
    delta_step: float = 0.05          # Step size for delta grid
    delta_min: float = 0.05           # Minimum delta to test
    delta_max: float = 0.60           # Maximum delta to test
    tol_range: Tuple[float, ...] = (0.06, 0.08, 0.10)  # Tolerance values
    slippage_range: Tuple[float, ...] = (0.02, 0.03, 0.04)
    # Objective
    objective: str = "sharpe"         # sharpe, total_pnl, win_rate
    min_trades: int = 5               # Minimum trades to score a combo
    # Walk-forward
    train_pct: float = 0.67           # 2/3 train, 1/3 test
    # Display
    top_n: int = 5                    # Show top N parameter combos


# Parameter definitions per structure: (param_name, default, min, max, step)
PARAM_SPACE = {
    "Call Debit Spread": [
        ("call_ds_long", 0.40, 0.25, 0.55, 0.05),
        ("call_ds_short", 0.25, 0.10, 0.40, 0.05),
    ],
    "Bull Put Spread": [
        ("bull_ps_short", 0.30, 0.15, 0.45, 0.05),
        ("bull_ps_long", 0.15, 0.05, 0.30, 0.05),
    ],
    "Long Call": [
        ("long_call_delta", 0.50, 0.30, 0.60, 0.05),
    ],
    "Call Ratio Spread": [
        ("crs_long_delta", 0.50, 0.35, 0.60, 0.05),
        ("crs_short_delta", 0.25, 0.10, 0.40, 0.05),
    ],
    "Broken Wing Butterfly": [
        ("bwb_lower_delta", 0.55, 0.40, 0.65, 0.05),
        ("bwb_middle_delta", 0.35, 0.20, 0.50, 0.05),
        ("bwb_upper_delta", 0.15, 0.05, 0.30, 0.05),
    ],
    "Put Debit Spread": [
        ("put_ds_long", 0.40, 0.25, 0.55, 0.05),
        ("put_ds_short", 0.25, 0.10, 0.40, 0.05),
    ],
    "Long Put": [
        ("long_put_delta", 0.50, 0.30, 0.60, 0.05),
    ],
    "Bear Call Spread": [
        ("bear_cs_short", 0.30, 0.15, 0.45, 0.05),
        ("bear_cs_long", 0.15, 0.05, 0.30, 0.05),
    ],
    "Iron Butterfly": [
        ("ifly_atm_delta", 0.50, 0.45, 0.55, 0.05),
        ("ifly_wing_delta", 0.15, 0.05, 0.25, 0.05),
    ],
    "Short Iron Condor": [
        ("ic_short_delta", 0.25, 0.15, 0.40, 0.05),
        ("ic_long_delta", 0.10, 0.05, 0.20, 0.05),
    ],
}


def _frange(start: float, stop: float, step: float) -> List[float]:
    """Generate float range, inclusive of endpoints."""
    vals = []
    v = start
    while v <= stop + 1e-9:
        vals.append(round(v, 4))
        v += step
    return vals


class OptimizerAgent(BaseAgent):
    """Parameter optimization for trade structure backtests.

    Reuses the same _build_trades math from TradeStructureBacktest but
    evaluates many parameter combinations on cached data.
    """

    def __init__(self, config: OptimizerCfg = None,
                 backtest_config: TradeBacktestCfg = None,
                 zero_dte_config: ZeroDTECfg = None,
                 sizing_config: SignalSizingCfg = None,
                 crs_config: CallRatioSpreadCfg = None,
                 bwb_config: BrokenWingButterflyCfg = None):
        config = config or OptimizerCfg()
        super().__init__("Optimizer", config)
        self.bt_config = backtest_config or TradeBacktestCfg()
        self.monitor = ZeroDTEAgent(zero_dte_config)
        self.sizer = SignalSizer(sizing_config)
        self.crs_config = crs_config or CallRatioSpreadCfg()
        self.bwb_config = bwb_config or BrokenWingButterflyCfg()

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Run optimization. Context: orats, state, months, structure (opt)."""
        months = context.get("months", 6)
        structure_filter = context.get("structure")  # None = all
        use_sizing = context.get("use_sizing", True)
        self.run_optimize(context["orats"], context["state"],
                          months=months, structure_filter=structure_filter,
                          use_sizing=use_sizing)
        return self._result(success=True)

    # -- data loading (reused from TradeStructureBacktest) ---------------------

    def _load_signal_days(self, orats, state, ticker: str,
                          start: date, end: date) -> Tuple[List[Dict], Dict]:
        """Load signal days and their chains from cache.

        Returns (signal_days_with_chains, cache).
        """
        from .trade_backtest import TradeStructureBacktest
        bt = TradeStructureBacktest(
            self.bt_config, self.monitor.config,
            self.sizer.config, crs_config=self.crs_config,
            bwb_config=self.bwb_config,
        )

        cache = state.load_cache()

        def _save():
            state.save_cache(cache)

        signal_days = bt._get_signal_days(ticker, start, end, cache, _save, orats)

        # Pre-load chains for all signal days
        enriched = []
        for sd in signal_days:
            chain = bt._fetch_hist_chain(orats, ticker, sd["date"], cache, _save)
            if not chain:
                continue
            exp = bt._find_next_expiry(chain, sd["date"])
            if not exp:
                continue
            strikes = [s for s in chain if s.get("expirDate") == exp]
            if len(strikes) < 10:
                continue
            enriched.append({**sd, "strikes": strikes, "expiry": exp})

        state.save_cache(cache)
        return enriched, cache

    # -- single-structure trade builder ---------------------------------------

    @staticmethod
    def _build_single_trade(structure_name: str, strikes: List[Dict],
                            spot: float, next_close: float,
                            params: Dict, risk_budget: float) -> Optional[Dict]:
        """Build a single structure's trade with given params.

        This is the hot loop — must be fast. No imports, no allocations.
        """
        tol = params.get("delta_tol", 0.08)
        slippage = params.get("slippage", 0.03)
        comm_per_leg = params.get("commission_per_leg", 0.65)

        def find_calls(target_delta):
            matches = []
            for row in strikes:
                d = row.get("delta")
                if d is not None and d > 0 and abs(d - target_delta) <= tol:
                    matches.append(row)
            matches.sort(key=lambda r: abs(r["delta"] - target_delta))
            return matches

        def find_puts(target_abs_delta):
            matches = []
            for row in strikes:
                cd = row.get("delta")
                if cd is None:
                    continue
                pd = cd - 1
                if abs(abs(pd) - target_abs_delta) <= tol:
                    r = dict(row)
                    r["put_delta"] = pd
                    matches.append(r)
            matches.sort(key=lambda r: abs(abs(r["put_delta"]) - target_abs_delta))
            return matches

        max_risk = risk_budget

        if structure_name == "Call Debit Spread":
            long_d = params["call_ds_long"]
            short_d = params["call_ds_short"]
            longs = find_calls(long_d)
            shorts = find_calls(short_d)
            if not longs or not shorts:
                return None
            lc, sc = longs[0], shorts[0]
            if lc["strike"] >= sc["strike"]:
                return None
            cost = lc.get("callAskPrice", 0) - sc.get("callBidPrice", 0)
            if cost <= 0:
                return None
            cost_slip = cost * (1 + slippage)
            comm = comm_per_leg * 2
            risk_per = cost_slip * 100 + comm
            qty = max(1, int(max_risk / risk_per))
            lv = max(0, next_close - lc["strike"])
            sv = max(0, next_close - sc["strike"])
            pnl_per = (lv - sv) - cost_slip
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Bull Put Spread":
            short_d = params["bull_ps_short"]
            long_d = params["bull_ps_long"]
            shorts = find_puts(short_d)
            longs = find_puts(long_d)
            if not shorts or not longs:
                return None
            sp, lp = shorts[0], longs[0]
            if sp["strike"] <= lp["strike"]:
                return None
            credit = sp.get("putBidPrice", 0) - lp.get("putAskPrice", 0)
            if credit <= 0:
                return None
            credit_slip = credit * (1 - slippage)
            width = sp["strike"] - lp["strike"]
            comm = comm_per_leg * 2
            risk_per = (width - credit_slip) * 100 + comm
            if risk_per <= 0:
                return None
            qty = max(1, int(max_risk / risk_per))
            sp_liab = max(0, sp["strike"] - next_close)
            lp_recov = max(0, lp["strike"] - next_close)
            pnl_per = credit_slip - (sp_liab - lp_recov)
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Long Call":
            delta = params["long_call_delta"]
            calls = find_calls(delta)
            if not calls:
                return None
            ac = calls[0]
            cost = ac.get("callAskPrice", 0)
            if cost <= 0:
                return None
            cost_slip = cost * (1 + slippage)
            comm = comm_per_leg
            risk_per = cost_slip * 100 + comm
            qty = max(1, int(max_risk / risk_per))
            val = max(0, next_close - ac["strike"])
            pnl_per = val - cost_slip
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Call Ratio Spread":
            long_d = params["crs_long_delta"]
            short_d = params["crs_short_delta"]
            longs = find_calls(long_d)
            shorts = find_calls(short_d)
            if not longs or not shorts:
                return None
            cl, cs = longs[0], shorts[0]
            if cl["strike"] >= cs["strike"]:
                return None
            cost = cl.get("callAskPrice", 0) - 2 * cs.get("callBidPrice", 0)
            cost_slip = cost * (1 + slippage) if cost > 0 else cost * (1 - slippage)
            comm = comm_per_leg * 3
            width = cs["strike"] - cl["strike"]
            risk_per = (abs(cost_slip) * 100 + comm) if cost_slip > 0 else (width * 100 + comm)
            qty = max(1, int(max_risk / risk_per))
            lv = max(0, next_close - cl["strike"])
            sv = max(0, next_close - cs["strike"])
            pnl_per = lv - 2 * sv - cost_slip
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Broken Wing Butterfly":
            lower_d = params["bwb_lower_delta"]
            mid_d = params["bwb_middle_delta"]
            upper_d = params["bwb_upper_delta"]
            lowers = find_calls(lower_d)
            mids = find_calls(mid_d)
            uppers = find_calls(upper_d)
            if not lowers or not mids or not uppers:
                return None
            bl, bm, bu = lowers[0], mids[0], uppers[0]
            if not (bl["strike"] < bm["strike"] < bu["strike"]):
                return None
            cost = (bl.get("callAskPrice", 0) -
                    2 * bm.get("callBidPrice", 0) +
                    bu.get("callAskPrice", 0))
            cost_slip = cost * (1 + slippage) if cost > 0 else cost * (1 - slippage)
            comm = comm_per_leg * 4
            lower_w = bm["strike"] - bl["strike"]
            upper_w = bu["strike"] - bm["strike"]
            risk_per = (abs(cost_slip) * 100 + comm) if cost_slip > 0 else (max(lower_w, upper_w) * 100 + comm)
            qty = max(1, int(max_risk / risk_per))
            blv = max(0, next_close - bl["strike"])
            bmv = max(0, next_close - bm["strike"])
            buv = max(0, next_close - bu["strike"])
            pnl_per = blv - 2 * bmv + buv - cost_slip
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Put Debit Spread":
            long_d = params["put_ds_long"]
            short_d = params["put_ds_short"]
            longs = find_puts(long_d)
            shorts = find_puts(short_d)
            if not longs or not shorts:
                return None
            lp, sp = longs[0], shorts[0]
            if lp["strike"] <= sp["strike"]:
                return None
            cost = lp.get("putAskPrice", 0) - sp.get("putBidPrice", 0)
            if cost <= 0:
                return None
            cost_slip = cost * (1 + slippage)
            comm = comm_per_leg * 2
            risk_per = cost_slip * 100 + comm
            qty = max(1, int(max_risk / risk_per))
            lv = max(0, lp["strike"] - next_close)
            sv = max(0, sp["strike"] - next_close)
            pnl_per = (lv - sv) - cost_slip
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Long Put":
            delta = params["long_put_delta"]
            puts = find_puts(delta)
            if not puts:
                return None
            ap = puts[0]
            cost = ap.get("putAskPrice", 0)
            if cost <= 0:
                return None
            cost_slip = cost * (1 + slippage)
            comm = comm_per_leg
            risk_per = cost_slip * 100 + comm
            qty = max(1, int(max_risk / risk_per))
            val = max(0, ap["strike"] - next_close)
            pnl_per = val - cost_slip
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Bear Call Spread":
            short_d = params["bear_cs_short"]
            long_d = params["bear_cs_long"]
            shorts = find_calls(short_d)
            longs = find_calls(long_d)
            if not shorts or not longs:
                return None
            sc, lc = shorts[0], longs[0]
            if sc["strike"] >= lc["strike"]:
                return None
            credit = sc.get("callBidPrice", 0) - lc.get("callAskPrice", 0)
            if credit <= 0:
                return None
            credit_slip = credit * (1 - slippage)
            width = lc["strike"] - sc["strike"]
            comm = comm_per_leg * 2
            risk_per = (width - credit_slip) * 100 + comm
            if risk_per <= 0:
                return None
            qty = max(1, int(max_risk / risk_per))
            sc_liab = max(0, next_close - sc["strike"])
            lc_recov = max(0, next_close - lc["strike"])
            pnl_per = credit_slip - (sc_liab - lc_recov)
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Iron Butterfly":
            atm_d = params["ifly_atm_delta"]
            wing_d = params["ifly_wing_delta"]
            atm_calls = find_calls(atm_d)
            wing_calls = find_calls(wing_d)
            wing_puts = find_puts(wing_d)
            if not atm_calls or not wing_calls or not wing_puts:
                return None
            atm = atm_calls[0]
            wc, wp = wing_calls[0], wing_puts[0]
            atm_s = atm["strike"]
            if not (wc["strike"] > atm_s and wp["strike"] < atm_s):
                return None
            call_credit = atm.get("callBidPrice", 0) - wc.get("callAskPrice", 0)
            put_credit = atm.get("putBidPrice", 0) - wp.get("putAskPrice", 0)
            total_credit = call_credit + put_credit
            if total_credit <= 0:
                return None
            credit_slip = total_credit * (1 - slippage)
            max_wing = max(wc["strike"] - atm_s, atm_s - wp["strike"])
            comm = comm_per_leg * 4
            risk_per = (max_wing - credit_slip) * 100 + comm
            if risk_per <= 0:
                return None
            qty = max(1, int(max_risk / risk_per))
            sc_liab = max(0, next_close - atm_s)
            lc_recov = max(0, next_close - wc["strike"])
            sp_liab = max(0, atm_s - next_close)
            lp_recov = max(0, wp["strike"] - next_close)
            net_liab = (sc_liab - lc_recov) + (sp_liab - lp_recov)
            pnl_per = credit_slip - net_liab
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        elif structure_name == "Short Iron Condor":
            short_d = params["ic_short_delta"]
            long_d = params["ic_long_delta"]
            sc_list = find_calls(short_d)
            sp_list = find_puts(short_d)
            lc_list = find_calls(long_d)
            lp_list = find_puts(long_d)
            if not sc_list or not sp_list or not lc_list or not lp_list:
                return None
            sc, sp, lc, lp = sc_list[0], sp_list[0], lc_list[0], lp_list[0]
            if not (lp["strike"] < sp["strike"] < sc["strike"] < lc["strike"]):
                return None
            call_credit = sc.get("callBidPrice", 0) - lc.get("callAskPrice", 0)
            put_credit = sp.get("putBidPrice", 0) - lp.get("putAskPrice", 0)
            total_credit = call_credit + put_credit
            if total_credit <= 0:
                return None
            credit_slip = total_credit * (1 - slippage)
            max_wing = max(lc["strike"] - sc["strike"],
                           sp["strike"] - lp["strike"])
            comm = comm_per_leg * 4
            risk_per = (max_wing - credit_slip) * 100 + comm
            if risk_per <= 0:
                return None
            qty = max(1, int(max_risk / risk_per))
            sc_liab = max(0, next_close - sc["strike"])
            lc_recov = max(0, next_close - lc["strike"])
            sp_liab = max(0, sp["strike"] - next_close)
            lp_recov = max(0, lp["strike"] - next_close)
            net_liab = (sc_liab - lc_recov) + (sp_liab - lp_recov)
            pnl_per = credit_slip - net_liab
            return {"name": structure_name,
                    "pnl": round(pnl_per * 100 * qty - comm * qty, 2),
                    "qty": qty, "max_risk": round(risk_per * qty, 2)}

        return None

    # -- scoring ---------------------------------------------------------------

    @staticmethod
    def _score(pnls: List[float], objective: str = "sharpe") -> float:
        """Score a parameter combination."""
        if not pnls:
            return -999.0
        n = len(pnls)
        total = sum(pnls)
        avg = total / n
        if objective == "total_pnl":
            return total
        elif objective == "win_rate":
            return sum(1 for p in pnls if p > 0) / n
        else:  # sharpe
            if n < 2:
                return avg if avg > 0 else -999.0
            var = sum((p - avg) ** 2 for p in pnls) / (n - 1)
            std = var ** 0.5
            return avg / std if std > 0 else (-999.0 if avg <= 0 else 999.0)

    # -- grid generation -------------------------------------------------------

    @staticmethod
    def _generate_grid(structure_name: str) -> List[Dict]:
        """Generate all valid parameter combinations for a structure."""
        param_defs = PARAM_SPACE.get(structure_name, [])
        if not param_defs:
            return []

        # Build range for each param
        ranges = []
        names = []
        for pname, default, pmin, pmax, step in param_defs:
            names.append(pname)
            ranges.append(_frange(pmin, pmax, step))

        # Cartesian product
        combos = []
        for vals in product(*ranges):
            params = dict(zip(names, vals))

            # Validity constraints
            if structure_name == "Call Debit Spread":
                if params["call_ds_long"] <= params["call_ds_short"]:
                    continue  # long delta must be higher (closer to ATM)
            elif structure_name == "Bull Put Spread":
                if params["bull_ps_short"] <= params["bull_ps_long"]:
                    continue
            elif structure_name == "Call Ratio Spread":
                if params["crs_long_delta"] <= params["crs_short_delta"]:
                    continue
            elif structure_name == "Broken Wing Butterfly":
                if not (params["bwb_lower_delta"] > params["bwb_middle_delta"]
                        > params["bwb_upper_delta"]):
                    continue
            elif structure_name == "Put Debit Spread":
                if params["put_ds_long"] <= params["put_ds_short"]:
                    continue
            elif structure_name == "Bear Call Spread":
                if params["bear_cs_short"] <= params["bear_cs_long"]:
                    continue
            elif structure_name == "Iron Butterfly":
                if params["ifly_atm_delta"] <= params["ifly_wing_delta"]:
                    continue
            elif structure_name == "Short Iron Condor":
                if params["ic_short_delta"] <= params["ic_long_delta"]:
                    continue

            combos.append(params)

        return combos

    # -- main optimizer loop ---------------------------------------------------

    def _optimize_structure(self, structure_name: str,
                            signal_days: List[Dict],
                            use_sizing: bool = True) -> Dict:
        """Optimize one structure across all signal days.

        Returns dict with best_params, best_score, default_score, all_results.
        """
        cfg = self.config
        grid = self._generate_grid(structure_name)
        if not grid:
            return {"structure": structure_name, "error": "no grid"}

        # Split train/test
        n = len(signal_days)
        split = int(n * cfg.train_pct)
        train_days = signal_days[:split]
        test_days = signal_days[split:]

        # Get default params
        param_defs = PARAM_SPACE.get(structure_name, [])
        default_params = {pname: default for pname, default, *_ in param_defs}

        # Evaluate all combos on TRAIN set
        results = []
        for params in grid:
            full_params = {
                **params,
                "delta_tol": self.bt_config.delta_tol,
                "slippage": self.bt_config.slippage,
                "commission_per_leg": self.bt_config.commission_per_leg,
            }
            pnls = []
            for sd in train_days:
                if use_sizing:
                    sizing = self.sizer.compute(sd.get("core_count", 3))
                    budget = sizing["risk_budget"]
                else:
                    budget = self.bt_config.max_risk

                trade = self._build_single_trade(
                    structure_name, sd["strikes"], sd["close"],
                    sd["next_close"], full_params, budget)
                if trade:
                    pnls.append(trade["pnl"])

            if len(pnls) < cfg.min_trades:
                continue

            score = self._score(pnls, cfg.objective)
            total = sum(pnls)
            wins = sum(1 for p in pnls if p > 0)
            results.append({
                "params": params,
                "score": score,
                "total_pnl": total,
                "avg_pnl": total / len(pnls),
                "win_rate": wins / len(pnls),
                "n_trades": len(pnls),
            })

        if not results:
            return {"structure": structure_name, "error": "no valid combos"}

        results.sort(key=lambda r: r["score"], reverse=True)
        best = results[0]

        # Evaluate DEFAULT on train
        default_full = {
            **default_params,
            "delta_tol": self.bt_config.delta_tol,
            "slippage": self.bt_config.slippage,
            "commission_per_leg": self.bt_config.commission_per_leg,
        }
        default_train_pnls = []
        for sd in train_days:
            if use_sizing:
                sizing = self.sizer.compute(sd.get("core_count", 3))
                budget = sizing["risk_budget"]
            else:
                budget = self.bt_config.max_risk
            trade = self._build_single_trade(
                structure_name, sd["strikes"], sd["close"],
                sd["next_close"], default_full, budget)
            if trade:
                default_train_pnls.append(trade["pnl"])

        default_train_score = self._score(default_train_pnls, cfg.objective)

        # Evaluate BEST and DEFAULT on TEST set (out-of-sample)
        best_full = {
            **best["params"],
            "delta_tol": self.bt_config.delta_tol,
            "slippage": self.bt_config.slippage,
            "commission_per_leg": self.bt_config.commission_per_leg,
        }
        best_test_pnls = []
        default_test_pnls = []
        for sd in test_days:
            if use_sizing:
                sizing = self.sizer.compute(sd.get("core_count", 3))
                budget = sizing["risk_budget"]
            else:
                budget = self.bt_config.max_risk

            trade = self._build_single_trade(
                structure_name, sd["strikes"], sd["close"],
                sd["next_close"], best_full, budget)
            if trade:
                best_test_pnls.append(trade["pnl"])

            trade = self._build_single_trade(
                structure_name, sd["strikes"], sd["close"],
                sd["next_close"], default_full, budget)
            if trade:
                default_test_pnls.append(trade["pnl"])

        best_test_score = self._score(best_test_pnls, cfg.objective)
        default_test_score = self._score(default_test_pnls, cfg.objective)

        return {
            "structure": structure_name,
            "grid_size": len(grid),
            "valid_combos": len(results),
            "default_params": default_params,
            "best_params": best["params"],
            # Train (in-sample)
            "train_n": len(train_days),
            "default_train_score": default_train_score,
            "default_train_pnl": sum(default_train_pnls),
            "best_train_score": best["score"],
            "best_train_pnl": best["total_pnl"],
            "best_train_win": best["win_rate"],
            "best_train_n": best["n_trades"],
            # Test (out-of-sample)
            "test_n": len(test_days),
            "default_test_score": default_test_score,
            "default_test_pnl": sum(default_test_pnls),
            "best_test_score": best_test_score,
            "best_test_pnl": sum(best_test_pnls),
            "best_test_win": (sum(1 for p in best_test_pnls if p > 0)
                              / len(best_test_pnls)) if best_test_pnls else 0,
            "best_test_n": len(best_test_pnls),
            # Top N for display
            "top_combos": results[:self.config.top_n],
        }

    # -- main entry ------------------------------------------------------------

    def run_optimize(self, orats, state, months: int = 6,
                     structure_filter: str = None,
                     use_sizing: bool = True) -> None:
        """Run parameter optimization across structures."""
        cfg = self.config
        end = date.today()
        start = end - timedelta(days=months * 30)

        print(f"{C.BOLD}{C.CYAN}Parameter Optimizer{C.RESET}")
        print(f"  {start} -> {end}")
        print(f"  Objective: {cfg.objective}")
        print(f"  Walk-forward split: {cfg.train_pct:.0%} train / "
              f"{1 - cfg.train_pct:.0%} test")
        if structure_filter:
            print(f"  Structure: {structure_filter}")
        print()

        structures = ([structure_filter] if structure_filter
                      else list(PARAM_SPACE.keys()))

        for ticker in self.bt_config.tickers:
            print(f"{'=' * 80}")
            print(f"  {C.BOLD}Optimizer — {ticker}{C.RESET}")
            print(f"{'=' * 80}")

            # Load data once
            t0 = time.time()
            signal_days, _ = self._load_signal_days(orats, state, ticker,
                                                     start, end)
            load_time = time.time() - t0
            print(f"  Loaded {len(signal_days)} signal days with chains "
                  f"({load_time:.1f}s)")

            if len(signal_days) < 5:
                print(f"  {C.RED}Too few signal days for optimization{C.RESET}")
                continue

            split = int(len(signal_days) * cfg.train_pct)
            print(f"  Train: {split} days | Test: {len(signal_days) - split} days\n")

            all_results = []
            for sname in structures:
                t0 = time.time()
                result = self._optimize_structure(sname, signal_days,
                                                   use_sizing=use_sizing)
                elapsed = time.time() - t0

                if "error" in result:
                    print(f"  {sname:<24} {C.YELLOW}{result['error']}{C.RESET}")
                    continue

                all_results.append(result)
                self._print_structure_result(result, elapsed)

            # Summary table
            if all_results:
                self._print_summary(all_results)

    # -- display ---------------------------------------------------------------

    def _print_structure_result(self, r: Dict, elapsed: float) -> None:
        """Print optimization result for one structure."""
        name = r["structure"]
        cfg = self.config

        print(f"\n  {C.BOLD}{C.CYAN}{name}{C.RESET} "
              f"({r['grid_size']} combos, {r['valid_combos']} valid, "
              f"{elapsed:.1f}s)")

        # Default vs optimized params
        print(f"    {'Param':<20} {'Default':>8} {'Optimal':>8} {'Change':>8}")
        print(f"    {'-' * 48}")
        for pname in r["default_params"]:
            dv = r["default_params"][pname]
            ov = r["best_params"][pname]
            change = ov - dv
            chg_clr = C.GREEN if change != 0 else C.DIM
            print(f"    {pname:<20} {dv:>8.2f} {ov:>8.2f} "
                  f"{chg_clr}{change:>+8.2f}{C.RESET}")

        # Train performance
        d_clr = C.GREEN if r["default_train_pnl"] > 0 else C.RED
        b_clr = C.GREEN if r["best_train_pnl"] > 0 else C.RED
        print(f"\n    {'TRAIN':>8} {'Sharpe':>8} {'Total P&L':>10} {'Win%':>6} {'N':>4}")
        print(f"    {'Default':>8} {r['default_train_score']:>+8.2f} "
              f"{d_clr}${r['default_train_pnl']:>+9.0f}{C.RESET}")
        print(f"    {'Optimal':>8} {r['best_train_score']:>+8.2f} "
              f"{b_clr}${r['best_train_pnl']:>+9.0f}{C.RESET} "
              f"{r['best_train_win']:>5.0%} {r['best_train_n']:>4}")

        # Test (OOS) performance
        if r["test_n"] > 0:
            dt_clr = C.GREEN if r["default_test_pnl"] > 0 else C.RED
            bt_clr = C.GREEN if r["best_test_pnl"] > 0 else C.RED
            print(f"\n    {'TEST':>8} {'Sharpe':>8} {'Total P&L':>10} {'Win%':>6} {'N':>4}")
            print(f"    {'Default':>8} {r['default_test_score']:>+8.2f} "
                  f"{dt_clr}${r['default_test_pnl']:>+9.0f}{C.RESET}")
            print(f"    {'Optimal':>8} {r['best_test_score']:>+8.2f} "
                  f"{bt_clr}${r['best_test_pnl']:>+9.0f}{C.RESET} "
                  f"{r['best_test_win']:>5.0%} {r['best_test_n']:>4}")

            # Improvement
            if r["default_test_score"] != 0:
                improvement = ((r["best_test_score"] - r["default_test_score"])
                               / abs(r["default_test_score"]) * 100)
                imp_clr = C.GREEN if improvement > 0 else C.RED
                print(f"\n    {C.BOLD}OOS Sharpe improvement: "
                      f"{imp_clr}{improvement:+.0f}%{C.RESET}")
            if r["best_test_score"] < r["best_train_score"] * 0.5:
                print(f"    {C.YELLOW}WARNING: Possible overfit — "
                      f"OOS score << train score{C.RESET}")

        # Top combos
        if len(r.get("top_combos", [])) > 1:
            print(f"\n    Top {cfg.top_n} combinations (train):")
            for i, combo in enumerate(r["top_combos"][:cfg.top_n]):
                param_str = ", ".join(f"{k}={v:.2f}" for k, v in combo["params"].items())
                s_clr = C.GREEN if combo["total_pnl"] > 0 else C.RED
                print(f"      {i+1}. {param_str}")
                print(f"         Sharpe={combo['score']:+.2f}  "
                      f"{s_clr}${combo['total_pnl']:+,.0f}{C.RESET}  "
                      f"Win={combo['win_rate']:.0%}  N={combo['n_trades']}")

    def _print_summary(self, results: List[Dict]) -> None:
        """Print summary table comparing all structures."""
        print(f"\n  {C.BOLD}{C.CYAN}{'=' * 80}{C.RESET}")
        print(f"  {C.BOLD}OPTIMIZATION SUMMARY{C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}{'=' * 80}{C.RESET}")

        header = (f"  {'STRUCTURE':<24} "
                  f"{'DEF.SHARPE':>10} {'OPT.SHARPE':>10} "
                  f"{'DEF.$':>9} {'OPT.$':>9} "
                  f"{'OOS.SHARPE':>10} {'OOS.$':>9}")
        print(f"\n{header}")
        print(f"  {'-' * 80}")

        for r in results:
            d_s = r["default_train_score"]
            b_s = r["best_train_score"]
            d_p = r["default_train_pnl"]
            b_p = r["best_train_pnl"]
            oos_s = r.get("best_test_score", 0)
            oos_p = r.get("best_test_pnl", 0)

            ds_clr = C.GREEN if d_s > 0 else C.RED
            bs_clr = C.GREEN if b_s > 0 else C.RED
            dp_clr = C.GREEN if d_p > 0 else C.RED
            bp_clr = C.GREEN if b_p > 0 else C.RED
            os_clr = C.GREEN if oos_s > 0 else C.RED
            op_clr = C.GREEN if oos_p > 0 else C.RED

            print(f"  {r['structure']:<24} "
                  f"{ds_clr}{d_s:>+10.2f}{C.RESET} "
                  f"{bs_clr}{b_s:>+10.2f}{C.RESET} "
                  f"{dp_clr}${d_p:>+8.0f}{C.RESET} "
                  f"{bp_clr}${b_p:>+8.0f}{C.RESET} "
                  f"{os_clr}{oos_s:>+10.2f}{C.RESET} "
                  f"{op_clr}${oos_p:>+8.0f}{C.RESET}")

        # Config snippet for best params
        print(f"\n  {C.BOLD}Optimal config values:{C.RESET}")
        print(f"  (Copy to TradeBacktestCfg / CallRatioSpreadCfg / BrokenWingButterflyCfg)")
        for r in results:
            if r.get("best_test_score", 0) > r.get("default_test_score", 0):
                print(f"\n    # {r['structure']} (OOS Sharpe: "
                      f"{r['best_test_score']:+.2f} vs default {r['default_test_score']:+.2f})")
                for k, v in r["best_params"].items():
                    print(f"    {k} = {v:.2f}")
