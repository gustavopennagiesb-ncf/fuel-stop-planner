"""Caption text must reach the 4.5:1 contrast that WCAG AA asks for (checked here for the
default light theme; a higher opacity also raises contrast in the dark theme)."""
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

import ui_style
from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"


def test_contrast_ratio_known_values():
    assert ui_style.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert ui_style.contrast_ratio((255, 255, 255), (255, 255, 255)) == pytest.approx(1.0)


def test_streamlits_default_caption_opacity_is_below_aa_and_ours_is_above():
    for bg in (ui_style.LIGHT_BG_MAIN, ui_style.LIGHT_BG_SIDEBAR):
        default = ui_style.contrast_ratio(ui_style.LIGHT_TEXT, bg, opacity=0.6)
        ours = ui_style.contrast_ratio(ui_style.LIGHT_TEXT, bg, opacity=ui_style.CAPTION_OPACITY)
        assert default < 4.5
        assert ours >= 4.5


def test_caption_rule_is_injected_into_the_page(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    css = " ".join(m.value for m in at.markdown)
    assert "stCaptionContainer" in css and str(ui_style.CAPTION_OPACITY) in css
    assert not at.exception
