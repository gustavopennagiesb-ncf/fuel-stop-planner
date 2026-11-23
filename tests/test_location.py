"""Use my current location as the route origin: parsing what the browser sends, choosing between
the location and the typed address, and the sidebar wiring (google mode; the paid APIs are
replaced by fixture data so no key is used)."""
import logging
from pathlib import Path

import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

import config
import location
import services
from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"


# ------------------------------------------------------------------ pure helpers
def test_nothing_clicked_yet_is_none():
    assert location.parse_fix(None) is None


def test_a_good_fix_is_rounded_to_about_ten_metres():
    got = location.parse_fix({"lat": 27.31234567, "lng": -82.50012345, "accuracy": 35.6})
    assert got == {"coords": (27.3123, -82.5001), "accuracy": 36}


@pytest.mark.parametrize("bad", [
    {"lat": 91, "lng": 0}, {"lat": 0, "lng": -181}, {"lat": float("nan"), "lng": 0},
    {"lat": "x", "lng": 0}, {"lat": 1}, "text", 5, [1, 2], {"lat": True, "lng": 0}])
def test_unusable_fixes_become_a_generic_error(bad):
    got = location.parse_fix(bad)
    assert set(got) == {"error"} and got["error"] == location.MESSAGES["unreadable"]


@pytest.mark.parametrize("code,key", [(0, "unsupported"), (1, "denied"), (2, "unavailable"),
                                      (3, "timeout")])
def test_error_codes_from_the_browser_map_to_fixed_messages(code, key):
    assert location.parse_fix({"error_code": code}) == {"error": location.MESSAGES[key]}


def test_free_text_from_the_browser_is_never_shown():
    got = location.parse_fix({"error_code": 99, "error": "<img src=x onerror=alert(1)>"})
    assert got == {"error": location.MESSAGES["unreadable"]}


def test_resolve_origin_prefers_the_location_over_the_typed_address():
    assert location.resolve_origin((27.3, -82.5), "Sarasota, FL") == (27.3, -82.5)
    assert location.resolve_origin(None, "Sarasota, FL") == "Sarasota, FL"


# ------------------------------------------------------------------ sidebar wiring
@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def make_app(monkeypatch, *, mode, fix=None, session=None):
    """Run app.py. In google mode the paid calls are replaced by fixture data and every origin
    that reaches load_trip is recorded."""
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    origins = []
    if mode == "google":
        monkeypatch.setattr(config, "PROVIDER", "google")
        monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key")
        monkeypatch.setattr(config, "EIA_API_KEY", "test-key")
        real_trip, real_series = services.load_trip, services.load_series

        def fake_trip(origin=None, destination=None, buffer_m=3000, provider=None,
                      fuel_type="REGULAR_UNLEADED"):
            origins.append(origin)
            return real_trip(None, None, buffer_m, provider="fixture")

        monkeypatch.setattr(services, "load_trip", fake_trip)
        monkeypatch.setattr(services, "load_series", lambda p: real_series("fixture"))
        monkeypatch.setattr(services, "compute_detours", lambda trip, **kw: trip["stations"])
    monkeypatch.setattr(location, "request_location", lambda key: fix)
    app = AppTest.from_file(str(APP), default_timeout=30)
    for k, v in (session or {}).items():
        app.session_state[k] = v
    app.run()
    return app, origins


def find(app):
    return next(b for b in app.button if b.label == "Find stations")


def captions(app):
    return " ".join(c.value for c in app.caption)


def test_fixture_mode_explains_that_location_needs_google_mode(monkeypatch):
    app, _ = make_app(monkeypatch, mode="fixture")
    assert "PROVIDER=google" in captions(app) and "location" in captions(app).lower()
    assert not app.exception


def test_a_fix_becomes_the_route_origin(monkeypatch):
    fix = {"lat": 27.31234, "lng": -82.5, "accuracy": 35}
    app, origins = make_app(monkeypatch, mode="google", fix=fix)
    assert "Using your current location" in captions(app)
    assert origins[-1] == "Sarasota, FL", "the location must wait for Find stations"
    find(app).click().run()
    assert origins[-1] == (27.3123, -82.5)
    assert not app.exception


def test_permission_denied_keeps_the_typed_address_and_says_why(monkeypatch):
    app, origins = make_app(monkeypatch, mode="google", fix={"error_code": 1})
    assert origins[-1] == "Sarasota, FL"
    assert any(location.MESSAGES["denied"] in w.value for w in app.warning)
    assert not app.exception


def test_clearing_returns_to_the_typed_address(monkeypatch):
    app, origins = make_app(monkeypatch, mode="google", session={"my_location": (27.3, -82.5)})
    find(app).click().run()
    assert origins[-1] == (27.3, -82.5)
    next(b for b in app.button if "typed address" in b.label).click().run()
    find(app).click().run()
    # the typed trip was searched before, so the 10-minute cache answers without a new call
    assert app.session_state["trip_applied"]["origin"]["label"] == "Sarasota, FL"
    assert "my_location" not in app.session_state or app.session_state["my_location"] is None


def test_the_position_is_never_logged(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    make_app(monkeypatch, mode="google", fix={"lat": 27.31234, "lng": -82.54321, "accuracy": 20})
    assert "27.31" not in caplog.text and "82.54" not in caplog.text
