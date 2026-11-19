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


def minutes_label(m):
    """'under 1 min' instead of a confusing '0 min' for a very short detour."""
    return "under 1 min" if m < 0.5 else f"{m:.0f} min"


def recommend(ranked, min_saving=MIN_SAVING_DEFAULT, n_alternatives=3, stop_needed=None):
    """stop_needed: None = not fuel-aware (today's go/skip/none); False = the tank covers the
    trip (verdict "no_stop", keeping the best deal as optional); True = a stop is needed."""
    floor = max(min_saving, 0.0)
    fuel_aware = stop_needed is not None

    def worth(s):
        return s["net_benefit"] > 0 and s["net_benefit"] >= floor

    def result(verdict, best, alts):
        return {"verdict": verdict, "best": best, "alternatives": alts,
                "min_saving": floor, "fuel_aware": fuel_aware}

    if stop_needed is False:
        return result("no_stop", ranked[0] if ranked and worth(ranked[0]) else None, [])
    if not ranked:
        return result("none", None, [])
    best = ranked[0]
    if not worth(best):
        return result("skip", best, [])
    return result("go", best, [s for s in ranked[1:] if worth(s)][:n_alternatives])


def summary(rec, nearest=None):
    """(title, body) shown in the recommendation card. `nearest`: under a fuel plan with a stop
    needed, the reachable station with the smallest along-route reach (with its gallons); a
    skip then names it instead of the generic "first station on your route"."""
    best = rec["best"]
    if rec["verdict"] == "no_stop":
        if best is None:
            return ("No stop needed", "")
        gal = f" for {best['gallons']:.1f} gal" if "gallons" in best else ""
        return ("No stop needed",
                f"If you want to fill up anyway, the best deal is {md_escape(best['name'])} "
                f"(saves about ${best['net_benefit']:.2f}{gal}).")
    if rec["verdict"] == "go":
        buy = (f" · buy {best['gallons']:.1f} gal"
               if rec.get("fuel_aware") and "gallons" in best else "")
        return (f"Fill up at {md_escape(best['name'])}{buy}",
                f"${best['price']:.2f}/gal. Stopping here saves about ${best['net_benefit']:.2f} "
                f"after the detour ({best['extra_miles']:.1f} extra miles, "
                f"{minutes_label(best['extra_minutes'])}).")
    if rec["verdict"] == "skip" and rec.get("fuel_aware") and nearest is not None:
        return ("Stop for fuel",
                "No detour pays off enough, so take the nearest reachable station: "
                f"{md_escape(nearest['name'])} · buy {nearest['gallons']:.1f} gal.")
    if rec["verdict"] == "skip":
        nb = best["net_benefit"]
        saving_clause = (f"costs ${-nb:.2f} to detour" if nb < 0
                         else f"saves only ${nb:.2f}, below your minimum of ${rec['min_saving']:.2f}")
        return ("No detour pays off enough",
                f"The best option {saving_clause}. Fill up at the first station on your route.")
    return ("No station has a price yet",
            'Open "Edit prices" below and type the price of a station to get a recommendation.')


def cheapest(ranked):
    """The station with the lowest price, whatever the detour (ties: the better saving)."""
    return min(ranked, key=lambda s: (s["price"], -s["net_benefit"]), default=None)


def describe_cheapest(station, p_ref, recommended_id=None):
    """(headline, detail) for the cheapest station, including what its detour does to the saving."""
    gap = p_ref - station["price"]
    gap_text = (f"${gap:.2f} below" if gap >= 0.005 else f"${-gap:.2f} above")
    net = station["net_benefit"]
    outcome = (f"saves about ${net:.2f}" if net >= 0.005 else
               f"costs you about ${-net:.2f}" if net <= -0.005 else "breaks even")
    detail = (f"{gap_text} the usual price on your route, "
              f"{minutes_label(station['extra_minutes'])} detour. Stopping there {outcome}.")
    if recommended_id is not None and station["id"] == recommended_id:
        detail += " This is also the recommended stop."
    return (f"Cheapest gas on this route: {md_escape(station['name'])} at "
            f"${station['price']:.2f}/gal", detail)
