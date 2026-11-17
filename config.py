"""Runtime configuration: plain environment variables, never committed. `app.py` calls
`envfile.load_dotenv()` and `envfile.load_secrets()` first, so a local `.env` and Streamlit
Cloud secrets both end up here. PROVIDER selects the data path: google | fallback | fixture."""
import os

PROVIDER = os.environ.get("PROVIDER", "fixture").lower()
EIA_API_KEY = os.environ.get("EIA_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")          # Places + Routes (server side)
GOOGLE_MAPS_JS_KEY = os.environ.get("GOOGLE_MAPS_JS_KEY")  # separate, referrer-restricted key
MAX_PLACES_CALLS = int(os.environ.get("MAX_PLACES_CALLS", "6"))  # per search, protects quota
MAX_ROUTE_CALLS = int(os.environ.get("MAX_ROUTE_CALLS", "6"))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")        # free-tier LLM; used when set
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # fallback when Groq key absent
