"""Declarative endpoint parameter validation.

The live server rejects ANY undocumented query parameter with HTTP 400
("That parameter is not available on this API."). To save a round trip and
give better errors, this module declares, per endpoint, exactly which params
are allowed, their types, ranges, enums and defaults — and validates/serialises
them *before* the request is made.

Rules enforced here:

* unknown kwarg            -> ValidationError listing allowed names
* out-of-range value       -> ValidationError with the accepted range
* invalid enum             -> ValidationError listing valid values
* booleans serialise as    -> "true" when True; omitted when False
                              (the server rejects `param=false` with a 400)
* ``None`` values          -> omitted entirely from the query string
* asset format             -> validated against ^[A-Za-z0-9_]{2,40}$ (input is
                              trimmed for validation but never mutated)
"""

from __future__ import annotations

import re
from typing import Any

from .config import TIMEFRAME_LABELS
from .errors import ValidationError
from .utils import coerce_bool_str, is_valid_asset, is_valid_date, is_valid_time

__all__ = ["validate_params", "allowed_params", "ENDPOINT_PARAMS"]

# ---------------------------------------------------------------------------
# Spec language
# ---------------------------------------------------------------------------
StrParam = "str"
IntParam = "int"
FloatParam = "float"
BoolParam = "bool"


def _tf_timeframe(value: str) -> str:
    """Timeframe labels the server accepts verbatim."""
    lowered = str(value).strip().lower()
    if lowered not in TIMEFRAME_LABELS:
        raise ValidationError(
            "Invalid timeframe {!r}. Valid timeframes: {}.".format(
                value, ", ".join(TIMEFRAME_LABELS)
            )
        )
    return lowered


def _time_str(value: str) -> str:
    if not is_valid_time(str(value)):
        raise ValidationError(f"Invalid time {value!r}. Expected 'HH:MM' or 'HH:MM:SS'.")
    return str(value).strip()


def _date_str(value: str) -> str:
    if not is_valid_date(str(value)):
        raise ValidationError(f"Invalid date {value!r}. Expected 'YYYY-MM-DD'.")
    return str(value).strip()


def _csv_timeframes(value: Any) -> str:
    """Comma-separated timeframe list, e.g. ``1m,5m,15m``."""
    parts = [p.strip().lower() for p in str(value).split(",") if p.strip()]
    if not parts:
        raise ValidationError("timeframes must be a non-empty comma-separated list.")
    for part in parts:
        if part not in TIMEFRAME_LABELS:
            raise ValidationError(
                "Invalid timeframe {!r} in timeframes. Valid: {}.".format(
                    part, ", ".join(TIMEFRAME_LABELS)
                )
            )
    return ",".join(parts)


def _asset(value: str) -> str:
    asset = str(value).strip()
    if not is_valid_asset(asset):
        raise ValidationError(
            f"Invalid asset {value!r}. Asset symbols look like 'EURUSD_otc' or "
            "'AUDCAD' and must match ^[A-Za-z0-9_]{2,40}$."
        )
    return asset


# name -> (type, required, choices, min, max, transformer, default)
class _Rule:
    __slots__ = (
        "kind",
        "required",
        "choices",
        "min_value",
        "max_value",
        "transform",
        "default",
        "doc",
    )

    def __init__(
        self,
        kind: str,
        required: bool = False,
        choices: tuple[Any, ...] | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        transform: Any | None = None,
        default: Any = None,
        doc: str = "",
    ) -> None:
        self.kind = kind
        self.required = required
        self.choices = choices
        self.min_value = min_value
        self.max_value = max_value
        self.transform = transform
        self.default = default
        self.doc = doc


def _s(doc: str = "", **kw: Any) -> _Rule:
    return _rule(kind=StrParam, doc=doc, **kw)


def _rule(kind: str = StrParam, **kw: Any) -> _Rule:
    return _Rule(kind=kind, **kw)


_TIMEFRAMES_TF = tuple(TIMEFRAME_LABELS)

# ---------------------------------------------------------------------------
# Per-endpoint parameter tables (exactly the documented sets — nothing more)
# ---------------------------------------------------------------------------
ENDPOINT_PARAMS: dict[str, dict[str, _Rule]] = {
    "assets": {
        "account": _rule(
            IntParam, min_value=0, max_value=99, doc="Account selector (server-defined values)."
        ),
    },
    "candles": {
        "asset": _rule(
            StrParam, required=True, transform=_asset, doc="Asset symbol, e.g. EURUSD_otc."
        ),
        "account": _rule(IntParam, min_value=0, max_value=99),
        "hours": _rule(
            IntParam,
            min_value=1,
            max_value=24,
            default=2,
            doc="Hours of history, 1..24 (default 2).",
        ),
        "timeframe": _rule(StrParam, choices=_TIMEFRAMES_TF, default="1m"),
        "timezone": _s(doc="e.g. '-3', 'UTC', 'Asia/Dhaka'."),
        "offset": _rule(IntParam, min_value=0, max_value=604800, default=3600),
        "timestamp": _rule(IntParam, min_value=1, max_value=4102444800),
        "timeout": _rule(IntParam, min_value=1, max_value=30, default=16),
        "verbose_logging": _rule(BoolParam),
        "period": _rule(IntParam, min_value=1, max_value=86400),
    },
    "history": {},  # filled below from candles
    "saved": {
        "asset": _rule(StrParam, required=True, transform=_asset),
        "timeframe": _rule(StrParam, choices=_TIMEFRAMES_TF, default="1m"),
        "timezone": _s(),
        "count": _rule(IntParam, min_value=1, max_value=1000, default=100),
        "raw": _rule(BoolParam),
        "nocache": _rule(BoolParam),
    },
    "screenshot": {
        "asset": _rule(StrParam, required=True, transform=_asset),
        "timeframe": _rule(StrParam, choices=_TIMEFRAMES_TF, default="1m"),
        "timezone": _s(),
        "time": _rule(StrParam, transform=_time_str),
        "result": _s(doc="Trade-result filter shown on the chart."),
        "names": _s(doc="Pattern names overlay."),
    },
    "result": {
        "asset": _rule(StrParam, required=True, transform=_asset),
        "time": _rule(StrParam, required=True, transform=_time_str),
        "timeframe": _rule(StrParam, choices=_TIMEFRAMES_TF, default="1m"),
        "timezone": _s(),
        "direction": _rule(StrParam, required=True, choices=("call", "put")),
        "mtg": _rule(IntParam, min_value=0, max_value=2, default=1),
    },
}

ENDPOINT_PARAMS["history"] = dict(ENDPOINT_PARAMS["candles"])

_ASSET_RE = re.compile(r"^[A-Za-z0-9_]{2,40}$")


def allowed_params(endpoint: str) -> tuple[str, ...]:
    """Return the documented parameter names for *endpoint*."""
    return tuple(ENDPOINT_PARAMS.get(endpoint, {}).keys())


def validate_params(endpoint: str, **params: Any) -> list[tuple[str, str]]:
    """Validate and serialise *params* for *endpoint*.

    Returns an ordered ``[(name, value), ...]`` list ready for urlencode.
    Raises :class:`ValidationError` before any network I/O when a parameter is
    unknown, out of range, an invalid enum, or of the wrong type.
    """
    table = ENDPOINT_PARAMS.get(endpoint)
    if table is None:
        raise ValidationError(f"Unknown endpoint {endpoint!r}.")

    unknown = [k for k in params if k not in table and params[k] is not None]
    if unknown:
        raise ValidationError(
            "Unknown parameter(s) {} for '{}'. This API validates the exact "
            "set of documented parameters; allowed names: {}.".format(
                ", ".join(repr(u) for u in sorted(unknown)),
                endpoint,
                ", ".join(table.keys()) or "(none)",
            )
        )

    out: list[tuple[str, str]] = []
    for name, rule in table.items():
        value = params.get(name, None)
        if value is None:
            if rule.required:
                raise ValidationError(f"Missing required parameter '{name}' for '{endpoint}'.")
            continue
        if rule.kind == BoolParam and value is False:
            # The server rejects `param=false` with a 400 ("not available on
            # this API"); false is the server default, so simply omit it.
            continue
        out.append((name, _serialize(name, rule, value)))
    return out


def _serialize(name: str, rule: _Rule, value: Any) -> str:
    if rule.kind == BoolParam:
        if not isinstance(value, bool):
            raise ValidationError(
                f"Parameter '{name}' expects a boolean (True/False); got {value!r}."
            )
        return coerce_bool_str(value)

    if rule.kind == IntParam:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"Parameter '{name}' expects an integer; got {value!r}.")
        _check_range(name, rule, float(value))
        return str(value)

    if rule.kind == FloatParam:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"Parameter '{name}' expects a number; got {value!r}.")
        _check_range(name, rule, float(value))
        return repr(float(value))

    # StrParam
    if not isinstance(value, str):
        raise ValidationError(f"Parameter '{name}' expects a string; got {value!r}.")
    if rule.transform is not None:
        value = rule.transform(value)
    if rule.choices is not None:
        if value not in rule.choices:
            raise ValidationError(
                "Invalid {} {!r}. Valid values: {}.".format(
                    name, value, ", ".join(str(c) for c in rule.choices)
                )
            )
    return str(value)


def _check_range(name: str, rule: _Rule, number: float) -> None:
    low, high = rule.min_value, rule.max_value
    if low is not None and number < low:
        raise ValidationError(f"Parameter '{name}' must be >= {low}; got {number}.")
    if high is not None and number > high:
        raise ValidationError(f"Parameter '{name}' must be <= {high}; got {number}.")
