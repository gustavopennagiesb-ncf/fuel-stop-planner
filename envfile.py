"""Getting configuration into os.environ, so `config.py` stays a plain module that only reads
the environment.

Two sources: a local `.env` file (KEY=VALUE lines; unlike `export $(grep ... | xargs)` this
tolerates stray spaces and quotes) and Streamlit secrets, which is how keys arrive on Streamlit
Cloud. A variable already in the real environment always wins over both.
"""
import os
import pathlib

DEFAULT_PATH = pathlib.Path(__file__).resolve().parent / ".env"


def load_dotenv(path=DEFAULT_PATH):
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'").strip()
        if key and value and not os.environ.get(key):  # a real shell value wins; empty is unset
            os.environ[key] = value


def load_secrets(secrets=None):
    """Copy top-level string secrets into the environment (Streamlit Cloud sets keys there, not
    in the shell). `secrets` defaults to `st.secrets`. Nested sections and non-strings are
    skipped, and a missing secrets file is not an error: the app then runs on .env or fixtures."""
    if secrets is None:
        try:
            import streamlit as st
            secrets = st.secrets
        except Exception:
            return
    try:
        items = [(k, secrets[k]) for k in secrets.keys()]
    except Exception:  # no secrets.toml, or Streamlit not running
        return
    for key, value in items:
        if isinstance(value, str) and value.strip() and not os.environ.get(key):
            os.environ[key] = value.strip()
