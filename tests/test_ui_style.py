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
    assert ui_style.contrast_ratio(rgb("#000000"), rgb(t["backgroundColor"])) < 1.3  # really dark


def sidebar_backgrounds():
    side = theme()["sidebar"]
    return [side["backgroundColor"], side["secondaryBackgroundColor"]]


@pytest.mark.parametrize("index", [0, 1])
def test_sidebar_text_and_captions_are_readable(index):
    t, bg = theme(), sidebar_backgrounds()[index]
    assert ui_style.contrast_ratio(rgb(t["textColor"]), rgb(bg)) >= 7.0
    assert ui_style.contrast_ratio(rgb(t["textColor"]), rgb(bg),
                                   opacity=ui_style.STREAMLIT_CAPTION_OPACITY) >= 5.0
    assert ui_style.contrast_ratio(rgb(t["primaryColor"]), rgb(bg)) >= 4.5


@pytest.mark.parametrize("background", ["backgroundColor", "secondaryBackgroundColor"])
def test_body_text_is_readable_on_both_backgrounds(background):
    t = theme()
    assert ui_style.contrast_ratio(rgb(t["textColor"]), rgb(t[background])) >= 7.0


@pytest.mark.parametrize("background", ["backgroundColor", "secondaryBackgroundColor"])
def test_captions_pass_aa_at_streamlits_default_opacity(background):
    t = theme()
    got = ui_style.contrast_ratio(rgb(t["textColor"]), rgb(t[background]),
                                  opacity=ui_style.STREAMLIT_CAPTION_OPACITY)
    assert got >= 5.0  # a margin over the 4.5 minimum


@pytest.mark.parametrize("background", ["backgroundColor", "secondaryBackgroundColor"])
def test_the_accent_colors_are_readable_as_text(background):
    t = theme()
    for name in ("primaryColor", "orangeColor", "redColor"):
        assert ui_style.contrast_ratio(rgb(t[name]), rgb(t[background])) >= 4.5, name


def test_the_page_injects_no_css(monkeypatch):
    """The look comes from config.toml alone (the Streamlit skill's rule: no CSS unless asked)."""
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not any("<style" in m.value for m in at.markdown)
    assert not at.exception
