"""Unit and integration tests for outbound and inbound webhooks.

All outbound HTTP calls are mocked; inbound HTTP server tests bind only to
127.0.0.1 on an ephemeral port.
"""

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apex_sharpe.agents.reporter import send_notification
from apex_sharpe.config import StateCfg, WebhookCfg, load_config
from apex_sharpe.data.state import StateManager
from apex_sharpe.webhooks import (
    WebhookClient,
    WebhookDeliveryError,
    WebhookRouter,
    WebhookServer,
    detect_webhook_format,
    format_payload,
    sign_payload,
    validate_webhook_url,
    verify_signature,
)


def _mock_urlopen(status: int = 200, body: bytes = b'{"ok":true}'):
    resp = MagicMock()
    resp.status = status
    resp.getcode.return_value = status
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestOutboundHelpers:
    def test_validate_webhook_url_accepts_http_and_https(self):
        assert validate_webhook_url("https://example.com/hook") == "https://example.com/hook"
        assert validate_webhook_url(" http://127.0.0.1:8080/webhook ") == "http://127.0.0.1:8080/webhook"

    @pytest.mark.parametrize("bad_url", ["", "   ", "ftp://example.com/hook", "file:///etc/passwd", "not-a-url"])
    def test_validate_webhook_url_rejects_invalid(self, bad_url):
        with pytest.raises(WebhookDeliveryError):
            validate_webhook_url(bad_url)

    def test_detect_format_auto(self):
        assert detect_webhook_format("https://hooks.slack.com/services/T/B/X") == "slack"
        assert detect_webhook_format("https://discord.com/api/webhooks/123/abc") == "discord"
        assert detect_webhook_format("https://discordapp.com/api/webhooks/123/abc") == "discord"
        assert detect_webhook_format("https://example.com/webhook") == "json"
        assert detect_webhook_format("https://hooks.slack.com/services/T/B/X", "json") == "json"

    def test_format_payload_variants(self):
        j = format_payload("trade.open", "New IC", "Opened SPY", subtitle="1 trade", severity="OK", data={"id": "IC-1"}, fmt="json")
        assert j["event"] == "trade.open"
        assert j["severity"] == "OK"
        assert j["data"]["id"] == "IC-1"

        s = format_payload("trade.open", "New IC", "Opened SPY", subtitle="1 trade", severity="OK", data={"id": "IC-1"}, fmt="slack")
        assert "[OK] New IC" in s["text"]
        assert s["attachments"][0]["title"] == "New IC"

        d = format_payload("trade.open", "New IC", "Opened SPY", subtitle="1 trade", severity="ACTION", data={"id": "IC-1"}, fmt="discord")
        assert "[ACTION] New IC" in d["content"]
        assert d["embeds"][0]["title"] == "New IC"

    def test_sign_and_verify_signature(self):
        body = b'{"hello":"world"}'
        sig = sign_payload(body, "super-secret")
        assert sig.startswith("sha256=")
        assert verify_signature(body, sig, "super-secret") is True
        assert verify_signature(body, sig, "wrong-secret") is False
        assert verify_signature(body, "invalid", "super-secret") is False
        assert verify_signature(body, "", "super-secret") is False


class TestWebhookClient:
    def test_send_signed_json_and_slack_multi_target(self):
        cfg = WebhookCfg(
            urls=("https://example.com/hook", "https://hooks.slack.com/services/T/B/X"),
            secret="my-secret",
        )
        client = WebhookClient(cfg)
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append({
                "url": req.full_url,
                "headers": {k.lower(): v for k, v in req.header_items()},
                "body": req.data,
                "timeout": timeout,
            })
            return _mock_urlopen(status=202)

        with patch("apex_sharpe.webhooks.outbound.urllib.request.urlopen", side_effect=fake_urlopen):
            results = client.send(
                event="monitor.action",
                title="ACTION REQUIRED",
                message="DTE <= 21",
                severity="ACTION",
                data={"symbol": "SPY"},
            )

        assert len(results) == 2
        assert all(r.ok and r.status_code == 202 for r in results)

        first = captured[0]
        assert first["headers"]["x-apex-event"] == "monitor.action"
        assert verify_signature(first["body"], first["headers"]["x-apex-signature-256"], "my-secret")
        assert json.loads(first["body"].decode())["event"] == "monitor.action"

        second = captured[1]
        assert "attachments" in json.loads(second["body"].decode())

    def test_send_captures_http_and_network_errors(self):
        cfg = WebhookCfg(urls=("https://example.com/a", "https://example.com/b"))
        client = WebhookClient(cfg)
        err_400 = urllib.error.HTTPError(
            url="https://example.com/a",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=io.BytesIO(b"boom"),
        )
        err_net = urllib.error.URLError("connection refused")

        with patch(
            "apex_sharpe.webhooks.outbound.urllib.request.urlopen",
            side_effect=[err_400, err_net],
        ):
            results = client.send("test", "Title", "Msg")

        assert len(results) == 2
        assert results[0].ok is False
        assert results[0].status_code == 500
        assert "HTTP 500" in results[0].error
        assert results[1].ok is False
        assert "connection refused" in results[1].error

    def test_send_notification_dispatches_to_webhook(self):
        cfg = WebhookCfg(urls=("https://example.com/notify",))
        client = WebhookClient(cfg)

        with patch("apex_sharpe.webhooks.outbound.urllib.request.urlopen", return_value=_mock_urlopen(200)) as mock_open:
            send_notification(
                title="Test Alert",
                message="Body",
                subtitle="Sub",
                severity="WARNING",
                event="test.alert",
                webhook_client=client,
            )
            mock_open.assert_called_once()


class TestWebhookRouter:
    def test_health_and_unknown_routes(self):
        router = WebhookRouter()
        ok = router.handle_get("/health")
        assert ok.status == 200
        assert ok.body["status"] == "ok"

        missing = router.handle_get("/nope")
        assert missing.status == 404

        post_missing = router.handle_post("/unknown", b"{}")
        assert post_missing.status == 404

    def test_signature_enforced_when_secret_configured(self):
        router = WebhookRouter(config=WebhookCfg(inbound_secret="top-secret"))
        body = json.dumps({"ticker": "SPY", "signal": "FEAR_BOUNCE"}).encode()

        no_sig = router.handle_post("/webhook", body, {})
        assert no_sig.status == 401

        bad_sig = router.handle_post("/webhook", body, {"X-Apex-Signature-256": "sha256=deadbeef"})
        assert bad_sig.status == 401

        good_sig = router.handle_post(
            "/webhook",
            body,
            {"X-Apex-Signature-256": sign_payload(body, "top-secret")},
        )
        assert good_sig.status == 202

    def test_signal_webhook_logs_state_and_historian(self, tmp_path: Path):
        state = StateManager(
            StateCfg(
                positions_path=str(tmp_path / "positions.json"),
                signals_path=str(tmp_path / "signals.json"),
                cache_path=str(tmp_path / "cache.json"),
            )
        )
        hist_dir = tmp_path / "historian"
        router = WebhookRouter(state=state, historian_root=str(hist_dir))

        payload = json.dumps({
            "ticker": "spx",
            "signal": "FEAR_BOUNCE_STRONG",
            "price": 5800.5,
            "edge_id": "EDGE-0DTE-001",
            "message": "TradingView trigger",
        }).encode()
        resp = router.handle_post("/webhooks/tradingview", payload)
        assert resp.status == 202
        assert resp.body["recorded"]["ticker"] == "SPX"

        saved_signals = state.load_signals()
        assert len(saved_signals) == 1
        assert saved_signals[0]["source"] == "webhook:tradingview"

        hist_files = list(hist_dir.glob("*.jsonl"))
        assert len(hist_files) == 1
        envelope = json.loads(hist_files[0].read_text().splitlines()[0])
        assert envelope["event"]["derived"] is True
        assert envelope["event"]["edge_id"] == "EDGE-0DTE-001"

    def test_github_webhook_ping_and_workflow_run_forward(self):
        outbound = MagicMock()
        outbound.enabled = True
        router = WebhookRouter(
            config=WebhookCfg(github_secret="gh-sec"),
            outbound=outbound,
        )

        ping_body = json.dumps({"zen": "Keep it logically awesome.", "repository": {"full_name": "mattyhtown/MasterProject"}}).encode()
        ping_resp = router.handle_post(
            "/webhooks/github",
            ping_body,
            {
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": sign_payload(ping_body, "gh-sec"),
            },
        )
        assert ping_resp.status == 200
        assert ping_resp.body["event"] == "ping"

        wf_body = json.dumps({
            "repository": {"full_name": "mattyhtown/MasterProject"},
            "workflow_run": {
                "name": "CI",
                "conclusion": "failure",
                "head_branch": "main",
                "html_url": "https://github.com/mattyhtown/MasterProject/actions/runs/1",
            },
        }).encode()
        wf_resp = router.handle_post(
            "/webhooks/github",
            wf_body,
            {
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": sign_payload(wf_body, "gh-sec"),
            },
        )
        assert wf_resp.status == 202
        outbound.send.assert_called_once()
        assert outbound.send.call_args.kwargs["severity"] == "FAIL"

    def test_supabase_and_ops_routes(self):
        outbound = MagicMock()
        outbound.enabled = True
        router = WebhookRouter(outbound=outbound)

        supa_body = json.dumps({
            "type": "INSERT",
            "table": "alerts",
            "record": {"id": "a1", "message": "DTE EXIT triggered"},
        }).encode()
        supa_resp = router.handle_post("/webhooks/supabase", supa_body)
        assert supa_resp.status == 202
        outbound.send.assert_called_once()
        assert outbound.send.call_args.kwargs["severity"] == "ACTION"

        ops_resp = router.handle_post("/webhooks/ops", json.dumps({"action": "health_check"}).encode())
        assert ops_resp.status == 200
        assert ops_resp.body["result"]["checks"]["python"]["status"] == "OK"

    def test_rejects_oversized_and_invalid_json(self):
        router = WebhookRouter(config=WebhookCfg(max_body_bytes=32))
        big = b'{"ticker":"' + b"A" * 64 + b'"}'
        assert router.handle_post("/webhook", big).status == 413
        assert router.handle_post("/webhook", b"{not-json").status == 400
        assert router.handle_post("/webhook", b"[1,2,3]").status == 400


class TestWebhookServerE2E:
    def test_http_server_health_and_signed_post(self, tmp_path: Path):
        state = StateManager(
            StateCfg(
                positions_path=str(tmp_path / "positions.json"),
                signals_path=str(tmp_path / "signals.json"),
                cache_path=str(tmp_path / "cache.json"),
            )
        )
        cfg = WebhookCfg(host="127.0.0.1", port=0, inbound_secret="e2e-secret")
        with WebhookServer(config=cfg, state=state) as srv:
            host, port = srv.server_address
            base = f"http://{host}:{port}"

            with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode())
                assert data["status"] == "ok"

            body = json.dumps({"ticker": "SPY", "signal": "BULL_PUT"}).encode()
            req = urllib.request.Request(
                f"{base}/webhooks/signal",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Apex-Signature-256": sign_payload(body, "e2e-secret"),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 202
                res_json = json.loads(resp.read().decode())
                assert res_json["status"] == "accepted"

        assert len(state.load_signals()) == 1


def test_load_config_reads_webhook_env(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/1, https://example.com/2")
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cr3t")
    monkeypatch.setenv("WEBHOOK_TIMEOUT", "15")
    monkeypatch.setenv("WEBHOOK_FORMAT", "json")
    monkeypatch.setenv("WEBHOOK_INBOUND_SECRET", "in-sec")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "gh-sec")
    monkeypatch.setenv("WEBHOOK_HOST", "0.0.0.0")
    monkeypatch.setenv("WEBHOOK_PORT", "9090")

    cfg = load_config()
    assert cfg.webhook.urls == ("https://example.com/1", "https://example.com/2")
    assert cfg.webhook.secret == "s3cr3t"
    assert cfg.webhook.timeout == 15
    assert cfg.webhook.format == "json"
    assert cfg.webhook.inbound_secret == "in-sec"
    assert cfg.webhook.github_secret == "gh-sec"
    assert cfg.webhook.host == "0.0.0.0"
    assert cfg.webhook.port == 9090
