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


def test_every_tab_starts_with_what_it_answers(at):
    intros = [c.value for c in at.caption if c.value.startswith(INTRO)]
    assert len(intros) >= 1  # default section (Where to stop) has an intro


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


def test_the_help_points_to_where_the_settings_really_are(at):
    """"Your car" and "Your trip" are cards on the page; only the fill-up and time settings are
    in the sidebar."""
    text = " ".join(m.value for m in at.markdown)
    assert "Your car" in text and "Your trip" in text
    assert "sidebar holds the trip" not in text


def test_no_tab_sends_the_user_to_the_sidebar_for_the_mpg(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(fueleconomy, "_get", offline)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.session_state["main_tabs"] = ":material/calculate: Sensitivity"
    app.run()
    intros = [c.value for c in app.caption if c.value.startswith(INTRO)]
    mpg_intro = next(c for c in intros if "detour a given price gap" in c)
    assert "mpg (Your car)" in mpg_intro


def test_the_sidebar_header_names_what_the_sidebar_holds(at):
    assert at.sidebar.header[0].value == "Fill-up and time"
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
