#!/usr/bin/env python3
"""Build the complete offline cache for the Québec City network.

After this runs, every static fact the router needs lives in `data/cache/`:

  * elevation.json               -- one elevation per station (open-meteo)
  * osrm_bike_distances.json     -- bicycle street distance for every ordered
                                    station pair (FOSSGIS OSRM, bike profile)
  * osrm_foot_distances.json     -- walking distance for every station pair
  * osrm_bike_geometry.json      -- the street polyline of every ride edge that
                                    fits the ride limit, for the map

Only live availability (`station_status`) still needs the network at runtime,
and even that is served stale if the feed is down. Re-run whenever the operator
adds stations; everything already cached is skipped.

    ./.venv/bin/python scripts/build_cache.py            # everything
    ./.venv/bin/python scripts/build_cache.py --no-geometry   # matrices only

The geometry pass is the slow part: one /route request per edge, a few in flight
at a time to stay polite to a shared public server. Progress is flushed to disk
every 25 routes, so the script can be interrupted and resumed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from avelo.config import get_settings
from avelo.data.elevation import ElevationClient
from avelo.data.gbfs import GBFSClient
from avelo.data.osrm import OSRMClient
from avelo.models import StationSnapshot, VehicleType
from avelo.routing.cost import CostModel
from avelo.routing.graph import StationGraph

log = logging.getLogger("build_cache")


async def main(geometry: bool, delay: float, limit_multiple: float, concurrency: int) -> int:
    settings = get_settings()
    t0 = time.perf_counter()

    async with GBFSClient(settings) as gbfs, ElevationClient(settings) as elev:
        stations = await elev.annotate(await gbfs.get_stations())
        status = await gbfs.get_status()
    known = sum(1 for s in stations.values() if s.elevation_m is not None)
    log.info("stations: %d, elevation known for %d", len(stations), known)
    coords = {sid: s.coord for sid, s in stations.items()}

    async with OSRMClient(settings, root=settings.osrm_root, profile="bike") as bike:
        bike_matrix = await bike.distance_matrix(coords)
        log.info(
            "bike matrix: %d / %d ordered pairs", len(bike_matrix), len(coords) * (len(coords) - 1)
        )

        if geometry:
            # Every edge the graph could contain for either vehicle type, using an
            # "everything available" snapshot so no station is filtered out by the
            # availability of the moment.
            snaps = {
                sid: StationSnapshot(
                    station=st,
                    status=status[sid].model_copy(
                        update={
                            "is_renting": True,
                            "is_returning": True,
                            "num_bikes_available": 99,
                            "num_docks_available": 99,
                            "bikes_by_type": {},
                        }
                    ),
                )
                for sid, st in stations.items()
                if sid in status
            }
            # ICONIC edges are a subset of EFIT edges (a mechanical bike is never
            # faster), so building for EFIT covers both vehicle types.
            geo_settings = settings.model_copy(update={"max_edge_limit_multiple": limit_multiple})
            g = StationGraph(snaps, CostModel(geo_settings), geo_settings, bike_matrix)
            g.build(VehicleType.EFIT)
            pairs = {(e.from_id, e.to_id) for es in g._adjacency.values() for e in es}
            todo = [(a, b) for a, b in sorted(pairs) if not bike.has_geometry(coords[a], coords[b])]
            log.info(
                "geometry: %d edges, %d already cached, %d to fetch",
                len(pairs),
                len(pairs) - len(todo),
                len(todo),
            )
            failures = 0
            done = 0
            gate = asyncio.Semaphore(concurrency)

            async def fetch(a: str, b: str) -> None:
                nonlocal failures, done
                async with gate:
                    line = await bike.route_geometry(coords[a], coords[b], persist=True)
                    await asyncio.sleep(delay)
                failures += line is None
                done += 1
                if done % 500 == 0 or done == len(todo):
                    log.info(
                        "  %d / %d  (%d failures, %.0f s)",
                        done,
                        len(todo),
                        failures,
                        time.perf_counter() - t0,
                    )

            await asyncio.gather(*(fetch(a, b) for a, b in todo))
            bike.flush()
            log.info("geometry cached: %d polylines", bike.cached_geometries)

    async with OSRMClient(settings, root=settings.osrm_walk_root, profile="foot") as foot:
        foot_matrix = await foot.distance_matrix(coords)
        log.info("foot matrix: %d ordered pairs", len(foot_matrix))

    log.info("done in %.0f s", time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--no-geometry", action="store_true", help="skip the per-edge polyline pass"
    )
    parser.add_argument(
        "--delay", type=float, default=0.1, help="pause after each geometry request"
    )
    parser.add_argument("--concurrency", type=int, default=3, help="geometry requests in flight")
    parser.add_argument(
        "--limit-multiple",
        type=float,
        default=1.0,
        help="cache polylines for edges up to this multiple of the ride limit "
        "(1.0 = every edge the constrained planner can return; over-limit edges are "
        "fetched lazily on first use)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(
        asyncio.run(main(not args.no_geometry, args.delay, args.limit_multiple, args.concurrency))
    )
