"""
ZeroDTEMonitor — 0DTE directional signal detection from vol surface.

Extracted from trading_pipeline.py. 10 signals, 5-core composite system.
Uses ORATSClient and StateManager via DI instead of module-level functions.
"""

import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from ..config import ZeroDTECfg
from ..data.yfinance_client import yf_price, yf_credit
from ..types import AgentResult, C


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence of a weekday in a month (1-indexed)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of a weekday in a month."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _easter(year: int) -> date:
    """Compute Easter Sunday (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


class ZeroDTEAgent(BaseAgent):
    """0DTE directional signal monitor using ORATS vol surface data.

    Detects regime shifts (skew steepening, contango collapse, IV/RV divergence)
    that historically precede directional SPX moves by 60-90 minutes.
    """

    # Display order for dashboard — original 10 + 10 discovered signals
    SIGNAL_ORDER = [
        # Core fear (Tier 1)
        "skewing", "rip", "skew_25d_rr", "contango", "credit_spread",
        # Wing skew (Tier 1+)
        "wing_skew_30d", "wing_skew_10d",
        # Funding stress (Tier 1+)
        "borrow_term", "borrow_spread",
        # Vol momentum (Tier 1+)
        "iv_momentum", "skewing_change", "contango_change",
        # Model/liquidity (Tier 2+)
        "model_confidence", "mw_adj_30", "iv10_iv30_ratio",
        # Original Tier 2-3
        "iv_rv_spread", "fbfwd30_20", "rSlp30", "fwd_kink", "rDrv30",
    ]

    def __init__(self, config: ZeroDTECfg = None):
        config = config or ZeroDTECfg()
        super().__init__("ZeroDTE", config)
        self.baseline: Dict[str, Dict] = {}
        self.prev_day: Dict[str, Dict] = {}
        self.log_entries: List[Dict] = []

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _safe(d: Dict, key: str, default: float = 0.0) -> float:
        v = d.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    # -- calendar overlays ------------------------------------------------
    # OpEx amplifier, VIXpiration discount, FOMC blackout

    # FOMC meeting dates (decision day = last day of 2-day meeting)
    FOMC_DATES = [
        # 2025
        date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),
        date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17),
        date(2025, 10, 29), date(2025, 12, 10),
        # 2026
        date(2026, 1, 28), date(2026, 3, 18), date(2026, 5, 6),
        date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
        date(2026, 10, 28), date(2026, 12, 9),
    ]

    @staticmethod
    def _monthly_opex(d: date) -> date:
        """3rd Friday of the month."""
        return _nth_weekday(d.year, d.month, 4, 3)  # weekday=4 is Friday

    @staticmethod
    def _vix_expiration(d: date) -> date:
        """VIX expiration = 3rd Wednesday of the month."""
        return _nth_weekday(d.year, d.month, 2, 3)  # weekday=2 is Wednesday

    def calendar_overlay(self, trade_date: date = None) -> Dict[str, Any]:
        """Compute calendar-based signal modifiers.

        Returns dict with:
            opex_amplifier: bool — within 3 days of monthly OpEx
            vixpiration_discount: bool — within 1 day of VIX expiration
            fomc_blackout: bool — within 1 day of FOMC meeting
            calendar_modifier: float — multiplier for signal confidence
                1.5 = OpEx week (amplify)
                0.7 = VIXpiration (discount)
                0.0 = FOMC blackout (suppress)
                1.0 = normal
        """
        d = trade_date or date.today()

        # Monthly OpEx: 3rd Friday, check within 3 calendar days
        opex = self._monthly_opex(d)
        opex_dist = abs((d - opex).days)
        near_opex = opex_dist <= 3

        # VIX expiration: 3rd Wednesday, check within 1 calendar day
        vix_exp = self._vix_expiration(d)
        vix_dist = abs((d - vix_exp).days)
        near_vix = vix_dist <= 1

        # FOMC: check both days of meeting (decision day and day before)
        near_fomc = False
        for fomc in self.FOMC_DATES:
            if abs((d - fomc).days) <= 1:
                near_fomc = True
                break

        # Priority: FOMC blackout > VIXpiration discount > OpEx amplify
        if near_fomc:
            modifier = 0.0
            label = "FOMC_BLACKOUT"
        elif near_vix and not near_opex:
            modifier = 0.7
            label = "VIXPIRATION_DISCOUNT"
        elif near_opex:
            modifier = 1.5
            label = "OPEX_AMPLIFIER"
        else:
            modifier = 1.0
            label = "NORMAL"

        return {
            "opex_amplifier": near_opex,
            "vixpiration_discount": near_vix,
            "fomc_blackout": near_fomc,
            "calendar_modifier": modifier,
            "calendar_label": label,
            "opex_date": opex.isoformat(),
            "vix_exp_date": vix_exp.isoformat(),
        }

    # -- signal computation -----------------------------------------------

    def compute_signals(self, ticker: str, summary: Dict) -> Dict[str, Dict]:
        """Compute all 10 signals from ORATS summary row."""
        cfg = self.config
        base = self.baseline.get(ticker, {})
        prev = self.prev_day.get(ticker, {})
        sf = self._safe
        signals: Dict[str, Dict] = {}

        # 1. IV vs RV spread (Tier 1)
        iv30 = sf(summary, "iv30d")
        rv30 = sf(summary, "rVol30")
        spread = iv30 - rv30
        signals["iv_rv_spread"] = {
            "value": round(spread, 4),
            "level": "ACTION" if spread < cfg.iv_rv_thresh else "OK",
            "tier": 1, "label": "IV vs RV Spread",
        }

        # 2. Skew change (Tier 1)
        skew = sf(summary, "dlt25Iv30d") - sf(summary, "dlt75Iv30d")
        base_skew = sf(base, "dlt25Iv30d", sf(summary, "dlt25Iv30d")) \
                   - sf(base, "dlt75Iv30d", sf(summary, "dlt75Iv30d"))
        skew_chg = skew - base_skew
        signals["skew_25d_rr"] = {
            "value": round(skew, 4),
            "baseline": round(base_skew, 4),
            "change": round(skew_chg, 4),
            "level": "ACTION" if abs(skew_chg) > cfg.skew_change_thresh else "OK",
            "tier": 1, "label": "Skew (25d RR)",
        }

        # 3. Contango collapse (Tier 1)
        ct = sf(summary, "contango")
        ct_base = sf(base, "contango", ct)
        ct_pct = (ct - ct_base) / abs(ct_base) if abs(ct_base) > 0.001 else 0.0
        signals["contango"] = {
            "value": round(ct, 4),
            "baseline": round(ct_base, 4),
            "pct_change": round(ct_pct, 4),
            "level": "ACTION" if (ct_pct < -cfg.contango_drop_thresh or ct < 0) else "OK",
            "tier": 1, "label": "Contango",
        }

        # 4. Forward/backward ratio (Tier 2)
        fb = sf(summary, "fbfwd30_20", 1.0)
        signals["fbfwd30_20"] = {
            "value": round(fb, 4),
            "level": "WARNING" if (fb > cfg.fbfwd_high or fb < cfg.fbfwd_low) else "OK",
            "tier": 2, "label": "ORATS Forecast (fbfwd)",
        }

        # 5. Realized skew slope change (Tier 2)
        rslp = sf(summary, "rSlp30")
        prev_rslp = sf(prev, "rSlp30", rslp)
        slope_chg = rslp - prev_rslp
        signals["rSlp30"] = {
            "value": round(rslp, 4),
            "prev_day": round(prev_rslp, 4),
            "change": round(slope_chg, 4),
            "level": "WARNING" if abs(slope_chg) > cfg.slope_change_thresh else "OK",
            "tier": 2, "label": "Skew Slope (rSlp30)",
        }

        # 6. Forward vol kink (Tier 3)
        kink = abs(sf(summary, "fwd30_20") - sf(summary, "fwd60_30"))
        signals["fwd_kink"] = {
            "value": round(kink, 4),
            "level": "INFO" if kink > cfg.fwd_kink_thresh else "OK",
            "tier": 3, "label": "Fwd Vol Kink",
        }

        # 7. RV derivative climbing while IV flat (Tier 3)
        rdrv = sf(summary, "rDrv30")
        prev_rdrv = sf(prev, "rDrv30", rdrv)
        base_iv = sf(base, "iv30d", iv30)
        iv_flat = abs(iv30 - base_iv) < 0.005
        signals["rDrv30"] = {
            "value": round(rdrv, 4),
            "prev_day": round(prev_rdrv, 4),
            "level": "INFO" if (rdrv > prev_rdrv + 0.01 and iv_flat) else "OK",
            "tier": 3, "label": "RV Derivative",
        }

        # 8. Skewing (Tier 1)
        skewing = sf(summary, "skewing")
        signals["skewing"] = {
            "value": round(skewing, 4),
            "level": "ACTION" if skewing > cfg.skewing_thresh else "OK",
            "tier": 1, "label": "Skewing",
        }

        # 9. RIP (Tier 1)
        rip_val = sf(summary, "rip")
        signals["rip"] = {
            "value": round(rip_val, 2),
            "level": "ACTION" if rip_val > cfg.rip_thresh else "OK",
            "tier": 1, "label": "Risk Implied Premium",
        }

        # === DISCOVERED SIGNALS (from SignalDiscoveryAgent) ==================

        # 10. Wing skew 30d — dlt95-dlt5 spread (crash skew)
        dlt95_30 = sf(summary, "dlt95Iv30d")
        dlt5_30 = sf(summary, "dlt5Iv30d")
        wing30 = dlt95_30 - dlt5_30 if dlt95_30 and dlt5_30 else 0.0
        signals["wing_skew_30d"] = {
            "value": round(wing30, 4),
            "level": "ACTION" if wing30 > cfg.wing_skew_30d_thresh else "OK",
            "tier": 1, "label": "Wing Skew 30d",
        }

        # 11. Wing skew 10d — near-term crash demand
        dlt95_10 = sf(summary, "dlt95Iv10d")
        dlt5_10 = sf(summary, "dlt5Iv10d")
        wing10 = dlt95_10 - dlt5_10 if dlt95_10 and dlt5_10 else 0.0
        signals["wing_skew_10d"] = {
            "value": round(wing10, 4),
            "level": "ACTION" if wing10 > cfg.wing_skew_10d_thresh else "OK",
            "tier": 1, "label": "Wing Skew 10d",
        }

        # 12. Borrow term spread — short vs long funding stress
        borrow30 = sf(summary, "borrow30")
        borrow2y = sf(summary, "borrow2y")
        borrow_term = borrow30 - borrow2y if borrow30 and borrow2y else 0.0
        signals["borrow_term"] = {
            "value": round(borrow_term, 6),
            "level": "ACTION" if borrow_term > cfg.borrow_term_thresh else "OK",
            "tier": 1, "label": "Borrow Term Spread",
        }

        # 13. Borrow spread — funding above risk-free
        rf30 = sf(summary, "riskFree30")
        borrow_rf = borrow30 - rf30 if borrow30 else 0.0
        signals["borrow_spread"] = {
            "value": round(borrow_rf, 6),
            "level": "ACTION" if borrow_rf > cfg.borrow_spread_thresh else "OK",
            "tier": 1, "label": "Borrow-RF Spread",
        }

        # 14. IV momentum — 1-day iv30d acceleration
        prev_iv30 = sf(prev, "iv30d")
        iv_mom = iv30 - prev_iv30 if prev_iv30 > 0 else 0.0
        signals["iv_momentum"] = {
            "value": round(iv_mom, 4),
            "prev_day": round(prev_iv30, 4),
            "level": "ACTION" if iv_mom > cfg.iv_momentum_thresh else "OK",
            "tier": 1, "label": "IV Momentum",
        }

        # 15. Skewing change — 1-day put demand acceleration
        prev_skewing = sf(prev, "skewing")
        skew_chg_1d = skewing - prev_skewing
        signals["skewing_change"] = {
            "value": round(skew_chg_1d, 4),
            "prev_day": round(prev_skewing, 4),
            "level": "ACTION" if skew_chg_1d > cfg.skewing_change_thresh else "OK",
            "tier": 1, "label": "Skewing Δ1d",
        }

        # 16. Contango change — 1-day term structure collapse
        prev_ct = sf(prev, "contango")
        ct_chg_1d = ct - prev_ct if prev_ct else 0.0
        signals["contango_change"] = {
            "value": round(ct_chg_1d, 4),
            "prev_day": round(prev_ct, 4),
            "level": "ACTION" if ct_chg_1d < cfg.contango_change_thresh else "OK",
            "tier": 1, "label": "Contango Δ1d",
        }

        # 17. Model confidence — low = vol surface dislocation
        confidence = sf(summary, "confidence", 1.0)
        signals["model_confidence"] = {
            "value": round(confidence, 6),
            "level": "WARNING" if confidence < cfg.model_confidence_thresh else "OK",
            "tier": 2, "label": "Model Confidence",
        }

        # 18. Market-width adjustment — liquidity proxy
        mw = sf(summary, "mwAdj30")
        signals["mw_adj_30"] = {
            "value": round(mw, 6),
            "level": "WARNING" if mw > cfg.mw_adj_thresh else "OK",
            "tier": 2, "label": "Mkt Width (Liquidity)",
        }

        # 19. IV10/IV30 ratio — short-term fear acceleration
        iv10 = sf(summary, "iv10d")
        iv_ratio = iv10 / iv30 if iv30 > 0.01 else 1.0
        signals["iv10_iv30_ratio"] = {
            "value": round(iv_ratio, 4),
            "level": "WARNING" if iv_ratio > cfg.iv10_iv30_thresh else "OK",
            "tier": 2, "label": "IV10/IV30 Ratio",
        }

        return signals

    def compute_credit_signal(
        self, hyg: float, tlt: float, hyg_prev: float, tlt_prev: float,
    ) -> Dict[str, Dict]:
        """Credit spread signal from HYG/TLT daily changes."""
        cfg = self.config
        if not all([hyg, tlt, hyg_prev, tlt_prev]):
            return {}
        if hyg_prev == 0 or tlt_prev == 0:
            return {}
        hyg_chg = (hyg - hyg_prev) / hyg_prev
        tlt_chg = (tlt - tlt_prev) / tlt_prev
        credit = hyg_chg - tlt_chg
        return {
            "credit_spread": {
                "value": round(credit, 4),
                "level": "ACTION" if credit < cfg.credit_thresh else "OK",
                "tier": 1, "label": "Credit Spread (HYG-TLT)",
            }
        }

    def determine_direction(
        self, signals: Dict, intraday: bool = False,
        trade_date: date = None,
    ) -> Tuple[Optional[str], List[str]]:
        """Expanded composite signal using 4 signal groups.

        Signal groups:
          1. Core fear (5): skewing, rip, skew_25d_rr, contango, credit_spread
          2. Wing skew (2): wing_skew_30d, wing_skew_10d
          3. Funding stress (2): borrow_term, borrow_spread
          4. Vol momentum (3): iv_momentum, skewing_change, contango_change

        Composite priority (daily):
          - MULTI_SIGNAL_STRONG: 3+ groups firing (85%+ hit rate)
          - FEAR_BOUNCE_STRONG: 3+ core signals (86%)
          - FUNDING_STRESS: both borrow signals + 1 other group (85%)
          - WING_PANIC: both wing signals + 1 other group (81%)
          - VOL_ACCELERATION: 2+ momentum signals + 1 other group (77%)
          - FEAR_BOUNCE_LONG: 2 core signals (75%)

        Calendar overlays applied:
          - FOMC blackout: suppress all signals within 1 day of FOMC
          - VIXpiration discount: require stronger confirmation
          - OpEx amplifier: tag signal as high-confidence
        """
        cfg = self.config

        # Tier 1 firing (any ACTION signal)
        t1_all = [k for k, v in signals.items()
                  if v.get("tier") == 1 and v.get("level") == "ACTION"]

        # Per-group firing
        core_firing = [k for k in cfg.core_signals
                       if signals.get(k, {}).get("level") == "ACTION"]
        wing_firing = [k for k in cfg.wing_signals
                       if signals.get(k, {}).get("level") == "ACTION"]
        fund_firing = [k for k in cfg.funding_signals
                       if signals.get(k, {}).get("level") == "ACTION"]
        mom_firing = [k for k in cfg.momentum_signals
                      if signals.get(k, {}).get("level") == "ACTION"]

        # Count distinct groups firing
        groups_firing = sum([
            len(core_firing) >= 2,   # core needs 2+ to count
            len(wing_firing) >= 1,
            len(fund_firing) >= 1,
            len(mom_firing) >= 1,
        ])

        # Need at least some signal activity
        if len(t1_all) < 2:
            return None, t1_all

        # Calendar overlay
        cal = self.calendar_overlay(trade_date)

        # FOMC blackout: suppress signals entirely
        if cal["fomc_blackout"]:
            return None, t1_all

        # VIXpiration discount: raise thresholds
        vix_discount = (cal["vixpiration_discount"]
                        and not cal["opex_amplifier"])

        if intraday:
            # Intraday: bearish interpretation
            if groups_firing >= 3 or (len(core_firing) >= 3 and groups_firing >= 2):
                return "DIRECTIONAL_BEARISH", t1_all
            if len(core_firing) >= 2 or groups_firing >= 2:
                return "DIRECTIONAL_BEARISH_WEAK", t1_all
            return None, t1_all

        # Daily: bullish (contrarian) interpretation
        # Priority 1: Multi-group strong — highest confidence
        if groups_firing >= 3:
            if vix_discount and groups_firing < 4:
                return "FEAR_BOUNCE_STRONG", t1_all
            return "MULTI_SIGNAL_STRONG", t1_all

        # Priority 2: Core fear composite (original system)
        core_strong = (len(core_firing) >= cfg.composite_min
                       if not vix_discount
                       else len(core_firing) >= max(cfg.composite_min, 4))
        if core_strong:
            if cal["opex_amplifier"]:
                return "FEAR_BOUNCE_STRONG_OPEX", t1_all
            return "FEAR_BOUNCE_STRONG", t1_all

        # Priority 3: Funding stress — independent signal (85% independent)
        if len(fund_firing) >= 2 and groups_firing >= 2:
            return "FUNDING_STRESS", t1_all

        # Priority 4: Wing panic — crash skew spike
        if len(wing_firing) >= 2 and groups_firing >= 2:
            return "WING_PANIC", t1_all

        # Priority 5: Vol acceleration — momentum signals
        if len(mom_firing) >= 2 and groups_firing >= 2:
            return "VOL_ACCELERATION", t1_all

        # Priority 6: Weak fear (2 core signals)
        if len(core_firing) >= 2:
            return "FEAR_BOUNCE_LONG", t1_all

        return None, t1_all

    # -- regime-aware direction -----------------------------------------------

    def determine_regime_direction(
        self, signals: Dict, summary: Dict,
        intraday: bool = False, trade_date: date = None,
        iv_rank: float = None,
    ) -> Tuple[Optional[str], List[str], Optional[Dict]]:
        """Regime-aware composite signal for ALL market conditions.

        Unlike determine_direction() which only fires on FEAR days (~14%),
        this method classifies the vol surface into a regime and produces
        signals for FEAR, NERVOUS, FLAT, COMPLACENT, and GREED conditions.

        Returns:
            (composite_name, tier1_firing, regime_info)
            - composite_name: signal name or None
            - tier1_firing: list of tier-1 signals at ACTION level
            - regime_info: dict with regime, confidence, direction, structures
        """
        from .regime_classifier import VolSurfaceRegimeClassifier

        # First: run the standard fear-based detection
        fear_composite, t1_all = self.determine_direction(
            signals, intraday=intraday, trade_date=trade_date)

        # If FEAR signal fires, use it (highest priority)
        if fear_composite and "FEAR" in fear_composite:
            classifier = VolSurfaceRegimeClassifier()
            regime_info = classifier.classify(summary, iv_rank=iv_rank)
            regime_info["composite"] = fear_composite  # override with calendar-aware
            return fear_composite, t1_all, regime_info

        # Otherwise: classify regime and produce regime-specific signal
        classifier = VolSurfaceRegimeClassifier()
        regime_info = classifier.classify(summary, iv_rank=iv_rank)
        regime_composite = regime_info.get("composite")

        # Calendar overlay still applies
        if trade_date:
            cal = self.calendar_overlay(trade_date)
            if cal["fomc_blackout"]:
                regime_info["composite"] = None
                return None, t1_all, regime_info

        # For intraday: only fire regime signals on high confidence
        if intraday and regime_info["confidence"] < 0.75:
            return None, t1_all, regime_info

        return regime_composite, t1_all, regime_info

    # -- BaseAgent interface ----------------------------------------------

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Compute signals for a single ticker/summary.

        Context keys:
            ticker: str
            summary: Dict — ORATS summary row
            credit: Optional[Tuple] — (hyg, tlt, hyg_prev, tlt_prev)
            intraday: bool (default False)
            use_regime: bool (default False) — use regime-aware composites
        """
        ticker = context["ticker"]
        summary = context["summary"]
        intraday = context.get("intraday", False)
        use_regime = context.get("use_regime", False)

        signals = self.compute_signals(ticker, summary)

        # Merge credit signal if available
        credit = context.get("credit")
        if credit and len(credit) == 4:
            signals.update(self.compute_credit_signal(*credit))

        if use_regime:
            composite, t1, regime_info = self.determine_regime_direction(
                signals, summary, intraday=intraday)
            return self._result(
                success=True,
                data={
                    "signals": signals,
                    "composite": composite,
                    "tier1_firing": t1,
                    "regime": regime_info,
                },
            )

        composite, t1 = self.determine_direction(signals, intraday=intraday)

        return self._result(
            success=True,
            data={
                "signals": signals,
                "composite": composite,
                "tier1_firing": t1,
            },
        )

    # -- terminal dashboard -----------------------------------------------

    def _fmt_val(self, key: str, sig: Dict) -> Tuple[str, str, str]:
        v = sig.get("value", 0)
        # Format value based on signal type
        if key in ("iv_rv_spread", "skew_25d_rr", "fwd_kink", "credit_spread",
                    "iv_momentum", "skewing_change", "contango_change"):
            val_s = f"{v * 100:+.1f}%"
        elif key == "rip":
            val_s = f"{v:.1f}"
        elif key in ("model_confidence",):
            val_s = f"{v:.6f}"
        elif key in ("iv10_iv30_ratio",):
            val_s = f"{v:.3f}x"
        elif key in ("borrow_term", "borrow_spread", "mw_adj_30"):
            val_s = f"{v * 100:.2f}%"
        else:
            val_s = f"{v:.4f}"

        base_s = ""
        if "baseline" in sig:
            b = sig["baseline"]
            base_s = f"{b * 100:+.1f}%" if key == "skew_25d_rr" else f"{b:.4f}"
        elif "prev_day" in sig:
            p = sig["prev_day"]
            if key in ("iv_momentum", "skewing_change", "contango_change"):
                base_s = f"{p * 100:+.1f}%"
            else:
                base_s = f"{p:.4f}"

        chg_s = ""
        if key == "skew_25d_rr" and "change" in sig:
            chg_s = f"{sig['change'] * 100:+.1f}%"
        elif key == "contango" and "pct_change" in sig:
            chg_s = f"{sig['pct_change'] * 100:+.0f}%"
        elif "change" in sig:
            chg_s = f"{sig['change']:+.4f}"
        return val_s, base_s, chg_s

    def print_dashboard(
        self, ticker: str, spot_orats: float, spot_yf: Optional[float],
        signals: Dict, composite: Optional[str], t1_firing: List[str],
    ) -> None:
        now_s = datetime.now().strftime("%Y-%m-%d %I:%M %p ET")
        yf_s = f" (yf: {spot_yf:.2f})" if spot_yf else ""

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
        print(f"  {C.BOLD}0DTE SIGNAL MONITOR — {now_s}{C.RESET}")
        print(f"  {ticker}: {spot_orats:.2f}{yf_s}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")

        print(f"\n  {'SIGNAL':<26} {'VALUE':>8} {'BASELINE':>10} {'CHANGE':>8} STATUS")
        print(f"  {'-' * 62}")

        for key in self.SIGNAL_ORDER:
            sig = signals.get(key, {})
            label = sig.get("label", key)
            level = sig.get("level", "OK")
            val_s, base_s, chg_s = self._fmt_val(key, sig)

            if level == "ACTION":
                st = f"{C.RED}{C.BOLD}! ACTION{C.RESET}"
            elif level == "WARNING":
                st = f"{C.YELLOW}* WARNING{C.RESET}"
            elif level == "INFO":
                st = f"{C.BLUE}i INFO{C.RESET}"
            else:
                st = f"{C.GREEN}  OK{C.RESET}"

            print(f"  {label:<26} {val_s:>8} {base_s:>10} {chg_s:>8}  {st}")

        if composite:
            dm = {
                "DIRECTIONAL_BEARISH": ("BUY PUTS (3+ signals)", C.RED),
                "DIRECTIONAL_BEARISH_WEAK": ("BUY PUTS (2 signals)", C.RED),
                "DIRECTIONAL_BULLISH": ("BUY CALLS", C.GREEN),
                "FEAR_BOUNCE_STRONG": ("FEAR SPIKE -> BUY CALLS (3+ core, 86%)", C.GREEN),
                "FEAR_BOUNCE_STRONG_OPEX": ("FEAR SPIKE -> BUY CALLS (OpEx, 90%+)", C.GREEN),
                "FEAR_BOUNCE_LONG": ("FEAR SPIKE -> BUY CALLS (2 core, 75%)", C.GREEN),
                "FUNDING_STRESS": ("FUNDING STRESS -> contrarian BUY (85%)", C.GREEN),
                "VOL_ACCELERATION": ("VOL SPIKE -> BUY CALLS (77%)", C.GREEN),
                "WING_PANIC": ("WING SKEW SPIKE -> contrarian BUY (81%)", C.GREEN),
                "MULTI_SIGNAL_STRONG": ("MULTI-GROUP FIRE -> BUY CALLS (85%+)", C.GREEN),
            }
            action, clr = dm.get(composite, (composite, C.YELLOW))
            cfg = self.config
            all_action = [k for k, v in signals.items()
                          if v.get("level") == "ACTION"]
            core_firing = [k for k in cfg.core_signals if k in all_action]
            wing_firing = [k for k in cfg.wing_signals if k in all_action]
            fund_firing = [k for k in cfg.funding_signals if k in all_action]
            mom_firing = [k for k in cfg.momentum_signals if k in all_action]
            print(f"\n  {clr}{C.BOLD}{'=' * 60}{C.RESET}")
            print(f"  {clr}{C.BOLD}>>> {action} <<<{C.RESET}")
            groups = []
            if core_firing:
                groups.append(f"core={','.join(core_firing)}")
            if wing_firing:
                groups.append(f"wing={','.join(wing_firing)}")
            if fund_firing:
                groups.append(f"funding={','.join(fund_firing)}")
            if mom_firing:
                groups.append(f"momentum={','.join(mom_firing)}")
            print(f"  {C.DIM}{len(all_action)} signals firing: "
                  f"{' | '.join(groups)}{C.RESET}")
            print(f"  {clr}{C.BOLD}{'=' * 60}{C.RESET}")

    # -- live polling mode ------------------------------------------------

    @staticmethod
    def _market_open() -> bool:
        """Check if US equity markets are currently open (9:30-16:00 ET).

        Checks weekday, time of day, and major US market holidays.
        """
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        # Weekend check
        if now_et.weekday() >= 5:
            return False
        # US market holidays (fixed + observed rules)
        y = now_et.year
        holidays = set()
        # New Year's Day
        holidays.add(date(y, 1, 1))
        # MLK Day — 3rd Monday of January
        holidays.add(_nth_weekday(y, 1, 0, 3))
        # Presidents' Day — 3rd Monday of February
        holidays.add(_nth_weekday(y, 2, 0, 3))
        # Good Friday — 2 days before Easter Sunday
        holidays.add(_easter(y) - timedelta(days=2))
        # Memorial Day — last Monday of May
        holidays.add(_last_weekday(y, 5, 0))
        # Juneteenth
        holidays.add(date(y, 6, 19))
        # Independence Day
        holidays.add(date(y, 7, 4))
        # Labor Day — 1st Monday of September
        holidays.add(_nth_weekday(y, 9, 0, 1))
        # Thanksgiving — 4th Thursday of November
        holidays.add(_nth_weekday(y, 11, 3, 4))
        # Christmas
        holidays.add(date(y, 12, 25))
        # Observed: if holiday falls on Sat → Fri, Sun → Mon
        observed = set()
        for h in holidays:
            if h.weekday() == 5:  # Saturday
                observed.add(h - timedelta(days=1))
            elif h.weekday() == 6:  # Sunday
                observed.add(h + timedelta(days=1))
            else:
                observed.add(h)
        if now_et.date() in observed:
            return False
        t = now_et.time()
        from datetime import time as dtime
        return dtime(9, 30) <= t <= dtime(16, 0)

    def run_live(self, orats, state, db=None, auto_exit: bool = False) -> None:
        """Live polling mode. Runs until Ctrl+C or market close (if auto_exit).

        Args:
            auto_exit: If True, exit automatically when market closes (for cron).
        """
        cfg = self.config
        from ..agents.reporter import send_notification

        print(f"{C.BOLD}{C.CYAN}0DTE Signal Monitor — Live Mode{C.RESET}")
        print(f"  Tickers: {', '.join(cfg.tickers)}")
        print(f"  Interval: {cfg.poll_interval}s | Log: {state.signals_path}")
        if auto_exit:
            print(f"  Auto-exit: ON (will stop at 4:00 PM ET)")
        print(f"  Core composite: ANY {cfg.composite_min} of "
              f"{len(cfg.core_signals)} -> {', '.join(cfg.core_signals)}\n")

        # If auto_exit, check that market is actually open
        if auto_exit and not self._market_open():
            print(f"  {C.YELLOW}Market closed — skipping.{C.RESET}")
            return

        # Load previous-day summaries
        for days_back in range(1, 5):
            d = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            for ticker in cfg.tickers:
                if ticker in self.prev_day:
                    continue
                resp = orats.hist_summaries(ticker, d)
                if resp and resp.get("data"):
                    self.prev_day[ticker] = resp["data"][0]
                    print(f"  Loaded prev-day: {ticker} ({d})")

        n = 0
        try:
            while True:
                # Auto-exit after market close
                if auto_exit and not self._market_open():
                    print(f"\n{C.BOLD}Market closed. "
                          f"{len(self.log_entries)} entries "
                          f"-> {state.signals_path}{C.RESET}")
                    return
                n += 1
                hyg, tlt, hyg_p, tlt_p = yf_credit()
                credit_sig = self.compute_credit_signal(hyg, tlt, hyg_p, tlt_p)

                for ticker in cfg.tickers:
                    resp = orats.summaries(ticker)
                    if not resp or not resp.get("data"):
                        print(f"  {C.RED}No data for {ticker}{C.RESET}")
                        continue
                    summary = resp["data"][0]
                    spot = self._safe(summary, "stockPrice")
                    spot_yf = yf_price(ticker)

                    if ticker not in self.baseline:
                        self.baseline[ticker] = dict(summary)
                        print(f"  {C.GREEN}Baseline set: {ticker}{C.RESET}")

                    signals = self.compute_signals(ticker, summary)
                    signals.update(credit_sig)
                    composite, t1 = self.determine_direction(signals, intraday=True)
                    self.print_dashboard(ticker, spot, spot_yf, signals, composite, t1)

                    # Log
                    entry = {
                        "timestamp": datetime.now().isoformat(),
                        "ticker": ticker,
                        "spot_orats": spot,
                        "spot_yfinance": spot_yf,
                        "signals": {
                            k: {kk: vv for kk, vv in v.items() if kk != "label"}
                            for k, v in signals.items()
                        },
                        "composite": composite,
                    }
                    self.log_entries.append(entry)
                    state.append_signal(entry)

                    # DB logging
                    if db and db.enabled:
                        core_f = [k for k in cfg.core_signals
                                  if signals.get(k, {}).get("level") == "ACTION"]
                        wing_f = [k for k in cfg.wing_signals
                                  if signals.get(k, {}).get("level") == "ACTION"]
                        fund_f = [k for k in cfg.funding_signals
                                  if signals.get(k, {}).get("level") == "ACTION"]
                        mom_f = [k for k in cfg.momentum_signals
                                 if signals.get(k, {}).get("level") == "ACTION"]
                        grps = sum([len(core_f) >= 2, len(wing_f) >= 1,
                                    len(fund_f) >= 1, len(mom_f) >= 1])
                        db.run({
                            "action": "log_0dte_signal",
                            "ticker": ticker,
                            "trade_date": date.today().isoformat(),
                            "spot_price": spot,
                            "composite": composite,
                            "core_count": len(core_f),
                            "wing_count": len(wing_f),
                            "fund_count": len(fund_f),
                            "mom_count": len(mom_f),
                            "groups_firing": grps,
                            "signals": signals,
                        })

                    # Notifications
                    if composite:
                        dm = {
                            "DIRECTIONAL_BEARISH": "BUY PUTS (strong)",
                            "DIRECTIONAL_BEARISH_WEAK": "BUY PUTS (watch)",
                            "DIRECTIONAL_BULLISH": "BUY CALLS",
                        }
                        send_notification(
                            title=f"0DTE: {dm.get(composite, 'ALERT')}",
                            subtitle=f"{ticker} — {len(t1)} Tier 1",
                            message=", ".join(t1),
                        )
                    elif any(v["level"] == "WARNING" for v in signals.values()):
                        wn = sum(1 for v in signals.values() if v["level"] == "WARNING")
                        send_notification(
                            f"0DTE: {ticker} Warning",
                            f"{wn} warning(s)",
                            "Check terminal",
                            sound=False,
                        )

                print(f"\n  {C.DIM}Poll #{n}. Next in {cfg.poll_interval}s "
                      f"(Ctrl+C to stop){C.RESET}")
                time.sleep(cfg.poll_interval)

        except KeyboardInterrupt:
            print(f"\n{C.BOLD}Stopped. {len(self.log_entries)} entries "
                  f"-> {state.signals_path}{C.RESET}")

    # -- demo mode --------------------------------------------------------

    def run_demo(self, orats) -> None:
        """Render dashboard using most recent historical data."""
        cfg = self.config
        print(f"{C.BOLD}{C.CYAN}0DTE Signal Monitor — Demo Mode{C.RESET}")
        print(f"  Loading most recent trading day data...\n")

        prev_day = {}
        demo_day = {}
        demo_date = None
        for days_back in range(1, 8):
            d = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            for ticker in cfg.tickers:
                if ticker in demo_day:
                    continue
                resp = orats.hist_summaries(ticker, d)
                if resp and resp.get("data"):
                    demo_day[ticker] = resp["data"][0]
                    demo_date = d
            if len(demo_day) == len(cfg.tickers):
                break

        if not demo_day:
            print(f"  {C.RED}No recent data found{C.RESET}")
            return

        demo_dt = datetime.strptime(demo_date, "%Y-%m-%d").date()
        for days_back in range(1, 8):
            d = (demo_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")
            for ticker in cfg.tickers:
                if ticker in prev_day:
                    continue
                resp = orats.hist_summaries(ticker, d)
                if resp and resp.get("data"):
                    prev_day[ticker] = resp["data"][0]
                    self.prev_day[ticker] = resp["data"][0]
            if len(prev_day) == len(cfg.tickers):
                break

        for ticker, summary in demo_day.items():
            self.baseline[ticker] = dict(summary)

        hyg, tlt, hyg_p, tlt_p = yf_credit()
        credit_sig = self.compute_credit_signal(hyg, tlt, hyg_p, tlt_p)

        print(f"  {C.GREEN}Loaded data for {demo_date}{C.RESET}\n")

        for ticker, summary in demo_day.items():
            spot = self._safe(summary, "stockPrice")
            signals = self.compute_signals(ticker, summary)
            signals.update(credit_sig)
            composite, t1 = self.determine_direction(signals, intraday=False)
            self.print_dashboard(ticker, spot, None, signals, composite, t1)

    # -- backtest mode ----------------------------------------------------

    def run_backtest(self, orats, state, months: int = 6, db=None) -> None:
        """Backtest signals against historical data."""
        cfg = self.config
        end = date.today()
        start = end - timedelta(days=months * 30)

        print(f"{C.BOLD}{C.CYAN}0DTE Signal Backtest{C.RESET}")
        print(f"  {start} -> {end} | Tickers: {', '.join(cfg.tickers)}")
        print(f"  Composite: ANY {cfg.composite_min} of "
              f"{len(cfg.core_signals)} core signals\n")

        cache = state.load_cache()

        def _save_cache():
            state.save_cache(cache)

        # Fetch HYG/TLT credit data
        credit_map: Dict = {}
        ck = f"credit_{start}_{end}"
        if ck in cache:
            credit_map = cache[ck]
            print(f"  Credit data: {len(credit_map)} days (cached)")
        else:
            print(f"  Fetching HYG/TLT for credit spread...")
            try:
                import yfinance as yf
                for sym in ("HYG", "TLT"):
                    df = yf.Ticker(sym).history(start=str(start), end=str(end))
                    for d, row in df.iterrows():
                        dt = d.strftime("%Y-%m-%d")
                        if dt not in credit_map:
                            credit_map[dt] = {}
                        credit_map[dt][sym] = float(row["Close"])
                cache[ck] = credit_map
                _save_cache()
                print(f"  Credit data: {len(credit_map)} days")
            except Exception as exc:
                print(f"  {C.YELLOW}Credit data unavailable: {exc}{C.RESET}")

        for ticker in cfg.tickers:
            print(f"{'=' * 60}")
            print(f"  {C.BOLD}Backtesting {ticker}{C.RESET}")
            print(f"{'=' * 60}")

            # 1. Historical dailies
            dk = f"daily_{ticker}_{start}_{end}"
            if dk in cache:
                daily_data = cache[dk]
                print(f"  Dailies: {len(daily_data)} days (cached)")
            else:
                print(f"  Fetching dailies...")
                resp = orats.hist_dailies(ticker, f"{start},{end}")
                if not resp or not resp.get("data"):
                    print(f"  {C.RED}Failed to fetch dailies — skipping{C.RESET}")
                    continue
                daily_data = resp["data"]
                cache[dk] = daily_data
                _save_cache()
                print(f"  Dailies: {len(daily_data)} days")

            prices: Dict[str, Dict] = {}
            for d in daily_data:
                dt = str(d.get("tradeDate", ""))[:10]
                prices[dt] = d
            trade_dates = sorted(prices.keys())
            date_idx = {d: i for i, d in enumerate(trade_dates)}

            # 2. Historical summaries
            sk = f"summ_{ticker}_{start}_{end}"
            if sk in cache:
                summ_map = cache[sk]
                print(f"  Summaries: {len(summ_map)} days (cached)")
            else:
                print(f"  Fetching summaries...")
                resp = orats.get("hist/summaries", {
                    "ticker": ticker,
                    "tradeDate": f"{start},{end}",
                })
                if resp and resp.get("data") and len(resp["data"]) > 5:
                    summ_map = {}
                    for row in resp["data"]:
                        dt = str(row.get("tradeDate", ""))[:10]
                        summ_map[dt] = row
                    print(f"  Summaries: {len(summ_map)} days (range query)")
                else:
                    summ_map = {}
                    total = len(trade_dates)
                    for i, dt in enumerate(trade_dates):
                        if (i + 1) % 20 == 0 or i == 0:
                            pct = (i + 1) / total * 100
                            print(f"\r    Summaries: {i+1}/{total} ({pct:.0f}%)    ",
                                  end="", flush=True)
                        resp = orats.hist_summaries(ticker, dt)
                        if resp and resp.get("data"):
                            summ_map[dt] = resp["data"][0]
                        time.sleep(0.2)
                    print(f"\n    Summaries: {len(summ_map)} days")
                cache[sk] = summ_map
                _save_cache()

            # 3. Compute signals per day
            sorted_dates = sorted(d for d in summ_map if d in date_idx)
            results: List[Dict] = []

            for i, dt in enumerate(sorted_dates):
                if i == 0:
                    continue
                prev_dt = sorted_dates[i - 1]
                self.prev_day[ticker] = summ_map[prev_dt]
                self.baseline[ticker] = summ_map[prev_dt]

                signals = self.compute_signals(ticker, summ_map[dt])

                cd = credit_map.get(dt, {})
                cd_prev = credit_map.get(prev_dt, {})
                if cd.get("HYG") and cd.get("TLT") and cd_prev.get("HYG") and cd_prev.get("TLT"):
                    signals.update(self.compute_credit_signal(
                        cd["HYG"], cd["TLT"], cd_prev["HYG"], cd_prev["TLT"],
                    ))

                composite, t1 = self.determine_direction(signals, intraday=False)

                idx = date_idx.get(dt)
                if idx is None or idx + 1 >= len(trade_dates):
                    continue
                next_dt = trade_dates[idx + 1]
                cls_today = float(prices[dt].get("clsPx", 0) or 0)
                cls_next = float(prices[next_dt].get("clsPx", 0) or 0)
                if not cls_today or not cls_next:
                    continue

                nxt_ret = (cls_next - cls_today) / cls_today
                results.append({
                    "date": dt, "signals": signals,
                    "composite": composite, "tier1": t1,
                    "next_return": nxt_ret,
                    "close": cls_today, "next_close": cls_next,
                })

            # Log signal days to DB
            if db and db.enabled:
                for r in results:
                    if r["composite"]:
                        core_f = [k for k in cfg.core_signals
                                  if r["signals"].get(k, {}).get("level") == "ACTION"]
                        wing_f = [k for k in cfg.wing_signals
                                  if r["signals"].get(k, {}).get("level") == "ACTION"]
                        fund_f = [k for k in cfg.funding_signals
                                  if r["signals"].get(k, {}).get("level") == "ACTION"]
                        mom_f = [k for k in cfg.momentum_signals
                                 if r["signals"].get(k, {}).get("level") == "ACTION"]
                        grps = sum([len(core_f) >= 2, len(wing_f) >= 1,
                                    len(fund_f) >= 1, len(mom_f) >= 1])
                        db.run({
                            "action": "log_0dte_signal",
                            "ticker": ticker,
                            "trade_date": r["date"],
                            "spot_price": r["close"],
                            "composite": r["composite"],
                            "core_count": len(core_f),
                            "wing_count": len(wing_f),
                            "fund_count": len(fund_f),
                            "mom_count": len(mom_f),
                            "groups_firing": grps,
                            "signals": r["signals"],
                        })
                print(f"  [DB] Logged {sum(1 for r in results if r['composite'])} signal days")

            self._print_backtest_results(ticker, results, end)

        print(f"\n{C.BOLD}Cache saved to {state.cache_path}{C.RESET}")

    def _print_backtest_results(
        self, ticker: str, results: List[Dict], end_date: date,
    ) -> None:
        if not results:
            print(f"  {C.RED}No results to analyze{C.RESET}")
            return

        print(f"\n  {C.BOLD}Signal Analysis — {ticker} ({len(results)} days){C.RESET}")
        print(f"  {'-' * 56}")

        for sk in self.SIGNAL_ORDER:
            fired = [
                r for r in results
                if r["signals"].get(sk, {}).get("level") in ("ACTION", "WARNING", "INFO")
            ]
            tier = results[0]["signals"].get(sk, {}).get("tier", "?")
            if not fired:
                print(f"  T{tier} {sk:<22} never fired")
                continue
            rets = [r["next_return"] for r in fired]
            avg = sum(rets) / len(rets)
            up = sum(1 for r in rets if r > 0)
            print(f"  T{tier} {sk:<22} {len(fired):>3}d | "
                  f"avg {avg * 100:+.2f}% | "
                  f"up {up}/{len(fired)} ({up / len(fired):.0%})")

        print(f"\n  {C.BOLD}Composite Signals:{C.RESET}")
        for label, direction, exp_sign in [
            ("STRONG (3+)", "FEAR_BOUNCE_STRONG", 1),
            ("MODERATE (2)", "FEAR_BOUNCE_LONG", 1),
        ]:
            days = [r for r in results if r["composite"] == direction]
            if not days:
                print(f"  {label:<15} never fired")
                continue
            rets = [r["next_return"] for r in days]
            avg = sum(rets) / len(rets)
            hits = sum(1 for r in rets if r > 0) if exp_sign == 1 else sum(1 for r in rets if r < 0)
            hr = hits / len(rets)
            hit_rets = [r for r in rets if (r > 0 if exp_sign == 1 else r < 0)]
            avg_hit = (sum(abs(r) for r in hit_rets) / len(hit_rets) * 100
                       if hit_rets else 0)

            clr = C.GREEN if hr > 0.55 else C.RED if hr < 0.45 else C.YELLOW
            print(f"  {label:<15} {len(days):>3}d | "
                  f"hit rate {clr}{hr:.0%}{C.RESET} | "
                  f"avg ret {avg * 100:+.2f}% | "
                  f"avg hit {avg_hit:.2f}%")

        sig_days = [r for r in results if r["composite"]]
        quiet = [r for r in results if not r["composite"]]
        avg_sig = (sum(abs(r["next_return"]) for r in sig_days) / len(sig_days) * 100
                   if sig_days else 0)
        avg_q = (sum(abs(r["next_return"]) for r in quiet) / len(quiet) * 100
                 if quiet else 0)

        print(f"\n  {C.BOLD}Summary:{C.RESET}")
        print(f"  Signal days: {len(sig_days):>3} | avg abs move: {avg_sig:.2f}%")
        print(f"  Quiet days:  {len(quiet):>3} | avg abs move: {avg_q:.2f}%")
        if avg_q > 0:
            print(f"  Move ratio:  {avg_sig / avg_q:.1f}x on signal days")

        cutoff = (end_date - timedelta(days=60)).strftime("%Y-%m-%d")
        recent = [r for r in sig_days if r["date"] >= cutoff]
        if recent:
            print(f"\n  {C.BOLD}Recent signals (last 60d):{C.RESET}")
            for r in recent[-10:]:
                d = r["composite"] or ""
                if "LONG" in d or "BULL" in d or "BOUNCE" in d:
                    arrow, clr = "^", C.GREEN
                    hit = r["next_return"] > 0
                elif "SHORT" in d or "BEAR" in d:
                    arrow, clr = "v", C.RED
                    hit = r["next_return"] < 0
                else:
                    arrow, clr = "~", C.MAGENTA
                    hit = abs(r["next_return"]) > 0.005
                mark = "Y" if hit else "N"
                print(f"    {r['date']} {clr}{arrow}{C.RESET} "
                      f"next day {r['next_return'] * 100:+.2f}% ({mark})")

        print()
