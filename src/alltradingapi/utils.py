"""Small shared helpers. Kept dependency-free and boring on purpose."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = [
    "redact_url",
    "REDACTED",
    "ASSET_RE",
    "normalize_asset",
    "is_valid_asset",
    "is_valid_time",
    "is_valid_date",
    "compact_json",
    "truncate_text",
]

REDACTED = "***"
_SECRET_QUERY_KEYS = ("key", "api_key", "apikey", "token", "access_token")

ASSET_RE = re.compile(r"^[A-Za-z0-9_]{2,40}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_BEARER_RE = re.compile(r"(?i)bearer\s+[ap0-9a-z_]+")
_QUERY_KEY_RE = re.compile(r"(?i)([?&])(key|api_key|apikey|token)=([^&]+)")


def redact_url(url: str | None) -> str | None:
    """Return *url* with every credential removed or replaced by ``***``.

    Safe to store in logs, exceptions and test assertions.
    """
    if not url:
        return url
    cleaned = _QUERY_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}={REDACTED}", url)
    cleaned = _BEARER_RE.sub("Bearer " + REDACTED, cleaned)
    return cleaned


def normalize_asset(asset: str) -> str:
    """Strip whitespace for validation only; never silently upper-case."""
    return asset.strip()


def is_valid_asset(asset: str) -> bool:
    """True when *asset* matches the server's documented symbol regex."""
    return bool(ASSET_RE.match(normalize_asset(asset)))


def is_valid_time(value: str) -> bool:
    """True for ``HH:MM`` / ``HH:MM:SS`` clock strings."""
    return bool(TIME_RE.match(value.strip()))


def is_valid_date(value: str) -> bool:
    """True for ``YYYY-MM-DD`` calendar strings."""
    return bool(DATE_RE.match(value.strip()))


def compact_json(data: Any, limit: int = 400) -> str:
    """JSON-dump *data* on one line, truncated to *limit* characters."""
    try:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = repr(data)
    return truncate_text(text, limit)


def truncate_text(text: str, limit: int) -> str:
    """Hard-truncate *text* to at most *limit* characters with a size marker."""
    if len(text) <= limit:
        return text
    suffix = f"…[{len(text)} chars]"
    return text[: max(0, limit - len(suffix))] + suffix


def merge_query(url: str, params: dict[str, str]) -> str:
    """Append *params* to *url*, preserving existing query values."""
    parts = urlsplit(url)
    existing = parse_qsl(parts.query, keep_blank_values=True)
    existing.extend(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(existing), parts.fragment))


def coerce_bool_str(value: Any) -> str:
    """Serialise booleans the way the server expects: ``true``/``false``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).lower()


def first_non_none(values: Iterable[Any], default: Any = None) -> Any:
    """Return the first item that is not ``None``, else *default*."""
    for value in values:
        if value is not None:
            return value
    return default
