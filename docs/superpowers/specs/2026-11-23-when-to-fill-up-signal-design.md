# When to fill up: a crude-oil signal with a clear verdict

Approved 2026-11-23 (short design).

## Why
The "When to fill up" page ends on a vague answer ("timing the week doesn't pay") backed by a
ratio table that assumes the reader knows what the naive forecast is. The user wants a final,
practical answer (fill up now, or wait) grounded in correlations from the data. The assignment
also asks for correlation analysis between key variables.

## What the data showed (full-history check before designing)
- Weekly changes in crude oil (EIA WTI spot, $/gal) correlate with Florida pump changes in the
  same week (r = 0.46) and the next (r = 0.37); the effect is gone after two weeks. Pump changes
  carry momentum (lag-1 r = 0.23).
- An OLS model on last week's pump change and the last two crude changes, refitted on an
  expanding window (rolling origin, every week, 2005–2026, 1,121 forecasts): mean miss 5.5¢ vs
  6.0¢ for naive at 1 week (ratio 0.91), 0.93 at 2 weeks, 0.98 at 4 weeks. It wins in every
  period but less recently (0.82 in 2005–09, 0.97 in 2020–26).
- When it forecast a drop of 3¢ or more (197 weeks) the price fell 87% of the time (−5.8¢ on
  average); a rise of 3¢ or more was right 61% of the time.

## Goals
- A verdict card: "Wait a week if you can" (forecast ≤ −3¢), "Fill up now" (≥ +3¢) or
  "No clear signal", with the forecast in ¢/gal and in dollars on the gallons the app uses, the
  reason (crude and pump changes), and the model's track record for that verdict.
- The why: crude→pump correlation by lag (plus momentum), the average weekly change by month,
  and the forecast test in cents, including the weaker recent years.
- Same test for every model (the crude model joins `rolling_origin_eval`).

## Non-goals
Daily prices, day-of-week effects, other states, changes to the assistant.

## Decisions
- Data: EIA series `PET.RWTC.W` (WTI spot, $/bbl, week ending Friday) through the v2
  `seriesid` endpoint, stored as a `wti` column in `data/eia_weekly.csv`; a Friday value is
  aligned to the following Monday's retail survey. If the crude fetch fails the retail data
  still loads and the page falls back to today's answer.
- Model in `forecast.py` (pure): direct OLS per horizon on
  `[1, Δpump(t), Δcrude(t), Δcrude(t−1)]`, crude in $/gal (÷ 42).
- Threshold 3¢ is an assumption, stated in the module and on the page.
- Synthetic mode gets a synthetic crude series that leads the synthetic U.S. price, so the page
  works without keys; existing synthetic retail values do not change.

## Left to the builder
Chart styling, exact copy, caching of the evaluation.

## Approaches considered
1. Crude signal + momentum (chosen): adds one free EIA series, beats naive out of sample,
   gives a verdict with money.
2. Momentum only: no new data, weaker and untested alone.
3. Correlations as context only: keeps the vague verdict the user rejected.
