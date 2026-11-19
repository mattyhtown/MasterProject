"""Webhook subsystem for APEX-SHARPE (outbound notifications and inbound receiver)."""

from .dispatcher import (
    SEVERITY_ORDER,
    WebhookDeliveryResult,
    WebhookDispatcher,
    WebhookEvent,
    format_discord_payload,
    format_generic_payload,
    format_slack_payload,
    normalize_severity,
    redact_url,
    sign_payload,
    validate_webhook_url,
    verify_signature,
)
from .server import WebhookServer, create_webhook_server

__all__ = [
    "SEVERITY_ORDER",
    "WebhookDeliveryResult",
    "WebhookDispatcher",
    "WebhookEvent",
    "WebhookServer",
    "create_webhook_server",
    "format_discord_payload",
    "format_generic_payload",
    "format_slack_payload",
    "normalize_severity",
    "redact_url",
    "sign_payload",
    "validate_webhook_url",
    "verify_signature",
]
