"""Glue between providers and the UI. One function per data need, switched by PROVIDER."""
import os
import time

import pandas as pd

import config
from geo import corridor_filter, decode_polyline, sample_points
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
_detour_memo = {}  # (origin, destination, lat, lng) -> (timestamp, extra_miles, extra_minutes)


def _route_detour(trip, s, now):
    """Extra miles/minutes of passing through station s, memoized so that moving a slider or
    editing a price in the UI does not bill the Routes API again for the same station."""
    key = (trip["origin"], trip["destination"], s["lat"], s["lng"])
    hit = _detour_memo.get(key)
    if hit and now - hit[0] < DETOUR_TTL_S:
        return hit[1], hit[2]
    via = routes.compute_route(config.GOOGLE_API_KEY, trip["origin"], trip["destination"],
                               via=(s["lat"], s["lng"]))
    d = trip["direct"]
    # The via route can come back shorter than the direct one (different road choice or traffic
    # snapshot); a detour is never negative, and a negative one would turn fuel burned into fuel gained.
    miles = max(0.0, (via["distanceMeters"] - d["distanceMeters"]) / 1609.344)
    minutes = max(0.0, (via["seconds"] - d["seconds"]) / 60.0)
    _detour_memo[key] = (now, miles, minutes)
    return miles, minutes


def compute_detours(trip, top_n=None):
    """Exact detours via Routes for the N cheapest priced stations (N extra calls, memoized).
    Other stations, and any station whose call fails, keep the geometric out-and-back
    approximation computed in detour.rank_stations; 'detour_source' says which one applies.
    Fixture mode returns the stations unchanged."""
    if trip["mode"] != "google":
        return trip["stations"]
    top_n = config.MAX_ROUTE_CALLS if top_n is None else top_n
    cheapest = sorted((s for s in trip["stations"] if s.get("price")), key=lambda s: s["price"])
    exact = {s["id"] for s in cheapest[:top_n]}
    now, out = time.time(), []
    for s in trip["stations"]:
        source = "approx"
        if s["id"] in exact:
            try:
                miles, minutes = _route_detour(trip, s, now)
                s = {**s, "extra_miles": miles, "extra_minutes": minutes}
                source = "routes"
            except Exception:  # keep the demo alive: this station falls back to the estimate
                pass
        out.append({**s, "detour_source": source})
    return out
