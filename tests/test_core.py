import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
import pytest

from detour import breakeven_miles, net_benefit, rank_stations, reference_price, sensitivity_table
from forecast import lagged_corr, rolling_origin_eval, treat_series, weekly_changes_outliers
from geo import corridor_filter, decode_polyline, encode_polyline, haversine_m, path_length_m
from llm import AnthropicProvider, ask, explain, template_explanation, validate
from providers import eia, fixtures, places, routes
from services import compute_detours, load_series, load_trip


# ---- geo -----------------------------------------------------------------------------
def test_polyline_google_example_and_roundtrip():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")  # Google's documented example
    assert pts == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert encode_polyline(pts) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def test_haversine_sarasota_tampa_about_47_miles():
    miles = haversine_m((27.3364, -82.5307), (27.9506, -82.4572)) / 1609.344
    assert 42 < miles < 48


def test_corridor_filter_keeps_near_drops_far():
    path = [(27.0, -82.0), (27.0, -81.9)]
    near = {"id": "a", "lat": 27.005, "lng": -81.95}
    far = {"id": "b", "lat": 27.05, "lng": -81.95}
    res = corridor_filter([near, far], path, 1500)
    assert [r["id"] for r in res] == ["a"] and 500 < res[0]["off_route_m"] < 600


# ---- detour math ---------------------------------------------------------------------
def test_breakeven_matches_report_example():
    # 10 gal, 20-cent gap, $4.153, 27.2 mpg, $19/h, 45 mph -> about 3.5 miles
    d = breakeven_miles(10, 0.20, 4.153, 27.2, 19, 45)
    assert d == pytest.approx(3.48, abs=0.02)


def test_net_benefit_is_zero_at_breakeven():
    d = breakeven_miles(10, 0.20, 4.0, 27.2, 19, 45)
    nb = net_benefit(10, 4.2, 4.0, d, d / 45 * 60, 27.2, 19)
    assert nb == pytest.approx(0.0, abs=1e-9)


def test_rank_stations_prefers_close_cheap_station_and_rejects_far_one():
    st = [{"id": "close", "price": 3.40, "off_route_m": 1200},
          {"id": "far", "price": 3.30, "off_route_m": 20000},
          {"id": "onroute", "price": 3.60, "off_route_m": 0},
          {"id": "noprice", "price": None, "off_route_m": 100}]
    p_ref = reference_price(st)
    assert p_ref == 3.60
    ranked = rank_stations(st, p_ref, 10, 27.2, 19, 45)
    assert [r["id"] for r in ranked][0] == "close"
    assert next(r for r in ranked if r["id"] == "far")["net_benefit"] < 0
    assert all(r["id"] != "noprice" for r in ranked)


def test_sensitivity_table_shape_and_monotonic():
    t = sensitivity_table(10, 4.153, 27.2, 45)
    assert t[0][0.5] > t[0][0.1] and t[0][0.2] > t[30][0.2]


# ---- forecast ------------------------------------------------------------------------
def test_naive_wins_on_random_walk():
    rng = np.random.default_rng(0)
    y = 2 + np.cumsum(rng.normal(0, 0.03, 600))
    res = rolling_origin_eval(y)
    assert res["naive"][1]["ratio_to_naive"] == 1.0
    assert res["seasonal_naive_52"][4]["ratio_to_naive"] > 1.0  # cannot beat naive on a walk


def test_outliers_flags_a_shock():
    y = np.full(100, 3.0)
    y[50:] += 1.0
    assert 50 in weekly_changes_outliers(y + np.random.default_rng(1).normal(0, 0.001, 100))


# ---- providers (payloads are illustrative: shaped like the docs, never captured) ------
def test_eia_parse_rows_skips_null_and_casts_total():
    payload = {"response": {"total": "3", "data": [
        {"period": "2026-11-02", "value": "3.791"}, {"period": "2026-10-26", "value": "4.017"},
        {"period": "2026-10-19", "value": "."}]}}
    total, rows = eia.parse_rows(payload)
    assert total == 3 and rows == [("2026-10-26", 4.017), ("2026-11-02", 3.791)]


def test_eia_params_contain_facets():
    p = eia.build_params("KEY", "SFL")
    assert p["facets[duoarea][]"] == "SFL" and p["frequency"] == "weekly"


def test_places_parse_money_and_missing_price():
    payload = {"places": [
        {"id": "p1", "displayName": {"text": "A"}, "location": {"latitude": 27.1, "longitude": -82.1},
         "fuelOptions": {"fuelPrices": [{"type": "REGULAR_UNLEADED",
                                         "price": {"currencyCode": "USD", "units": "3", "nanos": 459000000},
                                         "updateTime": "2026-11-09T10:00:00Z"}]}},
        {"id": "p2", "displayName": {"text": "B"}, "location": {"latitude": 27.2, "longitude": -82.2}}]}
    out = places.parse_places(payload)
    assert out[0]["price"] == pytest.approx(3.459) and out[1]["price"] is None


def test_routes_parse_duration_string():
    payload = {"routes": [{"duration": "5400s", "distanceMeters": 150000,
                           "polyline": {"encodedPolyline": "_p~iF~ps|U"}}]}
    assert routes.parse_route(payload) == {"distanceMeters": 150000, "seconds": 5400,
                                           "polyline": "_p~iF~ps|U"}


def test_routes_body_has_via_only_when_requested():
    assert "intermediates" not in routes.build_body("A", "B")
    assert "intermediates" in routes.build_body("A", "B", via=(27.0, -82.0))


def test_routes_via_waypoint_is_not_a_stop():
    wp = routes.build_body("A", "B", via=(27.0, -82.0))["intermediates"][0]
    assert wp["via"] is True and wp["location"]["latLng"]["latitude"] == 27.0
    assert "via" not in routes.build_body("A", "B")["origin"]


# ---- fixtures and services -----------------------------------------------------------
def test_fixture_trip_has_stations_and_is_marked_synthetic():
    trip = load_trip(provider="fixture")
    assert trip["mode"] == "fixture" and len(trip["stations"]) >= 10
    assert all(s["synthetic"] for s in trip["stations"])
    assert 100 < path_length_m(trip["path"]) / 1609.344 < 160
    assert compute_detours(trip) == trip["stations"]


def test_fixture_series_has_gaps_and_two_columns():
    df, label = load_series("fixture")
    assert label == "synthetic" and df.isna().any().any() and {"florida", "us"} <= set(df)


# ---- llm validator -------------------------------------------------------------------
FACTS = {"best_station": "Sunway #1", "best_price": 3.41, "p_ref": 3.62, "gallons": 10,
         "best_saving_gross": 2.10, "extra_miles": 1.2, "extra_minutes": 2.7,
         "best_net_benefit": 0.85, "breakeven_miles": 2.9, "value_of_time": 19}


def test_validator_accepts_template_and_flags_invented_number():
    assert validate(template_explanation(FACTS), FACTS) == (1.0, [])
    rate, bad = validate("Stop and save $7.50 per fill-up.", FACTS)
    assert bad == [7.5] and rate == 0.0


class LyingProvider:
    name = "liar"

    def generate(self, facts):
        return "Save $9.99 today."


class HonestProvider:
    name = "honest"

    def generate(self, facts):
        return "Net benefit is 0.85 dollars."


def test_explain_falls_back_to_template_after_retry():
    res = explain(FACTS, LyingProvider(), retries=1)
    assert res["fallback_used"] and res["source"] == "template" and res["attempts"] == 2
    ok = explain(FACTS, HonestProvider())
    assert not ok["fallback_used"] and ok["source"] == "honest"


def test_anthropic_provider_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not AnthropicProvider().available()


# ---- treatment, lag correlation, EDA/QA, CSV cache -----------------------------------
def test_treat_series_fills_missing_and_flags_outlier():
    import pandas as pd
    rng = np.random.default_rng(0)
    y = pd.Series(3.0 + np.cumsum(rng.normal(0, 0.01, 200)),
                  index=pd.date_range("2020-01-06", periods=200, freq="W-MON"))
    y.iloc[50] = np.nan
    y.iloc[120] += 1.5  # one-week spike
    kept, r1 = treat_series(y, outliers="keep")
    fixed, r2 = treat_series(y, outliers="interpolate")
    assert r1["n_missing_filled"] == 1 and not kept.isna().any()
    assert r1["n_outliers_flagged"] >= 1
    assert abs(kept.iloc[120] - y.iloc[120]) < 1e-9          # keep: spike stays
    assert fixed.iloc[120] < kept.iloc[120] - 1.0            # interpolate: spike removed
    with pytest.raises(ValueError):
        treat_series(y, outliers="drop")


def test_lagged_corr_detects_one_week_lead():
    rng = np.random.default_rng(1)
    b = np.cumsum(rng.normal(0, 1, 300))
    a = np.roll(b, 1)  # a(t) = b(t-1): b leads a by 1 week
    lc = lagged_corr(a[2:], b[2:], 3)
    assert max(lc, key=lc.get) == 1 and lc[1] > 0.95


EDA_FACTS = {"weeks": 1200, "n_missing_filled": 12, "n_outliers_flagged": 9,
             "latest_florida": 3.45, "corr_changes": 0.91, "best_lag_weeks": 0,
             "best_method": "moving_avg_4", "best_ratio": 1.04}


class FakeLLM:
    name = "fake"

    def __init__(self, text):
        self.text = text

    def complete(self, prompt, max_tokens=250):
        return self.text


def test_ask_eda_template_and_validation():
    res = ask("eda", EDA_FACTS, None)
    assert res["source"] == "template" and "3.45" in res["text"]
    good = ask("eda", EDA_FACTS, FakeLLM("Latest price is 3.45 and correlation is 0.91."))
    assert good["source"] == "fake" and not good["fallback_used"]
    bad = ask("eda", EDA_FACTS, FakeLLM("Latest price is 9.99."))
    assert bad["fallback_used"] and bad["attempts"] == 2 and "3.45" in bad["text"]


def test_ask_qa_without_provider_says_it_needs_llm():
    res = ask("qa", {"gallons": 10}, None, question="what if 15 gallons?")
    assert "LLM provider" in res["text"]


def test_series_csv_roundtrip(tmp_path):
    from services import load_cached_series, save_series
    df = fixtures.synthetic_eia().head(60)
    p = str(tmp_path / "d" / "eia.csv")
    save_series(df, p)
    back = load_cached_series(p)
    assert list(back.columns) == ["florida", "us"] and len(back) == 60
    assert np.allclose(back["us"].dropna(), df["us"].dropna())
    assert load_cached_series(str(tmp_path / "none.csv")) is None


# ---- a configured provider that fails must say so, not look like "no key" --------------
class BrokenProvider:
    """A real key that the API rejects: wrong key, no credits, network down."""
    name = "anthropic"

    def generate(self, facts):
        raise RuntimeError("Error code: 401 - authentication_error")

    def complete(self, prompt, max_tokens=300):
        raise RuntimeError("Error code: 401 - authentication_error")


def test_explain_reports_why_the_llm_failed_instead_of_hiding_it():
    res = explain(FACTS, BrokenProvider())
    assert res["source"] == "template" and res["fallback_used"]
    assert "401" in res["error"]


def test_ask_reports_why_the_llm_failed():
    res = ask("eda", EDA_FACTS, BrokenProvider())
    assert res["source"] == "template" and res["fallback_used"]
    assert "401" in res["error"]


def test_a_working_llm_reports_no_error():
    assert explain(FACTS, HonestProvider())["error"] is None
    assert ask("eda", EDA_FACTS, FakeLLM("Latest price is 3.45."))["error"] is None
    assert ask("eda", EDA_FACTS, None)["error"] is None  # no provider is not an error
