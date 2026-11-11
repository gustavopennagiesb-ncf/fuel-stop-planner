import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import driver
from driver import miles_until_reserve, reserve_gallons


def test_standard_reserve_uses_percent_above_floor_and_floor_below():
    assert reserve_gallons(40) == 6.0
    assert reserve_gallons(13) == 2.0
    assert reserve_gallons(5) == 2.0


def test_conservative_reserve_is_a_quarter_tank():
    assert reserve_gallons(12, "conservative") == 3.0


def test_conservative_never_below_standard():
    for t in (5, 8, 13, 40):
        assert reserve_gallons(t, "conservative") >= reserve_gallons(t, "standard")
    assert reserve_gallons(5, "conservative") == 2.0
    with pytest.raises(ValueError):
        reserve_gallons(13, "x")


def test_miles_example_from_spec():
    r = miles_until_reserve(4, 13, 25, 2.0)
    assert r["base"] == pytest.approx(112.5, abs=0.01)
    assert r["low"] == pytest.approx(61.09, abs=0.01)
    assert r["high"] == pytest.approx(146.09, abs=0.01)


def test_below_reserve_is_zero_not_negative():
    r = miles_until_reserve(0, 13, 25, 2.0)
    assert r["low"] == 0
    assert r["base"] == 0
    assert r["high"] == 0
    assert all(v >= 0 for v in r.values())
    assert miles_until_reserve(1, 13, 25, 2.0)["high"] > 0


def test_low_le_base_le_high_and_monotonic():
    prev = 0.0
    for level in range(0, 9):
        r = miles_until_reserve(level, 13, 25, 2.0)
        assert r["low"] <= r["base"] <= r["high"]
        assert r["base"] >= prev
        prev = r["base"]
    assert r["high"] <= 13 * 25 * driver.HIGH_MPG_FACTOR


def test_level_is_clamped_to_zero_and_eight():
    assert (miles_until_reserve(9, 13, 25, 2.0)["base"]
            == miles_until_reserve(8, 13, 25, 2.0)["base"])
    assert miles_until_reserve(-1, 13, 25, 2.0)["base"] == 0


def test_round_miles_nearest_five_half_up():
    assert driver.round_miles(112.5) == 115
    assert driver.round_miles(112.4) == 110
    assert driver.round_miles(0) == 0
    assert driver.round_miles(2.4) == 0
    assert driver.round_miles(2.5) == 5


def test_hours_and_orders_round_down():
    assert driver.to_hours_orders(112.5, 15, 8) == (7, 14)


def test_zero_miles_gives_zero():
    assert driver.to_hours_orders(0, 15, 8) == (0, 0)
    assert driver.to_hours_orders(-10, 15, 8) == (0, 0)


def test_nonpositive_rates_raise():
    with pytest.raises(ValueError):
        driver.to_hours_orders(100, 0, 8)
    with pytest.raises(ValueError):
        driver.to_hours_orders(100, 15, -1)


def test_status_three_states():
    assert driver.status(0, 0) == "At or below your reserve: refuel soon"
    assert driver.status(0, 40) == "Close to your reserve"
    assert driver.status(30, 100) == "Room before reserve"


def test_float_noise_does_not_undercount():
    assert driver.to_hours_orders(254.99999999999997, 15, 8) == (17, 31)
