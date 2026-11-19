"""
Outbound webhook dispatcher (stdlib urllib + hmac).

Supports generic JSON webhooks, Slack incoming webhooks, and Discord webhooks.
All outbound payloads can optionally be signed with HMAC-SHA256 via
``X-Apex-Signature-256: sha256=<hex>``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..config import WebhookCfg


_SEVERITY_COLORS = {
    "INFO": 0x2563EB,      # blue
    "OK": 0x16A34A,        # green
    "SUCCESS": 0x16A34A,   # green
    "WARNING": 0xD97706,   # amber
    "ACTION": 0xDC2626,    # red
    "ERROR": 0xDC2626,     # red
    "FAIL": 0xDC2626,      # red
}

_SLACK_COLORS = {
    "INFO": "#2563eb",
    "OK": "#16a34a",
    "SUCCESS": "#16a34a",
    "WARNING": "#d97706",
    "ACTION": "#dc2626",
    "ERROR": "#dc2626",
    "FAIL": "#dc2626",
}


class WebhookDeliveryError(RuntimeError):
    """Raised when webhook configuration or delivery fails in strict mode."""


@dataclass(frozen=True)
class WebhookResult:
    """Outcome of a single webhook HTTP delivery attempt."""
    url: str
    ok: bool
    status_code: Optional[int] = None
    error: str = ""


def validate_webhook_url(url: str) -> str:
    """Validate that a webhook URL uses http or https and has a network host."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise WebhookDeliveryError("Webhook URL is empty")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise WebhookDeliveryError(
            f"Invalid webhook URL (must be http/https with host): {cleaned}"
        )
    return cleaned


def detect_webhook_format(url: str, configured: str = "auto") -> str:
    """Resolve payload format ('json', 'slack', or 'discord') for a URL."""
    fmt = (configured or "auto").strip().lower()
    if fmt in ("json", "slack", "discord"):
        return fmt
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if host == "hooks.slack.com" or host.endswith(".hooks.slack.com"):
        return "slack"
    if host in ("discord.com", "discordapp.com") and "/api/webhooks/" in path:
        return "discord"
    return "json"


def sign_payload(body: bytes, secret: str) -> str:
    """Compute ``sha256=<hexdigest>`` HMAC signature for a request body."""
    digest = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def format_payload(
    event: str,
    title: str,
    message: str,
    subtitle: str = "",
    severity: str = "INFO",
    data: Optional[Dict[str, Any]] = None,
    fmt: str = "json",
) -> Dict[str, Any]:
    """Build a JSON-serializable webhook body for the target format."""
    sev = (severity or "INFO").upper()
    ts = datetime.now(timezone.utc).isoformat()
    extra = dict(data) if data else {}

    if fmt == "slack":
        fields = [
            {"title": k, "value": str(v), "short": len(str(v)) <= 32}
            for k, v in extra.items()
        ]
        if subtitle:
            fields.insert(0, {"title": "Context", "value": subtitle, "short": True})
        return {
            "text": f"[{sev}] {title}",
            "attachments": [
                {
                    "color": _SLACK_COLORS.get(sev, "#2563eb"),
                    "title": title,
                    "text": message,
                    "fields": fields,
                    "footer": f"APEX-SHARPE ({event})",
                    "ts": int(datetime.now(timezone.utc).timestamp()),
                }
            ],
        }

    if fmt == "discord":
        fields = [
            {"name": str(k), "value": str(v)[:1024], "inline": len(str(v)) <= 32}
            for k, v in extra.items()
        ]
        if subtitle:
            fields.insert(0, {"name": "Context", "value": subtitle[:1024], "inline": True})
        return {
            "content": f"**[{sev}] {title}**",
            "embeds": [
                {
                    "title": title[:256],
                    "description": message[:4096],
                    "color": _SEVERITY_COLORS.get(sev, 0x2563EB),
                    "fields": fields[:25],
                    "footer": {"text": f"APEX-SHARPE • {event}"},
                    "timestamp": ts,
                }
            ],
        }

    return {
        "event": event,
        "timestamp": ts,
        "severity": sev,
        "title": title,
        "subtitle": subtitle,
        "message": message,
        "data": extra,
    }


class WebhookClient:
    """Send outbound webhook notifications using stdlib urllib."""

    def __init__(
        self,
        config: Optional[WebhookCfg] = None,
        urls: Optional[Sequence[str]] = None,
    ):
        cfg = config or WebhookCfg()
        raw_urls = list(urls) if urls is not None else list(cfg.urls)
        self.urls: List[str] = [validate_webhook_url(u) for u in raw_urls if u and u.strip()]
        self.secret = (cfg.secret or "").strip()
        self.timeout = cfg.timeout
        self.format = cfg.format

    @property
    def enabled(self) -> bool:
        return len(self.urls) > 0

    def send(
        self,
        event: str,
        title: str,
        message: str,
        subtitle: str = "",
        severity: str = "INFO",
        data: Optional[Dict[str, Any]] = None,
    ) -> List[WebhookResult]:
        """Deliver an event to every configured webhook URL.

        Network/HTTP errors are captured in ``WebhookResult.error`` rather than
        raised so notification failures never interrupt trading pipelines.
        """
        if not self.urls:
            return []

        results: List[WebhookResult] = []
        for url in self.urls:
            fmt = detect_webhook_format(url, self.format)
            payload = format_payload(
                event=event,
                title=title,
                message=message,
                subtitle=subtitle,
                severity=severity,
                data=data,
                fmt=fmt,
            )
            body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "APEX-SHARPE/1.0",
                "X-Apex-Event": event,
            }
            if self.secret:
                headers["X-Apex-Signature-256"] = sign_payload(body, self.secret)

            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    status = getattr(resp, "status", None) or resp.getcode()
                    results.append(WebhookResult(url=url, ok=200 <= status < 300, status_code=status))
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    if exc.fp:
                        detail = exc.read().decode(errors="replace")[:200].strip()
                except Exception:
                    pass
                err_msg = f"HTTP {exc.code}" + (f": {detail}" if detail else "")
                results.append(
                    WebhookResult(url=url, ok=False, status_code=exc.code, error=err_msg)
                )
            except (urllib.error.URLError, OSError, ValueError) as exc:
                results.append(WebhookResult(url=url, ok=False, error=str(exc)))
        return results
