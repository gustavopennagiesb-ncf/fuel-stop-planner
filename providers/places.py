"""Google Places API (New): nearby gas stations with fuelOptions.

UNVERIFIED on day 0: whether fuelPrices is populated for US stations. Billing: a field mask
with fuelOptions is Enterprise + Atmosphere (1,000 free calls/month per SKU), so the number
of calls per search is capped in config. Google forbids storing Places content (only place
IDs may be kept indefinitely) and requires showing it on a Google map.
"""
import requests

URL = "https://places.googleapis.com/v1/places:searchNearby"
FIELD_MASK = "places.id,places.displayName,places.location,places.fuelOptions"


def build_body(lat, lng, radius_m=5000, max_results=20):
    return {"includedTypes": ["gas_station"], "maxResultCount": max_results,
            "locationRestriction": {"circle": {
                "center": {"latitude": lat, "longitude": lng}, "radius": float(radius_m)}}}


def money_to_float(money):
    """Google Money: units arrives as a string (int64), nanos as an int."""
    return int(money.get("units", 0)) + money.get("nanos", 0) / 1e9


def parse_places(payload, fuel_type="REGULAR_UNLEADED"):
    """Normalize a searchNearby response. price is None when the station has no price."""
    out = []
    for p in payload.get("places", []):
        price, updated = None, None
        for fp in (p.get("fuelOptions") or {}).get("fuelPrices", []):
            if fp.get("type") == fuel_type and fp.get("price"):
                price, updated = money_to_float(fp["price"]), fp.get("updateTime")
        out.append({"id": p["id"], "name": (p.get("displayName") or {}).get("text", "?"),
                    "lat": p["location"]["latitude"], "lng": p["location"]["longitude"],
                    "price": price, "price_updated": updated, "synthetic": False})
    return out


def search_nearby(api_key, lat, lng, radius_m=5000, fuel_type="REGULAR_UNLEADED", session=None):
    s = session or requests
    r = s.post(URL, json=build_body(lat, lng, radius_m),
               headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK}, timeout=30)
    r.raise_for_status()
    return parse_places(r.json(), fuel_type)


def search_along(api_key, sample_pts, radius_m=4000, max_calls=6,
                 fuel_type="REGULAR_UNLEADED", session=None):
    """Query Places around up to max_calls sample points; de-duplicate by place id."""
    seen = {}
    step = max(1, len(sample_pts) // max_calls)
    for lat, lng in sample_pts[::step][:max_calls]:
        for st in search_nearby(api_key, lat, lng, radius_m, fuel_type, session):
            seen[st["id"]] = st
    return list(seen.values())
