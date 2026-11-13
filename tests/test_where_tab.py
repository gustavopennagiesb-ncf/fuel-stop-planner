"""Where to stop: the answer first (go / skip / none), the map links, and the trip form. In google
mode the paid APIs are replaced by fixture data, so no key or call is used."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

import config
import services
from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def make_app(monkeypatch, *, mode="fixture", stations=None, session=None, rename_best=None):
    """mode "google" fakes the paid calls with fixture data (ids look like Place ids) and records
    every (origin, destination) that reaches load_trip."""
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    calls = []
    real_trip, real_series = services.load_trip, services.load_series

    def fake_trip(origin=None, destination=None, buffer_m=3000, provider=None):
        calls.append((origin, destination, buffer_m))
        trip = real_trip(None, None, buffer_m, provider="fixture")
        if stations is not None:
            trip = {**trip, "stations": stations}
        if mode == "google":
            sts = [{**s, "id": f"ChIJ_{i}"} for i, s in enumerate(trip["stations"])]
            if rename_best:
                sts = [{**s, "name": rename_best} for s in sts]  # whichever ranks first has it
            trip = {**trip, "stations": sts, "mode": "google"}
        return trip

    monkeypatch.setattr(services, "load_trip", fake_trip)
    monkeypatch.setattr(services, "load_series", lambda p: real_series("fixture"))
    if mode == "google":
        monkeypatch.setattr(config, "PROVIDER", "google")
        monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key")
        monkeypatch.setattr(config, "EIA_API_KEY", "test-key")
        monkeypatch.setattr(services, "compute_detours", lambda trip, top_n=None: trip["stations"])
    app = AppTest.from_file(str(APP), default_timeout=30)
    for k, v in (session or {}).items():
        app.session_state[k] = v
    app.run()
    return app, calls


def links(app):
    return {b.proto.label: b.proto for b in app.get("link_button")}


def query(url):
    return parse_qs(urlsplit(url).query)


# --------------------------------------------------------------------- synthetic mode
def test_a_worthwhile_station_is_the_headline_and_the_links_are_off(monkeypatch):
    app, _ = make_app(monkeypatch)
    success = app.get("success")
    assert len(success) == 1 and success[0].proto.title.startswith("Fill up at ")
    assert "saves about $" in success[0].proto.body
    pair = links(app)
    assert set(pair) == {"Open in Google Maps", "Navigate via this station"}
    assert all(p.disabled for p in pair.values())
    assert not app.exception


def test_a_high_minimum_saving_turns_the_answer_into_skip(monkeypatch):
    app, _ = make_app(monkeypatch)
    app.number_input(key="min_saving").set_value(20.0).run()
    assert not app.get("success") and not app.get("link_button")
    assert any(i.proto.title == "No detour pays off enough" for i in app.get("info"))
    assert not app.exception


def test_other_good_options_are_listed_when_there_are_any(monkeypatch):
    app, _ = make_app(monkeypatch)
    assert any("Other good options" in m.value for m in app.markdown)


def test_no_stations_gets_a_clear_message_not_a_crash(monkeypatch):
    app, _ = make_app(monkeypatch, stations=[])
    assert any(w.proto.title == "No stations found near this route" for w in app.get("warning"))
    assert not app.get("success") and not app.exception


def test_no_priced_station_asks_for_a_price(monkeypatch):
    unpriced = [{"id": "x1", "name": "Unpriced", "lat": 27.5, "lng": -82.5, "price": None,
                 "off_route_m": 100.0}]
    app, _ = make_app(monkeypatch, stations=unpriced)
    assert any(w.proto.title == "No station has a price yet" for w in app.get("warning"))
    assert not app.exception


# --------------------------------------------------------------------- google mode (faked)
def test_the_links_carry_the_place_id_and_the_trip(monkeypatch):
    app, _ = make_app(monkeypatch, mode="google")
    pair = links(app)
    assert not any(p.disabled for p in pair.values())
    pin, nav = query(pair["Open in Google Maps"].url), query(pair["Navigate via this station"].url)
    assert pin["query_place_id"][0].startswith("ChIJ_")
    assert nav["waypoint_place_ids"] == pin["query_place_id"]
    assert nav["origin"] == ["Sarasota, FL"] and nav["destination"] == ["Orlando, FL"]
    assert nav["travelmode"] == ["driving"] and "key" not in nav


def test_a_location_origin_goes_into_the_navigation_link_as_coordinates(monkeypatch):
    app, _ = make_app(monkeypatch, mode="google", session={"my_location": (27.9506, -82.4572)})
    nav = query(links(app)["Navigate via this station"].url)
    assert nav["origin"] == ["27.950600,-82.457200"]


def test_a_hostile_station_name_is_escaped_in_the_headline(monkeypatch):
    app, _ = make_app(monkeypatch, mode="google", rename_best="**x**[a](http://evil)$y$")
    title = app.get("success")[0].proto.title
    assert title == "Fill up at \\*\\*x\\*\\*\\[a\\]\\(http://evil\\)\\$y\\$"
    assert "evil" not in " ".join(links(app)[k].url for k in links(app))  # names never reach URLs


def test_typing_an_address_does_not_start_a_search_until_find_stations(monkeypatch):
    app, calls = make_app(monkeypatch, mode="google")
    first = len(calls)
    app.text_input(key="trip_origin").set_value("Tampa, FL").run()
    assert len(calls) == first, "typing alone started a (paid) search"
    app.text_input(key="trip_origin").set_value("Tampa, FL")  # type and press the button together
    next(b for b in app.button if b.label == "Find stations").click().run()
    assert calls[-1][0] == "Tampa, FL" and len(calls) == first + 1
    assert not app.exception


def test_changing_the_corridor_waits_for_find_stations_too(monkeypatch):
    app, calls = make_app(monkeypatch, mode="google")
    first = len(calls)
    app.number_input(key="buffer_km").set_value(8.0).run()
    assert len(calls) == first
    app.number_input(key="buffer_km").set_value(8.0)
    next(b for b in app.button if b.label == "Find stations").click().run()
    assert calls[-1][2] == 8000.0
