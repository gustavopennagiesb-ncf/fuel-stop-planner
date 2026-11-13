# Fuel Stop Planner

Streamlit app that helps a driver decide **when** to fill up (EIA weekly gasoline prices,
rolling-origin forecast evaluation against a naive baseline) and **where** to stop on a route
(stations along the corridor, detour break-even using fuel economy and the value of time),
with an **LLM explanation whose numbers are checked against the calculation**.

Not financial advice: historical data and arithmetic only.

## Data sources
| Need | Source | Status |
|---|---|---|
| Weekly regular gasoline, Florida and U.S. | EIA Open Data API v2 (`petroleum/pri/gnd`), free key | route/codes from secondary sources, verify day 1 |
| Station prices | Google Places API (New) `fuelOptions` | US coverage UNVERIFIED, verify day 1 |
| Route and detour | Google Routes API v2 (`TRAFFIC_AWARE`) | request shape from docs, not yet run |
| Car mpg | fueleconomy.gov web services (EPA/DOE), free, no key | city / combined / highway label values; electric cars report MPGe, so they are flagged |
| LLM | Anthropic API (or template fallback) | optional key |

## Run without any key (fixture mode, synthetic data)
```bash
pip install -r requirements.txt
streamlit run app.py        # PROVIDER=fixture by default
pytest
```

## Run with real data
```bash
cp .env.example .env        # fill keys; never commit .env
PROVIDER=google streamlit run app.py   # app.py reads .env itself; shell variables win
```

In `google` mode (day-1 check passed: Places returns U.S. fuel prices):
- Stations come from Places around points sampled along the Routes polyline (at most
  `MAX_PLACES_CALLS` calls), filtered to the corridor.
- Exact detours use Routes with a `via` waypoint for the `MAX_ROUTE_CALLS` cheapest priced
  stations, memoized for 10 minutes so moving a slider does not bill again. Other stations (or a
  failed call) use the out-and-back estimate; the `detour_source` column says which.
- Stations are drawn on a Google map when `GOOGLE_MAPS_JS_KEY` is set (required by Google's terms);
  without it only the table is shown. Missing `GOOGLE_API_KEY` falls back to fixture data with a
  warning, and any load error falls back to fixtures instead of crashing.
- Where to stop answers first ("Fill up at ...", or "No detour pays off enough"), the cheapest price on the route (with what its detour does to the saving), with buttons that
  open the station and a "navigate via this station" route in Google Maps (free links, no key).
  Origin and destination are under "Your trip" with Google's address suggestions (type, then pick
  an option, like Google Maps; plain text boxes if the browser key cannot use the Places API); a paid
  search starts only when you press "Find stations".
- "Use my current location" (under Origin, google mode only) asks the browser for your position
  and uses it as the route origin. The position stays in the session, is rounded to about 10 m,
  is never written to a file or a log, and only reaches Google in the Routes request made with
  your own key. If permission is denied, the typed address keeps working.

## Structure
- `app.py` UI (7 tabs, including Driver mode; "Your car" at the top, trip settings in the sidebar) · `.streamlit/config.toml` light theme · `services.py` provider switch · `config.py` env flags
- `providers/` `eia.py`, `places.py`, `routes.py`, `fixtures.py` (synthetic)
- `gmap.py` Google Maps JS embed · `geo.py` polyline + corridor filter · `detour.py` break-even math · `forecast.py` baselines · `driver.py` driver-mode math
- `llm.py` provider interface, number validator, template fallback
- `tests/` unit tests

## Driver mode
The Driver mode tab answers one question: how many miles can I drive before the tank hits my
reserve? It shows a low, base and high case, each with approximate hours of work and orders. It
does not predict orders and uses no DoorDash data. Its defaults are assumptions, not measured
values: 15 miles per hour of work, 8 miles per order, a reserve of 15% of the tank (at least
2 gal; Conservative uses the larger of 25% of the tank and the Standard reserve), and mpg factors of 0.85 (low) and 1.10 (high). Miles per hour,
miles per order, tank level, reserve mode, tank size and mpg are editable in the UI; the mpg
factors are constants in `driver.py`.

## Rules that shape the code
- Google Places content may not be stored (only place IDs): fixtures are synthetic, and no
  recorded Google responses go in the repo.
- Places/Routes content must be shown on a Google map. In `google` mode the Maps JS embed is
  still TODO, so only the table is shown there.
- Keys come from environment variables or `st.secrets`, never from the repo.

## Day-1 checklist (run on your own machine)
1. `EIA_API_KEY`: call `/v2/petroleum/pri/gnd/facet/duoarea` and confirm `SFL`, `NUS`, `YMIA`.
2. One Places `searchNearby` around Sarasota with `places.fuelOptions` in the field mask:
   do US stations return `fuelPrices`? (decides the "where" half)
3. One Routes `computeRoutes` call Sarasota to Orlando; compare with `providers/routes.py`.
4. Confirm the assignment accepts running locally (it says hosted locally or via Streamlit Cloud).

## Day-1 script
`python scripts/day1_check.py` runs the checks above against the live APIs (1 Places call, 1 Routes
call) and prints PASS / WARN / FAIL without ever printing a key.

## Team
Author: Gustavo Penna (individual project).

## Data storage
Live EIA series are saved to `data/eia_weekly.csv` (public U.S. government data, OK to commit) and
reused if the API is unreachable. Google Places/Routes responses are never stored.
