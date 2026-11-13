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
    assert len(intros) == 6  # Where, When, EDA, Sensitivity, Driver mode, AI


def test_how_to_use_covers_every_tab_and_driver_mode(at):
    text = " ".join(m.value for m in at.markdown)
    for name in ("Where to stop", "When to fill up", "EDA", "Sensitivity", "Driver mode", "AI"):
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


def test_glossary_defines_the_jargon(at):
    glossary = [e for e in at.expander if e.label == "Glossary"]
    assert glossary, "no Glossary expander"
    text = " ".join(m.value for m in glossary[0].markdown)
    for term in ("Net benefit", "Reference price", "Detour source", "EDA", "Naive forecast"):
        assert term in text


def test_the_help_points_to_where_the_settings_really_are(at):
    """"Your car" and "Your trip" are cards on the page; only the fill-up and time settings are
    in the sidebar."""
    text = " ".join(m.value for m in at.markdown)
    assert "Your car" in text and "Your trip" in text
    assert "sidebar holds the trip" not in text


def test_no_tab_sends_the_user_to_the_sidebar_for_the_mpg(at):
    intros = [c.value for c in at.caption if c.value.startswith(INTRO)]
    mpg_intro = next(c for c in intros if "detour a given price gap" in c)
    assert "mpg (Your car)" in mpg_intro


def test_the_sidebar_header_names_what_the_sidebar_holds(at):
    assert at.sidebar.header[0].value == "Fill-up and time"
    assert {s.label for s in at.sidebar.slider} == {
        "Gallons to buy", "Value of your time ($/h)", "Average speed on detours (mph)"}


def test_the_app_opens_on_the_answer_and_keeps_the_manual_last(at):
    labels = [t.label for t in at.tabs]
    assert labels[0].endswith("Where to stop")
    assert labels[1].endswith("Driver mode")
    assert labels[-1].endswith("How to use")


def test_every_tab_label_carries_an_icon_so_seven_tabs_stay_scannable(at):
    assert all(t.label.startswith(":material/") for t in at.tabs)
