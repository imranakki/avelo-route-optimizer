"""The route planner: two algorithms, one story.

The rider keeps ONE bike for the whole trip. A "transfer" is a clock reset: dock
at a station with a free dock, let the ride close, unlock the same bike again.
Every RIDE leg is therefore exactly one billing period, and `num_transfers` is
the number of resets.

--------------------------------------------------------------------------------
PLANNER 1 -- `plan_hard_constraint`
--------------------------------------------------------------------------------
Rule: no ride leg may exceed the limit, full stop. Minimise total door-to-door time.

Because the constraint applies independently to each edge, over-limit edges are
simply removed and ordinary Dijkstra runs on what remains. Nothing cleverer is
needed, and recognising that is the point: the constraint is *local*, so it never
interacts with the path structure.

Origin and destination are arbitrary coordinates, not stations, so the search runs
over a small virtual graph: ORIGIN --walk--> {nearest rentable stations} --ride-->
... --ride--> {nearest returnable stations} --walk--> DESTINATION, plus a direct
ORIGIN --walk--> DESTINATION arc when the two are within walking range. With K
walk candidates on each side (config `walk_candidates`) the search is exact for
any trip whose optimal first/last station is among the K nearest -- which, for
K = 5 and 800 m, is every trip we have looked at.

--------------------------------------------------------------------------------
PLANNER 2 -- `plan_pareto`
--------------------------------------------------------------------------------
Rule: you MAY exceed the limit -- it just costs $0.30/min. A 32-minute direct ride
costs $0.60 but saves a reset; a 3-hop route costs $0 but takes longer. Neither
is "the answer". Two objectives are minimised at once:

        minimise (total_time, total_money)

This is the bi-criteria shortest path problem. It does not reduce to Dijkstra
because there is no single "best" label per node: a slower-but-cheaper partial
path may still lead to the best complete one. The solution is MARTINS' LABEL-
SETTING ALGORITHM:

  * each node keeps a BAG of non-dominated labels (time, cost) instead of one
    distance;
  * labels are expanded from a priority queue in lexicographic (time, cost) order;
  * a new label is discarded if any label already in the target bag DOMINATES it
    (no worse in both objectives); labels it dominates are evicted.

Dominance pruning is the entire reason this is tractable. `last_stats` records
labels generated vs. surviving so the ratio can be reported.

A useful property of the lexicographic order: destination labels are popped in
increasing time, and each popped one is final (anything that could dominate it
would have been popped earlier). So the frontier arrives already sorted by time
with strictly decreasing cost, and the search can stop after `max_solutions` of
them without any post-processing.

The reset overhead is applied to every ride leg, including the last one, where
it overstates by the re-unlock time (~30 s). Costing every leg identically keeps the
numbers the search optimised equal to the numbers it reports -- adjusting the last
leg afterwards can silently create a dominated pair in the returned frontier.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field

from avelo.config import Settings, get_settings
from avelo.models import Coord, Itinerary, Leg, LegMode, RiskLevel, VehicleType
from avelo.routing.graph import Edge, StationGraph

ORIGIN = "__origin__"
DESTINATION = "__destination__"

_RISK_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.INFEASIBLE]


class NoRouteFound(RuntimeError):
    """No feasible itinerary exists between these points under these constraints.

    "No route" is a real, meaningful answer (the destination may simply be too far
    from any station), and the API should say so clearly instead of 500-ing.
    """


@dataclass(frozen=True)
class WalkContext:
    """Measured (street-routed) walking distances for one query, in metres.

    `origin_m[sid]` is origin -> station, `destination_m[sid]` is station ->
    destination, `direct_m` is origin -> destination. Any missing entry falls back
    to haversine * walk_detour_factor, so a partial or empty context is fine.
    """

    origin_m: Mapping[str, float] = field(default_factory=dict)
    destination_m: Mapping[str, float] = field(default_factory=dict)
    direct_m: float | None = None


@dataclass(frozen=True)
class SearchStats:
    """Instrumentation of the last search, for the README and the API."""

    labels_generated: int = 0
    labels_pruned: int = 0
    labels_settled: int = 0
    frontier_size: int = 0

    @property
    def pruned_fraction(self) -> float:
        return self.labels_pruned / self.labels_generated if self.labels_generated else 0.0


@dataclass(frozen=True)
class _Arc:
    """One traversable step in the virtual search graph."""

    to: str
    mode: LegMode
    seconds: float
    cost: float
    distance_m: float
    from_name: str
    to_name: str
    from_coord: Coord
    to_coord: Coord
    edge: Edge | None = None  # populated for RIDE arcs


@dataclass(frozen=True)
class Label:
    """One partial path in the multi-objective search.

    A back-pointer to the parent label, not the whole path: the frontier at scale
    holds thousands of labels sharing prefixes, and copying paths would make memory
    quadratic in path length. Reconstruction walks the chain once, at the end.
    """

    node: str
    seconds: float
    cost: float
    parent: Label | None
    arc: _Arc | None


def _dominates(a: Label, b: Label, eps: float = 1e-6) -> bool:
    """Weak dominance: a is no worse than b in both objectives.

    Weak (not strict) on purpose: a new label *equal* to a stored one adds nothing
    and should be pruned too.
    """
    return a.seconds <= b.seconds + eps and a.cost <= b.cost + eps


class RoutePlanner:
    def __init__(self, graph: StationGraph, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.graph = graph
        self.last_stats = SearchStats()

    # ------------------------------------------------------------- virtual graph

    def _walk_m(self, a: Coord, b: Coord, measured: float | None) -> float:
        if measured is not None and measured >= 0:
            return measured
        return self.graph.cost.walk_distance_m(a, b)

    def _walk_seconds(self, metres: float) -> float:
        return metres / (self.settings.walking_speed_kmh / 3.6)

    def _candidates(
        self, point: Coord, stations: Mapping[str, object], measured: Mapping[str, float]
    ) -> list[tuple[str, float]]:
        """Nearest `walk_candidates` stations within `max_walk_meters`, as (id, m)."""
        ranked = sorted(
            (
                (sid, self._walk_m(point, self.graph.snapshots[sid].coord, measured.get(sid)))
                for sid in stations
            ),
            key=lambda t: t[1],
        )
        near = [(sid, m) for sid, m in ranked if m <= self.settings.max_walk_meters]
        if near:
            return near[: self.settings.walk_candidates]
        # Nothing in comfortable range: offer the nearest station anyway, up to the
        # fallback radius, so an empty neighbourhood degrades to a longer walk
        # instead of "no route".
        return [(sid, m) for sid, m in ranked[:1] if m <= self.settings.max_walk_fallback_meters]

    def _build_arcs(
        self,
        origin: Coord,
        destination: Coord,
        vehicle: VehicleType,
        walks: WalkContext,
        within_limit_only: bool,
    ) -> dict[str, list[_Arc]]:
        arcs: dict[str, list[_Arc]] = {ORIGIN: [], DESTINATION: []}
        snaps = self.graph.snapshots

        for sid, metres in self._candidates(
            origin, self.graph.rentable_stations(vehicle), walks.origin_m
        ):
            arcs[ORIGIN].append(
                _Arc(
                    to=sid,
                    mode=LegMode.WALK,
                    seconds=self._walk_seconds(metres),
                    cost=0.0,
                    distance_m=metres,
                    from_name="Origin",
                    to_name=snaps[sid].station.name,
                    from_coord=origin,
                    to_coord=snaps[sid].coord,
                )
            )

        for sid, metres in self._candidates(
            destination, self.graph.returnable_stations(), walks.destination_m
        ):
            arcs.setdefault(sid, []).append(
                _Arc(
                    to=DESTINATION,
                    mode=LegMode.WALK,
                    seconds=self._walk_seconds(metres),
                    cost=0.0,
                    distance_m=metres,
                    from_name=snaps[sid].station.name,
                    to_name="Destination",
                    from_coord=snaps[sid].coord,
                    to_coord=destination,
                )
            )

        direct = self._walk_m(origin, destination, walks.direct_m)
        if direct <= self.settings.max_walk_meters:
            arcs[ORIGIN].append(
                _Arc(
                    to=DESTINATION,
                    mode=LegMode.WALK,
                    seconds=self._walk_seconds(direct),
                    cost=0.0,
                    distance_m=direct,
                    from_name="Origin",
                    to_name="Destination",
                    from_coord=origin,
                    to_coord=destination,
                )
            )

        for sid in snaps:
            for edge in self.graph.neighbours(sid):
                if within_limit_only and not edge.is_within_limit:
                    continue
                arcs.setdefault(sid, []).append(
                    _Arc(
                        to=edge.to_id,
                        mode=LegMode.RIDE,
                        seconds=edge.duration_seconds,
                        cost=edge.overage_cost,
                        distance_m=edge.distance_m,
                        from_name=snaps[sid].station.name,
                        to_name=snaps[edge.to_id].station.name,
                        from_coord=snaps[sid].coord,
                        to_coord=snaps[edge.to_id].coord,
                        edge=edge,
                    )
                )
        return arcs

    # ------------------------------------------------------------- itineraries

    @staticmethod
    def _leg(arc: _Arc) -> Leg:
        e = arc.edge
        return Leg(
            mode=arc.mode,
            from_name=arc.from_name,
            to_name=arc.to_name,
            from_coord=arc.from_coord,
            to_coord=arc.to_coord,
            distance_m=arc.distance_m,
            duration_seconds=arc.seconds,
            cycling_seconds=e.cycling_seconds if e else 0.0,
            traffic_light_seconds=e.traffic_light_seconds if e else 0.0,
            docking_seconds=e.docking_seconds if e else 0.0,
            elevation_gain_m=e.elevation_gain_m if e else 0.0,
            risk=e.risk if e else RiskLevel.LOW,
            overage_cost=arc.cost,
        )

    @classmethod
    def _itinerary(cls, legs: list[Leg]) -> Itinerary:
        rides = [leg for leg in legs if leg.mode is LegMode.RIDE]
        overall = max((leg.risk for leg in rides), key=_RISK_ORDER.index, default=RiskLevel.LOW)
        return Itinerary(
            legs=legs,
            total_seconds=sum(leg.duration_seconds for leg in legs),
            total_distance_m=sum(leg.distance_m for leg in legs),
            total_cost=round(sum(leg.overage_cost for leg in legs), 2),
            overall_risk=overall,
            num_transfers=max(0, len(rides) - 1),
        )

    @classmethod
    def _from_label(cls, label: Label) -> Itinerary:
        arcs: list[_Arc] = []
        cur: Label | None = label
        while cur is not None and cur.arc is not None:
            arcs.append(cur.arc)
            cur = cur.parent
        arcs.reverse()
        return cls._itinerary([cls._leg(a) for a in arcs])

    # ------------------------------------------------------------- planner 1

    def plan_hard_constraint(
        self,
        origin: Coord,
        destination: Coord,
        vehicle: VehicleType = VehicleType.EFIT,
        walks: WalkContext | None = None,
    ) -> Itinerary:
        """Fastest itinerary in which every ride leg respects the limit.

        Plain Dijkstra over the limit-filtered virtual graph. Raises NoRouteFound
        if the destination is unreachable.
        """
        arcs = self._build_arcs(origin, destination, vehicle, walks or WalkContext(), True)
        if not arcs[ORIGIN]:
            raise NoRouteFound(
                "no station with an available bike within 2 km of the origin right now"
            )

        best: dict[str, Label] = {}
        counter = itertools.count()
        start = Label(ORIGIN, 0.0, 0.0, None, None)
        heap: list[tuple[float, int, Label]] = [(0.0, next(counter), start)]
        settled = 0
        while heap:
            t, _, label = heapq.heappop(heap)
            if label.node in best:
                continue  # a shorter path to this node was settled already
            best[label.node] = label
            settled += 1
            if label.node == DESTINATION:
                self.last_stats = SearchStats(labels_settled=settled, frontier_size=1)
                return self._from_label(label)
            for arc in arcs.get(label.node, []):
                if arc.to not in best:
                    heapq.heappush(
                        heap,
                        (
                            t + arc.seconds,
                            next(counter),
                            Label(arc.to, t + arc.seconds, 0.0, label, arc),
                        ),
                    )
        self.last_stats = SearchStats(labels_settled=settled)
        if not any(arc.to == DESTINATION for arcs_ in arcs.values() for arc in arcs_):
            raise NoRouteFound(
                "no station with a free dock within 2 km of the destination right now"
            )
        raise NoRouteFound(
            "no itinerary reaches the destination with every ride leg under the limit"
        )

    # ------------------------------------------------------------- planner 2

    def plan_pareto(
        self,
        origin: Coord,
        destination: Coord,
        vehicle: VehicleType = VehicleType.EFIT,
        max_solutions: int = 5,
        walks: WalkContext | None = None,
    ) -> list[Itinerary]:
        """The non-dominated (time, cost) frontier, fastest first.

        Every returned itinerary is Pareto-optimal: no other returned itinerary is
        both faster and cheaper. See the module docstring for the algorithm.
        """
        arcs = self._build_arcs(origin, destination, vehicle, walks or WalkContext(), False)
        if not arcs[ORIGIN]:
            raise NoRouteFound(
                "no station with an available bike within 2 km of the origin right now"
            )

        bags: dict[str, list[Label]] = {}
        counter = itertools.count()
        start = Label(ORIGIN, 0.0, 0.0, None, None)
        bags[ORIGIN] = [start]
        heap: list[tuple[float, float, int, Label]] = [(0.0, 0.0, next(counter), start)]
        found: list[Label] = []
        generated = pruned = settled = 0

        while heap and len(found) < max_solutions:
            _, _, _, label = heapq.heappop(heap)
            # Evicted from its bag by a later, better label -> stale; skip.
            if not any(existing is label for existing in bags.get(label.node, ())):
                continue
            settled += 1
            if label.node == DESTINATION:
                found.append(label)
                continue
            dest_bag = bags.get(DESTINATION, [])
            for arc in arcs.get(label.node, []):
                new = Label(arc.to, label.seconds + arc.seconds, label.cost + arc.cost, label, arc)
                generated += 1
                # Any complete path we already have dominates this partial one -> it
                # can only get worse, so it will never reach the frontier.
                if any(_dominates(done, new) for done in dest_bag):
                    pruned += 1
                    continue
                bag = bags.setdefault(arc.to, [])
                if any(_dominates(existing, new) for existing in bag):
                    pruned += 1
                    continue
                bag[:] = [existing for existing in bag if not _dominates(new, existing)]
                bag.append(new)
                heapq.heappush(heap, (new.seconds, new.cost, next(counter), new))

        self.last_stats = SearchStats(
            labels_generated=generated,
            labels_pruned=pruned,
            labels_settled=settled,
            frontier_size=len(found),
        )
        if not found:
            raise NoRouteFound("no itinerary reaches the destination")
        return [self._from_label(label) for label in found]

    # ------------------------------------------------------------- baseline

    def plan_naive_baseline(
        self,
        origin: Coord,
        destination: Coord,
        vehicle: VehicleType = VehicleType.EFIT,
        walks: WalkContext | None = None,
    ) -> Itinerary:
        """What a normal navigation app tells you: nearest station to nearest station,
        one ride, no awareness of the limit.

        This is the CONTROL. Its station choice is naive; its *costing* uses the full
        model, because the evaluation compares what each strategy would actually
        cost a rider, and the naive app's own belief about the ride time is not what
        the operator bills. Deliberately not rigged: the baseline picks the closest
        stations, which is also what a sensible person would do.
        """
        w = walks or WalkContext()
        starts = self._candidates(origin, self.graph.rentable_stations(vehicle), w.origin_m)
        ends = self._candidates(destination, self.graph.returnable_stations(), w.destination_m)
        if not starts or not ends:
            raise NoRouteFound("no usable station within walking range of origin or destination")
        (s_id, s_m), (e_id, e_m) = starts[0], ends[0]
        if s_id == e_id:
            raise NoRouteFound("origin and destination share a nearest station; walk")
        s, e = self.graph.snapshots[s_id], self.graph.snapshots[e_id]
        est = self.graph.cost.estimate_ride(
            s.coord,
            e.coord,
            s.station.elevation_m,
            e.station.elevation_m,
            vehicle,
            include_docking=True,
            measured_m=self.graph.distances.get((s_id, e_id)),
        )
        ride = Leg(
            mode=LegMode.RIDE,
            from_name=s.station.name,
            to_name=e.station.name,
            from_coord=s.coord,
            to_coord=e.coord,
            distance_m=est.distance_m,
            duration_seconds=est.total_seconds,
            cycling_seconds=est.cycling_seconds,
            traffic_light_seconds=est.traffic_light_seconds,
            docking_seconds=est.docking_seconds,
            elevation_gain_m=est.elevation_gain_m,
            risk=self.graph.cost.risk_for(est.total_seconds),
            overage_cost=self.graph.cost.overage_cost(est.total_seconds),
        )
        walk_in = Leg(
            mode=LegMode.WALK,
            from_name="Origin",
            to_name=s.station.name,
            from_coord=origin,
            to_coord=s.coord,
            distance_m=s_m,
            duration_seconds=self._walk_seconds(s_m),
        )
        walk_out = Leg(
            mode=LegMode.WALK,
            from_name=e.station.name,
            to_name="Destination",
            from_coord=e.coord,
            to_coord=destination,
            distance_m=e_m,
            duration_seconds=self._walk_seconds(e_m),
        )
        return self._itinerary([walk_in, ride, walk_out])
