"""An address box with suggestions, like Google Maps: type, and the options appear below.

It is Google's own PlaceAutocompleteElement (Maps JavaScript API, "places" library), mounted as a
Streamlit component. It runs in the browser with the referrer-restricted browser key
(GOOGLE_MAPS_JS_KEY, meant to be visible in the page), which must allow the Places API (New). The
widget manages its own billing session: typing is free of charge within the monthly cap and
picking a suggestion fetches the address and coordinates once.

Everything the browser sends back is untrusted: parse_pick validates numbers and text, and errors
arrive as numeric codes mapped to the fixed MESSAGES (never free text from the page).
"""
import math
import re

MESSAGES = {
    "unsupported": "This browser cannot show address suggestions. Type the address instead.",
    "unavailable": "Address suggestions are not available right now (the browser key may not "
                   "allow the Places API). Type the address instead.",
    "unreadable": "The address received could not be read. Type the address instead.",
}
_CODES = {0: "unsupported", 1: "unavailable"}
_PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,300}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
MAX_LABEL = 200

CSS = """
.ac { width: 100%; }
gmp-place-autocomplete { width: 100%; color-scheme: light; }
"""
# Trusted, static script: nothing user-provided is put into it. The key arrives as component data.
JS = """
function loadMaps(key) {
  if (window.google && google.maps && google.maps.importLibrary) return Promise.resolve();
  if (!window.__fuelMapsPromise) {
    window.__fuelMapsPromise = new Promise((resolve, reject) => {
      window.__fuelMapsReady = resolve;   // the API calls this when importLibrary is available
      const s = document.createElement('script');
      s.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(key) +
              '&loading=async&v=weekly&callback=__fuelMapsReady';
      s.async = true; s.onerror = () => reject(new Error('load'));
      document.head.appendChild(s);
    });
  }
  return window.__fuelMapsPromise;
}
export default function(component) {
  const { data, setTriggerValue, parentElement } = component;
  const box = parentElement.querySelector('.ac');
  if (box.dataset.ready) return;
  box.dataset.ready = '1';
  loadMaps(data.key).then(async () => {
    const { PlaceAutocompleteElement } = await google.maps.importLibrary('places');
    const el = new PlaceAutocompleteElement({ includedRegionCodes: data.regions || ['us'] });
    if (data.placeholder) { el.placeholder = data.placeholder; }
    el.addEventListener('gmp-select', async ({ placePrediction }) => {
      try {
        const place = placePrediction.toPlace();
        await place.fetchFields({ fields: ['formattedAddress', 'displayName', 'location'] });
        setTriggerValue('pick', { lat: place.location.lat(), lng: place.location.lng(),
                                  label: place.formattedAddress || place.displayName,
                                  place_id: place.id });
      } catch (e) { console.error('address suggestions (pick):', e); setTriggerValue('pick', { error_code: 1 }); }
    });
    box.appendChild(el);
  }).catch((e) => { console.error('address suggestions:', e); setTriggerValue('pick', { error_code: 1 }); });
}
"""
HTML = '<div class="ac"></div>'

_renderer = None


def request_pick(key, js_key, placeholder="Type an address"):
    """Show the address box. Returns what the browser sent on the run right after a pick."""
    global _renderer
    import streamlit as st
    if _renderer is None:
        _renderer = st.components.v2.component("address_autocomplete", html=HTML, css=CSS, js=JS)
    return _renderer(key=key, data={"key": js_key, "placeholder": placeholder},
                     on_pick_change=lambda: None).pick


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_pick(raw):
    """None (nothing picked), {"label", "coords", "place_id"} or {"error": one of MESSAGES}."""
    if raw is None:
        return None
    unreadable = {"error": MESSAGES["unreadable"]}
    if not isinstance(raw, dict):
        return unreadable
    if "error_code" in raw:
        code = raw["error_code"]
        key = _CODES.get(code) if isinstance(code, int) and not isinstance(code, bool) else None
        return {"error": MESSAGES[key or "unreadable"]}
    lat, lng, label, pid = raw.get("lat"), raw.get("lng"), raw.get("label"), raw.get("place_id")
    if not (_number(lat) and _number(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return unreadable
    if not isinstance(label, str):
        return unreadable
    label = " ".join(_CONTROL.sub(" ", label).split())
    if not label or len(label) > MAX_LABEL:
        return unreadable
    if pid is not None and not (isinstance(pid, str) and _PLACE_ID.match(pid)):
        return unreadable
    return {"label": label, "coords": (round(lat, 6), round(lng, 6)), "place_id": pid}
