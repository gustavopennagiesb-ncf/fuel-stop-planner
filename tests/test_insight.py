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


def test_a_price_tie_does_not_depend_on_list_order():
    a, b = st("a", 3.40, 500.0), st("b", 3.40, 4000.0)
    fwd, rev = rank([a, b]), rank([b, a])
    assert insight.regret(fwd) == insight.regret(rev)
    assert insight.price_rank_gap(fwd) == insight.price_rank_gap(rev)


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


def test_the_grid_of_nothing_is_empty_not_a_crash():
    grid = insight.decision_grid([], 3.70, 27.2, 45.0)
    assert grid["total"] == 20 and grid["misled"] == 0 and grid["share"] == 0.0


def test_flip_point_finds_where_the_winner_changes():
    flip = insight.flip_point([NEAR, FAR_CHEAP], 3.70, 10.0, 27.2, 45.0)
    assert flip in insight.VOT_GRID and flip > 0


def test_flip_point_is_none_when_the_winner_never_changes():
    assert insight.flip_point([NEAR], 3.70, 10.0, 27.2, 45.0) is None
