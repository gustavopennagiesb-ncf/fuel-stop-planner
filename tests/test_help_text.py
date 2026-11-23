"""The app should explain itself: every tab says what question it answers, the sidebar controls
have plain-language tips with an example, and the jargon is defined in one glossary."""
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"
INTRO = "What this tab answers:"


@pytest.fixture
def at(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    return app


def test_the_default_page_describes_the_route_comparison(at):
    header = " ".join(h.proto.body for h in at.main.get("html"))
    assert '<h1 class="fs-title">Fuel Stop Planner</h1>' in header
    assert "Compare fuel stops along your route" in header


def test_how_to_use_covers_every_tab(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.session_state["main_tabs"] = ":material/help: How to use"
    app.run()
    text = " ".join(m.value for m in app.markdown)
    for name in ("Where to stop", "When to fill up", "EDA", "Sensitivity", "AI"):
        assert name in text
    assert "Which tab should I open?" in text


def test_corridor_width_has_a_tip_with_an_example(at):
    box = next(n for n in at.number_input if n.label.startswith("Search corridor width"))
    assert "for example" in box.help.lower()


def test_sidebar_controls_have_tips_with_an_example(at):
    wanted = ("Gallons to buy", "Value of your time ($/h)", "Average speed on detours (mph)")
    by_label = {s.label: s for s in at.sidebar.slider}
    for label in wanted:
        assert by_label[label].help, f"{label} has no tip"
        assert "for example" in by_label[label].help.lower()


def test_glossary_defines_the_jargon(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.session_state["main_tabs"] = ":material/help: How to use"
    app.run()
    glossary = [e for e in app.expander if e.label == "Glossary"]
    assert glossary, "no Glossary expander"
    text = " ".join(m.value for m in glossary[0].markdown)
    for term in ("You save", "Gas price on your route", "Value of time", "Naive forecast"):
        assert term in text


def test_trip_and_car_controls_stay_in_the_main_page(at):
    chips = " ".join(x.proto.body for x in at.main.get("html") if "fs-chips" in x.proto.body)
    assert "Route" in chips and "Car" in chips  # the trip strip summarises both
    assert at.main.slider(key="mpg")  # in the "Pick your car" popover of the trip strip
    assert at.main.number_input(key="buffer_km")  # in the "Where are you going?" popover
    assert "mpg" not in {s.key for s in at.sidebar.slider}


def test_no_tab_sends_the_user_to_the_sidebar_for_the_mpg(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.session_state["main_tabs"] = ":material/calculate: Sensitivity"
    app.run()
    intros = [c.value for c in app.caption if c.value.startswith(INTRO)]
    mpg_intro = next(c for c in intros if "detour a given price gap" in c)
    assert "mpg (Pick your car)" in mpg_intro


def test_the_sidebar_is_divided_into_labeled_sections(at):
    sidebar_html = " ".join(h.proto.body for h in at.sidebar.get("html"))
    for label in ("Navigate", "Fill-up", "Time", "Data"):
        assert f'class="fs-label">{label}<' in sidebar_html
    assert {s.label for s in at.sidebar.slider} == {
        "Gallons to buy", "Value of your time ($/h)", "Average speed on detours (mph)"}


def test_the_app_opens_on_the_answer_and_keeps_the_manual_last(at):
    buttons = at.sidebar.button
    labels = [b.label for b in buttons]
    assert labels[0] == "Where to stop"
    assert labels[1] == "When to fill up"
    assert labels[-1] == "How to use"


def test_every_tab_label_carries_an_icon_so_tabs_stay_scannable(at):
    buttons = at.sidebar.button
    for b in buttons:
        assert b.icon and b.icon.startswith(":material/")


def test_how_to_use_names_the_current_controls(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.session_state["main_tabs"] = ":material/help: How to use"
    app.run()
    text = " ".join(m.value for m in app.markdown)
    for control in ("Where are you going?", "Pick your car", "How much fuel do you have?"):
        assert control in text
    assert '"Your trip"' not in text and '"Your car"' not in text
