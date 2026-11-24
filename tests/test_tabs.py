"""Every tab must render. Tab bodies run only when their tab is open (st.tabs on_change="rerun"),
so without opening them here three tabs would never be exercised by any test."""
from pathlib import Path

import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"
TABS = {
    "where": ":material/local_gas_station: Where to stop",
    "driver": ":material/delivery_dining: Driver mode",
    "when": ":material/calendar_month: When to fill up",
    "sensitivity": ":material/calculate: Sensitivity",
    "eda": ":material/analytics: EDA",
    "help": ":material/help: How to use",
}


@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def open_tab(monkeypatch, label):
    monkeypatch.setattr(fueleconomy, "_get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("x")))
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.session_state["main_tabs"] = label
    app.run()
    return app


@pytest.mark.parametrize("name,label", TABS.items(), ids=list(TABS))
def test_every_tab_renders_without_an_exception(monkeypatch, name, label):
    assert not open_tab(monkeypatch, label).exception


def test_the_eda_tab_reports_the_cleaning_it_did(monkeypatch):
    app = open_tab(monkeypatch, TABS["eda"])
    labels = {m.label for m in app.metric}
    assert {"Weeks", "Missing weeks filled", "Price shocks flagged"} <= labels


def test_the_sensitivity_tab_shows_the_break_even_table(monkeypatch):
    app = open_tab(monkeypatch, TABS["sensitivity"])
    frames = [d for d in app.dataframe
              if getattr(d.value, "index", None) is not None
              and d.value.index.name == "value of time $/h"]
    assert frames, "the break-even table is missing"


def test_the_sensitivity_table_labels_money_and_time(monkeypatch):
    app = open_tab(monkeypatch, TABS["sensitivity"])
    frame = next(d.value for d in app.dataframe
                 if getattr(d.value, "index", None) is not None
                 and d.value.index.name == "value of time $/h")
    assert list(frame.columns)[0] == "$0.10"
    assert str(frame.index[0]).endswith("/h")



VERDICTS = ("Wait a week if you can", "Fill up now", "No clear signal: fill up when you need to")


def test_the_when_tab_gives_a_verdict(monkeypatch):
    app = open_tab(monkeypatch, TABS["when"])
    headers = [h.value for h in app.header]
    assert headers and headers[0] in VERDICTS
    labels = {m.label for m in app.metric}
    assert {"Latest Florida price", "Expected change next week", "Crude oil, last 2 weeks",
            "Track record"} <= labels


def test_the_when_tab_shows_the_correlations(monkeypatch):
    app = open_tab(monkeypatch, TABS["when"])
    alts = " | ".join(c.alt for c in app.get("plotly_chart")).lower()
    assert "correlation" in alts and "by month" in alts


def test_the_forecast_table_includes_the_crude_model(monkeypatch):
    app = open_tab(monkeypatch, TABS["when"])
    frames = [d for d in app.dataframe if "Method" in getattr(d.value, "columns", [])]
    assert frames, "the forecast comparison table is missing"
    frame = frames[0].value
    methods = list(frame["Method"])
    assert methods[0].startswith("No change") and "Crude oil + momentum" in methods
    assert all(c.endswith("(¢)") for c in frame.columns if c != "Method")


def test_when_tab_without_crude_keeps_the_old_answer(monkeypatch):
    from providers import fixtures
    real = fixtures.synthetic_eia
    monkeypatch.setattr(fixtures, "synthetic_eia", lambda *a, **k: real(*a, **k).drop(columns="wti"))
    app = open_tab(monkeypatch, TABS["when"])
    assert not app.exception
    assert [h.value for h in app.header][0] in ("Timing the week doesn't pay",
                                                "A simple forecast helps a little")




def _with_signal(monkeypatch, **changes):
    import forecast
    real = forecast.signal_and_record

    def patched(*a, **k):
        sig, rec = real(*a, **k)
        return {**sig, **changes}, rec
    monkeypatch.setattr(forecast, "signal_and_record", patched)


def test_no_clear_signal_shows_the_small_move_and_no_track_record(monkeypatch):
    _with_signal(monkeypatch, verdict="none", forecast=-0.026)
    app = open_tab(monkeypatch, TABS["when"])
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Expected change next week"] == "-2.6¢/gal"
    assert metrics["Track record"] == "n/a"
    assert "less than 3¢/gal" in " ".join(m.value for m in app.markdown)


def test_a_late_crude_week_says_the_lighter_forecast_is_used(monkeypatch):
    from providers import fixtures
    real = fixtures.synthetic_eia

    def late(*a, **k):
        df = real(*a, **k)
        df.iloc[-1, df.columns.get_loc("wti")] = float("nan")
        return df
    monkeypatch.setattr(fixtures, "synthetic_eia", late)
    app = open_tab(monkeypatch, TABS["when"])
    assert not app.exception
    caps = " ".join(c.value for c in app.caption)
    assert "lighter forecast" in caps and "Wednesday" in caps


def test_the_when_tab_avoids_the_word_naive(monkeypatch):
    app = open_tab(monkeypatch, TABS["when"])
    visible = " ".join([*(m.value for m in app.markdown), *(c.value for c in app.caption)])
    assert "naive" not in visible.lower()
