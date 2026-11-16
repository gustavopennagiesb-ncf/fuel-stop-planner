"""How often the cheapest station is not the best one once the detour is priced."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import detour
import insight
import recommend as rc

NEAR = {"id": "near", "name": "Near", "price": 3.55, "off_route_m": 100.0, "lat": 27.5, "lng": -82.5}
FAR_CHEAP = {"id": "far", "name": "Far Cheap", "price": 3.40, "off_route_m": 6000.0, "lat": 27.6, "lng": -82.6}


def st(name, price, off_route_m):
    return {"id": name, "name": name, "price": price, "off_route_m": off_route_m,
            "lat": 27.5, "lng": -82.5}


def rank(stations, gallons=10.0, vot=19.0):
    return detour.rank_stations(stations, 3.70, gallons, 27.2, vot, 45.0)


def test_regret_is_zero_when_the_cheapest_is_also_the_best():
    assert insight.regret(rank([NEAR])) == 0.0
    both = [st("a", 3.40, 100.0), st("b", 3.55, 3000.0)]
    assert insight.regret(rank(both)) == 0.0


def test_regret_is_the_net_benefit_difference():
    ranked = rank([NEAR, FAR_CHEAP])
    assert insight.regret(ranked) == pytest.approx(
        ranked[0]["net_benefit"] - rc.cheapest(ranked)["net_benefit"], abs=0.01)
    assert insight.regret(ranked) > 0


def test_regret_of_nothing_is_zero():
    assert insight.regret([]) == 0.0


def test_price_rank_gap_is_one_when_price_picks_the_winner():
    assert insight.price_rank_gap(rank([NEAR])) == 1


def test_price_rank_gap_counts_positions_when_it_does_not():
    ranked = rank([NEAR, FAR_CHEAP])
    assert ranked[0]["id"] == "near"
    assert insight.price_rank_gap(ranked) == 2


def test_price_rank_gap_of_nothing_is_none():
    assert insight.price_rank_gap([]) is None


def test_a_net_benefit_tie_counts_as_the_cheapest_winning_in_both_list_orders():
    """A (3.50, on the route) and B (3.40, 1464 m off) both net $2.00: no regret, rank 1,
    and no grid cell at that setting is "misled"."""
    a, b = st("a", 3.50, 0.0), st("b", 3.40, 1464.0)
    for stations in ([a, b], [b, a]):
        ranked = rank(stations)
        assert ranked[0]["net_benefit"] == ranked[1]["net_benefit"] == 2.0
        assert insight.regret(ranked) == 0.0
        assert insight.price_rank_gap(ranked) == 1
        grid = insight.decision_grid(stations, 3.70, 27.2, 45.0, gallons_values=(10.0,),
                                     vot_values=(19.0,))
        assert grid["misled"] == 0 and grid["cells"][0]["regret"] == 0.0


def test_the_grid_counts_every_combination():
    grid = insight.decision_grid([NEAR, FAR_CHEAP], 3.70, 27.2, 45.0)
    assert grid["total"] == len(insight.GALLONS_GRID) * len(insight.VOT_GRID) == 20
    assert len(grid["cells"]) == 20
    assert grid["misled"] == sum(not c["cheapest_won"] for c in grid["cells"])
    assert grid["share"] == grid["misled"] / grid["total"]


def test_a_cheap_near_station_wins_every_cell():
    stations = [st("a", 3.40, 100.0), st("b", 3.55, 3000.0)]
    grid = insight.decision_grid(stations, 3.70, 27.2, 45.0)
    assert grid["share"] == 0.0
    assert all(c["cheapest_won"] for c in grid["cells"])


def test_time_value_decides_whether_a_far_cheap_station_wins():
    grid = insight.decision_grid([NEAR, FAR_CHEAP], 3.70, 27.2, 45.0)
    at = {(c["gallons"], c["vot"]): c["cheapest_won"] for c in grid["cells"]}
    assert at[(10.0, 0.0)] is True
    assert at[(10.0, 40.0)] is False
    assert 0.0 < grid["share"] < 1.0


def test_the_grid_reports_how_much_price_alone_costs_not_only_how_often():
    grid = insight.decision_grid([NEAR, FAR_CHEAP], 3.70, 27.2, 45.0)
    regrets = [c["regret"] for c in grid["cells"]]
    assert all(r >= 0 for r in regrets)
    assert grid["max_regret"] == max(regrets) > 0
    assert min(regrets) <= grid["median_regret"] <= grid["max_regret"]
    assert all((c["regret"] > 0) == (not c["cheapest_won"]) for c in grid["cells"])


def test_the_grid_of_nothing_is_empty_not_a_crash():
    grid = insight.decision_grid([], 3.70, 27.2, 45.0)
    assert grid["total"] == 20 and grid["misled"] == 0 and grid["share"] == 0.0


def test_flip_point_finds_where_the_winner_changes():
    flip = insight.flip_point([NEAR, FAR_CHEAP], 3.70, 10.0, 27.2, 45.0)
    assert flip == pytest.approx(3.65, abs=0.1)   # exact crossing, not the $10 grid step
    assert rank([NEAR, FAR_CHEAP], vot=flip - 0.5)[0]["id"] == "far"
    assert rank([NEAR, FAR_CHEAP], vot=flip + 0.5)[0]["id"] == "near"


def test_flip_point_is_none_when_the_winner_never_changes():
    assert insight.flip_point([NEAR], 3.70, 10.0, 27.2, 45.0) is None


def test_a_cheap_corridor_is_reported_in_cents():
    out = insight.corridor_vs_state([3.55, 3.60, 3.65], 3.73)
    assert out["route_median"] == 3.60
    assert out["diff_cents"] == -13
    assert out["verdict"] == "cheaper than the state average"


def test_a_dear_corridor():
    out = insight.corridor_vs_state([3.90, 3.95], 3.73)
    assert out["diff_cents"] > 0
    assert out["verdict"] == "dearer than the state average"


def test_within_five_cents_is_about_the_same():
    assert insight.corridor_vs_state([3.76], 3.73)["verdict"] == "about the state average"
    assert insight.corridor_vs_state([3.79], 3.73)["verdict"] == "dearer than the state average"


def test_missing_prices_give_nothing_to_show():
    assert insight.corridor_vs_state([], 3.73) is None
    assert insight.corridor_vs_state([3.6], None) is None
    assert insight.corridor_vs_state([3.6], float("nan")) is None
