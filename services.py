"""Glue between providers and the UI. One function per data need, switched by PROVIDER."""
import os

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


def load_trip(origin=None, destination=None, buffer_m=3000, provider: str = None):
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
                                max_calls=config.MAX_PLACES_CALLS)
    stations = corridor_filter(found, path, buffer_m)
    if not any(s["price"] for s in stations):
        warnings.append("Places returned no fuel prices for these stations: type prices "
                        "manually in the sidebar.")
    return {"path": path, "polyline": direct["polyline"], "stations": stations,
            "mode": "google", "direct": direct, "warnings": warnings,
            "origin": origin, "destination": destination}


def compute_detours(trip, top_n=None):
    """Exact detours via Routes for the top N stations (N+1 calls in total). Fixture mode
    keeps the geometric out-and-back approximation computed in detour.rank_stations."""
    if trip["mode"] != "google":
        return trip["stations"]
    top_n = top_n or config.MAX_ROUTE_CALLS
    out = []
    for i, s in enumerate(trip["stations"]):
        if i < top_n and s.get("price"):
            via = routes.compute_route(config.GOOGLE_API_KEY, trip["origin"], trip["destination"],
                                       via=(s["lat"], s["lng"]))
            d = trip["direct"]
            s = {**s, "extra_miles": (via["distanceMeters"] - d["distanceMeters"]) / 1609.344,
                 "extra_minutes": (via["seconds"] - d["seconds"]) / 60.0}
        out.append(s)
    return out
