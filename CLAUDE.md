# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Use the project venv (`.venv`, Python 3.9); the system `python` does not exist and system `python3` has no pytest. Streamlit is not installed in `.venv`, so `app.py` cannot be run or `AppTest`ed there without installing `requirements.txt` first.

```bash
.venv/bin/python -m pytest -q                       # all tests (no network, no keys)
.venv/bin/python -m pytest tests/test_google_mode.py::test_detours_are_memoized_across_reruns -q   # one test
streamlit run app.py                                # fixture mode (default, synthetic data)
PROVIDER=google streamlit run app.py                # real data; app.py loads .env via envfile.py (shell vars win)
.venv/bin/python scripts/day1_check.py              # live API sanity check (1 Places + 1 Routes call, billed)
```

No linter or build step is configured. Run pytest before every commit; keep commits small.

## Architecture

Streamlit app (`app.py`, 6 tabs) over a thin service layer. Flow: `config.py` (env flags) → `services.py` (switches on `PROVIDER`) → `providers/*` (HTTP + parsing, no UI) → pure-math modules (`geo.py`, `detour.py`, `forecast.py`) → `app.py`.

- **Provider switch**: `fixture` (default, synthetic, needs no keys) or `google`. `services.load_trip` / `load_series` silently degrade to fixtures when keys are missing, and `app.py` falls back to a fixture trip on any exception. Always pass `provider=` explicitly in tests instead of relying on `config.PROVIDER`.
- **"Where" pipeline (google)**: Routes direct route → polyline decoded in `geo.py` → Places `searchNearby` around points sampled every 25 km (capped by `MAX_PLACES_CALLS`) → `corridor_filter` → `services.compute_detours` calls Routes with a `via` waypoint only for the `MAX_ROUTE_CALLS` cheapest priced stations, memoized 10 min in `_detour_memo` (so Streamlit reruns on slider moves do not bill again). Unpriced/failed stations keep the out-and-back estimate in `detour.rank_stations`; the `detour_source` field marks which one was used.
- **Net benefit** (`detour.py`): `G*(p_ref - p_station) - extra_miles/mpg*p_station - vot*extra_min/60`; `p_ref` is the median price of stations within 800 m of the route, or a user-typed price. Users can edit any station price in the UI, and typed prices always work.
- **"When" pipeline**: EIA weekly series (`providers/eia.py`) cached to `data/eia_weekly.csv` and reused if the API is down; `forecast.py` does rolling-origin evaluation against a naive baseline (the honest result is often "nothing beats naive").
- **LLM layer** (`llm.py`): the model only phrases numbers that the code computed; `validate()` checks every number in the output against the `facts` dict, retries, then falls back to a deterministic template. Facts must be pre-rounded to the precision the text uses. Works without `ANTHROPIC_API_KEY` (template only).

## Constraints that shape changes

- Google Places content must not be stored (only place IDs) and must be shown on a Google map: never commit recorded Google responses; fixtures stay synthetic. `gmap.py` renders the map via the Maps JS key (`GOOGLE_MAPS_JS_KEY`, a separate referrer-restricted browser key; the server key `GOOGLE_API_KEY` must never reach the page). Station names are untrusted: `gmap.py` escapes JSON for `<script>` and uses `textContent`.
- Places with `fuelOptions` and Routes `TRAFFIC_AWARE` are paid SKUs; keep call counts capped in `config.py`.
- Keys come only from env vars / `st.secrets`; `.env` is gitignored.
