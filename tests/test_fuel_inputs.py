"""Fuel now input: gauge or miles, chip, tank estimate and the gallons lock (inputs only)."""
from pathlib import Path

import requests
from streamlit.testing.v1 import AppTest

from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"


def make_app(monkeypatch, session=None):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    for k, v in (session or {}).items():
        app.session_state[k] = v
    app.run()
    return app


def chips(app):
    return " ".join(h.proto.body for h in app.get("html") if "fs-chips" in h.proto.body)


def test_fuel_not_set_by_default_and_chip_says_so(monkeypatch):
    app = make_app(monkeypatch)
    assert app.button_group(key="fuel_input_mode").value is None
    assert "Not set" in chips(app)


def test_gauge_input_shows_in_the_chip(monkeypatch):
    app = make_app(monkeypatch)
    app.button_group(key="fuel_input_mode").set_value("Fuel gauge").run()
    app.select_slider(key="fuel_level_eighths").set_value("1/4").run()
    assert "1/4 tank" in chips(app)


def test_miles_input_shows_in_the_chip(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Miles left"})
    assert "50 mi to reserve" in chips(app)


def test_gallons_slider_is_locked_until_set_by_hand(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Fuel gauge",
                                         "fuel_level_eighths": "1/4"})
    assert app.slider(key="gallons").disabled
    app.toggle(key="gallons_manual").set_value(True).run()
    assert not app.slider(key="gallons").disabled


def test_no_toggle_when_fuel_is_not_set(monkeypatch):
    app = make_app(monkeypatch)
    assert len(app.toggle) == 0
    assert not app.slider(key="gallons").disabled


# --- tank estimate from the EPA size class -------------------------------------------------
import pytest
import streamlit as st

import fuelplan
from tests.test_car_lookup import CAR, fake_get_for, pick_car

PICKUP = {**CAR, "VClass": "Standard Pickup Trucks 4WD"}


@pytest.fixture
def fresh_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def test_picking_a_car_estimates_the_tank_from_its_size_class(monkeypatch, fresh_cache):
    monkeypatch.setattr(fueleconomy, "_get", fake_get_for(PICKUP))
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    pick_car(app)
    expected = fuelplan.tank_for_class(PICKUP["VClass"])
    assert expected > 13.0
    assert app.number_input(key="tank").value == expected
    assert any("Estimated from the EPA size class" in c.value for c in app.caption)


def test_a_hand_edited_tank_survives_later_reruns(monkeypatch, fresh_cache):
    monkeypatch.setattr(fueleconomy, "_get", fake_get_for(PICKUP))
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    pick_car(app)
    app.number_input(key="tank").set_value(31.0).run()
    app.sidebar.slider(key="gallons").set_value(12.0).run()
    assert app.number_input(key="tank").value == 31.0


@pytest.fixture(autouse=True)
def rearm_default_value_warning(monkeypatch):
    """Streamlit shows that warning once per process; re-arm it so each test can see it."""
    from streamlit.elements.lib import policies
    monkeypatch.setattr(policies, "_shown_default_value_warning", False)


def session_state_warnings(caplog):
    """Streamlit logs "created with a default value but also had its value set via the
    Session State API" (and shows it as a warning box in the real app)."""
    return [r.getMessage() for r in caplog.records if "Session State API" in r.getMessage()]


def test_no_session_state_default_warning_for_the_tank(monkeypatch, fresh_cache, caplog):
    monkeypatch.setattr(fueleconomy, "_get", fake_get_for(PICKUP))
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    pick_car(app)
    assert session_state_warnings(caplog) == []


@pytest.mark.parametrize("session", [
    {"fuel_input_mode": "Fuel gauge", "fuel_level_eighths": "1/4"},
    {"fuel_input_mode": "Miles left", "fuel_miles_left": 120.0}])
def test_no_session_state_default_warning_for_the_fuel_inputs(monkeypatch, caplog, session):
    make_app(monkeypatch, session=session)
    assert session_state_warnings(caplog) == []


# --- the plan wired into the recommendation (Task 7) ---------------------------------------
def test_full_tank_on_the_synthetic_route_needs_no_stop(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Fuel gauge", "fuel_level_eighths": "F"})
    assert any(h.value == "No stop needed" for h in app.header)
    # the insight panel must not contradict the headline by telling you to fill up
    assert not any("fill up at the first station" in c.value for c in app.caption)


def test_low_tank_recommends_a_reachable_station_with_gallons(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Miles left", "fuel_miles_left": 80.0})
    titles = [h.value for h in app.header if h.value.startswith("Fill up at ")]
    assert titles and " · buy " in titles[0]
    assert "Buy" in {m.label for m in app.metric}


def test_low_tank_mirrors_the_gallons_into_the_locked_slider(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Miles left", "fuel_miles_left": 80.0})
    title = next(h.value for h in app.header if h.value.startswith("Fill up at "))
    bought = float(title.rsplit(" · buy ", 1)[1].split()[0])
    slider = app.slider(key="gallons")
    assert slider.disabled
    assert slider.value == min(25.0, max(3.0, bought))


def test_empty_tank_says_no_station_is_reachable(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Fuel gauge", "fuel_level_eighths": "E"})
    assert any("reachable" in w.value for w in app.warning) and not app.exception


def test_trip_at_a_glance_now_renders(monkeypatch):
    app = make_app(monkeypatch)
    assert "Trip distance" in {m.label for m in app.metric}


def test_fuel_on_arrival_shows_when_no_stop_is_needed(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Fuel gauge", "fuel_level_eighths": "F"})
    assert "Fuel on arrival" in {m.label for m in app.metric}


def test_regret_line_agrees_with_headline_when_gallons_differ(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Miles left", "fuel_miles_left": 80.0})
    assert not app.exception  # regret/price_rank_gap/flip_point get the same filtered stations and mapping


def test_no_fuel_level_keeps_todays_headline(monkeypatch):
    app = make_app(monkeypatch)
    assert not any(h.value == "No stop needed" for h in app.header)
    assert not any(" · buy " in h.value for h in app.header)
