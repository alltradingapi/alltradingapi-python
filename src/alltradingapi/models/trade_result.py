"""Trade-result models from ``/qx_result``."""

from __future__ import annotations

from .base import APIObject, TradeOutcome, coerce_enum
from .candle import Candle

__all__ = ["TradeResult"]


class TradeResult(APIObject):
    """Outcome of a checked trade entry from ``/qx_result``."""

    __slots__ = ()

    @property
    def success(self) -> bool:
        return bool(self._data.get("success", False))

    @property
    def asset(self) -> str:
        return str(self._data.get("asset", "") or "")

    @property
    def timeframe(self) -> str:
        return str(self._data.get("timeframe", "") or "")

    @property
    def direction(self) -> str:
        return str(self._data.get("direction", "") or "")

    @property
    def mtg(self) -> int:
        try:
            return int(self._data.get("mtg", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def entry_time(self) -> str:
        return str(self._data.get("entry_time", "") or "")

    @property
    def outcomes(self) -> list[TradeOutcome]:
        """Coerced per-candle outcome values (candles that carry a status)."""
        values: list[TradeOutcome] = []
        for entry in self._data.get("candles", []) or []:
            if isinstance(entry, dict) and "status" in entry:
                coerced = coerce_enum(TradeOutcome, entry.get("status"), None)
                if coerced is not None:
                    values.append(coerced)
        result_value = self.result
        if result_value is not None and (not values or self.mtg > 1):
            values.append(result_value)
        return values

    @property
    def checks(self) -> list[str]:
        """Raw per-check status strings from the payload."""
        value = self._data.get("checks", [])
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    @property
    def candle_directions(self) -> list[str]:
        value = self._data.get("candle_directions", [])
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    @property
    def result(self) -> TradeOutcome | None:
        """Final result coerced to a :class:`TradeOutcome`."""
        return coerce_enum(TradeOutcome, self._data.get("result"), None)

    @property
    def candles(self) -> list[Candle]:
        """Candles attached to the result (pending markers are skipped)."""
        out: list[Candle] = []
        for entry in self._data.get("candles", []) or []:
            if isinstance(entry, dict) and "time" in entry:
                out.append(Candle(entry))
        return out
