from __future__ import annotations

from geo_market import lat_lng_for_market, resolve_primary_market


def test_resolve_primary_market_passthrough() -> None:
    assert resolve_primary_market("Belgium", "BE") == ("Belgium", "BE")


def test_lat_lng_for_belgium() -> None:
    coords = lat_lng_for_market("Belgium", "BE")
    assert coords is not None
    assert coords["latitude"] == 50.8503
    assert coords["longitude"] == 4.3517


def test_lat_lng_for_netherlands() -> None:
    coords = lat_lng_for_market("Netherlands", "NL")
    assert coords is not None
    assert abs(coords["latitude"] - 52.3676) < 0.01
    assert abs(coords["longitude"] - 4.9041) < 0.01


def test_lat_lng_unknown_code_returns_none() -> None:
    assert lat_lng_for_market("", "ZZ") is None
