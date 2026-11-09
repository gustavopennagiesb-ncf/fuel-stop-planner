"""EIA Open Data API v2: weekly retail regular gasoline (Florida and U.S.).

Route and facet codes are inferred from EIA series IDs (EMM_EPMR_PTE_SFL_DPG etc.) and
confirmed only by secondary sources: verify with /facet/duoarea on day 1. No live response
has been captured yet, so tests use a synthetic payload shaped like the documented envelope.
"""
import pandas as pd
import requests

BASE = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"
AREAS = {"florida": "SFL", "us": "NUS", "miami": "YMIA"}


def build_params(api_key, duoarea, product="EPMR", process="PTE", start=None,
                 offset=0, length=5000):
    p = {"api_key": api_key, "frequency": "weekly", "data[0]": "value",
         "facets[duoarea][]": duoarea, "facets[product][]": product,
         "facets[process][]": process, "sort[0][column]": "period",
         "sort[0][direction]": "desc", "offset": offset, "length": length}
    if start:
        p["start"] = start
    return p


def parse_rows(payload):
    """Return (total, [(period, float_value)]) ascending; skips null/'.' values."""
    resp = payload["response"]
    total = int(resp["total"])  # may arrive as a string
    rows = []
    for r in resp["data"]:
        v = r.get("value")
        if v in (None, "", ".", "NA", "W"):
            continue
        rows.append((r["period"], float(v)))
    rows.sort()
    return total, rows


def fetch_all(api_key, duoarea, session=None, **kw):
    s = session or requests
    out, offset = [], 0
    while True:
        r = s.get(BASE, params=build_params(api_key, duoarea, offset=offset, **kw), timeout=30)
        r.raise_for_status()
        total, rows = parse_rows(r.json())
        out += rows
        offset += 5000
        if offset >= total:
            return sorted(set(out))


def load_live(api_key, areas=("florida", "us"), start="2003-01-01") -> pd.DataFrame:
    """One column per area, indexed by week (Monday)."""
    cols = {}
    for name in areas:
        rows = fetch_all(api_key, AREAS[name], start=start)
        cols[name] = pd.Series({pd.Timestamp(p): v for p, v in rows})
    return pd.DataFrame(cols).sort_index().rename_axis("period")
