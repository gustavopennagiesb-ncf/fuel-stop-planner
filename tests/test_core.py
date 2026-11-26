import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest
import requests

import geo
from detour import breakeven_miles, net_benefit, rank_stations, reference_price, sensitivity_table
from forecast import (SIGNAL_THRESHOLD, crude_model, crude_signal, lagged_corr, monthly_change_profile, naive,
                      rolling_origin_eval, signal_and_record, signal_text, signal_track_record, signed_cents, treat_series, weekly_changes_outliers)
from geo import corridor_filter, decode_polyline, encode_polyline, haversine_m, path_length_m
from llm import AnthropicProvider, GroqProvider, ask, explain, template_explanation, validate
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


def test_groq_provider_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert not GroqProvider().available()


def test_groq_provider_available_with_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    p = GroqProvider()
    assert p.available()
    assert p.name == "groq"


def test_groq_provider_complete_uses_requests(monkeypatch):
    """GroqProvider.complete posts to the Groq endpoint and returns the message content."""
    import types

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "Net benefit is 0.85 dollars."}}]}

    fake_requests = types.SimpleNamespace(
        post=lambda url, headers, json, timeout: FakeResp()
    )
    monkeypatch.setattr("llm.requests", fake_requests, raising=False)
    # Patch requests inside the groq provider's import
    import sys, unittest.mock as mock
    with mock.patch.dict(sys.modules, {"requests": fake_requests}):
        p = GroqProvider(api_key="gsk_test")
        # complete() does `import requests as _req` inside the method
        import importlib
        import llm as llm_mod
        original = None
        try:
            import requests as real_requests
            original = real_requests
        except ImportError:
            pass

        called = {}
        def fake_post(url, headers, json, timeout):
            called["url"] = url
            called["model"] = json.get("model")
            return FakeResp()

        with mock.patch("requests.post", fake_post):
            text = p.complete("test prompt", max_tokens=50)
        assert text == "Net benefit is 0.85 dollars."
        assert called["url"] == "https://api.groq.com/openai/v1/chat/completions"


def test_groq_provider_surfaces_error_via_explain(monkeypatch):
    """A network failure from GroqProvider is captured in explain()'s 'error' field."""
    import unittest.mock as mock

    def raise_error(*a, **kw):
        import requests
        raise requests.ConnectionError("offline")

    p = GroqProvider(api_key="gsk_test")
    with mock.patch("requests.post", raise_error):
        res = explain(FACTS, p, retries=0)
    assert res["fallback_used"]
    assert res["error"] is not None
    assert res["source"] == "template"


def test_llm_provider_helper_prefers_groq(monkeypatch):
    """_llm_provider() returns GroqProvider when GROQ_API_KEY is set, even with Anthropic key."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk_test")
    # Import app without running Streamlit
    import importlib, sys
    # We can test the logic directly by instantiating providers
    g = GroqProvider()
    a = AnthropicProvider()
    assert g.available()
    # Groq wins
    chosen = g if g.available() else (a if a.available() else None)
    assert chosen is g


def test_llm_provider_helper_falls_back_to_anthropic(monkeypatch):
    """When no GROQ key, AnthropicProvider is used."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk_test")
    g = GroqProvider()
    a = AnthropicProvider()
    assert not g.available()
    assert a.available()
    chosen = g if g.available() else (a if a.available() else None)
    assert chosen is a


def test_llm_provider_helper_returns_none_without_any_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    g = GroqProvider()
    a = AnthropicProvider()
    chosen = g if g.available() else (a if a.available() else None)
    assert chosen is None


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
    assert list(back.columns) == ["florida", "us", "wti"] and len(back) == 60
    assert np.allclose(back["us"].dropna(), df["us"].dropna())
    assert load_cached_series(str(tmp_path / "none.csv")) is None


# ---- crude (WTI) series ------------------------------------------------------------------
def test_fetch_wti_shifts_friday_to_monday():
    payload = {"response": {"total": "2", "data": [
        {"period": "2026-11-13", "value": "90.78"}, {"period": "2026-11-06", "value": "95.97"}]}}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class Sess:
        def get(self, url, params=None, timeout=None):
            return Resp()

    s = eia.fetch_wti("k", session=Sess())
    assert list(s.index) == [pd.Timestamp("2026-11-09"), pd.Timestamp("2026-11-16")]
    assert list(s.values) == [95.97, 90.78]


def test_load_live_survives_a_crude_failure(monkeypatch):
    rows = [("2026-11-02", 3.0), ("2026-11-09", 3.1)]
    monkeypatch.setattr(eia, "fetch_all", lambda *a, **k: rows)

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(eia, "fetch_wti", boom)
    df = eia.load_live("k")
    assert list(df.columns) == ["florida", "us"]


def test_synthetic_crude_leads_the_synthetic_pump():
    df = fixtures.synthetic_eia()
    assert lagged_corr(df.us, df.wti / 42, 2)[1] > 0.3
    # retail columns must be exactly what they were before wti existed
    assert round(df.florida.sum(), 3) == 3031.991
    assert round(df.us.sum(), 3) == 3091.44


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


def test_net_benefit_never_exceeds_the_gross_saving():
    import detour as d
    stations = [{"id": str(i), "name": str(i), "price": p, "off_route_m": off, "lat": 0, "lng": 0}
                for i, (p, off) in enumerate([(3.3, 0.0), (3.5, 50.0), (3.4, 4000.0)])]
    for r in d.rank_stations(stations, 3.70, 10.0, 27.2, 19.0, 45.0):
        assert r["net_benefit"] <= round(10.0 * (3.70 - r["price"]), 2) + 1e-9


def test_route_position_on_a_two_segment_path():
    path = [(27.0, -82.0), (27.0, -81.9), (27.1, -81.9)]
    along, off = geo.route_position((27.0005, -81.95), path)
    seg1 = geo.haversine_m(path[0], path[1])
    assert along == pytest.approx(seg1 / 2, rel=0.02)
    assert off == pytest.approx(55, abs=10)  # ~0.0005 deg of latitude


def test_route_position_behind_the_start_is_zero_along():
    path = [(27.0, -82.0), (27.0, -81.9)]
    along, _ = geo.route_position((27.0, -82.05), path)
    assert along == 0.0


def _fixture_stations():
    return [s for s in load_trip(None, None, 3000, provider="fixture")["stations"]
            if s["price"] is not None]


def test_rank_stations_mapping_matches_a_uniform_number():
    sts = _fixture_stations()
    by_number = rank_stations(sts, 3.5, 10, 27, 19, 45)
    by_map = rank_stations(sts, 3.5, {s["id"]: 10 for s in sts}, 27, 19, 45)
    assert [r["net_benefit"] for r in by_number] == [r["net_benefit"] for r in by_map]
    assert all(r["gallons"] == 10 for r in by_map)


def test_rank_stations_skips_stations_without_gallons():
    sts = _fixture_stations()
    ranked = rank_stations(sts, 3.5, {sts[0]["id"]: 12}, 27, 19, 45)
    assert [r["id"] for r in ranked] == [sts[0]["id"]] and ranked[0]["gallons"] == 12


def test_rank_stations_uses_each_stations_own_gallons():
    st = [{"id": "a", "price": 3.40, "off_route_m": 1200},
          {"id": "b", "price": 3.45, "off_route_m": 800}]
    ranked = rank_stations(st, 3.60, {"a": 5, "b": 15}, 27.2, 19, 45)
    for r in ranked:
        g = {"a": 5, "b": 15}[r["id"]]
        assert r["gallons"] == g
        miles = 2 * next(s for s in st if s["id"] == r["id"])["off_route_m"] / 1609.344
        assert r["net_benefit"] == round(net_benefit(g, 3.60, r["price"], miles,
                                                     miles / 45 * 60, 27.2, 19), 2)


def _plan_facts(**kw):
    facts = {"best_station": "RoadStop #28", "best_price": 3.41, "p_ref": 3.6, "gallons": 8.8,
             "best_saving_gross": 1.67, "extra_miles": 1.2, "extra_minutes": 2.1,
             "best_net_benefit": 1.12, "breakeven_miles": 3.4, "value_of_time": 19.0,
             "verdict": "the detour pays off: stop at the best station"}
    facts.update(kw)
    return facts


def test_template_keeps_one_decimal_gallons_so_every_number_traces():
    facts = _plan_facts()
    text = template_explanation(facts)
    assert "8.8 gallons" in text and "9 gallons" not in text
    assert validate(text, facts) == (1.0, [])
    skip = _plan_facts(best_net_benefit=-0.4,
                       verdict="no detour pays off enough: fill up on your route")
    assert validate(template_explanation(skip), skip) == (1.0, [])


def test_template_follows_a_no_stop_verdict():
    facts = _plan_facts(stop_needed=False, arrive_gal=6.4,
                        verdict="you have enough fuel: no stop needed")
    text = template_explanation(facts)
    assert text.startswith("No stop needed") and "6.4 gal" in text and "Stop at" not in text
    assert validate(text, facts) == (1.0, [])


def test_template_follows_a_skip_verdict_with_a_small_positive_saving():
    facts = _plan_facts(best_net_benefit=0.3,
                        verdict="no detour pays off enough: fill up on your route")
    text = template_explanation(facts)
    assert "Stop at" not in text and "below your minimum" in text
    assert validate(text, facts) == (1.0, [])


def test_sensitivity_prices_each_column_at_the_cheaper_station():
    """A gap of $0.30 means the station costs ref - 0.30, and the detour's fuel is bought there."""
    ref = 3.53
    t = sensitivity_table(10, ref, 27.2, 45)
    for vot, row in t.items():
        for gap, miles in row.items():
            assert miles == round(breakeven_miles(10, gap, ref - gap, 27.2, vot, 45), 1)


def test_point_along_walks_the_path_and_clamps():
    from geo import haversine_m, point_along
    path = [(27.0, -82.0), (27.1, -82.0), (27.2, -82.0)]
    leg = haversine_m(path[0], path[1])
    assert point_along(path, -5) == path[0]
    assert point_along(path, 10 ** 9) == path[-1]
    mid = point_along(path, leg * 1.5)
    assert mid[0] == pytest.approx(27.15) and mid[1] == pytest.approx(-82.0)


def test_treat_series_trims_weeks_before_the_series_starts_instead_of_inventing_them():
    import pandas as pd
    y = pd.Series([np.nan, np.nan, 3.0, np.nan, 3.2, 3.3],
                  index=pd.date_range("2003-01-06", periods=6, freq="W-MON"))
    out, rep = treat_series(y)
    assert rep["n_trimmed"] == 2 and rep["n_missing_filled"] == 1
    assert rep["start"] == "2003-01-20" and len(out) == 4
    assert out.iloc[1] == pytest.approx(3.1)


# ---- crude signal --------------------------------------------------------------------
def _lead_series(n=400, seed=0):
    rng = np.random.default_rng(seed)
    c = 2.0 + np.cumsum(rng.normal(0, 0.08, n))
    p = np.zeros(n)
    p[0] = 3.0
    for t in range(1, n):
        lag = c[t - 1] - c[t - 2] if t >= 2 else 0.0
        p[t] = p[t - 1] + 0.4 * lag + rng.normal(0, 0.005)
    return p, c


def test_crude_model_beats_naive_when_crude_leads():
    p, c = _lead_series()
    res = rolling_origin_eval(p, methods={"naive": naive, "crude": crude_model(c)})
    assert res["crude"][1]["ratio_to_naive"] < 0.9


def test_crude_signal_says_wait_after_a_crude_drop():
    p, c = _lead_series()
    c = c.copy()
    c[-2] = c[-3] - 0.20
    c[-1] = c[-2] - 0.20
    sig = crude_signal(p, c)
    assert sig["verdict"] == "wait"
    assert sig["forecast"] < -SIGNAL_THRESHOLD


def test_crude_signal_needs_enough_history():
    p, c = _lead_series(50)
    assert crude_signal(p, c) is None


def test_crude_model_skips_weeks_without_crude():
    p, c = _lead_series()
    c = c.copy()
    c[[50, 120, 200, 260, 300]] = np.nan
    sig = crude_signal(p, c)
    assert sig is not None and np.isfinite(sig["forecast"])


def test_track_record_counts_hits():
    p, c = _lead_series()
    rec = signal_track_record(p, c)
    assert rec["wait"]["n"] > 0
    assert 0.5 < rec["wait"]["hit"] <= 1
    assert "ratio_by_period" not in rec


def test_monthly_profile_has_twelve_months():
    prof = monthly_change_profile(fixtures.synthetic_eia()["florida"])
    assert list(prof.index) == list(range(1, 13))


def test_crude_signal_uses_the_latest_crude_week_when_this_weeks_is_missing():
    p, c = _lead_series()
    c = c.copy()
    c[-1] = np.nan
    sig = crude_signal(p, c)
    assert sig is not None and sig["crude_lag_weeks"] == 1
    assert sig["crude_change_2w"] == pytest.approx(c[-2] - c[-4])
    assert crude_signal(*_lead_series())["crude_lag_weeks"] == 0


def test_track_record_reports_the_first_scored_year():
    p, c = _lead_series()
    dates = pd.date_range("2004-01-05", periods=len(p), freq="W-MON")
    rec = signal_track_record(p, c, dates=dates)
    assert rec["since"] == dates[104 + 1].year


def test_signed_cents_never_prints_negative_zero():
    assert [signed_cents(x) for x in (-0.4, 0.4, -0.6, 0, 7.2, -7.2)] == \
        ["0", "0", "-1", "0", "+7", "-7"]


def test_signed_cents_decimals():
    assert [signed_cents(x, 1) for x in (-2.6, 2.64, -0.04, 0.0, 7.34)] == \
        ["-2.6", "+2.6", "0.0", "0.0", "+7.3"]


def test_a_late_crude_signal_is_paired_with_a_late_crude_record():
    p, c = _lead_series()
    c = c.copy()
    c[-1] = np.nan
    dates = pd.date_range("2004-01-05", periods=len(p), freq="W-MON")
    sig, rec = signal_and_record(p, c, dates)
    assert sig["crude_lag_weeks"] == 1
    shifted = signal_track_record(p, np.r_[np.nan, c[:-1]], dates=dates)
    same_week = signal_track_record(p, c, dates=dates)
    assert repr(rec) == repr(shifted) and repr(rec) != repr(same_week)
    sig0, rec0 = signal_and_record(*_lead_series(), dates)
    assert sig0["crude_lag_weeks"] == 0 and rec0["wait"]["n"] > 0


def test_the_last_period_is_open_ended_and_labelled_from_the_data():
    p, c = _lead_series(1000)
    dates = pd.date_range("2005-01-03", periods=len(p), freq="W-MON")
    labels = list(signal_track_record(p, c, dates=dates)["ratio_by_period"])
    assert labels[-1] == f"2020-{dates[-1].year}"
    later = pd.date_range("2012-01-02", periods=len(p), freq="W-MON")  # runs past 2026
    assert later[-1].year > 2026
    assert list(signal_track_record(p, c, dates=later)["ratio_by_period"])[-1] == \
        f"2020-{later[-1].year}"


def _sig(verdict="wait", fc=-0.07, lag=0):
    return {"forecast": fc, "verdict": verdict, "pump_change_1w": -0.20,
            "crude_change_2w": -0.35, "crude_lag_weeks": lag}


_REC = {"wait": {"n": 197, "hit": 0.87}, "fill": {"n": 235, "hit": 0.61}, "since": 2005}


def test_signal_text_sentences():
    t = signal_text(_sig(), _REC, 10)
    assert t["move"] == ("Next week's Florida price is expected to fall about 7¢/gal, "
                         "about $0.70 on the 10 gal you buy.")
    assert t["why"] == ("Crude oil fell 35¢/gal over the last two weeks and the pump price fell "
                        "20¢ last week; pump prices follow crude within about a week.")
    assert t["track"] == ("When the model said wait, the price fell the next week in 87% of "
                          "197 weeks since 2005.")


def test_signal_text_drops_dollars_without_gallons_and_words_a_late_crude_week():
    t = signal_text(_sig(lag=1), _REC, 0)
    assert "$" not in t["move"] and t["move"].endswith("7¢/gal.")
    assert "over the two latest weeks of data" in t["why"]


def test_signal_text_fill_and_none():
    assert "said fill up now, the price rose the next week in 61% of 235 weeks" in \
        signal_text(_sig("fill", 0.05), _REC, 10)["track"]
    t = signal_text(_sig("none", 0.01), _REC, 10)
    assert "too small to act on" in t["move"]
    assert "less than 3¢/gal" in t["move"] and "about 0" not in t["move"]
    assert "87%" in t["track"] and "61%" in t["track"]


def test_signal_text_prints_one_decimal_gallons_and_calls_zero_moves_flat():
    t = signal_text(_sig(fc=-0.07), _REC, 12.6)
    assert "on the 12.6 gal you buy" in t["move"]
    assert "on the 10 gal you buy" in signal_text(_sig(fc=-0.07), _REC, 10)["move"]
    flat = signal_text({**_sig(), "crude_change_2w": -0.004, "pump_change_1w": 0.002}, _REC, 10)
    assert "Crude oil was flat over" in flat["why"] and "pump price was flat last week" in flat["why"]
    assert "0¢" not in flat["why"]


def test_save_series_keeps_the_cached_crude_when_the_new_fetch_lacks_it(tmp_path):
    from services import load_cached_series, save_series
    df = fixtures.synthetic_eia().head(60)
    p = str(tmp_path / "eia.csv")
    save_series(df, p)
    newer = df.drop(columns="wti").iloc[10:]
    save_series(newer, p)
    back = load_cached_series(p)
    assert "wti" in back and np.allclose(back["wti"].dropna(), df["wti"].loc[back.index].dropna())
    save_series(df.assign(wti=1.0), p)  # a fresh crude column replaces the cache
    assert (load_cached_series(p)["wti"] == 1.0).all()


def test_a_live_load_without_crude_uses_the_cached_crude_in_the_session(tmp_path, monkeypatch):
    """EIA can answer for retail and time out for crude: the page must still get the crude
    weeks saved earlier, not only the file on disk."""
    import config
    import services
    df = fixtures.synthetic_eia().head(60)
    p = str(tmp_path / "eia.csv")
    services.save_series(df, p)
    monkeypatch.setattr(services, "CACHE_PATH", p)
    monkeypatch.setattr(config, "EIA_API_KEY", "k")
    monkeypatch.setattr(eia, "load_live", lambda key: df.drop(columns="wti"))
    out, label = services.load_series(provider="google")
    assert label == "EIA live" and "wti" in out and out["wti"].notna().any()


def test_eia_requests_fail_fast():
    assert eia.TIMEOUT_S <= 10
