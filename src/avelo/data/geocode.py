"""Place search for the web UI, via the Photon (OSM) geocoder.

Proxied through the API rather than called from the browser so that the client
never depends on a third party directly, results are cached, and the provider can
be swapped by changing one URL. Queries are biased to Québec City and clipped to
its bounding box: a search for "Laval" must return Université Laval, not the
city of Laval near Montréal.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx

from avelo.config import Settings, get_settings

log = logging.getLogger(__name__)

_CACHE_SIZE = 2048


@dataclass(frozen=True)
class Place:
    name: str
    label: str  # one-line description: street, district, city
    lat: float
    lon: float
    kind: str  # OSM value, e.g. "university", "station", "residential"


class GeocodeError(RuntimeError):
    """Upstream geocoder unreachable or malformed."""


class Geocoder:
    def __init__(
        self, settings: Settings | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client or httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": "avelo-route-optimizer/1.0"},
        )
        self._owns_client = client is None
        self._cache: OrderedDict[str, list[Place]] = OrderedDict()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _label(props: dict[str, Any]) -> str:
        parts = [
            " ".join(str(p) for p in (props.get("housenumber"), props.get("street")) if p),
            props.get("district"),
            props.get("city"),
        ]
        seen: list[str] = []
        for part in parts:
            if part and part not in seen and part != props.get("name"):
                seen.append(str(part))
        return ", ".join(seen)

    async def search(self, query: str, limit: int = 6, lang: str = "fr") -> list[Place]:
        q = " ".join(query.split()).lower()
        if len(q) < 2:
            return []
        key = f"{q}|{limit}|{lang}"
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit

        params: dict[str, str | int | float] = {
            "q": q,
            "limit": limit,
            "lang": lang,
            "lat": self.settings.geocoder_bias_lat,
            "lon": self.settings.geocoder_bias_lon,
            "bbox": self.settings.geocoder_bbox,
        }
        try:
            r = await self._client.get(self.settings.geocoder_url, params=params)
            r.raise_for_status()
            features = r.json().get("features", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise GeocodeError(f"geocoder request failed: {exc}") from exc

        places: list[Place] = []
        for f in features:
            props = f.get("properties", {})
            coords = f.get("geometry", {}).get("coordinates")
            name = props.get("name") or props.get("street")
            if not coords or not name:
                continue
            places.append(
                Place(
                    name=str(name),
                    label=self._label(props),
                    lat=float(coords[1]),
                    lon=float(coords[0]),
                    kind=str(props.get("osm_value") or props.get("type") or ""),
                )
            )

        self._cache[key] = places
        if len(self._cache) > _CACHE_SIZE:
            self._cache.popitem(last=False)
        return places

    async def reverse(self, lat: float, lon: float, lang: str = "fr") -> Place | None:
        """Nearest named place to a point, for labelling a pin dropped on the map."""
        url = self.settings.geocoder_url.rstrip("/")
        url = url[: -len("/api")] + "/reverse" if url.endswith("/api") else url + "/reverse"
        params: dict[str, str | int | float] = {"lat": lat, "lon": lon, "lang": lang, "limit": 1}
        try:
            r = await self._client.get(url, params=params)
            r.raise_for_status()
            features = r.json().get("features", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise GeocodeError(f"reverse geocode failed: {exc}") from exc
        for f in features:
            props = f.get("properties", {})
            name = props.get("name") or props.get("street")
            if name:
                return Place(
                    name=str(name),
                    label=self._label(props),
                    lat=lat,
                    lon=lon,
                    kind=str(props.get("osm_value") or ""),
                )
        return None
