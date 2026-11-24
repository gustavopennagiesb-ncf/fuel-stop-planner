"""Forecast baselines and rolling-origin evaluation for weekly gasoline prices.

The no-change forecast is very hard to beat for retail gasoline, so every model is reported
as a ratio to the naive forecast (MASE-style), and 'does not beat naive' is a valid result.

Crude signal: a small OLS regression of the next pump-price change on the last pump change and
the last two crude changes, refit on the expanding window (one fit per horizon). The verdict
threshold SIGNAL_THRESHOLD ($0.03/gal, an assumption, not an estimate) turns the forecast
into "wait" (<= -0.03), "fill" (>= +0.03) or "none".
"""
import numpy as np
import pandas as pd


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

    Weeks before the first or after the last published value are not missing data (the series
    simply starts later, e.g. EIA's Florida series begins in May 2003): they are trimmed, not
    invented, and counted in `n_trimmed`. Gaps inside the series are filled by time-ordered
    linear interpolation.
    Weekly changes beyond `z` SD are always *flagged*. outliers="keep" leaves them (price
    shocks are real events); "interpolate" replaces the flagged weeks by the mean of their
    neighbours. Returns (cleaned_series, report dict) so the UI can show before/after.
    """
    import pandas as pd
    s = pd.Series(s, dtype=float)
    n_all = len(s)
    first, last = s.first_valid_index(), s.last_valid_index()
    s = s.loc[first:last] if first is not None else s.iloc[0:0]
    n_missing = int(s.isna().sum())
    filled = s.interpolate(method="linear", limit_area="inside")
    idx = weekly_changes_outliers(filled.to_numpy(), z)
    cleaned = filled.copy()
    if outliers == "interpolate" and len(idx):
        mask = pd.Series(False, index=s.index)
        mask.iloc[idx] = True
        cleaned = cleaned.mask(mask).interpolate(method="linear", limit_direction="both")  # edge shocks: nearest week
    elif outliers not in ("keep", "interpolate"):
        raise ValueError("outliers must be 'keep' or 'interpolate'")
    report = {"n_missing_filled": n_missing, "n_trimmed": n_all - len(s),
              "start": str(first.date()) if hasattr(first, "date") else str(first),
              "n_outliers_flagged": int(len(idx)),
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


SIGNAL_THRESHOLD = 0.03  # $/gal; assumption: smaller expected moves are not worth acting on
_MIN_FIT_ROWS = 30
_MIN_SIGNAL_WEEKS = 104


def _features(p, c):
    """Feature rows for weeks t = 2..n-1: [1, dp[t], dc[t], dc[t-1]] (NaN where crude is missing)."""
    n = len(p)
    X = np.full((n, 4), np.nan)
    X[2:, 0] = 1.0
    X[2:, 1] = p[2:] - p[1:-1]
    X[2:, 2] = c[2:] - c[1:-1]
    X[2:, 3] = c[1:-1] - c[:-2]
    return X


def _fit(X, p, h, last):
    """OLS of p[t+h]-p[t] on X[t], using only rows whose target is known by week `last`.
    Returns coefficients or None when fewer than _MIN_FIT_ROWS complete rows exist."""
    t_max = last - h
    if t_max < 2:
        return None
    Xt, yt = X[2:t_max + 1], p[2 + h:t_max + 1 + h] - p[2:t_max + 1]
    ok = ~np.isnan(Xt).any(axis=1) & ~np.isnan(yt)
    if ok.sum() < _MIN_FIT_ROWS:
        return None
    return np.linalg.lstsq(Xt[ok], yt[ok], rcond=None)[0]


def crude_model(crude):
    """Forecast method (usable in rolling_origin_eval) from pump history + crude ($/gal)."""
    crude = np.asarray(crude, float)

    def method(train, h):
        p = np.asarray(train, float)
        c = crude[:len(p)]
        if len(c) < len(p) or len(p) < 3:
            return naive(p, h)
        X = _features(p, c)
        last = len(p) - 1
        out = []
        for k in range(1, h + 1):
            beta = _fit(X, p, k, last)
            if beta is None or np.isnan(X[last]).any():
                return naive(p, h)
            out.append(p[-1] + float(X[last] @ beta))
        return np.array(out)

    return method


def crude_signal(p, c, threshold=SIGNAL_THRESHOLD):
    """Next-week pump-price change forecast and a verdict; None without enough history."""
    p, c = np.asarray(p, float), np.asarray(c, float)
    n = len(p)
    if n < 3 or len(c) != n:
        return None
    X = _features(p, c)
    beta = _fit(X, p, 1, n - 1)
    ok = ~np.isnan(X[2:n - 1]).any(axis=1) & ~np.isnan(p[3:] - p[2:-1])
    if beta is None or ok.sum() < _MIN_SIGNAL_WEEKS or np.isnan(X[-1]).any():
        return None
    fc = float(X[-1] @ beta)
    verdict = "wait" if fc <= -threshold else "fill" if fc >= threshold else "none"
    return {"forecast": fc, "verdict": verdict,
            "pump_change_1w": float(p[-1] - p[-2]), "crude_change_2w": float(c[-1] - c[-3])}


_PERIODS = (("2005-2009", 2005, 2009), ("2010-2014", 2010, 2014),
            ("2015-2019", 2015, 2019), ("2020-2026", 2020, 2026))


def signal_track_record(p, c, threshold=SIGNAL_THRESHOLD, min_train=104, dates=None):
    """Replay the signal every week (expanding fit, h=1): how often did the called move happen?

    Returns {"wait": {"n", "hit"}, "fill": {"n", "hit"}} where hit is the share of called weeks
    whose next-week change went the called way, plus "ratio_by_period" (model MAE / naive MAE
    per period, labelled from `dates`) when `dates` is given."""
    p, c = np.asarray(p, float), np.asarray(c, float)
    X = _features(p, c)
    calls = {"wait": [], "fill": []}
    err_m, err_n, tgt = [], [], []
    for o in range(min_train, len(p) - 1):
        beta = _fit(X, p, 1, o)
        if beta is None or np.isnan(X[o]).any() or np.isnan(p[o + 1]):
            continue
        fc, actual = float(X[o] @ beta), p[o + 1] - p[o]
        err_m.append(abs(fc - actual))
        err_n.append(abs(actual))
        tgt.append(o + 1)
        if fc <= -threshold:
            calls["wait"].append(actual < 0)
        elif fc >= threshold:
            calls["fill"].append(actual > 0)
    out = {k: {"n": len(v), "hit": float(np.mean(v)) if v else float("nan")} for k, v in calls.items()}
    if dates is not None:
        years = np.asarray(pd.DatetimeIndex(dates).year)[tgt] if tgt else np.array([], int)
        em, en = np.asarray(err_m), np.asarray(err_n)
        ratios = {}
        for label, lo, hi in _PERIODS:
            m = (years >= lo) & (years <= hi)
            if m.any() and en[m].mean() > 0:
                ratios[label] = float(em[m].mean() / en[m].mean())
        out["ratio_by_period"] = ratios
    return out


def monthly_change_profile(series):
    """Mean weekly change by calendar month (index 1-12) of a date-indexed series."""
    d = pd.Series(series, dtype=float).dropna().diff().dropna()
    prof = d.groupby(pd.DatetimeIndex(d.index).month).mean()
    return prof.reindex(range(1, 13))
