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
