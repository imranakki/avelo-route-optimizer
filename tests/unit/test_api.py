"""End-to-end tests through the HTTP layer, with every upstream mocked.

The app is booted with `AVELO_USE_OSRM=false` so no street router is involved;
GBFS and elevation are served by `respx`. This exercises lifespan warm-up, the
graph cache, request validation and error mapping -- the plumbing the unit tests
of the planner do not touch.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from avelo.config import get_settings

ROOT = "https://feed.test/gbfs.json"
INFO = "https://feed.test/station_information"
STATUS = "https://feed.test/station_status"
ELEV = "https://elev.test/v1/elevation"

# Five stations in a line ~2 km apart, same layout as the planner fixtures.
STATIONS = [
    {
        "station_id": str(i),
        "name": f"S{i}",
        "lat": 46.8139,
        "lon": -71.28 + i * 0.026,
        "capacity": 20,
    }
    for i in range(5)
]
STATUS_ROWS = [
    {
        "station_id": str(i),
        "num_bikes_available": 6,
        "num_docks_available": 6,
        "vehicle_types_available": [{"vehicle_type_id": "EFIT", "count": 6}],
        "is_renting": True,
        "is_returning": True,
        "last_reported": 1,
    }
    for i in range(5)
]


def _mock_upstreams(router: respx.MockRouter) -> None:
    router.get(ROOT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "en": {
                        "feeds": [
                            {"name": "station_information", "url": INFO},
                            {"name": "station_status", "url": STATUS},
                        ]
                    }
                }
            },
        )
    )
    router.get(INFO).mock(return_value=httpx.Response(200, json={"data": {"stations": STATIONS}}))
    router.get(STATUS).mock(
        return_value=httpx.Response(200, json={"data": {"stations": STATUS_ROWS}})
    )
    router.get(url__startswith=ELEV).mock(
        side_effect=lambda request: httpx.Response(
            200, json={"elevation": [50.0] * (request.url.params["latitude"].count(",") + 1)}
        )
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("AVELO_GBFS_ROOT", ROOT)
    monkeypatch.setenv("AVELO_GBFS_LANGUAGE", "en")
    monkeypatch.setenv("AVELO_ELEVATION_API", ELEV)
    monkeypatch.setenv("AVELO_USE_OSRM", "false")
    monkeypatch.setenv("AVELO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("AVELO_MAX_EDGE_DISTANCE_KM", "20")
    get_settings.cache_clear()
    from avelo.api import app as app_module

    with respx.mock(assert_all_called=False) as router:
        _mock_upstreams(router)
        with TestClient(app_module.app) as c:
            yield c
    get_settings.cache_clear()


WEST = {"from_lat": 46.8139, "from_lon": -71.2805}
EAST = {"to_lat": 46.8139, "to_lon": -71.1755}


def test_health_reports_warm_caches(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["stations_cached"] >= 1
    assert body["ride_limit_minutes"] == 30.0


def test_stations_endpoint_lists_live_data(client: TestClient) -> None:
    body = client.get("/stations").json()
    assert body["count"] == 5
    assert body["stations"][0]["elevation_m"] == 50.0
    assert body["stations"][0]["bikes_by_type"] == {"EFIT": 6}


def test_route_returns_walk_ride_walk(client: TestClient) -> None:
    r = client.get("/route", params={**WEST, **EAST})
    assert r.status_code == 200, r.text
    it = r.json()["itinerary"]
    assert it["legs"][0]["mode"] == "WALK" and it["legs"][-1]["mode"] == "WALK"
    assert all(leg["duration_seconds"] <= 1800 for leg in it["legs"] if leg["mode"] == "RIDE")
    assert "X-Response-Ms" in r.headers


def test_options_returns_non_dominated_frontier(client: TestClient) -> None:
    body = client.get("/route/options", params={**WEST, **EAST, "max_solutions": 10}).json()
    opts = body["options"]
    assert body["count"] == len(opts) >= 1
    times = [o["total_seconds"] for o in opts]
    assert times == sorted(times)
    assert body["stats"]["labels_generated"] > 0
    assert body["stats"]["walk_legs_street_routed"] is False  # OSRM disabled in tests


def test_compare_bundles_all_strategies(client: TestClient) -> None:
    body = client.get("/route/compare", params={**WEST, **EAST}).json()
    assert body["naive"]["num_transfers"] == 0
    assert body["hard_constraint"]["total_cost"] == 0.0
    assert len(body["options"]) >= 1


def test_unreachable_destination_is_404_not_500(client: TestClient) -> None:
    r = client.get("/route", params={**WEST, "to_lat": 47.38, "to_lon": -61.86})
    assert r.status_code == 404
    assert "destination" in r.json()["detail"]  # says *why*, not just "no route"


def test_invalid_coordinates_are_422(client: TestClient) -> None:
    assert client.get("/route", params={**WEST, "to_lat": 95, "to_lon": 0}).status_code == 422


def test_index_serves_the_map(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "leaflet" in r.text.lower()


def test_geocode_proxies_and_normalises_results(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith="https://photon.komoot.io/api/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "geometry": {"coordinates": [-71.2741, 46.7812]},
                            "properties": {
                                "name": "Université Laval",
                                "street": "Rue de l'Université",
                                "city": "Québec",
                                "osm_value": "university",
                            },
                        }
                    ]
                },
            )
        )
        body = client.get("/geocode", params={"q": "universite laval"}).json()
    assert body["results"][0]["name"] == "Université Laval"
    assert body["results"][0]["label"] == "Rue de l'Université, Québec"
    assert body["results"][0]["lat"] == 46.7812


def test_geocode_rejects_empty_query(client: TestClient) -> None:
    assert client.get("/geocode", params={"q": ""}).status_code == 422
