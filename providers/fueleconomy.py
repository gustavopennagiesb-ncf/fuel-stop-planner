"""fueleconomy.gov web services (EPA/DOE): look up a car's mpg. Free, no key, JSON.

Flow: year -> make -> model -> options (the options value is the vehicle id) -> vehicle record.
The record carries three EPA label figures: city08, highway08 and comb08 (55% city / 45%
highway). For electric cars these are MPGe, not gallons, so callers must check `fuel_type`.
The record has no tank size: the driver types it.
"""
import requests

BASE = "https://www.fueleconomy.gov/ws/rest/vehicle"
KINDS = ("combined", "city", "highway")


def _get(path, params=None, session=None):
    s = session or requests
    r = s.get(f"{BASE}/{path}", params=params, headers={"Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_menu(payload):
    """The service returns a list, a single object (one choice) or nothing; always give a
    list of (text, value) pairs."""
    items = (payload or {}).get("menuItem") or []
    if isinstance(items, dict):
        items = [items]
    return [(i["text"], i["value"]) for i in items]


def years(session=None):
    return [v for _, v in parse_menu(_get("menu/year", session=session))]


def makes(year, session=None):
    return [v for _, v in parse_menu(_get("menu/make", {"year": year}, session))]


def models(year, make, session=None):
    return [v for _, v in parse_menu(_get("menu/model", {"year": year, "make": make}, session))]


def options(year, make, model, session=None):
    """[(label, vehicle_id)], e.g. ('Auto (S8), 6 cyl, 3.5 L', '47085')."""
    return parse_menu(_get("menu/options", {"year": year, "make": make, "model": model}, session))


def _mpg(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def parse_vehicle(payload):
    return {"id": str(payload.get("id", "")),
            "label": f"{payload.get('year', '?')} {payload.get('make', '?')} {payload.get('model', '?')}",
            "fuel_type": payload.get("fuelType") or "",
            "combined": _mpg(payload.get("comb08")), "city": _mpg(payload.get("city08")),
            "highway": _mpg(payload.get("highway08"))}


def vehicle(vehicle_id, session=None):
    return parse_vehicle(_get(str(vehicle_id), session=session))


def mpg_for(veh, kind="combined"):
    """mpg of kind combined|city|highway; falls back to combined, then None."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    return veh.get(kind) or veh.get("combined")


def is_electric(veh):
    """Pure EVs report MPGe (energy equivalent), which is not usable as gallons per mile."""
    return veh.get("fuel_type", "").strip().lower() == "electricity"
