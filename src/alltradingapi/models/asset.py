"""Asset model — one entry of the bare-array response from ``/qx_assets``."""

from __future__ import annotations

from typing import Any

from .base import APIObject

__all__ = ["Asset"]


class Asset(APIObject):
    """A tradable Quotex asset with live open/closed state and payout."""

    __slots__ = ()

    @property
    def id(self) -> int:
        """Numeric asset id (e.g. ``368``)."""
        return int(self._data.get("asset_id", 0) or 0)

    @property
    def symbol(self) -> str:
        """Trading symbol, e.g. ``EURUSD_otc``."""
        return str(self._data.get("asset", "") or "")

    @property
    def name(self) -> str:
        """Display name, e.g. ``Euro / US Dollar (OTC)``."""
        return str(self._data.get("asset_name", "") or "")

    @property
    def is_open(self) -> bool:
        """True when the asset is open for trading right now."""
        return bool(self._data.get("is_open", False))

    @property
    def type(self) -> str:
        """Asset class: currency, cryptocurrency, commodity, ..."""
        return str(self._data.get("type", "") or "")

    @property
    def payout(self) -> int:
        """Base payout percentage (e.g. ``77``)."""
        return int(self._data.get("payout", 0) or 0)

    @property
    def change_24h(self) -> float | None:
        """24h change, when the market reports it."""
        value = self._data.get("change_24h")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def profit_1m(self) -> int | None:
        """Payout for 1-minute expiry."""
        profit = self._data.get("profit")
        if isinstance(profit, dict):
            return _int_or_none(profit.get("1M"))
        return None

    @property
    def profit_5m(self) -> int | None:
        """Payout for 5-minute expiry."""
        profit = self._data.get("profit")
        if isinstance(profit, dict):
            return _int_or_none(profit.get("5M"))
        return None

    @property
    def is_otc(self) -> bool:
        """True when the symbol carries the ``_otc`` suffix."""
        return self.symbol.lower().endswith("_otc")

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> list[Asset]:
        """Build a list of :class:`Asset` from the bare-array payload."""
        return [cls(row) for row in rows if isinstance(row, dict)]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.symbol


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
