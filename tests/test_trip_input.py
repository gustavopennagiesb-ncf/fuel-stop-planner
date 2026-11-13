"""The trip as the user entered it: a typed address, a place picked from the suggestions, or the
current location. Pure helpers; no Streamlit."""
import trip_input as trip


def test_a_typed_address_is_a_place_with_only_a_label():
    assert trip.text_place("  Sarasota, FL ") == {"label": "Sarasota, FL", "coords": None,
                                                  "place_id": None}


def test_blank_text_is_no_place():
    assert trip.text_place("   ") is None and trip.text_place(None) is None


def test_a_picked_place_keeps_label_coordinates_and_id():
    pick = {"coords": (28.5383, -81.3792), "label": "Orlando, FL, USA", "place_id": "ChIJabc"}
    got = trip.pick_place(pick)
    assert got == {"label": "Orlando, FL, USA", "coords": (28.5383, -81.3792),
                   "place_id": "ChIJabc"}


def test_the_current_location_is_a_place_with_coordinates():
    got = trip.location_place((27.9506, -82.4572))
    assert got["coords"] == (27.9506, -82.4572) and got["label"] == "Your current location"


def test_routes_receives_coordinates_when_known_else_the_text():
    assert trip.route_point(trip.text_place("Tampa, FL")) == "Tampa, FL"
    picked = trip.pick_place({"coords": (28.0, -82.0), "label": "x", "place_id": "p"})
    assert trip.route_point(picked) == (28.0, -82.0)


def test_links_use_the_label_and_place_id_of_a_picked_place_and_coordinates_for_a_location():
    picked = trip.pick_place({"coords": (28.0, -82.0), "label": "Orlando, FL, USA",
                              "place_id": "ChIJabc"})
    assert trip.link_point(picked) == ("Orlando, FL, USA", "ChIJabc")
    assert trip.link_point(trip.text_place("Tampa, FL")) == ("Tampa, FL", None)
    assert trip.link_point(trip.location_place((27.9, -82.4))) == ((27.9, -82.4), None)


def test_a_change_is_detected_only_when_what_is_searched_changes():
    a = {"origin": trip.text_place("A"), "destination": trip.text_place("B"), "corridor": 3.0}
    same = {"origin": trip.text_place(" A "), "destination": trip.text_place("B"), "corridor": 3.0}
    assert not trip.changed(same, a)
    assert trip.changed({**a, "corridor": 4.0}, a)
    assert trip.changed({**a, "destination": trip.text_place("C")}, a)
    picked = {**a, "origin": trip.pick_place({"coords": (1.0, 2.0), "label": "A", "place_id": "p"})}
    assert trip.changed(picked, a)  # the same words, but now an exact place


def test_defaults_match_the_synthetic_route_names():
    d = trip.DEFAULT
    assert d["origin"]["label"] == "Sarasota, FL" and d["destination"]["label"] == "Orlando, FL"
    assert d["corridor"] == 3.0
