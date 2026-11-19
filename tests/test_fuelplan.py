import pytest
import fuelplan as fp

PATH = [(27.0, -82.0), (28.0, -82.0)]           # ~69 mi straight north

def st(i, lat, price, off=0.0):
    return {"id": f"s{i}", "name": f"S{i}", "lat": lat, "lng": -82.0, "price": price, "off_route_m": off}

def test_trip_miles_and_degenerate_paths():
    assert fp.trip_miles(PATH) == pytest.approx(69.1, abs=0.5)
    assert fp.trip_miles([(27.0, -82.0)]) == 0.0 and fp.trip_miles([]) == 0.0

def test_fuel_on_board_gauge_and_miles():
    assert fp.fuel_on_board("gauge", 2, 16, 25, 2.4) == 4.0           # 2/8 of 16
    assert fp.fuel_on_board("gauge", 12, 16, 25, 2.4) == 16.0         # clamped to the tank
    assert fp.fuel_on_board("miles", 50, 16, 25, 2.4) == pytest.approx(4.4)  # 50/25 + reserve
    assert fp.fuel_on_board("miles", 900, 16, 25, 2.4) == pytest.approx(38.4)  # not capped by tank

def test_tank_for_class_falls_back_to_13():
    assert fp.tank_for_class(None) == 13.0 and fp.tank_for_class("Spaceship") == 13.0
    assert fp.tank_for_class("Standard Pickup Trucks 4WD") > fp.tank_for_class("Compact Cars")

def test_full_tank_needs_no_stop_and_reports_arrival():
    r = fp.plan([st(1, 27.5, 3.0)], PATH, fuel_gal=16, tank_gal=16, mpg=25, reserve_gal=2.4, p_ref=3.5)
    assert r["stop_needed"] is False
    assert r["arrive_gal"] == pytest.approx(16 - 69.1 / 25, abs=0.05)

def test_arriving_with_exactly_the_reserve_is_no_stop():
    need = fp.trip_miles(PATH) / 25 + 2.4
    assert fp.plan([], PATH, need, 16, 25, 2.4, 3.5)["stop_needed"] is False

def test_unreachable_station_is_excluded_and_cheaper_fills_up():
    sts = [st(1, 27.1, 3.0), st(2, 27.9, 3.0), st(3, 27.2, 3.9), st(4, 27.15, None)]
    r = fp.plan(sts, PATH, fuel_gal=3.0, tank_gal=16, mpg=25, reserve_gal=2.4, p_ref=3.5)
    ps = r["per_station"]
    assert r["stop_needed"] and ps["s1"]["reachable"] and not ps["s2"]["reachable"]
    assert ps["s1"]["gallons"] == pytest.approx(16 - ps["s1"]["fuel_at_station"], abs=0.1)  # fill
    assert ps["s3"]["gallons"] <= 16 - ps["s3"]["fuel_at_station"]                          # minimum
    assert ps["s3"]["gallons"] % 0.5 == 0                                                    # rounded up to 0.5
    assert "s4" not in ps                                                                    # unpriced

def test_gauge_on_empty_reaches_nothing():
    r = fp.plan([st(1, 27.1, 3.0)], PATH, 0.0, 16, 25, 2.4, 3.5)
    assert r["stop_needed"] and not r["per_station"]["s1"]["reachable"]

def test_trip_longer_than_a_tank_marks_short():
    long_path = [(25.0, -82.0), (31.0, -82.0)]                      # ~414 mi
    r = fp.plan([st(1, 25.1, 3.0)], long_path, 4.0, 12, 25, 2.0, 3.5)
    assert r["per_station"]["s1"]["short"] is True

def test_miles_left_bigger_than_tank_raises_effective_tank():
    r = fp.plan([], PATH, fuel_gal=20.0, tank_gal=13, mpg=25, reserve_gal=2.0, p_ref=3.5)
    assert r["effective_tank"] == 20.0 and r["tank_too_small"] is True

def test_degenerate_path_needs_no_stop():
    r = fp.plan([], [(27.0, -82.0)], 1.0, 16, 25, 2.4, 3.5)
    assert r["stop_needed"] is False and r["trip_miles"] == 0.0

def test_no_stop_needed_gallons_is_room():
    r = fp.plan([st(1, 27.5, 3.9)], PATH, 16, 16, 25, 2.4, 3.5)
    g = r["per_station"]["s1"]
    assert g["gallons"] == pytest.approx(16 - g["fuel_at_station"], abs=0.1)

def test_rounding_ignores_float_noise():
    assert fp._floor_tenth(2.5000000001) == 2.5 and fp._floor_tenth(2.4999999999) == 2.5
    assert fp._ceil_half(2.5000000001) == 2.5 and fp._ceil_half(2.6) == 3.0
