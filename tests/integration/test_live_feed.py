"""Integration tests against the REAL àVélo feed.

Excluded from the default run (see `addopts` in pyproject.toml). Run explicitly::

    make live      # or: pytest -m integration

Keep these few and shallow. Their job is to catch "the operator changed their feed
format", not to test your logic -- that is what the offline unit tests are for.
"""

from __future__ import annotations

import pytest

from avelo.data.elevation import ElevationClient
from avelo.data.gbfs import GBFSClient

pytestmark = pytest.mark.integration


async def test_live_feed_returns_plausible_station_count() -> None:
    async with GBFSClient() as client:
        stations = await client.get_stations()
    # A range, not an exact number: the operator adds and removes stations
    # seasonally. A test asserting == 225 would start failing for no good reason.
    assert 150 <= len(stations) <= 400


async def test_live_snapshots_have_availability_data() -> None:
    async with GBFSClient() as client:
        snaps = await client.get_snapshots()
    assert any(s.can_rent() for s in snaps.values())
    assert any(s.can_return() for s in snaps.values())


async def test_network_has_significant_vertical_relief() -> None:
    """The premise of the elevation model, asserted against reality.

    Measured 2026-09-07: 3 m (Pont Dorchester) to 144 m (Hopital Chauveau) = 141 m.
    If this ever fails, the hill-modelling work is no longer justified -- exactly the
    kind of load-bearing assumption worth pinning down in a test.
    """
    async with GBFSClient() as g, ElevationClient() as e:
        stations = await e.annotate(await g.get_stations())
    known = [s.elevation_m for s in stations.values() if s.elevation_m is not None]
    assert len(known) > 100
    assert max(known) - min(known) > 100
