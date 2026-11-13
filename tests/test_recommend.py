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
