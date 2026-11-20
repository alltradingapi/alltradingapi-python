"""Model behaviour: payloads, round-trips, nested access, enum coercion."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alltradingapi.models import (
    APIObject,
    Asset,
    Candle,
    SavedCandles,
    TradeOutcome,
    TradeResult,
    coerce_enum,
)

from .conftest import load_fixture


def test_apiobject_attribute_access() -> None:
    obj = APIObject({"a-b": 1, "plain": 2, "nested": {"x": 3}})
    assert obj.a_b == 1
    assert obj.plain == 2
    assert obj.nested.x == 3
    with pytest.raises(AttributeError) as excinfo:
        _ = obj.missing
    assert "a_b" in str(excinfo.value) or "plain" in str(excinfo.value)


def test_apiobject_roundtrip_and_contains() -> None:
    payload = {"a": 1, "list": [{"k": "v"}], "s": "text"}
    obj = APIObject(payload)
    assert obj.to_dict() == payload
    assert "a" in obj
    assert obj.list[0].k == "v"
    assert set(obj.keys()) == {"a", "list", "s"}
    assert len(obj) == 3
    assert obj.get("zz", "fallback") == "fallback"
    assert obj == payload


def test_apiobject_setattr_and_eq_none() -> None:
    obj = APIObject({"a": 1})
    obj.b = 2  # plain setattr stores into payload
    assert obj.to_dict() == {"a": 1, "b": 2}
    assert (obj == 42) is False


def test_asset_model() -> None:
    assets = Asset.from_rows(load_fixture("assets.json"))
    assert len(assets) == 3
    first = assets[0]
    assert first.id == 368
    assert first.symbol == "ATOUSD_otc"
    assert first.is_open is True
    assert first.is_otc is True
    assert first.profit_1m == 77
    assert first.profit_5m == 69
    assert first.change_24h == pytest.approx(-11.56)
    assert str(first) == "ATOUSD_otc"
    closed = assets[2]
    assert closed.is_otc is False
    assert closed.change_24h is None


def test_asset_none_profit_and_payout() -> None:
    asset = Asset(
        {
            "asset_id": None,
            "asset": "AB",
            "asset_name": None,
            "is_open": None,
            "type": None,
            "payout": None,
            "change_24h": "x",
            "profit": None,
        }
    )
    assert asset.id == 0
    assert asset.profit_1m is None
    assert asset.payout == 0
    assert asset.change_24h is None


def test_candle_model_helpers() -> None:
    candles = Candle.from_rows(load_fixture("candles.json"))
    assert len(candles) == 6
    first = candles[0]
    assert first.dt == datetime.fromtimestamp(first.time, tz=timezone.utc)
    assert first.direction in ("call", "put", "neutral")
    assert first.is_bullish == (first.close > first.open)
    assert first.body == pytest.approx(abs(first.close - first.open))
    assert first.range == pytest.approx(first.high - first.low)
    assert isinstance(first.is_doji(), bool)
    row = first.to_row()
    assert set(row) == {"time", "open", "high", "low", "close"}


def test_candle_doji_zero_range() -> None:
    flat = Candle({"time": 1, "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0})
    assert flat.is_doji() is True
    assert flat.direction == "neutral"
    assert flat.is_bullish is False
    assert Candle.from_rows([None, {"time": 2}])[0].time == 2


def test_saved_candles_both_shapes() -> None:
    envelope = load_fixture("saved.json")
    saved = SavedCandles.from_payload(envelope)
    assert saved.asset == "ATOUSD_otc"
    assert saved.returned_count == 3
    assert len(saved.candles) == 3
    assert saved.from_cache is False
    assert len(saved) == 3
    raw = SavedCandles.from_payload(load_fixture("saved_raw.json"))
    assert len(raw.candles) == 2
    assert raw.asset == ""


def test_saved_candles_invalid_payload() -> None:
    empty = SavedCandles.from_payload(12345)
    assert empty.candles == []
    assert empty.requested_count == 0


def test_trade_result_model() -> None:
    result = TradeResult(load_fixture("result.json"))
    assert result.success is True
    assert result.direction == "call"
    assert result.mtg == 1
    assert result.result == TradeOutcome.WIN
    assert result.result.won is True
    assert len(result.candles) == 1
    assert result.outcomes[-1] == TradeOutcome.WIN


def test_trade_result_pending() -> None:
    result = TradeResult(load_fixture("result_pending.json"))
    assert result.result == TradeOutcome.PENDING
    assert result.result.is_pending is True
    assert result.candles == []


def test_trade_result_outcomes_and_missing() -> None:
    result = TradeResult(
        {
            "success": True,
            "asset": "A",
            "timeframe": "1m",
            "direction": "call",
            "mtg": 2,
            "entry_time": "t",
            "candles": [{"status": "win1"}, {"status": "lose"}, {"time": 1}],
            "checks": ["win1", "lose"],
            "candle_directions": ["call", "put"],
            "result": "win2",
        }
    )
    assert [o.value for o in result.outcomes] == ["win1", "lose", "win2"]
    assert result.candle_directions == ["call", "put"]
    assert result.checks == ["win1", "lose"]
    assert len(result.candles) == 1
    bad = TradeResult(
        {
            "mtg": "x",
            "checks": "odd",
            "candle_directions": None,
            "candles": None,
            "result": "unknown-ish",
        }
    )
    assert bad.mtg == 0
    assert bad.checks == []
    assert bad.result is None


def test_enum_coercion() -> None:
    assert coerce_enum(TradeOutcome, "win") == TradeOutcome.WIN
    assert coerce_enum(TradeOutcome, "win2") == TradeOutcome.WIN_2
    assert coerce_enum(TradeOutcome, "WIN_2") == TradeOutcome.WIN_2
    assert coerce_enum(TradeOutcome, "weird") is None
    assert coerce_enum(TradeOutcome, None) is None
    assert TradeOutcome.LOSE.lost is True
    assert TradeOutcome.REFUND.won is False and TradeOutcome.REFUND.lost is False
    assert TradeOutcome.coerce("WIN", None) == TradeOutcome.WIN
    assert TradeOutcome.PENDING.is_pending is True


def test_unknown_fields_tolerated() -> None:
    obj = APIObject({"success": True, "brand_new_key": {"x": 1}})
    assert obj.success is True
    assert obj.brand_new_key.x == 1
    assert obj.to_dict()["brand_new_key"] == {"x": 1}
