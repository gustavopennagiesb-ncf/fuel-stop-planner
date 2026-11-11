"""Small UI tweaks and the arithmetic that justifies them. No Streamlit import.

Streamlit draws captions at opacity 0.6, which on its default light theme measures about 3.8:1
against the sidebar and main backgrounds (below the 4.5:1 that WCAG AA asks for normal text).
CAPTION_OPACITY raises it. The colors below are the default light theme's, measured in the
browser; they are used only to check the ratio here.
"""

LIGHT_TEXT = (47, 45, 43)
LIGHT_BG_MAIN = (255, 255, 255)
LIGHT_BG_SIDEBAR = (248, 248, 247)

CAPTION_OPACITY = 0.75
CAPTION_CSS = ("<style>[data-testid=\"stCaptionContainer\"] "
               f"{{ opacity: {CAPTION_OPACITY} !important; }}</style>")


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
