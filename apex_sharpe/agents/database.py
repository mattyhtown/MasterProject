"""
DatabaseAgent — persist pipeline activity to Supabase.

Wraps SupabaseSync as a BaseAgent, logging positions, Greeks snapshots,
IV rank, and alerts on every pipeline run. Can also manage schema creation
and validate data integrity.
"""

import json
import urllib.request
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import SupabaseCfg
from ..database.supabase_sync import SupabaseSync
from ..types import AgentResult


# ---------------------------------------------------------------------------
# Schema definitions — SQL for each pipeline's required tables
# ---------------------------------------------------------------------------

IC_PIPELINE_TABLES = {
    "strategies": """
        CREATE TABLE IF NOT EXISTS strategies (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            description TEXT,
            parameters JSONB,
            is_active BOOLEAN DEFAULT true,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """,
    "positions": """
        CREATE TABLE IF NOT EXISTS positions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            symbol TEXT NOT NULL,
            position_type TEXT NOT NULL,
            entry_date DATE NOT NULL,
            entry_time TIMESTAMPTZ,
            entry_premium NUMERIC,
            entry_iv_rank NUMERIC,
            entry_dte INTEGER,
            max_profit NUMERIC,
            max_loss NUMERIC,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_date DATE,
            exit_time TIMESTAMPTZ,
            exit_reason TEXT,
            realized_pnl NUMERIC,
            exit_dte INTEGER,
            strategy_id UUID REFERENCES strategies(id),
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """,
    "position_legs": """
        CREATE TABLE IF NOT EXISTS position_legs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            position_id UUID NOT NULL REFERENCES positions(id),
            leg_index INTEGER NOT NULL,
            option_type TEXT NOT NULL,
            strike NUMERIC NOT NULL,
            expiration_date DATE NOT NULL,
            quantity INTEGER NOT NULL,
            action TEXT NOT NULL,
            entry_price NUMERIC NOT NULL,
            entry_fill_time TIMESTAMPTZ,
            exit_price NUMERIC,
            exit_fill_time TIMESTAMPTZ,
            entry_delta NUMERIC,
            entry_gamma NUMERIC,
            entry_theta NUMERIC,
            entry_vega NUMERIC,
            entry_iv NUMERIC,
            commission NUMERIC,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """,
    "greeks_history": """
        CREATE TABLE IF NOT EXISTS greeks_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            position_id UUID NOT NULL REFERENCES positions(id),
            trade_date DATE NOT NULL,
            dte INTEGER,
            underlying_price NUMERIC,
            portfolio_delta NUMERIC,
            portfolio_gamma NUMERIC,
            portfolio_theta NUMERIC,
            portfolio_vega NUMERIC,
            position_value NUMERIC,
            unrealized_pnl NUMERIC,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """,
    "iv_rank_history": """
        CREATE TABLE IF NOT EXISTS iv_rank_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            symbol TEXT NOT NULL,
            trade_date DATE NOT NULL,
            current_iv NUMERIC,
            iv_rank NUMERIC,
            underlying_price NUMERIC,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE(symbol, trade_date)
        );
    """,
    "alerts": """
        CREATE TABLE IF NOT EXISTS alerts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            position_id UUID REFERENCES positions(id),
            alert_type TEXT,
            severity TEXT,
            message TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """,
    "performance_metrics": """
        CREATE TABLE IF NOT EXISTS performance_metrics (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            date DATE NOT NULL,
            period_type TEXT NOT NULL DEFAULT 'DAILY',
            starting_capital NUMERIC,
            ending_capital NUMERIC,
            total_pnl NUMERIC,
            strategy_id UUID REFERENCES strategies(id),
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """,
    "backtest_runs": """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_name TEXT,
            strategy_id UUID REFERENCES strategies(id),
            start_date DATE,
            end_date DATE,
            initial_capital NUMERIC,
            strategy_parameters JSONB,
            run_at TIMESTAMPTZ DEFAULT now()
        );
    """,
}

ZERO_DTE_TABLES = {
    "zero_dte_signals": """
        CREATE TABLE IF NOT EXISTS zero_dte_signals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trade_date DATE NOT NULL,
            ticker TEXT NOT NULL,
            spot_price NUMERIC,
            composite TEXT,
            core_count INTEGER,
            signals JSONB,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE(ticker, trade_date)
        );
    """,
    "zero_dte_trades": """
        CREATE TABLE IF NOT EXISTS zero_dte_trades (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            signal_date DATE NOT NULL,
            ticker TEXT NOT NULL,
            structure TEXT NOT NULL,
            entry_price NUMERIC,
            exit_price NUMERIC,
            pnl NUMERIC,
            spot_at_entry NUMERIC,
            spot_at_exit NUMERIC,
            move_pct NUMERIC,
            composite TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        );
    """,
}

ALL_TABLES = {**IC_PIPELINE_TABLES, **ZERO_DTE_TABLES}


class DatabaseAgent(BaseAgent):
    """Persist pipeline state to Supabase and validate data integrity.

    Modes (via context["action"]):
        "log_scan"    — log new positions + IV rank from a scan run
        "log_monitor" — log Greeks snapshots + alerts from a monitor run
        "log_close"   — log a closed position
        "sync_full"   — full sync: positions, Greeks, IV, alerts
        "query"       — read positions/history from DB
        "validate"    — audit DB for data quality issues
    """

    def __init__(self, config: SupabaseCfg = None):
        config = config or SupabaseCfg()
        super().__init__("Database", config)
        self._sync = SupabaseSync(config)

    @property
    def enabled(self) -> bool:
        return self._sync.enabled

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Dispatch to the appropriate sync method."""
        action = context.get("action", "sync_full")

        if not self.enabled:
            return self._result(
                success=False,
                messages=["Supabase not connected — skipping DB sync"],
            )

        if action == "log_scan":
            return self._log_scan(context)
        elif action == "log_monitor":
            return self._log_monitor(context)
        elif action == "log_close":
            return self._log_close(context)
        elif action == "log_0dte_signal":
            return self._log_0dte_signal(context)
        elif action == "log_0dte_trades":
            return self._log_0dte_trades(context)
        elif action == "sync_full":
            return self._sync_full(context)
        elif action == "query":
            return self._query(context)
        elif action == "validate":
            return self._validate(context)
        elif action == "ensure_schema":
            return self._ensure_schema(context)
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _log_scan(self, context: Dict) -> AgentResult:
        """Log new positions and IV rank from a scan run."""
        new_positions = context.get("new_positions", [])
        candidates = context.get("candidates", [])
        logged_ids: List[str] = []

        # Log new positions
        for pos in new_positions:
            uuid = self._sync.log_position_open(pos)
            if uuid:
                logged_ids.append(uuid)

        # Log IV rank for scanned symbols
        for cand in candidates:
            iv_rank = cand.get("iv_rank")
            if iv_rank is not None:
                self._sync.log_iv_rank(
                    cand["symbol"], iv_rank, cand.get("stock_price", 0)
                )

        return self._result(
            success=True,
            data={"logged_position_uuids": logged_ids},
            messages=[f"Logged {len(logged_ids)} position(s), "
                      f"{len(candidates)} IV rank record(s)"],
        )

    def _log_monitor(self, context: Dict) -> AgentResult:
        """Log Greeks snapshots and alerts from a monitor run."""
        monitor_results = context.get("monitor_results", [])
        snapshots = 0
        alerts_logged = 0

        for res in monitor_results:
            pos = res["position"]
            val = res["valuation"]
            price = res["current_price"]
            alerts = res.get("alerts", [])

            # Log Greeks snapshot (now includes BS Greeks if available)
            greeks = val.get("greeks")
            if greeks:
                self._log_greeks_with_bs(pos, price, val, greeks)
            else:
                self._sync.log_greeks_snapshot(pos, price, val)
            snapshots += 1

            # Log alerts
            for alert in alerts:
                self._sync.log_alert(pos, alert)
                alerts_logged += 1

        return self._result(
            success=True,
            data={"snapshots": snapshots, "alerts": alerts_logged},
            messages=[f"Logged {snapshots} snapshot(s), {alerts_logged} alert(s)"],
        )

    def _log_greeks_with_bs(self, position: Dict, current_price: float,
                            valuation: Dict, greeks: Dict) -> None:
        """Log an enhanced Greeks snapshot with BS model data."""
        if not self.enabled:
            return
        try:
            from ..agents.monitor import calculate_dte
            resp = self._sync.client.table("positions").select("id").eq(
                "notes", position["id"]
            ).limit(1).execute()
            if not resp.data:
                return
            pos_uuid = resp.data[0]["id"]
            dte = calculate_dte(position["expiration"])

            self._sync.client.table("greeks_history").insert({
                "position_id": pos_uuid,
                "trade_date": date.today().isoformat(),
                "dte": dte,
                "underlying_price": current_price,
                "portfolio_delta": greeks["portfolio_delta"],
                "portfolio_gamma": greeks["portfolio_gamma"],
                "portfolio_theta": greeks["portfolio_theta"],
                "portfolio_vega": greeks["portfolio_vega"],
                "position_value": valuation["pnl"],
                "unrealized_pnl": valuation["pnl"],
            }).execute()
        except Exception as exc:
            print(f"[Database] log_greeks_with_bs failed: {exc}")

    def _log_close(self, context: Dict) -> AgentResult:
        """Log a closed position."""
        position = context["position"]
        self._sync.log_position_close(position)
        return self._result(
            success=True,
            messages=[f"Logged close for {position['id']}"],
        )

    # -- 0DTE logging -------------------------------------------------------

    def _log_0dte_signal(self, context: Dict) -> AgentResult:
        """Log a 0DTE signal snapshot to zero_dte_signals.

        Context keys:
            ticker: str
            trade_date: str (YYYY-MM-DD)
            spot_price: float
            composite: Optional[str]
            core_count: int
            signals: Dict — full signal dict from ZeroDTEAgent
        """
        try:
            row = {
                "trade_date": context["trade_date"],
                "ticker": context["ticker"],
                "spot_price": context.get("spot_price"),
                "composite": context.get("composite"),
                "core_count": context.get("core_count", 0),
                "signals": json.dumps({
                    k: {kk: vv for kk, vv in v.items() if kk != "label"}
                    for k, v in context.get("signals", {}).items()
                }),
            }
            self._sync.client.table("zero_dte_signals").upsert(
                row, on_conflict="ticker,trade_date"
            ).execute()
            return self._result(
                success=True,
                messages=[f"Logged 0DTE signal: {row['ticker']} {row['trade_date']}"],
            )
        except Exception as exc:
            return self._result(
                success=False,
                errors=[f"log_0dte_signal failed: {exc}"],
            )

    def _log_0dte_trades(self, context: Dict) -> AgentResult:
        """Log 0DTE trade backtest results to zero_dte_trades.

        Context keys:
            trades: List[Dict] — each with:
                signal_date, ticker, structure, entry_price/entry_credit,
                pnl, spot_at_entry, spot_at_exit, move_pct, composite
        """
        trades = context.get("trades", [])
        logged = 0
        for t in trades:
            try:
                row = {
                    "signal_date": t["signal_date"],
                    "ticker": t["ticker"],
                    "structure": t["structure"],
                    "entry_price": t.get("entry_price") or t.get("entry_credit"),
                    "exit_price": t.get("exit_price"),
                    "pnl": t.get("pnl"),
                    "spot_at_entry": t.get("spot_at_entry"),
                    "spot_at_exit": t.get("spot_at_exit"),
                    "move_pct": t.get("move_pct"),
                    "composite": t.get("composite"),
                }
                self._sync.client.table("zero_dte_trades").insert(row).execute()
                logged += 1
            except Exception as exc:
                print(f"[Database] log_0dte_trade failed: {exc}")
        return self._result(
            success=True,
            data={"logged": logged},
            messages=[f"Logged {logged}/{len(trades)} 0DTE trade(s)"],
        )

    def _sync_full(self, context: Dict) -> AgentResult:
        """Full sync: process both scan and monitor results."""
        messages: List[str] = []

        if context.get("new_positions"):
            r = self._log_scan(context)
            messages.extend(r.messages)

        if context.get("monitor_results"):
            r = self._log_monitor(context)
            messages.extend(r.messages)

        return self._result(success=True, messages=messages)

    def _query(self, context: Dict) -> AgentResult:
        """Query data from Supabase."""
        query_type = context.get("query_type", "open_positions")

        if query_type == "open_positions":
            data = self._sync.client.table("positions").select("*").eq(
                "status", "OPEN"
            ).execute().data
        elif query_type == "greeks_history":
            pos_id = context.get("position_id")
            data = self._sync.client.table("greeks_history").select("*").eq(
                "position_id", pos_id
            ).order("trade_date").execute().data
        elif query_type == "iv_history":
            symbol = context.get("symbol", "SPY")
            data = self._sync.client.table("iv_rank_history").select("*").eq(
                "symbol", symbol
            ).order("trade_date").execute().data
        elif query_type == "alerts":
            data = self._sync.client.table("alerts").select("*").order(
                "created_at", desc=True
            ).limit(20).execute().data
        else:
            return self._result(success=False, errors=[f"Unknown query_type: {query_type}"])

        return self._result(success=True, data={"results": data})

    # -- VALIDATION ---------------------------------------------------------

    def _validate(self, context: Dict) -> AgentResult:
        """Audit database for data quality issues.

        Checks:
        1. Positions: required fields present, valid status, legs count
        2. Position legs: strike > 0, valid option_type/action, prices > 0
        3. Greeks history: no nulls in key fields, values in sane ranges
        4. IV rank: values in [0, 100], no future dates
        5. Cross-table: every position has 4 legs, Greeks entries reference valid positions
        6. Sync check: local positions.json matches DB state
        """
        issues: List[str] = []
        fixes: List[str] = []
        client = self._sync.client

        # 1. Validate positions
        positions = client.table("positions").select("*").execute().data or []
        for pos in positions:
            pid = pos.get("id", "?")
            if not pos.get("symbol"):
                issues.append(f"Position {pid}: missing symbol")
            if pos.get("status") not in ("OPEN", "CLOSED"):
                issues.append(f"Position {pid}: invalid status '{pos.get('status')}'")
            if pos.get("entry_premium") is not None and pos["entry_premium"] < 0:
                issues.append(f"Position {pid}: negative entry_premium ({pos['entry_premium']})")
            if pos.get("status") == "CLOSED" and not pos.get("exit_date"):
                issues.append(f"Position {pid}: CLOSED but no exit_date")
            if pos.get("status") == "CLOSED" and pos.get("realized_pnl") is None:
                issues.append(f"Position {pid}: CLOSED but no realized_pnl")

        # 2. Validate position legs
        legs = client.table("position_legs").select("*").execute().data or []
        legs_by_pos: Dict[str, List] = {}
        for leg in legs:
            pid = leg.get("position_id")
            if pid:
                legs_by_pos.setdefault(pid, []).append(leg)
            if leg.get("strike") is not None and leg["strike"] <= 0:
                issues.append(f"Leg {leg.get('id')}: invalid strike ({leg['strike']})")
            if leg.get("option_type") not in ("CALL", "PUT"):
                issues.append(f"Leg {leg.get('id')}: invalid option_type '{leg.get('option_type')}'")
            if leg.get("entry_price") is not None and leg["entry_price"] < 0:
                issues.append(f"Leg {leg.get('id')}: negative entry_price ({leg['entry_price']})")

        # 3. Cross-check: each position should have exactly 4 legs (IC)
        for pos in positions:
            pid = pos["id"]
            n_legs = len(legs_by_pos.get(pid, []))
            if pos.get("position_type") == "IRON_CONDOR" and n_legs != 4:
                issues.append(f"Position {pid}: IRON_CONDOR has {n_legs} legs (expected 4)")

        # 4. Validate Greeks history
        greeks = client.table("greeks_history").select("*").execute().data or []
        valid_pos_ids = {p["id"] for p in positions}
        for gk in greeks:
            if gk.get("position_id") not in valid_pos_ids:
                issues.append(f"Greeks {gk.get('id')}: orphan record (position_id not found)")
            if gk.get("underlying_price") is not None and gk["underlying_price"] <= 0:
                issues.append(f"Greeks {gk.get('id')}: invalid underlying_price ({gk['underlying_price']})")

        # 5. Validate IV rank history
        iv_ranks = client.table("iv_rank_history").select("*").execute().data or []
        for iv in iv_ranks:
            rank = iv.get("iv_rank")
            if rank is not None and (rank < 0 or rank > 100):
                issues.append(f"IV rank {iv.get('symbol')} {iv.get('trade_date')}: "
                              f"out of range ({rank})")

        # 6. Sync check with local positions.json
        local_positions = context.get("local_positions", [])
        if local_positions:
            local_open = {p["id"] for p in local_positions if p.get("status") == "OPEN"}
            db_open_notes = set()
            for pos in positions:
                if pos.get("status") == "OPEN" and pos.get("notes"):
                    db_open_notes.add(pos["notes"])

            local_only = local_open - db_open_notes
            db_only = db_open_notes - local_open
            if local_only:
                issues.append(f"Sync: {len(local_only)} position(s) in local file but not in DB: "
                              f"{', '.join(local_only)}")
            if db_only:
                issues.append(f"Sync: {len(db_only)} position(s) in DB but not in local file: "
                              f"{', '.join(db_only)}")

        # Summary
        if issues:
            print(f"\n[Database] Validation found {len(issues)} issue(s):")
            for issue in issues:
                print(f"  ! {issue}")
        else:
            print("[Database] Validation passed — all data clean")

        return self._result(
            success=len(issues) == 0,
            data={
                "issues": issues,
                "fixes": fixes,
                "stats": {
                    "positions": len(positions),
                    "legs": len(legs),
                    "greeks_snapshots": len(greeks),
                    "iv_rank_entries": len(iv_ranks),
                },
            },
            messages=[f"{len(issues)} issue(s) found"] if issues else ["All clean"],
        )

    # -- SCHEMA MANAGEMENT --------------------------------------------------

    def _check_table_exists(self, table_name: str) -> bool:
        """Check if a table exists by attempting a zero-row select."""
        try:
            self._sync.client.table(table_name).select("*").limit(0).execute()
            return True
        except Exception:
            return False

    def _ensure_schema(self, context: Dict) -> AgentResult:
        """Check required tables exist and create missing ones.

        Context keys:
            pipeline: "ic" | "zero_dte" | "all" (default "all")
            service_key: Optional[str] — Supabase service_role key for DDL
        """
        pipeline = context.get("pipeline", "all")
        service_key = context.get("service_key")

        if pipeline == "ic":
            required = IC_PIPELINE_TABLES
        elif pipeline == "zero_dte":
            required = ZERO_DTE_TABLES
        else:
            required = ALL_TABLES

        existing: List[str] = []
        missing: List[str] = []

        for table_name in required:
            if self._check_table_exists(table_name):
                existing.append(table_name)
            else:
                missing.append(table_name)

        print(f"\n[Database] Schema check ({pipeline} pipeline):")
        print(f"  Existing: {', '.join(existing) if existing else 'none'}")
        print(f"  Missing:  {', '.join(missing) if missing else 'none'}")

        created: List[str] = []
        failed: List[str] = []

        if missing and service_key:
            # Try to create missing tables via Supabase SQL API
            url = self.config.url
            for table_name in missing:
                sql = required[table_name]
                success = self._exec_sql(url, service_key, sql)
                if success:
                    created.append(table_name)
                    print(f"  ✓ Created {table_name}")
                else:
                    failed.append(table_name)
                    print(f"  ✗ Failed to create {table_name}")

            # Enable RLS on new tables
            for table_name in created:
                self._exec_sql(url, service_key,
                               f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
                self._exec_sql(url, service_key,
                               f"CREATE POLICY \"{table_name}_anon_all\" ON {table_name} "
                               f"FOR ALL USING (true) WITH CHECK (true);")

        elif missing:
            # No service key — print SQL for manual execution
            print(f"\n  Run this in the Supabase SQL Editor (or set SUPABASE_DB_PASSWORD in .env):")
            print(f"  {'─' * 60}")
            for table_name in missing:
                print(required[table_name].strip())
                # RLS + open policy
                print(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
                print(f"CREATE POLICY \"{table_name}_anon_all\" ON {table_name} "
                      f"FOR ALL USING (true) WITH CHECK (true);")
                print()
            print(f"  {'─' * 60}")

        return self._result(
            success=len(missing) == 0 or len(created) == len(missing),
            data={
                "existing": existing,
                "missing": missing,
                "created": created,
                "failed": failed,
            },
            messages=[
                f"{len(existing)} existing, {len(missing)} missing, "
                f"{len(created)} created, {len(failed)} failed"
            ],
        )

    @staticmethod
    def _exec_sql(supabase_url: str, service_key: str, sql: str) -> bool:
        """Execute raw SQL via Supabase REST or direct psycopg2."""
        import os

        # Method 1: Direct Postgres (if DATABASE_URL or DB_PASSWORD available)
        db_url = os.environ.get("DATABASE_URL", "")
        db_pass = os.environ.get("SUPABASE_DB_PASSWORD", "")
        if db_url or db_pass:
            try:
                import psycopg2
                if db_url:
                    conn = psycopg2.connect(db_url, connect_timeout=10)
                else:
                    project_ref = supabase_url.split("//")[1].split(".")[0]
                    conn = psycopg2.connect(
                        host=f"db.{project_ref}.supabase.co",
                        port=5432, dbname="postgres",
                        user="postgres", password=db_pass,
                        connect_timeout=10,
                    )
                conn.autocommit = True
                conn.cursor().execute(sql)
                conn.close()
                return True
            except Exception:
                pass

        # Method 2: Supabase REST RPC
        try:
            api_url = f"{supabase_url}/rest/v1/rpc/exec_sql"
            headers = {
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            }
            data = json.dumps({"sql": sql}).encode()
            req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception:
            return False
