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
export $(grep -v '^#' .env | xargs)
PROVIDER=google streamlit run app.py
```

## Structure
- `app.py` UI (5 tabs) · `services.py` provider switch · `config.py` env flags
- `providers/` `eia.py`, `places.py`, `routes.py`, `fixtures.py` (synthetic)
- `geo.py` polyline + corridor filter · `detour.py` break-even math · `forecast.py` baselines
- `llm.py` provider interface, number validator, template fallback
- `tests/` unit tests

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
