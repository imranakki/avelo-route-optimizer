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
from avelo.data.osrm import OSRMClient
from avelo.models import Coord, Itinerary, LegMode, StationSnapshot, VehicleType
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
    distances: dict[PairKey, float] = field(default_factory=dict)
    graphs: dict[VehicleType, _CachedGraph] = field(default_factory=dict)
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


async def _current_graph(vehicle: VehicleType) -> StationGraph:
    """The routing graph for `vehicle`, rebuilt at most once per status TTL."""
    st = _state()
    ttl = st.settings.station_status_ttl_seconds
    hit = st.graphs.get(vehicle)
    if hit is not None and time.monotonic() - hit.built_at < ttl:
        return hit.graph
    async with st.lock:
        hit = st.graphs.get(vehicle)  # another request may have built it meanwhile
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
        graph = StationGraph(snaps, st.cost, st.settings, st.distances)
        graph.build(vehicle)
        log.info(
            "built %s graph: %d stations, %d edges, %.0f%% street-routed, %.0f ms",
            vehicle,
            len(snaps),
            graph.edge_count,
            graph.measured_edge_fraction * 100,
            (time.perf_counter() - t0) * 1000,
        )
        st.graphs[vehicle] = _CachedGraph(graph, time.monotonic())
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


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    stations_cached: int
    street_pairs_cached: int
    graphs_cached: list[str]
    ride_limit_minutes: float


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
    from_lat: float, from_lon: float, to_lat: float, to_lon: float, vehicle: VehicleType
) -> tuple[RoutePlanner, Coord, Coord, WalkContext, bool]:
    origin, destination = _coords(from_lat, from_lon, to_lat, to_lon)
    graph = await _current_graph(vehicle)
    if not graph.rentable_stations(vehicle):
        # A real state of the network, not a routing failure: àVélo's fleet is
        # currently all-electric, so ICONIC/FIT requests land here.
        raise HTTPException(
            status_code=404,
            detail=f"no {vehicle.value} bikes are available anywhere in the network right now",
        )
    walks, routed = await _walk_context(graph, origin, destination, vehicle)
    return RoutePlanner(graph, _state().settings), origin, destination, walks, routed


# Fresh Query objects per parameter: FastAPI binds the alias onto the instance, so a
# shared one would make every latitude parameter read the first one's value.
def _lat() -> float:
    return Query(..., ge=-90, le=90, description="WGS84 latitude")  # type: ignore[no-any-return]


def _lon() -> float:
    return Query(..., ge=-180, le=180, description="WGS84 longitude")  # type: ignore[no-any-return]


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
        graphs_cached=[v.value for v in st.graphs],
        ride_limit_minutes=st.settings.ride_limit_minutes,
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
    geometry: bool = _geom(),
) -> RouteResponse:
    """Fastest itinerary in which every ride leg respects the ride limit ($0 overage)."""
    planner, origin, destination, walks, routed = await _prepare(
        from_lat, from_lon, to_lat, to_lon, vehicle
    )
    t0 = time.perf_counter()
    try:
        it = planner.plan_hard_constraint(origin, destination, vehicle, walks)
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
    max_solutions: int = Query(5, ge=1, le=20),
    geometry: bool = _geom(),
) -> OptionsResponse:
    """The time/cost Pareto frontier: every option is non-dominated, fastest first."""
    planner, origin, destination, walks, routed = await _prepare(
        from_lat, from_lon, to_lat, to_lon, vehicle
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
    geometry: bool = _geom(),
) -> RouteResponse:
    """The naive control: nearest station to nearest station, one ride, limit ignored."""
    planner, origin, destination, walks, routed = await _prepare(
        from_lat, from_lon, to_lat, to_lon, vehicle
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
    max_solutions: int = Query(5, ge=1, le=20),
    geometry: bool = _geom(),
) -> CompareResponse:
    """All three strategies for one trip, side by side. 404 only if none can route."""
    planner, origin, destination, walks, routed = await _prepare(
        from_lat, from_lon, to_lat, to_lon, vehicle
    )
    t0 = time.perf_counter()
    naive: Itinerary | None = None
    hard: Itinerary | None = None
    options: list[Itinerary] = []
    with suppress(NoRouteFound):
        naive = planner.plan_naive_baseline(origin, destination, vehicle, walks)
    with suppress(NoRouteFound):
        hard = planner.plan_hard_constraint(origin, destination, vehicle, walks)
    with suppress(NoRouteFound):
        options = planner.plan_pareto(origin, destination, vehicle, max_solutions, walks)
    ms = (time.perf_counter() - t0) * 1000
    if naive is None and hard is None and not options:
        raise HTTPException(status_code=404, detail="no itinerary reaches the destination")
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


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.middleware("http")
async def _timing(request: Request, call_next):  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    return response


app.include_router(router)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")
