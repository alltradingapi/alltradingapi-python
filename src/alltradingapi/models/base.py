"""Shared model plumbing: the :class:`APIObject` base and the SDK's enums."""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum
from typing import Any, TypeVar, cast

__all__ = [
    "APIObject",
    "TradeOutcome",
    "coerce_enum",
]

_JSONLike = dict[str, Any] | list[Any] | str | int | float | bool | None


class APIObject:
    """Dict-backed base class for every API model.

    * Attribute access maps to payload keys; hyphens/spaces in keys are
      normalised to underscores.
    * Nested dicts convert lazily to :class:`APIObject`; nested lists convert
      their dict members on access.
    * Unknown attributes raise :class:`AttributeError` listing available keys.
    * ``to_dict()`` round-trips to the original payload.
    """

    __slots__ = ("_data",)

    _data: dict[str, Any]

    def __init__(self, data: dict[str, Any]) -> None:
        object.__setattr__(self, "_data", dict(data) if isinstance(data, dict) else {})

    # -- data access ------------------------------------------------------
    @property
    def raw(self) -> dict[str, Any]:
        """The underlying payload dict (shallow copy)."""
        return dict(self._data)

    @property
    def data(self) -> dict[str, Any]:
        """The live underlying payload dict."""
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        """Return ``payload[key]`` or *default*, with lazy conversion applied."""
        value = self._data.get(key, default)
        return _convert(value)

    def keys(self) -> Iterator[str]:
        return iter(self._data.keys())

    def items(self) -> Iterator[tuple[str, Any]]:
        for key in self._data:
            yield key, self.get(key)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __getattr__(self, name: str) -> Any:
        # Only called when normal attribute lookup fails.
        data = object.__getattribute__(self, "_data")
        if name.startswith("_"):
            raise AttributeError(name)
        if name in data:
            return _convert(data[name])
        normalized = {k.replace("-", "_").replace(" ", "_"): k for k in data}
        if name in normalized:
            return _convert(data[normalized[name]])
        raise AttributeError(
            "{} has no attribute {!r}. Available keys: {}".format(
                type(self).__name__, name, ", ".join(sorted(data)) or "(none)"
            )
        )

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self._data[name] = value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, APIObject):
            return self._data == other._data
        if isinstance(other, dict):
            return self._data == other
        return NotImplemented

    def __hash__(self) -> int:  # pragma: no cover - unhashable payloads
        raise TypeError("APIObject instances are unhashable")

    def __repr__(self) -> str:
        label = type(self).__name__
        keys = ", ".join(sorted(self._data)[:8])
        more = ", …" if len(self._data) > 8 else ""
        return f"<{label} {keys}{more}>"

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict copy that round-trips to the original payload."""
        return cast(dict[str, Any], _to_plain(self._data))


def _convert(value: Any) -> Any:
    """Lazily convert nested dicts/lists into APIObject-friendly structures."""
    if isinstance(value, dict):
        return APIObject(value)
    if isinstance(value, list):
        return [_convert(item) for item in value]
    return value


def _to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, APIObject):
        return _to_plain(value._data)
    return value


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
_EnumType = TypeVar("_EnumType", bound="_StrEnum")


def coerce_enum(
    enum_cls: type[_EnumType], value: Any, default: _EnumType | None = None
) -> _EnumType | None:
    """Coerce *value* to *enum_cls* with tolerant matching, else *default*."""
    if isinstance(value, enum_cls):
        return value
    if value is None:
        return default
    text = str(value)
    for member in enum_cls:
        if text == member.value:
            return member
        if text.upper().replace("-", "_").replace(" ", "_") == member.name:
            return member
    return default


class _StrEnum(str, Enum):
    """String enum with a friendly label and tolerant coercion."""

    @classmethod
    def coerce(cls, value: Any, default: Any = None) -> Any:
        """Tolerant coercion; ``None`` passes through unchanged."""
        return coerce_enum(cls, value, default)

    @property
    def label(self) -> str:
        """Human-readable label, e.g. ``'CALL / BUY'`` for ``TradeSetup`` sides."""
        return str(self.value).replace("_", " ").title()

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class TradeOutcome(_StrEnum):
    """Outcome values returned by /qx_result."""

    WIN = "win"
    WIN_1 = "win1"
    WIN_2 = "win2"
    REFUND = "refund"
    LOSE = "lose"
    PENDING = "pending"

    @property
    def won(self) -> bool:
        return self in (TradeOutcome.WIN, TradeOutcome.WIN_1, TradeOutcome.WIN_2)

    @property
    def lost(self) -> bool:
        return self is TradeOutcome.LOSE

    @property
    def is_pending(self) -> bool:
        return self is TradeOutcome.PENDING
