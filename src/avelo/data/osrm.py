"""Real street distances and route geometry from OSRM, cached permanently on disk.

Why this exists: haversine * 1.35 is a guess. The road network is not. For the
graph edges (station -> station) we can afford the truth, because there are only
~25k pairs and they never change: one batch of `/table` requests on first run, then
the project works offline forever.

Two OSRM instances are used, both public FOSSGIS servers (verified 2026-09-19):

  * `osrm_root`      -- the BICYCLE profile, for ride legs. This is the real bike
    network: cycle paths count, motorways do not, one-way streets are respected.
  * `osrm_walk_root` -- the FOOT profile, for the walk to the first station and
    from the last. Pedestrian shortcuts and stairs are in; the walk detour factor
    is only the fallback.

`/table` rejects requests with more than 100 coordinates ("TooBig"). The matrix is
therefore fetched in blocks of 50 sources x 50 destinations, with a short delay
between requests. Be a good citizen: it is someone else's server.

Every failure degrades, never breaks: a pair we could not fetch is simply absent
from the result, and the graph falls back to haversine * detour for that pair.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx

from avelo.config import Settings, get_settings
from avelo.models import Coord

log = logging.getLogger(__name__)

PairKey = tuple[str, str]

_GEOMETRY_FLUSH_EVERY = 25


def coord_key(c: Coord) -> str:
    """~11 m precision, matching the elevation cache. Finer is noise."""
    return f"{c.lat:.4f},{c.lon:.4f}"


class OSRMClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        root: str | None = None,
        profile: str = "bike",
    ) -> None:
        """`root` defaults to the bicycle instance; pass `settings.osrm_walk_root` and
        `profile="foot"` for a walking client. The profile name only namespaces the
        on-disk cache so the two networks' distances never mix."""
        self.settings = settings or get_settings()
        self.root = (root or self.settings.osrm_root).rstrip("/")
        self.profile = profile
        self._client = client or httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": "avelo-route-optimizer/0.1"},
        )
        self._owns_client = client is None
        cache_dir = Path(self.settings.cache_dir)
        self._dist_path = cache_dir / f"osrm_{profile}_distances.json"
        self._geom_path = cache_dir / f"osrm_{profile}_geometry.json"
        # {"lat,lon|lat,lon": metres}
        self._distances: dict[str, float] = self._load(self._dist_path)
        # {"lat,lon|lat,lon": [[lat, lon], ...]}
        self._geometry: dict[str, list[list[float]]] = self._load(self._geom_path)
        # Geometry is written in batches: the file grows to megabytes and rewriting
        # it after every single fetch would dominate a bulk warm-up.
        self._geometry_dirty = 0

    async def __aenter__(self) -> OSRMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self.flush()
        if self._owns_client:
            await self._client.aclose()

    def flush(self) -> None:
        """Write any unsaved geometry to disk."""
        if self._geometry_dirty:
            self._save(self._geom_path, self._geometry)
            self._geometry_dirty = 0

    # -- disk cache ---------------------------------------------------------

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError):
            log.warning("OSRM cache at %s unreadable; starting fresh", path)
            return {}

    @staticmethod
    def _save(path: Path, data: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")))
        tmp.replace(path)  # atomic on POSIX: a crash mid-write cannot corrupt the cache

    @staticmethod
    def _pair(a: Coord, b: Coord) -> str:
        return f"{coord_key(a)}|{coord_key(b)}"

    @property
    def cached_pairs(self) -> int:
        return len(self._distances)

    @property
    def cached_geometries(self) -> int:
        return len(self._geometry)

    def has_geometry(self, a: Coord, b: Coord) -> bool:
        return self._pair(a, b) in self._geometry

    # -- distance matrix ----------------------------------------------------

    def cached_distance_m(self, a: Coord, b: Coord) -> float | None:
        return self._distances.get(self._pair(a, b))

    async def distance_matrix(self, points: Mapping[str, Coord]) -> dict[PairKey, float]:
        """Street distance for every ordered pair in `points`, keyed by their ids.

        Fetches only the blocks that contain at least one uncached pair. On a warm
        cache this is a pure dictionary lookup.
        """
        ids = list(points)
        coords = [points[i] for i in ids]
        block = max(2, self.settings.osrm_table_max_coords // 2)

        fetched_any = False
        for si in range(0, len(ids), block):
            for di in range(0, len(ids), block):
                src = coords[si : si + block]
                dst = coords[di : di + block]
                if all(
                    self._pair(a, b) in self._distances
                    for a in src
                    for b in dst
                    if coord_key(a) != coord_key(b)
                ):
                    continue
                ok = await self._fetch_table(src, dst)
                fetched_any = fetched_any or ok
                await asyncio.sleep(self.settings.osrm_request_delay_seconds)
        if fetched_any:
            self._save(self._dist_path, self._distances)

        out: dict[PairKey, float] = {}
        for ia, a in zip(ids, coords, strict=True):
            for ib, b in zip(ids, coords, strict=True):
                if ia == ib:
                    continue
                d = self._distances.get(self._pair(a, b))
                if d is not None:
                    out[(ia, ib)] = d
        return out

    async def one_to_many(self, origin: Coord, targets: Mapping[str, Coord]) -> dict[str, float]:
        """Street distance from one point to each target, keyed by target id.

        Not disk-cached: the origin is a user's arbitrary address, so hits would be
        rare and the cache would grow without bound. Returns {} on failure.
        """
        ids = list(targets)
        if not ids:
            return {}
        rows = await self._table_rows([origin], [targets[i] for i in ids])
        if not rows:
            return {}
        return {i: float(d) for i, d in zip(ids, rows[0], strict=False) if d is not None}

    async def many_to_one(
        self, sources: Mapping[str, Coord], destination: Coord
    ) -> dict[str, float]:
        """Street distance from each source to one point, keyed by source id."""
        ids = list(sources)
        if not ids:
            return {}
        rows = await self._table_rows([sources[i] for i in ids], [destination])
        if not rows:
            return {}
        return {i: float(row[0]) for i, row in zip(ids, rows, strict=False) if row[0] is not None}

    async def _table_rows(
        self, sources: list[Coord], destinations: list[Coord]
    ) -> list[list[float | None]] | None:
        """One `/table` call: sources x destinations, distances in metres."""
        # OSRM wants a single coordinate list plus index lists for the two roles.
        # Sources and destinations may overlap (the diagonal blocks); de-duplicate so
        # the request stays under the coordinate cap.
        unique: dict[str, Coord] = {}
        for c in [*sources, *destinations]:
            unique.setdefault(coord_key(c), c)
        order = list(unique)
        index = {k: i for i, k in enumerate(order)}
        path = ";".join(f"{unique[k].lon:.6f},{unique[k].lat:.6f}" for k in order)
        params = {
            "annotations": "distance",
            "sources": ";".join(str(index[coord_key(c)]) for c in sources),
            "destinations": ";".join(str(index[coord_key(c)]) for c in destinations),
        }
        url = f"{self.root}/table/v1/driving/{path}"
        try:
            r = await self._client.get(url, params=params)
            r.raise_for_status()
            payload = r.json()
            if payload.get("code") != "Ok":
                raise ValueError(payload.get("message", payload.get("code")))
            rows: list[list[float | None]] = payload["distances"]
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            log.warning("OSRM %s table request failed (%s)", self.profile, exc)
            return None
        return rows

    async def _fetch_table(self, sources: list[Coord], destinations: list[Coord]) -> bool:
        rows = await self._table_rows(sources, destinations)
        if rows is None:
            return False
        for a, row in zip(sources, rows, strict=False):
            for b, metres in zip(destinations, row, strict=False):
                if metres is None or coord_key(a) == coord_key(b):
                    continue  # unroutable pair (snapped to a disconnected road), or self
                self._distances[self._pair(a, b)] = float(metres)
        return True

    # -- route geometry -----------------------------------------------------

    async def route_geometry(self, a: Coord, b: Coord, persist: bool = True) -> list[Coord] | None:
        """Simplified polyline of the street route a -> b, for drawing on a map.

        Cached per pair when `persist` is set (station pairs recur; a user's front
        door does not). Returns None if OSRM is unreachable: a map without a line is
        better than an API that fails because the map decoration did.
        """
        key = self._pair(a, b)
        hit = self._geometry.get(key)
        if hit is None:
            url = f"{self.root}/route/v1/driving/{a.lon:.6f},{a.lat:.6f};{b.lon:.6f},{b.lat:.6f}"
            try:
                r = await self._client.get(
                    url, params={"overview": "simplified", "geometries": "geojson"}
                )
                r.raise_for_status()
                payload = r.json()
                line = payload["routes"][0]["geometry"]["coordinates"]
                hit = [[float(lat), float(lon)] for lon, lat in line]
            except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
                log.warning("OSRM route geometry failed (%s)", exc)
                return None
            if persist:
                self._geometry[key] = hit
                self._geometry_dirty += 1
                if self._geometry_dirty >= _GEOMETRY_FLUSH_EVERY:
                    self.flush()
        return [Coord(lat=lat, lon=lon) for lat, lon in hit]

    async def geometries(self, pairs: Iterable[tuple[Coord, Coord]]) -> list[list[Coord] | None]:
        return [await self.route_geometry(a, b) for a, b in pairs]
