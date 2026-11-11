# Driver Mode v1 (miles until refuel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Driver mode" tab that tells a gig driver, as a low/base/high range, how many miles, hours of work and orders remain before the fuel reserve is reached.

**Architecture:** A new pure-math module `driver.py` (no Streamlit, no network) holds the formulas and constants. `app.py` gets one new tab that collects inputs, calls `driver.py` and renders. The tab reuses the sidebar's `tank` and `mpg`. Nothing else in the app changes.

**Tech Stack:** Python 3.12 (`.venv`), Streamlit 1.67, pytest, `streamlit.testing.v1.AppTest`.

**Spec:** `docs/superpowers/specs/2026-11-11-driver-range-design.md`

## Global Constraints

- Run tests with `.venv/bin/python -m pytest -q`; run the whole suite before every commit; keep commits small (CLAUDE.md).
- `driver.py` imports nothing from Streamlit, `requests` or the providers.
- Constants, verbatim from the spec: Standard reserve = `max(15% of tank, 2 gal)`; Conservative reserve = `max(25% of tank, Standard reserve)` (the spec says 25% of tank; the floor is added so Conservative can never be looser than Standard on small tanks, and the spec is updated in Task 4); low-case mpg factor `0.85`; high-case mpg factor `1.10`; low level = `level - 1` eighth; high level = `level + 0.5` eighth; defaults 15 miles/hour of work and 8 miles/order; miles rounded to the nearest 5, hours and orders rounded down; default level 4 of 8.
- Tests must not hit the network. `AppTest` runs `app.py`, whose sidebar calls fueleconomy.gov, so that test must patch `providers.fueleconomy._get` first.
- Always pass `provider=` explicitly in tests instead of relying on `config.PROVIDER` (CLAUDE.md).

## What each number means (for the presentation)

This section is the script for explaining the feature. The same text is shown in the tab's
"How this is calculated" expander (Task 3), so what you say matches what the screen says.

| Term | Plain-language meaning | Where the value comes from |
|---|---|---|
| **Tank level in eighths** | The fuel needle read as 0 to 8 eighths. Nobody knows exact gallons, so the app treats a reading as a band, not a point: an eighth of a 12-gal tank is 1.5 gal, and reading it by eye is off by up to half an eighth (about 0.75 gal, about 22 miles at 30 mpg). | Rounding arithmetic (sourced, our own math). Real gauge error is larger and unmeasured: [NÃO VERIFICADO] |
| **Reserve** | Fuel you do not plan to use, so you are never stranded mid-delivery. Stored in gallons, shown in miles with your mpg. | Standard `max(15%, 2 gal)`: the 2 gal floor is an assumption. It sits near where cars' own low-fuel light comes on (about 1.7 to 2.6 gal in manuals from third-party mirrors; AAA says 30 to 50 miles of range at the light). Conservative 25% = AAA "refill below a quarter" and fleet policies (sourced). |
| **Base miles** | `(gallons now - reserve) x mpg`. How far you can go before touching the reserve. | Formula, no assumption except your mpg |
| **Low case** | One eighth less fuel than you read, and mpg x 0.85 (stop-and-go uses more fuel than the label). | `-1 eighth` is design choice; `0.85` is an assumption. Evidence that city/stop-and-go cuts mpg 10 to 28%: Edmunds, Consumer Reports (search snippets only) |
| **High case** | Half an eighth more fuel and mpg x 1.10. | Both are assumptions; skewed low on purpose because running short costs more than running long |
| **Miles per hour of work** | Miles you drive per hour you are online, including empty miles to the restaurant. Turns miles into hours. | Default 15 is the middle of a 12 to 18 range the research subagents derived: assumption, no measured source, no Florida data. Better: your odometer miles / online hours |
| **Miles per order** | All miles in a shift divided by orders done. Turns miles into approximate orders. | Default 8: the suburban end of 4 to 12, an assumption. Best: your own shift |
| **Stacked errors** | Low and high combine every worst case at once, so the range is wide on purpose. A statistical combination (about 76 / 100 / 124 miles instead of 51 / 100 / 133 in the research example) is left for v2. | Design choice |

Not claimed anywhere: that the app predicts orders, knows DoorDash data, or gives financial
advice.

## Review Focus

- Tank level 0 (or any level whose usable gallons are below the reserve): miles are 0, never negative, status says refuel soon.
- Level changes by less than a full step or is non-integer (the high case uses `level + 0.5`): result is monotonic and clamped to 0 to 8 eighths.
- Extreme mpg (10 and 60) and the smallest tank (5 gal): reserve never exceeds the tank, no negative or absurd miles.
- Miles per hour or miles per order of 0 or negative (a typed zero): never divides by zero; the UI rejects it, the function raises `ValueError`.
- Conservative reserve with a full tank: still positive miles, and Conservative never gives more miles than Standard for the same inputs.

## File Structure

- Create `driver.py`: constants, `reserve_gallons`, `miles_until_reserve`, `round_miles`, `to_hours_orders`, `status`.
- Create `tests/test_driver.py`: unit tests for every function above.
- Create `tests/test_driver_tab.py`: one `AppTest` check of the tab.
- Modify `app.py:113-114` (tab list) and append a new `with tab_driver:` block before the AI tab or after it.
- Modify `README.md`, `CLAUDE.md`, and the spec (one line about the explanation expander).

---

### Task 1: Reserve and range math

**Files:**
- Create: `driver.py`
- Test: `tests/test_driver.py`

**Interfaces:**
- Produces:
  - `STANDARD_RESERVE_FRAC = 0.15`, `MIN_RESERVE_GAL = 2.0`, `CONSERVATIVE_RESERVE_FRAC = 0.25`, `LOW_MPG_FACTOR = 0.85`, `HIGH_MPG_FACTOR = 1.10`, `LOW_LEVEL_DELTA = -1.0`, `HIGH_LEVEL_DELTA = 0.5`
  - `reserve_gallons(tank_gal: float, mode: str = "standard") -> float` (`mode` is `"standard"` or `"conservative"`; anything else raises `ValueError`)
  - `miles_until_reserve(level_eighths: float, tank_gal: float, mpg: float, reserve_gal: float) -> dict` with keys `"low"`, `"base"`, `"high"`, all `>= 0`

- [ ] **Step 1: Write the failing tests** in `tests/test_driver.py` (add the same `sys.path.insert` header as `tests/test_fueleconomy.py`):
  - `test_standard_reserve_uses_percent_above_floor_and_floor_below`: `reserve_gallons(40) == 6.0`; `reserve_gallons(13) == 2.0`; `reserve_gallons(5) == 2.0`.
  - `test_conservative_reserve_is_a_quarter_tank`: `reserve_gallons(12, "conservative") == 3.0`.
  - `test_conservative_never_below_standard`: for tank in 5, 8, 13, 40: `reserve_gallons(t, "conservative") >= reserve_gallons(t, "standard")` (Conservative is `max(25% of tank, Standard)`, so on a 5 gal tank both are 2.0 rather than 1.25); an unknown mode `"x"` raises `ValueError`.
  - `test_miles_example_from_spec`: `miles_until_reserve(4, 13, 25, 2.0)` has `base == 112.5`, `low ~= 61.09`, `high ~= 146.09` (use `pytest.approx(abs=0.01)`).
  - `test_below_reserve_is_zero_not_negative`: `miles_until_reserve(0, 13, 25, 2.0)` has `low == 0` and `base == 0`, and no value is negative. `high` is also 0 here (half an eighth of 13 gal is 0.81 gal, below the 2 gal reserve); a level of 1 gives `high > 0`.
  - `test_low_le_base_le_high_and_monotonic`: for levels 0..8, `low <= base <= high` and `base` never decreases as the level rises; level 8 never exceeds `tank * mpg * HIGH_MPG_FACTOR`.
  - `test_level_is_clamped_to_zero_and_eight`: `miles_until_reserve(9, ...)["base"] == miles_until_reserve(8, ...)["base"]` and `miles_until_reserve(-1, ...)["base"] == 0`.

- [ ] **Step 2: Run to verify they fail**
  Run: `.venv/bin/python -m pytest tests/test_driver.py -q`
  Expected: collection error / `ModuleNotFoundError: No module named 'driver'`.

- [ ] **Step 3: Implement** `driver.py` with the constants and two functions above. Why: this file is the whole "brain" and has no UI, so it can be explained and tested alone. Approach: `usable(level) = max(0, clamp(level, 0, 8)/8 * tank - reserve)`; base uses `level` and `mpg`; low uses `level + LOW_LEVEL_DELTA` and `mpg * LOW_MPG_FACTOR`; high uses `level + HIGH_LEVEL_DELTA` and `mpg * HIGH_MPG_FACTOR`. Module docstring states each constant's status: sourced, or assumption.

- [ ] **Step 4: Run to verify they pass**
  Run: `.venv/bin/python -m pytest tests/test_driver.py -q`
  Expected: all pass.

- [ ] **Step 5: Run the whole suite, then commit**
  Run: `.venv/bin/python -m pytest -q` (expected: 47 + new tests pass).
  ```bash
  git add driver.py tests/test_driver.py
  git commit -m "Add driver.py: reserve and miles-until-reserve math"
  ```

---

### Task 2: Convert miles to hours and orders; status line

**Files:**
- Modify: `driver.py`
- Test: `tests/test_driver.py`

**Interfaces:**
- Consumes: nothing from Task 1 beyond the module.
- Produces:
  - `DEFAULT_MILES_PER_HOUR = 15.0`, `DEFAULT_MILES_PER_ORDER = 8.0`
  - `round_miles(miles: float) -> int` (nearest 5, halves round up)
  - `to_hours_orders(miles: float, miles_per_hour: float, miles_per_order: float) -> tuple` of `(hours: int, orders: int)`, both rounded down; `miles <= 0` gives `(0, 0)`; a rate `<= 0` raises `ValueError`
  - `status(low: float, base: float) -> str` returning exactly `"At or below your reserve: refuel soon"` when `base <= 0`, `"Close to your reserve"` when `low <= 0 < base`, else `"Room before reserve"`

- [ ] **Step 1: Write the failing tests** in `tests/test_driver.py`:
  - `test_round_miles_nearest_five_half_up`: `round_miles(112.5) == 115`, `round_miles(112.4) == 110`, `round_miles(0) == 0`, `round_miles(2.4) == 0`, `round_miles(2.5) == 5`.
  - `test_hours_and_orders_round_down`: `to_hours_orders(112.5, 15, 8) == (7, 14)`.
  - `test_zero_miles_gives_zero`: `to_hours_orders(0, 15, 8) == (0, 0)`; negative miles also `(0, 0)`.
  - `test_nonpositive_rates_raise`: `to_hours_orders(100, 0, 8)` and `to_hours_orders(100, 15, -1)` raise `ValueError`.
  - `test_status_three_states`: `status(0, 0)`, `status(0, 40)`, `status(30, 100)` return the three exact strings.

- [ ] **Step 2: Run to verify they fail**
  Run: `.venv/bin/python -m pytest tests/test_driver.py -q`
  Expected: `AttributeError` for the missing names.

- [ ] **Step 3: Implement** the functions in `driver.py`. Why: this is the step that turns "miles" into the units a delivery driver thinks in, since offers are short.

- [ ] **Step 4: Run to verify they pass**
  Run: `.venv/bin/python -m pytest tests/test_driver.py -q`
  Expected: all pass.

- [ ] **Step 5: Run the whole suite, then commit**
  ```bash
  git add driver.py tests/test_driver.py
  git commit -m "driver.py: hours/orders conversion, rounding and status line"
  ```

---

### Task 3: The Driver mode tab

**Files:**
- Modify: `app.py:113-114` (add `"Driver mode"` and `tab_driver`) and add a `with tab_driver:` block.
- Test: `tests/test_driver_tab.py`

**Interfaces:**
- Consumes: `driver.reserve_gallons`, `driver.miles_until_reserve`, `driver.round_miles`, `driver.to_hours_orders`, `driver.status`, `DEFAULT_MILES_PER_HOUR`, `DEFAULT_MILES_PER_ORDER`; the sidebar variables `tank` and `mpg` already in scope.
- Produces: widgets with keys `driver_level` (`st.select_slider`, options 0..8, default 4), `driver_reserve_mode` (`st.radio`, `"Standard"` / `"Conservative (1/4 tank)"`), `driver_mph` and `driver_mpo` (`st.number_input`, `min_value=1.0`, defaults 15.0 and 8.0).

- [ ] **Step 1: Write the failing test** in `tests/test_driver_tab.py`: build `AppTest.from_file("app.py")`; before `run()`, monkeypatch `providers.fueleconomy._get` to raise `requests.ConnectionError` (the sidebar's car lookup catches it); then
  - `test_tab_renders_with_defaults`: after `run()` there is no exception (`not at.exception`), and the metric labelled `Base miles` exists.
  - `test_lower_level_never_raises_base_miles`: set `driver_level` to 6, read base; set it to 2, read base; assert the second is `<=` the first.
  - `test_reserve_mode_conservative_not_more_miles`: base with the conservative option `<=` base with Standard at level 8 on a tank of 13 gal.
  Note: read metric values through `at.metric`, matching by `.label`. Set the provider with the existing env default (fixture), which needs no keys.

- [ ] **Step 2: Run to verify it fails**
  Run: `.venv/bin/python -m pytest tests/test_driver_tab.py -q`
  Expected: FAIL (no metric `Base miles`, no widget `driver_level`).

- [ ] **Step 3: Implement the tab in `app.py`.** Why: this is the screen the driver sees and the presenter shows. Contents, in order:
  - Inputs: level slider, reserve radio, miles/hour and miles/order, plus a caption "Reserve: X gal (about Y miles at your mpg)".
  - Results: a three-column row of `st.metric` with labels `Low miles`, `Base miles`, `High miles` (values from `round_miles`), and a table with rows Miles / Hours of work / Orders for the three cases (hours and orders from `to_hours_orders` using the unrounded miles).
  - The `status(...)` line as `st.info` / `st.warning` (warning for "refuel soon").
  - Caption: estimates only; use before or between orders, never while driving.
  - Expander `How this is calculated`: the formula and the table "What each number means" from this plan, each line saying sourced or assumption.
  Keep the block free of math; call `driver.py` only.

- [ ] **Step 4: Run to verify it passes**
  Run: `.venv/bin/python -m pytest tests/test_driver_tab.py -q` then `.venv/bin/python -m pytest -q`
  Expected: all pass.

- [ ] **Step 5: Look at it once** with `streamlit run app.py` (fixture mode), open the Driver mode tab, move the level slider and check that miles fall as the level drops. Then commit:
  ```bash
  git add app.py tests/test_driver_tab.py
  git commit -m "Add Driver mode tab: miles, hours and orders until reserve"
  ```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md` (structure list says "5 tabs"; make it 7 and add `driver.py` and the Driver mode description), `CLAUDE.md` (architecture: mention `driver.py` as a pure-math module and that its defaults are assumptions), `docs/superpowers/specs/2026-11-11-driver-range-design.md` (add the `How this is calculated` expander to the Output list, and change "Conservative = 25% of tank" to "max(25% of tank, Standard reserve)").

- [ ] **Step 1: Make the three edits.** Why: README and CLAUDE.md are what a new reader and a future session read first; the presentation script lives in this plan.
- [ ] **Step 2: Run the whole suite** (`.venv/bin/python -m pytest -q`; expected: pass) and commit:
  ```bash
  git add README.md CLAUDE.md docs/superpowers/specs/2026-11-11-driver-range-design.md
  git commit -m "Docs: driver mode v1"
  ```

## Self-review

- Spec coverage: inputs (Task 3), reserve (Task 1), calculation (Task 1), derived hours/orders and rounding (Task 2), status line and caption (Tasks 2 and 3), module split (Tasks 1 and 2), tests (all), out-of-scope respected (nothing added).
- One addition beyond the spec: the `How this is calculated` expander, requested by the user for the presentation. Task 4 records it in the spec.
- Types match across tasks: `miles_until_reserve` returns `{"low","base","high"}`; `to_hours_orders` returns `(hours, orders)`; `status(low, base)` takes the unrounded miles.
