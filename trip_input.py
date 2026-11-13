"""The trip as the user entered it. Pure helpers, no Streamlit.

A place is {"label": text, "coords": (lat, lng) or None, "place_id": str or None}. It comes from
typed text, from a suggestion picked in the address box, or from the current location. The trip a
search uses ("applied") only changes when the user presses "Find stations", so typing or picking
never starts a paid search by itself.
"""


LOCATION_LABEL = "Your current location"


def text_place(text):
    text = (text or "").strip()
    return {"label": text, "coords": None, "place_id": None} if text else None


def pick_place(pick):
    """A parsed suggestion (places_autocomplete.parse_pick) as a place."""
    return {"label": pick["label"], "coords": tuple(pick["coords"]), "place_id": pick["place_id"]}


def location_place(coords):
    return {"label": LOCATION_LABEL, "coords": tuple(coords), "place_id": None}


def route_point(place):
    """What the Routes API receives: exact coordinates when known, else the typed text."""
    return place["coords"] if place["coords"] else place["label"]


def link_point(place):
    """(origin/destination, place id) for a Google Maps directions link: the label plus the
    place id of a picked suggestion; the coordinates for the current location."""
    if place["place_id"]:
        return place["label"], place["place_id"]
    if place["label"] == LOCATION_LABEL and place["coords"]:
        return place["coords"], None
    return place["label"], None


def _key(place):
    return (place["label"].strip(), place["coords"], place["place_id"])


def changed(pending, applied):
    return (_key(pending["origin"]) != _key(applied["origin"])
            or _key(pending["destination"]) != _key(applied["destination"])
            or pending["corridor"] != applied["corridor"])


DEFAULT = {"origin": text_place("Sarasota, FL"), "destination": text_place("Orlando, FL"),
           "corridor": 3.0}
