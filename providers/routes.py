"""Google Routes API v2 computeRoutes (traffic-aware routing billed as Pro).

UNVERIFIED: semantics of `intermediates` (stop vs via) and departureTime format. Each
station detour costs one extra call: direct route + N via-station routes.
"""
import requests

URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"


def _waypoint(w):
    if isinstance(w, str):
        return {"address": w}
    return {"location": {"latLng": {"latitude": w[0], "longitude": w[1]}}}


def build_body(origin, destination, via=None, traffic=True):
    body = {"origin": _waypoint(origin), "destination": _waypoint(destination),
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE" if traffic else "TRAFFIC_UNAWARE"}
    if via:
        body["intermediates"] = [_waypoint(via)]
    return body


def parse_route(payload):
    """Return {'distanceMeters', 'seconds', 'polyline'} from the first route."""
    r = payload["routes"][0]
    return {"distanceMeters": int(r["distanceMeters"]),
            "seconds": int(str(r["duration"]).rstrip("s")),
            "polyline": r["polyline"]["encodedPolyline"]}


def compute_route(api_key, origin, destination, via=None, traffic=True, session=None):
    s = session or requests
    r = s.post(URL, json=build_body(origin, destination, via, traffic),
               headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK}, timeout=30)
    r.raise_for_status()
    return parse_route(r.json())
