"""A real threaded HTTP server for end-to-end transport tests (localhost only).

Canned routes mirror the live API contract, including error envelopes,
Retry-After on 429, gzip responses, and a PNG screenshot endpoint.
"""

from __future__ import annotations

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _load(name: str) -> bytes:
    import os

    path = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(path, "rb") as fh:
        return fh.read()


class _Handler(BaseHTTPRequestHandler):
    server_version = "ATAFake/1.0"
    last_user_agent: str | None = None  # class-level capture for tests

    def log_message(self, fmt: str, *args: Any) -> None:  # silence
        pass

    def _json(self, status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _gzip_json(self, status: int, payload: Any) -> None:
        body = gzip.compress(json.dumps(payload).encode("utf-8"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        _Handler.last_user_agent = self.headers.get("User-Agent")
        path = self.path.split("?")[0]
        if path == "/qx_assets":
            auth = self.headers.get("Authorization", "")
            if not (auth.startswith("Bearer ") or "key=" in self.path):
                self._json(401, {"error": "A valid API key is required."})
            else:
                self._json(200, _load_json("assets.json"))
        elif path == "/qx_gzip":
            self._gzip_json(200, _load_json("candles.json"))
        elif path == "/qx_429":
            self._json(429, {"error": "Rate limit reached."}, {"Retry-After": "1"})
        elif path == "/qx_500":
            self._json(500, {"error": "boom"})
        elif path == "/qx_502":
            self._json(502, {"error": "Upstream feed failure / insufficient candles."})
        elif path == "/qx_400":
            self._json(400, {"error": "That parameter is not available on this API."})
        elif path == "/qx_403":
            self._json(403, {"error": "This key does not include that API."})
        elif path == "/qx_ss_pro_v2":
            png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
        elif path == "/qx_truncated":
            body = b'{"error": "truncat'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/qx_candles":
            self._json(200, _load_json("candles.json"))
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(length)
        if path == "/qx_429":
            self._json(429, {"error": "Rate limit reached."}, {"Retry-After": "1"})
        elif path == "/qx_500":
            self._json(500, {"error": "boom"})
        else:
            self._json(404, {"error": "not found"})


def _load_json(name: str) -> Any:
    return json.loads(_load(name).decode("utf-8"))


class FakeServer:
    """Context manager around a threaded localhost HTTP server."""

    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> FakeServer:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"
