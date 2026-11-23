import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from gmap import build_map_html

RANKED = [{"lat": 27.6, "lng": -82.3, "name": "Shell</script><b>x", "price": 3.459,
           "net_benefit": 1.234}]


def test_map_html_contains_route_station_and_key():
    html = build_map_html("KEY 1&", [(27.3, -82.5), (28.0, -82.1)], RANKED)
    assert "maps.googleapis.com/maps/api/js?key=KEY%201%26&" in html
    assert '"lat": 27.3' in html and "$3.46/gal (saves $1.23)" in html


def test_map_html_escapes_untrusted_station_names():
    html = build_map_html("k", [(27.3, -82.5)], RANKED)
    assert html.count("</script>") == 2          # only the template's own two closers
    assert "\\u003c/script\\u003e" in html


def test_the_recommended_station_is_marked_and_the_others_are_not():
    ranked = [{"id": "p1", "name": "A", "price": 3.5, "net_benefit": 1.0, "lat": 27.0, "lng": -82.0},
              {"id": "p2", "name": "B", "price": 3.6, "net_benefit": 0.5, "lat": 27.1, "lng": -82.1}]
    html = build_map_html("key", [(27.0, -82.0), (28.0, -81.0)], ranked, best_id="p1")
    assert html.count('"best": true') == 1 and html.count('"best": false') == 1
    assert html.index('"best": true') < html.index('"best": false')


def test_no_best_id_marks_nothing():
    ranked = [{"id": "p1", "name": "A", "price": 3.5, "net_benefit": 1.0, "lat": 27.0, "lng": -82.0}]
    html = build_map_html("key", [(27.0, -82.0)], ranked)
    assert '"best": true' not in html


def test_the_compact_map_is_a_static_preview():
    from gmap import build_map_html
    s = {"id": "a", "name": "A", "lat": 27.5, "lng": -82.4, "price": 3.3, "net_benefit": 0.5}
    small = build_map_html("k", [(27.3, -82.5), (27.6, -82.3)], [s], best_id="a", compact=True)
    big = build_map_html("k", [(27.3, -82.5), (27.6, -82.3)], [s], best_id="a")
    assert "const COMPACT = true;" in small and "const COMPACT = false;" in big
