"""Parameter validation: allowed sets, ranges, enums, bool serialisation."""

from __future__ import annotations

import pytest

from alltradingapi.errors import ValidationError
from alltradingapi.params import allowed_params, validate_params


def test_candles_happy_path() -> None:
    params = validate_params("candles", asset="EURUSD_otc", hours=2, timeframe="1m", timezone="-3")
    names = [k for k, _ in params]
    assert names == ["asset", "hours", "timeframe", "timezone"]
    values = dict(params)
    assert values["hours"] == "2"
    assert values["asset"] == "EURUSD_otc"


def test_unknown_param_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_params("candles", asset="EURUSD_otc", bogus=1)
    assert "bogus" in excinfo.value.message
    assert "asset" in excinfo.value.message  # allowed names listed


def test_removed_endpoints_rejected() -> None:
    # v1 ships six endpoints only; anything else must fail client-side.
    for endpoint in ("signal", "future", "check", "analyzer", "market_trend"):
        with pytest.raises(ValidationError):
            validate_params(endpoint, asset="EURUSD_otc")


def test_range_enforced() -> None:
    with pytest.raises(ValidationError):
        validate_params("candles", asset="TEST", hours=0)
    with pytest.raises(ValidationError):
        validate_params("candles", asset="TEST", hours=25)
    with pytest.raises(ValidationError):
        validate_params("candles", asset="TEST", offset=604801)
    with pytest.raises(ValidationError):
        validate_params("saved", asset="TEST", count=0)
    with pytest.raises(ValidationError):
        validate_params("saved", asset="TEST", count=1001)


def test_enum_enforced() -> None:
    with pytest.raises(ValidationError):
        validate_params("candles", asset="TEST", timeframe="7m")
    with pytest.raises(ValidationError):
        validate_params("result", asset="TEST", time="08:00", direction="sideways")
    with pytest.raises(ValidationError):
        validate_params("screenshot", asset="TEST", time="8am")


def test_boolean_serialisation() -> None:
    params = dict(validate_params("candles", asset="TEST", verbose_logging=True))
    assert params["verbose_logging"] == "true"
    params = dict(validate_params("saved", asset="TEST", nocache=True))
    assert params["nocache"] == "true"


def test_false_booleans_omitted() -> None:
    # The server rejects `param=false` with a 400; False must be omitted.
    params = dict(validate_params("saved", asset="TEST", nocache=False))
    assert "nocache" not in params
    params = dict(validate_params("candles", asset="TEST", verbose_logging=False))
    assert "verbose_logging" not in params
    params = dict(validate_params("saved", asset="TEST", raw=False, nocache=True))
    assert "raw" not in params and params["nocache"] == "true"


def test_none_values_omitted() -> None:
    params = validate_params("candles", asset="TEST", hours=None, timeframe=None)
    assert [k for k, _ in params] == ["asset"]


def test_required_missing() -> None:
    with pytest.raises(ValidationError):
        validate_params("candles")
    with pytest.raises(ValidationError):
        validate_params("result", time="08:00", direction="call")


def test_asset_regex() -> None:
    with pytest.raises(ValidationError):
        validate_params("candles", asset="EUR USD!")  # space invalid
    with pytest.raises(ValidationError):
        validate_params("candles", asset="a")  # too short
    ok = validate_params("candles", asset="  EURUSD_otc ")
    assert dict(ok)["asset"] == "EURUSD_otc"


def test_time_formats() -> None:
    assert validate_params("result", asset="TEST", time="08:00", direction="call")[0][0] == "asset"
    with pytest.raises(ValidationError):
        validate_params("result", asset="TEST", time="8am", direction="call")
    with pytest.raises(ValidationError):
        validate_params("screenshot", asset="TEST", time="8am")
    assert dict(validate_params("screenshot", asset="TEST", time="08:00:30"))["time"] == "08:00:30"


def test_mtg_range() -> None:
    with pytest.raises(ValidationError):
        validate_params("result", asset="TEST", time="08:00", direction="call", mtg=3)
    with pytest.raises(ValidationError):
        validate_params("result", asset="TEST", time="08:00", direction="call", mtg=-1)


def test_type_errors() -> None:
    with pytest.raises(ValidationError):
        validate_params("candles", asset="TEST", hours="two")
    with pytest.raises(ValidationError):
        validate_params("candles", asset="TEST", verbose_logging="yes")
    with pytest.raises(ValidationError):
        validate_params("candles", asset=123)


def test_every_endpoint_has_table() -> None:
    for endpoint in ("assets", "candles", "history", "saved", "screenshot", "result"):
        assert isinstance(allowed_params(endpoint), tuple)
    with pytest.raises(ValidationError):
        validate_params("nonexistent", x=1)


def test_no_extra_params_documented() -> None:
    # The server rejects undocumented params; our tables must not invent any.
    expected = {
        "assets": {"account"},
        "candles": {
            "asset",
            "account",
            "hours",
            "timeframe",
            "timezone",
            "offset",
            "timestamp",
            "timeout",
            "verbose_logging",
            "period",
        },
        "history": {
            "asset",
            "account",
            "hours",
            "timeframe",
            "timezone",
            "offset",
            "timestamp",
            "timeout",
            "verbose_logging",
            "period",
        },
        "saved": {"asset", "timeframe", "timezone", "count", "raw", "nocache"},
        "screenshot": {"asset", "timeframe", "timezone", "time", "result", "names"},
        "result": {"asset", "time", "direction", "timeframe", "timezone", "mtg"},
    }
    for endpoint, names in expected.items():
        assert set(allowed_params(endpoint)) == names
