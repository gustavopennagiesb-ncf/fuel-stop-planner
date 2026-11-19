"""fueleconomy.gov web services (EPA/DOE): look up a car's mpg. Free, no key, JSON.

Flow: year -> make -> model -> options (the options value is the vehicle id) -> vehicle record.
The record carries three EPA label figures: city08, highway08 and comb08 (55% city / 45%
highway). For electric cars these are MPGe, not gallons, so callers must check `fuel_type`.
The record has no tank size but carries the EPA size class, used to estimate one (fuelplan.tank_for_class).
"""
import requests

BASE = "https://www.fueleconomy.gov/ws/rest/vehicle"
KINDS = ("combined", "city", "highway")
TIMEOUT_S = 5  # a UI call: fail fast instead of freezing the page while the service is down


class FuelEconomyError(requests.RequestException):
    """The service answered with something that is not the expected JSON shape. It subclasses
    RequestException so a caller handles "unreachable" and "unusable" with one except."""


def _get(path, params=None, session=None):
    s = session or requests
    r = s.get(f"{BASE}/{path}", params=params, headers={"Accept": "application/json"},
              timeout=TIMEOUT_S)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError as e:
        raise FuelEconomyError(f"fueleconomy.gov did not return JSON: {e}") from e


def parse_menu(payload):
    """The service returns a list, a single object (one choice) or nothing; always give a
    list of (text, value) pairs."""
    if payload is not None and not isinstance(payload, dict):
        raise FuelEconomyError("unexpected menu response")
    items = (payload or {}).get("menuItem") or []
    if isinstance(items, dict):
        items = [items]
    return [(i["text"], i["value"]) for i in items
            if isinstance(i, dict) and "text" in i and "value" in i]


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
    if not isinstance(payload, dict):
        raise FuelEconomyError("unexpected vehicle response")
    return {"id": str(payload.get("id", "")),
            "label": f"{payload.get('year', '?')} {payload.get('make', '?')} {payload.get('model', '?')}",
            "fuel_type": payload.get("fuelType") or "",
            "combined": _mpg(payload.get("comb08")), "city": _mpg(payload.get("city08")),
            "highway": _mpg(payload.get("highway08")),
            "vclass": payload.get("VClass") or ""}


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


def has_electric_part(veh):
    """True for pure EVs and for plug-in hybrids: their EPA figures blend electricity (MPGe) with
    gasoline, so they are not gasoline-only mpg."""
    return "electricity" in veh.get("fuel_type", "").lower()
