# Driver mode v1: miles until refuel

Status: draft for review. No code written yet.

## Purpose

A gig delivery driver (DoorDash and similar) wants to know **how far they can still drive before
they should refuel**, so they can plan the shift and pick where to fill up without running low
mid-delivery. The app cannot know upcoming orders (DoorDash has no public Dasher API and the app
will not read the Dasher app), so it does not predict orders. It answers one question from
numbers the driver types.

Said by the user: refuel timing matters for gig driving; use is partly at shift start and partly
between orders; offers in delivery apps are short, so the useful unit is "how many more orders /
hours" rather than "can I fit one long trip"; start with the simple version.

## Scope (v1)

One new tab, **Driver mode**, plus one pure-math module. Nothing else changes.

### Inputs

| Input | Source | Default |
|---|---|---|
| Tank level, eighths (0..8) | new `st.select_slider` | 4 (half) |
| Tank size (gal) | existing sidebar `tank` | 13 |
| Fuel economy (mpg) | existing sidebar `mpg` | from car lookup or slider |
| Reserve | new radio: Standard / Conservative | Standard |
| Miles per hour of work | new number input | 15 |
| Miles per order (all-in) | new number input | 8 |

Reserve: Standard = max(15% of tank, 2 gal). Conservative = 25% of tank (AAA "refill below a
quarter"). The reserve is stored in gallons and shown in miles using the user's mpg.

### Calculation

```
usable_gal(level)  = max(0, level/8 * tank - reserve_gal)
base miles         = usable_gal(level)       * mpg
low miles          = usable_gal(level - 1)   * mpg * 0.85
high miles         = usable_gal(level + 0.5) * mpg * 1.10
```

`level - 1` and `level + 0.5` express that an eighth is a band, not a point (rounding error alone
is about +-T/16). The 0.85 and 1.10 mpg factors express uncertain stop-and-go consumption, skewed
low. Errors are stacked, not combined statistically, in v1.

Derived: hours = miles / miles-per-hour; orders = miles / miles-per-order. Shown for low / base /
high; hours and orders rounded down, miles rounded to the nearest 5.

### Output

- Three numbers per row (low, base, high): miles, hours of work, approximate orders.
- Reserve shown in gallons and miles.
- A plain status line: if base miles is 0, "At or below your reserve: refuel soon"; if low
  miles is 0 but base is not, "Close to your reserve"; otherwise "Room before reserve".
- Caption: estimates only; set it before or between orders, never while driving.

### Module

`driver.py`: pure functions, no Streamlit or network imports.
`miles_until_reserve(level_eighths, tank_gal, mpg, reserve_gal)` returns `{"low","base","high"}`
miles; `reserve_gallons(tank_gal, mode)`; `to_hours_orders(miles, mph, miles_per_order)`.
`app.py` only collects inputs and renders.

## Out of scope for v1

Detour vs price break-even and the link to the Where tab; value of time; the three refuel states
(urgent / window / can wait); statistical buffer (quadrature); tank-to-tank mpg calibration;
"just filled" button and gallons-added input; saved shift history; weekday timing; any attempt to
read DoorDash data.

## Testing

- Unit tests (`tests/test_driver.py`): zero when level is below reserve; monotonic in level and
  mpg; low <= base <= high; level clamps at 0 and 8; Conservative reserve >= Standard; hours and
  orders handle zero miles and zero mph/miles-per-order without dividing by zero.
- One `AppTest` check that the tab renders with defaults and that a lower tank level never
  raises the base miles.

## Assumptions that are not sourced

These come from the research subagents' own reasoning, not from a cited source, and are
editable or labelled in the UI: 0.85 and 1.10 mpg factors; 2 gal floor and 15% reserve; 15
miles/hour and 8 miles/order defaults. Low-fuel-light volumes (about 1.7 to 2.6 gal) come from
third-party mirrors of owner's manuals. The 25% conservative preset is supported by AAA and fleet
policies. See `reports/Reserva de combustível para entregadores.md` and
`reports/Combustível em turnos de entregador.md`.

## Open questions

- Is a 4-eighth default level right, or should the tab start from the last value used?
- Should the standard reserve ask for the car's low-fuel-light gallons when the user knows it?
  (Left for v2.)
