# "The cheapest station is often the wrong one", measured — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the app's central claim into a measured result, and join the EIA price half to the stations half.

**Architecture:** One new pure module, `insight.py`, with no Streamlit and no network imports, built entirely on `detour.rank_stations` (already pure). `app.py` renders its output in the "Where to stop" tab: two lines under the recommendation and one new expander. No new API calls.

**Tech Stack:** Python 3.12, Streamlit 1.67, pytest, `streamlit.testing.v1.AppTest`.

**Spec:** `docs/superpowers/specs/2026-11-15-price-is-a-bad-proxy-design.md`

## Global Constraints

- Run tests with `.venv/bin/python -m pytest -q`; run the whole suite before every commit; keep commits small.
- `insight.py` imports nothing from Streamlit, `requests` or `providers`; it may import `detour`.
- No new API calls: every figure comes from stations already fetched.
- Every figure must come from the same `detour.rank_stations` the recommendation uses, so the panel can never contradict the headline above it.
- On screen, the grid result is labelled as **this route, these stations, today** — never as a general law — and the ranges used are stated.
- Station names are untrusted: pass any name through `recommend.md_escape` before Markdown.

## Review Focus

- No priced station at all (`ranked == []`): every function returns a neutral result and the UI shows nothing rather than dividing by zero.
- Exactly one priced station: cheapest and best are the same, so regret is 0 and the grid is unanimous; no "price misleads" claim may appear.
- Ties in price between two stations: the result must not depend on list order.
- A grid where the cheapest station wins every cell (a genuinely good cheap station): the share is 0% and the flip point is `None`, and the UI must not imply the opposite.
- `corridor_vs_state` with a missing or NaN state price: no crash, and the line is skipped.

---

### Task 1: The measurements

**Files:**
- Create: `insight.py`
- Test: `tests/test_insight.py`

**Interfaces:**
- Consumes: `detour.rank_stations(stations, p_ref, gallons, mpg, value_of_time, speed_mph)`, which returns stations sorted best-first with `net_benefit`, `extra_miles`, `extra_minutes` added; `recommend.cheapest(ranked)`.
- Produces:
  - `GALLONS_GRID = (5.0, 10.0, 15.0, 20.0)` and `VOT_GRID = (0.0, 10.0, 20.0, 30.0, 40.0)`
  - `regret(ranked) -> float` — dollars lost by filling at the cheapest instead of the best; `0.0` for an empty list or when they are the same station.
  - `price_rank_gap(ranked) -> int | None` — the 1-based position of the cheapest station in the net-benefit ranking; `1` means price picked the winner; `None` for an empty list.
  - `decision_grid(stations, p_ref, mpg, speed_mph, gallons_values=GALLONS_GRID, vot_values=VOT_GRID) -> dict` with keys `cells` (list of `{"gallons", "vot", "cheapest_won"}`), `total` (int), `misled` (int), `share` (float 0..1, the share where price picked the wrong station).
  - `flip_point(stations, p_ref, gallons, mpg, speed_mph, vot_values=VOT_GRID) -> float | None` — the lowest value of time in `vot_values` at which the best station stops being the one that wins at `vot_values[0]`; `None` when the winner never changes.

- [ ] **Step 1: Write the failing tests** in `tests/test_insight.py` (use the `sys.path.insert` header from `tests/test_recommend.py`). Build stations by hand with a helper `st(name, price, off_route_m)` so the detour is derived, not hand-fed:

```python
NEAR = {"id": "near", "name": "Near", "price": 3.55, "off_route_m": 100.0, "lat": 27.5, "lng": -82.5}
FAR_CHEAP = {"id": "far", "name": "Far Cheap", "price": 3.40, "off_route_m": 6000.0, "lat": 27.6, "lng": -82.6}
```

  - `test_regret_is_zero_when_the_cheapest_is_also_the_best`: one station, and a two-station set where the cheapest is also nearest.
  - `test_regret_is_the_net_benefit_difference`: with `[NEAR, FAR_CHEAP]` at `p_ref=3.70, gallons=10, mpg=27.2, vot=19, speed=45`, `regret(ranked) == pytest.approx(ranked[0]["net_benefit"] - rc.cheapest(ranked)["net_benefit"], abs=0.01)` and is `> 0`.
  - `test_regret_of_nothing_is_zero`: `regret([]) == 0.0`.
  - `test_price_rank_gap_is_one_when_price_picks_the_winner` and `test_price_rank_gap_counts_positions_when_it_does_not` (with `FAR_CHEAP` cheapest but ranked second, the gap is 2).
  - `test_price_rank_gap_of_nothing_is_none`.
  - `test_a_price_tie_does_not_depend_on_list_order`: two stations at the same price, different `off_route_m`; `regret` and `price_rank_gap` are equal for the list and its reverse.
  - `test_the_grid_counts_every_combination`: `decision_grid([NEAR, FAR_CHEAP], 3.70, 27.2, 45.0)` has `total == len(GALLONS_GRID) * len(VOT_GRID) == 20`, `len(cells) == 20`, and `misled == sum(not c["cheapest_won"] for c in cells)`, `share == misled / total`.
  - `test_a_cheap_near_station_wins_every_cell`: with only `NEAR`-like stations where the cheapest is also nearest, `share == 0.0` and every `cheapest_won` is `True`.
  - `test_time_value_decides_whether_a_far_cheap_station_wins`: in the `[NEAR, FAR_CHEAP]` grid, `FAR_CHEAP` wins at `vot == 0.0` and loses at `vot == 40.0`, so `0.0 < share < 1.0`.
  - `test_the_grid_of_nothing_is_empty_not_a_crash`: `decision_grid([], 3.70, 27.2, 45.0)` gives `total == 20`, `misled == 0`, `share == 0.0`.
  - `test_flip_point_finds_where_the_winner_changes`: with `[NEAR, FAR_CHEAP]` the flip point is a value in `VOT_GRID` and `> 0`.
  - `test_flip_point_is_none_when_the_winner_never_changes`: with one station, `None`.

- [ ] **Step 2: Run to verify they fail**
  Run: `.venv/bin/python -m pytest tests/test_insight.py -q`
  Expected: collection error, `ModuleNotFoundError: No module named 'insight'`.

- [ ] **Step 3: Implement `insight.py`.** Why: this is the whole measurement, and keeping it pure is what lets the grid run dozens of scenarios for free. Approach: `decision_grid` and `flip_point` call `detour.rank_stations` once per cell; for each cell compare `ranked[0]["id"]` with `recommend.cheapest(ranked)["id"]`. Module docstring states that the result describes one route's stations at one moment, not a general law.

- [ ] **Step 4: Run to verify they pass**
  Run: `.venv/bin/python -m pytest tests/test_insight.py -q` — Expected: all pass.

- [ ] **Step 5: Run the whole suite, then commit**
  Run: `.venv/bin/python -m pytest -q` (expected: 269 + new tests pass).
  ```bash
  git add insight.py tests/test_insight.py
  git commit -m "Add insight.py: how often price alone picks the wrong station"
  ```

---

### Task 2: The corridor against the state trend

**Files:**
- Modify: `insight.py`
- Test: `tests/test_insight.py`

**Interfaces:**
- Produces: `corridor_vs_state(route_prices, state_latest) -> dict | None` with keys `route_median` (float), `state_latest` (float), `diff_cents` (int, negative when the corridor is cheaper) and `verdict` (one of exactly `"cheaper than the state average"`, `"about the state average"`, `"dearer than the state average"`; "about" when within 5 cents). Returns `None` when there is no usable price on either side.

- [ ] **Step 1: Write the failing tests**
  - `test_a_cheap_corridor_is_reported_in_cents`: `corridor_vs_state([3.55, 3.60, 3.65], 3.73)` gives `route_median == 3.60`, `diff_cents == -13`, verdict `"cheaper than the state average"`.
  - `test_a_dear_corridor`: `corridor_vs_state([3.90, 3.95], 3.73)` has `diff_cents > 0` and the "dearer" verdict.
  - `test_within_five_cents_is_about_the_same`: `corridor_vs_state([3.76], 3.73)` gives the "about" verdict; `corridor_vs_state([3.79], 3.73)` gives "dearer" (6 cents).
  - `test_missing_prices_give_nothing_to_show`: `corridor_vs_state([], 3.73) is None`; `corridor_vs_state([3.6], None) is None`; `corridor_vs_state([3.6], float("nan")) is None`.

- [ ] **Step 2: Run to verify they fail** — Expected: `AttributeError: module 'insight' has no attribute 'corridor_vs_state'`.

- [ ] **Step 3: Implement `corridor_vs_state` in `insight.py`.** Use `statistics.median`; round `diff_cents` to the nearest cent.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Run the whole suite, then commit**
  ```bash
  git add insight.py tests/test_insight.py
  git commit -m "insight: compare the corridor's prices with the state weekly average"
  ```

---

### Task 3: Show it in "Where to stop"

**Files:**
- Modify: `app.py` (the `with tab_trip:` block, after the recommendation card)
- Test: `tests/test_where_tab.py`

**Interfaces:**
- Consumes: everything from Tasks 1 and 2; `series["florida"].dropna().iloc[-1]` for the latest state price, already in scope in `app.py`; `ranked`, `priced`, `p_ref`, `gallons`, `mpg`, `vot`, `speed`, all already in scope.

- [ ] **Step 1: Write the failing tests** in `tests/test_where_tab.py`, reusing `make_app` and `crafted()` (a far cheap station plus four near ones):
  - `test_the_regret_line_says_what_choosing_on_price_costs`: with `crafted()`, some caption matches `"would cost you $"`.
  - `test_the_corridor_is_compared_with_the_state_average`: a caption contains `"the state average"`.
  - `test_the_cheapest_question_expander_reports_a_share`: an expander labelled `"Is the cheapest station the best one?"` exists, and its text contains `"%"` and the phrase `"this route, these stations, today"`.
  - `test_the_panel_agrees_with_the_headline`: the station named in the success card is the same as `ranked[0]`, i.e. no second recommendation appears; assert the expander text does not contain `"Fill up at"`.
  - `test_no_priced_station_shows_no_insight_panel`: with the unpriced-station fixture, no expander labelled `"Is the cheapest station the best one?"` is rendered and `not app.exception`.

- [ ] **Step 2: Run to verify they fail** — Expected: the captions and the expander are missing.

- [ ] **Step 3: Render it in `app.py`.** Under the recommendation card, inside `if ranked:`, add two captions (regret, corridor-vs-state) and an expander `"Is the cheapest station the best one?"` holding: the share as a percentage, the price-rank gap, the flip point when there is one, and a line naming the grid ranges (`GALLONS_GRID`, `VOT_GRID`) and the words "this route, these stations, today". Skip the whole block when `ranked` is empty. Keep the math in `insight.py`: the tab only formats.

- [ ] **Step 4: Run to verify they pass**, then the whole suite.

- [ ] **Step 5: Look at it once** with `PROVIDER=fixture .venv/bin/streamlit run app.py`, open "Where to stop", and check the expander reads as a finding and not as a second recommendation. Then commit:
  ```bash
  git add app.py tests/test_where_tab.py
  git commit -m "Where to stop: show what choosing on price alone costs"
  ```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/report/project-report.md`

- [ ] **Step 1: Make the three edits.** README: one line under the google-mode list describing the new panel. CLAUDE.md: `insight.py` as a pure module built on `rank_stations`, and the rule that its figures must come from the same ranking as the recommendation. Report: replace the single Chevron anecdote in section 4.2 with the measured share, keeping the anecdote as the illustration.
- [ ] **Step 2: Run the whole suite and commit**
  ```bash
  git add README.md CLAUDE.md docs/report/project-report.md
  git commit -m "Docs: the measured result replaces the anecdote"
  ```

## Self-review

- **Spec coverage:** `regret`, `price_rank_gap`, `decision_grid`, `flip_point` (Task 1); `corridor_vs_state` (Task 2); placement, labelling and honesty rules (Task 3); report and docs (Task 4). No spec section is unassigned.
- **Review Focus:** empty list (Tasks 1, 2, 3), single station (Task 1), price tie (Task 1), cheap station that always wins (Task 1), missing state price (Task 2). Each has a named test.
- **Types:** `decision_grid` returns the same `cells`/`total`/`misled`/`share` keys everywhere it appears; `flip_point` and `price_rank_gap` return `None` rather than a sentinel number.
- **Proportion:** the plan is about the length of the spec; no function bodies are written out.
