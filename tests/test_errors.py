"""Error hierarchy: mapping, attributes, redaction."""

from __future__ import annotations

import pytest

from alltradingapi.errors import (
    ALLError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    TimeoutError_,
    TransportError,
    UpstreamError,
    ValidationError,
    error_from_response,
)
from alltradingapi.utils import redact_url


def test_401_message_is_actionable() -> None:
    error = error_from_response(
        401, b'{"error": "A valid API key is required."}', "/qx_candles?key=ap_secret"
    )
    assert isinstance(error, AuthenticationError)
    assert "api_key" in error.message or "ATA_API_KEY" in error.message
    assert "ap_secret" not in (error.url or "")
    assert error.status == 401


def test_403_permission() -> None:
    error = error_from_response(
        403, b'{"error": "This key does not include that API."}', "/qx_candles"
    )
    assert isinstance(error, PermissionDeniedError)
    assert "product" in error.message


def test_400_validation() -> None:
    error = error_from_response(
        400, b'{"error": "That parameter is not available on this API."}', "/qx_candles"
    )
    assert isinstance(error, ValidationError)
    assert "parameter" in error.message


def test_404_not_found() -> None:
    error = error_from_response(404, b"{}", "/api/candles.php")
    assert isinstance(error, NotFoundError)


def test_429_rate_limit() -> None:
    error = error_from_response(
        429, b'{"error": "Rate limit reached."}', "/qx_candles", headers={"Retry-After": "1"}
    )
    assert isinstance(error, RateLimitError)
    assert error.retry_after == 1.0


def test_500_server_and_502_upstream() -> None:
    assert isinstance(error_from_response(500, b"{}", "/x"), ServerError)
    upstream = error_from_response(502, b"{}", "/x")
    assert isinstance(upstream, UpstreamError)
    assert isinstance(upstream, ServerError)  # subclass relationship holds
    assert isinstance(error_from_response(503, b"{}", "/x"), ServerError)


def test_non_json_body_falls_back() -> None:
    error = error_from_response(500, b"<html>oops</html>", "/x")
    assert isinstance(error, ServerError)
    assert "500" in error.message


def test_unexpected_status() -> None:
    error = error_from_response(418, b"{}", "/x")
    assert isinstance(error, ALLError)
    assert error.status == 418


def test_to_dict_roundtrip() -> None:
    error = error_from_response(429, b'{"error": "Rate limit reached."}', "/qx_candles")
    data = error.to_dict()
    assert data["status"] == 429
    assert data["error"] == "rate_limited"
    assert "message" in data


def test_key_never_in_messages() -> None:
    key = "ap_" + "f" * 32
    error = error_from_response(
        401, b'{"error": "A valid API key is required."}', "/qx_candles?key=" + key
    )
    assert key not in error.message
    assert key not in repr(error)
    assert key not in str(error.to_dict())


def test_transport_and_timeout_codes() -> None:
    assert TransportError("net down").code == "transport_error"
    assert TimeoutError_("slow").code == "timeout"


def test_redact_url_variants() -> None:
    assert redact_url(None) is None
    assert redact_url("") == ""
    assert "ap_" + "f" * 32 not in redact_url("https://x.com/q?asset=A&key=ap_" + "f" * 32)
    assert "secrettoken" not in redact_url("https://x.com/q?token=secrettoken")
    assert "sekret" not in redact_url("https://x.com/q?api_key=sekret")


def test_transport_error_raised_by_transport() -> None:
    from alltradingapi.transport import Transport

    transport = Transport("https://alltradingapi.invalid-host-test-xyz", max_retries=0)
    with pytest.raises((TransportError, TimeoutError_)):
        transport.request("GET", "/qx_timeframes", timeout=1.0)


def test_error_payload_stored() -> None:
    error = error_from_response(400, b'{"error": "bad param", "detail": "x"}', "/q")
    assert error.payload.get("detail") == "x"
