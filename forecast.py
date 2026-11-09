"""Forecast baselines and rolling-origin evaluation for weekly gasoline prices.

The no-change forecast is very hard to beat for retail gasoline, so every model is reported
as a ratio to the naive forecast (MASE-style), and 'does not beat naive' is a valid result.
"""
import numpy as np


def naive(y, h):
    return np.repeat(y[-1], h)


def drift(y, h):
    slope = (y[-1] - y[0]) / (len(y) - 1)
    return y[-1] + slope * np.arange(1, h + 1)


def moving_average(y, h, k=4):
    return np.repeat(np.mean(y[-k:]), h)


def seasonal_naive(y, h, season=52):
    if len(y) < season:
        return naive(y, h)
    return np.array([y[-season + (i % season)] for i in range(h)])


METHODS = {"naive": naive, "drift": drift, "moving_avg_4": moving_average,
           "seasonal_naive_52": seasonal_naive}


def rolling_origin_eval(y, horizons=(1, 2, 4), min_train=104, step=4, methods=None):
    """Expanding-window evaluation. Returns {method: {h: {'mae', 'ratio_to_naive'}}}."""
    y = np.asarray(y, float)
    methods = methods or METHODS
    errs = {m: {h: [] for h in horizons} for m in methods}
    for origin in range(min_train, len(y) - max(horizons), step):
        train = y[:origin]
        for m, fn in methods.items():
            for h in horizons:
                pred = fn(train, h)[h - 1]
                errs[m][h].append(abs(pred - y[origin + h - 1]))
    out = {}
    for m in methods:
        out[m] = {}
        for h in horizons:
            mae = float(np.mean(errs[m][h]))
            base = float(np.mean(errs["naive"][h])) if "naive" in errs else float("nan")
            out[m][h] = {"mae": round(mae, 4), "ratio_to_naive": round(mae / base, 3)}
    return out


def weekly_changes_outliers(y, z=3.0):
    """Indices of weekly changes whose z-score exceeds z (price shocks)."""
    d = np.diff(np.asarray(y, float))
    sd = d.std()
    return np.where(np.abs(d - d.mean()) > z * sd)[0] + 1 if sd > 0 else np.array([], int)


def treat_series(s, z=3.0, outliers="keep"):
    """Missing-data and outlier treatment for a weekly price series (pandas Series).

    Missing weeks are filled by time-ordered linear interpolation (edges by nearest value).
    Weekly changes beyond `z` SD are always *flagged*. outliers="keep" leaves them (price
    shocks are real events); "interpolate" replaces the flagged weeks by the mean of their
    neighbours. Returns (cleaned_series, report dict) so the UI can show before/after.
    """
    import pandas as pd
    s = pd.Series(s, dtype=float)
    n_missing = int(s.isna().sum())
    filled = s.interpolate(method="linear", limit_direction="both")
    idx = weekly_changes_outliers(filled.to_numpy(), z)
    cleaned = filled.copy()
    if outliers == "interpolate" and len(idx):
        mask = pd.Series(False, index=s.index)
        mask.iloc[idx] = True
        cleaned = cleaned.mask(mask).interpolate(method="linear", limit_direction="both")
    elif outliers not in ("keep", "interpolate"):
        raise ValueError("outliers must be 'keep' or 'interpolate'")
    report = {"n_missing_filled": n_missing, "n_outliers_flagged": int(len(idx)),
              "outlier_weeks": [str(s.index[i]) for i in idx],
              "outlier_treatment": outliers,
              "mean_before": round(float(s.mean()), 4), "mean_after": round(float(cleaned.mean()), 4),
              "std_before": round(float(s.std()), 4), "std_after": round(float(cleaned.std()), 4)}
    return cleaned, report


def lagged_corr(a, b, max_lag=8):
    """Correlation of weekly changes of `a` (t) with changes of `b` (t - lag).
    Positive lag means `b` leads `a`. Returns {lag: corr}. Used for U.S. -> Florida."""
    import pandas as pd
    d = pd.concat([pd.Series(a), pd.Series(b)], axis=1, keys=["a", "b"]).dropna().diff().dropna()
    return {lag: round(float(d["a"].corr(d["b"].shift(lag))), 3) for lag in range(0, max_lag + 1)}
