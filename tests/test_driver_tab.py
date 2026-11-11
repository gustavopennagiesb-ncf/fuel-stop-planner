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


@pytest.mark.parametrize("tank,mpg", [(5.0, 10.0), (5.0, 60.0), (40.0, 10.0), (40.0, 60.0)])
@pytest.mark.parametrize("level", [0, 8])
def test_extreme_tank_and_mpg_never_negative(at, tank, mpg, level):
    at.number_input(key="tank").set_value(tank)
    at.slider(key="mpg").set_value(mpg)
    at.select_slider(key="driver_level").set_value(level).run()
    assert not at.exception
    for label in ("Low miles", "Base miles", "High miles"):
        value = next(m for m in at.metric if m.label == label).value
        assert int(value.split()[0].replace(",", "")) >= 0


def test_expander_states_conservative_rule_correctly(at):
    text = " ".join(m.value for m in at.markdown)
    assert "max(25% of tank, Standard reserve)" in text


def _all_text(app):
    return " ".join(m.value for m in app.markdown) + " " + " ".join(c.value for c in app.caption)


def test_assumptions_visible_without_expander(at):
    text = _all_text(at)
    assert "Defaults are assumptions, not measured values" in text
    assert "mpg x 0.85 / x 1.10" in text
    assert "Using tank 13.0 gal and" in text and "mpg from the sidebar: check both." in text
    assert "rounded to the nearest 5" in text


def test_expander_hedges_are_present(at):
    text = _all_text(at)
    assert "Assumes the gauge is linear (E means 0 gal)" in text
    assert "not independently verified by us" in text
    assert "double-count" in text
    assert "Our own arithmetic, not an external source" in text
    assert "(sourced, our own math)" not in text


def test_table_uses_displayed_rounded_miles(at):
    df = next(d.value for d in at.dataframe if "Miles" in d.value.index)
    for col in df.columns:
        miles = df.loc["Miles", col]
        assert df.loc["Hours of work (approx., rounded down)", col] == int(miles // 15)
        assert df.loc["Orders (approx., rounded down)", col] == int(miles // 8)


def test_rounded_zero_miles_shows_refuel_warning(at):
    at.number_input(key="tank").set_value(5.5)
    at.slider(key="mpg").set_value(10.0)
    at.select_slider(key="driver_level").set_value(3).run()
    base = next(m for m in at.metric if m.label == "Base miles").value
    assert base == "0 mi"
    assert any("refuel soon" in w.value for w in at.warning)
