"""Google-mode behaviour of services, with the HTTP providers faked (no network, no keys)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import config
import services
from geo import encode_polyline
from providers import places, routes

PATH = [(27.30, -82.50), (27.60, -82.30), (28.00, -82.10)]
DIRECT = {"distanceMeters": 100_000, "seconds": 6000, "polyline": encode_polyline(PATH)}


def _station(i, price, lat=27.6):
    return {"id": f"s{i}", "name": f"S{i}", "lat": lat + i * 0.001, "lng": -82.3,
            "price": price, "off_route_m": 100 * i}


def _trip(stations):
    return {"mode": "google", "stations": stations, "direct": DIRECT,
            "origin": "A", "destination": "B"}


@pytest.fixture(autouse=True)
def fake_google(monkeypatch):
    services._detour_memo.clear()
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key")
    calls = []

    def fake_route(api_key, origin, destination, via=None, **kw):
        calls.append(via)
        return {"distanceMeters": DIRECT["distanceMeters"] + 3218.688, "seconds": 6000 + 300,
                "polyline": DIRECT["polyline"]}

    monkeypatch.setattr(routes, "compute_route", fake_route)
    return calls


def test_detours_use_routes_for_cheapest_priced_stations_only(fake_google):
    stations = [_station(0, 3.90), _station(1, 3.20), _station(2, None), _station(3, 3.40)]
    out = services.compute_detours(_trip(stations), top_n=2)
    by_id = {s["id"]: s for s in out}
    assert [s["id"] for s in out] == ["s0", "s1", "s2", "s3"]          # order preserved
    assert by_id["s1"]["detour_source"] == by_id["s3"]["detour_source"] == "routes"
    assert by_id["s0"]["detour_source"] == by_id["s2"]["detour_source"] == "approx"
    assert by_id["s1"]["extra_miles"] == pytest.approx(2.0)             # 3218.688 m
    assert by_id["s1"]["extra_minutes"] == pytest.approx(5.0)
    assert len(fake_google) == 2


def test_detours_are_memoized_across_reruns(fake_google):
    trip = _trip([_station(0, 3.2), _station(1, 3.3)])
    services.compute_detours(trip)
    services.compute_detours(trip)   # a slider moved: same stations, no new billing
    assert len(fake_google) == 2


def test_route_failure_degrades_to_approximation(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(routes, "compute_route", boom)
    out = services.compute_detours(_trip([_station(0, 3.2)]))
    assert out[0]["detour_source"] == "approx" and "extra_miles" not in out[0]


def test_load_trip_google_filters_to_corridor_and_warns_without_prices(monkeypatch):
    monkeypatch.setattr(routes, "compute_route", lambda *a, **k: DIRECT)
    near = {"id": "n", "name": "N", "lat": 27.60, "lng": -82.30, "price": None}
    far = {"id": "f", "name": "F", "lat": 27.60, "lng": -81.50, "price": None}
    monkeypatch.setattr(places, "search_along", lambda *a, **k: [near, far])
    trip = services.load_trip("A", "B", 3000, provider="google")
    assert trip["mode"] == "google" and [s["id"] for s in trip["stations"]] == ["n"]
    assert any("no fuel prices" in w for w in trip["warnings"])


def test_load_trip_google_without_key_falls_back_to_fixture(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)
    trip = services.load_trip("A", "B", provider="google")
    assert trip["mode"] == "fixture" and any("GOOGLE_API_KEY" in w for w in trip["warnings"])
