"""Tests for outbound and inbound webhooks.

All outbound HTTP requests are mocked and inbound server tests bind only to
loopback 127.0.0.1 on an ephemeral port (0). No external network calls.
"""

import io
import json
import sys
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from apex_sharpe.agents.reporter import ReporterAgent, send_notification
from apex_sharpe.config import StateCfg, WebhookCfg, load_config, load_webhook_config
from apex_sharpe.data.state import StateManager
from apex_sharpe.webhooks import (
    WebhookDispatcher,
    WebhookEvent,
    create_webhook_server,
    format_discord_payload,
    format_generic_payload,
    format_slack_payload,
    redact_url,
    sign_payload,
    validate_webhook_url,
    verify_signature,
)


def _mock_urlopen(status: int = 200):
    resp = MagicMock()
    resp.status = status
    resp.getcode.return_value = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _http_request(url: str, method: str = "GET", body: bytes = None, headers: dict = None):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestWebhookConfig:
    def test_defaults_when_env_unset(self, monkeypatch):
        for key in (
            "WEBHOOK_ENABLED",
            "SLACK_WEBHOOK_URL",
            "DISCORD_WEBHOOK_URL",
            "WEBHOOK_URLS",
            "WEBHOOK_SIGNING_SECRET",
            "WEBHOOK_INBOUND_SECRET",
            "WEBHOOK_MIN_SEVERITY",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = load_webhook_config()
        assert cfg.enabled is False
        assert cfg.slack_url == ""
        assert cfg.discord_url == ""
        assert cfg.generic_urls == ()
        assert cfg.listen_host == "127.0.0.1"
        assert cfg.listen_port == 8080
        assert cfg.min_severity == "INFO"

    def test_auto_enabled_when_url_present(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_ENABLED", raising=False)
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
        monkeypatch.setenv(
            "WEBHOOK_URLS",
            " https://example.com/hook1 , https://example.com/hook2 ",
        )
        cfg = load_webhook_config()
        assert cfg.enabled is True
        assert cfg.slack_url == "https://hooks.slack.com/services/T/B/X"
        assert cfg.generic_urls == (
            "https://example.com/hook1",
            "https://example.com/hook2",
        )

    def test_explicit_disable_overrides_urls(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
        monkeypatch.setenv("WEBHOOK_ENABLED", "false")
        cfg = load_config().webhooks
        assert cfg.enabled is False


# ---------------------------------------------------------------------------
# Signing, URL validation, redaction
# ---------------------------------------------------------------------------

class TestSecurityHelpers:
    def test_sign_and_verify_roundtrip(self):
        body = b'{"hello":"world"}'
        sig = sign_payload(body, "top-secret")
        assert sig.startswith("sha256=")
        assert verify_signature(body, sig, "top-secret") is True
        assert verify_signature(body, sig.removeprefix("sha256="), "top-secret") is True
        assert verify_signature(body, sig, "wrong-secret") is False
        assert verify_signature(b"{}", sig, "top-secret") is False
        assert verify_signature(body, "", "top-secret") is False
        assert verify_signature(body, sig, "") is False

    def test_validate_webhook_url(self):
        assert validate_webhook_url("https://example.com/hook") == "https://example.com/hook"
        assert validate_webhook_url("http://127.0.0.1:8080/hook") == "http://127.0.0.1:8080/hook"
        for bad in ("", "file:///etc/passwd", "ftp://example.com/a", "/relative/path"):
            with pytest.raises(ValueError):
                validate_webhook_url(bad)

    def test_redact_url_hides_secret_segments(self):
        masked = redact_url("https://hooks.slack.com/services/T000/B000/SECRETTOKEN")
        assert "SECRETTOKEN" not in masked
        assert masked == "https://hooks.slack.com/services/***"


# ---------------------------------------------------------------------------
# Payload formatters & Dispatcher
# ---------------------------------------------------------------------------

class TestWebhookDispatcher:
    def test_payload_formatters(self):
        ev = WebhookEvent(
            event_type="trade_opened",
            title="New IC",
            subtitle="SPY 35 DTE",
            message="Opened IC-SPY-1",
            severity="ACTION",
            ticker="SPY",
            data={"credit": 3.5},
        )
        slack = format_slack_payload(ev)
        assert "[ACTION] New IC" in slack["text"]
        assert slack["attachments"][0]["color"] == "#D32F2F"

        discord = format_discord_payload(ev)
        assert discord["embeds"][0]["title"] == "New IC"
        assert discord["embeds"][0]["color"] == 0xD32F2F

        generic = format_generic_payload(ev)
        assert generic["event_type"] == "trade_opened"
        assert generic["ticker"] == "SPY"
        assert generic["data"] == {"credit": 3.5}

    def test_dispatch_all_providers_with_signing(self):
        cfg = WebhookCfg(
            enabled=True,
            slack_url="https://hooks.slack.com/services/T/B/SECRET",
            discord_url="https://discord.com/api/webhooks/123/SECRET",
            generic_urls=("https://example.com/webhook",),
            signing_secret="shared-hmac-secret",
            max_retries=0,
        )
        dispatcher = WebhookDispatcher(cfg)
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append({
                "url": req.full_url,
                "headers": {k.lower(): v for k, v in req.header_items()},
                "body": req.data,
                "timeout": timeout,
            })
            return _mock_urlopen(204)

        ev = WebhookEvent(
            event_type="zero_dte_composite",
            title="0DTE Signal",
            message="skewing, contango, rip",
            severity="ACTION",
            ticker="SPX",
        )
        with patch("apex_sharpe.webhooks.dispatcher.urllib.request.urlopen", side_effect=fake_urlopen):
            results = dispatcher.dispatch(ev)

        assert len(results) == 3
        assert all(r.success and r.status_code == 204 for r in results)
        generic_call = [c for c in captured if "example.com" in c["url"]][0]
        sig = generic_call["headers"]["x-apex-signature-256"]
        assert verify_signature(generic_call["body"], sig, "shared-hmac-secret")

    def test_min_severity_filtering(self):
        cfg = WebhookCfg(
            enabled=True,
            generic_urls=("https://example.com/hook",),
            min_severity="ACTION",
        )
        dispatcher = WebhookDispatcher(cfg)
        with patch("apex_sharpe.webhooks.dispatcher.urllib.request.urlopen") as mock_open:
            res_info = dispatcher.dispatch(WebhookEvent("t", "Info", "msg", severity="INFO"))
            res_warn = dispatcher.dispatch(WebhookEvent("t", "Warn", "msg", severity="WARNING"))
            mock_open.assert_not_called()
            assert res_info == []
            assert res_warn == []

            mock_open.return_value = _mock_urlopen(200)
            res_action = dispatcher.dispatch(WebhookEvent("t", "Act", "msg", severity="ACTION"))
            assert len(res_action) == 1
            assert res_action[0].success is True

    def test_retries_on_5xx_not_on_4xx(self):
        cfg = WebhookCfg(
            enabled=True,
            generic_urls=("https://example.com/hook",),
            max_retries=2,
        )
        dispatcher = WebhookDispatcher(cfg)
        ev = WebhookEvent("t", "Title", "Body", severity="ACTION")

        err_503 = urllib.error.HTTPError(
            "https://example.com/hook", 503, "Unavailable", None, io.BytesIO(b"")
        )
        with patch(
            "apex_sharpe.webhooks.dispatcher.urllib.request.urlopen",
            side_effect=[err_503, _mock_urlopen(200)],
        ) as mock_open, patch("apex_sharpe.webhooks.dispatcher.time.sleep"):
            results = dispatcher.dispatch(ev)
        assert results[0].success is True
        assert results[0].attempts == 2
        assert mock_open.call_count == 2

        err_400 = urllib.error.HTTPError(
            "https://example.com/hook", 400, "Bad Request", None, io.BytesIO(b"")
        )
        with patch(
            "apex_sharpe.webhooks.dispatcher.urllib.request.urlopen",
            side_effect=err_400,
        ) as mock_open:
            results = dispatcher.dispatch(ev)
        assert results[0].success is False
        assert results[0].status_code == 400
        assert results[0].attempts == 1
        assert mock_open.call_count == 1

    def test_invalid_url_fails_gracefully(self):
        cfg = WebhookCfg(
            enabled=True,
            generic_urls=("file:///etc/passwd",),
        )
        results = WebhookDispatcher(cfg).dispatch(WebhookEvent("t", "T", "M"))
        assert len(results) == 1
        assert results[0].success is False
        assert "Invalid webhook URL" in results[0].error


# ---------------------------------------------------------------------------
# ReporterAgent & send_notification integration
# ---------------------------------------------------------------------------

class TestReporterIntegration:
    def test_send_notification_skips_osascript_on_linux_and_sends_webhook(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        cfg = WebhookCfg(
            enabled=True,
            generic_urls=("https://example.com/hook",),
            max_retries=0,
        )
        with patch("apex_sharpe.agents.reporter.subprocess.run") as mock_sub, patch(
            "apex_sharpe.webhooks.dispatcher.urllib.request.urlopen",
            return_value=_mock_urlopen(200),
        ) as mock_open:
            results = send_notification(
                title="Test",
                message="Hello",
                severity="ACTION",
                event_type="trade_opened",
                webhook_config=cfg,
            )
        mock_sub.assert_not_called()
        mock_open.assert_called_once()
        assert len(results) == 1 and results[0].success is True

    def test_reporter_agent_scan_and_monitor_dispatch_webhooks(self, sample_position):
        cfg = WebhookCfg(
            enabled=True,
            generic_urls=("https://example.com/hook",),
            max_retries=0,
        )
        reporter = ReporterAgent(webhook_config=cfg)
        payloads = []

        def fake_urlopen(req, timeout=None):
            payloads.append(json.loads(req.data.decode("utf-8")))
            return _mock_urlopen(200)

        with patch("apex_sharpe.webhooks.dispatcher.urllib.request.urlopen", side_effect=fake_urlopen):
            res_scan = reporter.run({
                "scan_result": True,
                "candidates": [],
                "risk_decisions": [],
                "new_positions": [{
                    "id": "IC-SPY-1",
                    "symbol": "SPY",
                    "entry_credit": 3.5,
                    "max_profit": 350.0,
                    "max_loss": 1650.0,
                }],
            })
            res_mon = reporter.run({
                "monitor_results": [{
                    "position": sample_position,
                    "current_price": 690.0,
                    "valuation": {"pnl": 200.0, "pnl_pct": 50.0, "data_source": "ESTIMATED"},
                    "alerts": [{"level": "ACTION", "message": "PROFIT TARGET hit"}],
                }],
                "closed_positions": [],
            })

        assert res_scan.success and len(res_scan.data["deliveries"]) == 1
        assert res_mon.success and len(res_mon.data["deliveries"]) == 1
        assert payloads[0]["event_type"] == "trade_opened"
        assert payloads[1]["event_type"] == "position_action"
        assert payloads[1]["severity"] == "ACTION"


# ---------------------------------------------------------------------------
# Inbound WebhookServer
# ---------------------------------------------------------------------------

class TestWebhookServer:
    def _cfg(self, secret: str = "inbound-test-secret", outbound_url: str = "") -> WebhookCfg:
        return WebhookCfg(
            enabled=bool(outbound_url),
            generic_urls=(outbound_url,) if outbound_url else (),
            listen_host="127.0.0.1",
            listen_port=0,
            inbound_secret=secret,
            max_retries=0,
        )

    def test_health_and_404(self):
        with create_webhook_server(config=self._cfg(), quiet=True) as srv:
            host, port = srv.server_address
            status, body = _http_request(f"http://{host}:{port}/health")
            assert status == 200
            assert body["status"] == "ok"

            status404, _ = _http_request(f"http://{host}:{port}/nope")
            assert status404 == 404

    def test_signal_auth_fail_closed(self, tmp_path):
        state = StateManager(StateCfg(
            positions_path=str(tmp_path / "positions.json"),
            signals_path=str(tmp_path / "signals.json"),
            cache_path=str(tmp_path / "cache.json"),
        ))
        # Empty inbound secret -> 401 fail-closed
        with create_webhook_server(config=self._cfg(secret=""), state=state, quiet=True) as srv:
            host, port = srv.server_address
            raw = json.dumps({"ticker": "SPY", "signal": "TEST"}).encode()
            status, _ = _http_request(
                f"http://{host}:{port}/webhooks/signal",
                method="POST",
                body=raw,
                headers={"Content-Type": "application/json"},
            )
            assert status == 401

        # Missing/bad signature -> 401
        with create_webhook_server(config=self._cfg(secret="sec"), state=state, quiet=True) as srv:
            host, port = srv.server_address
            raw = json.dumps({"ticker": "SPY", "signal": "TEST"}).encode()
            status, _ = _http_request(
                f"http://{host}:{port}/webhooks/signal",
                method="POST",
                body=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Apex-Signature-256": "sha256=deadbeef",
                },
            )
            assert status == 401
            assert state.load_signals() == []

    def test_signal_hmac_and_bearer_record_to_state(self, tmp_path):
        state = StateManager(StateCfg(
            positions_path=str(tmp_path / "positions.json"),
            signals_path=str(tmp_path / "signals.json"),
            cache_path=str(tmp_path / "cache.json"),
        ))
        secret = "my-webhook-secret"
        with create_webhook_server(config=self._cfg(secret=secret), state=state, quiet=True) as srv:
            host, port = srv.server_address
            raw = json.dumps({
                "ticker": "SPX",
                "signal": "FEAR_BOUNCE",
                "direction": "bull",
                "price": 5800.0,
            }).encode()
            sig = sign_payload(raw, secret)
            status, body = _http_request(
                f"http://{host}:{port}/webhooks/signal",
                method="POST",
                body=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Apex-Signature-256": sig,
                },
            )
            assert status == 202
            assert body["accepted"] is True
            assert body["recorded"] is True

            # ****** also accepted
            raw2 = json.dumps({"ticker": "SPY", "signal": "TV_ALERT"}).encode()
            status2, body2 = _http_request(
                f"http://{host}:{port}/webhooks/signal",
                method="POST",
                body=raw2,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": " ".join(["Bearer", secret]),
                },
            )
            assert status2 == 202
            assert body2["recorded"] is True

        signals = state.load_signals()
        assert len(signals) == 2
        assert signals[0]["source"] == "webhook"
        assert signals[0]["paper_only"] is True
        assert signals[0]["ticker"] == "SPX"
        assert signals[1]["ticker"] == "SPY"

    def test_supabase_and_alert_endpoints_dispatch_outbound(self):
        secret = "sb-secret"
        cfg = self._cfg(secret=secret, outbound_url="https://example.com/out")
        dispatched_events = []

        def fake_urlopen(req, timeout=None):
            if "example.com" in req.full_url:
                dispatched_events.append(json.loads(req.data.decode("utf-8")))
                return _mock_urlopen(200)
            raise AssertionError(f"Unexpected URL {req.full_url}")

        # Let local loopback requests pass through while mocking only example.com
        orig_urlopen = urllib.request.urlopen

        def selective_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "127.0.0.1" in url:
                return orig_urlopen(req, timeout=timeout)
            return fake_urlopen(req, timeout=timeout)

        with create_webhook_server(config=cfg, quiet=True) as srv:
            host, port = srv.server_address
            with patch("apex_sharpe.webhooks.dispatcher.urllib.request.urlopen", side_effect=selective_urlopen):
                sb_body = json.dumps({
                    "type": "UPDATE",
                    "table": "positions",
                    "record": {
                        "symbol": "SPY",
                        "position_type": "IRON_CONDOR",
                        "status": "CLOSED",
                        "exit_reason": "PROFIT_TARGET",
                        "realized_pnl": 195.0,
                    },
                    "old_record": {"status": "OPEN"},
                }).encode()
                status, resp = _http_request(
                    f"http://{host}:{port}/webhooks/supabase",
                    method="POST",
                    body=sb_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Apex-Signature-256": sign_payload(sb_body, secret),
                    },
                )
                assert status == 200
                assert resp["dispatched"] == 1

                alert_body = json.dumps({
                    "title": "Latency Warning",
                    "message": "ORATS p95 > 5000ms",
                    "severity": "WARNING",
                }).encode()
                status2, resp2 = _http_request(
                    f"http://{host}:{port}/webhooks/alert",
                    method="POST",
                    body=alert_body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": " ".join(["Bearer", secret]),
                    },
                )
                assert status2 == 200
                assert resp2["dispatched"] == 1

        assert len(dispatched_events) == 2
        assert dispatched_events[0]["event_type"] == "supabase_positions_update"
        assert dispatched_events[0]["severity"] == "ACTION"
        assert dispatched_events[1]["title"] == "Latency Warning"
        assert dispatched_events[1]["severity"] == "WARNING"

    def test_request_validation_errors(self):
        secret = "val-secret"
        with create_webhook_server(config=self._cfg(secret=secret), quiet=True) as srv:
            host, port = srv.server_address
            url = f"http://{host}:{port}/webhooks/signal"

            # Unsupported content type -> 415
            status, _ = _http_request(
                url,
                method="POST",
                body=b"plain",
                headers={"Content-Type": "text/plain"},
            )
            assert status == 415

            # Bad JSON -> 400
            status, _ = _http_request(
                url,
                method="POST",
                body=b"{not-json",
                headers={"Content-Type": "application/json"},
            )
            assert status == 400

            # JSON array instead of object -> 400
            status, _ = _http_request(
                url,
                method="POST",
                body=b"[1, 2, 3]",
                headers={"Content-Type": "application/json"},
            )
            assert status == 400

            # Missing required signal fields -> 400
            empty_obj = b"{}"
            status, _ = _http_request(
                url,
                method="POST",
                body=empty_obj,
                headers={
                    "Content-Type": "application/json",
                    "X-Apex-Signature-256": sign_payload(empty_obj, secret),
                },
            )
            assert status == 400


class TestWebhookCLI:
    def test_webhook_test_missing_targets_exits_1(self, monkeypatch):
        from apex_sharpe import cli

        for key in ("SLACK_WEBHOOK_URL", "DISCORD_WEBHOOK_URL", "WEBHOOK_URLS", "WEBHOOK_ENABLED"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(sys, "argv", ["apex_sharpe", "webhook-test"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1

    def test_webhook_test_success_exits_0(self, monkeypatch):
        from apex_sharpe import cli

        monkeypatch.setenv("WEBHOOK_URLS", "https://example.com/hook")
        monkeypatch.setenv("WEBHOOK_ENABLED", "true")
        monkeypatch.setattr(sys, "argv", ["apex_sharpe", "webhook-test"])
        with patch("apex_sharpe.webhooks.dispatcher.urllib.request.urlopen", return_value=_mock_urlopen(200)):
            cli.main()

    def test_webhook_serve_requires_inbound_secret(self, monkeypatch):
        from apex_sharpe import cli

        monkeypatch.delenv("WEBHOOK_INBOUND_SECRET", raising=False)
        monkeypatch.setattr(sys, "argv", ["apex_sharpe", "webhook-serve"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1

