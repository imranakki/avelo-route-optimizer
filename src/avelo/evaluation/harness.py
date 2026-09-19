"""Evaluation harness: does the router beat the obvious alternative, and by how much?

Anyone can build a router. This module measures it. Three strategies are run over
the same sample of origin/destination pairs, on live station data and real street
distances, and compared on the numbers a rider cares about:

    | metric                        | naive | hard-constraint | pareto (fastest) |
    |-------------------------------|------:|----------------:|-----------------:|
    | trips exceeding the limit     |       |                 |                  |
    | mean overage charge per trip  |       |                 |                  |
    | median door-to-door time      |       |                 |                  |
    | ...                           |       |                 |                  |

An honest table shows the constrained router is SLOWER. That is the trade-off:
"I made trips N minutes longer and saved riders $X each" is the result.

Sampling: uniform random points in the bounding box are not representative --
real demand clusters around Université Laval, Vieux-Québec and Saint-Roch. Trips
are therefore sampled by picking two random stations (so density is respected)
and jittering each end by up to `jitter_m`, which turns "station to station" into
"address to address". A fixed seed makes every number reproducible.

`NoRouteFound` is a result, not an error: a trip the system cannot serve is
counted, not hidden.

Ablation: `evaluate(..., enable_elevation_model=False)` re-runs everything with a
flat earth so the contribution of the hill model can be reported -- including
when that contribution turns out to be small.
"""

from __future__ import annotations

import math
import random
import statistics
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from avelo.config import Settings, get_settings
from avelo.models import Coord, Itinerary, LegMode, StationSnapshot, VehicleType
from avelo.routing.cost import CostModel
from avelo.routing.graph import PairKey, StationGraph
from avelo.routing.planner import NoRouteFound, RoutePlanner

STRATEGIES = ("naive", "hard_constraint", "pareto_fastest", "pareto_cheapest")

# Québec City service-area bounding box, used only when no stations are supplied.
_BBOX = ((46.74, 46.87), (-71.36, -71.17))

_METRES_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class TripResult:
    """One evaluated origin/destination pair under every strategy."""

    origin: Coord
    destination: Coord
    straight_line_m: float
    # strategy -> itinerary (None when NoRouteFound)
    itineraries: Mapping[str, Itinerary | None]
    # strategy -> wall-clock planning latency in milliseconds
    latency_ms: Mapping[str, float]
    frontier_size: int = 0
    labels_generated: int = 0
    labels_pruned: int = 0


@dataclass(frozen=True)
class StrategyMetrics:
    served: int
    total: int
    exceeded_limit_pct: float
    mean_overage: float
    mean_minutes: float
    median_minutes: float
    p95_minutes: float
    median_transfers: float
    p95_latency_ms: float


@dataclass(frozen=True)
class EvaluationReport:
    """Aggregated metrics, printable as a markdown table for the README."""

    n: int
    seed: int
    ride_limit_minutes: float
    elevation_model: bool
    measured_edge_fraction: float
    edges_total: int
    edges_within_limit: int
    metrics: Mapping[str, StrategyMetrics]
    mean_frontier_size: float
    pruned_fraction: float
    trips: Sequence[TripResult] = field(default_factory=list, repr=False)

    def to_markdown(self) -> str:
        cols = [s for s in STRATEGIES if s in self.metrics]
        rows: list[tuple[str, list[str]]] = [
            ("trips served", [f"{self.metrics[c].served}/{self.metrics[c].total}" for c in cols]),
            (
                "trips exceeding the limit",
                [f"{self.metrics[c].exceeded_limit_pct:.1f}%" for c in cols],
            ),
            (
                "mean overage charge per trip",
                [f"${self.metrics[c].mean_overage:.2f}" for c in cols],
            ),
            ("mean door-to-door time", [f"{self.metrics[c].mean_minutes:.1f} min" for c in cols]),
            (
                "median door-to-door time",
                [f"{self.metrics[c].median_minutes:.1f} min" for c in cols],
            ),
            ("p95 door-to-door time", [f"{self.metrics[c].p95_minutes:.1f} min" for c in cols]),
            ("median transfers", [f"{self.metrics[c].median_transfers:.0f}" for c in cols]),
            ("p95 planning latency", [f"{self.metrics[c].p95_latency_ms:.1f} ms" for c in cols]),
        ]
        header = "| metric | " + " | ".join(c.replace("_", " ") for c in cols) + " |"
        sep = "|---|" + "|".join("---:" for _ in cols) + "|"
        body = "\n".join(f"| {name} | " + " | ".join(vals) + " |" for name, vals in rows)
        meta = (
            f"\n\n_n = {self.n} trips, seed = {self.seed}, "
            f"limit = {self.ride_limit_minutes:.0f} min, "
            f"elevation model = {'on' if self.elevation_model else 'off'}, "
            f"{self.measured_edge_fraction * 100:.0f}% of edges street-routed. "
            f"Pareto search: mean frontier size {self.mean_frontier_size:.2f}, "
            f"{self.pruned_fraction * 100:.1f}% of generated labels pruned by dominance._"
        )
        return header + "\n" + sep + "\n" + body + meta


# --------------------------------------------------------------------------- sampling


def _jitter(rng: random.Random, c: Coord, radius_m: float) -> Coord:
    """A uniformly random point within `radius_m` of `c` (flat-earth locally)."""
    r = radius_m * math.sqrt(rng.random())
    theta = rng.random() * 2 * math.pi
    dlat = r * math.cos(theta) / _METRES_PER_DEG_LAT
    dlon = r * math.sin(theta) / (_METRES_PER_DEG_LAT * math.cos(math.radians(c.lat)))
    return Coord(lat=c.lat + dlat, lon=c.lon + dlon)


def sample_trips(
    n: int,
    seed: int = 42,
    anchors: Sequence[Coord] | None = None,
    jitter_m: float = 400.0,
    min_separation_m: float = 1500.0,
) -> list[tuple[Coord, Coord]]:
    """N origin/destination pairs, reproducible for a given seed.

    With `anchors` (station coordinates) the sample follows station density; without
    them it is uniform over the service-area bounding box. Pairs closer than
    `min_separation_m` are rejected: those are walks, not bike trips.
    """
    rng = random.Random(seed)
    out: list[tuple[Coord, Coord]] = []
    attempts = 0
    while len(out) < n and attempts < n * 50:
        attempts += 1
        if anchors:
            a = _jitter(rng, rng.choice(anchors), jitter_m)
            b = _jitter(rng, rng.choice(anchors), jitter_m)
        else:
            (lat0, lat1), (lon0, lon1) = _BBOX
            a = Coord(lat=rng.uniform(lat0, lat1), lon=rng.uniform(lon0, lon1))
            b = Coord(lat=rng.uniform(lat0, lat1), lon=rng.uniform(lon0, lon1))
        if a.haversine_m(b) >= min_separation_m:
            out.append((a, b))
    return out


# --------------------------------------------------------------------------- running


def _percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _timed(fn: object, *args: object) -> tuple[Itinerary | None, float]:
    t0 = time.perf_counter()
    try:
        result = fn(*args)  # type: ignore[operator]
    except NoRouteFound:
        result = None
    return result, (time.perf_counter() - t0) * 1000.0


def evaluate_trips(
    trips: Sequence[tuple[Coord, Coord]],
    snapshots: dict[str, StationSnapshot],
    settings: Settings,
    distances: Mapping[PairKey, float] | None = None,
    vehicle: VehicleType = VehicleType.EFIT,
    seed: int = 42,
) -> EvaluationReport:
    """Run every strategy over `trips` on the given network and aggregate."""
    graph = StationGraph(snapshots, CostModel(settings), settings, distances)
    graph.build(vehicle)
    planner = RoutePlanner(graph, settings)
    limit_s = settings.ride_limit_minutes * 60.0

    results: list[TripResult] = []
    for origin, destination in trips:
        its: dict[str, Itinerary | None] = {}
        lat: dict[str, float] = {}
        its["naive"], lat["naive"] = _timed(
            planner.plan_naive_baseline, origin, destination, vehicle
        )
        its["hard_constraint"], lat["hard_constraint"] = _timed(
            planner.plan_hard_constraint, origin, destination, vehicle
        )
        frontier, ms = _timed(
            lambda o, d, v: planner.plan_pareto(o, d, v, 20), origin, destination, vehicle
        )
        stats = planner.last_stats
        options: list[Itinerary] = frontier or []  # type: ignore[assignment]
        its["pareto_fastest"] = options[0] if options else None
        its["pareto_cheapest"] = (
            min(options, key=lambda i: (i.total_cost, i.total_seconds)) if options else None
        )
        lat["pareto_fastest"] = lat["pareto_cheapest"] = ms
        results.append(
            TripResult(
                origin=origin,
                destination=destination,
                straight_line_m=origin.haversine_m(destination),
                itineraries=its,
                latency_ms=lat,
                frontier_size=len(options),
                labels_generated=stats.labels_generated,
                labels_pruned=stats.labels_pruned,
            )
        )

    metrics: dict[str, StrategyMetrics] = {}
    for strategy in STRATEGIES:
        served = [r.itineraries[strategy] for r in results if r.itineraries[strategy] is not None]
        minutes = [i.total_minutes for i in served if i is not None]
        over = [
            any(leg.mode is LegMode.RIDE and leg.duration_seconds > limit_s for leg in i.legs)
            for i in served
            if i is not None
        ]
        metrics[strategy] = StrategyMetrics(
            served=len(served),
            total=len(results),
            exceeded_limit_pct=100.0 * sum(over) / len(served) if served else math.nan,
            mean_overage=statistics.fmean([i.total_cost for i in served if i])
            if served
            else math.nan,
            mean_minutes=statistics.fmean(minutes) if minutes else math.nan,
            median_minutes=statistics.median(minutes) if minutes else math.nan,
            p95_minutes=_percentile(minutes, 0.95),
            median_transfers=statistics.median([i.num_transfers for i in served if i])
            if served
            else math.nan,
            p95_latency_ms=_percentile([r.latency_ms[strategy] for r in results], 0.95),
        )

    generated = sum(r.labels_generated for r in results)
    pruned = sum(r.labels_pruned for r in results)
    return EvaluationReport(
        n=len(results),
        seed=seed,
        ride_limit_minutes=settings.ride_limit_minutes,
        elevation_model=settings.enable_elevation_model,
        measured_edge_fraction=graph.measured_edge_fraction,
        edges_total=graph.edge_count,
        edges_within_limit=sum(
            1 for sid in snapshots for e in graph.neighbours(sid) if e.is_within_limit
        ),
        metrics=metrics,
        mean_frontier_size=statistics.fmean([r.frontier_size for r in results]) if results else 0.0,
        pruned_fraction=pruned / generated if generated else 0.0,
        trips=results,
    )


async def evaluate(
    n: int = 1000,
    seed: int = 42,
    settings: Settings | None = None,
    vehicle: VehicleType = VehicleType.EFIT,
) -> EvaluationReport:
    """Fetch live data (stations, status, elevation, street matrix) and evaluate.

    Pass a `Settings` with `enable_elevation_model=False` for the ablation.
    """
    from avelo.data.elevation import ElevationClient
    from avelo.data.gbfs import GBFSClient
    from avelo.data.osrm import OSRMClient

    settings = settings or get_settings()
    async with GBFSClient(settings) as gbfs, ElevationClient(settings) as elev:
        stations = await elev.annotate(await gbfs.get_stations())
        status = await gbfs.get_status()
    snapshots = {
        sid: StationSnapshot(station=st, status=status[sid])
        for sid, st in stations.items()
        if sid in status
    }
    distances: dict[PairKey, float] = {}
    if settings.use_osrm:
        async with OSRMClient(settings) as osrm:
            distances = await osrm.distance_matrix({sid: s.coord for sid, s in snapshots.items()})

    trips = sample_trips(n, seed, anchors=[s.coord for s in snapshots.values()])
    return evaluate_trips(trips, snapshots, settings, distances, vehicle, seed)
