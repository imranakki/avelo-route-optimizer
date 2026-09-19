"""Elevation lookup, cached permanently on disk.

Why this module exists: Québec City is not flat. The Basse-Ville sits by the
St. Lawrence; the Haute-Ville sits on Cap Diamant roughly 60-90 m above it. A
router that assumes constant speed will tell a user that Place-Royale ->
Grande Allée takes 6 minutes. On a non-assisted ICONIC, up the Côte de la
Montagne, it does not.

This is the single most defensible idea in the project, because it is:
  - locally specific (a generic tool would never model it),
  - empirically checkable (you can time the ride yourself), and
  - the actual cause of limit overruns in the real system.

Caching: terrain does not move. Once a station's elevation is fetched it is
correct forever, so the cache is written to disk with no TTL. 225 stations is
one batched request; after the first run the project works fully offline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from avelo.config import Settings, get_settings
from avelo.models import Coord, Station

log = logging.getLogger(__name__)

# open-meteo accepts comma-separated coordinate lists. Keep batches modest so the
# URL stays well under any server-side length limit.
_BATCH_SIZE = 100


class ElevationClient:
    def __init__(
        self, settings: Settings | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client or httpx.AsyncClient(timeout=self.settings.http_timeout_seconds)
        self._owns_client = client is None
        self._path = Path(self.settings.cache_dir) / "elevation.json"
        self._cache: dict[str, float] = self._load()

    async def __aenter__(self) -> ElevationClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- disk cache ---------------------------------------------------------

    @staticmethod
    def _key(c: Coord) -> str:
        # ~11 m precision. Finer than that is noise given the DEM's own resolution,
        # and rounding keeps the cache from filling with near-duplicate keys.
        return f"{c.lat:.4f},{c.lon:.4f}"

    def _load(self) -> dict[str, float]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError):
            log.warning("elevation cache at %s unreadable; starting fresh", self._path)
            return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._cache, indent=0, sort_keys=True))

    # -- fetching -----------------------------------------------------------

    async def elevations(self, coords: list[Coord]) -> dict[str, float]:
        """Return {coord_key: metres} for every coord, fetching only cache misses."""
        missing = [c for c in coords if self._key(c) not in self._cache]
        # dict.fromkeys preserves order while de-duplicating -- two stations can
        # round to the same key.
        unique = list(dict.fromkeys(self._key(c) for c in missing))
        by_key = {self._key(c): c for c in missing}

        for i in range(0, len(unique), _BATCH_SIZE):
            batch = [by_key[k] for k in unique[i : i + _BATCH_SIZE]]
            lats = ",".join(f"{c.lat:.4f}" for c in batch)
            lons = ",".join(f"{c.lon:.4f}" for c in batch)
            try:
                r = await self._client.get(
                    self.settings.elevation_api, params={"latitude": lats, "longitude": lons}
                )
                r.raise_for_status()
                values = r.json()["elevation"]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                log.warning("elevation batch failed (%s); those stations stay unknown", exc)
                continue
            for coord, metres in zip(batch, values, strict=False):
                self._cache[self._key(coord)] = float(metres)

        if missing:
            self._save()
        return {
            self._key(c): self._cache[self._key(c)] for c in coords if self._key(c) in self._cache
        }

    async def annotate(self, stations: dict[str, Station]) -> dict[str, Station]:
        """Return the same stations with `elevation_m` populated where known."""
        coords = [s.coord for s in stations.values()]
        table = await self.elevations(coords)
        return {
            sid: st.model_copy(update={"elevation_m": table.get(self._key(st.coord))})
            for sid, st in stations.items()
        }
