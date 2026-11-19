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
