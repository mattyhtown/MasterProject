"""
SupabaseSync — log pipeline activity to Supabase.

Extracted from trading_pipeline.py.
"""

from datetime import date, datetime
from typing import Dict, Optional

from ..config import SupabaseCfg, ScannerCfg
from ..agents.monitor import calculate_dte


class SupabaseSync:
    """Sync positions, alerts, IV rank, and greeks to Supabase."""

    def __init__(self, config: SupabaseCfg = None):
        config = config or SupabaseCfg()
        self.client = None
        self.strategy_id: Optional[str] = None
        if config.url and config.key:
            try:
                from supabase import create_client
                self.client = create_client(config.url, config.key)
                self._ensure_strategy()
                print("[Supabase] Connected")
            except Exception as exc:
                print(f"[Supabase] Init failed (continuing without DB): {exc}")
                self.client = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _ensure_strategy(self) -> None:
        """Get or create the pipeline strategy record."""
        resp = self.client.table("strategies").select("id").eq(
            "name", "APEX-SHARPE Pipeline"
        ).limit(1).execute()
        if resp.data:
            self.strategy_id = resp.data[0]["id"]
        else:
            scanner_cfg = ScannerCfg()
            resp = self.client.table("strategies").insert({
                "name": "APEX-SHARPE Pipeline",
                "strategy_type": "IRON_CONDOR",
                "description": "Automated iron condor scanning and monitoring",
                "parameters": {
                    "iv_rank_min": scanner_cfg.iv_rank_min,
                    "dte_min": scanner_cfg.dte_min,
                    "dte_max": scanner_cfg.dte_max,
                    "short_delta": scanner_cfg.short_delta,
                    "long_delta": scanner_cfg.long_delta,
                },
            }).execute()
            if resp.data:
                self.strategy_id = resp.data[0]["id"]

    def log_position_open(self, position: Dict) -> Optional[str]:
        """Insert an OPEN position + legs. Returns Supabase position UUID."""
        if not self.enabled:
            return None
        try:
            now = datetime.now().isoformat()
            dte = calculate_dte(position["expiration"])
            row = {
                "symbol": position["symbol"],
                "position_type": position["type"],
                "entry_date": position["entry_date"],
                "entry_time": now,
                "entry_premium": position["entry_credit"],
                "entry_iv_rank": position.get("iv_rank_at_entry"),
                "entry_dte": dte,
                "max_profit": position["max_profit"],
                "max_loss": position["max_loss"],
                "status": "OPEN",
                "strategy_id": self.strategy_id,
                "notes": position["id"],
            }
            resp = self.client.table("positions").insert(row).execute()
            if not resp.data:
                return None
            pos_uuid = resp.data[0]["id"]

            # Insert legs
            for i, leg in enumerate(position["legs"]):
                action_map = {
                    ("BUY", "PUT"): "BTO", ("SELL", "PUT"): "STO",
                    ("BUY", "CALL"): "BTO", ("SELL", "CALL"): "STO",
                }
                self.client.table("position_legs").insert({
                    "position_id": pos_uuid,
                    "leg_index": i,
                    "option_type": leg["type"],
                    "strike": leg["strike"],
                    "expiration_date": position["expiration"],
                    "quantity": -1 if leg["action"] == "SELL" else 1,
                    "action": action_map.get((leg["action"], leg["type"]), "BTO"),
                    "entry_price": leg["entry_price"],
                    "entry_fill_time": now,
                    "entry_delta": leg.get("delta"),
                    "commission": position.get("commission", 2.60) / 4,
                }).execute()

            print(f"[Supabase] Position {position['id']} -> {pos_uuid}")
            return pos_uuid
        except Exception as exc:
            print(f"[Supabase] log_position_open failed: {exc}")
            return None

    def log_position_close(self, position: Dict) -> None:
        """Update a position to CLOSED in Supabase."""
        if not self.enabled:
            return
        try:
            resp = self.client.table("positions").select("id").eq(
                "notes", position["id"]
            ).limit(1).execute()
            if not resp.data:
                print(f"[Supabase] Position {position['id']} not found in DB")
                return
            pos_uuid = resp.data[0]["id"]
            dte = calculate_dte(position["expiration"]) if position.get("expiration") else 0
            self.client.table("positions").update({
                "status": "CLOSED",
                "exit_date": position.get("exit_date", date.today().isoformat()),
                "exit_time": datetime.now().isoformat(),
                "exit_reason": position.get("exit_reason", "UNKNOWN"),
                "realized_pnl": position.get("realized_pnl", 0),
                "exit_dte": dte,
            }).eq("id", pos_uuid).execute()
            print(f"[Supabase] Closed {position['id']} -> {pos_uuid}")
        except Exception as exc:
            print(f"[Supabase] log_position_close failed: {exc}")

    def log_greeks_snapshot(self, position: Dict, current_price: float, valuation: Dict) -> None:
        """Record a greeks/valuation snapshot."""
        if not self.enabled or not valuation.get("leg_details"):
            return
        try:
            resp = self.client.table("positions").select("id").eq(
                "notes", position["id"]
            ).limit(1).execute()
            if not resp.data:
                return
            pos_uuid = resp.data[0]["id"]
            dte = calculate_dte(position["expiration"])

            p_delta = 0.0
            for ld in valuation["leg_details"]:
                sign = -1 if ld["action"] == "SELL" else 1
                p_delta += sign * ld.get("current_delta", 0)

            self.client.table("greeks_history").insert({
                "position_id": pos_uuid,
                "trade_date": date.today().isoformat(),
                "dte": dte,
                "underlying_price": current_price,
                "portfolio_delta": round(p_delta, 4),
                "position_value": valuation["pnl"],
                "unrealized_pnl": valuation["pnl"],
            }).execute()
        except Exception as exc:
            print(f"[Supabase] log_greeks_snapshot failed: {exc}")

    def log_iv_rank(self, symbol: str, iv_rank: float, stock_price: float) -> None:
        """Record IV rank history."""
        if not self.enabled or iv_rank is None:
            return
        try:
            self.client.table("iv_rank_history").upsert(
                {
                    "symbol": symbol,
                    "trade_date": date.today().isoformat(),
                    "current_iv": iv_rank / 100.0,
                    "iv_rank": iv_rank,
                    "underlying_price": stock_price,
                },
                on_conflict="symbol,trade_date",
            ).execute()
        except Exception as exc:
            print(f"[Supabase] log_iv_rank failed: {exc}")

    def log_alert(self, position: Dict, alert: Dict) -> None:
        """Record an alert."""
        if not self.enabled:
            return
        try:
            resp = self.client.table("positions").select("id").eq(
                "notes", position["id"]
            ).limit(1).execute()
            pos_uuid = resp.data[0]["id"] if resp.data else None

            severity_map = {"ACTION": "CRITICAL", "WARNING": "WARNING"}
            self.client.table("alerts").insert({
                "position_id": pos_uuid,
                "alert_type": alert.get("action", "UNKNOWN").split(" - ")[-1] if alert.get("action") else "UNKNOWN",
                "severity": severity_map.get(alert["level"], "INFO"),
                "message": alert["message"],
            }).execute()
        except Exception as exc:
            print(f"[Supabase] log_alert failed: {exc}")
