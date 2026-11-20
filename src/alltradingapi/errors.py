"""Error hierarchy for the All Trading API SDK.

Every error carries structured context (status, payload, redacted URL) and a
message that tells the user what to do next. API keys never appear in error
text, ``repr`` or URLs.
"""

from __future__ import annotations

import json
from typing import Any

from .utils import redact_url

__all__ = [
    "ALLError",
    "ConfigurationError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
    "UpstreamError",
    "TransportError",
    "TimeoutError_",
    "UpdateRequiredError",
    "UpdateCheckError",
    "update_hint",
]


class ALLError(Exception):
    """Base class for every error raised by this SDK.

    Attributes:
        message: Human-readable explanation including a next step.
        status: HTTP status code when the error came from a response.
        code: Short machine-readable code (e.g. ``rate_limited``).
        request_id: Server-provided request id when available.
        payload: Parsed JSON body of the failing response, when present.
        url: Request URL with any credentials redacted.
    """

    code: str = "all_error"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: dict[str, Any] | None = None,
        url: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload or {}
        self.url = url
        self.request_id = request_id

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{type(self).__name__} code={self.code} status={self.status}>"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view (URL already key-redacted)."""
        return {
            "error": self.code,
            "message": self.message,
            "status": self.status,
            "request_id": self.request_id,
            "url": self.url,
            "payload": self.payload,
        }


class ConfigurationError(ALLError):
    """The SDK was constructed or called with impossible configuration."""

    code = "configuration_error"


class AuthenticationError(ALLError):
    """HTTP 401 — missing or invalid API key."""

    code = "authentication_error"


class PermissionDeniedError(ALLError):
    """HTTP 403 — the key does not include this product, or is paused."""

    code = "permission_denied"


class NotFoundError(ALLError):
    """HTTP 404 — unknown route (the server only serves pretty routes)."""

    code = "not_found"


class ValidationError(ALLError):
    """HTTP 400 — bad/missing/unknown parameter (rejected client-side or server-side)."""

    code = "validation_error"


class RateLimitError(ALLError):
    """HTTP 429 — rate limit reached; ``retry_after`` says when to retry."""

    code = "rate_limited"

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(ALLError):
    """HTTP 5xx — server error on the API side."""

    code = "server_error"


class UpstreamError(ServerError):
    """HTTP 502 — market-feed failure or insufficient candles upstream."""

    code = "upstream_error"


class TransportError(ALLError):
    """Network-level failure: DNS, TCP, TLS."""

    code = "transport_error"


class TimeoutError_(ALLError):
    """A request exceeded its timeout. Alias avoids shadowing builtins."""

    code = "timeout"


class UpdateRequiredError(ALLError):
    """The installed SDK is outdated and the strict update gate blocked it.

    Attributes:
        installed: Version currently running.
        latest: Version found on PyPI.
        installed_now: True when ``pip install --upgrade`` was run in-process;
            the running interpreter still holds the old code, so restart.
    """

    code = "update_required"

    def __init__(
        self,
        message: str,
        *,
        installed: str | None = None,
        latest: str | None = None,
        installed_now: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.installed = installed
        self.latest = latest
        self.installed_now = installed_now


class UpdateCheckError(ALLError):
    """Raised only by ``Client.check_updates`` when local version config is invalid."""

    code = "update_check_error"


def update_hint(command: str = "pip install --upgrade alltradingapi") -> str:
    """A consistent, actionable hint used across update-related errors."""
    return f"Run: {command}"


def error_from_response(
    status: int,
    body: bytes,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> ALLError:
    """Map an HTTP response to the matching SDK error.

    The server's JSON envelope is ``{"error": "..."}``. The server text is
    preserved, and every message keeps an actionable SDK hint. URLs are
    key-redacted before storage.
    """
    raw_headers = {k.lower(): v for k, v in (headers or {}).items()}
    server_text = None
    payload: dict[str, Any] = {}
    try:
        decoded = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(decoded, dict):
            payload = decoded
            server_text = decoded.get("error") or decoded.get("message")
    except (ValueError, UnicodeDecodeError):
        pass

    def msg(hint: str) -> str:
        if server_text and server_text != hint:
            return f"{hint} (server: {server_text})"
        return hint

    retry_after: float | None = None
    retry_after_raw = raw_headers.get("retry-after")
    if retry_after_raw is not None:
        try:
            retry_after = float(retry_after_raw)
        except ValueError:
            retry_after = None

    safe_url = redact_url(url)

    if status == 400:
        return ValidationError(
            msg(
                "The request was rejected (400). Check parameter names, values "
                "and ranges — this SDK validates documented parameters "
                "client-side, so a 400 usually means the server-side set "
                "changed."
            ),
            status=status,
            payload=payload,
            url=safe_url,
        )
    if status == 401:
        return AuthenticationError(
            msg(
                "A valid API key is required (401). Pass api_key=..., build "
                "the client with a keys mapping, or set the ATA_API_KEY "
                "environment variable for the product you are calling."
            ),
            status=status,
            payload=payload,
            url=safe_url,
        )
    if status == 403:
        return PermissionDeniedError(
            msg(
                "This API key does not include this product, or the "
                "subscription is paused (403). Check your product "
                "subscription — each product needs its own key."
            ),
            status=status,
            payload=payload,
            url=safe_url,
        )
    if status == 404:
        return NotFoundError(
            msg(
                "Unknown route (404). The server exposes pretty routes only "
                "(e.g. /qx_candles); direct /api/*.php URLs are blocked by "
                "design."
            ),
            status=status,
            payload=payload,
            url=safe_url,
        )
    if status == 429:
        return RateLimitError(
            msg(
                "Rate limit reached (429). The SDK already honours Retry-After "
                "and retries automatically; slow down your polling rate or "
                "upgrade your plan."
            ),
            status=status,
            payload=payload,
            url=safe_url,
            retry_after=retry_after,
        )
    if status == 502:
        return UpstreamError(
            msg(
                "The market feed is unavailable upstream (502) or did not "
                "return enough candles. The SDK retries automatically; if it "
                "persists, the feed itself is down — try again shortly."
            ),
            status=status,
            payload=payload,
            url=safe_url,
        )
    if 500 <= status <= 599:
        return ServerError(
            msg(
                f"Server error {status} from the API. The SDK retries transient 5xx automatically."
            ),
            status=status,
            payload=payload,
            url=safe_url,
        )
    return ALLError(
        msg(f"Unexpected HTTP status {status} from the API."),
        status=status,
        payload=payload,
        url=safe_url,
    )
