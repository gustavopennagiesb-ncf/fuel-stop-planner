"""Streamlit app: when and where to fill up, with detour break-even and a validated LLM
explanation. Runs without any key in fixture mode (synthetic data, clearly labelled)."""
import time

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components

from envfile import load_dotenv

load_dotenv()  # before `import config`, which reads the environment at import time

import config  # noqa: E402
import driver
import location
import maps_links
import recommend
from detour import (DEFAULTS, breakeven_miles, rank_stations, reference_price,
                    sensitivity_table)
from forecast import lagged_corr, rolling_origin_eval, treat_series
from gmap import build_map_html
from llm import AnthropicProvider, ask, explain
from providers import fueleconomy
from services import compute_detours, load_series, load_trip

FE_RETRY_S = 60  # seconds before the car lookup is tried again after fueleconomy.gov failed

st.set_page_config(page_title="Fuel Stop Planner", page_icon=":material/local_gas_station:", layout="wide")
st.header("Fuel Stop Planner")
st.caption("When to fill up (EIA weekly prices) and where (stations along your route, with "
           "detour break-even). Not financial advice: historical data and simple arithmetic.")

provider = config.PROVIDER
if provider == "fixture" or not (config.GOOGLE_API_KEY and config.EIA_API_KEY):
    st.markdown(":orange-badge[:material/science: Synthetic test data] Prices, stations and the "
                "route are made up. Set PROVIDER=google plus EIA_API_KEY and GOOGLE_API_KEY for "
                "live data.")

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


def slider_with_number(label, lo, hi, default, step, key, help=None):
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

    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        value = st.slider(label, lo, hi, step=step, key=key, help=help)
    st.session_state[num_key] = value
    with right:
        st.number_input(f"{label}: exact value", lo, hi, step=step, key=num_key,
                        on_change=typed, label_visibility="collapsed")
    return value


st.session_state.setdefault("mpg", DEFAULTS["mpg"])

# "Your car" sits in the main area, not the sidebar, so it is visible on a phone (where the
# sidebar starts collapsed) and every tab reads the same mpg and tank.
with st.container(border=True):
    st.markdown("**Your car**")
    with st.expander("Look up my car (fueleconomy.gov)"):
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
            st.caption(f"{car['label']}: city {car['city']}, combined {car['combined']}, "
                       f"highway {car['highway']} mpg (EPA label values; real mileage varies).")
    c_mpg, c_tank = st.columns([3, 2], vertical_alignment="bottom")
    with c_mpg:
        mpg = slider_with_number("Fuel economy (mpg)", 10.0, 60.0, DEFAULTS["mpg"], 0.5, "mpg")
    with c_tank:
        tank = st.number_input("Tank size (gallons)", 5.0, 40.0, 13.0, 0.5, key="tank")
    st.caption(f"About {tank * mpg:.0f} miles on a full tank at this mpg.")

# "Your trip": where from and to, and how wide to search. In google mode the inputs sit in a form,
# so typing an address does not start a paid search on every keystroke: it starts on "Find stations".
origin = destination = None
with st.container(border=True):
    st.markdown("**Your trip**")
    corridor_help = ("How far from your route a station can be and still be listed. For example, "
                     "3 km each side searches a 6 km wide strip along the route.")
    if provider == "google" and config.GOOGLE_API_KEY:
        mine = st.session_state.get("my_location")
        typed = None
        if mine:
            acc = st.session_state.get("my_location_accuracy")
            st.caption(f"Using your current location ({mine[0]:.4f}, {mine[1]:.4f})"
                       + (f", accuracy about {acc} m" if acc else "")
                       + ". It is used only for this route and is not saved.")
            if st.button("Use the typed address instead"):
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
        with st.form("trip_form", border=False):
            if not mine:
                typed = st.text_input("Origin", "Sarasota, FL", key="trip_origin")
            destination = st.text_input("Destination", "Orlando, FL", key="trip_destination")
            buffer_km = st.number_input("Search corridor width (km each side)", 1.0, 10.0, 3.0,
                                        0.5, key="buffer_km", help=corridor_help)
            st.form_submit_button("Find stations", type="primary", icon=":material/search:")
        origin = location.resolve_origin(mine, typed)
    else:
        st.caption("Synthetic test mode: the route is always Sarasota to Orlando. Typing your own "
                   "trip and using your current location need PROVIDER=google (live route data).")
        buffer_km = st.number_input("Search corridor width (km each side)", 1.0, 10.0, 3.0, 0.5,
                                    key="buffer_km", help=corridor_help)

with st.sidebar:
    st.header("Trip and time")
    st.caption("Drag a slider or type the exact value in the box beside it.")
    gallons = slider_with_number(
        "Gallons to buy", 3.0, 25.0, DEFAULTS["gallons"], 0.5, "gallons",
        help="How much fuel you plan to put in at the stop. Bigger fills make a price gap worth "
             "more. For example, a 10 cent/gal gap on 12 gallons saves $1.20 before the detour "
             "costs.")
    vot = slider_with_number(
        "Value of your time ($/h)", 0.0, 60.0, DEFAULTS["value_of_time"], 1.0, "vot",
        help="What an hour of your time is worth to you; the app charges detour minutes at this "
             "rate. For example, at $20/h a 10 minute detour costs about $3.33.")
    speed = slider_with_number(
        "Average speed on detours (mph)", 20.0, 70.0, DEFAULTS["speed_mph"], 1.0, "speed",
        help="Used to turn detour miles into detour minutes. For example, 2 extra miles at 30 "
             "mph is 4 minutes.")


@st.cache_data(show_spinner="Loading prices...", ttl=3600)
def cached_series(p):
    return load_series(p)


@st.cache_data(show_spinner="Finding stations...", ttl=600)
def cached_trip(p, o, d, buf):
    return load_trip(o, d, buf, provider=p)


series, series_label = cached_series(provider)
try:
    trip = cached_trip(provider, origin, destination, buffer_km * 1000)
except Exception as e:  # network or key problems should not crash the demo
    st.error(f"Could not load the trip ({e}). Falling back to fixture data.")
    trip = cached_trip("fixture", None, None, buffer_km * 1000)

for w in trip["warnings"]:
    st.info(w)

tab_help, tab_trip, tab_when, tab_eda, tab_sens, tab_driver, tab_ai = st.tabs(
    ["How to use", "Where to stop", "When to fill up", "EDA", "Sensitivity", "Driver mode",
     "AI"], on_change="rerun")


def is_open(tab):
    """With on_change="rerun" Streamlit says which tab is open, so a tab's heavy body can run
    only then. Widgets stay outside the guard so their values survive while the tab is closed.
    `None` (state unknown, e.g. in AppTest) counts as open."""
    return tab.open is not False

# ---------------------------------------------------------------- how to use
with tab_help:
    st.subheader("How to use this app")
    st.markdown(
        "**Start with \"Your car\" at the top:** pick your car (or set the mpg) and your tank "
        "size. The sidebar holds the trip and time settings. Hover the small (?) next to a "
        "control for a tip. Every tab reads those values.\n\n"
        "**Which tab should I open?**\n"
        "- *I am on a trip and want the best station on the way* → **Where to stop**. Edit any "
        "price in the table (typed prices always work). The app ranks stations by net benefit: "
        "fuel saved minus the extra fuel and time of the detour.\n"
        "- *I deliver (DoorDash and similar) and want to know how far I can go before "
        "refueling* → **Driver mode**. Enter your tank level; it shows miles, hours of work and "
        "orders left before your reserve. It does not predict orders.\n"
        "- *Is this a good week to fill up?* → **When to fill up**: weekly Florida/U.S. prices "
        "and a check of whether any simple forecast beats 'next week = this week'.\n"
        "- *How much is a detour worth?* → **Sensitivity**: extra miles a detour is worth for "
        "each price gap.\n"
        "- *Where does the price data come from and is it clean?* → **EDA**: missing-data and "
        "outlier treatment (before/after), distributions and correlations of the EIA series.\n"
        "- *Explain it in plain words* → **AI**: a plain-language explanation and a question "
        "box. Every number the model writes is checked against the calculation; if it cannot "
        "be traced, a template is used.\n\n"
        "Data: EIA Open Data API v2 (weekly retail gasoline, JSON) and Google Places/Routes "
        "(stations, route). Without keys the app runs on **synthetic** data and says so. "
        "Not financial advice.")
    with st.expander("Glossary"):
        st.markdown(
            "- **Net benefit**: dollars saved by filling at a cheaper station, minus the fuel "
            "and the time cost of getting there. Positive means the detour pays off.\n"
            "- **Reference price**: what you would pay without detouring; the median price of "
            "stations within 800 m of the route, or a price you type.\n"
            "- **Detour source**: `routes` is an exact detour from the Google Routes API; "
            "`approx` is an out-and-back estimate used when no exact detour was fetched.\n"
            "- **Value of time**: dollars per hour you assign to your time; detour minutes are "
            "charged at this rate.\n"
            "- **Naive forecast**: 'next week's price = this week's price'. A forecast is only "
            "useful if it beats this.\n"
            "- **EDA**: exploratory data analysis, a first look at the data's gaps, outliers "
            "and shape.\n"
            "- **Reserve**: fuel you do not plan to use (Driver mode), so you are never "
            "stranded.\n"
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
            nav = maps_links.directions_url(origin, destination, (best["lat"], best["lng"]),
                                            best["id"])
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


with tab_trip:
    st.caption("What this tab answers: which station on your route is worth a detour, and where "
               "it is. The answer comes first; prices, settings and details are below it.")
    answer = st.container()  # the answer, filled below once the prices are known
    stations = trip["stations"]
    with st.expander("Edit prices and settings", icon=":material/tune:"):
        min_saving = st.number_input(
            "Only recommend a stop that saves at least ($)", 0.0, 20.0,
            recommend.MIN_SAVING_DEFAULT, 0.25, key="min_saving",
            help="Below this saving the app tells you to fill up at the first station on your "
                 "route. The default of $0.50 is an assumption: a smaller saving is not worth "
                 "the stop.")
        st.markdown("**Station prices** (typed prices always work)")
        table = pd.DataFrame([{"id": s["id"], "name": s["name"], "price": s["price"]}
                              for s in stations], columns=["id", "name", "price"])
        edited = st.data_editor(table, disabled=["id", "name"], hide_index=True,
                                column_order=["name", "price"], width="stretch",
                                key="price_editor",
                                alt="Station prices; edit the price column to change the ranking")
        price_by_id = dict(zip(edited["id"], edited["price"]))
        priced = [{**s, "price": (None if pd.isna(price_by_id.get(s["id"])) else
                                  float(price_by_id[s["id"]]))} for s in stations]
        p_ref = reference_price(priced)
        if p_ref is None:
            p_ref = st.number_input("No priced station is on the route: type the price you would "
                                    "pay on the route ($/gal)", 2.0, 6.0, 3.6, 0.01)
    detoured = compute_detours({**trip, "stations": priced})
    ranked = rank_stations(detoured, p_ref, gallons, mpg, vot, speed)
    rec = recommend.recommend(ranked, min_saving)
    live = trip["mode"] == "google"
    best_id = rec["best"]["id"] if rec["verdict"] == "go" else None

    with answer:
        with st.container(border=True):
            if not stations:
                st.warning("No stations were listed within the search corridor. Widen the "
                           "corridor under Your trip and press Find stations.",
                           title="No stations found near this route")
            else:
                title, body = recommend.summary(rec)
                if rec["verdict"] == "go":
                    st.success(body, title=title, icon=":material/local_gas_station:")
                    maps_buttons(rec["best"], live)
                elif rec["verdict"] == "skip":
                    st.info(body, title=title, icon=":material/info:")
                else:
                    st.warning(body, title=title)
        cheap = recommend.cheapest(ranked)
        if cheap:  # the lowest price on the route, which is not always the stop that saves most
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
        if rec["alternatives"]:
            st.markdown("**Other good options**")
            for col, alt in zip(st.columns(len(rec["alternatives"])), rec["alternatives"]):
                with col.container(border=True, height="stretch"):
                    st.markdown(f"**{recommend.md_escape(alt['name'])}**")
                    st.caption(f"${alt['price']:.2f}/gal · saves ${alt['net_benefit']:.2f} · "
                               f"{alt['extra_minutes']:.0f} min detour")
        if ranked:
            best = ranked[0]
            with st.container(horizontal=True):
                st.metric("Price without detouring", f"${p_ref:.2f}/gal", border=True,
                          help="The median price of stations right on your route.")
                st.metric("Best option", best["name"], f"${best['price']:.2f}/gal", border=True)
                st.metric("You save (after the detour)", f"${best['net_benefit']:.2f}",
                          "detour pays off" if best["net_benefit"] > 0 else "do not detour",
                          border=True)

    if ranked:
        with st.container(border=True):
            st.markdown("**Route and stations**")
            if trip["mode"] == "fixture":
                path = np.array(trip["path"])
                fig = go.Figure()
                fig.add_trace(go.Scattermap(lat=path[:, 0], lon=path[:, 1], mode="lines",
                                            line=dict(width=4, color="#1f77b4"), name="Route"))
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
                                                marker=dict(size=22, color="#1E3A8A"),
                                                text=[b["name"]], name="Recommended"))
                fig.update_layout(map=dict(style="carto-positron",
                                           center=dict(lat=path[:, 0].mean(),
                                                       lon=path[:, 1].mean()), zoom=7.2),
                                  height=520, margin=dict(l=0, r=0, t=0, b=0),
                                  legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0))
                st.caption("Synthetic route and stations, not real roads: the route is a straight "
                           "line between four cities and the stations are random points near it.")
                st.plotly_chart(fig, width="stretch",
                                alt="Map of the synthetic route with stations colored by net "
                                    "benefit; the recommended station is the large dark marker")
            elif config.GOOGLE_MAPS_JS_KEY:
                components.html(build_map_html(config.GOOGLE_MAPS_JS_KEY, trip["path"], ranked,
                                               best_id=best_id), height=520)
            else:
                st.warning("Google mode: Places content must be shown on a Google map. Set "
                           "GOOGLE_MAPS_JS_KEY (a referrer-restricted browser key) to show it; "
                           "until then only the table is shown.")
        with st.expander("All stations", icon=":material/table:"):
            cols = ["name", "price", "net_benefit", "extra_minutes", "extra_miles", "off_route_m"]
            if live:
                cols.append("detour_source")  # routes = exact Routes API, approx = out-and-back
            frame = pd.DataFrame(ranked)
            show = frame[[c for c in cols if c in frame.columns]]  # tolerate a missing column
            st.dataframe(show.head(25), hide_index=True, width="stretch",
                         alt="All stations ranked by how much they save", column_config={
                "name": "Station", "price": st.column_config.NumberColumn("Price ($/gal)"),
                "net_benefit": st.column_config.NumberColumn("You save ($)"),
                "extra_minutes": st.column_config.NumberColumn("Detour (min)"),
                "extra_miles": st.column_config.NumberColumn("Detour (miles)"),
                "off_route_m": st.column_config.NumberColumn("Off route (m)"),
                "detour_source": "Detour source"})
        with st.expander("How the saving is calculated", icon=":material/calculate:"):
            st.markdown(
                "**You save** = gallons x (price without detouring - station price) - the fuel "
                "burned on the detour - the value of the detour time. *Price without "
                "detouring* is the median price of stations right on your route (or the price "
                "you type). In google mode the detour comes from Google Routes for the "
                "cheapest stations (`routes`) and from a straight out-and-back estimate for "
                "the others (`approx`).")

# ---------------------------------------------------------------- when to fill up
with tab_when:
    st.caption("What this tab answers: is it worth timing your fill-up to the weekly price? "
               "It tests whether any forecast beats the naive guess that next week equals this "
               "week.")
    st.subheader(f"Weekly regular gasoline, $/gal ({series_label})")
    if is_open(tab_when):
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
with tab_eda:
    st.caption("What this tab answers: is the weekly price data clean and what does it look "
               "like? Check gaps, outliers and distributions before trusting any forecast.")
    st.subheader("Exploratory analysis of weekly gasoline prices")
    c_a, c_b = st.columns(2)
    z_thr = c_a.slider("Outlier threshold (SD of weekly change)", 2.0, 5.0, 3.0, 0.5)
    treat = c_b.segmented_control(
        "Outlier treatment", ["keep", "interpolate"], default="keep", key="eda_treat",
        help="Price shocks can be real events; 'interpolate' replaces them by the average of "
             "neighbouring weeks.") or "keep"
    if is_open(tab_eda):
        raw = series["florida"]
        clean, rep = treat_series(raw, z=z_thr, outliers=treat)
        d = series.copy()
        d["florida"] = clean
        d = d.interpolate(limit_direction="both")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Weeks", f"{len(d):,}", border=True)
        c2.metric("Missing weeks filled", rep["n_missing_filled"], border=True)
        c3.metric("Price shocks flagged", rep["n_outliers_flagged"], border=True)
        c4.metric("Latest Florida", f"${clean.iloc[-1]:.2f}", border=True)
        st.caption(f"Treatment: missing weeks by linear interpolation; outliers: {treat}. Mean "
                   f"{rep['mean_before']:.3f} -> {rep['mean_after']:.3f}, SD "
                   f"{rep['std_before']:.3f} -> {rep['std_after']:.3f} (before -> after).")
        comp = pd.DataFrame({"raw": raw, "treated": clean})
        st.line_chart(comp.tail(520), alt="Raw and treated Florida weekly price, last ten years")
        st.dataframe(d.describe().T.round(3), alt="Summary statistics of the weekly prices")
        ch = clean.diff().dropna()
        chg = d.diff().dropna()
        corr_d = chg.corr().loc["florida", "us"]
        lags = lagged_corr(d["florida"], d["us"], 8)
        best_lag = max(lags, key=lambda k: lags[k])
        st.write(f"Correlation of weekly changes, Florida vs U.S.: {corr_d:.3f}. "
                 f"Strongest lagged link: U.S. leads Florida by {best_lag} week(s) "
                 f"(r = {lags[best_lag]:.3f}).")
        st.plotly_chart(px.bar(x=list(lags), y=list(lags.values()),
                               labels=dict(x="weeks U.S. leads Florida", y="corr of weekly changes"),
                               title="Lead-lag correlation"), width="stretch",
                        alt="Bar chart: correlation of weekly changes by lag in weeks")
        st.plotly_chart(px.histogram(ch, nbins=60, title="Distribution of weekly changes ($/gal)"),
                        width="stretch", alt="Histogram of weekly price changes in dollars per gallon")
        prices = [s["price"] for s in priced if s["price"]]
        if prices:
            st.plotly_chart(px.histogram(prices, nbins=20, title="Station price spread on this route"),
                            width="stretch", alt="Histogram of station prices on this route")
            st.write(f"Spread: min ${min(prices):.2f}, max ${max(prices):.2f}, "
                     f"IQR ${np.subtract(*np.percentile(prices, [75, 25])):.2f}")
        ev_eda = rolling_origin_eval(clean.to_numpy())
        best_m = min((m for m in ev_eda if m != "naive"), key=lambda m: ev_eda[m][1]["ratio_to_naive"])
        eda_facts = {"weeks": len(d), "n_missing_filled": rep["n_missing_filled"],
                     "n_outliers_flagged": rep["n_outliers_flagged"],
                     "latest_florida": round(float(clean.iloc[-1]), 2),
                     "corr_changes": round(float(corr_d), 2), "best_lag_weeks": best_lag,
                     "best_method": best_m, "best_ratio": round(ev_eda[best_m][1]["ratio_to_naive"], 2)}
        st.markdown("**AI summary of this analysis**")
        eda_llm = AnthropicProvider()
        if st.button("Summarize the EDA"):
            res = ask("eda", eda_facts, eda_llm if eda_llm.available() else None)
            st.write(res["text"])
            st.caption(f"Source: {res['source']}"
                       + (" (no ANTHROPIC_API_KEY: template, not an LLM)" if res["source"] == "template" and not eda_llm.available() else ""))
        with st.expander("Facts the summary may use"):
            st.json(eda_facts)

# ---------------------------------------------------------------- sensitivity
with tab_sens:
    st.caption("What this tab answers: how many extra miles of detour a given price gap is "
               "worth, at your mpg and value of time (set in the sidebar).")
    st.subheader("How far is a detour worth it?")
    st.write("Break-even extra miles for a price gap (columns, $/gal) and value of time "
             "(rows, $/h), using your gallons, mpg and speed.")
    if is_open(tab_sens):
        ref = p_ref if p_ref else 3.6
        sens = sensitivity_table(gallons, ref, mpg, speed)
        df_s = pd.DataFrame(sens).T
        df_s.index.name = "value of time $/h"
        st.dataframe(df_s, alt="Break-even extra miles by price gap and value of time")
        st.plotly_chart(px.imshow(df_s, text_auto=True, aspect="auto",
                                  labels=dict(x="price gap $/gal", y="value of time $/h",
                                              color="miles")), width="stretch",
                        alt="Heat map of break-even extra miles by price gap and value of time")

# ---------------------------------------------------------------- driver mode
with tab_driver:
    st.caption("What this tab answers: how many miles, hours of work and orders you can do "
               "before reaching your fuel reserve. Set your tank level first.")
    st.subheader("Driver mode: how far until I must refuel?")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        level = c1.select_slider("Tank level (eighths, read from the gauge)",
                                 options=list(range(0, 9)), value=4, key="driver_level")
        reserve_label = c2.segmented_control(
            "Reserve", ["Standard", "Conservative (1/4 tank)"], default="Standard",
            key="driver_reserve_mode") or "Standard"
        st.caption(f"Using tank {tank} gal and {mpg} mpg from Your car above: check both.")
        reserve_gal = driver.reserve_gallons(
            tank, "conservative" if reserve_label.startswith("Conservative") else "standard")
        st.caption(f"Reserve: {reserve_gal:.1f} gal (about {reserve_gal * mpg:.0f} miles at your mpg)")
    # Drawn here, right under the tank inputs, and filled once the pace is known. No border of
    # its own: the metrics inside are already cards.
    results = st.container()
    with st.container(border=True):
        st.markdown("**Your pace**")
        c3, c4 = st.columns(2)
        mph = c3.number_input("Miles per hour of work", min_value=1.0,
                              value=driver.DEFAULT_MILES_PER_HOUR, step=1.0, key="driver_mph")
        mpo = c4.number_input("Miles per order", min_value=1.0,
                              value=driver.DEFAULT_MILES_PER_ORDER, step=1.0, key="driver_mpo")
        st.caption("Defaults are assumptions, not measured values: 15 mi/h, 8 mi/order, reserve 15% "
                   "of tank (min 2 gal), low/high cases use mpg x 0.85 / x 1.10. Replace them with "
                   "your own numbers.")
    rng = driver.miles_until_reserve(level, tank, mpg, reserve_gal)
    cases = [("Low", rng["low"]), ("Base", rng["base"]), ("High", rng["high"])]
    shown = [(n, driver.round_miles(m)) for n, m in cases]
    rows = {"Miles": [], "Hours (approx.)": [],
            "Orders (approx.)": []}
    for _, m in shown:
        hours, orders = driver.to_hours_orders(m, mph, mpo)
        rows["Miles"].append(m)
        rows["Hours (approx.)"].append(hours)
        rows["Orders (approx.)"].append(orders)
    with results:
        with st.container(horizontal=True):  # one row even on a phone
            for name, m in cases:
                st.metric(f"{name} miles", f"{driver.round_miles(m)} mi", border=True)
        msg = driver.status(shown[0][1], shown[1][1])
        (st.warning if "refuel soon" in msg else st.info)(msg)
        st.dataframe(pd.DataFrame(rows, index=[n for n, _ in shown]).T, width="stretch",
                     alt="Miles, hours and orders until the reserve, for the low, base and high cases")
        st.caption("Miles are rounded to the nearest 5; hours and orders are approximate, "
                   "rounded down, and computed from the rounded miles shown. Estimates only: "
                   "use before or between orders, never while driving.")

    with st.expander("How this is calculated"):
        st.markdown(
            "**Base miles** = (gallons now - reserve) x mpg. The app does not predict orders "
            "and does not use DoorDash data: hours and orders are miles divided by the two "
            "rates you type.\n\n"
            "| Term | Plain-language meaning | Where the value comes from |\n"
            "|---|---|---|\n"
            "| Tank level in eighths | The needle read as 0 to 8. A reading is a band, not a "
            "point: an eighth of a 12-gal tank is 1.5 gal, and reading by eye is off by up to "
            "half an eighth (about 0.75 gal, about 22 miles at 30 mpg). | Our own "
            "arithmetic, not an external source. Real gauge error is larger and not measured. |\n"
            "| Reserve | Fuel you do not plan to use, so you are never stranded. Stored in "
            "gallons, shown in miles with your mpg. | Standard max(15%, 2 gal): "
            "assumption; the 2 gal floor sits near the range where cars' low-fuel light comes "
            "on (about 1.7 to 2.6 gal in owner's manuals, seen only through third-party "
            "mirrors). "
            "Conservative max(25% of tank, Standard reserve): the quarter-tank idea is "
            "commonly cited advice (AAA, fleet policies); not independently verified by us. "
            "The floor keeps small tanks at least at the Standard reserve. |\n"
            "| Base miles | (gallons now - reserve) x mpg: how far you can go before touching "
            "the reserve. | Formula. Assumes the gauge is linear (E means 0 gal) and that your tank size and mpg are right. |\n"
            "| Low case | One eighth less fuel than you read, and mpg x 0.85 (assumed: "
            "stop-and-go may use more fuel than the label; do not apply this on top of a city "
            "mpg figure you already picked in the car lookup, it would double-count). | "
            "-1 eighth is a design choice; 0.85 is an assumption. |\n"
            "| High case | Half an eighth more fuel and mpg x 1.10. | +0.5 eighth is derived "
            "from rounding arithmetic; 1.10 is an assumption. The range is skewed low on "
            "purpose because running short costs more than running long. |\n"
            "| Miles per hour of work | Miles you drive per hour online, including empty miles "
            "to the restaurant. Turns miles into hours. | Default 15: assumption, no measured "
            "source and no Florida data. Better: your odometer miles / online hours. |\n"
            "| Miles per order | All miles in a shift divided by orders done. Turns miles "
            "into approximate orders. | Default 8: assumption. Best: your own shift. |\n"
            "| Stacked errors | Low and high combine every worst case at once, so the range "
            "is wide on purpose. | Design choice. |\n")

# ---------------------------------------------------------------- AI explanation
with tab_ai:
    st.caption("What this tab answers: what the numbers mean in plain words. The text is "
               "checked against the calculation; press the button to generate it.")
    st.subheader("Plain-language explanation (numbers verified against the calculation)")
    if not ranked:
        st.info("Add at least one station price first.")
    else:
        best = ranked[0]
        gap = p_ref - best["price"]
        facts = {"best_station": best["name"], "best_price": round(best["price"], 2),
                 "p_ref": round(p_ref, 2), "gallons": gallons,
                 "best_saving_gross": round(gallons * gap, 2),
                 "extra_miles": round(best["extra_miles"], 1),
                 "extra_minutes": round(best["extra_minutes"], 1),
                 "best_net_benefit": round(best["net_benefit"], 2),
                 "breakeven_miles": round(max(breakeven_miles(
                     gallons, gap, best["price"], mpg, vot, speed), 0.0), 1) if gap > 0 else 0.0,
                 "value_of_time": vot}
        llm_provider = AnthropicProvider()
        if not llm_provider.available():
            st.caption("No ANTHROPIC_API_KEY: using the deterministic template instead of an LLM.")
            llm_provider = None
        if st.button("Explain this recommendation"):
            res = explain(facts, llm_provider)
            st.write(res["text"])
            st.success(f"Source: {res['source']} | traceable numbers on first try: "
                       f"{(res['traceable_first_try'] or 1.0):.0%} | attempts: {res['attempts']}"
                       + (" | fell back to template" if res["fallback_used"] else ""))
        st.markdown("**Ask a question about this recommendation**")
        q = st.text_input("e.g. What if I buy 15 gallons? Why is the best station best?")
        if st.button("Ask") and q.strip():
            res = ask("qa", facts, llm_provider, question=q)
            st.write(res["text"])
            st.caption(f"Source: {res['source']} | answers use only the facts below")
        with st.expander("Facts the LLM is allowed to use"):
            st.json(facts)
