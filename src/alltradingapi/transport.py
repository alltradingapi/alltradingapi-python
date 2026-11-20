"""HTTP transport built on :mod:`http.client` — zero dependencies.

Design notes:

* Per-thread keep-alive connection pool (bounded idle connections per host).
* ``Accept-Encoding: gzip, deflate`` with transparent decompression.
* Explicit connect + read timeouts; no redirects (the API has none).
* Bounded retries with exponential backoff + **full jitter** on 429/5xx and
  transient socket errors; honours ``Retry-After``.
* GET retries freely (idempotent); POST retries at most once and only on 5xx.
* Response size cap (32 MiB) so a hostile response cannot exhaust memory.
* TLS via :func:`ssl.create_default_context`; ``verify=False`` is an explicit
  opt-in for local testing only.
* Optional proxy via ``ATA_HTTP_PROXY`` / ``ATA_HTTPS_PROXY``.
* API keys are redacted from URLs in logs and errors, always.
"""

from __future__ import annotations

import gzip
import http.client
import random
import socket
import ssl
import threading
import time
import zlib
from collections.abc import Sequence
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

from . import config as _config
from .errors import (
    ALLError,
    ServerError,
    TimeoutError_,
    TransportError,
    error_from_response,
)
from .logging_ import http_debug_line, logger
from .utils import redact_url

__all__ = ["Response", "Transport", "RETRYABLE_SOCKET_ERRORS"]

RETRYABLE_SOCKET_ERRORS = (socket.timeout, ConnectionError, OSError)
_MAX_REDIRECTS = 0  # the API does not redirect; we do not follow any


class Response:
    """A small, dependency-free HTTP response object."""

    __slots__ = ("status", "headers", "_body", "url", "elapsed_ms")

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        body: bytes,
        url: str,
        elapsed_ms: float,
    ) -> None:
        self.status = status
        self.headers = headers
        self._body = body
        self.url = url
        self.elapsed_ms = elapsed_ms

    @property
    def text(self) -> str:
        """Body decoded as UTF-8 with ``errors="replace"``."""
        return self._body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """Parse the body as JSON (raises ValueError on non-JSON bodies)."""
        import json

        return json.loads(self.text)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    @property
    def content(self) -> bytes:
        """Raw body bytes."""
        return self._body

    def header(self, name: str, default: str | None = None) -> str | None:
        """Case-insensitive header lookup."""
        return self.headers.get(name.lower(), default)


class Transport:
    """Low-level HTTP client used by :class:`~alltradingapi.client.Client`."""

    def __init__(
        self,
        base_url: str = _config.BASE_URL,
        *,
        connect_timeout: float = _config.DEFAULT_CONNECT_TIMEOUT,
        max_retries: int = _config.DEFAULT_MAX_RETRIES,
        backoff_factor: float = _config.DEFAULT_BACKOFF_FACTOR,
        retry_statuses: Sequence[int] = _config.DEFAULT_RETRY_STATUSES,
        verify: bool = True,
        user_agent: str = _config.DEFAULT_USER_AGENT,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https"):
            raise ValueError("base_url must start with http:// or https://")
        self.base_scheme = parts.scheme
        self.host = parts.hostname or ""
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.prefix = parts.path.rstrip("/")
        self.connect_timeout = connect_timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff_factor = max(0.0, float(backoff_factor))
        self.retry_statuses = tuple(retry_statuses)
        self.verify = verify
        self.user_agent = user_agent

        self._local = threading.local()
        self._ssl_context: ssl.SSLContext | None = None
        self._proxy_host, self._proxy_port = self._resolve_proxy()

    # -- connection management ------------------------------------------
    def _resolve_proxy(self) -> tuple[str, int]:
        """honour ATA_HTTP_PROXY / ATA_HTTPS_PROXY (host:port)."""
        raw = _config.env(_config.ENV_HTTPS_PROXY) or _config.env(_config.ENV_HTTP_PROXY)
        if not raw:
            return "", 0
        raw = raw.split("://")[-1].rstrip("/")
        if ":" in raw:
            host, _, port_s = raw.partition(":")
            try:
                return host, int(port_s)
            except ValueError:
                return "", 0
        return raw, 80

    def _ssl(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            if self.verify:
                ctx = ssl.create_default_context()
            else:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self._ssl_context = ctx
        return self._ssl_context

    def _pool_key(self) -> tuple[str, str, int]:
        host = self._proxy_host or self.host
        scheme = "http" if self._proxy_host and self.base_scheme == "http" else self.base_scheme
        port = self._proxy_port or self.port
        return (scheme, host, port)

    def _connection(self) -> http.client.HTTPConnection:
        pool = cast(
            dict[tuple[str, str, int], http.client.HTTPConnection] | None,
            getattr(self._local, "pool", None),
        )
        if pool is None:
            pool = {}
            self._local.pool = pool
        key = self._pool_key()
        conn = pool.get(key)
        if conn is not None:
            sock = getattr(conn, "sock", None)
            if sock is None:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
        if conn is None:
            if key[0] == "https":
                conn = http.client.HTTPSConnection(
                    key[1], key[2], timeout=self.connect_timeout, context=self._ssl()
                )
            else:
                conn = http.client.HTTPConnection(key[1], key[2], timeout=self.connect_timeout)
            if self._proxy_host and self.base_scheme == "https":
                conn.set_tunnel(self.host, self.port)
            pool[key] = conn
        return conn

    def _discard_connection(self) -> None:
        pool: dict[Any, http.client.HTTPConnection] = getattr(self._local, "pool", {})
        key = self._pool_key()
        conn = pool.pop(key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # -- public API ------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        params: list[tuple[str, str]] | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Response:
        """Perform *method* against *path* and return a :class:`Response`.

        Raises SDK errors on final HTTP failure states (mapped by
        :func:`errors.error_from_response`) and SDK transport errors on
        network-level problems. Retries are applied internally.
        """
        method = method.upper()
        read_timeout = timeout if timeout is not None else _config.DEFAULT_TIMEOUT
        extra_headers = {k.lower(): v for k, v in (headers or {}).items()}
        url = self._build_url(path, params)

        attempt = 0
        max_attempts = self.max_retries + 1
        post_retries_used = 0
        last_error: Exception | None = None

        while attempt < max_attempts:
            attempt += 1
            time.monotonic()
            try:
                response = self._send_once(method, url, extra_headers, body, read_timeout)
            except (TimeoutError, http.client.HTTPException, *RETRYABLE_SOCKET_ERRORS) as exc:
                last_error = exc
                self._discard_connection()
                if self._should_retry_socket(method, attempt, post_retries_used):
                    post_retries_used += 1
                    self._sleep_backoff(attempt)
                    continue
                raise self._network_error(exc, url) from None

            status = response.status
            logger.debug(
                http_debug_line(
                    method, url, status=status, elapsed_ms=response.elapsed_ms, attempt=attempt
                )
            )
            if status < 400:
                return response

            # Error statuses are mapped to SDK errors; some are retried.
            if self._should_retry_status(method, status, attempt, post_retries_used):
                post_retries_used += 1
                last_error = None
                self._sleep_backoff(attempt, response.header("retry-after"))
                continue

            error = error_from_response(status, response.content, url, headers=response.headers)
            raise error

        if last_error is not None:
            raise self._network_error(last_error, url)
        # Unreachable in practice; defensive.
        raise ServerError(  # pragma: no cover
            f"Request failed after {max_attempts} attempts.", url=redact_url(url)
        )

    # -- internals --------------------------------------------------------
    def _build_url(self, path: str, params: list[tuple[str, str]] | None) -> str:
        url = self.prefix + path
        if params:
            url = url + "?" + urlencode(params)
        return url

    def _send_once(
        self,
        method: str,
        url: str,
        extra_headers: dict[str, str],
        body: bytes | None,
        read_timeout: float,
    ) -> Response:
        headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json, image/png, */*",
            "Connection": "keep-alive",
        }
        headers.update(extra_headers)
        if body is not None and "content-length" not in headers:
            headers["Content-Length"] = str(len(body))
            headers.setdefault("Content-Type", "application/octet-stream")

        conn = self._connection()
        conn.timeout = read_timeout
        started = time.monotonic()
        conn.request(method, url, body=body, headers=headers)
        raw = conn.getresponse()
        data = self._read_bounded(raw)
        elapsed = (time.monotonic() - started) * 1000.0

        header_dict: dict[str, str] = {}
        for name, value in raw.getheaders():
            header_dict[name.lower()] = value

        encoding = header_dict.get("content-encoding", "").lower()
        if encoding == "gzip" and data[:2] == b"\x1f\x8b":
            try:
                data = gzip.decompress(data)
            except (OSError, EOFError, zlib.error):
                pass
        elif encoding == "deflate":
            try:
                data = zlib.decompress(data)
            except zlib.error:
                try:
                    data = zlib.decompress(data, -zlib.MAX_WBITS)
                except zlib.error:
                    pass

        response_headers = dict(header_dict)
        # Expose the *decompressed* length so callers can reason about size.
        response_headers["content-length"] = str(len(data))
        return Response(
            status=raw.status,
            headers=response_headers,
            body=data,
            url=url,
            elapsed_ms=elapsed,
        )

    def _read_bounded(self, raw: http.client.HTTPResponse) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = raw.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _config.MAX_RESPONSE_BYTES:
                raise TransportError(
                    f"Response exceeded the {_config.MAX_RESPONSE_BYTES // (1024 * 1024)} MiB safety cap; refusing to load "
                    "it into memory."
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _should_retry_socket(self, method: str, attempt: int, post_retries_used: int) -> bool:
        if method == "POST":
            return post_retries_used < 1
        return attempt < self.max_attempts

    def _should_retry_status(
        self, method: str, status: int, attempt: int, post_retries_used: int
    ) -> bool:
        if status not in self.retry_statuses:
            return False
        if method == "POST":
            return status >= 500 and post_retries_used < 1
        return attempt < self.max_attempts

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1

    def _sleep_backoff(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self._backoff_delay(attempt, retry_after)
        if delay > 0:
            logger.warning("Retrying in %.2fs (attempt %d)", delay, attempt + 1)
            time.sleep(delay)

    def _backoff_delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after is not None:
            try:
                return min(max(0.0, float(retry_after)), 30.0)
            except ValueError:
                pass
        base = self.backoff_factor * (2 ** (attempt - 1))
        return random.uniform(0, base)  # full jitter

    def _network_error(self, exc: Exception, url: str) -> ALLError:
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return TimeoutError_(
                "Request timed out. Every request carries a timeout so nothing "
                "hangs; raise Client(timeout=...) if the endpoint is slow.",
                url=redact_url(url),
            )
        return TransportError(
            f"Network error: {type(exc).__name__}: {exc}. Check connectivity; the SDK retries "
            "transient socket errors automatically.",
            url=redact_url(url),
        )

    def close(self) -> None:
        """Close this thread's pooled connections (other threads close lazily)."""
        pool: dict[Any, http.client.HTTPConnection] = getattr(self._local, "pool", {})
        for conn in list(pool.values()):
            try:
                conn.close()
            except Exception:
                pass
        pool.clear()
