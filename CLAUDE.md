# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Use the project venv (`.venv`, Python 3.12, Streamlit installed); the system `python` does not exist and system `python3` has no pytest. `AppTest` works against `app.py` (see `tests/test_driver_tab.py`); patch `providers.fueleconomy._get` so the sidebar car lookup never hits the network.

```bash
.venv/bin/python -m pytest -q                       # all tests (no network, no keys)
.venv/bin/python -m pytest tests/test_google_mode.py::test_detours_are_memoized_across_reruns -q   # one test
streamlit run app.py                                # fixture mode (default, synthetic data)
PROVIDER=google streamlit run app.py                # real data; app.py loads .env via envfile.py (shell vars win)
.venv/bin/python scripts/day1_check.py              # live API sanity check (1 Places + 1 Routes call, billed)
```

No linter or build step is configured. Run pytest before every commit; keep commits small.

## Architecture

Streamlit app (`app.py`, 7 tabs, including Driver mode) over a thin service layer. Flow: `config.py` (env flags) → `services.py` (switches on `PROVIDER`) → `providers/*` (HTTP + parsing, no UI) → pure-math modules (`geo.py`, `detour.py`, `forecast.py`, `driver.py`) → `app.py`.

- **Provider switch**: `fixture` (default, synthetic, needs no keys) or `google`. `services.load_trip` / `load_series` silently degrade to fixtures when keys are missing, and `app.py` falls back to a fixture trip on any exception. Always pass `provider=` explicitly in tests instead of relying on `config.PROVIDER`.
- **"Where" pipeline (google)**: Routes direct route → polyline decoded in `geo.py` → Places `searchNearby` around points sampled every 25 km (capped by `MAX_PLACES_CALLS`) → `corridor_filter` → `services.compute_detours` calls Routes with a `via` waypoint only for the `MAX_ROUTE_CALLS` cheapest priced stations, memoized 10 min in `_detour_memo` (so Streamlit reruns on slider moves do not bill again). Unpriced/failed stations keep the out-and-back estimate in `detour.rank_stations`; the `detour_source` field marks which one was used.
- **Car lookup**: `providers/fueleconomy.py` feeds the "Look up my car" expander under "Your car" (main area). Picking a car (or switching city/combined/highway) writes `st.session_state["mpg"]` once per `(car id, kind)`, tracked in `mpg_applied`, so the mpg slider (`key="mpg"`) stays hand-editable. Menus are cached 24 h; if the API is down (5 s timeout, or an unusable response, both `requests.RequestException`) the slider still works and the lookup is not retried for 60 s (`fe_down_until` in session state), so reruns never wait on it. Plug-in hybrids get a warning because their EPA figures blend electricity. In `AppTest`, address widgets by `key=`; the EPA figure and the reserve are segmented controls, so use `at.button_group(key=...)`.
- **Net benefit** (`detour.py`): `G*(p_ref - p_station) - extra_miles/mpg*p_station - vot*extra_min/60`; `p_ref` is the median price of stations within 800 m of the route, or a user-typed price. Users can edit any station price in the UI, and typed prices always work.
- **"When" pipeline**: EIA weekly series (`providers/eia.py`) cached to `data/eia_weekly.csv` and reused if the API is down; `forecast.py` does rolling-origin evaluation against a naive baseline (the honest result is often "nothing beats naive").
- **Driver mode** (`driver.py`, Driver tab): pure math, no UI or network. Miles until a fuel reserve as low/base/high, plus hours and orders. Its defaults (15 mi/h, 8 mi/order, 15%/2 gal reserve, mpg factors 0.85/1.10) are assumptions, documented per constant in the module docstring; Conservative reserve is max(25% of tank, Standard).
- **LLM layer** (`llm.py`): the model only phrases numbers that the code computed; `validate()` checks every number in the output against the `facts` dict, retries, then falls back to a deterministic template. A configured provider that fails returns `error` (bad key, no credits, network) and the app shows it, so a rejected key never looks like "no key given". Facts must be pre-rounded to the precision the text uses. Works without `ANTHROPIC_API_KEY` (template only).

## Constraints that shape changes

- Google Places content must not be stored (only place IDs) and must be shown on a Google map: never commit recorded Google responses; fixtures stay synthetic. `gmap.py` renders the map via the Maps JS key (`GOOGLE_MAPS_JS_KEY`, a separate referrer-restricted browser key; the server key `GOOGLE_API_KEY` must never reach the page). Station names are untrusted: `gmap.py` escapes JSON for `<script>` and uses `textContent`.
- Places with `fuelOptions` and Routes `TRAFFIC_AWARE` are paid SKUs; keep call counts capped in `config.py`.
- Keys come only from env vars / `st.secrets`; `.env` is gitignored.
- The user's location (`location.py`, an `st.components.v2` button around `navigator.geolocation`) is sensitive: keep it in session state only, never log or store it, never render free text sent by the browser (errors arrive as numeric codes mapped to fixed messages), and in tests replace `location.request_location` instead of using a real browser.

- Layout: the tabs open on the answer ("Where to stop") and end with the manual ("How to use"), each label carrying a Material icon; "Your car" and "Your trip" sit side by side in `st.columns` above the tabs (both `height="stretch"`), and the sidebar holds only the fill-up and time settings. The tab strip has `key="main_tabs"`, so a test opens a tab with `app.session_state["main_tabs"] = "<label with its :material/ icon>"` — without that, a guarded tab body never runs under `AppTest` (see `tests/test_tabs.py`). The `with tab_*` blocks stay in source order because "Where to stop" computes `p_ref`, `priced` and `ranked` that the EDA, Sensitivity and AI tabs read.
- Look: the light theme lives in `.streamlit/config.toml` (no injected CSS: the text color is dark enough that Streamlit's default 0.6-opacity captions pass AA); `tests/test_ui_style.py` reads those colors and enforces WCAG AA contrast, so change the palette there. mpg and tank are under "Your car" in the main area (visible on a phone); the sidebar holds gallons, value of time, speed, corridor and route.
- Streamlit style rules followed (from the skill bundled with Streamlit in `.venv/.../.agents/skills`): theme in config.toml, `st.segmented_control` instead of horizontal radios (AppTest: `at.button_group(key=...)`), `alt=` on tables and charts, and `st.tabs(..., on_change="rerun")` with `is_open(tab)` so only the open tab's heavy body runs (widgets stay outside the guard so their values persist).
- **Where to stop**: answer first. `recommend.py` (pure) turns the ranked stations into go / skip / none, and also reports the cheapest price on the route (`cheapest`, which is not always the stop that saves most) (a stop must save at least `min_saving`, default $0.50, an assumption, and always more than $0); `maps_links.py` (pure) builds Google Maps URLs (pin and "navigate via this station": no API key, no cost; only coordinates, place ids and typed addresses go into a URL, never a station name). Station names are untrusted: `recommend.md_escape` before any Markdown. In synthetic mode the link buttons are off because the stations are made up. Origin, destination and corridor are in the "Your trip" card; in google mode they sit in a form (`trip_form`), so a paid search starts on "Find stations", not on every keystroke. In `AppTest`, put `set_value` and the submit click in the same run.
- **Address suggestions** (`places_autocomplete.py`, an `st.components.v2` wrapper around Google's `PlaceAutocompleteElement`): runs in the browser with `GOOGLE_MAPS_JS_KEY`, which must allow the Places API (New); without the key, or if it fails, the trip card falls back to plain text boxes and says so. Everything the browser sends is untrusted (`parse_pick`: validated numbers, cleaned label, place id pattern, error codes mapped to fixed messages); picked labels are escaped before display. The trip is held as "pending" until "Find stations" (`trip_input.py`, `trip_applied` in session state), so typing, picking or using the location never starts a paid search. Load the Maps script with `callback=` (not `onload`) before calling `importLibrary`. In `AppTest`, replace `places_autocomplete.request_pick`.

