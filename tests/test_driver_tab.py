from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture
def at(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    return app


def _base(app):
    return next(m for m in app.metric if m.label == "Base miles").value


def _miles(app):
    return int(_base(app).split()[0].replace(",", ""))


def test_tab_renders_with_defaults(at):
    assert not at.exception
    assert any(m.label == "Base miles" for m in at.metric)


def test_lower_level_never_raises_base_miles(at):
    at.select_slider(key="driver_level").set_value(6).run()
    high = _miles(at)
    at.select_slider(key="driver_level").set_value(2).run()
    assert _miles(at) <= high


def test_reserve_mode_conservative_not_more_miles(at):
    at.select_slider(key="driver_level").set_value(8).run()
    standard = _miles(at)
    at.radio(key="driver_reserve_mode").set_value("Conservative (1/4 tank)").run()
    assert not at.exception
    assert _miles(at) <= standard
