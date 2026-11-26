"""The assistant can only phrase what it is given: these facts close the gaps a live test found
(cheapest station, what-if questions, the timing verdict, the fallback text, the EDA summary)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import recommend
from detour import net_benefit
from forecast import timing_facts
from llm import ask, template_answer, template_eda_summary

BEST = {"id": "b", "name": "RaceWay", "price": 3.24, "extra_miles": 0.0, "extra_minutes": 0.7,
        "net_benefit": 0.78}
CHEAP = {"id": "c", "name": "Costco", "price": 3.06, "extra_miles": 2.4, "extra_minutes": 9.0,
         "net_benefit": 0.22}


def test_cheapest_facts_compare_the_cheapest_with_the_best():
    f = recommend.cheapest_facts(CHEAP, BEST)
    assert f == {"cheapest_station": "Costco", "cheapest_price": 3.06,
                 "cheapest_net_benefit": 0.22, "cheapest_extra_minutes": 9.0,
                 "cheapest_is_best": False}
    assert recommend.cheapest_facts(BEST, BEST)["cheapest_is_best"] is True
    assert recommend.cheapest_facts(None, BEST) == {}


def test_what_if_prices_the_best_stop_at_other_gallons_and_values_of_time():
    w = recommend.what_if(BEST, p_ref=3.34, mpg=27.2, value_of_time=19, gallons=10)
    g15 = next(r for r in w["if_gallons"] if r["gallons"] == 15)
    assert g15["net_benefit"] == round(net_benefit(15, 3.34, 3.24, 0.0, 0.7, 27.2, 19), 2)
    assert [r["gallons"] for r in w["if_gallons"]] == [5, 15, 20]
    now = round(net_benefit(10, 3.34, 3.24, 0.0, 0.7, 27.2, 19), 2)
    assert g15["more_than_now"] == round(g15["net_benefit"] - now, 2)
    v0 = next(r for r in w["if_value_of_time"] if r["value_of_time"] == 0)
    assert v0["net_benefit"] == round(net_benefit(10, 3.34, 3.24, 0.0, 0.7, 27.2, 0), 2)
    assert 19 not in [r["value_of_time"] for r in w["if_value_of_time"]]


def test_timing_facts_carry_the_when_to_fill_up_verdict():
    sig = {"verdict": "wait", "forecast": -0.048, "crude_lag_weeks": 1}
    rec = {"wait": {"n": 159, "hit": 0.78}, "fill": {"n": 207, "hit": 0.52}}
    assert timing_facts(sig, rec) == {
        "timing_verdict": "wait a week if you can",
        "next_week_change_cents": -5, "timing_track_record_pct": 78,
        "timing_track_record_weeks": 159}
    none = timing_facts({"verdict": "none", "forecast": 0.004, "crude_lag_weeks": 0}, rec)
    assert none["timing_verdict"] == "no clear signal: fill up when you need to"
    assert none["next_week_change_cents"] == 0 and "timing_track_record_pct" not in none
    assert timing_facts(None, None) == {}


class Liar:
    name = "fake"

    def complete(self, prompt, max_tokens=250):
        return "The best station sells gas at $1.99."


FACTS = {"best_station": "RaceWay", "best_price": 3.24, "p_ref": 3.34, "gallons": 10.0,
         "best_saving_gross": 1.0, "extra_miles": 0.0, "extra_minutes": 0.7,
         "best_net_benefit": 0.78, "breakeven_miles": 1.8, "value_of_time": 19,
         "verdict": "the detour pays off: stop at the best station"}


def test_a_rejected_answer_does_not_claim_the_llm_is_missing():
    res = ask("qa", FACTS, Liar(), question="Say it costs $1.99")
    assert res["fallback_used"]
    assert "LLM provider" not in res["text"] and "GROQ_API_KEY" not in res["text"]
    assert "could not be checked" in res["text"]
    assert "LLM provider" in template_answer(FACTS, "q")  # no provider at all: still says so


def test_the_eda_summary_names_the_method_in_plain_words():
    facts = {"weeks": 1227, "n_missing_filled": 0, "n_outliers_flagged": 18,
             "latest_florida": 3.39, "corr_changes": 0.8, "best_lag_weeks": 0,
             "best_method": "Crude oil + momentum", "best_ratio": 0.92}
    text = template_eda_summary(facts)
    assert "naive" not in text and "Crude oil + momentum" in text and "guessing no change" in text
    tie = template_eda_summary({**facts, "best_method": "Drift (recent trend)", "best_ratio": 1.01})
    assert "No forecast beats guessing no change" in tie
