"""FastAPI application.

Design notes:

1. **Lifespan-managed shared state.** HTTP clients, the elevation cache and the
   street-distance matrix are created ONCE at startup and reused. Creating an
   httpx client per request discards the connection pool every time.

2. **A cached graph, not a per-request build.** Station availability changes every
   ~30 s, so the routing graph is rebuilt at most once per status TTL per vehicle
   type, behind a lock so concurrent requests do not stampede the build.

3. **A thin API layer.** Handlers fetch data, call the planner, serialise. No
   routing logic lives here, so all of it is testable without HTTP.

4. **Decoration never fails a request.** Street-routed walk legs and map polylines
   come from a public OSRM; if it is slow or down the response falls back to the
   estimate and says so, rather than 500-ing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from avelo import __version__
from avelo.config import Settings, get_settings
from avelo.data.elevation import ElevationClient
from avelo.data.gbfs import GBFSClient, GBFSError
from avelo.data.geocode import GeocodeError, Geocoder, Place
from avelo.data.osrm import OSRMClient
from avelo.models import Coord, Itinerary, LegMode, RiskLevel, StationSnapshot, VehicleType
from avelo.routing.cost import CostModel
from avelo.routing.graph import PairKey, StationGraph
from avelo.routing.planner import NoRouteFound, RoutePlanner, SearchStats, WalkContext

log = logging.getLogger(__name__)

_STATIC = Path(__file__).parent / "static"


# --------------------------------------------------------------------------- state


@dataclass
class _CachedGraph:
    graph: StationGraph
    built_at: float


@dataclass
class AppState:
    settings: Settings
    gbfs: GBFSClient
    elevation: ElevationClient
    osrm_bike: OSRMClient | None
    osrm_foot: OSRMClient | None
    cost: CostModel
    geocoder: Geocoder
    distances: dict[PairKey, float] = field(default_factory=dict)
    # Keyed by (vehicle, ride limit in minutes): the limit changes edge costs.
    graphs: dict[tuple[VehicleType, float], _CachedGraph] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started_at: float = field(default_factory=time.time)


state: AppState | None = None


def _state() -> AppState:
    assert state is not None, "application not started"
    return state


async def _snapshots(st: AppState) -> dict[str, StationSnapshot]:
    stations = await st.elevation.annotate(await st.gbfs.get_stations())
    status = await st.gbfs.get_status()
    return {
        sid: StationSnapshot(station=s, status=status[sid])
        for sid, s in stations.items()
        if sid in status
    }


def _settings_for(limit: float) -> Settings:
    base = _state().settings
    if limit == base.ride_limit_minutes:
        return base
    return base.model_copy(update={"ride_limit_minutes": limit})


async def _current_graph(vehicle: VehicleType, limit: float) -> StationGraph:
    """The routing graph for `vehicle` under a ride limit, rebuilt at most once per
    status TTL. The limit is per request because riders hold 30- or 45-minute plans."""
    st = _state()
    ttl = st.settings.station_status_ttl_seconds
    key = (vehicle, limit)
    hit = st.graphs.get(key)
    if hit is not None and time.monotonic() - hit.built_at < ttl:
        return hit.graph
    async with st.lock:
        hit = st.graphs.get(key)  # another request may have built it meanwhile
        if hit is not None and time.monotonic() - hit.built_at < ttl:
            return hit.graph
        try:
            snaps = await _snapshots(st)
        except GBFSError as exc:
            if hit is not None:
                log.warning("feed unavailable (%s); serving stale graph", exc)
                return hit.graph
            raise HTTPException(
                status_code=503, detail=f"upstream feed unavailable: {exc}"
            ) from exc
        t0 = time.perf_counter()
        settings = _settings_for(limit)
        graph = StationGraph(snaps, CostModel(settings), settings, st.distances)
        graph.build(vehicle)
        log.info(
            "built %s/%.0f-min graph: %d stations, %d edges, %.0f%% street-routed, %.0f ms",
            vehicle,
            limit,
            len(snaps),
            graph.edge_count,
            graph.measured_edge_fraction * 100,
            (time.perf_counter() - t0) * 1000,
        )
        st.graphs[key] = _CachedGraph(graph, time.monotonic())
        return graph


async def _walk_context(
    graph: StationGraph, origin: Coord, destination: Coord, vehicle: VehicleType
) -> tuple[WalkContext, bool]:
    """Street-routed walking distances for the nearest candidate stations.

    Two small /table calls, run concurrently. Returns (context, routed) where
    `routed` is False if the walking router could not be reached.
    """
    st = _state()
    if st.osrm_foot is None or not st.settings.osrm_walk_legs:
        return WalkContext(), False
    # Pre-select by straight line, generously: routed distance only ever grows.
    reach = st.settings.max_walk_meters * 1.5
    k = st.settings.walk_candidates * 2

    def nearest(point: Coord, pool: dict[str, StationSnapshot]) -> dict[str, Coord]:
        ranked = sorted(pool.items(), key=lambda kv: point.haversine_m(kv[1].coord))
        return {sid: s.coord for sid, s in ranked[:k] if point.haversine_m(s.coord) <= reach}

    starts = nearest(origin, graph.rentable_stations(vehicle))
    ends = nearest(destination, graph.returnable_stations())
    direct_needed = origin.haversine_m(destination) <= reach
    try:
        o_task = st.osrm_foot.one_to_many(origin, starts)
        d_task = st.osrm_foot.many_to_one(ends, destination)
        direct_task = (
            st.osrm_foot.one_to_many(origin, {"_": destination})
            if direct_needed
            else asyncio.sleep(0, result={})
        )
        o_m, d_m, direct = await asyncio.gather(o_task, d_task, direct_task)
    except Exception as exc:
        log.warning("walk routing failed (%s); using estimate", exc)
        return WalkContext(), False
    routed = bool(o_m) or bool(d_m) or not (starts or ends)
    return WalkContext(origin_m=o_m, destination_m=d_m, direct_m=direct.get("_")), routed


async def _with_geometry(itinerary: Itinerary) -> Itinerary:
    """Attach street polylines to every leg. Best-effort; legs stay None on failure."""
    st = _state()
    if st.osrm_bike is None:
        return itinerary
    foot = st.osrm_foot or st.osrm_bike
    tasks = [
        (st.osrm_bike if leg.mode is LegMode.RIDE else foot).route_geometry(
            leg.from_coord, leg.to_coord, persist=leg.mode is LegMode.RIDE
        )
        for leg in itinerary.legs
    ]
    lines = await asyncio.gather(*tasks)
    legs = [
        leg.model_copy(update={"geometry": line})
        for leg, line in zip(itinerary.legs, lines, strict=True)
    ]
    return itinerary.model_copy(update={"legs": legs})


# --------------------------------------------------------------------------- lifespan


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global state
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO line is the full URL
    gbfs = GBFSClient(settings)
    elev = ElevationClient(settings)
    bike = (
        OSRMClient(settings, root=settings.osrm_root, profile="bike") if settings.use_osrm else None
    )
    foot = (
        OSRMClient(settings, root=settings.osrm_walk_root, profile="foot")
        if settings.use_osrm
        else None
    )
    state = AppState(
        settings=settings,
        gbfs=gbfs,
        elevation=elev,
        osrm_bike=bike,
        osrm_foot=foot,
        cost=CostModel(settings),
        geocoder=Geocoder(settings),
    )
    # Warm every cache at boot so the first user request is not the slow one. The
    # street matrix is ~25 requests cold and zero requests warm (disk cache).
    try:
        stations = await elev.annotate(await gbfs.get_stations())
        if bike is not None:
            state.distances = await bike.distance_matrix(
                {sid: s.coord for sid, s in stations.items()}
            )
        log.info("warm: %d stations, %d street-routed pairs", len(stations), len(state.distances))
    except GBFSError as exc:
        log.warning("startup warm-up failed (%s); will retry per request", exc)
    yield
    await gbfs.aclose()
    await elev.__aexit__()
    await state.geocoder.aclose()
    for c in (bike, foot):
        if c is not None:
            await c.aclose()


app = FastAPI(
    title="àVélo Route Optimizer",
    description=(
        "Constraint-aware multi-hop routing for the àVélo bike-share network "
        "(Québec City). Plans trips whose every leg respects the ride-duration "
        "limit, and surfaces the time/cost trade-off when exceeding it is worth the "
        "money. Live station data (GBFS), real street distances (OSRM bicycle and "
        "foot profiles) and a grade-aware time model."
    ),
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["GET"],
    allow_headers=["*"],
)
router = APIRouter()


# --------------------------------------------------------------------------- schemas


class StationOut(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    elevation_m: float | None
    bikes: int
    docks: int
    bikes_by_type: dict[str, int]
    renting: bool
    returning: bool


class StationsResponse(BaseModel):
    count: int
    stations: list[StationOut]


class StatsOut(BaseModel):
    labels_generated: int
    labels_pruned: int
    labels_settled: int
    frontier_size: int
    pruned_fraction: float
    planning_ms: float
    walk_legs_street_routed: bool = Field(
        description="False when the walking router was unavailable and walk legs are estimated."
    )


class RouteResponse(BaseModel):
    itinerary: Itinerary
    stats: StatsOut


class OptionsResponse(BaseModel):
    count: int
    options: list[Itinerary]
    stats: StatsOut


class CompareResponse(BaseModel):
    naive: Itinerary | None
    hard_constraint: Itinerary | None
    options: list[Itinerary]
    stats: StatsOut


class PlaceOut(BaseModel):
    name: str
    label: str
    lat: float | None = Field(
        description="Known immediately for OSM results; resolve Google ones via /geocode/place."
    )
    lon: float | None = None
    kind: str
    place_id: str | None = None
    provider: str


class GeocodeResponse(BaseModel):
    query: str
    provider: str
    results: list[PlaceOut]


class StopOut(BaseModel):
    lat: float
    lon: float
    name: str


class TripSegmentOut(BaseModel):
    """One door-to-door leg of a multi-stop trip, with its own options."""

    from_stop: int
    to_stop: int
    naive: Itinerary | None
    hard_constraint: Itinerary | None
    options: list[Itinerary]


class TripOptionOut(BaseModel):
    """A whole-trip itinerary assembled from one option per segment."""

    itinerary: Itinerary
    # Index into each segment's `options` (or -1 for its hard-constraint route).
    choices: list[int]


class TripResponse(BaseModel):
    stops: list[StopOut]
    round_trip: bool
    segments: list[TripSegmentOut]
    naive: Itinerary | None
    hard_constraint: Itinerary | None
    options: list[TripOptionOut]
    stats: StatsOut


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    stations_cached: int
    street_pairs_cached: int
    graphs_cached: list[str]
    ride_limit_minutes: float
    search_provider: str
    google_calls_today: int


# --------------------------------------------------------------------------- helpers


def _stats(s: SearchStats, planning_ms: float, routed: bool) -> StatsOut:
    return StatsOut(
        labels_generated=s.labels_generated,
        labels_pruned=s.labels_pruned,
        labels_settled=s.labels_settled,
        frontier_size=s.frontier_size,
        pruned_fraction=round(s.pruned_fraction, 4),
        planning_ms=round(planning_ms, 2),
        walk_legs_street_routed=routed,
    )


def _coords(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> tuple[Coord, Coord]:
    return Coord(lat=from_lat, lon=from_lon), Coord(lat=to_lat, lon=to_lon)


async def _prepare(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    vehicle: VehicleType,
    limit: float,
) -> tuple[RoutePlanner, Coord, Coord, WalkContext, bool]:
    origin, destination = _coords(from_lat, from_lon, to_lat, to_lon)
    graph = await _current_graph(vehicle, limit)
    if not graph.rentable_stations(vehicle):
        # A real state of the network, not a routing failure: àVélo's fleet is
        # currently all-electric, so ICONIC/FIT requests land here.
        raise HTTPException(
            status_code=404,
            detail=f"no {vehicle.value} bikes are available anywhere in the network right now",
        )
    walks, routed = await _walk_context(graph, origin, destination, vehicle)
    return RoutePlanner(graph, _settings_for(limit)), origin, destination, walks, routed


# Fresh Query objects per parameter: FastAPI binds the alias onto the instance, so a
# shared one would make every latitude parameter read the first one's value.
def _lat() -> float:
    return Query(..., ge=-90, le=90, description="WGS84 latitude")  # type: ignore[no-any-return]


def _lon() -> float:
    return Query(..., ge=-180, le=180, description="WGS84 longitude")  # type: ignore[no-any-return]


def _limit() -> float:
    return Query(  # type: ignore[no-any-return]
        30.0,
        ge=15,
        le=90,
        description="Ride limit of the rider's plan in minutes (àVélo: 30 or 45).",
    )


def _max_risk() -> RiskLevel:
    return Query(  # type: ignore[no-any-return]
        RiskLevel.MEDIUM,
        description="Worst per-leg risk the constrained route accepts. MEDIUM (default) "
        "splits any leg estimated above 95% of the limit at a nearby station; HIGH "
        "accepts anything that fits.",
    )


def _geom() -> bool:
    return Query(False, description="Attach street polylines to every leg (for maps).")  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- routes


@router.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    st = _state()
    return HealthResponse(
        status="ok",
        version=__version__,
        uptime_seconds=round(time.time() - st.started_at, 1),
        stations_cached=len(st.elevation._cache),
        street_pairs_cached=len(st.distances),
        graphs_cached=[f"{v.value}/{lim:.0f}" for v, lim in st.graphs],
        ride_limit_minutes=st.settings.ride_limit_minutes,
        search_provider=st.geocoder.provider,
        google_calls_today=st.geocoder.google_calls_today,
    )


@router.get("/stations", response_model=StationsResponse, tags=["data"])
async def stations() -> StationsResponse:
    """Live station list with availability and elevation."""
    try:
        snaps = await _snapshots(_state())
    except GBFSError as exc:
        raise HTTPException(status_code=503, detail=f"upstream feed unavailable: {exc}") from exc
    return StationsResponse(
        count=len(snaps),
        stations=[
            StationOut(
                id=s.station_id,
                name=s.station.name,
                lat=s.coord.lat,
                lon=s.coord.lon,
                elevation_m=s.station.elevation_m,
                bikes=s.status.num_bikes_available,
                docks=s.status.num_docks_available,
                bikes_by_type={k.value: v for k, v in s.status.bikes_by_type.items()},
                renting=s.status.is_renting,
                returning=s.status.is_returning,
            )
            for s in snaps.values()
        ],
    )


@router.get("/route", response_model=RouteResponse, tags=["routing"])
async def route(
    from_lat: float = _lat(),
    from_lon: float = _lon(),
    to_lat: float = _lat(),
    to_lon: float = _lon(),
    vehicle: VehicleType = VehicleType.EFIT,
    limit: float = _limit(),
    max_risk: RiskLevel = _max_risk(),  # noqa: B008 -- FastAPI Query default
    geometry: bool = _geom(),
) -> RouteResponse:
    """Fastest itinerary in which every ride leg respects the ride limit ($0 overage)."""
    planner, origin, destination, walks, routed = await _prepare(
        from_lat, from_lon, to_lat, to_lon, vehicle, limit
    )
    t0 = time.perf_counter()
    try:
        it = planner.plan_hard_constraint(origin, destination, vehicle, walks, max_risk)
    except NoRouteFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ms = (time.perf_counter() - t0) * 1000
    if geometry:
        it = await _with_geometry(it)
    return RouteResponse(itinerary=it, stats=_stats(planner.last_stats, ms, routed))


@router.get("/route/options", response_model=OptionsResponse, tags=["routing"])
async def route_options(
    from_lat: float = _lat(),
    from_lon: float = _lon(),
    to_lat: float = _lat(),
    to_lon: float = _lon(),
    vehicle: VehicleType = VehicleType.EFIT,
    limit: float = _limit(),
    max_solutions: int = Query(5, ge=1, le=20),
    geometry: bool = _geom(),
) -> OptionsResponse:
    """The time/cost Pareto frontier: every option is non-dominated, fastest first."""
    planner, origin, destination, walks, routed = await _prepare(
        from_lat, from_lon, to_lat, to_lon, vehicle, limit
    )
    t0 = time.perf_counter()
    try:
        options = planner.plan_pareto(origin, destination, vehicle, max_solutions, walks)
    except NoRouteFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ms = (time.perf_counter() - t0) * 1000
    if geometry:
        options = list(await asyncio.gather(*(_with_geometry(o) for o in options)))
    return OptionsResponse(
        count=len(options), options=options, stats=_stats(planner.last_stats, ms, routed)
    )


@router.get("/route/baseline", response_model=RouteResponse, tags=["routing"])
async def route_baseline(
    from_lat: float = _lat(),
    from_lon: float = _lon(),
    to_lat: float = _lat(),
    to_lon: float = _lon(),
    vehicle: VehicleType = VehicleType.EFIT,
    limit: float = _limit(),
    geometry: bool = _geom(),
) -> RouteResponse:
    """The naive control: nearest station to nearest station, one ride, limit ignored."""
    planner, origin, destination, walks, routed = await _prepare(
        from_lat, from_lon, to_lat, to_lon, vehicle, limit
    )
    t0 = time.perf_counter()
    try:
        it = planner.plan_naive_baseline(origin, destination, vehicle, walks)
    except NoRouteFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ms = (time.perf_counter() - t0) * 1000
    if geometry:
        it = await _with_geometry(it)
    return RouteResponse(itinerary=it, stats=_stats(SearchStats(), ms, routed))


@router.get("/route/compare", response_model=CompareResponse, tags=["routing"])
async def route_compare(
    from_lat: float = _lat(),
    from_lon: float = _lon(),
    to_lat: float = _lat(),
    to_lon: float = _lon(),
    vehicle: VehicleType = VehicleType.EFIT,
    limit: float = _limit(),
    max_risk: RiskLevel = _max_risk(),  # noqa: B008 -- FastAPI Query default
    max_solutions: int = Query(5, ge=1, le=20),
    geometry: bool = _geom(),
) -> CompareResponse:
    """All three strategies for one trip, side by side. 404 only if none can route."""
    planner, origin, destination, walks, routed = await _prepare(
        from_lat, from_lon, to_lat, to_lon, vehicle, limit
    )
    t0 = time.perf_counter()
    naive: Itinerary | None = None
    hard: Itinerary | None = None
    options: list[Itinerary] = []
    reason = "no itinerary reaches the destination"
    with suppress(NoRouteFound):
        naive = planner.plan_naive_baseline(origin, destination, vehicle, walks)
    try:
        hard = planner.plan_hard_constraint(origin, destination, vehicle, walks, max_risk)
    except NoRouteFound as exc:
        reason = str(exc)  # the most specific explanation we have
    with suppress(NoRouteFound):
        options = planner.plan_pareto(origin, destination, vehicle, max_solutions, walks)
    ms = (time.perf_counter() - t0) * 1000
    if naive is None and hard is None and not options:
        raise HTTPException(status_code=404, detail=reason)
    if geometry:
        naive = await _with_geometry(naive) if naive else None
        hard = await _with_geometry(hard) if hard else None
        options = list(await asyncio.gather(*(_with_geometry(o) for o in options)))
    return CompareResponse(
        naive=naive,
        hard_constraint=hard,
        options=options,
        stats=_stats(planner.last_stats, ms, routed),
    )


def _place_out(p: Place) -> PlaceOut:
    return PlaceOut(
        name=p.name,
        label=p.label,
        lat=p.lat,
        lon=p.lon,
        kind=p.kind,
        place_id=p.place_id,
        provider=p.provider,
    )


@router.get("/geocode", response_model=GeocodeResponse, tags=["data"])
async def geocode(
    q: str = Query(..., min_length=1, max_length=200, description="Free-text place query"),
    limit: int = Query(6, ge=1, le=10),
    lang: str = Query("fr", pattern="^(fr|en)$"),
    session: str | None = Query(None, max_length=64, description="Autocomplete session id"),
) -> GeocodeResponse:
    """Place search within Québec City, for picking an origin or destination."""
    geocoder = _state().geocoder
    try:
        places = await geocoder.search(q, limit, lang, session)
    except GeocodeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return GeocodeResponse(
        query=q, provider=geocoder.last_provider, results=[_place_out(p) for p in places]
    )


@router.get("/geocode/place", response_model=PlaceOut, tags=["data"])
async def geocode_place(
    id: str = Query(..., min_length=1, max_length=300, description="Google place id"),
    session: str | None = Query(None, max_length=64),
) -> PlaceOut:
    """Location of a place returned by /geocode without coordinates."""
    try:
        p = await _state().geocoder.resolve(id, session)
    except GeocodeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if p is None:
        raise HTTPException(status_code=404, detail="unknown place")
    return _place_out(p)


@router.get("/geocode/reverse", response_model=PlaceOut | None, tags=["data"])
async def reverse_geocode(
    lat: float = _lat(), lon: float = _lon(), lang: str = Query("fr", pattern="^(fr|en)$")
) -> PlaceOut | None:
    """Nearest named place to a point (labels a pin dropped on the map)."""
    try:
        p = await _state().geocoder.reverse(lat, lon, lang)
    except GeocodeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _place_out(p) if p else None


def _relabel(it: Itinerary, from_name: str, to_name: str) -> Itinerary:
    """Name the walk legs after the actual stops instead of Origin/Destination."""
    legs = []
    for leg in it.legs:
        upd: dict[str, str] = {}
        if leg.from_name == "Origin":
            upd["from_name"] = from_name
        if leg.to_name == "Destination":
            upd["to_name"] = to_name
        legs.append(leg.model_copy(update=upd) if upd else leg)
    return it.model_copy(update={"legs": legs})


def _concat(parts: list[Itinerary]) -> Itinerary:
    legs = [leg for it in parts for leg in it.legs]
    rides = [leg for leg in legs if leg.mode is LegMode.RIDE]
    order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.INFEASIBLE]
    return Itinerary(
        legs=legs,
        total_seconds=sum(it.total_seconds for it in parts),
        total_distance_m=sum(it.total_distance_m for it in parts),
        total_cost=round(sum(it.total_cost for it in parts), 2),
        overall_risk=max((leg.risk for leg in rides), key=order.index, default=RiskLevel.LOW),
        # Bikes are docked at every visit; resets are the ones inside each segment.
        num_transfers=sum(it.num_transfers for it in parts),
    )


def _trip_frontier(segments: list[TripSegmentOut], max_solutions: int) -> list[TripOptionOut]:
    """Pareto-optimal whole-trip combinations of per-segment options.

    Every segment's candidate set is its frontier plus its hard-constraint route
    (index -1). Combinations are enumerated segment by segment and pruned by
    dominance after each step, so the work stays proportional to the frontier
    size rather than to the full cartesian product.
    """
    partial: list[tuple[float, float, list[int], list[Itinerary]]] = [(0.0, 0.0, [], [])]
    for seg in segments:
        candidates: list[tuple[int, Itinerary]] = list(enumerate(seg.options))
        if seg.hard_constraint is not None:
            candidates.append((-1, seg.hard_constraint))
        if not candidates:
            return []
        grown = [
            (t + c.total_seconds, m + c.total_cost, [*idx, i], [*its, c])
            for t, m, idx, its in partial
            for i, c in candidates
        ]
        grown.sort(key=lambda g: (g[0], g[1]))
        partial = []
        best_cost = float("inf")
        for g in grown:  # sorted by time: keep only strictly cheaper survivors
            if g[1] < best_cost - 1e-9:
                partial.append(g)
                best_cost = g[1]
    return [
        TripOptionOut(itinerary=_concat(its), choices=idx)
        for _, _, idx, its in partial[:max_solutions]
    ]


def _parse_stops(raw: str, names: str | None) -> list[StopOut]:
    labels = [n.strip() for n in names.split("|")] if names else []
    stops: list[StopOut] = []
    for i, part in enumerate(raw.split(";")):
        try:
            lat_s, lon_s = part.split(",")
            lat, lon = float(lat_s), float(lon_s)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"bad stop #{i + 1}: {part!r}") from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(status_code=422, detail=f"stop #{i + 1} out of range")
        name = labels[i] if i < len(labels) and labels[i] else f"Stop {i + 1}"
        stops.append(StopOut(lat=lat, lon=lon, name=name))
    if not 2 <= len(stops) <= 8:
        raise HTTPException(status_code=422, detail="a trip needs between 2 and 8 stops")
    return stops


@router.get("/trip", response_model=TripResponse, tags=["routing"])
async def trip(
    stops: str = Query(
        ..., description="Semicolon-separated lat,lon pairs, 2 to 8 stops in order."
    ),
    names: str | None = Query(None, description="Pipe-separated stop names, same order."),
    round_trip: bool = Query(False, description="Return to the first stop at the end."),
    vehicle: VehicleType = VehicleType.EFIT,
    limit: float = _limit(),
    max_risk: RiskLevel = _max_risk(),  # noqa: B008 -- FastAPI Query default
    max_solutions: int = Query(6, ge=1, le=20),
    geometry: bool = _geom(),
) -> TripResponse:
    """A trip through several places, optionally back to the start.

    Each visit means docking near the place, so the trip is a chain of door-to-door
    segments planned independently; the whole-trip options are the Pareto-optimal
    combinations of the segments' options.
    """
    stop_list = _parse_stops(stops, names)
    if round_trip:
        first = stop_list[0]
        stop_list.append(StopOut(lat=first.lat, lon=first.lon, name=first.name))
    t0 = time.perf_counter()
    segments: list[TripSegmentOut] = []
    generated = pruned = settled = 0
    routed_all = True
    for i in range(len(stop_list) - 1):
        a, b = stop_list[i], stop_list[i + 1]
        planner, origin, destination, walks, routed = await _prepare(
            a.lat, a.lon, b.lat, b.lon, vehicle, limit
        )
        routed_all = routed_all and routed
        naive = hard = None
        options: list[Itinerary] = []
        with suppress(NoRouteFound):
            naive = _relabel(
                planner.plan_naive_baseline(origin, destination, vehicle, walks), a.name, b.name
            )
        with suppress(NoRouteFound):
            hard = _relabel(
                planner.plan_hard_constraint(origin, destination, vehicle, walks, max_risk),
                a.name,
                b.name,
            )
        with suppress(NoRouteFound):
            options = [
                _relabel(o, a.name, b.name)
                for o in planner.plan_pareto(origin, destination, vehicle, max_solutions, walks)
            ]
        st = planner.last_stats
        generated += st.labels_generated
        pruned += st.labels_pruned
        settled += st.labels_settled
        if naive is None and hard is None and not options:
            raise HTTPException(status_code=404, detail=f"no itinerary from {a.name} to {b.name}")
        segments.append(
            TripSegmentOut(
                from_stop=i, to_stop=i + 1, naive=naive, hard_constraint=hard, options=options
            )
        )
    ms = (time.perf_counter() - t0) * 1000

    naive_all = (
        _concat([s.naive for s in segments if s.naive is not None])
        if all(s.naive is not None for s in segments)
        else None
    )
    hard_all = (
        _concat([s.hard_constraint for s in segments if s.hard_constraint is not None])
        if all(s.hard_constraint is not None for s in segments)
        else None
    )
    options_all = _trip_frontier(segments, max_solutions)

    if geometry:
        for seg in segments:
            seg.options = list(await asyncio.gather(*(_with_geometry(o) for o in seg.options)))
            seg.naive = await _with_geometry(seg.naive) if seg.naive else None
            seg.hard_constraint = (
                await _with_geometry(seg.hard_constraint) if seg.hard_constraint else None
            )
        # Re-assemble from the decorated segments so the polylines come along.
        naive_all = (
            _concat([s.naive for s in segments if s.naive is not None]) if naive_all else None
        )
        hard_all = (
            _concat([s.hard_constraint for s in segments if s.hard_constraint is not None])
            if hard_all
            else None
        )
        options_all = [
            TripOptionOut(
                itinerary=_concat(
                    [
                        seg.hard_constraint if i == -1 else seg.options[i]  # type: ignore[misc]
                        for seg, i in zip(segments, o.choices, strict=True)
                    ]
                ),
                choices=o.choices,
            )
            for o in options_all
        ]

    stats = SearchStats(
        labels_generated=generated,
        labels_pruned=pruned,
        labels_settled=settled,
        frontier_size=len(options_all),
    )
    return TripResponse(
        stops=stop_list,
        round_trip=round_trip,
        segments=segments,
        naive=naive_all,
        hard_constraint=hard_all,
        options=options_all,
        stats=_stats(stats, ms, routed_all),
    )


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


class _RateLimiter:
    """Sliding-minute per-client limiter. In-memory: right for one instance, which
    is what this deploys as; a shared store is the upgrade if that ever changes."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = {}

    def allow(self, client: str, bucket: str, per_minute: int) -> bool:
        now = time.monotonic()
        key = (client, bucket)
        window = [t for t in self._hits.get(key, []) if now - t < 60.0]
        if len(window) >= per_minute:
            self._hits[key] = window
            return False
        window.append(now)
        self._hits[key] = window
        if len(self._hits) > 10_000:  # forget idle clients rather than grow forever
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 60.0}
        return True


_limiter = _RateLimiter()


@app.middleware("http")
async def _rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
    path = request.url.path
    bucket = (
        "search"
        if path.startswith("/geocode")
        else "routing"
        if path.startswith(("/route", "/trip"))
        else None
    )
    if bucket is not None:
        settings = get_settings()
        limit = (
            settings.rate_limit_search_per_minute
            if bucket == "search"
            else settings.rate_limit_routing_per_minute
        )
        # Behind a reverse proxy the real client is the first X-Forwarded-For entry.
        forwarded = request.headers.get("x-forwarded-for", "")
        client = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
        if not _limiter.allow(client, bucket, limit):
            return JSONResponse(
                status_code=429,
                content={"detail": "too many requests; slow down"},
                headers={"Retry-After": "30"},
            )
    return await call_next(request)


@app.middleware("http")
async def _timing(request: Request, call_next):  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    return response


app.include_router(router)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")
