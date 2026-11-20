"""Async client: loop-offloaded I/O, six-endpoint surface, concurrent scan."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from alltradingapi.async_client import AsyncClient
from alltradingapi.models import Asset, SavedCandles, TradeResult

from .conftest import FakeTransport


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def _async_client(fake_transport: FakeTransport) -> AsyncClient:
    return AsyncClient(api_key="ap_" + "0" * 32, transport=fake_transport, run_update_gate=False)


def test_async_assets(fake_transport: FakeTransport) -> None:
    async def scenario() -> list[Asset]:
        async with _async_client(fake_transport) as ata:
            return await ata.assets(only_open=True)

    assets = _run(scenario())
    assert len(assets) == 2
    assert all(a.is_open for a in assets)


def test_async_full_surface(fake_transport: FakeTransport) -> None:
    async def scenario() -> None:
        async with _async_client(fake_transport) as ata:
            assert len(await ata.candles("EURUSD_otc")) == 6
            assert len(await ata.history("EURUSD_otc")) == 6
            saved = await ata.saved("EURUSD_otc")
            assert isinstance(saved, SavedCandles)
            assert saved.returned_count == 3
            png = await ata.screenshot("EURUSD_otc")
            assert png.startswith(b"\x89PNG")
            trade = await ata.result("EURUSD_otc", time="08:00", direction="call")
            assert isinstance(trade, TradeResult)
            assert trade.result is not None

    _run(scenario())


def test_async_io_does_not_block_loop(fake_transport: FakeTransport) -> None:
    """While a request runs in the executor the loop must stay responsive."""
    slow_transport = fake_transport

    def slow_request(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.2)
        return FakeTransport.request(slow_transport, *args, **kwargs)

    fake_transport.request = slow_request  # type: ignore[method-assign]

    async def scenario() -> None:
        async with _async_client(fake_transport) as ata:
            ticks: list[float] = []

            async def ticker() -> None:
                for _ in range(8):
                    ticks.append(time.monotonic())
                    await asyncio.sleep(0.05)

            async def call() -> None:
                await ata.assets(refresh=True)

            await asyncio.gather(ticker(), call())
            assert len(ticks) >= 3  # loop kept ticking during the request

    _run(scenario())


def test_async_scan_concurrency_and_failures(
    fake_transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATA_UPDATE_POLICY", "off")
    symbols = ["A" + str(i) for i in range(6)]

    # Odd-numbered assets fail server-side to prove per-asset isolation.
    fake_transport.routes["GET /qx_candles"] = lambda call: (
        (500, b'{"error": "boom"}', {})
        if dict(call["params"]).get("asset", "").endswith(("1", "3", "5"))
        else (
            200,
            __import__("json")
            .dumps(__import__("json").load(open("tests/fixtures/candles.json", encoding="utf-8")))
            .encode("utf-8"),
            {},
        )
    )

    async def scenario() -> Any:
        async with _async_client(fake_transport) as ata:
            return await ata.scan(symbols, concurrency=3)

    results = _run(scenario())
    assert set(results) == set(symbols)
    ok = [s for s, v in results.items() if not isinstance(v, Exception)]
    failed = [s for s, v in results.items() if isinstance(v, Exception)]
    assert len(ok) == 3 and len(failed) == 3


def test_async_from_env_and_close(monkeypatch: pytest.MonkeyPatch, fake_transport: Any) -> None:
    from alltradingapi.async_client import AsyncClient
    from alltradingapi.config import ENV_API_KEY

    monkeypatch.setenv(ENV_API_KEY, "ap_env")

    async def scenario() -> None:
        client = AsyncClient.from_env(transport=fake_transport, run_update_gate=False)
        assert client.version == "1.0.0"
        await client.close()
        await client.close()  # idempotent

    _run(scenario())
