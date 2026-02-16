"""
Iron Condor Pipeline — scan -> risk -> execute -> monitor -> report.

Orchestrates the IC agents with dependency injection.
"""

from datetime import datetime
from typing import List, Dict

from ..config import AppConfig
from ..data.orats_client import ORATSClient
from ..data.state import StateManager
from ..agents.scanner import ScannerAgent
from ..agents.risk import RiskAgent
from ..agents.executor import ExecutorAgent
from ..agents.monitor import MonitorAgent, calculate_dte, estimate_from_chain, estimate_from_model, generate_alerts
from ..agents.reporter import ReporterAgent, send_notification
from ..agents.database import DatabaseAgent
from ..types import C


class ICPipeline:
    """Orchestrate Scanner -> Risk -> Executor -> Monitor -> Reporter -> Database."""

    def __init__(self, config: AppConfig, orats: ORATSClient, state: StateManager,
                 ib_client=None):
        self.config = config
        self.orats = orats
        self.state = state
        self.ib_client = ib_client

        self.scanner = ScannerAgent(config.scanner)
        self.risk = RiskAgent(config.risk)

        # Use IB executor when enabled and connected
        if config.ib.enabled and ib_client is not None:
            from ..agents.ib_executor import IBExecutorAgent
            self.executor = IBExecutorAgent(ib_client, config.executor, config.monitor)
            self._ib_mode = True
        else:
            self.executor = ExecutorAgent(config.executor, config.monitor)
            self._ib_mode = False

        self.monitor_agent = MonitorAgent(config.monitor)
        self.reporter = ReporterAgent()
        self.db = DatabaseAgent(config.supabase)

    # -- SCAN pipeline ----------------------------------------------------

    def run_scan(self) -> None:
        print("=" * 80)
        mode_tag = " [IB LIVE]" if self._ib_mode else ""
        print(f"APEX-SHARPE AGENT PIPELINE — SCAN MODE{mode_tag}")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)

        positions = self.state.load_positions()

        # 1. Scan
        scan_result = self.scanner.run({"orats": self.orats})
        candidates = scan_result.data.get("candidates", [])

        if not candidates:
            self.reporter.report_scan([], [], [])
            return

        # 2. Risk check
        risk_result = self.risk.run({"candidates": candidates, "positions": positions})
        decisions = risk_result.data.get("decisions", [])

        # 3. Execute opens
        exec_result = self.executor.run({"decisions": decisions})
        new_positions = exec_result.data.get("new_positions", [])

        # 4. Persist
        if new_positions:
            positions.extend(new_positions)
            self.state.save_positions(positions)
            print(f"\n[Pipeline] Positions saved to {self.state.positions_path}")

        # 5. Database sync (positions + IV rank)
        self.db.run({
            "action": "log_scan",
            "new_positions": new_positions,
            "candidates": candidates,
        })

        # 6. Report
        self.reporter.report_scan(candidates, decisions, new_positions)

    # -- MONITOR pipeline -------------------------------------------------

    def run_monitor(self) -> None:
        print("=" * 80)
        mode_tag = " [IB LIVE]" if self._ib_mode else ""
        print(f"APEX-SHARPE AGENT PIPELINE — MONITOR MODE{mode_tag}")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)

        positions = self.state.load_positions()
        open_positions = [p for p in positions if p.get("status") == "OPEN"]

        if not open_positions:
            print("\nNo open positions to monitor.")
            send_notification(
                title="APEX-SHARPE: Monitor",
                subtitle="No open positions",
                message="Nothing to check",
                sound=False,
            )
            return

        print(f"\nMonitoring {len(open_positions)} open position(s)...")

        monitor_results: List[Dict] = []
        closed_positions: List[Dict] = []

        for pos in open_positions:
            symbol = pos["symbol"]
            expiry = pos["expiration"]
            dte = calculate_dte(expiry)

            if dte < 0:
                print(f"\n  {pos['id']}: EXPIRED — closing")
                updated = self.executor.run_close(pos, "EXPIRED", 0.0)
                for i, p in enumerate(positions):
                    if p["id"] == pos["id"]:
                        positions[i] = updated
                        break
                closed_positions.append(updated)
                continue

            # Fetch live data
            print(f"\n  Fetching data for {symbol} exp {expiry}...")
            summ = self.orats.summaries(symbol)
            current_price = None
            if summ and summ.get("data"):
                current_price = summ["data"][0].get("stockPrice")

            if current_price is None:
                print("  [WARN] No live price — using entry price")
                current_price = pos["entry_stock_price"]

            chain = self.orats.chain(symbol, expiry)

            # Filter chain to target expiry
            if chain and chain.get("data"):
                filtered = [s for s in chain["data"] if s.get("expirDate") == expiry]
                if filtered:
                    chain = dict(chain, data=filtered)

            # Run monitor agent
            mon_result = self.monitor_agent.run({
                "position": pos,
                "current_price": current_price,
                "chain": chain,
            })

            valuation = mon_result.data["valuation"]
            alerts = mon_result.data["alerts"]

            monitor_results.append({
                "position": pos,
                "current_price": current_price,
                "valuation": valuation,
                "alerts": alerts,
            })

            # Auto-close on ACTION alerts
            action_alerts = [a for a in alerts if a["level"] == "ACTION"]
            if action_alerts:
                reason = action_alerts[0]["message"]
                updated = self.executor.run_close(pos, reason, valuation["pnl"])
                for i, p in enumerate(positions):
                    if p["id"] == pos["id"]:
                        positions[i] = updated
                        break
                closed_positions.append(updated)

        # Save + report
        self.state.save_positions(positions)
        print(f"\n[Pipeline] Positions saved to {self.state.positions_path}")

        # Database sync (Greeks snapshots + alerts + closed positions)
        self.db.run({
            "action": "log_monitor",
            "monitor_results": monitor_results,
        })
        for cp in closed_positions:
            self.db.run({"action": "log_close", "position": cp})

        self.reporter.report_monitor(monitor_results, closed_positions)

    # -- FULL pipeline ----------------------------------------------------

    def run_full(self) -> None:
        self.run_scan()
        print("\n\n")
        self.run_monitor()
