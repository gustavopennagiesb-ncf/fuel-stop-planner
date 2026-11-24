"""SYNTHETIC data for fixture mode (no keys needed). Nothing here is real EIA or Google
data: never commit recorded Google responses, because Places content may not be stored."""
import numpy as np
import pandas as pd

from geo import encode_polyline

WAYPOINTS = [(27.3364, -82.5307), (27.9506, -82.4572), (28.0395, -81.9498), (28.5383, -81.3792)]
ROUTE_NAME = "Sarasota -> Orlando (synthetic route)"


def synthetic_route(points_per_leg=25):
    """Interpolated path through Sarasota, Tampa, Lakeland and Orlando; plus its polyline."""
    path = []
    for (la1, lo1), (la2, lo2) in zip(WAYPOINTS[:-1], WAYPOINTS[1:]):
        for t in np.linspace(0, 1, points_per_leg, endpoint=False):
            path.append((la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t))
    path.append(WAYPOINTS[-1])
    return path, encode_polyline(path)


def synthetic_stations(path, n=30, seed=7):
    """Stations scattered along the route with prices around $3.55/gal (synthetic)."""
    rng = np.random.default_rng(seed)
    brands = ["Sunway", "QuickFill", "Palm Oil", "RoadStop", "Gator Gas"]
    out = []
    for i in range(n):
        idx = int(rng.integers(0, len(path) - 1))
        lat, lng = path[idx]
        off_km = rng.exponential(2.0)               # most stations close to the route
        ang = rng.uniform(0, 2 * np.pi)
        lat += off_km * np.sin(ang) / 111.0
        lng += off_km * np.cos(ang) / (111.0 * np.cos(np.radians(lat)))
        price = round(3.55 + rng.normal(0, 0.12) + (-0.10 if off_km > 2 else 0.0), 3)
        out.append({"id": f"SYN{i:03d}", "name": f"{brands[i % 5]} #{i}", "lat": lat, "lng": lng,
                    "price": price, "price_updated": None, "synthetic": True})
    return out


def synthetic_eia(weeks=1200, seed=11):
    """Weekly random walk with a seasonal term and a few missing weeks, for Florida and US, plus a crude ($/bbl) column that leads the U.S. pump."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2003-09-01", periods=weeks, freq="W-MON")
    season = 0.12 * np.sin(2 * np.pi * np.arange(weeks) / 52)
    walk = np.cumsum(rng.normal(0, 0.03, weeks)) + 2.2
    us = np.clip(walk + season, 1.0, None)
    fl = np.clip(us + rng.normal(-0.05, 0.02, weeks) + 0.03 * np.sin(np.arange(weeks) / 9), 1.0, None)
    # crude leads the U.S. pump by one week; its own generator keeps the retail values unchanged
    rng_crude = np.random.default_rng(seed + 1)
    us_next = np.append(us[1:], us[-1])
    wti = 42 * (us_next - 0.85) + rng_crude.normal(0, 1.5, weeks)
    df = pd.DataFrame({"florida": fl.round(3), "us": us.round(3), "wti": wti.round(2)}, index=idx).rename_axis("period")
    df.loc[df.index[[150, 151, 600]], :] = np.nan   # a few missing weeks for the EDA
    return df
