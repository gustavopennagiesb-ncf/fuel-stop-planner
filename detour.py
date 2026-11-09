"""Detour break-even math (the team's own derivation, see the validation report).

net benefit = G*(p_ref - p_station) - (extra_miles / mpg) * p_station - vot * extra_minutes / 60
"""
from statistics import median

DEFAULTS = {"gallons": 10.0, "mpg": 27.2, "value_of_time": 19.0, "speed_mph": 45.0}


def net_benefit(gallons, p_ref, p_station, extra_miles, extra_minutes, mpg, value_of_time):
    """Dollars saved by stopping at the station instead of paying p_ref on the route."""
    saving = gallons * (p_ref - p_station)
    fuel_burned = (extra_miles / mpg) * p_station
    time_cost = value_of_time * extra_minutes / 60.0
    return saving - fuel_burned - time_cost


def breakeven_miles(gallons, price_gap, p_station, mpg, value_of_time, speed_mph):
    """Largest extra distance (miles) for which a station that is price_gap $/gal cheaper
    still pays off, assuming extra minutes = extra miles / speed."""
    cost_per_mile = p_station / mpg + value_of_time / speed_mph
    return gallons * price_gap / cost_per_mile


def reference_price(stations, onroute_m=800):
    """Median price of stations (almost) on the route; None when there are none."""
    prices = [s["price"] for s in stations if s.get("price") and s["off_route_m"] <= onroute_m]
    return median(prices) if prices else None


def rank_stations(stations, p_ref, gallons, mpg, value_of_time, speed_mph):
    """Add extra miles/minutes and net benefit to each station; best first.

    If a station already carries 'extra_miles'/'extra_minutes' (e.g. from the Routes API)
    those are used. Otherwise they are approximated as out-and-back off the route.
    """
    out = []
    for s in stations:
        if s.get("price") is None:
            continue
        miles = s.get("extra_miles")
        if miles is None:
            miles = 2 * s["off_route_m"] / 1609.344
        minutes = s.get("extra_minutes")
        if minutes is None:
            minutes = miles / speed_mph * 60
        nb = net_benefit(gallons, p_ref, s["price"], miles, minutes, mpg, value_of_time)
        out.append({**s, "extra_miles": round(miles, 2), "extra_minutes": round(minutes, 1),
                    "net_benefit": round(nb, 2)})
    return sorted(out, key=lambda r: r["net_benefit"], reverse=True)


def sensitivity_table(gallons, p_station, mpg, speed_mph, gaps=(0.10, 0.20, 0.30, 0.50),
                      values_of_time=(0, 10, 19, 30)):
    """Break-even miles for a grid of price gaps (columns) and values of time (rows)."""
    return {vot: {g: round(breakeven_miles(gallons, g, p_station, mpg, vot, speed_mph), 1)
                  for g in gaps} for vot in values_of_time}
