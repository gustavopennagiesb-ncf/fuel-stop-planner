"""Where to stop: the answer first (go / skip / none), the map links, and the trip form. In google
mode the paid APIs are replaced by fixture data, so no key or call is used."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

import config
import services
from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def make_app(monkeypatch, *, mode="fixture", stations=None, session=None, rename_best=None,
             series=None):
    """mode "google" fakes the paid calls with fixture data (ids look like Place ids) and records
    every (origin, destination) that reaches load_trip."""
    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(fueleconomy, "_get", offline)
    calls = []
    real_trip, real_series = services.load_trip, services.load_series

    def fake_trip(origin=None, destination=None, buffer_m=3000, provider=None,
                  fuel_type="REGULAR_UNLEADED"):
        calls.append((origin, destination, buffer_m))
        trip = real_trip(None, None, buffer_m, provider="fixture")
        if stations is not None:
            trip = {**trip, "stations": stations}
        if mode == "google":
            sts = [{**s, "id": f"ChIJ_{i}"} for i, s in enumerate(trip["stations"])]
            if rename_best:
                sts = [{**s, "name": rename_best} for s in sts]  # whichever ranks first has it
            trip = {**trip, "stations": sts, "mode": "google"}
        return trip

    monkeypatch.setattr(services, "load_trip", fake_trip)
    monkeypatch.setattr(services, "load_series",
                        (lambda p: series) if series else (lambda p: real_series("fixture")))
    if mode == "google":
        monkeypatch.setattr(config, "PROVIDER", "google")
        monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key")
        monkeypatch.setattr(config, "EIA_API_KEY", "test-key")
        monkeypatch.setattr(services, "compute_detours", lambda trip, top_n=None: trip["stations"])
    app = AppTest.from_file(str(APP), default_timeout=30)
    for k, v in (session or {}).items():
        app.session_state[k] = v
    app.run()
    return app, calls


def links(app):
    return {b.proto.label: b.proto for b in app.get("link_button")
            if b.proto.label != "Open in Maps"}  # "Open in Maps": the other good options


def query(url):
    return parse_qs(urlsplit(url).query)


def headline(app):
    """The recommended stop's title: the heading of the green answer card."""
    return [h.value for h in app.header if h.value.startswith("Fill up at ")]


# --------------------------------------------------------------------- synthetic mode
def test_a_worthwhile_station_is_the_headline_and_the_links_are_off(monkeypatch):
    app, _ = make_app(monkeypatch)
    assert len(headline(app)) == 1
    saved = next(m for m in app.metric if m.label == "You save")
    assert saved.value.startswith("$")
    pair = links(app)
    assert set(pair) == {"Open in Google Maps", "Navigate via this station"}
    assert all(p.disabled for p in pair.values())
    assert not app.exception


def test_a_high_minimum_saving_turns_the_answer_into_skip(monkeypatch):
    app, _ = make_app(monkeypatch)
    app.number_input(key="min_saving").set_value(20.0).run()
    assert not headline(app) and not links(app)
    assert any(i.proto.title == "No detour pays off enough" for i in app.get("info"))
    assert not app.exception


def test_other_good_options_are_listed_when_there_are_any(monkeypatch):
    app, _ = make_app(monkeypatch)
    assert any("Other good options" in h.value for h in app.subheader)


def test_no_stations_gets_a_clear_message_not_a_crash(monkeypatch):
    app, _ = make_app(monkeypatch, stations=[])
    assert any(w.proto.title == "No stations found near this route" for w in app.get("warning"))
    assert not headline(app) and not app.exception


def test_no_priced_station_asks_for_a_price(monkeypatch):
    unpriced = [{"id": "x1", "name": "Unpriced", "lat": 27.5, "lng": -82.5, "price": None,
                 "off_route_m": 100.0}]
    app, _ = make_app(monkeypatch, stations=unpriced)
    assert any(w.proto.title == "No station has a price yet" for w in app.get("warning"))
    assert not app.exception


# --------------------------------------------------------------------- google mode (faked)
def test_the_links_carry_the_place_id_and_the_trip(monkeypatch):
    app, _ = make_app(monkeypatch, mode="google")
    pair = links(app)
    assert not any(p.disabled for p in pair.values())
    pin, nav = query(pair["Open in Google Maps"].url), query(pair["Navigate via this station"].url)
    assert pin["query_place_id"][0].startswith("ChIJ_")
    assert nav["waypoint_place_ids"] == pin["query_place_id"]
    assert nav["origin"] == ["Sarasota, FL"] and nav["destination"] == ["Orlando, FL"]
    assert nav["travelmode"] == ["driving"] and "key" not in nav


def test_a_location_origin_goes_into_the_navigation_link_as_coordinates(monkeypatch):
    app, _ = make_app(monkeypatch, mode="google", session={"my_location": (27.9506, -82.4572)})
    next(b for b in app.button if b.label == "Find stations").click().run()
    nav = query(links(app)["Navigate via this station"].url)
    assert nav["origin"] == ["27.950600,-82.457200"]


def test_a_hostile_station_name_is_escaped_in_the_headline(monkeypatch):
    app, _ = make_app(monkeypatch, mode="google", rename_best="**x**[a](http://evil)$y$")
    title = headline(app)[0]
    assert title == "Fill up at \\*\\*x\\*\\*\\[a\\]\\(http://evil\\)\\$y\\$"
    assert "evil" not in " ".join(links(app)[k].url for k in links(app))  # names never reach URLs


def test_typing_an_address_does_not_start_a_search_until_find_stations(monkeypatch):
    app, calls = make_app(monkeypatch, mode="google")
    first = len(calls)
    app.text_input(key="trip_origin_text").set_value("Tampa, FL").run()
    assert len(calls) == first, "typing alone started a (paid) search"
    assert any("You changed the trip" in c.value for c in app.caption)
    next(b for b in app.button if b.label == "Find stations").click().run()
    assert calls[-1][0] == "Tampa, FL" and len(calls) == first + 1
    assert not any("You changed the trip" in c.value for c in app.caption)
    assert not app.exception


def test_changing_the_corridor_waits_for_find_stations_too(monkeypatch):
    app, calls = make_app(monkeypatch, mode="google")
    first = len(calls)
    app.number_input(key="buffer_km").set_value(8.0).run()
    assert len(calls) == first
    next(b for b in app.button if b.label == "Find stations").click().run()
    assert calls[-1][2] == 8000.0


def test_an_empty_address_is_refused_with_a_message(monkeypatch):
    app, calls = make_app(monkeypatch, mode="google")
    first = len(calls)
    app.text_input(key="trip_destination_text").set_value("   ").run()
    assert any("cannot be empty" in w.value for w in app.warning)
    assert not [b for b in app.button if b.label == "Find stations"]
    assert len(calls) == first and not app.exception


# --------------------------------------------------------------------- cheapest price
def crafted():
    """One cheap station far off the route and four near it: the cheapest price is not the stop
    that saves the most."""
    def s(sid, name, price, off):
        return {"id": sid, "name": name, "lat": 27.5, "lng": -82.5, "price": price,
                "off_route_m": off}
    return [s("far", "Far Cheap", 3.40, 5000), s("n1", "Near 1", 3.55, 100),
            s("n2", "Near 2", 3.60, 200), s("n3", "Near 3", 3.65, 300),
            s("n4", "Near 4", 3.70, 400)]


def test_the_cheapest_price_is_shown_even_when_a_nearer_stop_saves_more(monkeypatch):
    app, _ = make_app(monkeypatch, stations=crafted())
    assert headline(app) == ["Fill up at Near 1"]
    text = " ".join(m.value for m in app.markdown) + " " + " ".join(c.value for c in app.caption)
    assert "Cheapest gas on this route: Far Cheap at $3.40/gal" in text
    assert "costs you about" in text  # the detour eats the saving, and it says so


def test_the_cheapest_station_gets_its_own_map_link_in_google_mode(monkeypatch):
    app, _ = make_app(monkeypatch, mode="google", stations=crafted())
    pair = links(app)
    assert "Open cheapest in Google Maps" in pair
    assert query(pair["Open cheapest in Google Maps"].url)["query_place_id"] == ["ChIJ_0"]
    assert query(pair["Open in Google Maps"].url)["query_place_id"] == ["ChIJ_1"]  # the pick


def test_when_the_cheapest_is_also_the_pick_there_is_no_duplicate_link(monkeypatch):
    sts = [s for s in crafted() if s["id"] != "far"]
    app, _ = make_app(monkeypatch, mode="google", stations=sts)
    assert "Open cheapest in Google Maps" not in links(app)
    assert set(links(app)) == {"Open in Google Maps", "Navigate via this station"}


# --------------------------------------------------------------------- the Google map embed
def test_the_google_map_uses_st_iframe_not_the_deprecated_components_html():
    source = APP.read_text()
    assert "components.html" not in source and "components.v1" not in source


def test_google_mode_with_a_maps_key_draws_the_map_in_an_iframe(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_MAPS_JS_KEY", "browser-key")
    app, _ = make_app(monkeypatch, mode="google")
    # make_app fakes load_trip in "google" mode but keeps fixture geometry; the map branch is
    # chosen by trip["mode"] == "google" and the browser key
    frames = app.get("iframe")
    assert frames and "maps.googleapis.com/maps/api/js?key=browser-key" in frames[0].proto.srcdoc
    assert not app.exception


# --------------------------------------------------------------------- address suggestions
import places_autocomplete  # noqa: E402


def trip_boxes(app):
    """The plain text boxes of the trip card (the AI tab has its own text input)."""
    return {t.key for t in app.text_input if t.key and t.key.startswith("trip_")}


def with_suggestions(monkeypatch, picks=None):
    """The browser key is set, and `picks` maps a box ('trip_origin_ac', ...) to what the address
    widget would send back on the run after a pick."""
    monkeypatch.setattr(config, "GOOGLE_MAPS_JS_KEY", "browser-key")
    seen = []

    def fake(key, js_key, placeholder="Type an address"):
        seen.append((key, js_key))
        return (picks or {}).get(key)

    monkeypatch.setattr(places_autocomplete, "request_pick", fake)
    return seen


PICK = {"lat": 28.5383, "lng": -81.3792, "label": "Orlando, FL, USA", "place_id": "ChIJorl"}


def test_the_address_boxes_use_the_browser_key(monkeypatch):
    seen = with_suggestions(monkeypatch)
    app, _ = make_app(monkeypatch, mode="google")
    assert {k for k, _ in seen} == {"trip_origin_ac", "trip_destination_ac"}
    assert all(js == "browser-key" for _, js in seen)
    assert not trip_boxes(app)  # no plain text boxes while suggestions work
    assert not app.exception


def test_picking_a_suggestion_waits_for_find_stations_then_uses_exact_coordinates(monkeypatch):
    with_suggestions(monkeypatch, {"trip_destination_ac": PICK})
    app, calls = make_app(monkeypatch, mode="google")
    first = len(calls)
    assert any("Selected: Orlando, FL, USA" in c.value for c in app.caption)
    assert any("You changed the trip" in c.value for c in app.caption)
    assert len(calls) == first, "a pick started a (paid) search"
    next(b for b in app.button if b.label == "Find stations").click().run()
    assert calls[-1][1] == (28.5383, -81.3792)  # Routes gets the exact point, not the words
    assert not app.exception


def test_the_navigation_link_carries_the_picked_places_ids(monkeypatch):
    with_suggestions(monkeypatch, {"trip_destination_ac": PICK})
    app, _ = make_app(monkeypatch, mode="google")
    next(b for b in app.button if b.label == "Find stations").click().run()
    nav = query(links(app)["Navigate via this station"].url)
    assert nav["destination"] == ["Orlando, FL, USA"] and nav["destination_place_id"] == ["ChIJorl"]


def test_a_hostile_picked_label_is_escaped_on_screen(monkeypatch):
    with_suggestions(monkeypatch, {"trip_destination_ac": {**PICK, "label": "**x**[a](http://e)"}})
    app, _ = make_app(monkeypatch, mode="google")
    assert any("Selected: \\*\\*x\\*\\*\\[a\\]\\(http://e\\)" in c.value for c in app.caption)


def test_when_suggestions_are_unavailable_the_text_boxes_come_back_with_a_warning(monkeypatch):
    with_suggestions(monkeypatch, {"trip_origin_ac": {"error_code": 1}})
    app, _ = make_app(monkeypatch, mode="google")
    assert any(places_autocomplete.MESSAGES["unavailable"] in w.value for w in app.warning)
    assert trip_boxes(app) == {"trip_origin_text", "trip_destination_text"}
    assert not app.exception


def test_without_a_browser_key_the_text_boxes_are_used(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_MAPS_JS_KEY", "")
    app, _ = make_app(monkeypatch, mode="google")
    assert trip_boxes(app) == {"trip_origin_text", "trip_destination_text"}


def test_when_the_cheapest_is_the_recommended_stop_it_is_one_card_not_two(monkeypatch):
    """The same station must not be announced twice: the green card says it is also the cheapest."""
    app, _ = make_app(monkeypatch, stations=[s for s in crafted() if s["id"] != "far"])
    assert not any("Cheapest gas on this route" in m.value for m in app.markdown)
    assert any("also the cheapest" in m.value.lower() for m in app.markdown)


def test_the_answer_is_not_repeated_in_the_metrics(monkeypatch):
    """The green card already names the station and the saving; the metric row adds the context
    the card cannot: what the same fill-up costs without detouring."""
    app, _ = make_app(monkeypatch)
    labels = {m.label for m in app.metric}
    assert "Gas price on your route" in labels
    assert "Best option" not in labels and "You save (after the detour)" not in labels


# --------------------------------------------------------------------- price is a bad proxy
INSIGHT = "Does cheapest always mean best?"


def all_text(app):
    return " ".join(c.value for c in app.caption) + " " + " ".join(m.value for m in app.markdown)


def test_the_regret_line_says_what_choosing_on_price_costs(monkeypatch):
    app, _ = make_app(monkeypatch, stations=crafted())
    assert any("you'd save" in c.value and "less" in c.value for c in app.caption)
    assert not app.exception


def versus_lines(app):
    return [c.value for c in app.caption if "Prices on your route:" in c.value]


def test_the_corridor_is_compared_with_the_florida_average_when_both_are_synthetic(monkeypatch):
    app, _ = make_app(monkeypatch, stations=crafted())
    lines = versus_lines(app)
    assert len(lines) == 1
    assert "Florida average (" in lines[0] and "synthetic" in lines[0]
    assert "the state" not in lines[0] and "np.float64" not in lines[0]


def test_real_stations_are_not_compared_with_a_synthetic_series(monkeypatch):
    """Google mode, but the series is the synthetic one (no EIA key): not comparable, no line."""
    app, _ = make_app(monkeypatch, mode="google", stations=crafted())
    assert versus_lines(app) == []
    assert not app.exception


def test_real_stations_are_compared_with_a_real_series(monkeypatch):
    import pandas as pd
    real = pd.DataFrame({"florida": [3.50, 3.60], "us": [3.4, 3.5]},
                        index=pd.to_datetime(["2026-11-02", "2026-11-09"]))
    app, _ = make_app(monkeypatch, mode="google", stations=crafted(),
                      series=(real, "EIA live"))
    lines = versus_lines(app)
    assert len(lines) == 1
    assert "2026-11-09" in lines[0] and "EIA live" in lines[0] and "$3.60/gal" in lines[0]


def test_an_empty_series_shows_no_comparison_instead_of_crashing(monkeypatch):
    import pandas as pd
    empty = pd.DataFrame({"florida": [], "us": []}, index=pd.to_datetime([]))
    app, _ = make_app(monkeypatch, stations=crafted(), series=(empty, "synthetic"))
    assert versus_lines(app) == []


def test_the_cheapest_question_expander_reports_a_share(monkeypatch):
    app, _ = make_app(monkeypatch, stations=crafted())
    found = [e for e in app.expander if e.label == INSIGHT]
    assert len(found) == 1
    text = " ".join(m.value for m in found[0].markdown) + " " + \
        " ".join(c.value for c in found[0].caption)
    assert "this route and these stations" in text
    assert "**15 of 20 scenarios**" in text and "(75%)" in text
    assert "ranks **#5**" in text
    assert "$5.48/h or more" in text
    assert "**$1.59**" in text and "**$5.43**" in text
    assert len(found[0].dataframe) == 1  # 4 x 5 grid of dollars lost


def test_when_no_detour_pays_off_the_regret_line_does_not_mention_a_recommended_stop(monkeypatch):
    def s(sid, price, off):
        return {"id": sid, "name": sid, "lat": 27.5, "lng": -82.5, "price": price,
                "off_route_m": off}
    stations = [s("r1", 3.60, 0), s("r2", 3.60, 100), s("r3", 3.60, 200), s("r4", 3.603, 0),
                s("far", 3.10, 8000)]
    app, _ = make_app(monkeypatch, stations=stations)
    assert not headline(app)
    caps = [c.value for c in app.caption]
    assert any("no detour pays off enough" in c for c in caps)
    assert not any("instead of the best one" in c for c in caps)


def test_the_panel_and_the_regret_line_agree_when_price_misleads(monkeypatch):
    app, _ = make_app(monkeypatch, stations=crafted())
    expander = [e for e in app.expander if e.label == INSIGHT][0]
    text = " ".join(m.value for m in expander.markdown)
    assert "ranks **#" in text and "ranks **#1**" not in text
    assert any("you'd save" in c.value and "less" in c.value for c in app.caption)
    assert not any("also the best choice" in c.value for c in app.caption)


def test_when_the_cheapest_is_also_the_best_the_insight_says_it_costs_nothing(monkeypatch):
    app, _ = make_app(monkeypatch, stations=[s for s in crafted() if s["id"] != "far"])
    assert any("also the best choice" in c.value for c in app.caption)
    assert not any("you'd save" in c.value and "less" in c.value for c in app.caption)
    expander = [e for e in app.expander if e.label == INSIGHT][0]
    assert "also first in the ranking" in " ".join(m.value for m in expander.markdown)


def test_no_priced_station_shows_no_insight_panel(monkeypatch):
    unpriced = [{"id": "x1", "name": "Unpriced", "lat": 27.5, "lng": -82.5, "price": None,
                 "off_route_m": 100.0}]
    app, _ = make_app(monkeypatch, stations=unpriced)
    assert not [e for e in app.expander if e.label == INSIGHT]
    assert not app.exception


def test_every_suggested_station_links_to_its_own_place(monkeypatch):
    app, _ = make_app(monkeypatch, mode="google")
    alt_links = [b.proto for b in app.get("link_button") if b.proto.label == "Open in Maps"]
    assert alt_links, "the other good options have no map link"
    ids = [query(b.url)["query_place_id"][0] for b in alt_links]
    assert all(not b.disabled for b in alt_links)
    assert len(set(ids)) == len(ids)
