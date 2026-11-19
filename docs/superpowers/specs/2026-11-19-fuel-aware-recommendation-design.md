# Fuel-aware recommendation: do I need to stop, and how much should I buy?

Date: 2026-11-19. Status: draft for approval (builder check done; its six questions are settled
below and marked *builder-check call*).

## Intent

Today the app answers "which station is worth the detour?" for a fill-up size the user types
("Gallons to buy", sidebar). It never asks how much fuel is already in the tank, so it cannot
answer the two questions a driver actually has before a trip:

1. **Do I need to stop at all?**
2. **If I stop, how many gallons should I buy for the stop to pay off?**

The user (Gustavo, student project due 2026-11-30) wants the app to answer both. Who it serves:
a driver planning a single trip (e.g. Sarasota to Orlando), reading a phone or laptop.

## Goals

- The user can tell the app how much fuel they have, in either of two ways:
  - **Fuel gauge**: E, 1/8, 1/4, … 7/8, F (what a needle shows).
  - **Miles left**: the "miles until reserve / distance to empty" number some dashboards show.
- The app computes the fuel the trip needs from the real route length.
- If the fuel on board reaches the destination with the reserve intact, the answer is
  **"No stop needed"**, with how much fuel the user arrives with.
- If not, the app recommends only stations the user can **reach before hitting the reserve**,
  and for each it says **how many gallons to buy**:
  - station cheaper than the route reference price (`p_ref`): **fill the tank** (savings grow
    with every gallon, so the most that fits is the best);
  - otherwise: **the minimum** that reaches the destination with the reserve intact.
- "Gallons to buy" is filled in automatically from the above, and the user can still override it.
- The tank size is pre-filled with an estimate from the car's EPA size class when the user looks
  up a car; the user can edit it. It is labelled as an estimate.
- The dead "Your trip at a glance" card (trip fuel cost, distance, gallons) starts working.

## Non-goals

- Planning several stops, or several partial fills, on one trip (overkill for trips of a few
  hours; rejected as Approach C).
- Paid vehicle-data APIs for tank size.
- Tracking fuel over time, or remembering the tank level between visits.
- Electric cars (the EPA figures are MPGe; the app already warns and leaves them out).

## Anti-goals (would make this a failure even if it "works")

- Recommending a station the user cannot reach with the fuel they have.
- Saying "No stop needed" when the user would arrive with less than the reserve.
- The "Does cheapest always mean best?" panel contradicting the headline (it must stay built
  on the same stations, gallons and settings as the recommendation; see CLAUDE.md).
- Changing today's behaviour for a user who never enters a fuel level.
- A number the LLM assistant states that is not in its `facts` dict.

## Constraints

- Pure math stays out of `app.py`: new logic goes in a pure module (no Streamlit, no network),
  tested like `driver.py` and `insight.py`.
- No new paid API calls: route length comes from the decoded route polyline already in
  `trip["path"]` (`geo.path_length_m`), and station positions from coordinates already present.
- Station names stay untrusted (`recommend.md_escape` before Markdown, `html.escape` in HTML).
- The user's location stays session-only (unchanged).
- Tests: `.venv/bin/python -m pytest -q` must pass; AppTest addresses widgets by `key=`.

## Approaches considered

- **A. Fuel warning only.** Trip fuel and "stop / no stop"; "Gallons to buy" stays manual.
  Cheap, but does not answer "how many gallons are worth it". Rejected.
- **B. Fuel-aware recommendation (chosen).** Fuel level in, then trip fuel, a no-stop verdict,
  reachable stations only, per-station gallons (fill if cheaper, minimum otherwise), and
  automatic "Gallons to buy". Chosen by Gustavo on 2026-11-19, with the added "Miles left" input.
- **C. Multi-stop / partial-fill optimisation.** Out of scope for single trips. Rejected.

Tank size source, considered and decided:
- fueleconomy.gov (EPA) and NHTSA vPIC have no tank capacity; paid APIs (e.g. Car API's
  `fuel_tank_capacity`) cost money and a key. **Decision:** estimate from the EPA size class
  (`VClass` in the vehicle record), editable, labelled as an estimate.

## Design

### 1. Inputs (UI)

- A new chip in the trip strip, **"Fuel now"**, with its own popover **"How much fuel do you
  have?"** (same pattern as "Where are you going?" / "Pick your car").
  - `st.segmented_control` **"How do you know?"**: `Fuel gauge` | `Miles left`
    (key `fuel_input_mode`). No default selection means **"not set"**.
  - Fuel gauge: `st.select_slider` over E, 1/8 … F (key `fuel_level_eighths`, value 0–8).
  - Miles left: `st.number_input` "Miles until the reserve light" (key `fuel_miles_left`).
  - A clear way back to "not set" (deselecting the segmented control is enough).
- Chip text: "Not set" / "1/4 tank" / "85 mi to reserve".
- "Pick your car": the tank input keeps key `tank`; when a looked-up car has a known class, the
  tank is pre-filled once per car (same "apply once" pattern as `mpg_applied`), with a caption
  "Estimated from the EPA size class (<class>); check your manual."
- Sidebar "Gallons to buy": when a fuel level is set, it shows the app's choice and a toggle
  **"Set gallons by hand"** (key `gallons_manual`, off by default). Off: the slider is disabled
  and mirrors the computed value. On: today's behaviour. With no fuel level set, nothing changes.

### 2. Pure module `fuelplan.py` (new)

All in gallons and miles; no Streamlit, no network.

- `trip_miles(path) -> float`: `geo.path_length_m(path) / 1609.344`.
- `fuel_on_board(mode, value, tank_gal, mpg, reserve_gal) -> float` (gallons):
  - gauge: `value / 8 * tank_gal`;
  - miles left: `value / mpg + reserve_gal` (the dashboard number counts down to the reserve).
  - clamp to `[0, ∞)` only. *(Builder-check call:)* the dashboard's "Miles left" is more
    trustworthy than an estimated tank, so it is **not** capped by the tank. When it implies more
    fuel than the tank holds, the plan uses `effective_tank = max(tank_gal, fuel_gal)` and the UI
    says "Your tank looks bigger than X gal: check Tank size under Pick your car." The gauge
    mode is clamped to `[0, tank_gal]` as before.
- Reserve: reuse `driver.reserve_gallons(tank_gal, "standard")` (15% of tank, at least 2 gal:
  existing, documented assumptions). *(Builder-check call:)* fixed at "standard", no new widget;
  Driver mode's Standard/Conservative control is not brought back.
- `along_route_miles(point, path) -> float`: distance from the route start to the point's
  projection on the route (needs a small helper in `geo.py` returning cumulative distance at
  the nearest segment).
- `plan(stations, path, fuel_gal, tank_gal, mpg, reserve_gal, p_ref) -> dict`:
  - `need_gal = trip_miles / mpg + reserve_gal`; `stop_needed = fuel_gal < need_gal`, so arriving
    with exactly the reserve counts as "no stop needed" (the reserve *is* the safety margin; no
    second margin on top). *(Builder-check call.)*
  - `arrive_gal = fuel_gal - trip_miles / mpg` (shown when no stop is needed).
  - for each priced station: `reach_miles = along_route + off_route_miles` (one way);
    `fuel_at_station = fuel_gal - reach_miles / mpg`;
    `reachable = fuel_at_station >= reserve_gal`;
    `room = tank_gal - fuel_at_station`;
    `minimum = max(0, (trip_miles - along_route) / mpg + reserve_gal - fuel_at_station)`;
    `gallons = room if price < p_ref else min(room, minimum)` (rounded to 0.1, never negative,
    never above `room`).
  - *(Builder-check call)* if even a full tank at the best reachable station cannot reach the
    destination with the reserve (`room < minimum`), the station still gets `gallons = room`, the
    row carries `"short": true`, and the card adds "This trip needs more than one tank: plan
    another stop before <destination>." (Multi-stop planning stays a non-goal.)
  - *(Builder-check call)* when **no stop is needed**, every reachable station gets
    `gallons = room` (filling up is optional, and you would only bother if you fill), and that
    same rule feeds both the optional "fill up anyway" line and the insight panel.
  - returns `{"trip_miles", "need_gal", "fuel_gal", "arrive_gal", "stop_needed",
    "per_station": {id: {"reachable", "gallons", "fuel_at_station"}}}`.

### 3. Ranking with per-station gallons

- `detour.rank_stations` accepts `gallons` as either a number (today) or a mapping
  `{station_id: gallons}`; with a mapping, each station's net benefit uses its own gallons.
  The output rows carry `"gallons"` so the UI can show "buy N gal".
- When a fuel level is set and a stop is needed, unreachable stations are removed **before**
  ranking (they never appear in the headline, the options or the insight panel).
- `insight.decision_grid` keeps its fixed `GALLONS_GRID` (it explores "what if you bought N");
  `insight.flip_point`, `regret` and `price_rank_gap` receive the same gallons argument (number
  or mapping) and the same filtered stations as the headline, so the panel cannot contradict it.

### 3b. The single "gallons" value the rest of the app reads *(builder-check call)*

Sensitivity (`sensitivity_table`), the `flip_point` sentence, the AI facts (`gallons`,
`best_saving_gross`) and the disabled sidebar slider all take one number. With a fuel level set:

- `go` (stop needed or not): the **recommended station's** gallons.
- `skip`, `none`, "no reachable station": the manual slider value (today's behaviour).
- The disabled slider shows that number clamped to its 3–25 range, with a caption giving the
  exact value when clamped (e.g. "App's choice: 1.5 gal").

`decision_grid` keeps its fixed `GALLONS_GRID` as before.

### 4. Verdicts and the answer card

- Fuel level **not set**: unchanged (today's go / skip / none).
- Set and **no stop needed**: a new verdict `"no_stop"`. Card title "No stop needed"; body
  "You reach <destination> with about X gal left (Y mi)." Below, optional: "If you want to fill
  up anyway, the best deal is <station> (saves $Z for N gal)" when the best net benefit clears
  `min_saving`.
- Set and **stop needed**:
  - `go`: title "Fill up at <station> · buy N gal"; the stat tiles gain "Buy" (gallons).
  - no station clears `min_saving`: today's "skip" wording, but pointing to the nearest
    **reachable** on-route station.
  - no reachable station: a warning "None of the listed stations is reachable before your
    reserve: fill up at the first station you pass." (the corridor may simply be too narrow).
- `recommend.summary` and the LLM facts (`assistant_facts`) gain `gallons_to_buy`,
  `stop_needed`, `arrive_gal` (pre-rounded), and the `verdict` text covers `no_stop`.

### 5. Trip at a glance

- `trip["distance_m"]` is never set today, so the card never renders. Compute
  `trip_miles` from the path for both modes and show: fuel the trip needs, its cost at `p_ref`,
  distance, and (fuel level set) fuel on arrival. In synthetic mode the distance is labelled
  as approximate (the synthetic route is straight lines between cities).

### 6. Tank estimate by EPA class

- `providers/fueleconomy.parse_vehicle` also returns `"vclass"` (`payload["VClass"]`).
- A small table in a pure module (e.g. `fuelplan.TANK_BY_CLASS`) maps class keywords to a
  typical tank in gallons (compact cars, midsize, large, small SUV, standard SUV, pickup, minivan),
  each value documented in the module docstring as an **assumption**, with a fallback of 13 gal
  (today's default).

## Testing

- `fuelplan.py`: unit tests for gauge and miles-left conversions, clamping, `stop_needed` at the
  boundary (exactly reserve), reachability (a station just past the reserve is excluded), fill
  vs minimum by price, gallons never above room, and a station behind the start projecting to 0.
- `geo` helper: along-route distance on a straight two-segment path.
- `detour.rank_stations`: number and mapping give identical results when every station has the
  same gallons; rows carry `"gallons"`.
- AppTest: no fuel level set leaves today's headline unchanged (existing tests keep passing);
  a full tank on the synthetic route yields "No stop needed"; an almost empty tank yields a
  reachable recommendation with "buy N gal"; the "Set gallons by hand" toggle restores the
  slider; the insight panel's regret line agrees with the headline when gallons differ by station.

## Left to the builder

- Exact copy of captions, chip labels and the warning texts (keep them short, plain English,
  like the rest of the app).
- The tank-by-class numbers, as long as each is documented as an assumption with its reasoning.
- How "minimum" rounds up (suggest: up to the next 0.5 gal so the reserve is never undercut).
- Whether to show "Buy" as a fifth stat tile or replace "Price" (keep the card from wrapping
  on a laptop width).
