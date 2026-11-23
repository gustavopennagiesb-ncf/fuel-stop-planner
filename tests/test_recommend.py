"""Turning the ranked stations into one clear answer: go, skip (nothing pays off enough) or none."""
import pytest

import recommend as rc


def st(name, net, price=3.5, miles=1.0, minutes=2.0, sid=None):
    return {"id": sid or name, "name": name, "price": price, "net_benefit": net,
            "extra_miles": miles, "extra_minutes": minutes, "lat": 27.5, "lng": -82.5}


def test_no_priced_station_means_none():
    got = rc.recommend([])
    assert got["verdict"] == "none" and got["best"] is None and got["alternatives"] == []


def test_a_station_that_saves_enough_is_a_go_with_alternatives():
    ranked = [st("A", 1.9), st("B", 1.2), st("C", 0.6), st("D", 0.55), st("E", 0.3)]
    got = rc.recommend(ranked, min_saving=0.5, n_alternatives=3)
    assert got["verdict"] == "go" and got["best"]["name"] == "A"
    assert [a["name"] for a in got["alternatives"]] == ["B", "C", "D"]  # E saves too little


def test_alternatives_only_include_stops_that_clear_the_minimum():
    got = rc.recommend([st("A", 1.0), st("B", 0.2)], min_saving=0.5)
    assert got["alternatives"] == []


def test_best_below_the_minimum_is_skip_and_keeps_the_best_for_context():
    got = rc.recommend([st("A", 0.3), st("B", -1.0)], min_saving=0.5)
    assert got["verdict"] == "skip" and got["best"]["name"] == "A" and got["alternatives"] == []


def test_exactly_the_minimum_is_a_go():
    assert rc.recommend([st("A", 0.5)], min_saving=0.5)["verdict"] == "go"


def test_a_stop_must_always_save_something_even_with_a_zero_minimum():
    assert rc.recommend([st("A", 0.0)], min_saving=0.0)["verdict"] == "skip"
    assert rc.recommend([st("A", 0.01)], min_saving=0.0)["verdict"] == "go"
    assert rc.recommend([st("A", 0.01)], min_saving=-5)["verdict"] == "go"  # negative = zero


def test_all_negative_is_skip():
    assert rc.recommend([st("A", -0.2), st("B", -0.9)])["verdict"] == "skip"


def test_ties_keep_the_ranked_order():
    got = rc.recommend([st("A", 1.0), st("B", 1.0), st("C", 1.0)], n_alternatives=2)
    assert got["best"]["name"] == "A" and [a["name"] for a in got["alternatives"]] == ["B", "C"]


def test_the_input_list_is_not_modified():
    ranked = [st("A", 1.0), st("B", 0.9)]
    before = [dict(r) for r in ranked]
    rc.recommend(ranked)
    assert ranked == before


def test_summary_for_a_go_names_price_saving_and_detour():
    rec = rc.recommend([st("Sunway #5", 1.9, price=3.229, miles=2.04, minutes=2.7)])
    title, body = rc.summary(rec)
    assert title == "Fill up at Sunway \\#5"  # "#" is escaped so a name cannot become a heading
    assert "$3.23/gal" in body and "$1.90" in body and "2.0 extra miles" in body and "3 min" in body


def test_summary_for_skip_and_none_explain_what_to_do():
    skip_title, skip_body = rc.summary(rc.recommend([st("A", 0.2)], min_saving=0.5))
    assert "No detour" in skip_title and "$0.50" in skip_body and "first station" in skip_body
    none_title, none_body = rc.summary(rc.recommend([]))
    assert "No station" in none_title and "Edit prices" in none_body


@pytest.mark.parametrize("name", ["**bold**", "[x](http://evil)", "a $b$ c", "<img src=x>",
                                  "x_y_z", "# h", "`code`", "a|b", "~~s~~"])
def test_station_names_cannot_inject_markdown_or_math(name):
    rec = rc.recommend([st(name, 1.0)])
    title, body = rc.summary(rec)
    for ch in "*[]()$<>`|~#_":
        if ch in name:
            assert "\\" + ch in title, (ch, title)


def test_md_escape_keeps_plain_names_readable():
    assert rc.md_escape("Sunway #5") == "Sunway \\#5"
    assert rc.md_escape("Wawa") == "Wawa"


def test_a_very_short_detour_reads_under_one_minute_not_zero():
    _, body = rc.summary(rc.recommend([st("A", 1.0, miles=0.2, minutes=0.3)]))
    assert "under 1 min" in body and "0 min" not in body
    _, body = rc.summary(rc.recommend([st("A", 1.0, miles=1.0, minutes=1.4)]))
    assert "1 min" in body and "under" not in body


# ---------------------------------------------------------------- cheapest price
def test_cheapest_is_the_lowest_price_whatever_the_ranking():
    ranked = [st("Near", 1.0, price=3.60), st("Far", -0.5, price=3.40), st("Mid", 0.2, price=3.50)]
    assert rc.cheapest(ranked)["name"] == "Far"


def test_cheapest_breaks_price_ties_by_the_better_saving():
    ranked = [st("A", 0.9, price=3.40), st("B", 0.4, price=3.40)]
    assert rc.cheapest(ranked)["name"] == "A"
    assert rc.cheapest(list(reversed(ranked)))["name"] == "A"


def test_cheapest_of_nothing_is_none():
    assert rc.cheapest([]) is None


def test_describe_cheapest_when_it_is_also_the_recommended_stop():
    a = st("Wawa", 1.5, price=3.40, minutes=1.2)
    head, detail = rc.describe_cheapest(a, p_ref=3.60, recommended_id="Wawa")
    assert head == "Cheapest gas on this route: Wawa at $3.40/gal"
    assert "$0.20 below" in detail and "also the recommended stop" in detail


def test_describe_cheapest_when_the_detour_eats_the_saving():
    far = st("Far", -1.13, price=3.40, miles=6.2, minutes=8.3)
    head, detail = rc.describe_cheapest(far, p_ref=3.625, recommended_id="Near")
    assert "Far" in head and "$3.40/gal" in head
    assert "$0.23 below" in detail and "8 min detour" in detail
    assert "costs you about $1.13" in detail and "recommended" not in detail


def test_describe_cheapest_when_it_still_saves_but_is_not_the_top_pick():
    c = st("C", 0.6, price=3.45, minutes=3.0)
    _, detail = rc.describe_cheapest(c, p_ref=3.60, recommended_id="Other")
    assert "saves about $0.60" in detail


def test_describe_cheapest_above_the_usual_price_and_hostile_names():
    pricey = st("**$x$**", -0.1, price=3.70, minutes=0.2)
    head, detail = rc.describe_cheapest(pricey, p_ref=3.60, recommended_id=None)
    assert "\\*\\*\\$x\\$\\*\\*" in head and "$0.10 above" in detail and "under 1 min" in detail


def test_no_stop_verdict_keeps_the_optional_best_deal():
    ranked = [{"id": "a", "name": "A", "price": 3.0, "net_benefit": 2.0, "extra_miles": 1, "extra_minutes": 2}]
    rec = rc.recommend(ranked, 0.5, stop_needed=False)
    assert rec["verdict"] == "no_stop" and rec["best"]["id"] == "a"
    assert rec["fuel_aware"] is True and rec["alternatives"] == []
    title, body = rc.summary(rec)
    assert title == "No stop needed"
    assert "saves about $2.00" in body


def test_no_stop_without_a_worthwhile_deal_or_stations():
    weak = [{"id": "a", "name": "A", "price": 3.0, "net_benefit": 0.1, "extra_miles": 1, "extra_minutes": 2}]
    for ranked in (weak, []):
        rec = rc.recommend(ranked, 0.5, stop_needed=False)
        assert rec["verdict"] == "no_stop" and rec["best"] is None
        assert rc.summary(rec) == ("No stop needed", "")


def test_go_title_names_the_gallons_when_fuel_aware():
    best = {"id": "a", "name": "A*", "price": 3.0, "net_benefit": 2.0, "extra_miles": 1,
            "extra_minutes": 2, "gallons": 9.5}
    rec = rc.recommend([best], 0.5, stop_needed=True)
    assert rec["fuel_aware"] is True
    assert rc.summary(rec)[0] == "Fill up at A\\* · buy 9.5 gal"


def test_stop_needed_none_is_todays_behaviour_even_with_gallons():
    row = {"id": "a", "name": "A", "price": 3.0, "net_benefit": 2.0, "extra_miles": 1,
           "extra_minutes": 2, "gallons": 9.5}
    rec = rc.recommend([row], 0.5)
    assert rec["fuel_aware"] is False
    assert rc.summary(rec)[0] == "Fill up at A"
    assert rc.recommend([], 0.5)["fuel_aware"] is False
    skip = dict(row, net_benefit=0.1)
    assert rc.recommend([skip], 0.5)["verdict"] == "skip"


def test_no_stop_optional_line_names_the_gallons_and_escapes_the_name():
    best = {**st("Evil *[x](http://e)* $1$", 2.4), "gallons": 8.8}
    title, body = rc.summary(rc.recommend([best], stop_needed=False))
    assert title == "No stop needed"
    assert "saves about $2.40 for 8.8 gal)." in body
    assert "\\*\\[x\\]\\(http://e\\)\\* \\$1\\$" in body


def test_stop_needed_skip_names_the_nearest_reachable_station():
    best = {**st("Far <b>", 0.2), "gallons": 9.0}
    near = {**st("Near *1*", -0.5, sid="n"), "gallons": 4.5}
    rec = rc.recommend([best, near], stop_needed=True)
    title, body = rc.summary(rec, nearest=near)
    assert title == "Stop for fuel"
    assert body == ("No detour pays off enough, so take the nearest reachable station: "
                    "Near \\*1\\* · buy 4.5 gal.")


def test_non_fuel_aware_skip_copy_is_unchanged():
    title, body = rc.summary(rc.recommend([st("A", 0.3)], min_saving=0.5))
    assert title == "No detour pays off enough"
    assert body.endswith("Fill up at the first station on your route.")


def test_a_half_minute_detour_never_reads_zero_minutes():
    """Python rounds 0.5 to 0, so anything under a minute must say so in words."""
    for m in (0.0, 0.4, 0.5, 0.9):
        assert rc.minutes_label(m) == "under 1 min"
    assert rc.minutes_label(1.0) == "1 min"
    assert rc.minutes_label(2.6) == "3 min"
