"""Google Maps URLs (no key, no billing): the exact shape matters because the links are what the
driver taps. Only coordinates and place ids go into a URL, never a station name."""
from urllib.parse import parse_qs, urlsplit

import pytest

import maps_links as ml


def query(url):
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_pin_url_with_coordinates_and_place_id():
    url = ml.pin_url(27.3364, -82.5307, "ChIJabc_DEF-123")
    assert url.startswith("https://www.google.com/maps/search/?api=1&")
    q = query(url)
    assert q["query"] == ["27.336400,-82.530700"]
    assert q["query_place_id"] == ["ChIJabc_DEF-123"]
    assert "%2C" in url  # the comma is encoded, as the Maps URLs docs ask


def test_pin_url_without_place_id_has_no_place_id_parameter():
    assert "query_place_id" not in ml.pin_url(27.3, -82.5)


def test_directions_url_passes_through_the_station():
    url = ml.directions_url("Sarasota, FL", "Orlando, FL", via=(27.9506, -82.4572),
                            via_place_id="ChIJxyz")
    assert url.startswith("https://www.google.com/maps/dir/?api=1&")
    q = query(url)
    assert q["origin"] == ["Sarasota, FL"] and q["destination"] == ["Orlando, FL"]
    assert q["waypoints"] == ["27.950600,-82.457200"]
    assert q["waypoint_place_ids"] == ["ChIJxyz"]
    assert q["travelmode"] == ["driving"]
    assert " " not in url and "|" not in url  # spaces and pipes are always encoded


def test_a_location_origin_becomes_lat_lng_text():
    q = query(ml.directions_url((27.9506, -82.4572), "Orlando, FL", via=(28.0, -82.0)))
    assert q["origin"] == ["27.950600,-82.457200"]


def test_origin_is_optional_and_destination_is_required():
    assert "origin" not in query(ml.directions_url(None, "Orlando, FL", via=(28.0, -82.0)))
    with pytest.raises(ValueError):
        ml.directions_url("Sarasota, FL", "  ", via=(28.0, -82.0))
    with pytest.raises(ValueError):
        ml.directions_url("Sarasota, FL", None, via=(28.0, -82.0))


def test_no_api_key_ever_appears_in_a_link():
    assert "key=" not in ml.pin_url(27.3, -82.5, "id")
    assert "key=" not in ml.directions_url("A", "B", via=(27.3, -82.5))


@pytest.mark.parametrize("lat,lng", [(91, 0), (-91, 0), (0, 181), (0, -181), (float("nan"), 0),
                                     ("x", 0), (None, 0), (True, 0)])
def test_invalid_coordinates_are_rejected(lat, lng):
    with pytest.raises(ValueError):
        ml.pin_url(lat, lng)
    with pytest.raises(ValueError):
        ml.directions_url("A", "B", via=(lat, lng))


def test_hostile_text_is_encoded_not_interpreted():
    url = ml.directions_url("a&destination=evil|b#frag", "B c", via=(27.3, -82.5))
    q = query(url)
    assert q["origin"] == ["a&destination=evil|b#frag"]   # one value, not extra parameters
    assert q["destination"] == ["B c"]
    assert "#" not in url


def test_overlong_urls_are_rejected():
    with pytest.raises(ValueError):
        ml.directions_url("x" * 3000, "B", via=(27.3, -82.5))
