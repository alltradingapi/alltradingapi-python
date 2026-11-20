"""Shared test plumbing.

Guarantees:

* the update gate is disabled by default (``ATA_UPDATE_POLICY=off``) so tests
  never touch PyPI;
* a deterministic :class:`FakeTransport` replaces the real HTTP layer;
* a real local threaded HTTP server (``tests/fake_server.py``) exists for
  end-to-end transport tests on 127.0.0.1 — still fully offline.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

# Make the update gate inert for the whole test session, before imports run.
os.environ.setdefault("ATA_UPDATE_POLICY", "off")
os.environ.setdefault("ATA_SKIP_UPDATE", "1")
os.environ.pop("ATA_API_KEY", None)
for name in list(os.environ):
    if name.startswith("ATA_KEY_"):
        del os.environ[name]

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alltradingapi import config as _config  # noqa: E402
from alltradingapi import update as update_module  # noqa: E402
from alltradingapi.client import Client  # noqa: E402
from alltradingapi.errors import error_from_response  # noqa: E402
from alltradingapi.transport import Response  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

# PNG file signature, built from bytes so the source stays ASCII-only.
_PNG_MAGIC = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A])


def load_fixture(name: str) -> Any:
    """Load a JSON fixture from tests/fixtures."""
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


class FakeTransport:
    """Records every request and replies from a routing table.

    Routes map ``METHOD path`` to either a static ``(status, body)`` tuple,
    a ``(status, body, headers)`` triple, or a callable
    ``(request) -> (status, body_bytes, headers)``.
    """

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {"content-type": "application/json"}

    def request(
        self,
        method: str,
        path: str,
        params: list[tuple[str, str]] | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Response:
        query = dict(params or [])
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": query,
                "body": body,
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        route_key = f"{method.upper()} {path}"
        handler = self.routes.get(route_key, self.routes.get(path))
        if handler is None:
            response = Response(
                404, dict(self.headers), json.dumps({"error": "not found"}).encode(), path, 1.0
            )
        elif callable(handler):
            status, body_bytes, extra = handler(self.calls[-1])
            merged = dict(self.headers)
            merged.update(extra or {})
            response = Response(status, merged, body_bytes, path, 1.0)
        elif len(handler) == 3:
            status, body_bytes, extra = handler
            merged = dict(self.headers)
            merged.update(extra or {})
            response = Response(status, merged, body_bytes, path, 1.0)
        else:
            status, body_bytes = handler
            response = Response(status, dict(self.headers), body_bytes, path, 1.0)
        # Mirror the real transport's status->error mapping so client tests
        # behave exactly like they would against a live server.
        if response.status >= 400:
            raise error_from_response(
                response.status,
                response.content,
                response.url,
                headers=response.headers,
            )
        return response

    def close(self) -> None:
        pass

    # -- helpers ----------------------------------------------------------
    def json_route(self, path: str, status: int, payload: Any, method: str = "GET") -> None:
        self.routes[f"{method.upper()} {path}"] = (
            status,
            json.dumps(payload).encode("utf-8"),
        )

    def calls_for(self, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["path"] == path]

    def assert_authorized(self, path: str, index: int = -1) -> str:
        calls = self.calls_for(path)
        assert calls, "no request recorded for " + path
        auth = calls[index]["headers"].get("Authorization", "")
        assert auth.startswith("Bearer "), "expected bearer auth"
        return auth


@pytest.fixture()
def fake_transport() -> FakeTransport:
    transport = FakeTransport()
    transport.json_route("/qx_assets", 200, load_fixture("assets.json"))
    transport.json_route("/qx_candles", 200, load_fixture("candles.json"))
    transport.json_route("/qx_history", 200, load_fixture("candles.json"))
    transport.json_route("/qx_saved", 200, load_fixture("saved.json"))
    transport.json_route("/qx_result", 200, load_fixture("result.json"))
    transport.routes["GET /qx_ss_pro_v2"] = (
        200,
        _PNG_MAGIC + b"0" * 64,
        {"content-type": "image/png"},
    )
    return transport


@pytest.fixture()
def client(fake_transport: FakeTransport) -> Client:
    return Client(
        api_key="ap_" + "0" * 32,
        transport=fake_transport,
        run_update_gate=False,
    )


@pytest.fixture()
def no_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no key resolves from the environment."""
    monkeypatch.delenv(_config.ENV_API_KEY, raising=False)
    for name in list(os.environ):
        if name.startswith(_config.ENV_KEY_PREFIX):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_update_gate() -> None:
    """Keep per-process update state isolated between tests."""
    update_module.reset_gate_state()
    yield
    update_module.reset_gate_state()


@pytest.fixture()
def update_cache_dir(tmp_path: Any) -> str:
    """A temporary cache dir for update tests."""
    return str(tmp_path / "cache")


class FakeClock:
    """Deterministic monotonic/time source for cache-TTL tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()
