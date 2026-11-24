"""Glue between providers and the UI. One function per data need, switched by PROVIDER."""
import os
import time

import pandas as pd

import config
from geo import corridor_filter, decode_polyline, point_along, route_position, sample_points
from providers import eia, fixtures, places, routes

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "eia_weekly.csv")


def load_series(provider: str = None):
    provider = provider or config.PROVIDER
    if provider == "fixture" or not config.EIA_API_KEY:
        return fixtures.synthetic_eia(), "synthetic"
    try:
        df = eia.load_live(config.EIA_API_KEY)
    except Exception:  # offline or rate limited: reuse the last saved copy if there is one
        cached = load_cached_series()
        if cached is None:
            raise
        return cached, "EIA cached CSV"
    save_series(df)
    return df, "EIA live"


def save_series(df, path=CACHE_PATH):
    """Store the EIA series locally (public US government data, safe to keep in the repo)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if "wti" not in df.columns:  # crude fetch failed this time: keep the crude weeks already cached
        cached = load_cached_series(path)
        if cached is not None and "wti" in cached:
            df = df.assign(wti=cached["wti"].reindex(df.index))
    df.to_csv(path)


def load_cached_series(path=CACHE_PATH):
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, index_col="period", parse_dates=True)


def load_trip(origin=None, destination=None, buffer_m=3000, provider: str = None,
              fuel_type: str = "REGULAR_UNLEADED"):
    """Return {'path', 'polyline', 'stations', 'mode', 'warnings'}.

    fixture: synthetic route and stations. google: Routes for the path, Places along it
    (capped calls), corridor filter; detours then come from compute_detours().
    """
    provider = provider or config.PROVIDER
    warnings = []
    if provider == "fixture" or not config.GOOGLE_API_KEY:
        if provider != "fixture":
            warnings.append("GOOGLE_API_KEY missing: using synthetic fixture data.")
        path, poly = fixtures.synthetic_route()
        stations = corridor_filter(fixtures.synthetic_stations(path), path, buffer_m)
        return {"path": path, "polyline": poly, "stations": stations,
                "mode": "fixture", "warnings": warnings}
    direct = routes.compute_route(config.GOOGLE_API_KEY, origin, destination)
    path = decode_polyline(direct["polyline"])
    found = places.search_along(config.GOOGLE_API_KEY, sample_points(path, 25000),
                                max_calls=config.MAX_PLACES_CALLS, fuel_type=fuel_type)
    stations = corridor_filter(found, path, buffer_m)
    if not any(s["price"] for s in stations):
        warnings.append("Places returned no fuel prices for these stations: type prices "
                        "manually in the sidebar.")
    return {"path": path, "polyline": direct["polyline"], "stations": stations,
            "mode": "google", "direct": direct, "warnings": warnings,
            "origin": origin, "destination": destination}


DETOUR_TTL_S = 600
WINDOW_M = 5000  # assumption: 5 km of route before and after a station is enough to leave and rejoin
_detour_memo = {}  # (origin, destination, lat, lng) -> (timestamp, raw_miles, raw_minutes)


def _local_detour(trip, s, now):
    """Raw extra miles/minutes of passing through station s, measured on a short stretch of the
    route around it: Routes from a point WINDOW_M before the station to a point WINDOW_M after,
    with and without the station as a `via` waypoint. A whole-trip via route is not used: forcing
    a waypoint can make Routes pick a different road for the whole trip, which measures a route
    change, not the detour. Memoized so a slider move or a price edit does not bill again."""
    key = (trip["origin"], trip["destination"], s["lat"], s["lng"])
    hit = _detour_memo.get(key)
    if hit and now - hit[0] < DETOUR_TTL_S:
        return hit[1], hit[2]
    path = trip["path"]
    along, _ = route_position((s["lat"], s["lng"]), path)
    a, b = point_along(path, along - WINDOW_M), point_along(path, along + WINDOW_M)
    # Without traffic (the cheaper Essentials SKU): over 10 km the detour is about roads, exits
    # and lights, and both legs are measured the same way.
    base = routes.compute_route(config.GOOGLE_API_KEY, a, b, traffic=False)
    via = routes.compute_route(config.GOOGLE_API_KEY, a, b, via=(s["lat"], s["lng"]),
                               traffic=False)
    # A via route can come back shorter or faster (another road, a traffic snapshot); a detour is
    # never negative, and a negative one would turn fuel burned into fuel gained.
    miles = max(0.0, (via["distanceMeters"] - base["distanceMeters"]) / 1609.344)
    minutes = max(0.0, (via["seconds"] - base["seconds"]) / 60.0)
    _detour_memo[key] = (now, miles, minutes)
    return miles, minutes


def _cheapest_first(stations):
    return [s["id"] for s in sorted((s for s in stations if s.get("price")),
                                    key=lambda s: s["price"])]


def compute_detours(trip, top_n=None, priority=None):
    """Exact detours via Routes for up to `top_n` priced stations (2 calls each, memoized).

    Every stop means leaving the road, the "fill up on your route" baseline included, so the
    smallest measured detour (the station closest to the route is always measured as that
    reference) is subtracted from every measured one: what remains is the extra a station costs
    over a stop on the route, the same thing the out-and-back estimate approximates for the
    others. `priority(stations)` returns station ids best first (the app ranks by estimated net
    benefit); stations are measured one at a time, always the best-ranked one not yet measured,
    re-ranking after each, so the stations at the top of the final ranking are the measured ones.
    Default priority: cheapest first. Unmeasured stations and failed calls keep the estimate;
    'detour_source' says which applies. Fixture mode returns the stations unchanged."""
    if trip["mode"] != "google":
        return trip["stations"]
    top_n = config.MAX_ROUTE_CALLS if top_n is None else top_n
    priority = priority or _cheapest_first
    by_id = {s["id"]: s for s in trip["stations"]}
    priced = [s for s in trip["stations"] if s.get("price")]
    raw, tried, now = {}, set(), time.time()

    def measure(s):
        tried.add(s["id"])
        try:
            raw[s["id"]] = _local_detour(trip, s, now)
        except Exception:  # keep the demo alive: this station falls back to the estimate
            pass

    def assemble():
        if not raw:
            return [{**s, "detour_source": "approx"} for s in trip["stations"]]
        ref_mi = min(m for m, _ in raw.values())
        ref_min = min(t for _, t in raw.values())
        return [{**s, "extra_miles": raw[s["id"]][0] - ref_mi,
                 "extra_minutes": raw[s["id"]][1] - ref_min, "detour_source": "routes"}
                if s["id"] in raw else {**s, "detour_source": "approx"}
                for s in trip["stations"]]

    if priced and top_n > 0:
        measure(min(priced, key=lambda s: s["off_route_m"]))   # the on-route reference stop
    while len(tried) < min(top_n, len(priced)):
        nxt = next((i for i in priority(assemble()) if i not in tried and by_id[i].get("price")),
                   None)
        if nxt is None:
            break
        measure(by_id[nxt])
    return assemble()
