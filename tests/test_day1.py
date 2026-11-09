import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import requests

import day1_check as d


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("boom https://x?api_key=SECRET", response=self)


class FakeSession:
    """Routes a request to a canned payload by URL."""

    def __init__(self, routes):
        self.routes = routes

    def _find(self, url):
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected url {url}")

    def get(self, url, **kw):
        return self._find(url)

    def post(self, url, **kw):
        return self._find(url)


def eia_payload(n=1100):
    data = [{"period": f"2005-{i:04d}", "value": "3.10"} for i in range(n)]
    return {"response": {"total": str(n), "data": data}}


FACETS = {"response": {"facets": [{"id": "SFL"}, {"id": "NUS"}, {"id": "YMIA"}]}}


def test_load_env_parses_and_does_not_override(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# c\nEIA_API_KEY=abc\nGOOGLE_API_KEY=\nX='q'\n")
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    monkeypatch.setenv("X", "keep")
    d.load_env(f)
    import os
    assert os.environ["EIA_API_KEY"] == "abc" and os.environ["X"] == "keep"


def test_check_eia_pass_and_missing_facet_fail():
    ok = FakeSession({"facet/duoarea": Resp(FACETS), "pri/gnd/data": Resp(eia_payload())})
    assert [r["status"] for r in d.check_eia("k", ok)] == ["PASS", "PASS"]
    bad = FakeSession({"facet/duoarea": Resp({"response": {"facets": [{"id": "NUS"}]}}),
                       "pri/gnd/data": Resp(eia_payload())})
    assert d.check_eia("k", bad)[0]["status"] == "FAIL"


def test_http_error_never_leaks_key():
    s = FakeSession({"facet/duoarea": Resp({}, 403), "pri/gnd/data": Resp({}, 403)})
    for r in d.check_eia("SECRET", s):
        assert r["status"] == "FAIL" and "SECRET" not in r["detail"] and "403" in r["detail"]


def test_places_warn_when_no_prices_and_pass_when_priced():
    station = {"id": "p1", "displayName": {"text": "Shell"},
               "location": {"latitude": 27.3, "longitude": -82.5}}
    none = FakeSession({"places:searchNearby": Resp({"places": [station]})})
    assert d.check_places("k", none)[0]["status"] == "WARN"
    priced = dict(station, fuelOptions={"fuelPrices": [{
        "type": "REGULAR_UNLEADED", "price": {"units": "3", "nanos": 150000000}}]})
    ok = FakeSession({"places:searchNearby": Resp({"places": [priced]})})
    r = d.check_places("k", ok)[0]
    assert r["status"] == "PASS" and "3.15" in r["detail"]


def test_routes_distance_sanity_and_run_without_keys():
    route = {"routes": [{"distanceMeters": 210000, "duration": "7200s",
                         "polyline": {"encodedPolyline": "abc"}}]}
    assert d.check_routes("k", FakeSession({"computeRoutes": Resp(route)}))[0]["status"] == "PASS"
    res = d.run(env={}, session=FakeSession({}))
    assert [r["status"] for r in res] == ["FAIL", "WARN"]
