"""Google Maps URLs for the recommended station: https://developers.google.com/maps/documentation/urls

These links need no API key and cost nothing: they only open Google Maps when the user taps one.
Only coordinates, place ids and the addresses the user typed go into a URL; a station name
(untrusted text) never does. A link that would exceed Google's 2,048-character limit is rejected.
"""
import math
from urllib.parse import quote

BASE = "https://www.google.com/maps"
MAX_URL = 2048


def _coord(lat, lng):
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
             for v in (lat, lng))
    if not ok or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError(f"invalid coordinates: {lat!r}, {lng!r}")
    return f"{lat:.6f},{lng:.6f}"


def _place(p, what):
    """A typed address, or a (lat, lng) pair, as URL text."""
    if isinstance(p, (tuple, list)) and len(p) == 2:
        return _coord(*p)
    if isinstance(p, str) and p.strip():
        return p.strip()
    raise ValueError(f"{what} is required")


def _enc(text):
    return quote(text, safe="")


def _checked(url):
    if len(url) > MAX_URL:
        raise ValueError("the link would be longer than Google Maps accepts")
    return url


def pin_url(lat, lng, place_id=None):
    """Open the station as a pin. The place id, when known, makes Maps show the exact place."""
    url = f"{BASE}/search/?api=1&query={_enc(_coord(lat, lng))}"
    if place_id:
        url += f"&query_place_id={_enc(str(place_id))}"
    return _checked(url)


def directions_url(origin, destination, via, via_place_id=None):
    """Driving directions from `origin` (optional: Maps then uses the current position) to
    `destination`, passing through the station `via` = (lat, lng)."""
    url = f"{BASE}/dir/?api=1&travelmode=driving"
    if origin is not None:
        url += f"&origin={_enc(_place(origin, 'origin'))}"
    url += f"&destination={_enc(_place(destination, 'destination'))}"
    if via is None:
        raise ValueError("the station to pass through is required")
    url += f"&waypoints={_enc(_coord(*via))}"
    if via_place_id:
        url += f"&waypoint_place_ids={_enc(str(via_place_id))}"
    return _checked(url)
