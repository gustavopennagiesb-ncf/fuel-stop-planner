"""Day-1 live checks for the Fuel Stop Planner (run on your own machine, with keys in .env).

    python scripts/day1_check.py

Checks, in order: EIA facets and weekly data, Google Places fuel prices near Sarasota,
Google Routes Sarasota -> Orlando. It prints PASS / WARN / FAIL per check and never prints
an API key. Places uses the paid SKU: this script makes 1 Places call and 1 Routes call.
"""
import os
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from providers import eia, places, routes  # noqa: E402

FACET_URL = "https://api.eia.gov/v2/petroleum/pri/gnd/facet/duoarea"
SARASOTA = (27.3364, -82.5307)


def load_env(path):
    """Read KEY=VALUE lines from a .env file into os.environ (existing variables win)."""
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)


def safe(name, fn):
    """Run fn() -> (status, detail). Errors become FAIL without ever echoing the URL (it
    contains the API key for EIA), only the HTTP status and a short body excerpt."""
    try:
        status, detail = fn()
    except requests.HTTPError as e:
        body = (e.response.text or "")[:300].replace("\n", " ") if e.response is not None else ""
        code = e.response.status_code if e.response is not None else "?"
        status, detail = "FAIL", f"HTTP {code}: {body}"
    except Exception as e:  # noqa: BLE001 - report any problem as a failed check
        status, detail = "FAIL", f"{type(e).__name__}: {str(e)[:200]}"
    return {"name": name, "status": status, "detail": detail}


def check_eia(api_key, session=requests):
    def facets():
        r = session.get(FACET_URL, params={"api_key": api_key}, timeout=30)
        r.raise_for_status()
        ids = {f["id"] for f in r.json()["response"]["facets"]}
        missing = {"SFL", "NUS", "YMIA"} - ids
        if missing:
            return "FAIL", f"duoarea facet is missing {sorted(missing)}; update AREAS in providers/eia.py"
        return "PASS", "duoarea contains SFL, NUS, YMIA"

    def weekly():
        rows = eia.fetch_all(api_key, "SFL", session=session, start="2003-01-01")
        if len(rows) < 500:
            return "FAIL", f"only {len(rows)} weekly Florida rows (expected 1000+)"
        values = [v for _, v in rows]
        if not all(0.5 < v < 8 for v in values):
            return "FAIL", "Florida prices outside $0.50-$8.00: wrong product/process facet?"
        return "PASS", f"{len(rows)} weekly rows, {rows[0][0]} to {rows[-1][0]}, latest ${rows[-1][1]:.3f}"

    return [safe("EIA facets", facets), safe("EIA weekly Florida", weekly)]


def check_places(api_key, session=requests):
    def nearby():
        st = places.search_nearby(api_key, *SARASOTA, radius_m=5000, session=session)
        priced = [s for s in st if s["price"] is not None]
        if not st:
            return "FAIL", "Places returned no gas stations near Sarasota"
        if not priced:
            return ("WARN", f"{len(st)} stations but NONE has a regular price: the 'where' half "
                            "must use typed prices (the app already supports that)")
        return "PASS", f"{len(st)} stations, {len(priced)} with a regular price (e.g. {priced[0]['name']} ${priced[0]['price']:.2f})"

    return [safe("Places fuel prices (Sarasota)", nearby)]


def check_routes(api_key, session=requests):
    def route():
        r = routes.compute_route(api_key, "Sarasota, FL", "Orlando, FL", session=session)
        km = r["distanceMeters"] / 1000
        if not 150 < km < 300:
            return "FAIL", f"Sarasota->Orlando came back as {km:.0f} km"
        return "PASS", f"{km:.0f} km, {r['seconds'] / 60:.0f} min, polyline length {len(r['polyline'])}"

    return [safe("Routes Sarasota -> Orlando", route)]


def run(env=None, session=requests):
    env = os.environ if env is None else env
    results = []
    if env.get("EIA_API_KEY"):
        results += check_eia(env["EIA_API_KEY"], session)
    else:
        results.append({"name": "EIA", "status": "FAIL", "detail": "EIA_API_KEY missing in .env"})
    if env.get("GOOGLE_API_KEY"):
        results += check_places(env["GOOGLE_API_KEY"], session)
        results += check_routes(env["GOOGLE_API_KEY"], session)
    else:
        results.append({"name": "Google", "status": "WARN",
                        "detail": "GOOGLE_API_KEY missing: Places and Routes skipped"})
    return results


def main():
    load_env(ROOT / ".env")
    results = run()
    for r in results:
        print(f"[{r['status']}] {r['name']}: {r['detail']}")
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
