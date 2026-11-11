"""Driver mode math: how far can I drive before I must refuel? Pure functions, no UI or network.

Status of each constant (sourced vs assumption):
- STANDARD_RESERVE_FRAC = 0.15 and MIN_RESERVE_GAL = 2.0: ASSUMPTIONS (a rule of thumb
  for a comfortable cushion; not taken from a published standard).
- CONSERVATIVE_RESERVE_FRAC = 0.25: supported by AAA advice to refill below a quarter
  tank and by fleet refuel policies. Conservative reserve is max(25% of tank, Standard
  reserve), so small tanks never get less than the Standard reserve.
- LOW_MPG_FACTOR = 0.85 and HIGH_MPG_FACTOR = 1.10: ASSUMPTIONS (real mpg varies with
  speed, load and weather; these set a pessimistic and an optimistic case).
- LOW_LEVEL_DELTA = -1.0 and HIGH_LEVEL_DELTA = 0.5 (in eighths of a tank): derived from
  rounding arithmetic. A gauge read to the nearest eighth can be off by half an eighth
  either way; the low side gets a full eighth as extra margin for a pessimistic case.
- DEFAULT_MILES_PER_HOUR = 15.0 and DEFAULT_MILES_PER_ORDER = 8.0: ASSUMPTIONS (no
  measured source and no Florida data; the user is expected to adjust them).
"""

import math

STANDARD_RESERVE_FRAC = 0.15
MIN_RESERVE_GAL = 2.0
CONSERVATIVE_RESERVE_FRAC = 0.25
LOW_MPG_FACTOR = 0.85
HIGH_MPG_FACTOR = 1.10
LOW_LEVEL_DELTA = -1.0
HIGH_LEVEL_DELTA = 0.5
DEFAULT_MILES_PER_HOUR = 15.0  # assumption, see module docstring
DEFAULT_MILES_PER_ORDER = 8.0  # assumption, see module docstring


def reserve_gallons(tank_gal: float, mode: str = "standard") -> float:
    """Gallons to keep in the tank. mode is "standard" or "conservative"."""
    standard = max(STANDARD_RESERVE_FRAC * tank_gal, MIN_RESERVE_GAL)
    if mode == "standard":
        return standard
    if mode == "conservative":
        return max(CONSERVATIVE_RESERVE_FRAC * tank_gal, standard)
    raise ValueError(f"unknown reserve mode: {mode!r}")


def _miles(level_eighths: float, tank_gal: float, mpg: float, reserve_gal: float) -> float:
    level = min(max(level_eighths, 0.0), 8.0)
    usable = max(0.0, level / 8.0 * tank_gal - reserve_gal)
    return usable * mpg


def miles_until_reserve(level_eighths: float, tank_gal: float, mpg: float,
                        reserve_gal: float) -> dict:
    """Miles until the tank hits the reserve: low / base / high cases, each >= 0."""
    return {
        "low": _miles(level_eighths + LOW_LEVEL_DELTA, tank_gal, mpg * LOW_MPG_FACTOR, reserve_gal),
        "base": _miles(level_eighths, tank_gal, mpg, reserve_gal),
        "high": _miles(level_eighths + HIGH_LEVEL_DELTA, tank_gal, mpg * HIGH_MPG_FACTOR, reserve_gal),
    }


def round_miles(miles: float) -> int:
    """Round to the nearest 5 miles, halves round up."""
    return int(math.floor(miles / 5.0 + 0.5)) * 5


def to_hours_orders(miles: float, miles_per_hour: float, miles_per_order: float) -> tuple:
    """Convert miles to (whole hours, whole orders), both rounded down."""
    if miles_per_hour <= 0 or miles_per_order <= 0:
        raise ValueError("miles_per_hour and miles_per_order must be positive")
    if miles <= 0:
        return (0, 0)
    miles = round(miles, 6)  # absorb float noise such as 254.99999999999997
    return (int(miles // miles_per_hour), int(miles // miles_per_order))


def status(low: float, base: float) -> str:
    """One-line state from the low and base miles-until-reserve."""
    if base <= 0:
        return "At or below your reserve: refuel soon"
    if low <= 0:
        return "Close to your reserve"
    return "Room before reserve"
