"""Async client — same six endpoints as :class:`Client`, loop-offloaded.

Implementation: a bounded :class:`~concurrent.futures.ThreadPoolExecutor`
around the same sync transport, so there is exactly one HTTP code path to
audit and the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from . import config as _config
from ._version import __version__
from .client import Client, ImageLike  # noqa: F401 - re-exported type
from .models import Asset, Candle, SavedCandles, TradeResult

__all__ = ["AsyncClient"]


class AsyncClient:
    """Async SDK client wrapping :class:`~alltradingapi.client.Client`.

    Example::

        from alltradingapi import AsyncClient

        async with AsyncClient(api_key="ap_...") as ata:
            assets = await ata.assets(only_open=True)
            candles = await ata.candles("EURUSD_otc")
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        keys: Mapping[str, str] | None = None,
        max_concurrency: int = 8,
        run_update_gate: bool = True,
        **client_kwargs: Any,
    ) -> None:
        self._sync = Client(
            api_key=api_key,
            keys=keys,
            run_update_gate=run_update_gate,
            **client_kwargs,
        )
        self._max_concurrency = max(1, int(max_concurrency))
        self._executor: ThreadPoolExecutor | None = None

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def from_env(cls, **kwargs: Any) -> AsyncClient:
        """Build from environment configuration, mirroring ``Client.from_env``."""
        kwargs.setdefault("api_key", os.environ.get(_config.ENV_API_KEY) or None)
        return cls(**kwargs)

    def _get_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_concurrency,
                thread_name_prefix="alltradingapi",
            )
        return self._executor

    async def _run(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        method = getattr(self._sync, method_name)
        return await loop.run_in_executor(
            self._get_executor(),
            lambda: method(*args, **kwargs),
        )

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Shut the executor down and release pooled connections."""
        if self._executor is not None:
            executor = self._executor
            self._executor = None
            await asyncio.get_event_loop().run_in_executor(None, executor.shutdown)
        self._sync.close()

    @property
    def version(self) -> str:
        """Installed SDK version string."""
        return __version__

    def check_updates(self, force: bool = False) -> Any:
        """Sync passthrough to :meth:`Client.check_updates` (network-light)."""
        return self._sync.check_updates(force=force)

    def on_update_available(self, callback: Any) -> None:
        """Register the update-available hook on the underlying client."""
        self._sync.on_update_available(callback)

    def clear_cache(self) -> None:
        """Drop cached metadata responses."""
        self._sync.clear_cache()

    # -- endpoint surface ---------------------------------------------------
    async def assets(
        self,
        *,
        only_open: bool = False,
        refresh: bool = False,
        account: int | None = None,
        api_key: str | None = None,
    ) -> list[Asset]:
        """List assets (see :meth:`Client.assets`)."""
        return list(
            await self._run(
                "assets",
                only_open=only_open,
                refresh=refresh,
                account=account,
                api_key=api_key,
            )
        )

    async def candles(self, asset: str, **kwargs: Any) -> list[Candle]:
        """Fetch candles (see :meth:`Client.candles`)."""
        return list(await self._run("candles", asset, **kwargs))

    async def history(self, asset: str, **kwargs: Any) -> list[Candle]:
        """Fetch candle history (see :meth:`Client.history`)."""
        return list(await self._run("history", asset, **kwargs))

    async def saved(self, asset: str, **kwargs: Any) -> SavedCandles:
        """Fetch saved candles (see :meth:`Client.saved`)."""
        return cast(SavedCandles, await self._run("saved", asset, **kwargs))

    async def screenshot(self, asset: str, **kwargs: Any) -> bytes:
        """Download a chart screenshot (see :meth:`Client.screenshot`)."""
        return cast(bytes, await self._run("screenshot", asset, **kwargs))

    async def result(self, asset: str, **kwargs: Any) -> TradeResult:
        """Check a trade result (see :meth:`Client.result`)."""
        return cast(TradeResult, await self._run("result", asset, **kwargs))

    # -- fan-out -------------------------------------------------------------
    async def scan(
        self,
        assets: Iterable[str],
        *,
        concurrency: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch candles for many assets concurrently; per-asset failures
        never abort. Returns ``{asset: List[Candle] | Exception}``.
        """
        symbols: list[str] = list(assets)
        limit = min(self._max_concurrency, concurrency or self._max_concurrency)
        semaphore = asyncio.Semaphore(max(1, limit))
        results: dict[str, Any] = {}

        async def _one(symbol: str) -> tuple[str, Any]:
            async with semaphore:
                try:
                    return symbol, await self._run("candles", symbol, **kwargs)
                except Exception as exc:  # surfaced per asset, never raised
                    return symbol, exc

        done = await asyncio.gather(*(_one(s) for s in symbols))
        for symbol, value in done:
            results[symbol] = value
        return results
