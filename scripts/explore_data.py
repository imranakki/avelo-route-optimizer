#!/usr/bin/env python3
"""Print a profile of the live àVélo network.

The numbers it prints (distance percentiles, vertical relief, one-hop feasibility)
are the ones the README cites to justify multi-hop routing.

    ./.venv/bin/python scripts/explore_data.py
"""

from __future__ import annotations

import asyncio
import itertools
import statistics

from avelo.config import get_settings
from avelo.data.elevation import ElevationClient
from avelo.data.gbfs import GBFSClient


async def main() -> None:
    settings = get_settings()
    async with GBFSClient() as gbfs, ElevationClient() as elev:
        stations = await elev.annotate(await gbfs.get_stations())
        status = await gbfs.get_status()

    print(f"\n{'=' * 68}\n  àVélo network profile\n{'=' * 68}")
    print(f"  stations (valid coords)   {len(stations)}")

    live = [status[s] for s in stations if s in status]
    print(f"  currently renting         {sum(1 for s in live if s.is_renting)}")
    print(f"  with zero bikes           {sum(1 for s in live if s.num_bikes_available == 0)}")
    print(f"  with zero docks           {sum(1 for s in live if s.num_docks_available == 0)}")

    elevs = [s.elevation_m for s in stations.values() if s.elevation_m is not None]
    if elevs:
        print(f"\n  elevation range           {min(elevs):.0f} m -- {max(elevs):.0f} m")
        print(f"  vertical relief           {max(elevs) - min(elevs):.0f} m")
        print(f"  median elevation          {statistics.median(elevs):.0f} m")

    coords = [s.coord for s in stations.values()]
    dists = sorted(a.haversine_m(b) / 1000 for a, b in itertools.combinations(coords, 2))
    print(f"\n  station pairs             {len(dists):,}")
    for q in (50, 75, 90, 99):
        print(f"  p{q:<2} pair distance         {dists[int(len(dists) * q / 100)]:.2f} km")
    print(f"  max pair distance         {dists[-1]:.2f} km")

    print(f"\n  one-hop feasibility at a {settings.ride_limit_minutes:.0f}-minute limit:")
    for speed in (13.0, 17.0):
        over = sum(
            1
            for d in dists
            if (d * settings.detour_factor) / speed * 60 > settings.ride_limit_minutes
        )
        label = "mechanical" if speed == 13.0 else "electric  "
        pct = over / len(dists) * 100
        print(f"    {label} ({speed:.0f} km/h)   {pct:5.1f}% of pairs need a reset")

    print(f"{'=' * 68}\n")
    print("  ^ That last block is the justification for multi-hop routing.\n")


if __name__ == "__main__":
    asyncio.run(main())
