# Fuel Stop Planner

Streamlit app that helps a driver decide **when** to fill up (EIA weekly gasoline prices and WTI crude oil,
a crude-oil + momentum forecast checked by rolling-origin evaluation against a "no change" baseline) and **where** to stop on a route
(stations along the corridor, detour break-even using fuel economy and the value of time),
whether a stop is **needed at all** and how many gallons to buy given the fuel already in the
tank, with an **LLM assistant whose numbers are checked against the calculation**.

Not financial advice: historical data and arithmetic only.

## Data sources

Every API below returns **JSON** over HTTPS. The retrieval and parsing live in `providers/`
(one module per API, no UI code); `services.py` chooses between them with `PROVIDER`.

| Need | Source | Endpoint | Key | What is read |
|---|---|---|---|---|
| Weekly regular gasoline, Florida and U.S. | EIA Open Data API v2 | `GET /v2/petroleum/pri/gnd/data/` | free key | `response.total`, `response.data[]` with `period` and `value` |
| Weekly WTI crude oil spot, $/bbl | EIA Open Data API v2 | `GET /v2/seriesid/PET.RWTC.W` | same free key | `response.data[]` with `period` and `value`; the Friday value is aligned to the following Monday and joined to the retail series |
| Station prices | Google Places API (New) | `POST /v1/places:searchNearby` | server key | `places[]` with `id`, `displayName.text`, `location.latitude/longitude`, `fuelOptions.fuelPrices[]` |
| Route and detour | Google Routes API v2 | `POST /directions/v2:computeRoutes` | server key | `routes[0]` with `distanceMeters`, `duration`, `polyline.encodedPolyline` |
| Car mpg | fueleconomy.gov web services (EPA/DOE) | `GET /ws/rest/vehicle/...` | none | `menuItem[]` cascade, then `city08`, `comb08`, `highway08`, `fuelType` |
| Plain-language text | Groq (OpenAI-compatible), else Anthropic Messages API | `POST /openai/v1/chat/completions`, `POST /v1/messages` | optional key (Groq has a free tier) | the model's text, with every number checked against the computed facts |

Both Google calls send a **field mask**, so only the fields above are requested and billed
(`providers/places.py` and `providers/routes.py`).

### Shape of the data inside the app

| Structure | Where | Columns / fields |
|---|---|---|
| `pandas.DataFrame`, weekly `DatetimeIndex` | the EIA series | `florida`, `us` (float, $/gal), `wti` (float, $/bbl; absent if the crude fetch failed) |
| `list[dict]` | one station | `id`, `name`, `lat`, `lng`, `price`, `price_updated`, `synthetic` |
| `list[dict]` | a ranked station (adds) | `extra_miles`, `extra_minutes`, `net_benefit`, `detour_source` |
| `list[tuple]` | the route | `(lat, lng)` points decoded from the polyline in `geo.py` |

### Stored data

`data/eia_weekly.csv` (in this repository) is the weekly EIA series: four columns, `period`,
`florida`, `us`, `wti` (WTI crude, $/bbl). It is public U.S. government data. The app refreshes it when the API answers
and falls back to it when the API is down. Google Places and Routes responses are **never**
written to disk: Google's terms allow storing only place IDs.

## Run without any key (fixture mode, synthetic data)

Needs **Python 3.11 or newer** (built and tested on 3.12; the tests read the theme with
`tomllib`, which arrived in 3.11). Dependency versions are pinned in `requirements.txt`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py        # PROVIDER=fixture by default
.venv/bin/python -m pytest -q         # 400 tests, no network and no keys
```

The app opens empty: choose the route (press *Find stations*) and your car to see the
recommendation; *Start over* clears them. `FUEL_DEMO_TRIP=1` opens with the demo trip
(Sarasota → Orlando, 27.2 mpg) already chosen.

## Run with real data
```bash
cp .env.example .env        # fill keys; never commit .env
PROVIDER=google streamlit run app.py   # app.py reads .env itself; shell variables win
```

In `google` mode (day-1 check passed: Places returns U.S. fuel prices):
- Stations come from Places around points sampled along the Routes polyline (at most
  `MAX_PLACES_CALLS` calls), filtered to the corridor.
- Detours are measured with Routes on a 10 km stretch of road around each station (5 km before
  and after, with and without the station as a `via` waypoint; 2 calls each, without traffic).
  A `via` on the whole trip is not used: it can make Routes pick another road for the whole trip.
  Every stop means leaving the road, so the smallest measured detour (the station closest to the
  route is always measured) is subtracted. Up to `MAX_ROUTE_CALLS` stations are measured, always
  the best-ranked one not yet measured, re-ranking after each, so the top of the ranking is
  measured; results are memoized for 10 minutes so moving a slider does not bill again. Other
  stations (or a failed call) use the out-and-back estimate; the `detour_source` column says which.
- Stations are drawn on a Google map when `GOOGLE_MAPS_JS_KEY` is set (required by Google's terms);
  without it only the table is shown. Missing `GOOGLE_API_KEY` falls back to fixture data with a
  warning, and any load error falls back to fixtures instead of crashing.
- Where to stop answers first ("Fill up at ...", or "No detour pays off enough"), the cheapest price on the route (with what its detour does to the saving), with buttons that
  open the station and a "navigate via this station" route in Google Maps (free links, no key).
  Origin and destination are under "Where are you going?" with Google's address suggestions (type, then pick
  an option, like Google Maps; plain text boxes if the browser key cannot use the Places API); a paid
  search starts only when you press "Find stations".
- Under the recommendation, "Is the cheapest station the best one?" shows what choosing on price
  alone costs (regret), how that changes across fill sizes and values of time, and how the
  route's prices compare with the latest weekly state average. It re-ranks the same stations, so it
  makes no extra API calls.
- "How much fuel do you have?" (fuel gauge or miles to the reserve light) turns the answer into
  "No stop needed" or a stop with "buy N gal": only stations reachable before the reserve are
  ranked, a station cheaper than the route is a fill-up and any other the minimum to arrive with
  the reserve (`fuelplan.py`, pure math; no extra API call). "Set gallons by hand" in the sidebar
  overrides the gallons but keeps the reachability check.
- "Use my current location" (under Origin, google mode only) asks the browser for your position
  and uses it as the route origin. The position stays in the session, is rounded to about 10 m,
  is never written to a file or a log, and only reaches Google in the Routes request made with
  your own key. If permission is denied, the typed address keeps working.

## Structure
- `app.py` UI (sidebar navigation, answer first: Where to stop · When to fill up · Sensitivity · EDA · How to use; a "Start here" strip of trip, car and fuel chips edited in popovers; an AI assistant button on every page; fill-up and time settings in the sidebar) · `.streamlit/config.toml` dark theme · `services.py` provider switch · `config.py` env flags
- `providers/` `eia.py`, `places.py`, `routes.py`, `fixtures.py` (synthetic)
- `gmap.py` Google Maps JS embed · `geo.py` polyline + corridor filter · `detour.py` break-even math · `forecast.py` baselines, crude + momentum model, verdict and track record · `fuelplan.py` trip fuel, reachability and gallons per station · `driver.py` fuel reserve
- `llm.py` provider interface, number validator, template fallback
- `tests/` unit tests

## Rules that shape the code
- Google Places content may not be stored (only place IDs): fixtures are synthetic, and no
  recorded Google responses go in the repo.
- Places/Routes content must be shown on a Google map: `gmap.py` builds that map and `app.py`
  renders it with `st.iframe`. Without `GOOGLE_MAPS_JS_KEY` the stations are not drawn and the
  app says so.
- Keys come from environment variables, a local `.env`, or Streamlit secrets
  (`envfile.load_dotenv()` and `envfile.load_secrets()` run before `config.py` reads the
  environment, so the same code works locally and on Streamlit Cloud). A real shell
  variable wins over `.env`, which wins over secrets. Nothing is read from the repo.

## Day-1 checklist (run on your own machine)
1. `EIA_API_KEY`: call `/v2/petroleum/pri/gnd/facet/duoarea` and confirm `SFL`, `NUS`, `YMIA`.
2. One Places `searchNearby` around Sarasota with `places.fuelOptions` in the field mask:
   do US stations return `fuelPrices`? (decides the "where" half)
3. One Routes `computeRoutes` call Sarasota to Orlando; compare with `providers/routes.py`.
4. Confirm the assignment accepts running locally (it says hosted locally or via Streamlit Cloud).

## Day-1 script
`python scripts/day1_check.py` runs the checks above against the live APIs (1 Places call, 1 Routes
call) and prints PASS / WARN / FAIL without ever printing a key.

## Report

`docs/report/project-report.md` is the project report (objectives, method, results, screenshots).
Screenshots of the live app are in `docs/screenshots/`.

## Team
Author: Gustavo Penna (individual project).

