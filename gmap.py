"""Google Maps JavaScript embed for google mode.

Google's terms require Places/Routes content to be shown on a Google map. This builds a
self-contained HTML page (rendered with st.components.v1.html) that draws the route and the
ranked stations. GOOGLE_MAPS_JS_KEY is a separate, referrer-restricted browser key: it is meant
to be visible in the page, unlike the server-side GOOGLE_API_KEY, which is never used here.
"""
import json
from urllib.parse import quote

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body,#map{height:100%;margin:0}</style></head><body><div id="map"></div>
<script>
const ROUTE = __ROUTE__;
const STATIONS = __STATIONS__;
function initMap() {
  const map = new google.maps.Map(document.getElementById("map"), {mapTypeControl: false});
  const bounds = new google.maps.LatLngBounds();
  ROUTE.forEach(p => bounds.extend(p));
  new google.maps.Polyline({path: ROUTE, map: map, strokeColor: "#1f77b4", strokeWeight: 4});
  const info = new google.maps.InfoWindow();
  STATIONS.forEach(s => {
    const pos = {lat: s.lat, lng: s.lng};
    const m = new google.maps.Marker({position: pos, map: map, title: s.name,
      label: {text: s.price, fontSize: "11px", fontWeight: "bold"}});
    m.addListener("click", () => {
      const box = document.createElement("div");   // textContent: place names are untrusted
      box.textContent = s.text;
      info.setContent(box);
      info.open(map, m);
    });
    bounds.extend(pos);
  });
  map.fitBounds(bounds);
}
</script>
<script async src="https://maps.googleapis.com/maps/api/js?key=__KEY__&callback=initMap&loading=async"></script>
</body></html>"""


def _js(obj):
    """JSON that is safe inside a <script> element (no early </script>, no U+2028)."""
    return (json.dumps(obj).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def build_map_html(js_key, path, ranked):
    """path: [(lat, lng)]; ranked: stations with name, price, net_benefit, lat, lng."""
    route = [{"lat": lat, "lng": lng} for lat, lng in path]
    stations = [{"lat": r["lat"], "lng": r["lng"], "name": r["name"],
                 "price": f"{r['price']:.2f}",
                 "text": f"{r['name']}: ${r['price']:.2f}/gal (net ${r['net_benefit']:.2f})"}
                for r in ranked]
    return (TEMPLATE.replace("__ROUTE__", _js(route)).replace("__STATIONS__", _js(stations))
            .replace("__KEY__", quote(js_key, safe="")))
