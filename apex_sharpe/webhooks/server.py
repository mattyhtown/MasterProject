"""
Stdlib HTTP server for receiving inbound webhooks.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple

from ..config import WebhookCfg
from ..data.state import StateManager
from .inbound import WebhookResponse, WebhookRouter
from .outbound import WebhookClient


def _make_handler(router: WebhookRouter):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            resp = router.handle_get(self.path)
            self._write_json(resp)

        def do_POST(self) -> None:
            raw_len = self.headers.get("Content-Length")
            try:
                content_length = int(raw_len) if raw_len is not None else 0
            except ValueError:
                self._write_json(WebhookResponse(status=400, body={"error": "Invalid Content-Length"}))
                return
            if content_length < 0:
                self._write_json(WebhookResponse(status=400, body={"error": "Negative Content-Length"}))
                return
            if content_length > router.config.max_body_bytes:
                self._write_json(
                    WebhookResponse(
                        status=413,
                        body={"error": f"Payload exceeds {router.config.max_body_bytes} bytes"},
                    )
                )
                return

            body = self.rfile.read(content_length) if content_length > 0 else b""
            headers = {k: v for k, v in self.headers.items()}
            resp = router.handle_post(self.path, body, headers)
            self._write_json(resp)

        def _write_json(self, resp: WebhookResponse) -> None:
            encoded = json.dumps(resp.body, default=str).encode("utf-8")
            self.send_response(resp.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:
            # Suppress noisy stderr logging during tests; server status is printed explicitly.
            return

    return _Handler


class WebhookServer:
    """Threaded HTTP server wrapping ``WebhookRouter``."""

    def __init__(
        self,
        config: Optional[WebhookCfg] = None,
        state: Optional[StateManager] = None,
        outbound: Optional[WebhookClient] = None,
        historian_root: Optional[str] = None,
        router: Optional[WebhookRouter] = None,
    ):
        self.config = config or WebhookCfg()
        self.router = router or WebhookRouter(
            config=self.config,
            state=state,
            outbound=outbound,
            historian_root=historian_root,
        )
        self._httpd = ThreadingHTTPServer(
            (self.config.host, self.config.port),
            _make_handler(self.router),
        )
        self._thread: Optional[threading.Thread] = None

    @property
    def server_address(self) -> Tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def start(self) -> Tuple[str, int]:
        """Start serving in a daemon background thread and return (host, port)."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
        return self.server_address

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "WebhookServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.shutdown()
