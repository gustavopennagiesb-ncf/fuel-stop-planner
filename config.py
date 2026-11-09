"""Runtime configuration. Keys come from environment variables or Streamlit secrets and are
never committed. PROVIDER selects the data path: google | fallback | fixture."""
import os

PROVIDER = os.environ.get("PROVIDER", "fixture").lower()
EIA_API_KEY = os.environ.get("EIA_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")          # Places + Routes (server side)
GOOGLE_MAPS_JS_KEY = os.environ.get("GOOGLE_MAPS_JS_KEY")  # separate, referrer-restricted key
MAX_PLACES_CALLS = int(os.environ.get("MAX_PLACES_CALLS", "6"))  # per search, protects quota
MAX_ROUTE_CALLS = int(os.environ.get("MAX_ROUTE_CALLS", "6"))
