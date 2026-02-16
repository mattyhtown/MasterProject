"""
ScannerAgent — scan the watchlist for iron condor opportunities.

Extracted from trading_pipeline.py. Uses ORATSClient via DI instead of
module-level fetch_* functions.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import ScannerCfg
from ..types import AgentResult, C


def _calculate_dte(expiration_str: str) -> int:
    exp = datetime.strptime(expiration_str, "%Y-%m-%d").date()
    return (exp - date.today()).days


class ScannerAgent(BaseAgent):
    """Scan the watchlist for iron condor opportunities."""

    def __init__(self, config: ScannerCfg = None):
        config = config or ScannerCfg()
        super().__init__("Scanner", config)

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _find_put_by_delta(
        strikes: List[Dict], target: float, tol: float
    ) -> List[Dict]:
        """Find put strikes closest to |target| delta.
        ORATS 'delta' field is call delta; put delta = call_delta - 1.
        """
        matches = []
        for row in strikes:
            cd = row.get("delta")
            if cd is None:
                continue
            pd = cd - 1  # put delta (negative)
            if abs(abs(pd) - target) <= tol:
                row = dict(row)
                row["put_delta"] = pd
                matches.append(row)
        matches.sort(key=lambda r: abs(abs(r["put_delta"]) - target))
        return matches

    @staticmethod
    def _find_call_by_delta(
        strikes: List[Dict], target: float, tol: float
    ) -> List[Dict]:
        matches = []
        for row in strikes:
            d = row.get("delta")
            if d is not None and d > 0 and abs(d - target) <= tol:
                matches.append(row)
        matches.sort(key=lambda r: abs(r["delta"] - target))
        return matches

    # -- main entry -------------------------------------------------------

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Scan for IC candidates.

        Context keys:
            orats: ORATSClient instance
        """
        orats = context["orats"]
        cfg = self.config
        candidates: List[Dict] = []
        messages: List[str] = []

        for ticker in cfg.watchlist:
            print(f"\n{C.BOLD}[Scanner]{C.RESET} Evaluating {ticker}...")

            # 1. Check IV rank
            iv_data = orats.iv_rank(ticker)
            iv_rank: Optional[float] = None
            if iv_data and iv_data.get("data"):
                row0 = iv_data["data"][0]
                iv_rank = row0.get("ivRank1m", row0.get("ivRank"))
                print(f"  IV rank (1m): {iv_rank}")

            if iv_rank is not None and iv_rank < cfg.iv_rank_min:
                msg = f"{ticker}: IV rank {iv_rank} < {cfg.iv_rank_min} — skipping"
                print(f"  {msg}")
                messages.append(msg)
                continue

            # 2. Get stock price
            summ = orats.summaries(ticker)
            stock_price: Optional[float] = None
            if summ and summ.get("data"):
                stock_price = summ["data"][0].get("stockPrice")
            if stock_price is None:
                messages.append(f"{ticker}: Could not fetch stock price")
                print("  Could not fetch stock price — skipping")
                continue
            print(f"  Stock price: ${stock_price:.2f}")

            # 3. Find suitable expiration
            exp_data = orats.expirations(ticker)
            if not exp_data or not exp_data.get("data"):
                messages.append(f"{ticker}: No expirations available")
                print("  No expirations available — skipping")
                continue

            target_expiry: Optional[str] = None
            exp_list = sorted(exp_data["data"])
            for exp_str in exp_list:
                if not isinstance(exp_str, str) or len(exp_str) < 10:
                    continue
                dte = _calculate_dte(exp_str)
                if cfg.dte_min <= dte <= cfg.dte_max:
                    target_expiry = exp_str
                    print(f"  Selected expiry: {exp_str} ({dte} DTE)")
                    break

            if target_expiry is None:
                messages.append(f"{ticker}: No expiry in {cfg.dte_min}-{cfg.dte_max} DTE range")
                print(f"  No expiry in {cfg.dte_min}-{cfg.dte_max} DTE range — skipping")
                continue

            # 4. Fetch chain
            chain = orats.chain(ticker, target_expiry)
            if not chain or not chain.get("data"):
                messages.append(f"{ticker}: Chain fetch failed")
                print("  Chain fetch failed — skipping")
                continue

            # Filter to target expiry (ORATS may return all expirations)
            strikes = [s for s in chain["data"] if s.get("expirDate") == target_expiry]
            if not strikes:
                messages.append(f"{ticker}: No strikes for target expiry")
                print("  No strikes for target expiry in chain — skipping")
                continue
            print(f"  Chain loaded: {len(strikes)} strikes (filtered to {target_expiry})")

            # 5. Select 4 legs by delta
            short_puts = self._find_put_by_delta(strikes, cfg.short_delta, cfg.delta_tolerance)
            long_puts = self._find_put_by_delta(strikes, cfg.long_delta, cfg.delta_tolerance)
            short_calls = self._find_call_by_delta(strikes, cfg.short_delta, cfg.delta_tolerance)
            long_calls = self._find_call_by_delta(strikes, cfg.long_delta, cfg.delta_tolerance)

            if not (short_puts and long_puts and short_calls and long_calls):
                messages.append(f"{ticker}: Could not find 4 legs matching delta targets")
                print("  Could not find 4 legs matching delta targets — skipping")
                continue

            sp, lp, sc, lc = short_puts[0], long_puts[0], short_calls[0], long_calls[0]

            # 6. Compute metrics
            put_credit = sp["putBidPrice"] - lp["putAskPrice"]
            call_credit = sc["callBidPrice"] - lc["callAskPrice"]
            total_credit = put_credit + call_credit

            put_width = sp["strike"] - lp["strike"]
            call_width = lc["strike"] - sc["strike"]
            max_width = max(put_width, call_width)

            max_profit = total_credit * 100
            max_loss = max_width * 100 - max_profit

            put_breakeven = sp["strike"] - total_credit
            call_breakeven = sc["strike"] + total_credit

            dte = _calculate_dte(target_expiry)

            candidate = {
                "symbol": ticker,
                "expiration": target_expiry,
                "dte": dte,
                "stock_price": stock_price,
                "iv_rank": iv_rank,
                "legs": [
                    {
                        "type": "PUT", "action": "BUY",
                        "strike": lp["strike"],
                        "price": lp["putAskPrice"],
                        "delta": lp.get("put_delta", lp.get("delta", 0) - 1),
                    },
                    {
                        "type": "PUT", "action": "SELL",
                        "strike": sp["strike"],
                        "price": sp["putBidPrice"],
                        "delta": sp.get("put_delta", sp.get("delta", 0) - 1),
                    },
                    {
                        "type": "CALL", "action": "SELL",
                        "strike": sc["strike"],
                        "price": sc["callBidPrice"],
                        "delta": sc["delta"],
                    },
                    {
                        "type": "CALL", "action": "BUY",
                        "strike": lc["strike"],
                        "price": lc["callAskPrice"],
                        "delta": lc["delta"],
                    },
                ],
                "total_credit": round(total_credit, 2),
                "max_profit": round(max_profit, 2),
                "max_loss": round(max_loss, 2),
                "breakeven_lower": round(put_breakeven, 2),
                "breakeven_upper": round(call_breakeven, 2),
                "put_width": put_width,
                "call_width": call_width,
            }

            print(f"  Candidate: credit ${total_credit:.2f}, "
                  f"max P/L ${max_profit:.0f}/${max_loss:.0f}, "
                  f"BE ${put_breakeven:.0f}-${call_breakeven:.0f}")
            candidates.append(candidate)

        print(f"\n{C.BOLD}[Scanner]{C.RESET} Found {len(candidates)} candidate(s)")
        return self._result(
            success=True,
            data={"candidates": candidates},
            messages=messages,
        )
