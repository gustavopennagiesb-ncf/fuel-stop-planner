"""The theme (.streamlit/config.toml) must keep text readable: WCAG AA asks 4.5:1 for normal text.
Ratios are computed from the colors actually written in the config, so editing the palette to
something unreadable fails here."""
import tomllib
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

import ui_style
from providers import fueleconomy

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
CONFIG = ROOT / ".streamlit" / "config.toml"


def theme():
    return tomllib.loads(CONFIG.read_text())["theme"]


def rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def test_contrast_ratio_known_values():
    assert ui_style.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert ui_style.contrast_ratio((255, 255, 255), (255, 255, 255)) == pytest.approx(1.0)


def test_the_app_uses_a_dark_theme():
    t = theme()
    assert t["base"] == "dark"
    assert ui_style.contrast_ratio(rgb("#ffffff"), rgb(t["backgroundColor"])) > 10  # really dark


@pytest.mark.parametrize("background", ["backgroundColor", "secondaryBackgroundColor"])
def test_body_text_is_readable_on_both_backgrounds(background):
    t = theme()
    assert ui_style.contrast_ratio(rgb(t["textColor"]), rgb(t[background])) >= 7.0


@pytest.mark.parametrize("background", ["backgroundColor", "secondaryBackgroundColor"])
def test_captions_stay_above_aa_at_the_opacity_we_set(background):
    t = theme()
    got = ui_style.contrast_ratio(rgb(t["textColor"]), rgb(t[background]),
                                  opacity=ui_style.CAPTION_OPACITY)
    assert got >= 4.5


@pytest.mark.parametrize("background", ["backgroundColor", "secondaryBackgroundColor"])
def test_the_accent_color_is_readable_as_text(background):
    t = theme()
    assert ui_style.contrast_ratio(rgb(t["primaryColor"]), rgb(t[background])) >= 4.5


def test_caption_rule_is_injected_into_the_page(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    css = " ".join(m.value for m in at.markdown)
    assert "stCaptionContainer" in css and str(ui_style.CAPTION_OPACITY) in css
    assert not at.exception
