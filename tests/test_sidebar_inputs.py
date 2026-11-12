"""The sliders (sidebar, and mpg under Your car) also accept an exact typed number; the slider and the number box stay in sync."""
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"

# key, slider minimum, slider maximum
CONTROLS = [("gallons", 3.0, 25.0), ("mpg", 10.0, 60.0), ("vot", 0.0, 60.0),
            ("speed", 20.0, 70.0), ("buffer_km", 1.0, 10.0)]


@pytest.fixture
def at(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    return app


@pytest.mark.parametrize("key,lo,hi", CONTROLS)
def test_every_control_has_slider_and_number_box_with_the_same_start_value(at, key, lo, hi):
    slider, box = at.slider(key=key), at.number_input(key=f"{key}_num")
    assert slider.value == box.value
    assert lo <= box.value <= hi


@pytest.mark.parametrize("key,lo,hi", CONTROLS)
def test_typing_a_number_moves_the_slider(at, key, lo, hi):
    target = lo + 2 * 0.5 if lo + 1 < hi else lo
    at.number_input(key=f"{key}_num").set_value(target).run()
    assert at.slider(key=key).value == target
    assert at.number_input(key=f"{key}_num").value == target
    assert not at.exception


@pytest.mark.parametrize("key,lo,hi", CONTROLS)
def test_dragging_the_slider_updates_the_number_box(at, key, lo, hi):
    target = hi - 1.0
    at.slider(key=key).set_value(target).run()
    assert at.number_input(key=f"{key}_num").value == target


@pytest.mark.parametrize("key,lo,hi", CONTROLS)
def test_out_of_range_typed_values_never_break_the_sidebar(at, key, lo, hi):
    # The browser blocks out-of-range input; Streamlit's backend (and AppTest) discards an
    # out-of-range value and falls back to the minimum. Either way both controls must stay
    # inside the range, equal to each other, and the app must not raise.
    for typed in (hi + 100, lo - 100):
        at.number_input(key=f"{key}_num").set_value(typed).run()
        slider, box = at.slider(key=key), at.number_input(key=f"{key}_num")
        assert lo <= slider.value <= hi
        assert slider.value == box.value
        assert not at.exception


def test_mpg_set_by_the_car_lookup_shows_in_both_controls(at):
    at.session_state["mpg"] = 33.0
    at.run()
    assert at.slider(key="mpg").value == 33.0
    assert at.number_input(key="mpg_num").value == 33.0
    assert not at.exception
