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


def test_sidebar_controls_have_tips_with_an_example(at):
    wanted = ("Gallons to buy", "Value of your time ($/h)", "Average speed on detours (mph)",
              "Search corridor width (km each side)")
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
