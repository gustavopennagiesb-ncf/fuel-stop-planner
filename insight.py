"""How often the cheapest station is not the best one once the detour is priced.

Pure math, no UI or network. It turns the app's central claim into measurements:
the dollars lost by filling at the cheapest station (`regret`), where the cheapest
sits in the net-benefit ranking (`price_rank_gap`), the share of fill-up sizes and
values of time for which price alone picks the wrong station (`decision_grid`), and
the value of time at which the winner changes (`flip_point`).

The result describes one route's stations at one moment, not a general law: change
the route, the prices or the hour and the numbers change with them.
"""
import math
from statistics import median

import detour
import recommend

GALLONS_GRID = (5.0, 10.0, 15.0, 20.0)
VOT_GRID = (0.0, 10.0, 20.0, 30.0, 40.0)
SAME_AS_STATE_CENTS = 5


def regret(ranked):
    """Dollars lost by filling at the cheapest station instead of the best one."""
    cheap = recommend.cheapest(ranked)
    if cheap is None or ranked[0]["id"] == cheap["id"]:
        return 0.0
    return round(ranked[0]["net_benefit"] - cheap["net_benefit"], 2)


def price_rank_gap(ranked):
    """1-based position of the cheapest station in the net-benefit ranking (1: price won)."""
    cheap = recommend.cheapest(ranked)
    if cheap is None:
        return None
    return next(i for i, s in enumerate(ranked, 1) if s["id"] == cheap["id"])


def decision_grid(stations, p_ref, mpg, speed_mph, gallons_values=GALLONS_GRID,
                  vot_values=VOT_GRID):
    """Rank the stations for every (gallons, value of time) pair and count where price misled."""
    cells = []
    for gallons in gallons_values:
        for vot in vot_values:
            ranked = detour.rank_stations(stations, p_ref, gallons, mpg, vot, speed_mph)
            cheap = recommend.cheapest(ranked)
            won = cheap is None or ranked[0]["id"] == cheap["id"]
            cells.append({"gallons": gallons, "vot": vot, "cheapest_won": won})
    total = len(cells)
    misled = sum(not c["cheapest_won"] for c in cells)
    return {"cells": cells, "total": total, "misled": misled,
            "share": misled / total if total else 0.0}


def flip_point(stations, p_ref, gallons, mpg, speed_mph, vot_values=VOT_GRID):
    """Lowest value of time at which the winner differs from the one at vot_values[0], or None."""
    first = None
    for vot in vot_values:
        ranked = detour.rank_stations(stations, p_ref, gallons, mpg, vot, speed_mph)
        winner = ranked[0]["id"] if ranked else None
        if first is None:
            first = winner
        elif winner != first:
            return vot
    return None


def _usable(price):
    return isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price)


def corridor_vs_state(route_prices, state_latest):
    """Median price of the stations on the route against the latest weekly state price.

    `diff_cents` is negative when the corridor is cheaper. Within 5 cents counts as
    "about" the state average. None when either side has no usable (finite) price.
    """
    prices = [p for p in route_prices if _usable(p)]
    if not prices or not _usable(state_latest):
        return None
    route_median = median(prices)
    diff_cents = round((route_median - state_latest) * 100)
    if abs(diff_cents) <= SAME_AS_STATE_CENTS:
        verdict = "about the state average"
    elif diff_cents < 0:
        verdict = "cheaper than the state average"
    else:
        verdict = "dearer than the state average"
    return {"route_median": route_median, "state_latest": state_latest,
            "diff_cents": diff_cents, "verdict": verdict}
