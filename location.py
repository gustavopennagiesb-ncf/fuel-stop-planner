"""Use the browser's location as the route origin.

The position is read in the browser (navigator.geolocation, after the user allows it) and sent
only to this app's own server; it is rounded to about 10 m, kept in the session, never written to
a file or a log, and the only place it goes is the Routes request made with the user's own key.
Nothing the browser sends is shown as text: errors arrive as numeric codes and map to the fixed
MESSAGES below.
"""
import math

MESSAGES = {
    "unsupported": "This browser cannot share a location. Type an address instead.",
    "denied": "Location permission was denied. Allow it in the browser's site settings, or type "
              "an address instead.",
    "unavailable": "Your position could not be determined right now. Type an address instead.",
    "timeout": "Finding your position took too long. Try again, or type an address instead.",
    "unreadable": "The location received could not be read. Type an address instead.",
}
_CODES = {0: "unsupported", 1: "denied", 2: "unavailable", 3: "timeout"}

HTML = '<button type="button" class="geo">Use my current location</button>'
CSS = """
.geo { width: 100%; padding: 0.45rem 0.75rem; border-radius: 0.5rem; cursor: pointer;
       border: 1px solid var(--st-primary-color); background: transparent;
       color: var(--st-primary-color); font: inherit; }
.geo:disabled { opacity: 0.6; cursor: progress; }
"""
# Trusted, static script: nothing user-provided is ever put into it.
JS = """
export default function(component) {
  const { setTriggerValue, parentElement } = component;
  const btn = parentElement.querySelector('button');
  const label = btn.textContent;
  const done = () => { btn.disabled = false; btn.textContent = label; };
  btn.onclick = () => {
    if (!navigator.geolocation) { setTriggerValue('fix', { error_code: 0 }); return; }
    btn.disabled = true; btn.textContent = 'Locating...';
    navigator.geolocation.getCurrentPosition(
      (p) => { done(); setTriggerValue('fix', { lat: p.coords.latitude, lng: p.coords.longitude,
                                                  accuracy: p.coords.accuracy }); },
      (e) => { done(); setTriggerValue('fix', { error_code: e.code }); },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 });
  };
}
"""

_renderer = None


def request_location(key):
    """Show the button. Returns what the browser sent on the run right after a click, else None."""
    global _renderer
    import streamlit as st
    if _renderer is None:
        _renderer = st.components.v2.component("my_location_button", html=HTML, css=CSS, js=JS)
    return _renderer(key=key, on_fix_change=lambda: None).fix


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_fix(raw):
    """None (nothing happened), {"coords": (lat, lng), "accuracy": metres or None} or
    {"error": one of MESSAGES}."""
    if raw is None:
        return None
    if isinstance(raw, dict) and "error_code" in raw:
        code = raw["error_code"]
        key = _CODES.get(code) if isinstance(code, int) and not isinstance(code, bool) else None
        return {"error": MESSAGES[key or "unreadable"]}
    if (isinstance(raw, dict) and _number(raw.get("lat")) and _number(raw.get("lng"))
            and -90 <= raw["lat"] <= 90 and -180 <= raw["lng"] <= 180):
        acc = raw.get("accuracy")
        return {"coords": (round(raw["lat"], 4), round(raw["lng"], 4)),
                "accuracy": round(acc) if _number(acc) and acc >= 0 else None}
    return {"error": MESSAGES["unreadable"]}


def resolve_origin(my_location, typed):
    """The location when one is set, else the typed address."""
    return my_location if my_location else typed
