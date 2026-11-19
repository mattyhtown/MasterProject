"""
ReporterAgent — terminal output, macOS notifications, and outbound webhooks.

Extracted from trading_pipeline.py.
"""

import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from .monitor import calculate_dte
from ..config import WebhookCfg, load_webhook_config
from ..types import AgentResult, C
from ..webhooks import WebhookDeliveryResult, WebhookDispatcher, WebhookEvent


def send_notification(
    title: str,
    message: str,
    subtitle: str = "",
    sound: bool = True,
    *,
    severity: str = "INFO",
    event_type: str = "notification",
    ticker: str = "",
    source: str = "apex_sharpe",
    data: Optional[Dict[str, Any]] = None,
    webhook_config: Optional[WebhookCfg] = None,
) -> List[WebhookDeliveryResult]:
    """Send desktop notification (macOS) and dispatch configured webhooks."""
    if sys.platform == "darwin":
        safe_title = title.replace('"', '\\"')
        safe_message = message.replace('"', '\\"')
        safe_subtitle = subtitle.replace('"', '\\"')
        sound_line = 'sound name "Glass"' if sound else ""
        script = (
            f'display notification "{safe_message}" '
            f'with title "{safe_title}" subtitle "{safe_subtitle}" {sound_line}'
        )
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        except Exception as exc:
            print(f"  [WARN] Notification failed: {exc}")

    cfg = webhook_config if webhook_config is not None else load_webhook_config()
    dispatcher = WebhookDispatcher(cfg)
    if not dispatcher.enabled:
        return []

    event = WebhookEvent(
        event_type=event_type,
        title=title,
        subtitle=subtitle,
        message=message,
        severity=severity,
        source=source,
        ticker=ticker,
        data=data or {},
    )
    results = dispatcher.dispatch(event)
    for res in results:
        if not res.success:
            print(f"  [WARN] Webhook ({res.provider} -> {res.target}) failed: {res.error}")
    return results


class ReporterAgent(BaseAgent):
    """Print reports, send macOS notifications, and dispatch outbound webhooks."""

    def __init__(self, webhook_config: Optional[WebhookCfg] = None):
        super().__init__("Reporter", webhook_config)
        self.webhook_config = webhook_config

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Dispatch to appropriate report method based on context."""
        deliveries: List[WebhookDeliveryResult] = []
        if "scan_result" in context:
            deliveries.extend(self.report_scan(
                context.get("candidates", []),
                context.get("risk_decisions", []),
                context.get("new_positions", []),
                webhook_config=self.webhook_config,
            ))
        if "monitor_results" in context:
            deliveries.extend(self.report_monitor(
                context["monitor_results"],
                context.get("closed_positions", []),
                webhook_config=self.webhook_config,
            ))
        if "webhook_event" in context:
            ev_data = context["webhook_event"]
            if isinstance(ev_data, WebhookEvent):
                cfg = self.webhook_config if self.webhook_config is not None else load_webhook_config()
                deliveries.extend(WebhookDispatcher(cfg).dispatch(ev_data))
            elif isinstance(ev_data, dict):
                deliveries.extend(send_notification(
                    title=str(ev_data.get("title", "APEX-SHARPE Alert")),
                    message=str(ev_data.get("message", "")),
                    subtitle=str(ev_data.get("subtitle", "")),
                    severity=str(ev_data.get("severity", "INFO")),
                    event_type=str(ev_data.get("event_type", "alert")),
                    ticker=str(ev_data.get("ticker", "")),
                    source=str(ev_data.get("source", "apex_sharpe")),
                    data=ev_data.get("data") if isinstance(ev_data.get("data"), dict) else None,
                    webhook_config=self.webhook_config,
                ))
        return self._result(
            success=True,
            data={
                "deliveries": [
                    {
                        "target": d.target,
                        "provider": d.provider,
                        "success": d.success,
                        "status_code": d.status_code,
                        "error": d.error,
                    }
                    for d in deliveries
                ]
            },
        )

    @staticmethod
    def report_scan(
        candidates: List[Dict],
        risk_results: List[Dict],
        new_positions: List[Dict],
        webhook_config: Optional[WebhookCfg] = None,
    ) -> List[WebhookDeliveryResult]:
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
            first = new_positions[0]
            return send_notification(
                title="APEX-SHARPE: New Position",
                subtitle=f"{len(new_positions)} trade(s) opened",
                message=f"{first['id']} — credit ${first['entry_credit']:.2f}",
                severity="INFO",
                event_type="trade_opened",
                ticker=str(first.get("symbol", "")),
                data={"positions": [p.get("id") for p in new_positions]},
                webhook_config=webhook_config,
            )
        return []

    @staticmethod
    def report_monitor(
        monitor_results: List[Dict],
        closed_positions: List[Dict],
        webhook_config: Optional[WebhookCfg] = None,
    ) -> List[WebhookDeliveryResult]:
        print(f"\n{'=' * 80}")
        print("APEX-SHARPE MONITOR REPORT")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 80}")

        if not monitor_results:
            print("\nNo open positions to monitor.")
            return []

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
            return send_notification(
                title="APEX-SHARPE: ACTION REQUIRED",
                subtitle=f"{len(action_alerts)} position(s) need attention",
                message=action_alerts[0][:200],
                severity="ACTION",
                event_type="position_action",
                data={
                    "total_pnl": round(total_pnl, 2),
                    "closed_count": len(closed_positions),
                    "actions": action_alerts,
                },
                webhook_config=webhook_config,
            )
        elif warning_alerts:
            return send_notification(
                title="APEX-SHARPE: Warning",
                subtitle=f"{len(warning_alerts)} warning(s)",
                message=warning_alerts[0][:200],
                severity="WARNING",
                event_type="position_warning",
                data={
                    "total_pnl": round(total_pnl, 2),
                    "warnings": warning_alerts,
                },
                webhook_config=webhook_config,
            )
        return []
