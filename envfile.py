"""Minimal .env reader (KEY=VALUE lines). Unlike `export $(grep ... | xargs)`, it tolerates
stray spaces around values and quotes. Variables already in the environment always win."""
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
        if key and value:                      # empty values never mask a real variable
            os.environ.setdefault(key, value)
