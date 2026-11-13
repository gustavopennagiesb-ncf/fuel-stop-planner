"""One clear answer for the Where-to-stop tab. Pure functions, no Streamlit.

`recommend` takes the stations already ranked by net benefit (detour.rank_stations, best first)
and decides: "go" (the best stop saves at least `min_saving`, and always more than zero), "skip"
(nothing pays off enough: fill up at the first station on the route) or "none" (no priced station).
The default minimum is an assumption: below about 50 cents the saving is not worth thinking about.
"""
import re

MIN_SAVING_DEFAULT = 0.50
_MD_SPECIAL = re.compile(r"([\\`*_\[\]()#$<>|~])")


def md_escape(text):
    """Station names are untrusted text: stop Markdown, links and $math$ from being interpreted."""
    return _MD_SPECIAL.sub(r"\\\1", str(text))


def _minutes(m):
    """'under 1 min' instead of a confusing '0 min' for a very short detour."""
    return "under 1 min" if m < 0.5 else f"{m:.0f} min"


def recommend(ranked, min_saving=MIN_SAVING_DEFAULT, n_alternatives=3):
    floor = max(min_saving, 0.0)

    def worth(s):
        return s["net_benefit"] > 0 and s["net_benefit"] >= floor

    if not ranked:
        return {"verdict": "none", "best": None, "alternatives": [], "min_saving": floor}
    best = ranked[0]
    if not worth(best):
        return {"verdict": "skip", "best": best, "alternatives": [], "min_saving": floor}
    alts = [s for s in ranked[1:] if worth(s)][:n_alternatives]
    return {"verdict": "go", "best": best, "alternatives": alts, "min_saving": floor}


def summary(rec):
    """(title, body) shown in the recommendation card."""
    best = rec["best"]
    if rec["verdict"] == "go":
        return (f"Fill up at {md_escape(best['name'])}",
                f"${best['price']:.2f}/gal. Stopping here saves about ${best['net_benefit']:.2f} "
                f"after the detour ({best['extra_miles']:.1f} extra miles, "
                f"{_minutes(best['extra_minutes'])}).")
    if rec["verdict"] == "skip":
        return ("No detour pays off enough",
                f"The best option saves only ${best['net_benefit']:.2f}, below your minimum of "
                f"${rec['min_saving']:.2f}. Fill up at the first station on your route.")
    return ("No station has a price yet",
            'Open "Edit prices" below and type the price of a station to get a recommendation.')
