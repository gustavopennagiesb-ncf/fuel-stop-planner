# Demo Script — Fuel Stop Planner (15 min, 9 slides + live demo)

Matches the Google Slides deck *Fuel Stop Planner* (9 slides). The speaker notes on each slide
hold the same text; this file adds timing and the click-by-click demo.

## Before the class (10 min early)

1. Start the app with real data:
   ```bash
   PROVIDER=google .venv/bin/streamlit run app.py
   ```
2. Open it in the browser once and click **When to fill up**, so the weekly prices are loaded
   (the first load can take 20–30 s when EIA is slow; after that it is cached).
3. Leave the app **empty** (do not choose a route yet: the demo starts from the empty app).
4. Open the slides in presenter view; keep the app in a second tab.

If the Wi-Fi fails during the demo: stop the app and run
`FUEL_DEMO_TRIP=1 .venv/bin/streamlit run app.py` (synthetic data, opens with the demo trip
already chosen). Say once that it is synthetic data; the badge in the sidebar says so too.

## Timing

| # | Slide | Time | Running |
|---|---|---|---|
| 1 | Fuel Stop Planner (cover) | 0:30 | 0:30 |
| 2 | What the app does | 1:00 | 1:30 |
| 3 | Six APIs, all JSON over HTTPS | 1:00 | 2:30 |
| 4 | First: can we trust the data? | 1:00 | 3:30 |
| 5 | What gas prices look like | 1:00 | 4:30 |
| 6 | Florida moves with the whole U.S. | 1:00 | 5:30 |
| 7 | Crude oil moves first; spring costs more | 1:00 | 6:30 |
| 8 | What a stop really saves | 1:30 | 8:00 |
| 9 | Sarasota → Orlando, real data (then switch to the app) | 5:30 | 13:30 |
| — | Close and questions | 1:30 | 15:00 |

---

## 1. Cover (0:30)

"Hi, I'm Gustavo. This is my final project for Programming for Data Science: the Fuel Stop
Planner. It answers a question every driver settles on gut feeling: is the cheap gas station
worth the detour?"

## 2. What the app does (1:00)

"It's a web app for drivers, built in Python with Streamlit. You tell it where you're going and
what car you drive, and it answers three money questions. One: where should I stop? It ranks
every station on the route by what it really saves once you pay for the detour in fuel and
time. Two: do I need to stop at all? From the fuel already in the tank, it says which stations
you can still reach and how many gallons to buy. Three: should I fill up now or wait? Crude-oil
prices give about a week's warning. And an AI assistant explains every answer, with every number
checked against the app's own math."

## 3. Six APIs (1:00)

"Six APIs, all returning JSON. Three from Google: Routes for the driving path and the real
detour to each station, Places for the stations and their prices, and the Maps JavaScript API
for the map and the address search. Those cost a few cents per search. Two are free U.S.
government data: the EIA for weekly gas prices since 2003 and crude oil, and the EPA's
fueleconomy.gov for the car's mpg. And Groq, on its free tier, writes the assistant's answers.
The keys never reach the browser, Google results are never saved, and without keys the app runs
on synthetic data."

## 4–7. Exploring the data (4:00, about 1 minute each)

**4. Can we trust the data?** "Before trusting any result, I checked the data: 1,227 weeks of
Florida prices, every week since May 2003. No missing weeks; the weeks before Florida's data
starts are left out, not invented. The app flagged 18 sudden jumps; I kept them because they
really happened."

**5. What prices look like.** "Over the years prices swing a lot: $1.74 in 2020, $4.85 in 2022.
But week to week they usually move only about 4 cents. And Florida is usually a bit cheaper than
the U.S. average."

**6. Florida and the U.S.** "Correlation means: do two things move together? 0 is no link, 1 is
moving exactly together. Florida and U.S. weekly changes are at 0.80, in the same week: a very
strong link. On the right, every dot is a week, and they sit on one straight line."

**7. Crude oil and the season.** "Gas is made from crude oil. When crude changes, the pump price
follows the same week and the next; after two weeks the effect is gone. So crude gives about a
week's warning, and that is how the app decides 'wait' or 'fill up now'. Prices also tend to rise
from February to April and fall from October to December."

## 8. What a stop really saves (1:30)

"This is the whole idea in one formula. A cheaper station saves the price gap times the gallons.
But you pay twice for the detour: the fuel to get there, and your time. In this real example,
Circle K is 30 cents cheaper, so the sign promises $3. After 21 cents of fuel and $2.12 of time —
6.7 minutes at $19 an hour — the real saving is 67 cents."

## 9. Live demo (5:30) — switch to the app

Say: "The app opens empty, so you can see every choice change it."

1. **Choose the route (1:00).** Open **Where are you going?** Type `Sarasota` and **click the
   suggestion**; type `Orlando` and **click the suggestion**. Scroll to the bottom of the box and
   press **Find stations**. While it loads (about 20 s): "It asks Google for the route, then for
   the stations along it, and measures the real detour of the most promising ones." The summary
   shows the route; step 1 of the welcome gets a check.
2. **Pick the car (0:45).** Open **Pick your car** and look up a car (Year, Make, Model, Version),
   or type its mpg. The recommendation and the map appear. Point at the green card: station,
   price, what it saves, detour.
3. **Cheapest vs best (1:00).** Scroll to the cheapest-station card: "This is the cheapest sign,
   but after the detour it saves less, or even costs money."
4. **Fuel gauge (1:15).** Open **How much fuel do you have?** → Fuel gauge. Set **F**: "No stop
   needed." Set **1/4**: "Now it only suggests stations I can reach before my reserve, and how
   many gallons to buy."
5. **Ask the assistant (1:15).** Click **Assistant** (bottom-right corner) and ask *"Should I
   fill up now or wait?"*. Point at the green **All numbers traced** badge: "Python computes every
   number; the model only writes the sentence, and a checker traces each number back to the
   app's math."
   - On **Monday to Wednesday** (the demo is on a **Tuesday**) EIA has not published that week's
     crude price yet, so the app uses the latest crude week and a slightly weaker record
     (around 78% instead of 87%); the page says so.
6. *(Optional, if time)* Click **When to fill up** to show the verdict card, or **EDA** to show
   the charts from slides 4–7 live.

## Close (0:30) and questions

"So: the cheapest sign is often not the best stop, and the app shows why with real data and
simple math. Thank you — happy to take questions."

### Likely questions

- **Why not GasBuddy's prices?** Their API is a paid business product; Google Places is the best
  per-station price source available to a student project. Stations without a price are shown,
  and you can type a price in the app.
- **How do you know the formula is right?** It is plain cost arithmetic: the saving minus the two
  costs of the detour. Because it is linear, the break-even point is exact.
- **Does the crude-oil model really work?** Tested on every week since 2005, using only past data
  each time: it misses by 5.5¢ instead of 6.0¢ for "no change", and when it said "wait" the price
  fell 87% of the time. The edge is smaller since 2020, and the app says so.
- **Did you use AI to write the code?** Claude Code as an assistant: I reviewed every change, ran
  the tests (402 of them, no network needed), and checked the numbers against live data — which
  is how the detour-measurement problem was found and fixed.
- **What would you add next?** Price freshness from Google's update time, plans with more than one
  stop for long trips, and testing on more routes.
