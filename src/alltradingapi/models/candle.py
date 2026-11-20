"""Candle models — OHLC bars from ``/qx_candles``, ``/qx_history``, ``/qx_saved``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import APIObject

__all__ = ["Candle", "SavedCandles"]

_DOJI_BODY_RATIO = 0.1  # body < 10% of full range counts as a doji


class Candle(APIObject):
    """One OHLC bar with tick metadata and typed convenience helpers."""

    __slots__ = ()

    @property
    def time(self) -> int:
        """Bar open time as unix seconds."""
        return int(self._data.get("time", 0) or 0)

    @property
    def open(self) -> float:
        return float(self._data.get("open", 0.0) or 0.0)

    @property
    def high(self) -> float:
        return float(self._data.get("high", 0.0) or 0.0)

    @property
    def low(self) -> float:
        return float(self._data.get("low", 0.0) or 0.0)

    @property
    def close(self) -> float:
        return float(self._data.get("close", 0.0) or 0.0)

    @property
    def ticks(self) -> int:
        return int(self._data.get("ticks", 0) or 0)

    @property
    def last_tick(self) -> float:
        """Unix timestamp (with fraction) of the last tick in the bar."""
        return float(self._data.get("last_tick", 0.0) or 0.0)

    @property
    def asset(self) -> str:
        return str(self._data.get("asset", "") or "")

    @property
    def asset_name(self) -> str:
        return str(self._data.get("asset_name", "") or "")

    @property
    def time_read(self) -> str:
        """Formatted bar time as the server rendered it."""
        return str(self._data.get("time_read", "") or "")

    # -- derived helpers ---------------------------------------------------
    @property
    def dt(self) -> datetime:
        """Bar open time as a UTC-aware :class:`datetime`."""
        return datetime.fromtimestamp(self.time, tz=timezone.utc)

    @property
    def direction(self) -> str:
        """``"call"`` when bullish, ``"put"`` when bearish, else ``"neutral"``."""
        if self.close > self.open:
            return "call"
        if self.close < self.open:
            return "put"
        return "neutral"

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def body(self) -> float:
        """Absolute open-to-close distance."""
        return abs(self.close - self.open)

    @property
    def range(self) -> float:  # noqa: A003 - mirrors the OHLC term
        """High-to-low distance."""
        return abs(self.high - self.low)

    def is_doji(self, threshold: float = _DOJI_BODY_RATIO) -> bool:
        """True when the body is smaller than *threshold* of the full range."""
        full = self.range
        if full <= 0:
            return True
        return self.body / full < threshold

    def to_row(self) -> dict[str, Any]:
        """Compact ``(time, open, high, low, close)`` row for tabular output."""
        return {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> list[Candle]:
        """Build candles from a bare-array payload."""
        return [cls(row) for row in rows if isinstance(row, dict)]


class SavedCandles(APIObject):
    """Envelope returned by ``/qx_saved`` (with ``raw=true`` the body may be a bare list)."""

    __slots__ = ()

    @property
    def asset(self) -> str:
        return str(self._data.get("asset", "") or "")

    @property
    def timeframe(self) -> str:
        return str(self._data.get("timeframe", "") or "")

    @property
    def period(self) -> int:
        try:
            return int(self._data.get("period", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def requested_count(self) -> int:
        try:
            return int(self._data.get("requested_count", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def returned_count(self) -> int:
        try:
            return int(self._data.get("returned_count", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def base_period(self) -> int:
        try:
            return int(self._data.get("base_period", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def days_scanned(self) -> int:
        try:
            return int(self._data.get("days_scanned", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def raw_5s_candles(self) -> int:
        try:
            return int(self._data.get("raw_5s_candles", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def from_cache(self) -> bool:
        return bool(self._data.get("from_cache", False))

    @property
    def candles(self) -> list[Candle]:
        rows = self._data.get("candles", [])
        if isinstance(rows, list):
            return [Candle(r) for r in rows if isinstance(r, dict)]
        return []

    def __len__(self) -> int:
        return len(self.candles)

    @classmethod
    def from_payload(cls, payload: Any) -> SavedCandles:
        """Accept either the object envelope or a bare candle list."""
        if isinstance(payload, list):
            return cls({"candles": payload})
        if isinstance(payload, dict):
            return cls(payload)
        return cls({})
