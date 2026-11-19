"""
Inbound HTTP webhook receiver for APEX-SHARPE (stdlib http.server).

Receives authenticated JSON webhooks for external signals (e.g. TradingView),
Supabase database events, and operational alerts.

Safety: inbound webhooks NEVER place live orders or bypass RiskAgent.
"""

from __future__ import annotations

import hmac
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from ..config import WebhookCfg
from ..data.state import StateManager
from .dispatcher import WebhookDispatcher, WebhookEvent, verify_signature


MAX_BODY_BYTES = 1024 * 1024  # 1 MiB


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _authenticate_request(
    headers: Any,
    body: bytes,
    secret: str,
) -> Tuple[bool, str]:
    """Fail-closed authentication using HMAC-SHA256 or bearer auth."""
    if not secret:
        return False, "Inbound webhook secret is not configured"

    sig = (
        headers.get("X-Apex-Signature-256")
        or headers.get("X-Hub-Signature-256")
        or ""
    ).strip()
    if sig:
        if verify_signature(body, sig, secret):
            return True, ""
        return False, "Invalid webhook signature"

    auth = (headers.get("Authorization") or "").strip()
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):].strip()
        if token and hmac.compare_digest(token, secret):
            return True, ""
        return False, "Invalid bearer token"

    return False, "Missing webhook authentication"


def _format_supabase_event(payload: Dict[str, Any]) -> WebhookEvent:
    """Convert a Supabase database webhook payload into a WebhookEvent."""
    op = str(payload.get("type") or "EVENT").upper()
    table = str(payload.get("table") or "unknown")
    record = payload.get("record")
    if not isinstance(record, dict):
        record = {}
    old_record = payload.get("old_record")
    if not isinstance(old_record, dict):
        old_record = {}

    ticker = str(record.get("symbol") or record.get("ticker") or "")
    severity = "INFO"
    subtitle = f"{table} {op}"

    if table == "alerts":
        severity = str(record.get("severity") or "WARNING")
        message = str(record.get("message") or f"Alert recorded in {table}")
        title = f"Supabase Alert ({record.get('alert_type', op)})"
    elif table == "positions":
        status = str(record.get("status") or "")
        pos_type = str(record.get("position_type") or "POSITION")
        if op == "UPDATE" and status == "CLOSED" and old_record.get("status") != "CLOSED":
            severity = "ACTION"
            pnl = record.get("realized_pnl")
            pnl_str = f" | P&L {pnl}" if pnl is not None else ""
            title = f"Position Closed: {ticker or pos_type}"
            message = f"{pos_type} closed ({record.get('exit_reason', 'UNKNOWN')}){pnl_str}"
        else:
            title = f"Position {op}: {ticker or pos_type}"
            message = f"{pos_type} status={status or 'UNKNOWN'}"
    elif table == "zero_dte_signals":
        composite = record.get("composite") or "NONE"
        severity = "ACTION" if record.get("composite") else "INFO"
        title = f"0DTE Signal: {ticker or 'UNKNOWN'}"
        message = f"Composite={composite} core_count={record.get('core_count', 0)}"
    else:
        title = f"Supabase {table} {op}"
        message = f"Database {op} on {table}"

    return WebhookEvent(
        event_type=f"supabase_{table}_{op.lower()}",
        title=title,
        subtitle=subtitle,
        message=message,
        severity=severity,
        source="supabase_webhook",
        ticker=ticker,
        data={"table": table, "type": op, "record": record},
    )


class WebhookServer:
    """Threaded HTTP webhook receiver wrapping ThreadingHTTPServer."""

    def __init__(
        self,
        config: Optional[WebhookCfg] = None,
        state: Optional[StateManager] = None,
        dispatcher: Optional[WebhookDispatcher] = None,
        quiet: bool = False,
    ):
        self.config = config or WebhookCfg()
        self.state = state
        self.dispatcher = dispatcher or WebhookDispatcher(self.config)
        self.quiet = quiet
        self._lock = threading.Lock()
        self._httpd = self._build_httpd()
        self._thread: Optional[threading.Thread] = None

    @property
    def server_address(self) -> Tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def _build_httpd(self) -> ThreadingHTTPServer:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                if not parent.quiet:
                    super().log_message(format, *args)

            def _send_json(self, status: int, body: Dict[str, Any]) -> None:
                raw = json.dumps(body, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _read_json_body(self) -> Tuple[Optional[Dict[str, Any]], Optional[bytes], Optional[Tuple[int, str]]]:
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    return None, None, (415, "Content-Type must be application/json")

                raw_len = self.headers.get("Content-Length")
                if raw_len is None:
                    return None, None, (400, "Missing Content-Length header")
                try:
                    length = int(raw_len)
                except ValueError:
                    return None, None, (400, "Invalid Content-Length header")
                if length < 0 or length > MAX_BODY_BYTES:
                    return None, None, (413, "Payload exceeds maximum allowed size")

                body = self.rfile.read(length)
                try:
                    parsed = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None, body, (400, "Invalid JSON payload")

                if not isinstance(parsed, dict):
                    return None, body, (400, "JSON payload must be an object")

                return parsed, body, None

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/health":
                    self._send_json(200, {
                        "status": "ok",
                        "service": "apex-sharpe-webhooks",
                        "outbound_enabled": parent.dispatcher.enabled,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    return
                self._send_json(404, {"error": "Not found"})

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path not in (
                    "/webhooks/signal",
                    "/webhooks/supabase",
                    "/webhooks/alert",
                    "/webhooks/notify",
                ):
                    self._send_json(404, {"error": "Not found"})
                    return

                payload, raw_body, err = self._read_json_body()
                if err is not None:
                    self._send_json(err[0], {"error": err[1]})
                    return

                assert payload is not None and raw_body is not None
                ok, reason = _authenticate_request(
                    self.headers, raw_body, parent.config.inbound_secret
                )
                if not ok:
                    self._send_json(401, {"error": reason})
                    return

                if path == "/webhooks/signal":
                    self._handle_signal(payload)
                elif path == "/webhooks/supabase":
                    self._handle_supabase(payload)
                else:
                    self._handle_alert(payload)

            def _handle_signal(self, payload: Dict[str, Any]) -> None:
                ticker = str(payload.get("ticker") or payload.get("symbol") or "").strip()
                signal_name = str(payload.get("signal") or payload.get("event") or "").strip()
                message = str(payload.get("message") or "").strip()
                if not (ticker or signal_name or message):
                    self._send_json(
                        400,
                        {"error": "Signal payload requires 'ticker', 'symbol', 'signal', or 'message'"},
                    )
                    return

                entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "webhook",
                    "paper_only": True,
                    "ticker": ticker,
                    "signal": signal_name,
                    "direction": payload.get("direction", ""),
                    "price": payload.get("price"),
                    "message": message,
                    "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                }

                recorded = False
                if parent.state is not None:
                    with parent._lock:
                        parent.state.append_signal(entry)
                    recorded = True

                dispatched = 0
                if parent.dispatcher.enabled:
                    ev = WebhookEvent(
                        event_type="inbound_signal",
                        title=f"Webhook Signal: {ticker or signal_name or 'EXTERNAL'}",
                        subtitle=signal_name or str(entry["direction"]),
                        message=message or f"Received signal for {ticker}",
                        severity=str(payload.get("severity") or "INFO"),
                        source="webhook_receiver",
                        ticker=ticker,
                        data=entry,
                    )
                    dispatched = sum(1 for r in parent.dispatcher.dispatch(ev) if r.success)

                self._send_json(202, {
                    "accepted": True,
                    "recorded": recorded,
                    "dispatched": dispatched,
                })

            def _handle_supabase(self, payload: Dict[str, Any]) -> None:
                if not payload.get("table"):
                    self._send_json(400, {"error": "Supabase payload requires 'table'"})
                    return
                ev = _format_supabase_event(payload)
                results = parent.dispatcher.dispatch(ev)
                self._send_json(200, {
                    "accepted": True,
                    "event_type": ev.event_type,
                    "dispatched": sum(1 for r in results if r.success),
                })

            def _handle_alert(self, payload: Dict[str, Any]) -> None:
                title = str(payload.get("title") or "").strip()
                message = str(payload.get("message") or "").strip()
                if not (title or message):
                    self._send_json(400, {"error": "Alert payload requires 'title' or 'message'"})
                    return
                ev = WebhookEvent(
                    event_type=str(payload.get("event_type") or "alert"),
                    title=title or "APEX-SHARPE Alert",
                    subtitle=str(payload.get("subtitle") or ""),
                    message=message or title,
                    severity=str(payload.get("severity") or "INFO"),
                    source=str(payload.get("source") or "webhook_receiver"),
                    ticker=str(payload.get("ticker") or ""),
                    data=payload.get("data") if isinstance(payload.get("data"), dict) else {},
                )
                results = parent.dispatcher.dispatch(ev)
                self._send_json(200, {
                    "accepted": True,
                    "event_type": ev.event_type,
                    "dispatched": sum(1 for r in results if r.success),
                })

        return _ReusableThreadingHTTPServer(
            (self.config.listen_host, self.config.listen_port),
            Handler,
        )

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "WebhookServer":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()


def create_webhook_server(
    config: Optional[WebhookCfg] = None,
    state: Optional[StateManager] = None,
    dispatcher: Optional[WebhookDispatcher] = None,
    quiet: bool = False,
) -> WebhookServer:
    """Factory to construct a WebhookServer."""
    return WebhookServer(
        config=config,
        state=state,
        dispatcher=dispatcher,
        quiet=quiet,
    )
