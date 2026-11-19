# Fuel-aware Recommendation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user say how much fuel is on board, then answer "do I need to stop?" and "how many gallons should I buy?", recommending only reachable stations.

**Architecture:** A new pure module `fuelplan.py` (no Streamlit, no network) turns route, tank, mpg and fuel level into a plan with per-station gallons and reachability. `detour.rank_stations` and `insight.flip_point` accept per-station gallons. `recommend.py` gains a `no_stop` verdict. `app.py` adds the "Fuel now" input, wires the plan into the shared computation, and renders the new card states.

**Tech Stack:** Python 3.12, Streamlit 1.67, pytest + `streamlit.testing.v1.AppTest`.

**Spec:** `docs/superpowers/specs/2026-11-19-fuel-aware-recommendation-design.md`

## Global Constraints

- Run tests with `.venv/bin/python -m pytest -q` (no network, no keys); all must pass before every commit.
- Pure logic lives in `fuelplan.py` / `geo.py` / `detour.py` / `insight.py` / `recommend.py`: no `import streamlit` there.
- No new paid API calls: route length from `trip["path"]` via `geo.path_length_m`.
- Reserve: `driver.reserve_gallons(tank_gal, "standard")` (15% of tank, at least 2 gal), fixed.
- Fuel level **not set** (`fuel_input_mode` is None) must leave today's behaviour unchanged.
- Station names are untrusted: `recommend.md_escape` before Markdown, `html.escape` in HTML.
- AppTest: address widgets by `key=`; segmented controls via `at.button_group(key=...)`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A station behind the route start (projects before point 0) gets `along_miles == 0`, not a negative number (Task 1).
- Fuel gauge on E (0/8) or 0 miles left: stop needed, no station reachable, card shows the "none reachable" warning, no exception (Tasks 2, 7).
- A path with fewer than 2 points: `trip_miles == 0`, `stop_needed` False, no exception (Task 2).
- "Miles left" implying more fuel than the (estimated) tank: plan uses the larger figure and the UI suggests checking Tank size (Tasks 2, 6).
- Unpriced stations never get gallons or appear in the ranking, even when reachable (Task 2).

---

### Task 1: Position of a point along the route

**Files:**
- Modify: `geo.py` (add after `dist_point_segment_m`)
- Test: `tests/test_core.py` (append)

**Interfaces:**
- Produces: `geo.route_position(point: tuple, path: list) -> tuple[float, float]` returning `(along_m, off_m)`: metres from the path's first point to the point's projection on the nearest segment, and the distance from the point to that segment.

- [ ] **Step 1: Write the failing tests**

```python
def test_route_position_on_a_two_segment_path():
    path = [(27.0, -82.0), (27.0, -81.9), (27.1, -81.9)]
    along, off = geo.route_position((27.0005, -81.95), path)
    seg1 = geo.haversine_m(path[0], path[1])
    assert along == pytest.approx(seg1 / 2, rel=0.02)
    assert off == pytest.approx(55, abs=10)          # ~0.0005 deg of latitude

def test_route_position_behind_the_start_is_zero_along():
    path = [(27.0, -82.0), (27.0, -81.9)]
    along, _ = geo.route_position((27.0, -82.05), path)
    assert along == 0.0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core.py -k route_position -q`
Expected: FAIL with `AttributeError: module 'geo' has no attribute 'route_position'`

- [ ] **Step 3: Implement `route_position(point, path)` in `geo.py`**

Find the segment with the smallest `dist_point_segment_m`; `along_m` = cumulative `haversine_m` up to that segment's start + `t` × segment length, where `t` is the same clamped projection factor `dist_point_segment_m` computes (factor it into a private `_project(p, a, b) -> (t, dist)` and have both functions use it). Paths with fewer than 2 points return `(0.0, haversine_m(point, path[0]) if path else 0.0)`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_core.py -q`
Expected: PASS (including the existing geo tests)

- [ ] **Step 5: Commit** — `git add geo.py tests/test_core.py && git commit -m "geo: position of a point along the route"`

---

### Task 2: `fuelplan.py`, the pure fuel plan

**Files:**
- Create: `fuelplan.py`
- Test: `tests/test_fuelplan.py`

**Interfaces:**
- Consumes: `geo.route_position`, `geo.path_length_m`, `driver.reserve_gallons`.
- Produces:
  - `trip_miles(path: list) -> float`
  - `fuel_on_board(mode: str, value: float, tank_gal: float, mpg: float, reserve_gal: float) -> float` — `mode` is `"gauge"` (value in eighths, 0–8) or `"miles"` (miles until reserve).
  - `TANK_BY_CLASS: list[tuple[str, float]]` and `tank_for_class(vclass: str | None) -> float` (fallback `13.0`).
  - `plan(stations: list, path: list, fuel_gal: float, tank_gal: float, mpg: float, reserve_gal: float, p_ref: float) -> dict` with keys `trip_miles, need_gal, fuel_gal, effective_tank, tank_too_small, arrive_gal, stop_needed, per_station`, where `per_station[station_id] = {"reachable": bool, "gallons": float, "fuel_at_station": float, "short": bool}`.

Module docstring documents each `TANK_BY_CLASS` value as an ASSUMPTION with its reasoning (same style as `driver.py`). Match class keywords case-insensitively, most specific first, e.g. `("pickup", …)`, `("minivan", …)`, `("standard sport utility", …)`, `("small sport utility", …)`, `("large cars", …)`, `("midsize", …)`, `("compact", …)`, `("two seaters", …)`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
import fuelplan as fp

PATH = [(27.0, -82.0), (28.0, -82.0)]           # ~69 mi straight north

def st(i, lat, price, off=0.0):
    return {"id": f"s{i}", "name": f"S{i}", "lat": lat, "lng": -82.0, "price": price, "off_route_m": off}

def test_trip_miles_and_degenerate_paths():
    assert fp.trip_miles(PATH) == pytest.approx(69.1, abs=0.5)
    assert fp.trip_miles([(27.0, -82.0)]) == 0.0 and fp.trip_miles([]) == 0.0

def test_fuel_on_board_gauge_and_miles():
    assert fp.fuel_on_board("gauge", 2, 16, 25, 2.4) == 4.0           # 2/8 of 16
    assert fp.fuel_on_board("gauge", 12, 16, 25, 2.4) == 16.0         # clamped to the tank
    assert fp.fuel_on_board("miles", 50, 16, 25, 2.4) == pytest.approx(4.4)  # 50/25 + reserve
    assert fp.fuel_on_board("miles", 900, 16, 25, 2.4) == pytest.approx(38.4)  # not capped by tank

def test_tank_for_class_falls_back_to_13():
    assert fp.tank_for_class(None) == 13.0 and fp.tank_for_class("Spaceship") == 13.0
    assert fp.tank_for_class("Standard Pickup Trucks 4WD") > fp.tank_for_class("Compact Cars")

def test_full_tank_needs_no_stop_and_reports_arrival():
    r = fp.plan([st(1, 27.5, 3.0)], PATH, fuel_gal=16, tank_gal=16, mpg=25, reserve_gal=2.4, p_ref=3.5)
    assert r["stop_needed"] is False
    assert r["arrive_gal"] == pytest.approx(16 - 69.1 / 25, abs=0.05)

def test_arriving_with_exactly_the_reserve_is_no_stop():
    need = fp.trip_miles(PATH) / 25 + 2.4
    assert fp.plan([], PATH, need, 16, 25, 2.4, 3.5)["stop_needed"] is False

def test_unreachable_station_is_excluded_and_cheaper_fills_up():
    sts = [st(1, 27.1, 3.0), st(2, 27.9, 3.0), st(3, 27.2, 3.9), st(4, 27.15, None)]
    r = fp.plan(sts, PATH, fuel_gal=3.0, tank_gal=16, mpg=25, reserve_gal=2.4, p_ref=3.5)
    ps = r["per_station"]
    assert r["stop_needed"] and ps["s1"]["reachable"] and not ps["s2"]["reachable"]
    assert ps["s1"]["gallons"] == pytest.approx(16 - ps["s1"]["fuel_at_station"], abs=0.1)  # fill
    assert ps["s3"]["gallons"] <= 16 - ps["s3"]["fuel_at_station"]                          # minimum
    assert ps["s3"]["gallons"] % 0.5 == 0                                                    # rounded up to 0.5
    assert "s4" not in ps                                                                    # unpriced

def test_gauge_on_empty_reaches_nothing():
    r = fp.plan([st(1, 27.1, 3.0)], PATH, 0.0, 16, 25, 2.4, 3.5)
    assert r["stop_needed"] and not r["per_station"]["s1"]["reachable"]

def test_trip_longer_than_a_tank_marks_short():
    long_path = [(25.0, -82.0), (31.0, -82.0)]                      # ~414 mi
    r = fp.plan([st(1, 25.1, 3.0)], long_path, 4.0, 12, 25, 2.0, 3.5)
    assert r["per_station"]["s1"]["short"] is True

def test_miles_left_bigger_than_tank_raises_effective_tank():
    r = fp.plan([], PATH, fuel_gal=20.0, tank_gal=13, mpg=25, reserve_gal=2.0, p_ref=3.5)
    assert r["effective_tank"] == 20.0 and r["tank_too_small"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fuelplan.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'fuelplan'`

- [ ] **Step 3: Implement `fuelplan.py`**

Formulas are fixed by the spec (section 2 and the builder-check calls); write them as given:
`need_gal = trip_miles/mpg + reserve`; `stop_needed = fuel_gal < need_gal`; `arrive_gal = fuel_gal - trip_miles/mpg`; `effective_tank = max(tank_gal, fuel_gal)`; `tank_too_small = fuel_gal > tank_gal`. Per priced station: `along, off = geo.route_position((lat, lng), path)` (use `s["off_route_m"]` for `off` when present); `reach = (along + off) / 1609.344`; `fuel_at_station = fuel_gal - reach/mpg`; `reachable = fuel_at_station >= reserve`; `room = floor_to_0.1(effective_tank - fuel_at_station)`; `minimum = ceil_to_0.5(max(0, (trip_miles - along/1609.344)/mpg + reserve - fuel_at_station))`; `short = room < minimum`. Gallons: when `stop_needed`, `room` if `price < p_ref` else `min(room, minimum)`; when no stop is needed, `room`. Clamp gallons to `[0, room]`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_fuelplan.py -q`
Expected: PASS

- [ ] **Step 5: Commit** — `git add fuelplan.py tests/test_fuelplan.py && git commit -m "fuelplan: trip fuel, reachability and gallons per station"`

---

### Task 3: EPA size class in the car record

**Files:**
- Modify: `providers/fueleconomy.py` (`parse_vehicle`, module docstring)
- Test: `tests/test_fueleconomy.py`

**Interfaces:**
- Produces: `parse_vehicle(...)["vclass"]: str` (`""` when missing).

- [ ] **Step 1: Write the failing test** — in `test_parse_vehicle_and_mpg_kinds`, add `"VClass": "Midsize Cars"` to a copy of `CAMRY` and assert `fe.parse_vehicle(rec)["vclass"] == "Midsize Cars"`; assert `fe.parse_vehicle(CAMRY_without_VClass)["vclass"] == ""`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_fueleconomy.py -q` — Expected: FAIL with `KeyError: 'vclass'`
- [ ] **Step 3: Add `"vclass": payload.get("VClass") or ""` to `parse_vehicle`**; update the docstring line "The record has no tank size" to add "but carries the EPA size class, used to estimate one (fuelplan.tank_for_class)".
- [ ] **Step 4: Run** the same command — Expected: PASS
- [ ] **Step 5: Commit** — `git commit -am "fueleconomy: keep the EPA size class"`

---

### Task 4: Per-station gallons in ranking and the insight panel

**Files:**
- Modify: `detour.py` (`rank_stations`), `insight.py` (`flip_point` docstring only, if the pass-through already works)
- Test: `tests/test_core.py`, `tests/test_insight.py`

**Interfaces:**
- Produces: `detour.rank_stations(stations, p_ref, gallons: float | dict[str, float], mpg, value_of_time, speed_mph) -> list[dict]`; each row gains `"gallons": float`. A station missing from the mapping is skipped.
- `insight.flip_point(stations, p_ref, gallons: float | dict, ...)` works unchanged by passing `gallons` through.

- [ ] **Step 1: Write the failing tests**

```python
def fixture_stations():
    return [s for s in services.load_trip(None, None, 3000, provider="fixture")["stations"]
            if s["price"] is not None]

def test_rank_stations_mapping_matches_a_uniform_number():
    sts = fixture_stations()
    by_number = detour.rank_stations(sts, 3.5, 10, 27, 19, 45)
    by_map = detour.rank_stations(sts, 3.5, {s["id"]: 10 for s in sts}, 27, 19, 45)
    assert [r["net_benefit"] for r in by_number] == [r["net_benefit"] for r in by_map]
    assert all(r["gallons"] == 10 for r in by_map)

def test_rank_stations_skips_stations_without_gallons():
    sts = fixture_stations()
    ranked = detour.rank_stations(sts, 3.5, {sts[0]["id"]: 12}, 27, 19, 45)
    assert [r["id"] for r in ranked] == [sts[0]["id"]] and ranked[0]["gallons"] == 12
```

In `tests/test_insight.py` (same `fixture_stations()` helper, importing `services`): `insight.flip_point(sts, 3.5, {s["id"]: 10 for s in sts}, 27, 45) == insight.flip_point(sts, 3.5, 10, 27, 45)`.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_core.py tests/test_insight.py -q` — Expected: FAIL (`TypeError` on the dict, or missing `"gallons"` key)
- [ ] **Step 3: Implement** — in `rank_stations`, resolve `g = gallons.get(s["id"]) if isinstance(gallons, dict) else gallons`; `continue` when `g is None`; pass `g` to `net_benefit`; add `"gallons": round(g, 1)` to the row.
- [ ] **Step 4: Run** the same command, then the full suite — Expected: PASS
- [ ] **Step 5: Commit** — `git commit -am "detour: per-station gallons in the ranking"`

---

### Task 5: `no_stop` verdict and card text

**Files:**
- Modify: `recommend.py` (`recommend`, `summary`)
- Test: `tests/test_recommend.py`

**Interfaces:**
- Produces: `recommend.recommend(ranked, min_saving=..., n_alternatives=3, stop_needed: bool | None = None)` — `stop_needed is False` returns `{"verdict": "no_stop", "best": <ranked[0] if worth else None>, "alternatives": [], "min_saving": floor}`; `None` keeps today's behaviour.
- `recommend.summary(rec)` returns for `go` with a `"gallons"` key in `best` a title `f"Fill up at {name} · buy {gallons:.1f} gal"`; for `no_stop` the title `"No stop needed"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_stop_verdict_keeps_the_optional_best_deal():
    ranked = [{"id": "a", "name": "A", "price": 3.0, "net_benefit": 2.0, "extra_miles": 1, "extra_minutes": 2}]
    rec = recommend.recommend(ranked, 0.5, stop_needed=False)
    assert rec["verdict"] == "no_stop" and rec["best"]["id"] == "a"
    assert recommend.summary(rec)[0] == "No stop needed"

def test_go_title_names_the_gallons_when_known():
    best = {"id": "a", "name": "A*", "price": 3.0, "net_benefit": 2.0, "extra_miles": 1,
            "extra_minutes": 2, "gallons": 9.5}
    title, _ = recommend.summary(recommend.recommend([best], 0.5, stop_needed=True))
    assert title == "Fill up at A\\* · buy 9.5 gal"

def test_stop_needed_none_is_todays_behaviour():
    ranked = [{"id": "a", "name": "A", "price": 3.0, "net_benefit": 0.1, "extra_miles": 1, "extra_minutes": 2}]
    assert recommend.recommend(ranked, 0.5)["verdict"] == "skip"
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_recommend.py -q` — Expected: FAIL (`unexpected keyword argument 'stop_needed'`)
- [ ] **Step 3: Implement** the parameter and the two title branches (escape the name with `md_escape` as today).
- [ ] **Step 4: Run** the same command and the full suite — Expected: PASS
- [ ] **Step 5: Commit** — `git commit -am "recommend: no_stop verdict and gallons in the title"`

---

### Task 6: "Fuel now" input, tank estimate and gallons toggle (UI only)

**Files:**
- Modify: `app.py` (trip strip, "Pick your car" popover, sidebar gallons, chips)
- Test: `tests/test_fuel_inputs.py` (new; reuse the `make_app` pattern from `tests/test_where_tab.py`)

**Interfaces:**
- Consumes: `fuelplan.tank_for_class`, `fuelplan.fuel_on_board`, `driver.reserve_gallons`.
- Produces session keys used by Task 7: `fuel_input_mode` (`None | "Fuel gauge" | "Miles left"`), `fuel_level_eighths` (gauge label `"E"`, `"1/8"` … `"F"`; its index 0–8 is the eighths), `fuel_miles_left` (float), `gallons_manual` (bool), `tank_applied` (car id). Module-level values after the strip: `fuel_mode: str | None` (`"gauge"`, `"miles"` or `None`), `fuel_value: float | None`.

- [ ] **Step 1: Write the failing AppTests**

```python
def test_fuel_not_set_by_default_and_chip_says_so(monkeypatch):
    app = make_app(monkeypatch)
    assert app.button_group(key="fuel_input_mode").value is None
    assert "Not set" in " ".join(h.proto.body for h in app.get("html") if "fs-chips" in h.proto.body)

def test_gauge_input_shows_in_the_chip(monkeypatch):
    app = make_app(monkeypatch)
    app.button_group(key="fuel_input_mode").set_value("Fuel gauge")
    app.select_slider(key="fuel_level_eighths").set_value("1/4").run()
    assert "1/4 tank" in " ".join(h.proto.body for h in app.get("html") if "fs-chips" in h.proto.body)

def test_gallons_slider_is_locked_until_set_by_hand(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Fuel gauge", "fuel_level_eighths": "1/4"})
    assert app.slider(key="gallons").disabled
    app.toggle(key="gallons_manual").set_value(True).run()
    assert not app.slider(key="gallons").disabled
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_fuel_inputs.py -q` — Expected: FAIL (`KeyError: 'fuel_input_mode'`)
- [ ] **Step 3: Implement in `app.py`**
  - Third popover in `strip_row`: `"How much fuel do you have?"`, icon `:material/local_gas_station:`; inside, `st.segmented_control("How do you know?", ["Fuel gauge", "Miles left"], key="fuel_input_mode")`, then `st.select_slider("Fuel gauge", options=["E","1/8","1/4","3/8","1/2","5/8","3/4","7/8","F"], key="fuel_level_eighths")` or `st.number_input("Miles until the reserve light", 0.0, 900.0, 50.0, 5.0, key="fuel_miles_left")`. Map the gauge label to its index for `fuel_value`.
  - Chip `"Fuel now"`: `"Not set"`, `"<label> tank"` or `"<n> mi to reserve"`.
  - Tank: when a looked-up car has `vclass` and `st.session_state.get("tank_applied") != car["id"]`, set `st.session_state["tank"] = fuelplan.tank_for_class(car["vclass"])` and `tank_applied`; caption `"Estimated from the EPA size class ({vclass}); check your manual."`.
  - Sidebar: when `fuel_mode` is set, `st.toggle("Set gallons by hand", key="gallons_manual")` above the gallons control, and pass `disabled=not manual` through `slider_with_number` (add a `disabled: bool = False` parameter that disables both the slider and its number box).
- [ ] **Step 4: Run** the new tests and the full suite — Expected: PASS (existing tests unaffected: no fuel level set by default)
- [ ] **Step 5: Commit** — `git add app.py tests/test_fuel_inputs.py && git commit -m "app: Fuel now input, tank estimate and gallons toggle"`

---

### Task 7: Wire the plan into the recommendation

**Files:**
- Modify: `app.py` (shared computation block, "Where to stop" answer card, "Your trip at a glance", `assistant_facts`)
- Test: `tests/test_fuel_inputs.py` (append)

**Interfaces:**
- Consumes: `fuelplan.plan`, `fuelplan.trip_miles`, `detour.rank_stations` with a mapping (Task 4), `recommend.recommend(..., stop_needed=)` (Task 5), Task 6's `fuel_mode`, `fuel_value`, `gallons_manual`.
- Produces: module-level `fplan: dict | None` and `gallons_used: float` (the single number of spec section 3b) read by Sensitivity, the insight panel and `assistant_facts`.

- [ ] **Step 1: Write the failing AppTests**

```python
def test_full_tank_on_the_synthetic_route_needs_no_stop(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Fuel gauge", "fuel_level_eighths": "F"})
    assert any(h.value == "No stop needed" for h in app.header)

def test_low_tank_recommends_a_reachable_station_with_gallons(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Miles left", "fuel_miles_left": 30.0})
    titles = [h.value for h in app.header if h.value.startswith("Fill up at ")]
    assert titles and " · buy " in titles[0]

def test_empty_tank_says_no_station_is_reachable(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Fuel gauge", "fuel_level_eighths": "E"})
    assert any("reachable" in w.value for w in app.warning) and not app.exception

def test_trip_at_a_glance_now_renders(monkeypatch):
    app = make_app(monkeypatch)
    assert "Trip distance" in {m.label for m in app.metric}

def test_regret_line_agrees_with_headline_when_gallons_differ(monkeypatch):
    app = make_app(monkeypatch, session={"fuel_input_mode": "Miles left", "fuel_miles_left": 30.0})
    assert not app.exception  # regret/price_rank_gap/flip_point get the same filtered stations and mapping
```

(Adjust `make_app` to accept `session` the way `tests/test_where_tab.py` does; the gauge value set in session is the label string.)

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_fuel_inputs.py -q` — Expected: the new tests FAIL
- [ ] **Step 3: Implement in `app.py`**
  - Shared block: `tmiles = fuelplan.trip_miles(trip["path"])` always; when `fuel_mode` is set and `not gallons_manual`: `reserve = driver.reserve_gallons(tank)`, `fuel_gal = fuelplan.fuel_on_board(fuel_mode, fuel_value, tank, mpg, reserve)`, `fplan = fuelplan.plan(detoured, trip["path"], fuel_gal, tank, mpg, reserve, p_ref)`; filter `detoured` to reachable ids (when a stop is needed), build `{id: gallons}`, rank with it, `rec = recommend.recommend(ranked, min_saving, stop_needed=fplan["stop_needed"])`. Otherwise today's path with `fplan = None`.
  - `gallons_used`: `rec["best"]["gallons"]` for `go`/`no_stop` with a best; else the slider value. Mirror it into the disabled slider (clamped 3–25, caption with the exact value when clamped). Pass the same mapping and filtered stations to `insight.flip_point`, `regret`, `price_rank_gap`; `sensitivity_table` and facts use `gallons_used`.
  - Card: `no_stop` → `st.header("No stop needed")` + "You reach {destination} with about {arrive:.1f} gal left ({arrive*mpg:.0f} mi)." + optional best-deal line; `go` with `fplan` → add a `"Buy"` metric and, when `short`, `st.warning("This trip needs more than one tank: plan another stop before {destination}.")`; no reachable station → `st.warning("None of the listed stations is reachable before your reserve: fill up at the first station you pass.")`; `tank_too_small` → caption suggesting Tank size.
  - "Your trip at a glance": replace `trip.get("distance_m")` with `tmiles > 0`; label the distance "approximate" in synthetic mode; add "Fuel on arrival" when `fplan` and no stop is needed.
  - `assistant_facts`: add `gallons_to_buy`, `stop_needed`, `arrive_gal` (rounded to 1 decimal) and a `verdict` string for `no_stop` ("you have enough fuel: no stop needed").
- [ ] **Step 4: Run** the full suite — Expected: PASS
- [ ] **Step 5: Check in the browser** — start `fuel-app` (synthetic) and `fuel-app-google` (live) from `.claude/launch.json`; set Fuel gauge F → "No stop needed"; 1/8 → "Fill up at … · buy N gal"; E → the warning; toggle "Set gallons by hand" → slider unlocks.
- [ ] **Step 6: Update `CLAUDE.md`** (Architecture: add `fuelplan.py`; Layout: the "Fuel now" popover and the gallons toggle) and commit — `git add app.py tests/test_fuel_inputs.py CLAUDE.md && git commit -m "Where to stop: fuel-aware verdict and gallons to buy"`
