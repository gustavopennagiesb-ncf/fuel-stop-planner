"""Parsing what the browser's address box sends back. Everything from the browser is untrusted."""
import pytest

import places_autocomplete as pa

GOOD = {"lat": 28.5383, "lng": -81.3792, "label": "Orlando, FL, USA", "place_id": "ChIJabc_DEF-1"}


def test_nothing_picked_yet_is_none():
    assert pa.parse_pick(None) is None


def test_a_good_pick_becomes_label_coordinates_and_place_id():
    assert pa.parse_pick(GOOD) == {"label": "Orlando, FL, USA", "coords": (28.5383, -81.3792),
                                   "place_id": "ChIJabc_DEF-1"}


def test_the_place_id_is_optional():
    got = pa.parse_pick({k: v for k, v in GOOD.items() if k != "place_id"})
    assert got["place_id"] is None and got["coords"] == (28.5383, -81.3792)


def test_label_whitespace_and_control_characters_are_cleaned():
    got = pa.parse_pick({**GOOD, "label": "  Orlando,\n FL\t\x00 "})
    assert got["label"] == "Orlando, FL"


@pytest.mark.parametrize("bad", [
    {**GOOD, "lat": 91}, {**GOOD, "lng": -181}, {**GOOD, "lat": float("nan")},
    {**GOOD, "lat": "28.5"}, {**GOOD, "lat": True}, {**GOOD, "label": ""}, {**GOOD, "label": "  "},
    {**GOOD, "label": 5}, {**GOOD, "label": "x" * 201}, {**GOOD, "place_id": "has space"},
    {**GOOD, "place_id": "a" * 301}, {**GOOD, "place_id": 12}, "text", 5, [1, 2], {}])
def test_unusable_picks_become_a_generic_error(bad):
    assert pa.parse_pick(bad) == {"error": pa.MESSAGES["unreadable"]}


@pytest.mark.parametrize("code,key", [(0, "unsupported"), (1, "unavailable")])
def test_error_codes_from_the_browser_map_to_fixed_messages(code, key):
    assert pa.parse_pick({"error_code": code}) == {"error": pa.MESSAGES[key]}


def test_free_text_errors_from_the_browser_are_never_shown():
    got = pa.parse_pick({"error_code": 99, "error": "<img src=x onerror=alert(1)>"})
    assert got == {"error": pa.MESSAGES["unreadable"]}


def test_a_hostile_label_is_kept_as_plain_text_for_the_caller_to_escape():
    got = pa.parse_pick({**GOOD, "label": "**x**[a](http://evil)$y$"})
    assert got["label"] == "**x**[a](http://evil)$y$"
