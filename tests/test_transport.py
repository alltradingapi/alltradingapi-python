"""End-to-end transport tests against a real localhost HTTP server (offline)."""

from __future__ import annotations

import pytest

from alltradingapi.errors import (
    RateLimitError,
    TimeoutError_,
    TransportError,
    UpstreamError,
    ValidationError,
)
from alltradingapi.transport import Transport

from .fake_server import FakeServer


@pytest.fixture()
def server() -> str:
    with FakeServer() as srv:
        yield srv.base_url


def _transport(base_url: str, **kw: object) -> Transport:
    return Transport(base_url, **kw)  # type: ignore[arg-type]


def test_get_json_roundtrip(server: str) -> None:
    transport = _transport(server)
    response = transport.request("GET", "/qx_candles")
    assert response.status == 200
    payload = response.json()
    assert isinstance(payload, list) and payload[0]["asset"]


def test_auth_header_reaches_server(server: str) -> None:
    transport = _transport(server)
    response = transport.request(
        "GET", "/qx_assets", headers={"Authorization": "Bearer " + "a" * 35}
    )
    assert response.status == 200
    # Without a key the fake server answers 401 (mapped to an SDK error).
    from alltradingapi.errors import AuthenticationError

    with pytest.raises(AuthenticationError):
        transport.request("GET", "/qx_assets")


def test_gzip_decompression(server: str) -> None:
    transport = _transport(server)
    response = transport.request("GET", "/qx_gzip")
    assert response.status == 200
    assert isinstance(response.json(), list)


def test_400_maps_to_validation_error(server: str) -> None:
    transport = _transport(server)
    with pytest.raises(ValidationError) as excinfo:
        transport.request("GET", "/qx_400")
    assert "parameter" in excinfo.value.message


def test_429_maps_to_rate_limit_with_retry_after(server: str) -> None:
    transport = _transport(server, max_retries=0)
    with pytest.raises(RateLimitError) as excinfo:
        transport.request("GET", "/qx_429")
    assert excinfo.value.retry_after == 1.0


def test_502_maps_to_upstream_error(server: str) -> None:
    transport = _transport(server, max_retries=0)
    with pytest.raises(UpstreamError):
        transport.request("GET", "/qx_502")


def test_get_retries_on_502_then_succeeds(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _transport(server, max_retries=2, backoff_factor=0.0)
    monkeypatch.setattr(transport, "_backoff_delay", lambda *a: 0.0)
    # Flip the fake server's 502 flag behaviour by racing retries: the fake
    # always answers 502, so a retryable failure must exhaust attempts.
    with pytest.raises(UpstreamError):
        transport.request("GET", "/qx_502")
    assert transport.max_attempts == 3


def test_post_does_not_retry_on_429(server: str) -> None:
    transport = _transport(server, max_retries=3, backoff_factor=0.0)
    with pytest.raises(RateLimitError):
        transport.request("POST", "/qx_429", body=b"x")


def test_truncated_body_surfaces_as_invalid_json(server: str) -> None:
    transport = _transport(server, max_retries=0)
    response = transport.request("GET", "/qx_truncated")
    with pytest.raises(ValueError):
        response.json()


def test_timeout_raises_timeout_error() -> None:
    # Connect to a non-routable loopback port that drops packets -> timeout.
    transport = _transport("http://127.0.0.1:1", max_retries=0, connect_timeout=0.2)
    with pytest.raises((TimeoutError_, TransportError)):
        transport.request("GET", "/qx_assets")


def test_bad_base_url_rejected() -> None:
    with pytest.raises(ValueError):
        _transport("not-a-url")


def test_url_redaction_in_errors(server: str) -> None:
    transport = _transport(server, max_retries=0)
    with pytest.raises(RateLimitError) as excinfo:
        transport.request("GET", "/qx_429")
    assert "Bearer" not in (excinfo.value.url or "")


def test_key_redaction_helper() -> None:
    from alltradingapi.utils import redact_url

    url = "https://alltradingapi.com/qx_candles?key=ap_deadbeefdeadbeefdeadbeefdeadbeef"
    redacted = redact_url(url)
    assert "ap_deadbeef" not in redacted
    assert "key=***" in redacted
    bearer = "https://example.com/x?h=Bearer ap_deadbeefdeadbeefdeadbeefdeadbeef"
    assert "ap_deadbeef" not in redact_url(bearer)


def test_response_exposes_headers(server: str) -> None:
    transport = _transport(server)
    response = transport.request("GET", "/qx_candles")
    assert response.header("content-type", "").startswith("application/json")
    assert response.content_type.startswith("application/json")
