"""The sidebar car lookup: apply-once, hand edits survive, kind switching, and outages."""
from pathlib import Path

import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"
CAR = {"id": 47085, "year": "2024", "make": "Toyota", "model": "Camry", "fuelType": "Regular",
       "comb08": "26", "city08": "22", "highway08": "33"}


def fake_get_for(vehicle):
    def fake_get(path, params=None, session=None):
        menus = {"menu/year": "2024", "menu/make": "Toyota", "menu/model": "Camry"}
        if path in menus:
            return {"menuItem": [{"text": menus[path], "value": menus[path]}]}
        if path == "menu/options":
            return {"menuItem": {"text": "Auto, 6 cyl", "value": "47085"}}
        return vehicle
    return fake_get


@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def make_app(monkeypatch, getter):
    monkeypatch.setattr(fueleconomy, "_get", getter)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    return app


def pick_car(app):
    app.selectbox(key="car_year").set_value("2024").run()
    app.selectbox(key="car_make").set_value("Toyota").run()
    app.selectbox(key="car_model").set_value("Camry").run()
    app.selectbox(key="car_version").set_value("Auto, 6 cyl").run()
    return app


def epa_kind(app):
    return app.button_group(key="epa_kind")


def mpg(app):
    return app.slider(key="mpg").value, app.number_input(key="mpg_num").value


def test_picking_a_car_applies_combined_mpg_to_both_controls(monkeypatch):
    app = pick_car(make_app(monkeypatch, fake_get_for(CAR)))
    assert mpg(app) == (26.0, 26.0)
    assert not app.exception


def test_a_hand_edit_survives_later_reruns(monkeypatch):
    app = pick_car(make_app(monkeypatch, fake_get_for(CAR)))
    app.number_input(key="mpg_num").set_value(31.0).run()
    app.sidebar.slider(key="gallons").set_value(12.0).run()
    assert mpg(app) == (31.0, 31.0)


def test_switching_the_figure_kind_applies_that_figure(monkeypatch):
    app = pick_car(make_app(monkeypatch, fake_get_for(CAR)))
    epa_kind(app).set_value("city").run()
    assert mpg(app) == (22.0, 22.0)
    epa_kind(app).set_value("highway").run()
    assert mpg(app) == (33.0, 33.0)


def test_plug_in_hybrid_shows_a_warning(monkeypatch):
    phev = {**CAR, "fuelType": "Premium Gas or Electricity"}
    app = pick_car(make_app(monkeypatch, fake_get_for(phev)))
    assert any("Plug-in hybrid" in w.value for w in app.warning)


def test_outage_does_not_crash_and_is_not_retried_on_every_rerun(monkeypatch):
    calls = []

    def down(path, params=None, session=None):
        calls.append(path)
        raise requests.ConnectionError("offline")

    app = make_app(monkeypatch, down)
    first = len(calls)
    assert first >= 1 and not app.exception
    assert any("not reachable" in c.value for c in app.caption)
    app.sidebar.slider(key="gallons").set_value(12.0).run()
    app.sidebar.slider(key="gallons").set_value(14.0).run()
    assert len(calls) == first, "the outage was retried on a rerun"
    assert not app.exception


def test_malformed_response_does_not_crash_the_sidebar(monkeypatch):
    app = make_app(monkeypatch, lambda path, params=None, session=None: ["unexpected"])
    assert not app.exception
    assert any("not reachable" in c.value for c in app.caption)
