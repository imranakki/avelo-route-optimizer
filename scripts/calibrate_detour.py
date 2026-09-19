#!/usr/bin/env python3
"""Fit the cycling detour factor against the cached OSRM bicycle matrix.

The detour factor is only a fallback (pairs missing from the matrix), but its
value should still be measured rather than guessed. Prints the ratio of street to
straight-line distance over every cached station pair, with percentiles.

    ./.venv/bin/python scripts/calibrate_detour.py
"""

from __future__ import annotations

import asyncio
import statistics

from avelo.config import get_settings
from avelo.data.gbfs import GBFSClient
from avelo.data.osrm import OSRMClient


async def main() -> None:
    settings = get_settings()
    async with GBFSClient(settings) as gbfs:
        stations = await gbfs.get_stations()
    async with OSRMClient(settings) as osrm:
        matrix = await osrm.distance_matrix({sid: s.coord for sid, s in stations.items()})

    ratios = []
    for (a, b), street in matrix.items():
        straight = stations[a].coord.haversine_m(stations[b].coord)
        if straight >= 500:  # below that, snapping noise dominates the ratio
            ratios.append(street / straight)
    ratios.sort()
    n = len(ratios)
    pct = lambda p: ratios[int(p * (n - 1))]  # noqa: E731
    print(f"pairs measured          {n}")
    print(f"median street/straight  {statistics.median(ratios):.3f}")
    print(f"mean                    {statistics.fmean(ratios):.3f}")
    print(f"p10 / p90               {pct(0.10):.3f} / {pct(0.90):.3f}")
    print(f"configured detour_factor {settings.detour_factor:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
