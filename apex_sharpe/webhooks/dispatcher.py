"""
Outbound webhook dispatcher for APEX-SHARPE (stdlib only).

Supports Slack incoming webhooks, Discord webhooks, and generic JSON HTTP
webhooks with optional HMAC-SHA256 payload signing (X-Apex-Signature-256).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..config import WebhookCfg


SEVERITY_ORDER: Dict[str, int] = {
    "INFO": 0,
    "WARNING": 1,
    "ACTION": 2,
    "CRITICAL": 3,
}

_SLACK_COLORS: Dict[str, str] = {
    "INFO": "#2E7D32",
    "WARNING": "#F57C00",
    "ACTION": "#D32F2F",
    "CRITICAL": "#B71C1C",
}

_DISCORD_COLORS: Dict[str, int] = {
    "INFO": 0x2E7D32,
    "WARNING": 0xF57C00,
    "ACTION": 0xD32F2F,
    "CRITICAL": 0xB71C1C,
}


def normalize_severity(severity: Optional[str]) -> str:
    """Normalize severity string into one of INFO, WARNING, ACTION, CRITICAL."""
    if not severity:
        return "INFO"
    val = str(severity).strip().upper()
    if val in SEVERITY_ORDER:
        return val
    if val in ("OK", "SUCCESS", "DEBUG"):
        return "INFO"
    if val in ("WARN",):
        return "WARNING"
    if val in ("ERROR", "FAIL", "HIGH"):
        return "ACTION"
    return "INFO"


def redact_url(url: str) -> str:
    """Redact path tokens from webhook URLs before logging or reporting."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return "<invalid-url>"
    if not parsed.scheme or not parsed.netloc:
        return "<invalid-url>"
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return f"{parsed.scheme}://{parsed.netloc}"
    if len(parts) == 1:
        return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}"
    return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/***"


def validate_webhook_url(url: str) -> str:
    """Validate that a webhook target uses http(s) and includes a host."""
    cleaned = (url or "").strip()
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid webhook URL (must be http/https with host): {redact_url(cleaned)}")
    return cleaned


def sign_payload(body: bytes, secret: str) -> str:
    """Return 'sha256=<hexdigest>' HMAC signature for a raw JSON body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify an HMAC-SHA256 signature in constant time."""
    if not secret or not signature:
        return False
    provided = signature.strip()
    if provided.startswith("sha256="):
        provided = provided[len("sha256="):]
    if not provided:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


@dataclass
class WebhookEvent:
    """Canonical outbound webhook event."""
    event_type: str
    title: str
    message: str
    severity: str = "INFO"
    subtitle: str = ""
    source: str = "apex_sharpe"
    ticker: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        self.severity = normalize_severity(self.severity)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WebhookDeliveryResult:
    """Outcome of delivering a WebhookEvent to a single target."""
    target: str
    provider: str
    success: bool
    status_code: Optional[int] = None
    attempts: int = 0
    error: str = ""


def format_generic_payload(event: WebhookEvent) -> Dict[str, Any]:
    """Build canonical JSON payload for generic webhook receivers."""
    return event.to_dict()


def format_slack_payload(event: WebhookEvent) -> Dict[str, Any]:
    """Build Slack Incoming Webhook attachment payload."""
    color = _SLACK_COLORS.get(event.severity, "#2E7D32")
    headline = f"[{event.severity}] {event.title}"
    if event.subtitle:
        headline = f"{headline} — {event.subtitle}"

    fields = [
        {"title": "Severity", "value": event.severity, "short": True},
        {"title": "Event", "value": event.event_type, "short": True},
    ]
    if event.ticker:
        fields.append({"title": "Ticker", "value": event.ticker, "short": True})

    return {
        "text": headline,
        "attachments": [
            {
                "color": color,
                "title": event.title,
                "text": event.message,
                "fields": fields,
                "footer": event.source,
            }
        ],
    }


def format_discord_payload(event: WebhookEvent) -> Dict[str, Any]:
    """Build Discord Webhook embed payload."""
    color = _DISCORD_COLORS.get(event.severity, 0x2E7D32)
    fields = [
        {"name": "Severity", "value": event.severity, "inline": True},
        {"name": "Event", "value": event.event_type, "inline": True},
    ]
    if event.ticker:
        fields.append({"name": "Ticker", "value": event.ticker, "inline": True})
    if event.subtitle:
        fields.append({"name": "Detail", "value": event.subtitle, "inline": False})

    return {
        "embeds": [
            {
                "title": event.title[:256],
                "description": event.message[:4096],
                "color": color,
                "fields": fields,
                "timestamp": event.timestamp,
                "footer": {"text": event.source},
            }
        ]
    }


class WebhookDispatcher:
    """Dispatches WebhookEvent notifications to configured endpoints."""

    def __init__(self, config: Optional[WebhookCfg] = None):
        self.config = config or WebhookCfg()

    @property
    def has_targets(self) -> bool:
        return bool(
            self.config.slack_url
            or self.config.discord_url
            or self.config.generic_urls
        )

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and self.has_targets)

    def should_dispatch(self, severity: str) -> bool:
        min_sev = normalize_severity(self.config.min_severity)
        ev_sev = normalize_severity(severity)
        return SEVERITY_ORDER[ev_sev] >= SEVERITY_ORDER[min_sev]

    def _targets(self) -> List[Tuple[str, str]]:
        targets: List[Tuple[str, str]] = []
        if self.config.slack_url:
            targets.append(("slack", self.config.slack_url))
        if self.config.discord_url:
            targets.append(("discord", self.config.discord_url))
        for url in self.config.generic_urls:
            if url:
                targets.append(("generic", url))
        return targets

    def _format_for_provider(self, provider: str, event: WebhookEvent) -> Dict[str, Any]:
        if provider == "slack":
            return format_slack_payload(event)
        if provider == "discord":
            return format_discord_payload(event)
        return format_generic_payload(event)

    def dispatch(self, event: WebhookEvent) -> List[WebhookDeliveryResult]:
        """Deliver an event to all configured targets without raising."""
        if not self.enabled or not self.should_dispatch(event.severity):
            return []

        results: List[WebhookDeliveryResult] = []
        for provider, raw_url in self._targets():
            results.append(self._send_one(provider, raw_url, event))
        return results

    def _send_one(
        self,
        provider: str,
        raw_url: str,
        event: WebhookEvent,
    ) -> WebhookDeliveryResult:
        masked = redact_url(raw_url)
        try:
            url = validate_webhook_url(raw_url)
        except ValueError as exc:
            return WebhookDeliveryResult(
                target=masked,
                provider=provider,
                success=False,
                attempts=0,
                error=str(exc),
            )

        payload = self._format_for_provider(provider, event)
        body = json.dumps(payload, default=str).encode("utf-8")

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "APEX-SHARPE/1.0",
            "X-Apex-Event": event.event_type,
            "X-Apex-Severity": event.severity,
        }
        if provider == "generic" and self.config.signing_secret:
            headers["X-Apex-Signature-256"] = sign_payload(
                body, self.config.signing_secret
            )

        max_attempts = 1 + max(0, int(self.config.max_retries))
        last_status: Optional[int] = None
        last_error = ""
        attempts = 0

        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    status = getattr(resp, "status", None) or resp.getcode()
                    return WebhookDeliveryResult(
                        target=masked,
                        provider=provider,
                        success=200 <= int(status) < 300,
                        status_code=int(status),
                        attempts=attempt,
                    )
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                last_error = f"HTTP {exc.code}"
                # Do not retry permanent 4xx client errors (except 429)
                if 400 <= exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_error = str(exc)

            if attempt < max_attempts:
                time.sleep(min(0.2 * attempt, 1.0))

        return WebhookDeliveryResult(
            target=masked,
            provider=provider,
            success=False,
            status_code=last_status,
            attempts=attempts,
            error=last_error,
        )
