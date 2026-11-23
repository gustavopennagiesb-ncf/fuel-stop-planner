"""Fuel plan: do I need to stop, which stations can I reach, how much to buy. Pure
functions, no UI or network.

Status of each tank size in TANK_BY_CLASS (gallons, matched against the EPA vehicle
class text). All are ASSUMPTIONS: typical figures for the class, not read from the
user's own car, so the user can still type the tank by hand.
- pickup 26.0: full-size pickups commonly carry 23-36 gal; 26 is a middle value.
- minivan 20.0: most minivans carry about 19-21 gal.
- standard sport utility 20.0: full-size SUVs commonly carry 20-26 gal; the low end
  is used so the plan errs on the side of stopping.
- small sport utility 14.0: compact crossovers typically carry 13-16 gal.
- large cars 18.0: large sedans typically carry 17-19 gal.
- midsize 16.0: midsize sedans and wagons typically carry 15-18 gal.
- compact 13.0: compact and subcompact cars typically carry 12-14 gal.
- two seaters 14.0: sports cars typically carry 13-18 gal; low end used.
- fallback 13.0: the app's existing default tank.

Other assumptions:
- Gauge readings are in eighths of the tank (0-8) and are taken at face value.
- Reaching a station costs the along-route distance plus the off-route distance (one way).
- "Minimum" purchase is rounded up to the next 0.5 gal so the reserve is never undercut;
  room in the tank is rounded down to 0.1 gal.
"""

import math

import geo

METERS_PER_MILE = 1609.344
FALLBACK_TANK_GAL = 13.0

# Most specific keyword first: the first match wins.
TANK_BY_CLASS = [
    ("pickup", 26.0),
    ("minivan", 20.0),
    ("standard sport utility", 20.0),
    ("small sport utility", 14.0),
    ("large cars", 18.0),
    ("midsize", 16.0),
    ("compact", 13.0),
    ("two seaters", 14.0),
]


def trip_miles(path: list) -> float:
    if len(path) < 2:
        return 0.0
    return geo.path_length_m(path) / METERS_PER_MILE


def tank_for_class(vclass) -> float:
    text = (vclass or "").lower()
    for keyword, gal in TANK_BY_CLASS:
        if keyword in text:
            return gal
    return FALLBACK_TANK_GAL


def fuel_on_board(mode: str, value: float, tank_gal: float, mpg: float, reserve_gal: float) -> float:
    """Gallons in the tank. mode "gauge": value in eighths (0-8), clamped.
    mode "miles": miles until the reserve, plus the reserve (not capped by the tank)."""
    if mode == "gauge":
        eighths = min(max(value, 0.0), 8.0)
        return tank_gal * eighths / 8.0
    if mode == "miles":
        return max(value, 0.0) / mpg + reserve_gal
    raise ValueError(f"unknown fuel mode: {mode!r}")


def _floor_tenth(x: float) -> float:
    return math.floor(round(x * 10, 6)) / 10


def _ceil_half(x: float) -> float:
    return math.ceil(round(x * 2, 6)) / 2


def plan(stations: list, path: list, fuel_gal: float, tank_gal: float, mpg: float,
         reserve_gal: float, p_ref: float) -> dict:
    miles = trip_miles(path)
    need_gal = miles / mpg + reserve_gal
    effective_tank = max(tank_gal, fuel_gal)
    stop_needed = miles > 0 and fuel_gal < need_gal
    per_station = {}
    for s in stations:
        if s.get("price") is None:
            continue
        along, off = geo.route_position((s["lat"], s["lng"]), path)
        if s.get("off_route_m") is not None:
            off = s["off_route_m"]
        reach_mi = (along + off) / METERS_PER_MILE
        fuel_at = fuel_gal - reach_mi / mpg
        reachable = fuel_at >= reserve_gal
        room = max(0.0, _floor_tenth(effective_tank - fuel_at))
        # After filling, the car drives back to the route (off) and then to the destination.
        rest_mi = (miles - along / METERS_PER_MILE) + off / METERS_PER_MILE
        minimum = _ceil_half(max(0.0, rest_mi / mpg + reserve_gal - fuel_at))
        if not reachable:
            gallons = 0.0
        elif stop_needed and s["price"] >= p_ref:
            gallons = min(room, minimum)
        else:
            gallons = room
        per_station[s["id"]] = {
            "reachable": reachable,
            "gallons": min(max(gallons, 0.0), room),
            "fuel_at_station": fuel_at,
            "short": room < minimum,
        }
    return {
        "trip_miles": miles,
        "need_gal": need_gal,
        "fuel_gal": fuel_gal,
        "effective_tank": effective_tank,
        "tank_too_small": fuel_gal > tank_gal,
        "arrive_gal": fuel_gal - miles / mpg,
        "stop_needed": stop_needed,
        "per_station": per_station,
    }
