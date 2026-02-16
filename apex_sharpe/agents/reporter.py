"""
ReporterAgent — terminal output and macOS notifications.

Extracted from trading_pipeline.py.
"""

import subprocess
from datetime import datetime
from typing import Any, Dict, List

from .base import BaseAgent
from .monitor import calculate_dte
from ..types import AgentResult, C


def send_notification(title: str, message: str, subtitle: str = "", sound: bool = True) -> None:
    title = title.replace('"', '\\"')
    message = message.replace('"', '\\"')
    subtitle = subtitle.replace('"', '\\"')
    sound_line = 'sound name "Glass"' if sound else ""
    script = f'display notification "{message}" with title "{title}" subtitle "{subtitle}" {sound_line}'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except Exception as exc:
        print(f"  [WARN] Notification failed: {exc}")


class ReporterAgent(BaseAgent):
    """Print reports and send macOS notifications."""

    def __init__(self):
        super().__init__("Reporter")

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Dispatch to appropriate report method based on context."""
        if "scan_result" in context:
            self.report_scan(
                context.get("candidates", []),
                context.get("risk_decisions", []),
                context.get("new_positions", []),
            )
        if "monitor_results" in context:
            self.report_monitor(
                context["monitor_results"],
                context.get("closed_positions", []),
            )
        return self._result(success=True)

    @staticmethod
    def report_scan(
        candidates: List[Dict],
        risk_results: List[Dict],
        new_positions: List[Dict],
    ) -> None:
        print(f"\n{'=' * 80}")
        print("APEX-SHARPE SCAN REPORT")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 80}")

        print(f"\nCandidates found: {len(candidates)}")
        for item in risk_results:
            c = item["candidate"]
            print(f"  {c['symbol']} {c['expiration']} — {item['decision']}")
            for r in item["reasons"]:
                print(f"    {r}")

        print(f"\nPositions opened: {len(new_positions)}")
        for p in new_positions:
            print(f"  {p['id']}: credit ${p['entry_credit']:.2f}, "
                  f"max P/L ${p['max_profit']:.0f}/${p['max_loss']:.0f}")

        # Only notify on actual trade opens — suppress noise from
        # blocked candidates and empty scans (these fire every cron run)
        if new_positions:
            send_notification(
                title="APEX-SHARPE: New Position",
                subtitle=f"{len(new_positions)} trade(s) opened",
                message=f"{new_positions[0]['id']} — credit ${new_positions[0]['entry_credit']:.2f}",
            )

    @staticmethod
    def report_monitor(
        monitor_results: List[Dict],
        closed_positions: List[Dict],
    ) -> None:
        print(f"\n{'=' * 80}")
        print("APEX-SHARPE MONITOR REPORT")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 80}")

        if not monitor_results:
            print("\nNo open positions to monitor.")
            return

        total_pnl = 0.0
        action_alerts: List[str] = []
        warning_alerts: List[str] = []

        for res in monitor_results:
            pos = res["position"]
            val = res["valuation"]
            alerts = res["alerts"]
            price = res["current_price"]
            dte = calculate_dte(pos["expiration"])
            total_pnl += val["pnl"]

            status_tag = ""
            if any(a["level"] == "ACTION" for a in alerts):
                status_tag = " ** ACTION **"
            elif any(a["level"] == "WARNING" for a in alerts):
                status_tag = " ! WARNING !"

            print(f"\n{'~' * 80}")
            print(f"{pos['id']}  |  {pos['symbol']}  |  {dte} DTE  |  "
                  f"P&L ${val['pnl']:+.0f} ({val['pnl_pct']:+.1f}%){status_tag}")
            print(f"  Stock: ${price:.2f}  |  "
                  f"BE: ${pos['breakeven_lower']:.0f} - ${pos['breakeven_upper']:.0f}  |  "
                  f"Data: {val['data_source']}")

            if val.get("leg_details"):
                for ld in val["leg_details"]:
                    print(f"    {ld['action']:<4} ${ld['strike']:<6} {ld['type']:<4}  "
                          f"entry ${ld['entry_price']:.2f} -> ${ld['current_mid']:.2f}  "
                          f"delta {ld['current_delta']:+.4f}")

            # Greeks enrichment (from GreeksCalculator if available)
            gk = val.get("greeks")
            if gk:
                daily_theta = gk['portfolio_theta'] / 365
                print(f"  {C.DIM}Greeks (BS):{C.RESET}  "
                      f"Δ {gk['portfolio_delta']:+.2f}  "
                      f"Γ {gk['portfolio_gamma']:+.4f}  "
                      f"Θ {daily_theta:+.2f}/day  "
                      f"V {gk['portfolio_vega']:+.2f}")

            for a in alerts:
                prefix = ">>>" if a["level"] == "ACTION" else "  !"
                print(f"  {prefix} [{a['level']}] {a['message']}")
                if a["level"] == "ACTION":
                    action_alerts.append(f"{pos['id']}: {a['message']}")
                else:
                    warning_alerts.append(f"{pos['id']}: {a['message']}")

        # Summary
        print(f"\n{'=' * 80}")
        print(f"Total P&L: ${total_pnl:+.2f}  |  "
              f"Positions closed: {len(closed_positions)}  |  "
              f"Actions: {len(action_alerts)}  |  Warnings: {len(warning_alerts)}")

        # Only notify on ACTION or WARNING — suppress "All Clear" noise
        if action_alerts:
            send_notification(
                title="APEX-SHARPE: ACTION REQUIRED",
                subtitle=f"{len(action_alerts)} position(s) need attention",
                message=action_alerts[0][:200],
            )
        elif warning_alerts:
            send_notification(
                title="APEX-SHARPE: Warning",
                subtitle=f"{len(warning_alerts)} warning(s)",
                message=warning_alerts[0][:200],
            )
