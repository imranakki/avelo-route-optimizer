"""Tests for the GBFS client.

These tests never touch the network. `respx` intercepts httpx calls and returns
canned payloads, so the suite is fast, deterministic, and runs offline. A test that
hits a real API is not a unit test; it is a flaky dependency on someone else's uptime.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from avelo.config import Settings
from avelo.data.gbfs import GBFSClient, GBFSError

ROOT = "https://test.example/gbfs.json"
INFO = "https://test.example/station_information"
STATUS = "https://test.example/station_status"


@pytest.fixture
def cfg() -> Settings:
    return Settings(gbfs_root=ROOT, gbfs_language="en")


def _mock_feeds() -> None:
    respx.get(ROOT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "en": {
                        "feeds": [
                            {"name": "station_information", "url": INFO},
                            {"name": "station_status", "url": STATUS},
                        ]
                    }
                }
            },
        )
    )


@respx.mock
async def test_drops_stations_with_corrupt_coordinates(cfg: Settings) -> None:
    """The live àVélo feed really does ship a station at (0.0, 0.0). Prove we drop it."""
    _mock_feeds()
    respx.get(INFO).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "stations": [
                        {
                            "station_id": "1",
                            "name": "Good",
                            "lat": 46.81,
                            "lon": -71.22,
                            "capacity": 20,
                        },
                        {
                            "station_id": "2",
                            "name": "Corrupt",
                            "lat": 0.0,
                            "lon": 0.0,
                            "capacity": 20,
                        },
                        {
                            "station_id": "3",
                            "name": "Montreal",
                            "lat": 45.50,
                            "lon": -73.56,
                            "capacity": 20,
                        },
                    ]
                }
            },
        )
    )
    async with GBFSClient(settings=cfg) as client:
        stations = await client.get_stations()

    assert set(stations) == {"1"}, "only the in-region station should survive"


@respx.mock
async def test_serves_stale_cache_when_upstream_fails(cfg: Settings) -> None:
    """Resilience: a transient upstream failure must not break an already-warm client.

    This is the behaviour that separates a service from a script.
    """
    _mock_feeds()
    route = respx.get(INFO).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "stations": [
                        {
                            "station_id": "1",
                            "name": "Good",
                            "lat": 46.81,
                            "lon": -71.22,
                            "capacity": 20,
                        }
                    ]
                }
            },
        )
    )
    async with GBFSClient(settings=cfg) as client:
        first = await client.get_stations()
        assert len(first) == 1

        # Expire the cache, then make upstream fail.
        client._cache[INFO].expires_at = 0.0
        route.mock(side_effect=httpx.ConnectError("upstream down"))

        second = await client.get_stations()
        assert second == first, "should serve stale data rather than raise"


@respx.mock
async def test_raises_when_upstream_fails_with_cold_cache(cfg: Settings) -> None:
    """No cached data + upstream down = an explicit, typed error. Never a silent empty list:
    an empty station map would make the router report 'no route' for a network problem."""
    _mock_feeds()
    respx.get(INFO).mock(side_effect=httpx.ConnectError("upstream down"))
    async with GBFSClient(settings=cfg) as client:
        with pytest.raises(GBFSError):
            await client.get_stations()


@respx.mock
async def test_snapshots_inner_join_drops_half_present_stations(cfg: Settings) -> None:
    """Station 2 exists in the info feed but not the status feed. It must not appear."""
    _mock_feeds()
    respx.get(INFO).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "stations": [
                        {
                            "station_id": "1",
                            "name": "A",
                            "lat": 46.81,
                            "lon": -71.22,
                            "capacity": 20,
                        },
                        {
                            "station_id": "2",
                            "name": "B",
                            "lat": 46.82,
                            "lon": -71.23,
                            "capacity": 20,
                        },
                    ]
                }
            },
        )
    )
    respx.get(STATUS).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "stations": [
                        {
                            "station_id": "1",
                            "num_bikes_available": 4,
                            "num_docks_available": 6,
                            "is_renting": True,
                            "is_returning": True,
                            "last_reported": 1,
                            "vehicle_types_available": [{"vehicle_type_id": "EFIT", "count": 4}],
                        },
                    ]
                }
            },
        )
    )
    async with GBFSClient(settings=cfg) as client:
        snaps = await client.get_snapshots()

    assert set(snaps) == {"1"}
    assert snaps["1"].can_rent() is True
