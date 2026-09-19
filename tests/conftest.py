"""Shared fixtures.

`conftest.py` is pytest magic: anything defined here is available to every test
in this directory and below, with no import. It is the right home for fixtures
that more than one test file needs.
"""

from __future__ import annotations

import pytest

from avelo.config import Settings
from avelo.models import Coord, Station, StationSnapshot, StationStatus, VehicleType


@pytest.fixture
def settings() -> Settings:
    """Explicit test settings.

    Note we do NOT use `get_settings()` here: that reads the environment and a .env
    file, so a test using it would pass or fail depending on the developer's machine.
    Tests must be hermetic. Pin every value the test depends on.
    """
    return Settings(
        ride_limit_minutes=30.0,
        overage_cost_per_minute=0.30,
        speed_electric_kmh=17.0,
        speed_mechanical_kmh=13.0,
        detour_factor=1.27,
        traffic_light_spacing_m=250.0,
        traffic_light_delay_seconds=12.0,
        docking_overhead_seconds=60.0,
        safety_buffer_seconds=90.0,
        walking_speed_kmh=4.8,
    )


def make_snapshot(
    sid: str,
    lat: float,
    lon: float,
    bikes: int = 5,
    docks: int = 5,
    elevation: float = 50.0,
    renting: bool = True,
    returning: bool = True,
) -> StationSnapshot:
    """Build a station snapshot for tests. Defaults are 'healthy station'."""
    return StationSnapshot(
        station=Station(
            station_id=sid,
            name=f"Station {sid}",
            coord=Coord(lat=lat, lon=lon),
            capacity=bikes + docks,
            elevation_m=elevation,
        ),
        status=StationStatus(
            station_id=sid,
            num_bikes_available=bikes,
            num_docks_available=docks,
            bikes_by_type={VehicleType.EFIT: bikes},
            is_renting=renting,
            is_returning=returning,
            last_reported=1788737562,
        ),
    )


@pytest.fixture
def line_network() -> dict[str, StationSnapshot]:
    """Five stations in a straight west-to-east line, ~2 km apart, all flat.

    A deliberately trivial topology: you can compute the right answer by hand,
    which is the whole point of a unit-test fixture. Save realistic data for
    integration tests.
    """
    return {
        str(i): make_snapshot(str(i), 46.8139, -71.28 + i * 0.026, elevation=50.0) for i in range(5)
    }


@pytest.fixture
def hill_network() -> dict[str, StationSnapshot]:
    """Two stations 1.4 km apart with a 90 m climb between them: the Basse-Ville /
    Haute-Ville escarpment in miniature. Used to prove the grade model does something."""
    return {
        "low": make_snapshot("low", 46.8139, -71.2080, elevation=10.0),
        "high": make_snapshot("high", 46.8087, -71.2245, elevation=100.0),
    }
