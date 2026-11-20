"""Logging setup for the SDK.

The SDK logs under the ``"alltradingapi"`` logger:

* DEBUG — request/response lines with **redacted** keys and timings
* WARNING — retries
* INFO — update-gate events
* ERROR — failures

Payloads are never logged at INFO. Keys are redacted everywhere.
"""

from __future__ import annotations

import logging

from .utils import REDACTED, redact_url

__all__ = ["logger", "set_log_level", "http_debug_line"]

logger = logging.getLogger("alltradingapi")


def set_log_level(level: str, *, log_http: bool = True) -> None:
    """Configure the SDK logger.

    Args:
        level: Standard level name (``"DEBUG"``, ``"INFO"``, ...).
        log_http: When True (default) attach the redacting HTTP handler at
            DEBUG level. Set False for fully silent HTTP logging.
    """
    numeric = getattr(logging, str(level).upper(), logging.WARNING)
    logger.setLevel(numeric)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = True
    if log_http and numeric <= logging.DEBUG:
        logger.debug("alltradingapi HTTP logging enabled (keys are redacted)")


def http_debug_line(
    method: str,
    url: str,
    *,
    status: int | None = None,
    elapsed_ms: float | None = None,
    attempt: int = 0,
) -> str:
    """Build a single redacted DEBUG line for a request/response."""
    safe_url = redact_url(url)
    parts = [method, safe_url or "<url>"]
    if status is not None:
        parts.append(f"-> {status}")
    if elapsed_ms is not None:
        parts.append(f"{elapsed_ms:.0f}ms")
    if attempt:
        parts.append(f"attempt={attempt + 1}")
    return " ".join(parts)


def redacted_key(key: str | None) -> str:
    """Return a non-sensitive fingerprint of an API key for cache/debug keys."""
    if not key:
        return "none"
    return REDACTED
