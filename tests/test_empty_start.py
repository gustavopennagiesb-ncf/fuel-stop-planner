"""The app opens with no route, no car and no fuel level, so every choice visibly changes it,
and "Start over" brings it back to that state."""
from pathlib import Path

import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

import config
from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"
WHERE = ":material/local_gas_station: Where to stop"
SENS = ":material/calculate: Sensitivity"


@pytest.fixture(autouse=True)
def empty(monkeypatch):
    st.cache_data.clear()
    monkeypatch.setattr(config, "DEMO_TRIP", False)
    monkeypatch.setattr(fueleconomy, "_get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("x")))
    yield
    st.cache_data.clear()


def start(tab=WHERE):
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.session_state["main_tabs"] = tab
    app.run()
    return app


def html_text(app):
    return " ".join(str(h.proto.body) for h in app.get("html"))


def welcome_shown(app):
    return any("Choose where you are going and your car" in h.value for h in app.header)


def test_the_app_opens_with_nothing_chosen():
    app = start()
    assert not app.exception and welcome_shown(app)
    chips = html_text(app)
    assert "Route</small><b>Not set" in chips and "Car</small><b>Not set" in chips
    assert not [m for m in app.metric if m.label == "Price"]   # no recommendation yet


def test_choosing_the_route_and_the_car_shows_the_recommendation():
    app = start()
    next(b for b in app.button if b.label == "Find stations").click().run()
    assert welcome_shown(app)                                  # still needs the car
    app.slider(key="mpg").set_value(30.0).run()
    assert not app.exception and not welcome_shown(app)
    assert "30.0 mpg" in html_text(app)


def test_start_over_brings_back_the_empty_app():
    app = start()
    next(b for b in app.button if b.label == "Find stations").click().run()
    app.slider(key="mpg").set_value(30.0).run()
    next(b for b in app.button if b.label == "Start over").click().run()
    assert not app.exception and welcome_shown(app)
    assert "Car</small><b>Not set" in html_text(app)


def test_other_pages_ask_for_the_route_and_the_car_first():
    app = start(SENS)
    assert any("Choose where you are going and your car" in i.value for i in app.info)
    assistant = [i.value for i in app.info if "route and your car" in i.value]
    assert assistant, "the assistant should ask for the route and the car first"
