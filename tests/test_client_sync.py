"""Sync client behaviour: every method, key resolution, caching, auth modes."""

from __future__ import annotations

import pytest

from alltradingapi.client import Client
from alltradingapi.config import ENV_API_KEY, ENV_KEY_PREFIX
from alltradingapi.errors import (
    AuthenticationError,
    ConfigurationError,
    RateLimitError,
    UpstreamError,
    ValidationError,
)
from alltradingapi.models import (
    Asset,
    Candle,
    SavedCandles,
    TradeResult,
)

from .conftest import FakeTransport


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------
def test_key_resolution_order(
    fake_transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    monkeypatch.delenv(ENV_KEY_PREFIX + "QUOTEX_ASSETS", raising=False)
    client = Client(
        api_key="ap_constructor",
        keys={"quotex-assets": "ap_product", "default": "ap_default"},
        transport=fake_transport,
        run_update_gate=False,
    )
    client.assets()
    auth = fake_transport.assert_authorized("/qx_assets")
    assert auth == "Bearer ap_product"  # product key beats default
    # candles has no product key in the mapping -> "default" beats the
    # constructor key
    client.candles("EURUSD_otc")
    auth = fake_transport.assert_authorized("/qx_candles")
    assert auth == "Bearer ap_default"


def test_key_resolution_env(monkeypatch: pytest.MonkeyPatch, fake_transport: FakeTransport) -> None:
    monkeypatch.setenv(ENV_API_KEY, "ap_envdefault")
    client = Client(transport=fake_transport, run_update_gate=False)
    client.assets(refresh=True)
    assert fake_transport.assert_authorized("/qx_assets") == "Bearer ap_envdefault"


def test_key_resolution_product_env(
    monkeypatch: pytest.MonkeyPatch, fake_transport: FakeTransport
) -> None:
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    monkeypatch.setenv(ENV_KEY_PREFIX + "QUOTEX_ASSETS", "ap_prodenv")
    client = Client(transport=fake_transport, run_update_gate=False)
    client.assets(refresh=True)
    assert fake_transport.assert_authorized("/qx_assets") == "Bearer ap_prodenv"


def test_no_key_raises_authentication_error(
    monkeypatch: pytest.MonkeyPatch, fake_transport: FakeTransport, no_key_env: None
) -> None:
    client = Client(transport=fake_transport, run_update_gate=False)
    with pytest.raises(AuthenticationError) as excinfo:
        client.assets(refresh=True)
    assert "ATA_API_KEY" in excinfo.value.message
    # The gate must run before any network call.
    assert fake_transport.calls == []


def test_per_call_key_wins(fake_transport: FakeTransport) -> None:
    client = Client(api_key="ap_base", transport=fake_transport, run_update_gate=False)
    client.assets(refresh=True, api_key="ap_override")
    assert fake_transport.assert_authorized("/qx_assets") == "Bearer ap_override"


def test_query_auth_mode(fake_transport: FakeTransport) -> None:
    client = Client(
        api_key="ap_q", auth_mode="query", transport=fake_transport, run_update_gate=False
    )
    client.assets(refresh=True)
    call = fake_transport.calls[-1]
    assert call["params"].get("key") == "ap_q"
    assert "Authorization" not in call["headers"]


def test_invalid_auth_mode_rejected() -> None:
    with pytest.raises(ConfigurationError):
        Client(api_key="x", auth_mode="cookie", run_update_gate=False)


# ---------------------------------------------------------------------------
# Endpoint methods
# ---------------------------------------------------------------------------
def test_assets(client: Client, fake_transport: FakeTransport) -> None:
    assets = client.assets()
    assert len(assets) == 3
    assert isinstance(assets[0], Asset)
    open_assets = client.assets(only_open=True)
    assert len(open_assets) == 2
    # Cached: only one real request so far.
    requests_before = len(fake_transport.calls)
    client.assets()
    assert len(fake_transport.calls) == requests_before
    client.assets(refresh=True)
    assert len(fake_transport.calls) == requests_before + 1


def test_assets_account_param(client: Client, fake_transport: FakeTransport) -> None:
    client.assets(account=1, refresh=True)
    call = fake_transport.calls[-1]
    assert call["params"].get("account") == "1"


def test_candles(client: Client, fake_transport: FakeTransport) -> None:
    candles = client.candles("EURUSD_otc", hours=3, timeframe="1m")
    assert len(candles) == 6
    assert isinstance(candles[0], Candle)
    call = fake_transport.calls[-1]
    assert call["params"]["asset"] == "EURUSD_otc"
    assert call["params"]["hours"] == "3"


def test_history_alias(client: Client, fake_transport: FakeTransport) -> None:
    candles = client.history("EURUSD_otc")
    assert len(candles) == 6
    assert fake_transport.calls[-1]["path"] == "/qx_history"


def test_saved(client: Client) -> None:
    saved = client.saved("EURUSD_otc", count=3)
    assert isinstance(saved, SavedCandles)
    assert saved.returned_count == 3


def test_screenshot(client: Client) -> None:
    png = client.screenshot("EURUSD_otc")
    assert png.startswith(b"\x89PNG")


def test_screenshot_wrong_content_type(fake_transport: FakeTransport) -> None:
    transport = fake_transport
    transport.routes["GET /qx_ss_pro_v2"] = (502, b'{"error": "feed down"}', {})
    client = Client(api_key="x", transport=transport, run_update_gate=False)
    with pytest.raises(UpstreamError):
        client.screenshot("EURUSD_otc")


def test_result(client: Client) -> None:
    result = client.result("EURUSD_otc", time="08:00", direction="call", mtg=1)
    assert isinstance(result, TradeResult)
    assert result.result is not None


def test_unknown_kwarg_never_sent(client: Client, fake_transport: FakeTransport) -> None:
    # Python's explicit method signature rejects unknown kwargs before any
    # request is made (TypeError is a client-side rejection per the contract).
    with pytest.raises(TypeError):
        client.candles("EURUSD_otc", bogus_param=1)  # type: ignore[call-arg]
    assert fake_transport.calls == []


def test_out_of_range_never_sent(client: Client, fake_transport: FakeTransport) -> None:
    with pytest.raises(ValidationError):
        client.candles("EURUSD_otc", hours=99)
    assert fake_transport.calls == []


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
def test_clear_cache(client: Client, fake_transport: FakeTransport) -> None:
    client.assets()
    client.assets()
    assert len(fake_transport.calls_for("/qx_assets")) == 1
    client.clear_cache()
    client.assets()
    assert len(fake_transport.calls_for("/qx_assets")) == 2


def test_cache_ttl_zero_disables(fake_transport: FakeTransport) -> None:
    client = Client(api_key="x", transport=fake_transport, cache_ttl=0.0, run_update_gate=False)
    client.assets()
    client.assets()
    assert len(fake_transport.calls_for("/qx_assets")) == 2


# ---------------------------------------------------------------------------
# Error mapping through the client
# ---------------------------------------------------------------------------
def test_client_maps_403(fake_transport: FakeTransport) -> None:
    fake_transport.json_route("/qx_assets", 403, {"error": "This key does not include that API."})
    client = Client(api_key="x", transport=fake_transport, run_update_gate=False)
    with pytest.raises(Exception) as excinfo:
        client.assets(refresh=True)
    assert "include" in str(excinfo.value)


def test_client_maps_429_with_retry(
    fake_transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_transport.routes["GET /qx_assets"] = (429, b'{"error": "rate"}', {})
    client = Client(api_key="x", transport=fake_transport, max_retries=0, run_update_gate=False)
    with pytest.raises(RateLimitError):
        client.assets(refresh=True)


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


def test_context_manager(fake_transport: FakeTransport) -> None:
    with Client(api_key="x", transport=fake_transport, run_update_gate=False) as ata:
        ata.assets(refresh=True)
    # close() is safe twice
    ata.close()


def test_from_env(monkeypatch: pytest.MonkeyPatch, fake_transport: FakeTransport) -> None:
    monkeypatch.setenv(ENV_API_KEY, "ap_env")
    client = Client.from_env(transport=fake_transport, run_update_gate=False)
    client.assets(refresh=True)
    assert fake_transport.assert_authorized("/qx_assets") == "Bearer ap_env"


def test_client_version_property(client: Client) -> None:
    assert client.version == "1.0.0"
