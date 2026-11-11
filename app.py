"""Streamlit app: when and where to fill up, with detour break-even and a validated LLM
explanation. Runs without any key in fixture mode (synthetic data, clearly labelled)."""
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
from detour import (DEFAULTS, breakeven_miles, rank_stations, reference_price,
                    sensitivity_table)
from forecast import lagged_corr, rolling_origin_eval, treat_series
from gmap import build_map_html
from llm import AnthropicProvider, ask, explain
from providers import fueleconomy
from services import compute_detours, load_series, load_trip

st.set_page_config(page_title="Fuel Stop Planner", layout="wide")
st.title("⛽ Fuel Stop Planner")
st.caption("When to fill up (EIA weekly prices) and where (stations along your route, with "
           "detour break-even). Not financial advice: historical data and simple arithmetic.")

provider = config.PROVIDER
if provider == "fixture" or not (config.GOOGLE_API_KEY and config.EIA_API_KEY):
    st.warning("FIXTURE MODE: all prices, stations and the route are SYNTHETIC test data, "
               "not real. Set PROVIDER=google plus EIA_API_KEY and GOOGLE_API_KEY for live data.")

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


st.session_state.setdefault("mpg", DEFAULTS["mpg"])
with st.sidebar:
    st.header("Your car and time")
    gallons = st.slider("Gallons to buy", 3.0, 25.0, DEFAULTS["gallons"], 0.5)
    with st.expander("Look up my car (fueleconomy.gov)"):
        try:
            car = car_lookup()
        except requests.RequestException:
            car = None
            st.caption("fueleconomy.gov is not reachable: set the mpg by hand below.")
        if car and fueleconomy.is_electric(car):
            st.warning("Electric car: EPA reports MPGe, not gallons. Set mpg by hand if you use "
                       "a gas range extender or hybrid figures.")
        elif car:
            kind = st.radio("Which EPA figure", fueleconomy.KINDS, horizontal=True,
                            help="city: stop-and-go (closest to delivery driving). highway: steady "
                                 "speed. combined: 55% city / 45% highway.")
            looked_up = fueleconomy.mpg_for(car, kind)
            if looked_up and st.session_state.get("mpg_applied") != (car["id"], kind):
                st.session_state["mpg"] = min(60.0, max(10.0, round(looked_up * 2) / 2))
                st.session_state["mpg_applied"] = (car["id"], kind)
            st.caption(f"{car['label']}: city {car['city']}, combined {car['combined']}, "
                       f"highway {car['highway']} mpg (EPA label values; real mileage varies).")
    mpg = st.slider("Fuel economy (mpg)", 10.0, 60.0, step=0.5, key="mpg")
    tank = st.number_input("Tank size (gallons)", 5.0, 40.0, 13.0, 0.5,
                           key="tank")
    st.caption(f"About {tank * mpg:.0f} miles on a full tank at this mpg.")
    vot = st.slider("Value of your time ($/h)", 0.0, 60.0, DEFAULTS["value_of_time"], 1.0)
    speed = st.slider("Average speed on detours (mph)", 20.0, 70.0, DEFAULTS["speed_mph"], 1.0)
    buffer_km = st.slider("Search corridor width (km each side)", 1.0, 10.0, 3.0, 0.5)
    origin = destination = None
    if provider == "google" and config.GOOGLE_API_KEY:
        origin = st.text_input("Origin", "Sarasota, FL")
        destination = st.text_input("Destination", "Orlando, FL")


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
     "AI"])

# ---------------------------------------------------------------- how to use
with tab_help:
    st.subheader("How to use this app")
    st.markdown(
        "1. **Where to stop**: set gallons, fuel economy and the value of your time in the "
        "sidebar. Edit any station price in the table (typed prices always work). The app "
        "ranks stations by *net benefit* = fuel saved minus the extra fuel and time of the "
        "detour.\n"
        "2. **When to fill up**: weekly Florida/U.S. prices and a check of whether any simple "
        "forecast beats 'next week = this week'.\n"
        "3. **EDA**: missing-data and outlier treatment (before/after), distributions and "
        "correlations of the EIA series, plus an AI summary.\n"
        "4. **Sensitivity**: how many extra miles a detour is worth for each price gap.\n"
        "5. **AI**: a plain-language explanation and a question box. Every number the model "
        "writes is checked against the calculation; if it cannot be traced, a template is used.\n\n"
        "Data: EIA Open Data API v2 (weekly retail gasoline, JSON) and Google Places/Routes "
        "(stations, route). Without keys the app runs on **synthetic** data and says so. "
        "Not financial advice.")

# ---------------------------------------------------------------- where to stop
with tab_trip:
    stations = trip["stations"]
    st.subheader("Station prices (edit any price: typed prices always work)")
    table = pd.DataFrame([{"id": s["id"], "name": s["name"], "price": s["price"]}
                          for s in stations])
    edited = st.data_editor(table, disabled=["id", "name"], hide_index=True,
                            width="stretch", key="price_editor")
    price_by_id = dict(zip(edited["id"], edited["price"]))
    priced = [{**s, "price": (None if pd.isna(price_by_id.get(s["id"])) else
                              float(price_by_id[s["id"]]))} for s in stations]
    p_ref = reference_price(priced)
    if p_ref is None:
        p_ref = st.number_input("No priced station is on the route: type the price you would "
                                "pay on the route ($/gal)", 2.0, 6.0, 3.6, 0.01)
    detoured = compute_detours({**trip, "stations": priced})
    ranked = rank_stations(detoured, p_ref, gallons, mpg, vot, speed)
    if not ranked:
        st.info("No station has a price yet. Type prices in the table above.")
    else:
        best = ranked[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Reference price on route", f"${p_ref:.2f}/gal")
        c2.metric("Best station", best["name"], f"${best['price']:.2f}/gal")
        c3.metric("Net benefit", f"${best['net_benefit']:.2f}",
                  "detour pays off" if best["net_benefit"] > 0 else "do not detour")
        cols = ["name", "price", "off_route_m", "extra_miles", "extra_minutes", "net_benefit"]
        if trip["mode"] == "google":
            cols.append("detour_source")  # routes = exact Routes API, approx = out-and-back
        show = pd.DataFrame(ranked)[cols]
        st.dataframe(show.head(10), hide_index=True, width="stretch")
        if trip["mode"] == "fixture":
            path = np.array(trip["path"])
            fig = go.Figure()
            fig.add_trace(go.Scattermap(lat=path[:, 0], lon=path[:, 1], mode="lines",
                                        line=dict(width=4, color="#1f77b4"), name="Route"))
            fig.add_trace(go.Scattermap(
                lat=[r["lat"] for r in ranked], lon=[r["lng"] for r in ranked], mode="markers",
                marker=dict(size=11, color=[r["net_benefit"] for r in ranked],
                            colorscale="RdYlGn", cmid=0, showscale=True,
                            colorbar=dict(title="Net $")),
                text=[f"{r['name']}: ${r['price']:.2f} (net ${r['net_benefit']:.2f})"
                      for r in ranked], name="Stations"))
            fig.update_layout(map=dict(style="open-street-map",
                                       center=dict(lat=path[:, 0].mean(), lon=path[:, 1].mean()),
                                       zoom=7.2), height=520, margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig, width="stretch")
        elif config.GOOGLE_MAPS_JS_KEY:
            components.html(build_map_html(config.GOOGLE_MAPS_JS_KEY, trip["path"], ranked),
                            height=520)
        else:
            st.warning("Google mode: Places content must be shown on a Google map. Set "
                       "GOOGLE_MAPS_JS_KEY (a referrer-restricted browser key) to show it; "
                       "until then only the table is shown.")

# ---------------------------------------------------------------- when to fill up
with tab_when:
    st.subheader(f"Weekly regular gasoline, $/gal ({series_label})")
    s_fl = series["florida"].dropna()
    st.line_chart(series[["florida", "us"]].dropna().tail(260))
    st.markdown("**Does any simple model beat 'next week = this week'?** Rolling-origin "
                "evaluation on the Florida series, error shown as a ratio to the naive forecast "
                "(below 1.00 beats naive).")
    ev = rolling_origin_eval(s_fl.to_numpy())
    rows = [{"method": m, **{f"h={h}": ev[m][h]["ratio_to_naive"] for h in ev[m]}} for m in ev]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption("If nothing beats naive, the honest advice is: prices move too randomly to time "
               "the week; use the station comparison instead.")

# ---------------------------------------------------------------- EDA
with tab_eda:
    st.subheader("Exploratory analysis of weekly gasoline prices")
    c_a, c_b = st.columns(2)
    z_thr = c_a.slider("Outlier threshold (SD of weekly change)", 2.0, 5.0, 3.0, 0.5)
    treat = c_b.radio("Outlier treatment", ["keep", "interpolate"], horizontal=True,
                      help="Price shocks can be real events; 'interpolate' replaces them by "
                           "the average of neighbouring weeks.")
    raw = series["florida"]
    clean, rep = treat_series(raw, z=z_thr, outliers=treat)
    d = series.copy()
    d["florida"] = clean
    d = d.interpolate(limit_direction="both")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Weeks", f"{len(d):,}")
    c2.metric("Missing weeks filled", rep["n_missing_filled"])
    c3.metric("Price shocks flagged", rep["n_outliers_flagged"])
    c4.metric("Latest Florida", f"${clean.iloc[-1]:.2f}")
    st.caption(f"Treatment: missing weeks by linear interpolation; outliers: {treat}. Mean "
               f"{rep['mean_before']:.3f} -> {rep['mean_after']:.3f}, SD "
               f"{rep['std_before']:.3f} -> {rep['std_after']:.3f} (before -> after).")
    comp = pd.DataFrame({"raw": raw, "treated": clean})
    st.line_chart(comp.tail(520))
    st.dataframe(d.describe().T.round(3))
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
                           title="Lead-lag correlation"), width="stretch")
    st.plotly_chart(px.histogram(ch, nbins=60, title="Distribution of weekly changes ($/gal)"),
                    width="stretch")
    prices = [s["price"] for s in priced if s["price"]]
    if prices:
        st.plotly_chart(px.histogram(prices, nbins=20, title="Station price spread on this route"),
                        width="stretch")
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
    st.subheader("How far is a detour worth it?")
    st.write("Break-even extra miles for a price gap (columns, $/gal) and value of time "
             "(rows, $/h), using your gallons, mpg and speed.")
    ref = p_ref if p_ref else 3.6
    sens = sensitivity_table(gallons, ref, mpg, speed)
    df_s = pd.DataFrame(sens).T
    df_s.index.name = "value of time $/h"
    st.dataframe(df_s)
    st.plotly_chart(px.imshow(df_s, text_auto=True, aspect="auto",
                              labels=dict(x="price gap $/gal", y="value of time $/h",
                                          color="miles")), width="stretch")

# ---------------------------------------------------------------- driver mode
with tab_driver:
    st.subheader("Driver mode: how far until I must refuel?")
    c1, c2 = st.columns(2)
    level = c1.select_slider("Tank level (eighths, read from the gauge)",
                             options=list(range(0, 9)), value=4, key="driver_level")
    reserve_label = c2.radio("Reserve", ["Standard", "Conservative (1/4 tank)"],
                             key="driver_reserve_mode", horizontal=True)
    st.caption(f"Using tank {tank} gal and {mpg} mpg from the sidebar: check both.")
    c3, c4 = st.columns(2)
    mph = c3.number_input("Miles per hour of work", min_value=1.0,
                          value=driver.DEFAULT_MILES_PER_HOUR, step=1.0, key="driver_mph")
    mpo = c4.number_input("Miles per order", min_value=1.0,
                          value=driver.DEFAULT_MILES_PER_ORDER, step=1.0, key="driver_mpo")
    st.caption("Defaults are assumptions, not measured values: 15 mi/h, 8 mi/order, reserve 15% "
               "of tank (min 2 gal), low/high cases use mpg x 0.85 / x 1.10. Replace them with "
               "your own numbers.")
    reserve_gal = driver.reserve_gallons(
        tank, "conservative" if reserve_label.startswith("Conservative") else "standard")
    st.caption(f"Reserve: {reserve_gal:.1f} gal (about {reserve_gal * mpg:.0f} miles at your mpg)")

    rng = driver.miles_until_reserve(level, tank, mpg, reserve_gal)
    cases = [("Low", rng["low"]), ("Base", rng["base"]), ("High", rng["high"])]
    for col, (name, m) in zip(st.columns(3), cases):
        col.metric(f"{name} miles", f"{driver.round_miles(m)} mi")
    shown = [(n, driver.round_miles(m)) for n, m in cases]
    rows = {"Miles": [], "Hours of work (approx., rounded down)": [],
            "Orders (approx., rounded down)": []}
    for _, m in shown:
        hours, orders = driver.to_hours_orders(m, mph, mpo)
        rows["Miles"].append(m)
        rows["Hours of work (approx., rounded down)"].append(hours)
        rows["Orders (approx., rounded down)"].append(orders)
    st.dataframe(pd.DataFrame(rows, index=[n for n, _ in shown]).T, width="stretch")
    st.caption("Miles are rounded to the nearest 5; hours and orders are computed from the "
               "rounded miles shown.")

    msg = driver.status(shown[0][1], shown[1][1])
    (st.warning if "refuel soon" in msg else st.info)(msg)
    st.caption("Estimates only. Use before or between orders, never while driving.")

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
