# When to Fill Up Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the "When to fill up" page a clear verdict (wait / fill now / no clear signal) from a crude-oil + momentum model, and show the correlations behind it.

**Architecture:** `providers/eia.py` adds the WTI series as a `wti` column of the cached weekly frame; `forecast.py` (pure) adds the crude model to the existing rolling-origin test plus a signal, a track record and a month profile; `app.py` renders the verdict card and the "why" charts.

**Tech Stack:** Python 3.12, pandas, numpy, Streamlit 1.67, Plotly, pytest + AppTest.

**Spec:** `docs/superpowers/specs/2026-11-23-when-to-fill-up-signal-design.md`

## Global Constraints

- Crude: EIA `PET.RWTC.W` via `https://api.eia.gov/v2/seriesid/PET.RWTC.W`, $/bbl, week ending Friday; aligned to the following Monday (+3 days). Stored as column `wti` ($/bbl). Model uses $/gal = $/bbl ÷ 42.
- Model features at week t: `[1, p[t]−p[t−1], c[t]−c[t−1], c[t−1]−c[t−2]]`, target `p[t+h]−p[t]`, OLS refit on the expanding window, one fit per horizon.
- Verdict threshold `SIGNAL_THRESHOLD = 0.03` $/gal (an assumption, stated in the module docstring and on the page): ≤ −0.03 → "Wait a week if you can"; ≥ +0.03 → "Fill up now"; else "No clear signal".
- If `wti` is missing or all NaN, everything else still works and the page shows today's answer card unchanged.
- Synthetic retail values (`florida`, `us`) must not change.
- No `data-testid` CSS; widgets addressed by `key=`; `alt=` on every chart.

## Review Focus

- EIA returns WTI for a week the retail series lacks (or a Friday holiday): reindex leaves NaN, the model drops rows with NaN features instead of crashing → test in Task 2 (`test_crude_model_skips_weeks_without_crude`).
- Cached CSV from before this change has no `wti` column: page falls back to the old answer, no exception → test in Task 3 (`test_when_tab_without_crude_keeps_the_old_answer`).
- Too little history (fewer than `min_train` weeks with crude): signal returns `None`, page says no signal → test in Task 2 (`test_crude_signal_needs_enough_history`).
- Gallons shown in dollars use `gallons_used`; with 0 or missing gallons the dollar line is omitted, never "$-0.00" → covered by Task 3's formatting test.
- WTI fetch fails while retail succeeds: `load_live` still returns retail columns → test in Task 1 (`test_load_live_survives_a_crude_failure`).

---

### Task 1: Crude series in the data layer

**Files:**
- Modify: `providers/eia.py`, `providers/fixtures.py:40-50`, `services.py` (no logic change expected; check `load_series` passes the new column through)
- Refresh: `data/eia_weekly.csv` (re-fetch live once with the key; public data)
- Test: `tests/test_core.py`

**Interfaces:**
- Produces: `eia.fetch_wti(api_key, session=None) -> pd.Series` (index: Monday Timestamps, values $/bbl, ascending); `eia.load_live(api_key, ...)` returns columns `florida, us, wti` (`wti` absent if the crude fetch raised); `fixtures.synthetic_eia()` returns `florida, us, wti`.

- [ ] **Step 1: Failing tests** in `tests/test_core.py`:
  - `test_fetch_wti_shifts_friday_to_monday`: fake session returning `{"response": {"total": "2", "data": [{"period": "2026-11-13", "value": "90.78"}, {"period": "2026-11-06", "value": "95.97"}]}}`; assert index == `[2026-11-09, 2026-11-16]` and values `[95.97, 90.78]`.
  - `test_load_live_survives_a_crude_failure`: monkeypatch `eia.fetch_all` to return two rows and `eia.fetch_wti` to raise `requests.ConnectionError`; assert columns == `["florida", "us"]`.
  - `test_synthetic_crude_leads_the_synthetic_pump`: `df = fixtures.synthetic_eia()`; `lagged_corr(df.us, df.wti / 42, 2)[1] > 0.3`; and `df[["florida", "us"]]` equals the frame produced before this change (compare to a fixed checksum: `round(df.florida.sum(), 3)` taken before editing).
  - Update `test_series_csv_roundtrip` to expect `["florida", "us", "wti"]`.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_core.py -q` → the new tests fail.
- [ ] **Step 3: Implement.** `fetch_wti` pages the `seriesid` endpoint with `length=5000` using `parse_rows`, then shifts the index by `pd.Timedelta(days=3)`. `load_live` joins it with `reindex` on the retail index inside `try/except requests.RequestException`. In `synthetic_eia`, derive `wti` so it leads `us` by one week: `wti[t] = 42 * (us[t+1] − 0.85) + rng_crude.normal(0, 1.5)` from a separate `np.random.default_rng(seed + 1)` so retail values stay identical; last row uses `us[t]`.
- [ ] **Step 4:** tests pass; then run `PROVIDER=google` load once to rewrite `data/eia_weekly.csv` and check `tail` shows `wti` ≈ 95.97 on 2026-11-09 and 90.78 on 2026-11-16.
- [ ] **Step 5: Commit** "Data: weekly WTI crude from EIA, aligned to the retail Monday".

### Task 2: Crude model, signal, track record, month profile

**Files:**
- Modify: `forecast.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: aligned arrays of pump price `p` ($/gal) and crude `c` ($/gal).
- Produces:
  - `SIGNAL_THRESHOLD = 0.03`
  - `crude_model(crude) -> Callable[[np.ndarray, int], np.ndarray]` — a method usable in `rolling_origin_eval(methods={...})`; slices `crude[:len(train)]`, fits per horizon, returns `h` forecasts (levels). Falls back to `naive` when fewer than 30 complete feature rows.
  - `crude_signal(p, c, threshold=SIGNAL_THRESHOLD) -> dict | None` with keys `forecast` (next-week change, $/gal), `verdict` (`"wait" | "fill" | "none"`), `pump_change_1w`, `crude_change_2w` ($/gal); `None` if fewer than 104 complete weeks.
  - `signal_track_record(p, c, threshold=SIGNAL_THRESHOLD, min_train=104) -> dict` → `{"wait": {"n", "hit"}, "fill": {"n", "hit"}, "ratio_by_period": {"2005-2009": float, ...}}`; weekly step, h=1; `hit` = share of weeks the price moved in the called direction. Period labels built from an index passed as `dates=None` optional kwarg (omit the period dict when `dates` is None).
  - `monthly_change_profile(series: pd.Series) -> pd.Series` mean weekly change by calendar month (index 1–12).
- [ ] **Step 1: Failing tests:**
  - `test_crude_model_beats_naive_when_crude_leads`: synthetic `c` random walk, `p[t] = p[t−1] + 0.4*(c[t−1]−c[t−2]) + noise(0.005)`; `rolling_origin_eval(p, methods={"naive": naive, "crude": crude_model(c)})["crude"][1]["ratio_to_naive"] < 0.9`.
  - `test_crude_signal_says_wait_after_a_crude_drop`: same series with the last two crude changes forced to −0.20; `crude_signal(p, c)["verdict"] == "wait"` and `forecast < -0.03`.
  - `test_crude_signal_needs_enough_history`: 50 weeks → `None`.
  - `test_crude_model_skips_weeks_without_crude`: set 5 interior `c` values to NaN; `crude_signal` still returns a dict with finite `forecast`.
  - `test_track_record_counts_hits`: on the synthetic lead series, `rec["wait"]["n"] > 0` and `0.5 < rec["wait"]["hit"] <= 1`.
  - `test_monthly_profile_has_twelve_months`: on `fixtures.synthetic_eia()["florida"]`, index == `range(1, 13)`.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3: Implement** with `np.linalg.lstsq`; build the feature matrix vectorised once per fit; drop rows with any NaN.
- [ ] **Step 4:** run the file → pass; also run the live check: on `data/eia_weekly.csv`, `crude_signal` forecast ≈ −0.072 and `signal_track_record(...)["wait"]["hit"]` ≈ 0.87 (± 0.02; the plan's full-history check used step 1 from week 104).
- [ ] **Step 5: Commit** "Forecast: crude + momentum model, verdict and track record".

### Task 3: The When to fill up page

**Files:**
- Modify: `app.py:1011-1081`
- Test: `tests/test_tabs.py`

**Interfaces:**
- Consumes: Task 2 functions; `series` frame with optional `wti`; `gallons_used`.
- Produces: header texts `"Wait a week if you can"`, `"Fill up now"`, `"No clear signal: fill up when you need to"`; fallback headers unchanged when there is no crude.

- [ ] **Step 1: Failing tests** in `tests/test_tabs.py` (fixture mode):
  - `test_the_when_tab_gives_a_verdict`: first header ∈ the three verdict texts; metric labels ⊇ `{"Latest Florida price", "Expected change next week", "Crude oil, last 2 weeks", "Track record"}`.
  - `test_the_when_tab_shows_the_correlations`: a plotly chart whose alt text contains "correlation" and one containing "by month".
  - `test_the_forecast_table_includes_the_crude_model`: the "Method" table contains `"Crude oil + momentum"`; its value columns are average misses in cents (column names end with "(¢)").
  - `test_when_tab_without_crude_keeps_the_old_answer`: monkeypatch `fixtures.synthetic_eia` to drop `wti`; first header ∈ `("Timing the week doesn't pay", "A simple forecast helps a little")`.
  - Replace `test_the_when_tab_leads_with_its_answer` and `test_the_when_tab_compares_forecasts_against_the_naive_one` with the above.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3: Implement.** Cache the evaluation and track record with `st.cache_data` keyed on the series. Answer card (`key="answer_when"`): kicker, verdict header, one sentence with the forecast in ¢/gal and in dollars on `gallons_used` (omit dollars if `gallons_used <= 0`), the reason ("Crude fell X¢/gal over two weeks; pump prices follow crude within about a week"), the track record sentence, and the 3¢ assumption in a caption. Four metrics as named above. Then three bordered cards: (1) bar chart of `lagged_corr(florida, wti/42, 4)` with momentum r in its caption, alt "correlation of crude-oil changes with Florida pump changes by weeks of lag"; (2) bar chart of `monthly_change_profile`, alt "average weekly price change by month"; (3) the test table in cents (`mae*100`, one decimal), methods in `FORECAST_NAMES` plus `"crude": "Crude oil + momentum"`, and a caption with the per-period ratios. Keep the 5-year price chart.
- [ ] **Step 4:** full suite `.venv/bin/python -m pytest -q` → all pass; open the page in the `fuel-app` and `fuel-app-google` previews and check the verdict reads −7¢ / "Wait a week if you can" on live data.
- [ ] **Step 5: Commit** "When to fill up: a verdict from crude oil and momentum, with the correlations behind it".

### Task 4: Docs

**Files:**
- Modify: `README.md` (API table: WTI series), `CLAUDE.md` ("When" pipeline bullet), `docs/report/project-report.md` §4.1 and §3 (correlation), `services.py` docstring if it names columns.

- [ ] **Step 1:** Rewrite report §4.1 with the full-history numbers from the spec (0.91 / 0.93 / 0.98; 87% / 61%; period ratios) and keep the negative result for the four simple methods.
- [ ] **Step 2:** `.venv/bin/python -m pytest -q` → pass.
- [ ] **Step 3: Commit** "Docs: crude-oil signal in README, report and CLAUDE.md".
