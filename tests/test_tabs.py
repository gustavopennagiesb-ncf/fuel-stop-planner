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
    "ai": ":material/smart_toy: AI",
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


def test_the_when_tab_compares_forecasts_against_the_naive_one(monkeypatch):
    app = open_tab(monkeypatch, TABS["when"])
    frames = [d for d in app.dataframe if "method" in getattr(d.value, "columns", [])]
    assert frames, "the forecast comparison table is missing"
    assert "naive" in list(frames[0].value["method"])


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
