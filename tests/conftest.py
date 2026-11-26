"""Most app tests check what the app does with a trip and a car. The app now opens empty, so
they start from the demo trip (Sarasota → Orlando, a 27.2 mpg car) through config.DEMO_TRIP;
tests/test_empty_start.py turns it off to check the empty start itself."""
import pytest

import config


@pytest.fixture(autouse=True)
def demo_trip(monkeypatch):
    monkeypatch.setattr(config, "DEMO_TRIP", True)
