"""Specification for `StationGraph`. Defines 'done' for graph.py."""

from __future__ import annotations

import pytest

from avelo.config import Settings
from avelo.models import StationSnapshot, VehicleType
from avelo.routing.cost import CostModel
from avelo.routing.graph import StationGraph
from tests.conftest import make_snapshot


@pytest.fixture
def graph(line_network: dict[str, StationSnapshot], settings: Settings) -> StationGraph:
    return StationGraph(line_network, CostModel(settings), settings)


def test_station_with_no_bikes_cannot_start_a_trip_but_can_reset_one(settings: Settings) -> None:
    """The rider keeps the same bike, so an empty station is a fine place to dock and
    re-unlock -- it just cannot be where the trip starts."""
    snaps = {
        "empty": make_snapshot("empty", 46.81, -71.22, bikes=0, docks=10),
        "ok": make_snapshot("ok", 46.82, -71.23, bikes=5, docks=5),
    }
    g = StationGraph(snaps, CostModel(settings), settings)
    assert "empty" not in g.rentable_stations()
    assert "empty" in g.usable_stations()


def test_excludes_stations_with_no_docks(settings: Settings) -> None:
    """A full station cannot receive the bike, so the clock cannot be reset there."""
    snaps = {
        "full": make_snapshot("full", 46.81, -71.22, bikes=10, docks=0),
        "ok": make_snapshot("ok", 46.82, -71.23, bikes=5, docks=5),
    }
    g = StationGraph(snaps, CostModel(settings), settings)
    assert "full" not in g.usable_stations()


def test_excludes_stations_out_of_service(settings: Settings) -> None:
    snaps = {
        "down": make_snapshot("down", 46.81, -71.22, renting=False, returning=False),
        "ok": make_snapshot("ok", 46.82, -71.23),
    }
    g = StationGraph(snaps, CostModel(settings), settings)
    assert "down" not in g.usable_stations()


def test_station_not_renting_can_end_a_trip_but_not_reset_one(settings: Settings) -> None:
    """Docking is allowed but re-unlocking is not: a valid destination, not a transfer."""
    snaps = {
        "dock_only": make_snapshot("dock_only", 46.81, -71.22, renting=False, returning=True),
        "ok": make_snapshot("ok", 46.82, -71.23),
    }
    g = StationGraph(snaps, CostModel(settings), settings)
    assert "dock_only" in g.returnable_stations()
    assert "dock_only" not in g.usable_stations()
    g.build()
    assert "dock_only" in {e.to_id for e in g.neighbours("ok")}
    assert g.neighbours("dock_only") == []


def test_build_creates_edges(graph: StationGraph) -> None:
    graph.build(VehicleType.EFIT)
    assert graph.edge_count > 0
    assert len(graph.neighbours("0")) > 0


def test_respects_max_neighbours_cap(line_network: dict[str, StationSnapshot]) -> None:
    cfg = Settings(max_neighbours_per_station=2, max_edge_distance_km=50.0)
    g = StationGraph(line_network, CostModel(cfg), cfg)
    g.build()
    for sid in line_network:
        assert len(g.neighbours(sid)) <= 2


def test_respects_max_edge_distance(line_network: dict[str, StationSnapshot]) -> None:
    """Stations are ~2 km apart in a line, so a 3 km cap must exclude the far ends."""
    cfg = Settings(max_edge_distance_km=3.0, max_neighbours_per_station=99)
    g = StationGraph(line_network, CostModel(cfg), cfg)
    g.build()
    assert all(e.distance_m <= 3000 * cfg.detour_factor for e in g.neighbours("0"))
    assert "4" not in {e.to_id for e in g.neighbours("0")}, "8 km away, must be pruned"


def test_no_self_loops(graph: StationGraph) -> None:
    """Riding from a station to itself is meaningless and will confuse your search."""
    graph.build()
    for sid in graph.snapshots:
        assert all(e.to_id != sid for e in graph.neighbours(sid))


def test_edge_knows_whether_it_fits_the_limit(graph: StationGraph) -> None:
    graph.build()
    for edge in graph.neighbours("0"):
        expected = edge.duration_seconds <= graph.settings.ride_limit_minutes * 60
        assert edge.is_within_limit is expected


def test_hill_edge_is_slower_uphill_than_downhill(
    hill_network: dict[str, StationSnapshot], settings: Settings
) -> None:
    """The payoff test for the whole elevation idea: the SAME pair of stations must
    produce different durations in each direction. If this passes, your router will
    correctly send riders the long way round rather than up the escarpment."""
    g = StationGraph(hill_network, CostModel(settings), settings)
    g.build(VehicleType.ICONIC)
    up = next(e for e in g.neighbours("low") if e.to_id == "high")
    down = next(e for e in g.neighbours("high") if e.to_id == "low")
    assert up.duration_seconds > down.duration_seconds
