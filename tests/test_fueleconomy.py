import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from providers import fueleconomy as fe

CAMRY = {"id": 47085, "year": "2024", "make": "Toyota", "model": "Camry", "fuelType": "Regular",
         "comb08": "26", "city08": "22", "highway08": "33", "tankSize": None}


class FakeSession:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        payload = self.payload

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return R()


def test_parse_menu_handles_list_single_object_and_empty():
    assert fe.parse_menu({"menuItem": [{"text": "A", "value": "a"}, {"text": "B", "value": "b"}]}) \
        == [("A", "a"), ("B", "b")]
    assert fe.parse_menu({"menuItem": {"text": "Auto, 6 cyl", "value": "47085"}}) \
        == [("Auto, 6 cyl", "47085")]
    assert fe.parse_menu({}) == [] and fe.parse_menu(None) == []


def test_menu_calls_send_the_right_query():
    s = FakeSession({"menuItem": [{"text": "Camry", "value": "Camry"}]})
    assert fe.models("2024", "Toyota", session=s) == ["Camry"]
    url, params = s.calls[0]
    assert url.endswith("/menu/model") and params == {"year": "2024", "make": "Toyota"}


def test_options_returns_label_and_vehicle_id():
    s = FakeSession({"menuItem": {"text": "Auto (S8), 6 cyl, 3.5 L", "value": "47085"}})
    assert fe.options("2024", "Toyota", "Camry", session=s) == [("Auto (S8), 6 cyl, 3.5 L", "47085")]


def test_parse_vehicle_and_mpg_kinds():
    v = fe.parse_vehicle(CAMRY)
    assert v["label"] == "2024 Toyota Camry" and v["id"] == "47085"
    assert (v["combined"], v["city"], v["highway"]) == (26.0, 22.0, 33.0)
    assert fe.mpg_for(v) == 26.0 and fe.mpg_for(v, "city") == 22.0 and fe.mpg_for(v, "highway") == 33.0
    with pytest.raises(ValueError):
        fe.mpg_for(v, "fast")


def test_missing_or_zero_figures_fall_back_to_combined():
    v = fe.parse_vehicle({**CAMRY, "city08": "0", "highway08": None})
    assert v["city"] is None and v["highway"] is None
    assert fe.mpg_for(v, "city") == 26.0
    assert fe.mpg_for(fe.parse_vehicle({"comb08": "0"})) is None


def test_electric_vehicles_are_flagged_because_mpge_is_not_gallons():
    assert fe.is_electric(fe.parse_vehicle({**CAMRY, "fuelType": "Electricity"}))
    assert not fe.is_electric(fe.parse_vehicle(CAMRY))
    assert not fe.is_electric(fe.parse_vehicle({**CAMRY, "fuelType": "Premium Gas or Electricity"}))


def test_vehicle_fetches_by_id():
    s = FakeSession(CAMRY)
    assert fe.vehicle("47085", session=s)["combined"] == 26.0
    assert s.calls[0][0].endswith("/vehicle/47085")


def test_malformed_menu_items_are_skipped_and_non_dict_payloads_are_an_error():
    assert fe.parse_menu({"menuItem": [{"text": "A", "value": "a"}, {"text": "no value"}, "junk"]}) \
        == [("A", "a")]
    with pytest.raises(fe.FuelEconomyError):
        fe.parse_menu(["not", "a", "dict"])
    with pytest.raises(fe.FuelEconomyError):
        fe.parse_vehicle("<html>oops</html>")


def test_non_json_response_is_a_requests_error_so_callers_can_catch_one_type():
    import requests

    class HtmlSession:
        def get(self, url, params=None, headers=None, timeout=None):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    raise ValueError("not json")
            return R()

    with pytest.raises(requests.RequestException):
        fe.years(session=HtmlSession())


def test_requests_use_a_short_timeout():
    s = FakeSession({"menuItem": []})
    seen = {}
    original = s.get

    def spy(url, params=None, headers=None, timeout=None):
        seen["timeout"] = timeout
        return original(url, params, headers, timeout)

    s.get = spy
    fe.years(session=s)
    assert seen["timeout"] <= 5


def test_plug_in_hybrids_are_flagged_as_having_an_electric_part():
    assert fe.has_electric_part(fe.parse_vehicle({**CAMRY, "fuelType": "Premium Gas or Electricity"}))
    assert fe.has_electric_part(fe.parse_vehicle({**CAMRY, "fuelType": "Electricity"}))
    assert not fe.has_electric_part(fe.parse_vehicle(CAMRY))
