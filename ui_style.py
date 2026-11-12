"""Small UI tweaks and the arithmetic that justifies them. No Streamlit import.

The theme itself lives in .streamlit/config.toml (a neutral slate-and-navy look); tests/test_ui_style.py reads
its colors and checks WCAG AA contrast with contrast_ratio(). Streamlit draws captions at opacity
0.6 (STREAMLIT_CAPTION_OPACITY), so the text color is chosen dark enough for captions to pass.
"""

STREAMLIT_CAPTION_OPACITY = 0.6  # how Streamlit draws captions by default


def _luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg, opacity=1.0):
    """WCAG contrast of `fg` drawn over `bg` at the given opacity (1.0 = fully opaque)."""
    shown = tuple(opacity * f + (1 - opacity) * b for f, b in zip(fg, bg))
    hi, lo = sorted((_luminance(shown), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
