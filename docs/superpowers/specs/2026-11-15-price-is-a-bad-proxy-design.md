# Design: "the cheapest station is often the wrong one", measured

Status: short design for a small change, awaiting approval. No code written.

## Why

The app's real differentiator is that it prices the *detour*, not just the fuel. Today that
claim rests on one anecdote in the report (a Chevron at $3.28 that saves less than one at
$3.36). The goal is to turn the claim into a **measured finding**, and to connect the two halves
of the app, which currently never speak to each other.

Said by the user: improvements inside this app, aimed at the top grade, and original. They chose
approaches 1 and 3 below.

## Goals

- Produce a quantitative result, from the app's own data, that answers: **how often, and by how
  much, does choosing the cheapest station cost you money?**
- Tell the user whether the corridor they are on is cheap or expensive versus the state trend,
  joining the EIA half to the stations half.

## Non-goals

- No new API calls. Both features use data the app has already fetched.
- No change to the net-benefit formula or to the recommendation itself.
- Not a multi-route study: measuring across many routes would mean many paid searches.

## Approaches considered

1. **Measure price-versus-benefit disagreement on the current route** (chosen). Uses data in
   memory; no cost; produces a real number.
2. **Measure LLM hallucination rate** (deferred). Highest payoff on the LLM criterion, but
   blocked on an API key that does not exist yet.
3. **Join the EIA series to the route prices** (chosen). Cheap, and fixes the app reading as two
   disconnected products.

A fourth was dropped: measuring across several routes, which would need repeated paid searches
for a result the single-route grid already supports.

## What gets built

### A. `insight.py` — a new pure module (no Streamlit, no network)

- `regret(ranked)` → the dollars lost by filling at the cheapest station instead of the one with
  the highest net benefit, on the current inputs. Zero when they are the same station.
- `price_rank_gap(ranked)` → where the cheapest station actually sits in the net-benefit
  ranking (1 means price picked the winner), so "price is a good proxy" becomes checkable.
- `decision_grid(stations, p_ref, mpg, speed, gallons_values, vot_values)` → re-ranks the same
  stations for each combination of fill size and value of time, and returns, for each cell,
  whether the cheapest station was also the best. The headline is the share of combinations in
  which **price alone picked the wrong station**. This is the originality: the result is a
  frequency, not an anecdote, and it costs nothing because `detour.rank_stations` is pure.
- `flip_point(ranked_at_vot)` → the value of time at which the recommendation changes, when one
  exists ("below $12/h the Costco wins; above it, stay on route").

### B. `corridor_vs_state(route_prices, state_latest)` in the same module

Median price of the priced stations on the route against the latest weekly state price, with the
difference in cents and a plain verdict (cheap / about the same / expensive corridor).

### C. Where it shows

- In **Where to stop**, under the recommendation: one line from `corridor_vs_state`, and one
  line with the regret ("choosing the cheapest station here would cost you $0.75").
- A new expander, **"Is the cheapest station the best one?"**, holding the grid result, the
  share, and the flip point. Kept in an expander so the answer-first layout survives.

### D. Honesty rules, consistent with the rest of the app

- The grid result is labelled as **this route, these stations, today**, never as a general law.
- The ranges of fill size and value of time used in the grid are stated on screen.
- Every figure comes from the same `detour.rank_stations` the recommendation uses, so the panel
  cannot disagree with the headline.

## Testing

TDD, as in the rest of the project. `tests/test_insight.py` covers: regret is zero when the
cheapest is the best; regret is positive and equals the net-benefit difference otherwise; the
grid counts disagreements correctly on a hand-built set of stations; the flip point is found when
one exists and is `None` when it does not; `corridor_vs_state` gets the sign and the wording
right at, above and below the state price. One `AppTest` check that the new expander renders and
that the figures match the recommendation shown above it.

## Left to the builder

Chart or table for the grid (a small heat map matching the Sensitivity tab would be consistent);
exact wording of the verdicts.
