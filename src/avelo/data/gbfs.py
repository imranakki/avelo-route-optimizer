"""GBFS client for the àVélo feed.

GBFS (General Bikeshare Feed Specification) is the open standard nearly every
bike-share operator publishes. Because you code against the *spec* rather than
against àVélo specifically, this client works for Bixi, Citi Bike, Vélib' and
~600 other systems by changing one URL.

Three things here are worth studying, because they are what separates a script
from a service:

1. **Discovery, not hard-coded URLs.** The root `gbfs.json` lists the feeds. We
   read it and follow the links, so an upstream URL change does not break us.
2. **TTL-aware caching.** The status feed changes every 30s; the station list
   changes monthly. Caching them identically is wrong in one direction or the
   other. So the TTL is per-feed.
3. **Defensive parsing.** Real feeds contain garbage. àVélo currently ships one
   station at `lat=0.0, lon=0.0` -- a point in the Atlantic Ocean off Africa.
   If you feed that into a router it will happily plan a 5,000 km bike ride.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from avelo.config import Settings, get_settings
from avelo.models import Coord, Station, StationSnapshot, StationStatus, VehicleType

log = logging.getLogger(__name__)

# Québec City sanity box. Anything outside this is corrupt data, not a station.
_QC_LAT = (46.6, 47.1)
_QC_LON = (-71.6, -70.9)


class GBFSError(RuntimeError):
    """Upstream feed was unreachable or malformed."""


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class GBFSClient:
    """Async client for a GBFS-compliant feed.

    Use as an async context manager so the underlying connection pool is closed::

        async with GBFSClient() as client:
            snapshots = await client.get_snapshots()
    """

    def __init__(
        self, settings: Settings | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        # Accepting an injected client is what makes this testable: tests pass a
        # mock transport instead of monkey-patching the network. Dependency
        # injection is not ceremony here, it is the reason the test suite can run offline.
        self._client = client or httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": "avelo-route-optimizer/0.1"},
        )
        self._owns_client = client is None
        self._cache: dict[str, _CacheEntry] = {}

    async def __aenter__(self) -> GBFSClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- transport ----------------------------------------------------------

    async def _get_json(self, url: str, ttl: float) -> dict[str, Any]:
        now = time.monotonic()
        hit = self._cache.get(url)
        if hit is not None and hit.expires_at > now:
            return hit.value  # type: ignore[no-any-return]

        try:
            response = await self._client.get(url)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            # Serve stale data rather than failing: a 40-second-old station count
            # is far more useful to a user than a 500 error.
            if hit is not None:
                log.warning("GBFS fetch failed for %s (%s); serving stale cache", url, exc)
                return hit.value  # type: ignore[no-any-return]
            raise GBFSError(f"failed to fetch {url}: {exc}") from exc

        self._cache[url] = _CacheEntry(payload, now + ttl)
        return payload  # type: ignore[no-any-return]

    async def _feed_urls(self) -> dict[str, str]:
        """Follow the GBFS discovery document to a {feed_name: url} map."""
        root = await self._get_json(self.settings.gbfs_root, ttl=86_400)
        data = root.get("data", {})
        lang = self.settings.gbfs_language
        block: dict[str, Any] = data.get(lang) or next(iter(data.values()), {})
        return {f["name"]: f["url"] for f in block.get("feeds", [])}

    # -- parsing ------------------------------------------------------------

    @staticmethod
    def _valid_coord(lat: float, lon: float) -> bool:
        return _QC_LAT[0] <= lat <= _QC_LAT[1] and _QC_LON[0] <= lon <= _QC_LON[1]

    async def get_stations(self) -> dict[str, Station]:
        """Static station registry, keyed by station_id. Corrupt rows are dropped."""
        urls = await self._feed_urls()
        payload = await self._get_json(
            urls["station_information"], ttl=self.settings.station_info_ttl_seconds
        )

        stations: dict[str, Station] = {}
        dropped = 0
        for raw in payload["data"]["stations"]:
            lat, lon = float(raw.get("lat", 0.0)), float(raw.get("lon", 0.0))
            if not self._valid_coord(lat, lon):
                dropped += 1
                continue
            sid = str(raw["station_id"])
            stations[sid] = Station(
                station_id=sid,
                name=raw.get("name", f"Station {sid}"),
                coord=Coord(lat=lat, lon=lon),
                capacity=int(raw.get("capacity", 0)),
            )
        if dropped:
            log.info("dropped %d station(s) with out-of-region coordinates", dropped)
        return stations

    async def get_status(self) -> dict[str, StationStatus]:
        """Live availability, keyed by station_id."""
        urls = await self._feed_urls()
        payload = await self._get_json(
            urls["station_status"], ttl=self.settings.station_status_ttl_seconds
        )

        out: dict[str, StationStatus] = {}
        for raw in payload["data"]["stations"]:
            sid = str(raw["station_id"])
            by_type: dict[VehicleType, int] = {}
            for entry in raw.get("vehicle_types_available", []):
                try:
                    by_type[VehicleType(entry["vehicle_type_id"])] = int(entry["count"])
                except ValueError:
                    continue  # unknown vehicle type: the operator added one, ignore it
            out[sid] = StationStatus(
                station_id=sid,
                num_bikes_available=int(raw.get("num_bikes_available", 0)),
                num_docks_available=int(raw.get("num_docks_available", 0)),
                bikes_by_type=by_type,
                is_renting=bool(raw.get("is_renting", False)),
                is_returning=bool(raw.get("is_returning", False)),
                last_reported=int(raw.get("last_reported", 0)),
            )
        return out

    async def get_snapshots(self) -> dict[str, StationSnapshot]:
        """Join static + live data. This is the router's entry point.

        Note the inner join: a station present in only one feed is skipped. Feeds
        can disagree transiently (a station added to one before the other), and a
        snapshot with a missing half is not something the router can reason about.
        """
        stations = await self.get_stations()
        status = await self.get_status()
        return {
            sid: StationSnapshot(station=st, status=status[sid])
            for sid, st in stations.items()
            if sid in status
        }
