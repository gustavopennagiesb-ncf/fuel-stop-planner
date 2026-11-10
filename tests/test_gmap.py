import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from gmap import build_map_html

RANKED = [{"lat": 27.6, "lng": -82.3, "name": "Shell</script><b>x", "price": 3.459,
           "net_benefit": 1.234}]


def test_map_html_contains_route_station_and_key():
    html = build_map_html("KEY 1&", [(27.3, -82.5), (28.0, -82.1)], RANKED)
    assert "maps.googleapis.com/maps/api/js?key=KEY%201%26&" in html
    assert '"lat": 27.3' in html and "$3.46/gal (net $1.23)" in html


def test_map_html_escapes_untrusted_station_names():
    html = build_map_html("k", [(27.3, -82.5)], RANKED)
    assert html.count("</script>") == 2          # only the template's own two closers
    assert "\\u003c/script\\u003e" in html
