"""Streamlit app: when and where to fill up, with detour break-even and a validated LLM
explanation. Runs without any key in fixture mode (synthetic data, clearly labelled)."""
import base64
import html
import time

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from envfile import load_dotenv, load_secrets

# Both run before `import config`, which reads the environment at import time. A real shell
# variable wins over .env, which wins over Streamlit secrets (how keys arrive on Streamlit Cloud).
load_dotenv()
load_secrets()

import config  # noqa: E402
import driver
import fuelplan
import insight
import location
import places_autocomplete
import maps_links
import recommend
import trip_input as ti
from detour import (DEFAULTS, breakeven_miles, rank_stations, reference_price,
                    sensitivity_table)
from forecast import lagged_corr, rolling_origin_eval, treat_series
from gmap import build_map_html
from llm import AnthropicProvider, GroqProvider, ask, explain
from providers import fueleconomy
from services import compute_detours, load_series, load_trip

FE_RETRY_S = 60  # seconds before the car lookup is tried again after fueleconomy.gov failed

st.set_page_config(page_title="Fuel Stop Planner", page_icon=":material/local_gas_station:", layout="wide")



def logo_svg(size):
    """The app's mark: a map pin with a fuel drop inside."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="{size}" '
           f'height="{size}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" '
           f'y2="1"><stop offset="0" stop-color="#9CD0FF"/><stop offset="1" '
           f'stop-color="#3B82F6"/></linearGradient></defs>'
           f'<rect width="32" height="32" rx="9" fill="url(#g)"/>'
           '<path d="M16 6.2c-4.1 0-7.2 3.1-7.2 7.1 0 5.2 7.2 12.5 7.2 12.5s7.2-7.3 7.2-12.5'
           'c0-4-3.1-7.1-7.2-7.1z" fill="#0F1115"/>'
           '<path d="M16 9.4c-1 1.4-2.7 3.4-2.7 4.8a2.7 2.7 0 0 0 5.4 0c0-1.4-1.7-3.4-2.7-4.8z" '
           'fill="#9CD0FF"/></svg>')
    # st.html strips inline <svg>, so the mark goes in as an image.
    data = base64.b64encode(svg.encode()).decode()
    return (f'<img class="fs-mark" src="data:image/svg+xml;base64,{data}" width="{size}" '
            f'height="{size}" alt="">')


head_left, head_right = st.columns([3, 2], vertical_alignment="bottom")
head_left.html('<div class="fs-hero">' + logo_svg(46) + '<div><h1 class="fs-title">Fuel Stop '
               'Planner</h1><p class="fs-sub">Compare fuel stops along your route, including the '
               'cost of the detour.</p></div></div>')
status_line = head_right.empty()  # filled once the trip is loaded

provider = config.PROVIDER
SYNTHETIC = provider == "fixture" or not (config.GOOGLE_API_KEY and config.EIA_API_KEY)

def _llm_provider():
    """Return the best available LLM provider: Groq (free) → Anthropic → None (template).

    Groq is preferred because it has a generous free tier and needs no credit card.
    Set GROQ_API_KEY in .env to enable it; ANTHROPIC_API_KEY works as a fallback.
    Without either key the app uses the deterministic template and says so.
    """
    g = GroqProvider()
    if g.available():
        return g
    a = AnthropicProvider()
    if a.available():
        return a
    return None


@st.cache_data(show_spinner=False, ttl=86400)
def fe_menu(kind, *args):
    return getattr(fueleconomy, kind)(*args)


@st.cache_data(show_spinner=False, ttl=86400)
def fe_vehicle(vehicle_id):
    return fueleconomy.vehicle(vehicle_id)


def car_lookup():
    """fueleconomy.gov cascade year > make > model > version. Returns the vehicle or None."""
    def pick(label, key, options, fmt=str):
        return st.selectbox(label, [None] + list(options), key=key,
                            format_func=lambda v: "Select..." if v is None else fmt(v))

    year = pick("Year", "car_year", fe_menu("years"))
    make = pick("Make", "car_make", fe_menu("makes", year) if year else [])
    model = pick("Model", "car_model", fe_menu("models", year, make) if make else [])
    versions = dict(fe_menu("options", year, make, model)) if model else {}
    label = pick("Version", "car_version", versions)
    return fe_vehicle(versions[label]) if label else None


def slider_with_number(label, lo, hi, default, step, key, help=None, disabled: bool = False):
    """A slider plus a box to type the exact value. The slider's own session-state `key` is the
    value everyone reads; the box (`{key}_num`) mirrors it and, when typed into, writes back to
    it (clamped to the slider's range). Setting either from elsewhere, e.g. the car lookup
    writing `mpg`, shows up in both."""
    num_key = f"{key}_num"
    st.session_state.setdefault(key, float(default))

    def typed():
        v = st.session_state.get(num_key)
        if v is not None:
            st.session_state[key] = min(hi, max(lo, float(v)))

    st.markdown(f':small[{label.replace("$", "\\$")}]', help=help)
    left, right = st.columns([3, 2], vertical_alignment="center")
    with left:
        value = st.slider(label, lo, hi, step=step, key=key, help=help,
                          label_visibility="collapsed", disabled=disabled)
    st.session_state[num_key] = value
    with right:
        st.number_input(f"{label}: exact value", lo, hi, step=step, key=num_key,
                        on_change=typed, label_visibility="collapsed", disabled=disabled)
    return value


st.session_state.setdefault("mpg", DEFAULTS["mpg"])
GAUGE_LEVELS = ["E", "1/8", "1/4", "3/8", "1/2", "5/8", "3/4", "7/8", "F"]


def card_header(title, description, icon=None):
    """Every card opens the same way: a bold title, then one line on what it is for."""
    st.subheader(f"{icon} {title}" if icon else title, anchor=False)
    st.caption(description)

# "Your trip": where from and to, and how wide to search. What is typed, picked or located is only
# "pending": a paid search starts when "Find stations" is pressed, never on a keystroke or a pick.
def address_input(name, label, default_text):
    """An address box with Google's suggestions when the browser key is set (and they work), else a
    plain text box. Returns the pending place, or None if the text box is empty."""
    key = f"trip_{name}"
    if config.GOOGLE_MAPS_JS_KEY and not st.session_state.get("suggestions_off"):
        st.markdown(label)
        parsed = places_autocomplete.parse_pick(places_autocomplete.request_pick(
            f"{key}_ac", config.GOOGLE_MAPS_JS_KEY, "Type an address"))
        if parsed and "coords" in parsed:
            st.session_state[f"{key}_pick"] = ti.pick_place(parsed)
        elif parsed:  # unavailable here (e.g. the key lacks the Places API): use plain text boxes
            st.session_state["suggestions_off"] = True
            st.session_state["suggestions_error"] = parsed["error"]
            st.rerun()
        picked = st.session_state.get(f"{key}_pick")
        st.caption(f"Selected: {recommend.md_escape(picked['label'])}" if picked
                   else f"Selected: {default_text} (type above and pick a suggestion to change)")
        return picked or ti.text_place(default_text)
    return ti.text_place(st.text_input(label, default_text, key=f"{key}_text"))


origin = destination = None
# The choices (popovers) sit on the left and a summary of them on the right, so the answer
# starts near the top of the page instead of below two tall cards.
trip_strip = st.container(border=True, key="trip_strip")
trip_strip.html('<p class="fs-step"><b>Start here</b> · Tell the app where you are going and pick '
                'your car. ' + ("Press Find stations to search." if provider == "google"
                                and config.GOOGLE_API_KEY else
                                "The recommendation below updates on its own.") + '</p>')
strip_left, strip_right = trip_strip.columns([2, 3], gap="large")
strip_left.html('<p class="fs-panel-label">Your choices</p>')
choices = strip_left.container(key="trip_choices", gap="small")
strip_right.html('<p class="fs-panel-label">Summary</p>')
trip_chips = strip_right.empty()  # filled once the trip and the car are known
with choices.popover("Where are you going?", icon=":material/edit_location_alt:",
                     width="stretch"):
    corridor_help = ("How far from your route a station can be and still be listed. For example, "
                     "3 km each side searches a 6 km wide strip along the route.")
    if provider == "google" and config.GOOGLE_API_KEY:
        applied = st.session_state.setdefault("trip_applied", dict(ti.DEFAULT))
        mine = st.session_state.get("my_location")
        if mine:
            accuracy = st.session_state.get("my_location_accuracy")
            detail = f" (about {accuracy} m accuracy)" if accuracy else ""
            st.caption(f"Using your current location{detail} · used for this route only")
            if st.button("Use the typed address instead", type="tertiary"):
                st.session_state["my_location"] = None
                st.rerun()
        else:
            fix = location.parse_fix(location.request_location("geo_button"))
            if fix and "coords" in fix:
                st.session_state["my_location"] = fix["coords"]
                st.session_state["my_location_accuracy"] = fix["accuracy"]
                st.session_state["my_location_error"] = None
                st.rerun()
            elif fix:
                st.session_state["my_location_error"] = fix["error"]
            if st.session_state.get("my_location_error"):
                st.warning(st.session_state["my_location_error"])
        if st.session_state.get("suggestions_error"):
            st.warning(st.session_state["suggestions_error"])
        origin_place = ti.location_place(mine) if mine else address_input(
            "origin", "Origin", ti.DEFAULT["origin"]["label"])
        destination_place = address_input("destination", "Destination",
                                          ti.DEFAULT["destination"]["label"])
        with st.popover("Search settings", icon=":material/tune:"):
            corridor = st.number_input(
                "Search corridor width (km each side)", 1.0, 10.0, 3.0, 0.5,
                key="buffer_km", help=corridor_help)
        if origin_place is None or destination_place is None:
            st.warning("The origin and the destination cannot be empty.")
        else:
            pending = {"origin": origin_place, "destination": destination_place,
                       "corridor": corridor}
            pressed = st.button("Find stations", type="primary", icon=":material/search:")
            if pressed:
                applied = st.session_state["trip_applied"] = pending
            elif ti.changed(pending, applied):
                st.caption("You changed the trip. Press Find stations to search with the new "
                           "origin, destination or corridor.")
        origin = ti.route_point(applied["origin"])
        destination = ti.route_point(applied["destination"])
        buffer_km = applied["corridor"]
    else:
        st.caption("Synthetic test mode: the route is always Sarasota to Orlando. Typing your own "
                   "trip and using your current location need PROVIDER=google (live route data).")
        with st.popover("Search settings", icon=":material/tune:"):
            buffer_km = st.number_input(
                "Search corridor width (km each side)", 1.0, 10.0, 3.0, 0.5,
                key="buffer_km", help=corridor_help)
with choices.popover("Pick your car", icon=":material/directions_car:", width="stretch"):
    st.markdown("**Look up my car**")
    car = None
    if time.time() < st.session_state.get("fe_down_until", 0.0):
        # After a failure, do not retry on every rerun (each slider move would wait for the
        # timeout again); the lookup is tried again after FE_RETRY_S.
        st.caption("fueleconomy.gov is not reachable: set the mpg by hand below. The app "
                   "tries again in about a minute.")
    else:
        try:
            car = car_lookup()
        except requests.RequestException:  # includes unusable responses (FuelEconomyError)
            st.session_state["fe_down_until"] = time.time() + FE_RETRY_S
            st.caption("fueleconomy.gov is not reachable: set the mpg by hand below. The "
                       "app tries again in about a minute.")
    if car and fueleconomy.is_electric(car):
        st.warning("Electric car: EPA reports MPGe, not gallons. Set mpg by hand if you use "
                   "a gas range extender or hybrid figures.")
    elif car:
        if fueleconomy.has_electric_part(car):
            st.warning("Plug-in hybrid: the EPA figures blend electricity (MPGe) with "
                       "gasoline, so they overstate gasoline-only mpg. Check the mpg by hand.")
        kind = st.segmented_control(
            "Which EPA figure", fueleconomy.KINDS, default="combined", key="epa_kind",
            help="city: stop-and-go (closest to delivery driving). highway: steady speed. "
                 "combined: 55% city / 45% highway.") or "combined"
        looked_up = fueleconomy.mpg_for(car, kind)
        if looked_up and st.session_state.get("mpg_applied") != (car["id"], kind):
            st.session_state["mpg"] = min(60.0, max(10.0, round(looked_up * 2) / 2))
            st.session_state["mpg_applied"] = (car["id"], kind)
        if car.get("vclass") and st.session_state.get("tank_applied") != car["id"]:
            st.session_state["tank"] = fuelplan.tank_for_class(car["vclass"])
            st.session_state["tank_applied"] = car["id"]
        st.caption(f"{car['label']}: city {car['city']}, combined {car['combined']}, "
                   f"highway {car['highway']} mpg (EPA label values; real mileage varies).")
    mpg = slider_with_number("Fuel economy (mpg)", 10.0, 60.0, DEFAULTS["mpg"], 0.5, "mpg")
    st.session_state.setdefault("tank", 13.0)
    tank = st.number_input("Tank size (gallons)", min_value=5.0, max_value=40.0, step=0.5,
                           key="tank")
    if car and car.get("vclass") and st.session_state.get("tank_applied") == car["id"]:
        st.caption(f"Estimated from the EPA size class ({car['vclass']}); check your manual.")
    st.caption(f"About {tank * mpg:.0f} miles on a full tank at this mpg.")
    fuel_grade = st.segmented_control(
        "Fuel grade", ["Regular", "Midgrade", "Premium", "Diesel"],
        default="Regular", key="fuel_grade",
        help="Filters station prices to this grade. Regular = 87 octane.")
with choices.popover("How much fuel do you have?", icon=":material/local_gas_station:",
                     width="stretch"):
    fuel_input_mode = st.segmented_control(
        "How do you know?", ["Fuel gauge", "Miles left"], key="fuel_input_mode")
    fuel_mode = None
    fuel_value = None
    if fuel_input_mode == "Fuel gauge":
        st.session_state.setdefault("fuel_level_eighths", "1/2")
        gauge_label = st.select_slider("Fuel gauge", options=GAUGE_LEVELS,
                                       key="fuel_level_eighths")
        fuel_mode, fuel_value = "gauge", GAUGE_LEVELS.index(gauge_label)
    elif fuel_input_mode == "Miles left":
        st.session_state.setdefault("fuel_miles_left", 50.0)
        fuel_value = st.number_input("Miles until the reserve light", min_value=0.0,
                                     max_value=900.0, step=5.0, key="fuel_miles_left")
        fuel_mode = "miles"
    else:
        st.caption("Optional. Tell the app how much fuel you have and it can plan how much "
                   "to buy.")


def chip(label, value, kind=""):
    return (f'<div class="fs-chip {kind}"><small>{html.escape(label)}</small>'
            f'<b>{html.escape(value)}</b></div>')


_trip_labels = (st.session_state.get("trip_applied", ti.DEFAULT)
                if provider == "google" and config.GOOGLE_API_KEY else ti.DEFAULT)
trip_chips.html('<div class="fs-chips">'
                + chip("Route", f'{_trip_labels["origin"]["label"]} → '
                                f'{_trip_labels["destination"]["label"]}', "wide")
                + chip("Car", f"{mpg:.1f} mpg")
                + chip("Tank", f"{tank:g} gal")
                + chip("Range", f"about {tank * mpg:.0f} mi")
                + chip("Fuel now", "Not set" if fuel_mode is None else
                       f"{gauge_label} tank" if fuel_mode == "gauge" else
                       f"{fuel_value:.0f} mi to reserve", "muted" if fuel_mode is None else "")
                + chip("Fuel grade", fuel_grade or "Regular")
                + chip("Corridor", f"{buffer_km:g} km each side") + "</div>")

_NAV = [
    ":material/local_gas_station: Where to stop",
    ":material/calendar_month: When to fill up",
    ":material/calculate: Sensitivity",
    ":material/analytics: EDA",
    ":material/help: How to use",
]


def _nav_click(item):
    st.session_state["main_tabs"] = item


_SIDEBAR_CSS = """<style>
html {font-variant-numeric: tabular-nums;}
h1, h2, h3 {letter-spacing: -0.012em;}
.fs-brand {display: flex; align-items: center; gap: 0.7rem; margin: 0;}
.fs-brand b {display: block; font-family: "Space Grotesk", sans-serif; font-weight: 700;
    font-size: 1.05rem; letter-spacing: 0.28em; line-height: 1.1;}
.fs-brand small {display: block; font-weight: 600; font-size: 0.58rem; letter-spacing: 0.24em;
    opacity: 0.55; margin-top: 0.2rem;}
.fs-hero {display: flex; align-items: center; gap: 1rem; margin: 0.25rem 0 0.2rem 0;}
.fs-mark {flex-shrink: 0; filter: drop-shadow(0 6px 18px rgba(59, 130, 246, 0.35));}
.fs-title {font-family: "Space Grotesk", sans-serif; font-size: 2.1rem; font-weight: 700;
    letter-spacing: -0.02em; line-height: 1.1; margin: 0; padding: 0;}
.fs-sub {margin: 0.35rem 0 0 0; opacity: 0.7; font-size: 0.95rem;}
.fs-panel-label {font-size: 0.66rem; font-weight: 600; letter-spacing: 0.18em;
    text-transform: uppercase; opacity: 0.55; margin: 0.35rem 0 -0.35rem 0;}
.st-key-trip_choices button {min-height: 3.55rem; border-radius: 0.7rem !important;
    background: linear-gradient(180deg, #1B1F26, #16191F) !important;
    padding: 0.5rem 1rem !important;}
.st-key-trip_choices button > div {justify-content: flex-start !important; width: 100%;
    gap: 0.6rem; font-weight: 500;}
.st-key-trip_choices button > div > div:first-child {justify-content: flex-start !important;}
.st-key-trip_choices button:hover {border-color: #6CB4FF !important;}
.fs-chips {display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.55rem;}
@media (max-width: 640px) {.fs-chips {grid-template-columns: repeat(2, minmax(0, 1fr));}}
.fs-chip {background: linear-gradient(180deg, #1B1F26, #16191F); border: 1px solid #2B3039;
    border-left: 3px solid #6CB4FF; border-radius: 0.7rem; padding: 0.6rem 0.85rem; min-width: 0;}
.fs-chip.wide {grid-column: 1 / -1;}
.fs-chip.muted {border-left-color: #3A404B;}
.fs-chip.muted b {opacity: 0.55; font-weight: 500;}
.fs-chip small {display: block; font-size: 0.64rem; font-weight: 600; letter-spacing: 0.12em;
    text-transform: uppercase; opacity: 0.55; margin-bottom: 0.2rem;}
.fs-chip b {display: block; font-size: 0.98rem; font-weight: 600; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;}
.fs-step {margin: 0 0 0.1rem 0; font-size: 0.9rem; opacity: 0.85;}
.fs-step b {color: #9CCBFF; font-weight: 600;}
.fs-kicker {font-size: 0.72rem; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase;
    color: #5BD48A; margin: 0;}
.st-key-answer_go {background: linear-gradient(160deg, rgba(91, 212, 138, 0.13),
    rgba(30, 34, 41, 0) 55%); border-color: rgba(91, 212, 138, 0.35) !important;}
.fs-status {font-size: 0.68rem; font-weight: 600; letter-spacing: 0.16em;
    text-transform: uppercase; opacity: 0.6; margin: 0 0 0.45rem 0; text-align: right;}
/* In the header strip, left of Streamlit's own buttons: it never covers the page content. */
.st-key-assistant_fab {position: fixed; top: 0.55rem; right: 9rem; z-index: 1000000;
    width: auto !important;}
.st-key-assistant_fab button {border-radius: 999px !important; background: #6CB4FF !important;
    color: #0F1115 !important; border: none !important; font-weight: 600;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35); padding: 0.35rem 1rem !important;}
.fs-label {font-size: 0.66rem; font-weight: 600; letter-spacing: 0.18em; text-transform: uppercase;
    opacity: 0.55; margin: 0.9rem 0 -0.4rem 0;}
.st-key-nav_container button {border: 1px solid transparent !important;
    background: transparent !important; justify-content: flex-start !important;
    opacity: 0.72; font-weight: 500;}
.st-key-nav_container button > div {justify-content: flex-start !important; width: 100%;
    gap: 0.6rem;}
.st-key-nav_container button:hover {background: rgba(255,255,255,0.05) !important; opacity: 1;}
.st-key-ACTIVE button {background: rgba(108,180,255,0.12) !important; opacity: 1;
    border-color: rgba(108,180,255,0.35) !important; color: #9CCBFF !important;}
</style>"""


def sidebar_label(text):
    st.html(f'<div class="fs-label">{text}</div>')


with st.sidebar:
    if st.session_state.get("main_tabs") not in _NAV:  # e.g. a page that was removed
        st.session_state["main_tabs"] = _NAV[0]
    _current_nav = st.session_state["main_tabs"]
    _active_key = "_nav_" + _current_nav.split(": ", 1)[1].replace(" ", "_")
    st.html(_SIDEBAR_CSS.replace("ACTIVE", _active_key))
    st.html('<div class="fs-brand">' + logo_svg(34)
            + '<div><b>FUEL STOP</b><small>ROUTE PLANNER</small></div></div>')

    sidebar_label("Navigate")
    with st.container(key="nav_container", gap=None):
        for _item in _NAV:
            _icon_part, _label_part = _item.split(" ", 1)
            st.button(
                _label_part,
                icon=_icon_part,
                key=f"_nav_{_label_part.replace(' ', '_')}",
                width="stretch",
                type="secondary",
                on_click=_nav_click,
                args=(_item,),
            )

    sidebar_label("Fill-up")
    # The gallons slider is drawn into this slot after the shared computation: with a fuel level
    # set, the app chooses the gallons, and a widget's value cannot change once it is drawn.
    fill_slot = st.container()
    with fill_slot:
        gallons_by_hand = True
        if fuel_mode is not None:
            gallons_by_hand = st.toggle("Set gallons by hand", key="gallons_manual",
                                        help="Off: the app works out how much to buy from your "
                                             "fuel level.")

    sidebar_label("Time")
    vot = slider_with_number(
        "Value of your time ($/h)", 0.0, 60.0, DEFAULTS["value_of_time"], 1.0, "vot",
        help="What an hour of your time is worth to you; the app charges detour minutes at this "
             "rate. For example, at $20/h a 10 minute detour costs about $3.33.")
    speed = slider_with_number(
        "Average speed on detours (mph)", 20.0, 70.0, DEFAULTS["speed_mph"], 1.0, "speed",
        help="Used to turn detour miles into detour minutes. For example, 2 extra miles at 30 "
             "mph is 4 minutes.")

    sidebar_label("Data")
    if SYNTHETIC:
        st.markdown(":orange-badge[:material/science: Synthetic test data]")
        st.caption("Prices, stations and the route are made up. Set PROVIDER=google plus "
                   "EIA_API_KEY and GOOGLE_API_KEY for live data.")
    else:
        st.markdown(":green-badge[:material/cloud_done: Live data]")
        st.caption("Google Places and Routes for stations, EIA for weekly prices.")


@st.cache_data(show_spinner="Loading prices...", ttl=3600)
def cached_series(p):
    return load_series(p)


_FUEL_TYPE = {
    "Regular": "REGULAR_UNLEADED",
    "Midgrade": "MIDGRADE",
    "Premium": "PREMIUM",
    "Diesel": "DIESEL",
}


@st.cache_data(show_spinner="Finding stations...", ttl=600)
def cached_trip(p, o, d, buf, ft="REGULAR_UNLEADED"):
    return load_trip(o, d, buf, provider=p, fuel_type=ft)


series, series_label = cached_series(provider)
_ft = _FUEL_TYPE.get(st.session_state.get("fuel_grade", "Regular"), "REGULAR_UNLEADED")
try:
    trip = cached_trip(provider, origin, destination, buffer_km * 1000, _ft)
except Exception as e:  # network or key problems should not crash the demo
    st.error(f"Could not load the trip ({e}). Falling back to fixture data.")
    trip = cached_trip("fixture", None, None, buffer_km * 1000, _ft)



def minimap(path, me=None, best=None, height=190):
    """A small, static map of the route. `me` (the browser location, session only) and `best`
    (the recommended stop) each add a dot."""
    path = np.array(path)
    fig = go.Figure(go.Scattermap(lat=path[:, 0], lon=path[:, 1], mode="lines",
                                  line=dict(width=3, color="#9CCBFF"), hoverinfo="skip"))
    fig.add_trace(go.Scattermap(lat=[path[0, 0], path[-1, 0]], lon=[path[0, 1], path[-1, 1]],
                                mode="markers", marker=dict(size=9, color="#E6E9EF"),
                                hoverinfo="skip"))
    if me:
        fig.add_trace(go.Scattermap(lat=[me[0]], lon=[me[1]], mode="markers",
                                    marker=dict(size=13, color="#E6E9EF"), hoverinfo="skip"))
    if best:
        fig.add_trace(go.Scattermap(lat=[best["lat"]], lon=[best["lng"]], mode="markers",
                                    marker=dict(size=16, color="#6CB4FF"), hoverinfo="skip"))
    span = max(np.ptp(path[:, 0]), np.ptp(path[:, 1]), 0.05)
    zoom = float(np.clip(np.log2(360 / span) - 2.4, 3, 13))
    fig.update_layout(map=dict(style="carto-darkmatter", zoom=zoom,
                               center=dict(lat=float(path[:, 0].mean()),
                                           lon=float(path[:, 1].mean()))),
                      height=height, margin=dict(l=0, r=0, t=0, b=0), showlegend=False)
    return fig


for w in trip["warnings"]:
    st.info(w)

# Shared computation — runs every rerun so all sections can read priced, ranked, etc.
_stations = trip["stations"]
_edit_df = st.session_state.get("_price_df")  # set by data_editor on "Where to stop"
_price_by_id = (dict(zip(_edit_df["id"], _edit_df["price"])) if _edit_df is not None
                else {s["id"]: s.get("price") for s in _stations})
_min_saving_val = float(st.session_state.get("min_saving", recommend.MIN_SAVING_DEFAULT))
priced = [{**s, "price": (None if pd.isna(_price_by_id.get(s["id"])) else
                          float(_price_by_id[s["id"]]))} for s in _stations]
p_ref = reference_price(priced)
if p_ref is None:
    p_ref = float(st.session_state.get("p_ref_manual", 3.6))
detoured = compute_detours({**trip, "stations": priced})
tmiles = fuelplan.trip_miles(trip["path"]) if trip.get("path") else 0.0
_gallons_slider = float(st.session_state.get("gallons", DEFAULTS["gallons"]))
# With a fuel level set, the plan always runs: unreachable stations are dropped before ranking
# (never ranked with 0 gallons), and it decides stop / no stop. Each station gets its own gallons
# from the plan, unless "Set gallons by hand" is on: then the slider's one number is used. The
# headline, the options and the insight panel all read the same `detoured` and `gallons_arg`.
fplan = None
gallons_arg = _gallons_slider
if fuel_mode is not None and tmiles > 0:
    _reserve = driver.reserve_gallons(tank, "standard")
    _fuel_gal = fuelplan.fuel_on_board(fuel_mode, fuel_value, tank, mpg, _reserve)
    fplan = fuelplan.plan(detoured, trip["path"], _fuel_gal, tank, mpg, _reserve, p_ref)
    _reach = {sid: v["gallons"] for sid, v in fplan["per_station"].items() if v["reachable"]}
    detoured = [s for s in detoured if s["id"] in _reach]
    if not gallons_by_hand:
        gallons_arg = _reach
ranked = rank_stations(detoured, p_ref, gallons_arg, mpg, vot, speed)
rec = recommend.recommend(ranked, _min_saving_val,
                          stop_needed=None if fplan is None else fplan["stop_needed"])
# The single gallons figure the rest of the app reads (Sensitivity, flip point, AI facts, slider):
# under a plan, the best-ranked station's own gallons for every verdict, so the facts and the
# flip sentence describe the same fill as its net benefit; the slider only when nothing ranks.
_app_chose = fplan is not None and bool(ranked) and not gallons_by_hand
gallons_used = float(ranked[0]["gallons"]) if _app_chose else _gallons_slider
# Under a plan with a stop needed, a skip names the reachable station reached first along the
# route (most fuel left on arrival there; ties: the shorter detour).
_nearest = (min(ranked, key=lambda s: (-fplan["per_station"][s["id"]]["fuel_at_station"],
                                       s["extra_miles"]))
            if fplan is not None and fplan["stop_needed"] and ranked else None)
# Under a plan, every priced station unreachable before the reserve (stop needed or not).
_none_reachable = bool(fplan is not None and fplan["per_station"]
                       and not any(v["reachable"] for v in fplan["per_station"].values()))
with fill_slot:
    if _app_chose:
        st.session_state["gallons"] = min(25.0, max(3.0, gallons_used))
        st.session_state["gallons_num"] = st.session_state["gallons"]
    slider_with_number(
        "Gallons to buy", 3.0, 25.0, DEFAULTS["gallons"], 0.5, "gallons",
        help="How much fuel you plan to put in at the stop. Bigger fills make a price gap worth "
             "more. For example, a 10 cent/gal gap on 12 gallons saves $1.20 before the detour "
             "costs.", disabled=not gallons_by_hand)
    if _app_chose and not 3.0 <= gallons_used <= 25.0:
        st.caption(f"App's choice: {gallons_used:.1f} gal")
live = trip["mode"] == "google"
best_id = rec["best"]["id"] if rec["verdict"] == "go" else None
cheap = recommend.cheapest(ranked)
cheap_is_best = bool(cheap and best_id and cheap["id"] == best_id)

_applied = st.session_state.get("trip_applied", ti.DEFAULT) if live else ti.DEFAULT
_route = f'{_applied["origin"]["label"]} → {_applied["destination"]["label"]}'
_dest = _applied["destination"]["label"] or "your destination"
_n_priced = sum(1 for s in priced if s.get("price") is not None)
_status = [f"{len(_stations)} stations", f"{_n_priced} priced",
           st.session_state.get("fuel_grade") or "Regular",
           "synthetic data" if SYNTHETIC else "live data"]
status_line.html('<p class="fs-status">' + " · ".join(html.escape(x) for x in _status) + "</p>")

_selected = st.session_state.get("main_tabs", _NAV[0])

# ---------------------------------------------------------------- how to use
if _selected == ":material/help: How to use":
    st.subheader("How to use this app")
    st.markdown(
        "**Start here:** the strip at the top of the page has three buttons. *Where are you "
        "going?* sets the route, *Pick your car* sets your mpg, tank and fuel grade, and *How "
        "much fuel do you have?* (fuel gauge or miles to the reserve light) lets the app tell "
        "you whether you need to stop at all and how many gallons to buy. The sidebar holds the "
        "fill-up and time settings (gallons, value of your time, speed). Hover the small (?) "
        "next to a control for a tip. Every page reads those values.\n\n"
        "**Which tab should I open?**\n"
        "- *I am on a trip and want the best station on the way* → **Where to stop**. Edit any "
        "price in the table (typed prices always work). The app ranks stations by net benefit: "
        "fuel saved minus the extra fuel and time of the detour. With your fuel level set, it "
        "says *No stop needed* or which reachable station to use and how many gallons to buy.\n"
        "- *Is this a good week to fill up?* → **When to fill up**: weekly Florida/U.S. prices "
        "and a check of whether any simple forecast beats 'next week = this week'.\n"
        "- *How much is a detour worth?* → **Sensitivity**: extra miles a detour is worth for "
        "each price gap.\n"
        "- *Where does the price data come from and is it clean?* → **EDA**: missing-data and "
        "outlier treatment (before/after), distributions and correlations of the EIA series.\n"
        "- *Explain it in plain words* → the **AI assistant** button at the top right, "
        "on every page: a plain-language explanation and a question box. Every number the model writes is checked against the calculation; if it cannot "
        "be traced, a template is used.\n\n"
        "Data: EIA Open Data API v2 (weekly retail gasoline, JSON) and Google Places/Routes "
        "(stations, route). Without keys the app runs on **synthetic** data and says so. "
        "Not financial advice.")
    with st.expander("Glossary"):
        st.markdown(
            "- **You save**: the net dollars you gain by stopping at a cheaper station, "
            "after the extra fuel and time of the detour are subtracted. Positive = worth it.\n"
            "- **Gas price on your route**: the typical price at stations along your direct "
            "route (no detour). Used as the baseline for comparing stations.\n"
            "- **Value of time**: how much an hour of your time is worth to you. The app uses "
            "this to put a dollar cost on detour minutes.\n"
            "- **Naive forecast** (When to fill up tab): 'next week's price = this week's "
            "price'. A forecast is only useful if it beats this simple guess.\n"
            "- **MPGe**: the electric-car equivalent of mpg; not gallons.")

# ---------------------------------------------------------------- where to stop
MAPS_HOME = "https://www.google.com/maps"


def maps_buttons(best, live):
    """'Open in Google Maps' and 'Navigate via this station'. In the synthetic mode the stations
    are made up, so the buttons stay off instead of pointing at places that do not exist."""
    pin = nav = None
    if live:
        try:
            pin = maps_links.pin_url(best["lat"], best["lng"], best["id"])
            applied_trip = st.session_state.get("trip_applied", ti.DEFAULT)
            o_pt, o_id = ti.link_point(applied_trip["origin"])
            d_pt, d_id = ti.link_point(applied_trip["destination"])
            nav = maps_links.directions_url(o_pt, d_pt, (best["lat"], best["lng"]), best["id"],
                                            origin_place_id=o_id, destination_place_id=d_id)
        except ValueError:  # e.g. an empty destination: leave that button off
            pass
    with st.container(horizontal=True):
        st.link_button("Open in Google Maps", pin or MAPS_HOME, icon=":material/location_on:",
                       disabled=pin is None, key="open_pin")
        st.link_button("Navigate via this station", nav or MAPS_HOME,
                       icon=":material/directions:", type="primary", disabled=nav is None,
                       key="open_nav")
    if not live:
        st.caption("These buttons open Google Maps at the real station. They are off in the "
                   "synthetic test mode because its stations are made up.")


if _selected == ":material/local_gas_station: Where to stop":
    answer = st.container()  # the answer, filled below once the prices are known
    with st.expander("Edit prices and settings", icon=":material/tune:"):
        st.number_input(
            "Only recommend a stop that saves at least ($)", 0.0, 20.0,
            recommend.MIN_SAVING_DEFAULT, 0.25, key="min_saving",
            help="Below this saving the app tells you to fill up at the first station on your "
                 "route. The default of $0.50 is an assumption: a smaller saving is not worth "
                 "the stop.")
        st.markdown("**Station prices** (typed prices always work)")
        table = pd.DataFrame([{"id": s["id"], "name": s["name"], "price": s["price"],
                                "status": "" if s["price"] is not None else "⚠ no price"}
                              for s in _stations], columns=["id", "name", "price", "status"])
        table = table.sort_values("status", ascending=False).reset_index(drop=True)
        _edited = st.data_editor(table, disabled=["id", "name", "status"], hide_index=True,
                                column_order=["status", "name", "price"], width="stretch",
                                key="price_editor",
                                column_config={
                                    "status": st.column_config.TextColumn("", width="small"),
                                    "price": st.column_config.NumberColumn(
                                        "Price $/gal", min_value=1.0, max_value=9.0,
                                        step=0.01, format="$%.2f"),
                                },
                                alt="Station prices; edit the price column to change the ranking")
        st.session_state["_price_df"] = _edited
        if reference_price(priced) is None:
            st.number_input("No priced station is on the route: type the price you would "
                            "pay on the route ($/gal)", 2.0, 6.0, 3.6, 0.01,
                            key="p_ref_manual")

    with answer:
        has_map = bool(trip.get("path"))
        hero_left, hero_right = (st.columns([1.55, 1], vertical_alignment="top") if has_map
                                 else (st.container(), None))
        with hero_left:
            if rec["verdict"] == "go" and _stations:
                b = rec["best"]
                title, _ = recommend.summary(rec)
                with st.container(border=True, key="answer_go", height="stretch"):
                    st.html('<p class="fs-kicker">Recommended stop</p>')
                    st.header(title, anchor=False)
                    st.markdown("Saves you the most after the detour's extra fuel and time."
                                + (" It is also the cheapest gas on this route."
                                   if cheap_is_best else ""))
                    tiles = ([("Buy", f"{b['gallons']:.1f} gal",
                               "Worked out from your fuel level: fill up when this station is "
                               "cheaper than the route, else just enough to finish the trip "
                               "with your reserve.")] if fplan is not None else [])
                    tiles += [("Price", f"${b['price']:.2f}/gal", None),
                              ("You save", f"${b['net_benefit']:.2f}",
                               "After the extra fuel and your time on the detour."),
                              ("Detour", f"{b['extra_miles']:.1f} mi", None),
                              ("Extra time", recommend.minutes_label(b["extra_minutes"]), None)]
                    # Even rows (2 + 2, or 3 + 2) instead of a row that wraps to 3 + 1.
                    split = 2 if len(tiles) == 4 else 3
                    for row in (tiles[:split], tiles[split:]):
                        for col, (label, value, hint) in zip(st.columns(len(row)), row):
                            col.metric(label, value, border=True, help=hint)
                    if fplan is not None and fplan["per_station"][b["id"]]["short"]:
                        st.warning("This trip needs more than one tank: plan another stop "
                                   f"before {recommend.md_escape(_dest)}.")
                    maps_buttons(b, live)
            elif rec["verdict"] == "no_stop":
                title, body = recommend.summary(rec)
                arrive = fplan["arrive_gal"]
                with st.container(border=True, key="answer_go", height="stretch"):
                    st.html('<p class="fs-kicker">Your fuel</p>')
                    st.header(title, anchor=False)
                    st.markdown(f"You reach {recommend.md_escape(_dest)} with about "
                                f"{arrive:.1f} gal left ({arrive * mpg:.0f} mi).")
                    if body:
                        st.caption(body)
            else:
                with st.container(border=True, height="stretch"):
                    card_header("Recommended stop", "The station that saves you the most after "
                                "the detour's extra fuel and time.", ":material/verified:")
                    if not _stations:
                        st.warning("No stations were listed within the search corridor. Widen "
                                   "the corridor under Where are you going? and press Find stations.",
                                   title="No stations found near this route")
                    elif (fplan is not None and fplan["stop_needed"] and fplan["per_station"]
                          and not any(v["reachable"] for v in fplan["per_station"].values())):
                        # per_station holds priced stations only: with none priced, the
                        # "No station has a price yet" prompt below is the true answer.
                        st.warning("None of the listed stations is reachable before your "
                                   "reserve: fill up at the first station you pass.")
                    else:
                        title, body = recommend.summary(rec, nearest=_nearest)
                        if rec["verdict"] == "skip":
                            st.info(body, title=title, icon=":material/info:")
                        else:
                            st.warning(body, title=title)
        if hero_right is not None:
            with hero_right, st.container(border=True, height="stretch"):
                me = st.session_state.get("my_location") if live else None
                st.plotly_chart(minimap(trip["path"], me, rec["best"] if best_id else None,
                                        height=300),
                                width="stretch", key="hero_minimap",
                                config={"displayModeBar": False, "staticPlot": True},
                                alt="Small map of the route" + (" and the recommended stop"
                                                                if best_id else ""))
                st.caption(html.escape(_route) + (" · blue dot: recommended stop" if best_id
                                                  else ""))
        if fplan is not None and fplan["tank_too_small"]:
            st.caption(f"Your tank looks bigger than {tank:g} gal: check Tank size under "
                       "Pick your car.")
        if cheap and not cheap_is_best:  # a cheaper station that the detour may not justify
            with st.container(border=True):
                headline, detail = recommend.describe_cheapest(cheap, p_ref, best_id)
                st.markdown(f"**{headline}**")
                st.caption(detail)
                if live and cheap["id"] != best_id:
                    try:
                        st.link_button("Open cheapest in Google Maps",
                                       maps_links.pin_url(cheap["lat"], cheap["lng"], cheap["id"]),
                                       icon=":material/location_on:", key="open_cheapest")
                    except ValueError:
                        pass
        if tmiles > 0 and p_ref:
            trip_miles = tmiles
            trip_gallons = trip_miles / mpg
            trip_cost = trip_gallons * p_ref
            nb = rec["best"]["net_benefit"] if rec["verdict"] == "go" else 0.0
            with st.container(border=True):
                card_header("Your trip at a glance", "What the whole drive costs in fuel, and "
                            "what the recommended stop saves on the fill-up you buy.",
                            ":material/route:")
                with st.container(horizontal=True, gap="small"):
                    st.metric("Estimated trip fuel cost", f"${trip_cost:.2f}", border=True,
                              help=f"{trip_miles:.0f} mi ÷ {mpg:.1f} mpg × ${p_ref:.2f}/gal "
                                   f"(route median price)")
                    st.metric("Potential savings", f"${max(nb, 0):.2f}",
                              delta=(f"on this fill-up ({gallons_used:g} gal)" if nb > 0
                                     else None),
                              delta_color="normal", delta_arrow="off", border=True,
                              help=f"Total for the {gallons_used:g} gallons you buy, not per "
                                   "gallon: the price gap times the gallons, minus the fuel "
                                   "the detour burns and the cost of your time. $0.00 means "
                                   "no detour pays off.")
                    st.metric("Trip distance",
                              f"about {trip_miles:.0f} mi" if SYNTHETIC else f"{trip_miles:.0f} mi",
                              border=True,
                              help="Direct route distance, not including any detour."
                                   + (" Approximate: the synthetic route is straight lines "
                                      "between cities." if SYNTHETIC else ""))
                    st.metric("Fuel needed", f"{trip_gallons:.1f} gal", border=True,
                              help=f"{trip_miles:.0f} mi ÷ {mpg:.1f} mpg")
                    if fplan is not None and not fplan["stop_needed"]:
                        st.metric("Fuel on arrival", f"{fplan['arrive_gal']:.1f} gal",
                                  border=True,
                                  help="Fuel left at the destination without stopping.")
        if rec["alternatives"]:
            with st.container(horizontal=True, vertical_alignment="bottom"):
                st.subheader("Other good options", anchor=False)
                st.caption("Also worth the detour, ranked by what they save", width="content")
            for rank, (col, alt) in enumerate(zip(st.columns(len(rec["alternatives"])),
                                                  rec["alternatives"]), start=2):
                with col.container(border=True, height="stretch"):
                    with st.container(horizontal=True, vertical_alignment="center"):
                        st.markdown(f":small[**#{rank}**]")
                        st.markdown(f":green-badge[saves \\${alt['net_benefit']:.2f}]",
                                    width="content")
                    st.markdown(f"**{recommend.md_escape(alt['name'])}**")
                    st.subheader(f"\\${alt['price']:.2f} :small[/gal]", anchor=False)
                    st.caption(f"{recommend.minutes_label(alt['extra_minutes'])} detour")
                    alt_pin = None
                    if live:
                        try:
                            alt_pin = maps_links.pin_url(alt["lat"], alt["lng"], alt["id"])
                        except ValueError:
                            pass
                    st.link_button("Open in Maps", alt_pin or MAPS_HOME,
                                   icon=":material/location_on:", disabled=alt_pin is None,
                                   key=f"open_alt_{alt['id']}", width="stretch")
        if ranked:
            # Real station prices against a made-up series (or the reverse) are not comparable.
            florida = series["florida"].dropna() if "florida" in series else []
            same_kind = (trip["mode"] == "fixture") == (series_label == "synthetic")
            versus = None
            if same_kind and len(florida):
                versus = insight.corridor_vs_state(
                    [s["price"] for s in priced if s["price"]], float(florida.iloc[-1]))
            n_total = len(_stations)
            n_priced = sum(1 for s in priced if s["price"] is not None)
            cols = st.columns(3 if versus else 2)
            with cols[0].container(border=True, height="stretch"):
                st.metric("Gas price on your route", f"${p_ref:.2f}/gal",
                          help="The typical price at stations right on your route — what "
                               "you'd pay without taking any detour.")
                st.caption("Typical price without a detour")
            with cols[1].container(border=True, height="stretch"):
                st.metric("Stations with a price",
                          f"{n_priced} of {n_total}" if n_total > n_priced else f"{n_priced}",
                          help="Stations inside your search corridor that returned a price. "
                               "Stations without a price can be priced by hand under Edit "
                               "prices and settings, and the ranking updates.")
                st.caption(f"{n_total - n_priced} had no price listed; type it under Edit "
                           f"prices and settings" if n_total > n_priced
                           else f"Inside the {buffer_km:g} km corridor")
            if versus:
                with cols[2].container(border=True, height="stretch"):
                    diff = versus["diff_cents"]
                    st.metric("Versus Florida average", f"{'+' if diff > 0 else ''}{diff}¢")
                    week = florida.index[-1]
                    week = week.strftime("%Y-%m-%d") if hasattr(week, "strftime") else str(week)
                    st.caption(f"Median of all priced stations in the corridor: "
                               f"${versus['route_median']:.2f}/gal; "
                               f"Florida average ({week}, {series_label}): "
                               f"${versus['state_latest']:.2f}/gal")
            lost = insight.regret(ranked)
            if lost > 0 and rec["verdict"] == "go":
                st.caption(f"If you stopped at the cheapest station instead, you'd save "
                           f"${lost:.2f} less on this fill-up.")
            elif lost > 0 and rec["verdict"] == "no_stop":
                st.caption(f"No stop is needed. If you fill up anyway, the cheapest station "
                           f"would save you ${lost:.2f} less than the best one.")
            elif lost > 0:
                st.caption(f"The cheapest station isn't the best one here — but no detour "
                           f"pays off enough, so fill up at the first station on your route.")
            else:
                st.caption("The cheapest station is also the best choice here.")
            grid = insight.decision_grid(detoured, p_ref, mpg, speed)
            rank_gap = insight.price_rank_gap(ranked)
            flip = insight.flip_point(detoured, p_ref, gallons_arg, mpg, speed)
            with st.expander("Does cheapest always mean best?",
                             icon=":material/balance:"):
                st.markdown(f"Picking the **cheapest** sign instead of the **best-ranked** stop "
                            f"would have cost you money in "
                            f"**{grid['misled']} of {grid['total']} scenarios** "
                            f"({grid['share'] * 100:.0f}%) tested across different fill-up sizes "
                            f"and how much you value your time.")
                st.markdown(f"The typical loss from going by price alone: "
                            f"**${grid['median_regret']:.2f}** per fill-up "
                            f"(worst case: **${grid['max_regret']:.2f}**).")
                if rank_gap == 1:
                    st.markdown("Right now the cheapest station is also first in the ranking.")
                else:
                    st.markdown(f"Right now the cheapest station ranks **#{rank_gap}** "
                                f"when detour cost and your time are included.")
                if flip is None:
                    st.markdown(f"At {gallons_used:g} gallons the best station stays the same "
                                f"regardless of how you value your time.")
                else:
                    st.markdown(f"At {gallons_used:g} gallons the best station changes once your time "
                                f"is worth **${flip:.2f}/h or more** — adjust the slider in the "
                                f"sidebar to see.")
                table = pd.DataFrame(
                    {f"${v:g}/h": [next(c["regret"] for c in grid["cells"]
                                        if c["gallons"] == g and c["vot"] == v)
                                   for g in insight.GALLONS_GRID]
                     for v in insight.VOT_GRID},
                    index=[f"{g:g} gal" for g in insight.GALLONS_GRID])
                st.caption("How much you'd lose by picking the cheapest station — rows are "
                           "gallons, columns are how much an hour of your time is worth. "
                           "$0.00 means price alone happened to be right.")
                st.dataframe(table.style.format("${:.2f}"), width="stretch",
                             alt="Dollars lost by choosing the cheapest station, by fill-up size "
                                 "and value of time")
                st.caption("Based on this route and these stations only — not a general forecast.")

    if ranked:
        with st.container(border=True):
            card_header("Route and stations", "Every station found along your route, "
                        "colored by what it saves; the large marker is the recommended stop.",
                        ":material/map:")
            if trip["mode"] == "fixture":
                path = np.array(trip["path"])
                fig = go.Figure()
                fig.add_trace(go.Scattermap(lat=path[:, 0], lon=path[:, 1], mode="lines",
                                            line=dict(width=4, color="#9CCBFF"), name="Route"))
                fig.add_trace(go.Scattermap(
                    lat=[r["lat"] for r in ranked], lon=[r["lng"] for r in ranked],
                    mode="markers",
                    marker=dict(size=11, color=[r["net_benefit"] for r in ranked],
                                colorscale="RdYlGn", cmid=0, showscale=True,
                                colorbar=dict(title="Net $")),
                    text=[f"{r['name']}: ${r['price']:.2f} (net ${r['net_benefit']:.2f})"
                          for r in ranked], name="Stations"))
                if rec["verdict"] == "go":
                    b = rec["best"]
                    fig.add_trace(go.Scattermap(lat=[b["lat"]], lon=[b["lng"]], mode="markers",
                                                marker=dict(size=22, color="#6CB4FF"),
                                                text=[b["name"]], name="Recommended"))
                fig.update_layout(map=dict(style="carto-darkmatter",
                                           center=dict(lat=path[:, 0].mean(),
                                                       lon=path[:, 1].mean()), zoom=7.2),
                                  height=520, margin=dict(l=0, r=0, t=0, b=0),
                                  legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0))
                st.caption("Synthetic route and stations, not real roads: the route is a straight "
                           "line between four cities and the stations are random points near it.")
                st.plotly_chart(fig, width="stretch",
                                alt="Map of the synthetic route with stations colored by net "
                                    "benefit; the recommended station is the large blue marker")
            elif config.GOOGLE_MAPS_JS_KEY:
                st.iframe(build_map_html(config.GOOGLE_MAPS_JS_KEY, trip["path"], ranked,
                                         best_id=best_id), height=520,
                          alt="Google map of the route and the stations; the recommended "
                              "station is the large dark marker")
            else:
                st.warning("Google mode: Places content must be shown on a Google map. Set "
                           "GOOGLE_MAPS_JS_KEY (a referrer-restricted browser key) to show it; "
                           "until then only the table is shown.")
        with st.expander("All stations", icon=":material/table:"):
            cols = ["name", "price", "net_benefit", "extra_minutes", "extra_miles"]
            frame = pd.DataFrame(ranked)
            show = frame[[c for c in cols if c in frame.columns]]  # tolerate a missing column
            st.dataframe(show.head(25), hide_index=True, width="stretch",
                         alt="All stations ranked by how much they save", column_config={
                "name": "Station",
                "price": st.column_config.NumberColumn("Price ($/gal)", format="$%.2f"),
                "net_benefit": st.column_config.NumberColumn("You save ($)", format="$%.2f"),
                "extra_minutes": st.column_config.NumberColumn("Detour (min)", format="%.0f min"),
                "extra_miles": st.column_config.NumberColumn("Detour (miles)", format="%.1f mi")})
        with st.expander("How the saving is calculated", icon=":material/calculate:"):
            st.markdown(
                "**What you save** = (gallons × price difference) − fuel burned getting there "
                "− cost of your time on the detour.\n\n"
                "The **price difference** is compared against the typical price at stations "
                "right on your route (or a price you type in). "
                "The **detour cost** uses real routing for the top stations and a straight "
                "out-and-back estimate for the rest. "
                "The **time cost** uses the value you set in the sidebar.")

# ---------------------------------------------------------------- when to fill up
elif _selected == ":material/calendar_month: When to fill up":
    st.caption("What this tab answers: is it worth timing your fill-up to the weekly price? "
               "It tests whether any forecast beats the naive guess that next week equals this "
               "week.")
    st.subheader(f"Weekly regular gasoline, $/gal ({series_label})")
    s_fl = series["florida"].dropna()
    st.line_chart(series[["florida", "us"]].dropna().tail(260),
                  alt="Weekly regular gasoline price, Florida and U.S., last five years")
    st.markdown("**Does any simple model beat 'next week = this week'?** Rolling-origin "
                "evaluation on the Florida series, error shown as a ratio to the naive forecast "
                "(below 1.00 beats naive).")
    ev = rolling_origin_eval(s_fl.to_numpy())
    rows = [{"method": m, **{f"h={h}": ev[m][h]["ratio_to_naive"] for h in ev[m]}} for m in ev]
    st.dataframe(pd.DataFrame(rows), hide_index=True,
                 alt="Forecast error as a ratio to the naive forecast, by method and horizon")
    st.caption("If nothing beats naive, the honest advice is: prices move too randomly to time "
               "the week; use the station comparison instead.")

# ---------------------------------------------------------------- EDA
elif _selected == ":material/analytics: EDA":
    st.caption("What this tab answers: is the weekly price data clean and what does it look "
               "like? Check gaps, outliers and distributions before trusting any forecast.")
    st.subheader("Exploratory analysis of weekly gasoline prices")
    c_a, c_b = st.columns(2)
    z_thr = c_a.slider("Outlier threshold (SD of weekly change)", 2.0, 5.0, 3.0, 0.5)
    treat = c_b.segmented_control(
        "Outlier treatment", ["keep", "interpolate"], default="keep", key="eda_treat",
        help="Price shocks can be real events; 'interpolate' replaces them by the average of "
             "neighbouring weeks.") or "keep"
    if True:
        raw = series["florida"]
        clean, rep = treat_series(raw, z=z_thr, outliers=treat)
        d = series.copy()
        d["florida"] = clean
        d = d.interpolate(limit_direction="both")

        # ---- 1. Data quality ------------------------------------------------
        st.markdown("#### 1. Data quality")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Weeks", f"{len(d):,}", border=True)
        c2.metric("Missing weeks filled", rep["n_missing_filled"], border=True)
        c3.metric("Price shocks flagged", rep["n_outliers_flagged"], border=True)
        c4.metric("Latest Florida", f"${clean.iloc[-1]:.2f}", border=True)
        st.caption(f"Treatment: missing weeks by linear interpolation; outliers: {treat}. Mean "
                   f"{rep['mean_before']:.3f} → {rep['mean_after']:.3f}, SD "
                   f"{rep['std_before']:.3f} → {rep['std_after']:.3f} (before → after).")

        # ---- 2. Descriptive statistics --------------------------------------
        st.markdown("#### 2. Descriptive statistics")
        st.dataframe(d.describe().T.round(3), alt="Summary statistics of the weekly prices")
        box_df = d.reset_index().melt(id_vars="period", var_name="Series",
                                      value_name="Price ($/gal)")
        box_df["Series"] = box_df["Series"].map({"florida": "Florida", "us": "U.S."})
        st.plotly_chart(
            px.box(box_df, x="Series", y="Price ($/gal)",
                   title="Price distribution: Florida vs U.S.",
                   color="Series", points="outliers"),
            width="stretch", alt="Box plot comparing Florida and US price distributions")

        # ---- 3. Outlier detection and treatment ----------------------------
        st.markdown("#### 3. Outlier detection and treatment")
        comp = pd.DataFrame({"raw": raw, "treated": clean})
        st.line_chart(comp.tail(520), alt="Raw and treated Florida weekly price, last ten years")
        st.caption("Flagged points are weeks where the week-on-week change exceeded "
                   f"{z_thr}× the historical standard deviation of changes.")

        # ---- 4. Correlation analysis ----------------------------------------
        st.markdown("#### 4. Correlation analysis")
        ch = clean.diff().dropna()
        chg = d.diff().dropna()
        corr_d = chg.corr().loc["florida", "us"]
        lags = lagged_corr(d["florida"], d["us"], 8)
        best_lag = max(lags, key=lambda k: lags[k])
        col_corr, col_scatter = st.columns(2)
        with col_corr:
            st.plotly_chart(
                px.bar(x=list(lags), y=list(lags.values()),
                       labels=dict(x="weeks U.S. leads Florida",
                                   y="corr of weekly changes"),
                       title="Lead-lag correlation (Florida vs U.S.)"),
                width="stretch",
                alt="Bar chart: correlation of weekly changes by lag in weeks")
        with col_scatter:
            _x, _y = d["us"].to_numpy(), d["florida"].to_numpy()
            _mask = np.isfinite(_x) & np.isfinite(_y)
            _m, _b = np.polyfit(_x[_mask], _y[_mask], 1)
            _fig_sc = px.scatter(d, x="us", y="florida",
                                 labels=dict(us="U.S. price ($/gal)",
                                             florida="Florida price ($/gal)"),
                                 title="Florida vs U.S. price levels",
                                 opacity=0.5)
            _fig_sc.add_scatter(x=_x[_mask], y=_m * _x[_mask] + _b,
                                mode="lines", name="OLS fit",
                                line=dict(color="red", width=2))
            st.plotly_chart(_fig_sc, width="stretch",
                            alt="Scatter plot of Florida vs US weekly prices with OLS trendline")
        st.plotly_chart(
            px.histogram(ch, nbins=60,
                         title="Distribution of weekly price changes ($/gal)"),
            width="stretch",
            alt="Histogram of weekly price changes in dollars per gallon")
        prices = [s["price"] for s in priced if s["price"]]
        if prices:
            st.plotly_chart(
                px.histogram(prices, nbins=20,
                             title="Station price spread on this route"),
                width="stretch",
                alt="Histogram of station prices on this route")
            st.write(f"Station spread: min ${min(prices):.2f}, max ${max(prices):.2f}, "
                     f"IQR ${np.subtract(*np.percentile(prices, [75, 25])):.2f}")

        # ---- 5. Key insights ------------------------------------------------
        st.markdown("#### 5. Key insights")
        avg_52 = float(clean.iloc[-52:].mean()) if len(clean) >= 52 else float(clean.mean())
        latest_fl = float(clean.iloc[-1])
        vs_avg = "above" if latest_fl > avg_52 else "below"
        gap_avg = abs(latest_fl - avg_52)
        corr_str = ("strongly" if abs(corr_d) >= 0.7
                    else "moderately" if abs(corr_d) >= 0.4 else "weakly")
        st.info(
            f"- The dataset covers **{len(d):,} weeks** of EIA weekly prices. "
            + (f"**{rep['n_missing_filled']} missing week(s)** were filled by interpolation. "
               if rep["n_missing_filled"] else "No missing weeks were found. ")
            + f"\n- **{rep['n_outliers_flagged']} price shock(s)** were flagged at the "
              f"current threshold (Z > {z_thr})"
            + ("; they were replaced." if treat == "interpolate" else "; they were kept.")
            + f"\n- Florida and U.S. weekly price changes are **{corr_str} correlated** "
              f"(r = {corr_d:.2f})."
            + f"\n- U.S. prices lead Florida by **{best_lag} week(s)** at the strongest lag "
              f"(r = {lags[best_lag]:.2f})."
            + f"\n- The latest Florida price (${latest_fl:.2f}/gal) is **${gap_avg:.2f} "
              f"{vs_avg}** the 52-week average (${avg_52:.2f}/gal).",
            icon=":material/lightbulb:")

        # ---- AI summary -----------------------------------------------------
        ev_eda = rolling_origin_eval(clean.to_numpy())
        best_m = min((m for m in ev_eda if m != "naive"),
                     key=lambda m: ev_eda[m][1]["ratio_to_naive"])
        eda_facts = {"weeks": len(d), "n_missing_filled": rep["n_missing_filled"],
                     "n_outliers_flagged": rep["n_outliers_flagged"],
                     "latest_florida": round(float(clean.iloc[-1]), 2),
                     "corr_changes": round(float(corr_d), 2), "best_lag_weeks": best_lag,
                     "best_method": best_m,
                     "best_ratio": round(ev_eda[best_m][1]["ratio_to_naive"], 2)}
        st.markdown("**AI summary of this analysis**")
        eda_llm = _llm_provider()
        if st.button("Summarize the EDA"):
            res = ask("eda", eda_facts, eda_llm)
            if res.get("error"):
                st.error(f"The LLM could not be reached, so this is the template: "
                         f"{res['error']}", icon=":material/error:")
            st.write(res["text"])
            st.caption(f"Source: {res['source']}"
                       + (" (no LLM key — set GROQ_API_KEY in .env for free access; "
                          "template used instead)"
                          if res["source"] == "template" and eda_llm is None else ""))
        with st.expander("Facts the summary may use"):
            st.json(eda_facts)

# ---------------------------------------------------------------- sensitivity
elif _selected == ":material/calculate: Sensitivity":
    st.caption("What this tab answers: how many extra miles of detour a given price gap is "
               "worth, at your mpg (Pick your car) and value of time (sidebar).")
    st.subheader("How far is a detour worth it?")
    st.write("Break-even extra miles for a price gap (columns, $/gal) and value of time "
             "(rows, $/h), using your gallons, mpg and speed.")
    if True:
        ref = p_ref if p_ref else 3.6
        sens = sensitivity_table(gallons_used, ref, mpg, speed)
        df_s = pd.DataFrame(sens).T
        # Text labels make the axes categorical, so every cell is the same size.
        df_s.columns = [f"${g:.2f}" for g in df_s.columns]
        df_s.index = [f"${v:g}/h" for v in df_s.index]
        df_s.index.name = "value of time $/h"
        heat = px.imshow(df_s, text_auto=True, aspect="auto", color_continuous_scale="Blues",
                         labels=dict(x="price gap ($/gal)", y="value of time", color="miles"))
        # Plotly reads "$0.10" as a number; categorical axes keep the cells even.
        heat.update_xaxes(type="category")
        heat.update_yaxes(type="category")
        st.plotly_chart(heat, width="stretch",
                        alt="Heat map of break-even extra miles by price gap and value of time")
        with st.expander("See the numbers as a table", icon=":material/table:"):
            st.dataframe(df_s, alt="Break-even extra miles by price gap and value of time")

# ---------------------------------------------------------------- AI explanation


# The assistant sits in the top bar of every page. The model only phrases numbers the
# code computed: every answer is validated against `facts`, else a template is used (llm.py).
def assistant_facts():
    best = ranked[0]
    gap = p_ref - best["price"]
    g = round(gallons_used, 1)
    facts = {"best_station": best["name"], "best_price": round(best["price"], 2),
            "p_ref": round(p_ref, 2), "gallons": g,
            "best_saving_gross": round(g * gap, 2),
            "extra_miles": round(best["extra_miles"], 1),
            "extra_minutes": round(best["extra_minutes"], 1),
            "best_net_benefit": round(best["net_benefit"], 2),
            "breakeven_miles": round(max(breakeven_miles(
                g, gap, best["price"], mpg, vot, speed), 0.0), 1) if gap > 0 else 0.0,
            "value_of_time": vot,
            # The decision is the app's, not the model's: it must state it, never re-derive it.
            "verdict": ("you have enough fuel: no stop needed" if rec["verdict"] == "no_stop"
                        else "the detour pays off: stop at the best station"
                        if rec["verdict"] == "go"
                        else "no detour pays off enough: fill up on your route")}
    if fplan is not None:
        facts["stop_needed"] = fplan["stop_needed"]
        facts["arrive_gal"] = round(fplan["arrive_gal"], 1)
        if _app_chose:
            facts["gallons_to_buy"] = g
    return facts


def show_reply(msg):
    """An answer, then how it was checked: share of numbers traced on the first try, attempts,
    source. This is what makes a hallucination visible instead of hypothetical."""
    if msg.get("error"):
        st.error(f"The LLM could not be reached, so this is the template: {msg['error']}",
                 icon=":material/error:")
    st.markdown(recommend.md_escape(msg["text"]))
    if msg.get("fallback_used"):
        badge = ":orange-badge[:material/rule: Numbers not traceable · template used]"
    elif msg["source"] == "template":
        badge = ":gray-badge[:material/description: Template answer]"
    else:
        rate = msg.get("traceable_first_try")
        badge = f":green-badge[:material/verified: {(1.0 if rate is None else rate):.0%} of numbers traced]"
    attempts = msg.get("attempts") or 0
    detail = f"{msg['source']}" + (f" · {attempts} attempt{'s' if attempts != 1 else ''}"
                                   if attempts else "")
    st.markdown(f"{badge} :small[{detail}]")


ASSISTANT_SUGGESTIONS = ["Explain this recommendation", "Is the detour worth my time?",
                         "Why not the cheapest station?"]


def assistant_turn(chat, text, facts, provider):
    chat.append({"role": "user", "text": text})
    reply = (explain(facts, provider) if text == ASSISTANT_SUGGESTIONS[0]
             else ask("qa", facts, provider, question=text))
    chat.append({"role": "assistant", **reply})


AVATARS = {"user": ":material/person:", "assistant": ":material/smart_toy:"}

with st.container(key="assistant_fab"):
    with st.popover("Assistant", icon=":material/smart_toy:"):
        llm_provider = _llm_provider()
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown("**Fuel assistant**", width="content")
            st.markdown(":green-badge[:material/bolt: LLM on]" if llm_provider
                        else ":gray-badge[:material/description: Template mode]", width="content")
        st.caption("Every number in an answer is checked against the app's calculation.")
        if not ranked and _none_reachable:
            st.info("None of the listed stations is reachable before your reserve, so there is "
                    "nothing to explain yet.")
        elif not ranked:
            st.info("Add at least one station price first.")
        else:
            facts = assistant_facts()
            chat = st.session_state.setdefault("assistant_chat", [])
            log = st.container()
            with st.container(horizontal=True, gap="small"):
                for suggestion in ASSISTANT_SUGGESTIONS:
                    if st.button(suggestion, key=f"suggest_{suggestion}"):
                        assistant_turn(chat, suggestion, facts, llm_provider)
            with st.form("assistant_form", clear_on_submit=True, border=False):
                with st.container(horizontal=True, vertical_alignment="bottom", gap="small"):
                    question = st.text_input("Your question", label_visibility="collapsed",
                                             placeholder="Ask about this trip...")
                    sent = st.form_submit_button("Send", icon=":material/send:",
                                                 type="primary")
                if sent and question.strip():
                    assistant_turn(chat, question.strip(), facts, llm_provider)
            with log:
                if not chat:
                    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
                        st.markdown("Hi! I can explain why the app picked this stop, or answer "
                                    "a question about this trip. Pick a suggestion or type below.")
                        if llm_provider is None:
                            st.caption("No LLM key is set (GROQ_API_KEY, free at groq.com), so "
                                       "answers come from a fixed template.")
                else:
                    with st.container(height=340, border=False):
                        for msg in chat[-8:]:
                            with st.chat_message(msg["role"], avatar=AVATARS[msg["role"]]):
                                if msg["role"] == "user":
                                    st.markdown(recommend.md_escape(msg["text"]))
                                else:
                                    show_reply(msg)
            with st.container(horizontal=True, vertical_alignment="center"):
                with st.expander("What the assistant knows", icon=":material/dataset:"):
                    st.json(facts)
                if chat and st.button("Clear", icon=":material/delete_sweep:", type="tertiary"):
                    chat.clear()
                    st.rerun()
