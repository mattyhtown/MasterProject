"""
Inbound webhook router and HMAC-SHA256 verification.

Processes incoming webhook events from GitHub, TradingView/external signal
providers, Supabase database triggers, and ops health triggers.

Safety invariant: inbound webhooks NEVER place live orders or bypass RiskAgent.
Signal webhooks only record observations to StateManager / Historian.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from ..config import WebhookCfg
from ..data.state import StateManager
from ..governance.edge_ledger import HistorianEvent, append_historian_event
from .outbound import WebhookClient


@dataclass(frozen=True)
class WebhookResponse:
    """HTTP response returned by WebhookRouter."""
    status: int
    body: Dict[str, Any]


def verify_signature(body: bytes, header_value: str, secret: str) -> bool:
    """Constant-time verification of ``sha256=<hex>`` HMAC-SHA256 signatures."""
    if not secret or not header_value:
        return False
    sig = header_value.strip()
    if sig.startswith("sha256="):
        sig = sig[len("sha256="):]
    else:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.lower(), expected.lower())


def _get_header(headers: Mapping[str, str], name: str) -> str:
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return ""


class WebhookRouter:
    """Framework-agnostic inbound webhook dispatcher."""

    def __init__(
        self,
        config: Optional[WebhookCfg] = None,
        state: Optional[StateManager] = None,
        outbound: Optional[WebhookClient] = None,
        historian_root: Optional[str] = None,
        custom_handlers: Optional[Dict[str, Callable[[str, Dict[str, Any], Mapping[str, str]], WebhookResponse]]] = None,
    ):
        self.config = config or WebhookCfg()
        self.state = state
        self.outbound = outbound
        self.historian_root = historian_root
        self.custom_handlers = dict(custom_handlers or {})

    def handle_get(self, path: str) -> WebhookResponse:
        """Handle GET requests (health check endpoint)."""
        clean_path = path.split("?", 1)[0].rstrip("/") or "/"
        if clean_path in ("/health", "/healthz"):
            return WebhookResponse(
                status=200,
                body={
                    "status": "ok",
                    "service": "apex-sharpe-webhooks",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        return WebhookResponse(status=404, body={"error": f"Unknown route: {clean_path}"})

    def handle_post(
        self,
        path: str,
        body: bytes,
        headers: Optional[Mapping[str, str]] = None,
    ) -> WebhookResponse:
        """Validate size, verify signature, parse JSON, and dispatch."""
        hdrs = headers or {}
        clean_path = path.split("?", 1)[0].rstrip("/") or "/"

        if len(body) > self.config.max_body_bytes:
            return WebhookResponse(
                status=413,
                body={"error": f"Payload exceeds {self.config.max_body_bytes} bytes"},
            )

        route = self._resolve_route(clean_path)
        if route is None:
            return WebhookResponse(status=404, body={"error": f"Unknown route: {clean_path}"})

        # Verify HMAC signature whenever a secret is configured for this route
        auth_err = self._check_auth(route, body, hdrs)
        if auth_err is not None:
            return auth_err

        # Parse JSON strictly
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return WebhookResponse(status=400, body={"error": f"Invalid JSON payload: {exc}"})

        if not isinstance(payload, dict):
            return WebhookResponse(status=400, body={"error": "Webhook payload must be a JSON object"})

        if route in self.custom_handlers:
            return self.custom_handlers[route](route, payload, hdrs)

        if route == "github":
            return self._handle_github(payload, hdrs)
        if route in ("tradingview", "signal", "generic"):
            return self._handle_signal(route, payload, hdrs)
        if route == "supabase":
            return self._handle_supabase(payload)
        if route == "ops":
            return self._handle_ops(payload)

        return WebhookResponse(status=404, body={"error": f"Unsupported webhook route: {route}"})

    @staticmethod
    def _resolve_route(clean_path: str) -> Optional[str]:
        if clean_path == "/webhook":
            return "generic"
        if clean_path.startswith("/webhooks/"):
            sub = clean_path[len("/webhooks/"):]
            if sub and "/" not in sub:
                return sub
        return None

    def _check_auth(
        self,
        route: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> Optional[WebhookResponse]:
        if route == "github":
            secret = (self.config.github_secret or self.config.inbound_secret).strip()
            if not secret:
                return None
            sig = _get_header(headers, "X-Hub-Signature-256")
            if not verify_signature(body, sig, secret):
                return WebhookResponse(status=401, body={"error": "Invalid GitHub webhook signature"})
            return None

        secret = self.config.inbound_secret.strip()
        if not secret:
            return None
        sig = (
            _get_header(headers, "X-Apex-Signature-256")
            or _get_header(headers, "X-Webhook-Signature")
            or _get_header(headers, "X-Hub-Signature-256")
        )
        if not verify_signature(body, sig, secret):
            return WebhookResponse(status=401, body={"error": "Invalid webhook signature"})
        return None

    def _handle_github(self, payload: Dict[str, Any], headers: Mapping[str, str]) -> WebhookResponse:
        event = (_get_header(headers, "X-GitHub-Event") or payload.get("event") or "unknown").lower()
        repo = (payload.get("repository") or {}).get("full_name", "unknown")

        if event == "ping":
            return WebhookResponse(
                status=200,
                body={"status": "ok", "event": "ping", "repository": repo, "zen": payload.get("zen", "")},
            )

        summary = self._summarize_github_event(event, payload, repo)
        if self.outbound and self.outbound.enabled:
            severity = summary.pop("severity", "INFO")
            self.outbound.send(
                event=f"github.{event}",
                title=summary["title"],
                message=summary["message"],
                subtitle=repo,
                severity=severity,
                data=summary.get("data"),
            )

        return WebhookResponse(
            status=202,
            body={"status": "accepted", "source": "github", "event": event, "repository": repo},
        )

    @staticmethod
    def _summarize_github_event(event: str, payload: Dict[str, Any], repo: str) -> Dict[str, Any]:
        if event == "workflow_run":
            wf = payload.get("workflow_run") or {}
            name = wf.get("name", "Workflow")
            conclusion = wf.get("conclusion") or wf.get("status") or "unknown"
            branch = wf.get("head_branch", "")
            severity = "FAIL" if conclusion in ("failure", "timed_out") else "SUCCESS" if conclusion == "success" else "INFO"
            return {
                "title": f"CI/CD {name}: {conclusion}",
                "message": f"Branch {branch} finished with status '{conclusion}'.",
                "severity": severity,
                "data": {"workflow": name, "branch": branch, "conclusion": conclusion, "url": wf.get("html_url", "")},
            }
        if event == "pull_request":
            pr = payload.get("pull_request") or {}
            action = payload.get("action", "updated")
            num = pr.get("number", "?")
            return {
                "title": f"PR #{num} {action}",
                "message": pr.get("title", ""),
                "severity": "INFO",
                "data": {"pr": num, "action": action, "url": pr.get("html_url", "")},
            }
        if event == "push":
            ref = payload.get("ref", "")
            commits = len(payload.get("commits") or [])
            return {
                "title": f"Push to {ref}",
                "message": f"{commits} commit(s) pushed to {repo}.",
                "severity": "INFO",
                "data": {"ref": ref, "commits": commits},
            }
        return {
            "title": f"GitHub event: {event}",
            "message": f"Received {event} for {repo}.",
            "severity": "INFO",
            "data": {"action": payload.get("action", "")},
        }

    def _handle_signal(
        self,
        route: str,
        payload: Dict[str, Any],
        headers: Mapping[str, str],
    ) -> WebhookResponse:
        event_name = (
            _get_header(headers, "X-Apex-Event")
            or str(payload.get("event") or "signal")
        )
        ticker = str(payload.get("ticker") or payload.get("symbol") or "").upper().strip()
        signal = str(payload.get("signal") or payload.get("action") or payload.get("composite") or "").strip()
        if not ticker and not signal:
            return WebhookResponse(
                status=400,
                body={"error": "Signal webhook requires 'ticker'/'symbol' or 'signal'/'action'"},
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        record = {
            "timestamp": now_iso,
            "source": f"webhook:{route}",
            "event": event_name,
            "ticker": ticker,
            "signal": signal,
            "price": payload.get("price"),
            "message": payload.get("message", ""),
        }

        if self.state is not None:
            self.state.append_signal(record)

        historian_path = None
        if self.historian_root:
            try:
                hist_event = HistorianEvent(
                    event_type=f"webhook_{event_name}",
                    observed_at=str(payload.get("observed_at") or now_iso),
                    recorded_at=now_iso,
                    source=f"webhook:{route}",
                    payload=record,
                    edge_id=payload.get("edge_id"),
                    derived=True,
                )
                historian_path = str(append_historian_event(hist_event, root=self.historian_root))
            except ValueError as exc:
                return WebhookResponse(status=400, body={"error": f"Invalid historian payload: {exc}"})

        if self.outbound and self.outbound.enabled:
            self.outbound.send(
                event=f"inbound.{event_name}",
                title=f"Webhook Signal: {ticker or 'MARKET'} {signal}".strip(),
                message=str(payload.get("message") or f"Received {signal} from {route}"),
                subtitle=f"source={route}",
                severity=str(payload.get("severity") or "INFO"),
                data={"ticker": ticker, "signal": signal, "price": payload.get("price")},
            )

        body: Dict[str, Any] = {"status": "accepted", "recorded": record}
        if historian_path:
            body["historian_file"] = historian_path
        return WebhookResponse(status=202, body=body)

    def _handle_supabase(self, payload: Dict[str, Any]) -> WebhookResponse:
        table = str(payload.get("table") or "unknown")
        op = str(payload.get("type") or payload.get("event") or "UPDATE").upper()
        record = payload.get("record") or {}

        if self.outbound and self.outbound.enabled:
            severity = "ACTION" if table == "alerts" else "INFO"
            msg = str(record.get("message") or f"{op} on {table}")
            self.outbound.send(
                event=f"supabase.{table}.{op.lower()}",
                title=f"Supabase {table}: {op}",
                message=msg,
                severity=severity,
                data={"table": table, "op": op, "id": record.get("id", "")},
            )

        return WebhookResponse(
            status=202,
            body={"status": "accepted", "source": "supabase", "table": table, "type": op},
        )

    def _handle_ops(self, payload: Dict[str, Any]) -> WebhookResponse:
        action = str(payload.get("action") or "health_check").lower()
        if action == "health_check":
            from ..agents.ops import InfraAgent
            res = InfraAgent().run({"action": "health_check"})
            return WebhookResponse(status=200, body={"status": "ok", "action": action, "result": res.data})
        if action == "security_audit":
            from ..agents.ops import SecurityAgent
            res = SecurityAgent().run({"action": "audit_config"})
            summary = {
                "status": res.data.get("status"),
                "severity_counts": res.data.get("severity_counts"),
                "passed_count": len(res.data.get("passed", [])),
                "finding_count": len(res.data.get("findings", [])),
            }
            return WebhookResponse(status=200, body={"status": "ok", "action": action, "result": summary})
        return WebhookResponse(status=400, body={"error": f"Unsupported ops action: {action}"})
