"""Specification for `RoutePlanner`. Defines 'done' for planner.py.

The Pareto tests near the bottom are the ones that matter. If you can make
`test_returned_solutions_are_mutually_non_dominated` pass and explain why it is the
correct definition of optimality here, you can defend this project to anyone.
"""

from __future__ import annotations

import pytest

from avelo.config import Settings
from avelo.models import Coord, Itinerary, LegMode, StationSnapshot, VehicleType
from avelo.routing.cost import CostModel
from avelo.routing.graph import StationGraph
from avelo.routing.planner import NoRouteFound, RoutePlanner


@pytest.fixture
def planner(line_network: dict[str, StationSnapshot], settings: Settings) -> RoutePlanner:
    g = StationGraph(line_network, CostModel(settings), settings)
    g.build(VehicleType.EFIT)
    return RoutePlanner(g, settings)


WEST = Coord(lat=46.8139, lon=-71.2805)  # next to station 0
EAST = Coord(lat=46.8139, lon=-71.1755)  # next to station 4, ~8 km away


# --- hard-constraint planner -------------------------------------------------


def test_returns_an_itinerary(planner: RoutePlanner) -> None:
    it = planner.plan_hard_constraint(WEST, EAST)
    assert isinstance(it, Itinerary)
    assert len(it.legs) >= 1


def test_itinerary_starts_and_ends_with_a_walk(planner: RoutePlanner) -> None:
    """Origin and destination are addresses, not stations. Forgetting the walk legs is
    the most common bug here -- it makes routes look faster than they are."""
    it = planner.plan_hard_constraint(WEST, EAST)
    assert it.legs[0].mode is LegMode.WALK
    assert it.legs[-1].mode is LegMode.WALK


def test_itinerary_totals_match_its_legs(planner: RoutePlanner) -> None:
    it = planner.plan_hard_constraint(WEST, EAST)
    assert it.total_seconds == pytest.approx(sum(leg.duration_seconds for leg in it.legs))
    assert it.total_distance_m == pytest.approx(sum(leg.distance_m for leg in it.legs))


def test_every_ride_leg_respects_the_limit(planner: RoutePlanner, settings: Settings) -> None:
    """THE core guarantee of this planner. If this fails, the project does not work."""
    it = planner.plan_hard_constraint(WEST, EAST)
    limit = settings.ride_limit_minutes * 60
    for leg in it.legs:
        if leg.mode is LegMode.RIDE:
            assert leg.duration_seconds <= limit


def test_transfer_count_matches_ride_legs(planner: RoutePlanner) -> None:
    it = planner.plan_hard_constraint(WEST, EAST)
    rides = sum(1 for leg in it.legs if leg.mode is LegMode.RIDE)
    assert it.num_transfers == max(0, rides - 1)


def test_tight_limit_forces_more_transfers(
    line_network: dict[str, StationSnapshot],
) -> None:
    """Shrink the limit and the planner must chop the trip into more legs. This proves
    the constraint is actually driving the search, not decorating it."""

    def build(limit: float) -> Itinerary:
        cfg = Settings(
            ride_limit_minutes=limit, max_edge_distance_km=20.0, max_neighbours_per_station=99
        )
        g = StationGraph(line_network, CostModel(cfg), cfg)
        g.build(VehicleType.EFIT)
        return RoutePlanner(g, cfg).plan_hard_constraint(WEST, EAST)

    # Stations are ~2 km apart. One hop is ~14 min once detour, lights, docking and
    # the safety buffer are included, so 15 min admits single hops but not doubles.
    # (8 min would admit nothing: the network becomes unroutable, which is correct.)
    assert build(15.0).num_transfers > build(60.0).num_transfers


def test_unreachable_destination_raises(planner: RoutePlanner) -> None:
    """Îles-de-la-Madeleine. No stations, no route -- and a clear error, not a crash."""
    with pytest.raises(NoRouteFound):
        planner.plan_hard_constraint(WEST, Coord(lat=47.38, lon=-61.86))


def test_empty_network_raises(settings: Settings) -> None:
    g = StationGraph({}, CostModel(settings), settings)
    g.build()
    with pytest.raises(NoRouteFound):
        RoutePlanner(g, settings).plan_hard_constraint(WEST, EAST)


# --- Pareto planner: THE CENTREPIECE -----------------------------------------


def test_pareto_returns_at_least_one_solution(planner: RoutePlanner) -> None:
    assert len(planner.plan_pareto(WEST, EAST)) >= 1


def test_returned_solutions_are_mutually_non_dominated(planner: RoutePlanner) -> None:
    """THE defining property of a Pareto frontier.

    Solution X dominates Y if X is no worse in EVERY objective and strictly better in
    at least one. A correct frontier contains no dominated member -- if one itinerary
    is both faster AND cheaper than another, the second should never have been returned.

    """
    solutions = planner.plan_pareto(WEST, EAST, max_solutions=10)
    for i, a in enumerate(solutions):
        for j, b in enumerate(solutions):
            if i == j:
                continue
            dominates = (
                a.total_seconds <= b.total_seconds
                and a.total_cost <= b.total_cost
                and (a.total_seconds < b.total_seconds or a.total_cost < b.total_cost)
            )
            assert not dominates, (
                f"solution {i} ({a.total_minutes:.1f} min / ${a.total_cost:.2f}) dominates "
                f"solution {j} ({b.total_minutes:.1f} min / ${b.total_cost:.2f})"
            )


def test_pareto_frontier_is_sorted_by_time(planner: RoutePlanner) -> None:
    times = [s.total_seconds for s in planner.plan_pareto(WEST, EAST, max_solutions=10)]
    assert times == sorted(times)


def test_faster_solutions_cost_more(planner: RoutePlanner) -> None:
    """A real trade-off curve. If time and money both improve together, you have not
    found a frontier -- you have found one answer listed several times."""
    sols = planner.plan_pareto(WEST, EAST, max_solutions=10)
    if len(sols) < 2:
        pytest.skip("network too simple to expose a trade-off")
    costs = [s.total_cost for s in sols]
    assert costs != sorted(costs), "cost should DECREASE as time increases"


def test_pareto_respects_max_solutions(planner: RoutePlanner) -> None:
    assert len(planner.plan_pareto(WEST, EAST, max_solutions=2)) <= 2


def test_pareto_includes_a_zero_cost_option_when_one_exists(
    planner: RoutePlanner,
) -> None:
    """If a fully-compliant route exists, the frontier must offer it -- that is the
    cheapest possible option, so it is non-dominated on the money axis by definition."""
    sols = planner.plan_pareto(WEST, EAST, max_solutions=10)
    assert any(s.total_cost == pytest.approx(0.0) for s in sols)


# --- baseline ----------------------------------------------------------------


def test_baseline_is_a_single_hop(planner: RoutePlanner) -> None:
    """The control: what a normal nav app does. Exactly one ride leg, limit ignored."""
    it = planner.plan_naive_baseline(WEST, EAST)
    assert sum(1 for leg in it.legs if leg.mode is LegMode.RIDE) == 1
    assert it.num_transfers == 0


def test_baseline_can_violate_the_limit(
    line_network: dict[str, StationSnapshot],
) -> None:
    """The thesis of the project, expressed as a test: the naive
    approach produces a route that will cost the rider money. Your evaluation chapter
    is just this test, measured over thousands of real origin/destination pairs."""
    cfg = Settings(ride_limit_minutes=8.0, max_edge_distance_km=20.0, max_neighbours_per_station=99)
    g = StationGraph(line_network, CostModel(cfg), cfg)
    g.build(VehicleType.EFIT)
    it = RoutePlanner(g, cfg).plan_naive_baseline(WEST, EAST)
    ride = next(leg for leg in it.legs if leg.mode is LegMode.RIDE)
    assert ride.duration_seconds > cfg.ride_limit_minutes * 60
