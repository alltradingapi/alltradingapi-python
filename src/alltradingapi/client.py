"""The synchronous :class:`Client` — typed access to the six v1 endpoints.

Endpoints covered:

* ``assets()``      -> ``/qx_assets``      (asset data)
* ``candles()``     -> ``/qx_candles``     (current candles)
* ``history()``     -> ``/qx_history``     (candle history)
* ``saved()``       -> ``/qx_saved``       (saved candles)
* ``screenshot()``  -> ``/qx_ss_pro_v2``   (chart image, PNG bytes)
* ``result()``      -> ``/qx_result``      (trade result check)

Key resolution order (first hit wins):

1. explicit per-call ``api_key``
2. ``keys[product]`` (product id such as ``"quotex-quotes"``)
3. ``keys["default"]``
4. constructor ``api_key``
5. environment ``ATA_API_KEY``
6. environment ``ATA_KEY_<PRODUCT_UPPER_SNAKE>`` (e.g. ``ATA_KEY_QUOTEX_QUOTES``)

If nothing resolves, calls raise :class:`AuthenticationError` with an
actionable message — never a bare ``KeyError``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from typing import (
    Any,
    Union,
)

from . import config as _config
from . import update as _update
from ._version import __version__
from .cache import TTLCache
from .errors import ALLError, AuthenticationError, ConfigurationError
from .logging_ import logger
from .models import Asset, Candle, SavedCandles, TradeResult
from .params import validate_params
from .transport import Response, Transport
from .utils import redact_url

__all__ = ["Client", "ApiKey"]

# Public alias: an API key is just a string (``ap_`` + 32 hex chars).
ApiKey = str

ImageLike = Union[bytes, str, "os.PathLike[str]"]

_ROUTES: dict[str, str] = {
    "assets": "/qx_assets",
    "candles": "/qx_candles",
    "history": "/qx_history",
    "saved": "/qx_saved",
    "screenshot": "/qx_ss_pro_v2",
    "result": "/qx_result",
}

# Which product id (and therefore which key) each call needs.
_PRODUCTS: dict[str, str] = {
    "assets": "quotex-assets",
    "candles": "quotex-quotes",
    "history": "quotex-realtime",
    "saved": "quotex-saved",
    "screenshot": "quotex-screenshots",
    "result": "quotex-results",
}


class Client:
    """Synchronous SDK client for the All Trading API.

    Example::

        from alltradingapi import Client

        with Client(api_key="ap_...") as ata:
            candles = ata.candles("EURUSD_otc")
            print(candles[-1].close)
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        keys: Mapping[str, str] | None = None,
        base_url: str = _config.BASE_URL,
        timeout: float = _config.DEFAULT_TIMEOUT,
        connect_timeout: float = _config.DEFAULT_CONNECT_TIMEOUT,
        max_retries: int = _config.DEFAULT_MAX_RETRIES,
        backoff_factor: float = _config.DEFAULT_BACKOFF_FACTOR,
        retry_statuses: Sequence[int] = _config.DEFAULT_RETRY_STATUSES,
        user_agent: str | None = None,
        auth_mode: str = _config.DEFAULT_AUTH_MODE,
        cache_ttl: float = _config.DEFAULT_CACHE_TTL,
        update_policy: Any | None = None,
        allow_outdated: bool = False,
        interactive: bool | None = None,
        verify: bool = True,
        logger: logging.Logger | None = None,  # noqa: A002 - spec parity
        transport: Transport | None = None,
        run_update_gate: bool = True,
    ) -> None:
        if auth_mode not in ("bearer", "query"):
            raise ConfigurationError(
                "auth_mode must be 'bearer' (recommended: keeps keys out of "
                "URLs and logs) or 'query'."
            )
        self._keys: dict[str, str] = {}
        if keys:
            for product, key in keys.items():
                if not key:
                    continue
                self._keys[str(product)] = str(key)
        if api_key:
            self._keys["__constructor__"] = api_key
        self._auth_mode = auth_mode
        self._cache_ttl = float(cache_ttl)
        self._cache = TTLCache(ttl=self._cache_ttl)
        self._update_policy = update_policy
        self._allow_outdated = allow_outdated
        self._interactive = interactive
        self._logger = logger or logging.getLogger("alltradingapi")
        self._update_hooks: list[Callable[[Any], None]] = []
        self._closed = False

        self._transport = transport or Transport(
            base_url=base_url,
            connect_timeout=connect_timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            retry_statuses=retry_statuses,
            verify=verify,
            user_agent=user_agent or _config.DEFAULT_USER_AGENT,
        )

        if run_update_gate:
            self._run_update_gate()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, **kwargs: Any) -> Client:
        """Build a client from environment configuration (ATA_* variables)."""
        kwargs.setdefault("api_key", os.environ.get(_config.ENV_API_KEY) or None)
        return cls(**kwargs)

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        """Release pooled connections. Safe to call multiple times."""
        if not self._closed:
            self._transport.close()
            self._closed = True

    # ------------------------------------------------------------------
    # Update gate & info
    # ------------------------------------------------------------------
    def _run_update_gate(self) -> None:
        try:
            info = _update.ensure_usable(
                self.version,
                policy=self._update_policy,
                allow_outdated=self._allow_outdated,
                interactive=self._interactive,
                installer=None,
            )
        except ALLError:
            raise
        except Exception as exc:  # fail-open: the gate must never crash the SDK
            logger.debug("update gate skipped: %s", exc)
            return
        if info is not None and info.is_outdated:
            for hook in self._update_hooks:
                try:
                    hook(info)
                except Exception:  # pragma: no cover - user callback errors
                    logger.debug("update hook raised", exc_info=True)

    def on_update_available(self, callback: Callable[[Any], None]) -> None:
        """Register a callback receiving :class:`~alltradingapi.update.UpdateInfo`."""
        self._update_hooks.append(callback)

    def check_updates(self, force: bool = False) -> Any:
        """Return :class:`~alltradingapi.update.UpdateInfo`; never raises for network issues."""
        try:
            return _update.check_for_update(force=force, installed=self.version)
        except ALLError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("update check failed: %s", exc)
            return _update.UpdateInfo(
                installed=self.version,
                latest=None,
                is_outdated=False,
                checked_at=0.0,
                source="error",
                known=False,
            )

    @property
    def version(self) -> str:
        """Installed SDK version string."""
        return __version__

    # ------------------------------------------------------------------
    # Key resolution
    # ------------------------------------------------------------------
    def _resolve_key(self, call: str, api_key: str | None) -> str:
        if api_key:
            return api_key
        product = _PRODUCTS.get(call)
        if product and product in self._keys:
            return self._keys[product]
        if "default" in self._keys:
            return self._keys["default"]
        if "__constructor__" in self._keys:
            return self._keys["__constructor__"]
        env_default = os.environ.get(_config.ENV_API_KEY)
        if env_default:
            return env_default
        if product:
            env_product = os.environ.get(_config.ENV_KEY_PREFIX + product.upper().replace("-", "_"))
            if env_product:
                return env_product
        raise AuthenticationError(
            "No API key available for '{}'. Pass api_key=..., build the client "
            "with keys={{'{}': 'ap_...'}}, or set the environment variable "
            "ATA_API_KEY (or ATA_KEY_{}). Each product needs its own key "
            "from https://alltradingapi.com.".format(
                call,
                product or "default",
                (product or "DEFAULT").upper().replace("-", "_"),
            )
        )

    def _request(
        self,
        call: str,
        method: str = "GET",
        params: list[tuple[str, str]] | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> Response:
        key = self._resolve_key(call, api_key)
        auth_headers = dict(headers or {})
        query: list[tuple[str, str]] = list(params or [])
        if self._auth_mode == "bearer":
            auth_headers["Authorization"] = "Bearer " + key
        else:
            query.append(("key", key))
        path = _ROUTES[call]
        return self._transport.request(
            method,
            path,
            params=query,
            body=body,
            headers=auth_headers,
            timeout=timeout,
        )

    def _cached_json(self, cache_key: tuple[Any, ...], call: str, **kw: Any) -> Any:
        if not kw.get("refresh"):
            hit = self._cache.get(cache_key)
            if hit is not None:
                return hit
        response = self._request(call, **{k: v for k, v in kw.items() if k != "refresh"})
        payload = response.json()
        self._cache.put(cache_key, payload)
        return payload

    def clear_cache(self) -> None:
        """Drop all client-side cached responses."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Asset data
    # ------------------------------------------------------------------
    def assets(
        self,
        *,
        only_open: bool = False,
        refresh: bool = False,
        account: int | None = None,
        api_key: str | None = None,
    ) -> list[Asset]:
        """List every tradable asset with open/closed state and payouts.

        Args:
            only_open: Return only assets currently open for trading.
            refresh: Bypass the client-side TTL cache.
            account: Optional account selector (server-defined values).
        """
        params = validate_params("assets", account=account)
        key = ("assets", tuple(params), bool(refresh), self._auth_mode)
        payload = self._cached_json(key, "assets", params=params, api_key=api_key, refresh=refresh)
        rows = payload if isinstance(payload, list) else []
        assets = Asset.from_rows(rows)
        if only_open:
            assets = [a for a in assets if a.is_open]
        return assets

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------
    def candles(
        self,
        asset: str,
        *,
        hours: int | None = None,
        timeframe: str | None = None,
        timezone: str | None = None,  # noqa: A002 - server parameter name
        offset: int | None = None,
        timestamp: int | None = None,
        period: int | None = None,
        account: int | None = None,
        timeout: int | None = None,
        verbose_logging: bool | None = None,
        api_key: str | None = None,
    ) -> list[Candle]:
        """Fetch recent candles for *asset* (default ~2h of 1m bars).

        Args:
            asset: Symbol such as ``EURUSD_otc``.
            hours: 1..24 hours of history (server default 2).
            timeframe: One of 5s..4h (server default 1m).
            timezone: e.g. ``-3``, ``UTC``, ``Asia/Dhaka`` (default Asia/Dhaka).
        """
        return self._candles(
            "candles",
            asset,
            hours=hours,
            timeframe=timeframe,
            timezone=timezone,
            offset=offset,
            timestamp=timestamp,
            period=period,
            account=account,
            timeout=timeout,
            verbose_logging=verbose_logging,
            api_key=api_key,
        )

    def history(
        self,
        asset: str,
        *,
        hours: int | None = None,
        timeframe: str | None = None,
        timezone: str | None = None,  # noqa: A002
        offset: int | None = None,
        timestamp: int | None = None,
        period: int | None = None,
        account: int | None = None,
        timeout: int | None = None,
        verbose_logging: bool | None = None,
        api_key: str | None = None,
    ) -> list[Candle]:
        """Fetch candle history for *asset* (same parameters as candles)."""
        return self._candles(
            "history",
            asset,
            hours=hours,
            timeframe=timeframe,
            timezone=timezone,
            offset=offset,
            timestamp=timestamp,
            period=period,
            account=account,
            timeout=timeout,
            verbose_logging=verbose_logging,
            api_key=api_key,
        )

    def _candles(self, call: str, asset: str, **kw: Any) -> list[Candle]:
        params = validate_params(
            call,
            asset=asset,
            **{k: v for k, v in kw.items() if k != "api_key"},
        )
        response = self._request(
            call,
            params=params,
            api_key=kw.get("api_key"),
            timeout=_effective_timeout(kw.get("timeout")),
        )
        rows = response.json()
        return Candle.from_rows(rows if isinstance(rows, list) else [])

    def saved(
        self,
        asset: str,
        *,
        timeframe: str | None = None,
        count: int | None = None,
        timezone: str | None = None,  # noqa: A002
        raw: bool | None = None,
        nocache: bool = False,
        api_key: str | None = None,
    ) -> SavedCandles:
        """Fetch server-side saved candles (scanned from the 5s archive)."""
        params = validate_params(
            "saved",
            asset=asset,
            timeframe=timeframe,
            timezone=timezone,
            count=count,
            raw=raw,
            nocache=nocache,
        )
        response = self._request("saved", params=params, api_key=api_key)
        return SavedCandles.from_payload(response.json())

    def screenshot(
        self,
        asset: str,
        *,
        timeframe: str | None = None,
        timezone: str | None = None,  # noqa: A002
        time: str | None = None,  # noqa: A002
        result: str | None = None,
        names: str | None = None,
        api_key: str | None = None,
    ) -> bytes:
        """Download a chart screenshot (PNG bytes) for *asset*.

        Returns raw PNG bytes; save with ``open(path, "wb")``.
        """
        params = validate_params(
            "screenshot",
            asset=asset,
            timeframe=timeframe,
            timezone=timezone,
            time=time,
            result=result,
            names=names,
        )
        response = self._request("screenshot", params=params, api_key=api_key)
        if not response.content_type.startswith("image/"):
            raise ALLError(
                "Expected an image from /qx_ss_pro_v2 but got Content-Type "
                f"'{response.content_type}' (status {response.status}). The server may have returned an error; "
                "check the asset/time parameter values.",
                status=response.status,
                url=redact_url(response.url),
            )
        return response.content

    def result(
        self,
        asset: str,
        *,
        time: str,  # noqa: A002 - server parameter name
        direction: str,
        timeframe: str | None = None,
        timezone: str | None = None,  # noqa: A002
        mtg: int | None = None,
        api_key: str | None = None,
    ) -> TradeResult:
        """Check a past trade entry (call/put at HH:MM) and its outcome(s)."""
        params = validate_params(
            "result",
            asset=asset,
            time=time,
            direction=direction,
            timeframe=timeframe,
            timezone=timezone,
            mtg=mtg,
        )
        response = self._request("result", params=params, api_key=api_key)
        return TradeResult(response.json())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _effective_timeout(param_timeout: int | None) -> float | None:
    """The server-side 'timeout' param hints at feed latency; pad for transport."""
    if param_timeout is None:
        return None
    return float(param_timeout) + 5.0


__all__ += ["ImageLike"]
