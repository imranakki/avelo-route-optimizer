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
    monkeypatch.setenv("AVELO_GOOGLE_MAPS_API_KEY", "")  # a developer .env must not leak in
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


def test_index_redirects_to_the_openapi_docs(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


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


def test_ride_limit_is_per_request(client: TestClient) -> None:
    """A 45-minute plan admits longer single hops than a 30-minute one."""
    short = client.get("/route", params={**WEST, **EAST, "limit": 15}).json()["itinerary"]
    long = client.get("/route", params={**WEST, **EAST, "limit": 60}).json()["itinerary"]
    assert short["num_transfers"] > long["num_transfers"]
    assert client.get("/health").json()["graphs_cached"] == ["EFIT/15", "EFIT/60"]


def test_geocode_prefers_google_places_and_resolves_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AVELO_GBFS_ROOT", ROOT)
    monkeypatch.setenv("AVELO_GBFS_LANGUAGE", "en")
    monkeypatch.setenv("AVELO_ELEVATION_API", ELEV)
    monkeypatch.setenv("AVELO_USE_OSRM", "false")
    monkeypatch.setenv("AVELO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("AVELO_GOOGLE_MAPS_API_KEY", "test-key")
    get_settings.cache_clear()
    from avelo.api import app as app_module

    with respx.mock(assert_all_called=False) as router:
        _mock_upstreams(router)
        auto = router.post(host="places.googleapis.com", path__startswith="/v1/places").mock(
            return_value=httpx.Response(
                200,
                json={
                    "suggestions": [
                        {
                            "placePrediction": {
                                "placeId": "ChIJ123",
                                "types": ["university"],
                                "structuredFormat": {
                                    "mainText": {"text": "Université Laval"},
                                    "secondaryText": {"text": "Rue de l'Université, Québec, QC"},
                                },
                            }
                        }
                    ]
                },
            )
        )
        router.get(host="places.googleapis.com", path="/v1/places/ChIJ123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "location": {"latitude": 46.7812, "longitude": -71.2741},
                    "displayName": {"text": "Université Laval"},
                    "formattedAddress": "2325 Rue de l'Université, Québec, QC",
                },
            )
        )
        with TestClient(app_module.app) as c:
            body = c.get("/geocode", params={"q": "universite laval", "session": "abc"}).json()
            assert body["provider"] == "google"
            assert body["results"][0]["place_id"] == "ChIJ123" and body["results"][0]["lat"] is None
            assert auto.calls.last.request.headers["X-Goog-Api-Key"] == "test-key"
            assert b'"sessionToken":"abc"' in auto.calls.last.request.content.replace(b" ", b"")
            place = c.get("/geocode/place", params={"id": "ChIJ123"}).json()
            assert place["lat"] == 46.7812 and place["name"] == "Université Laval"
    get_settings.cache_clear()


def test_trip_chains_segments_and_returns_home(client: TestClient) -> None:
    """Three stops with a round trip = three segments back to the start; the combined
    frontier is non-dominated and every option has one choice per segment."""
    home = f"{WEST['from_lat']},{WEST['from_lon']}"
    office = f"{EAST['to_lat']},{EAST['to_lon']}"
    r = client.get(
        "/trip",
        params={
            "stops": f"{home};46.8139,-71.228;{office}",
            "names": "Home|Café|Office",
            "round_trip": "true",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["name"] for s in body["stops"]] == ["Home", "Café", "Office", "Home"]
    assert len(body["segments"]) == 3
    hard = body["hard_constraint"]
    assert hard["legs"][0]["from_name"] == "Home" and hard["legs"][-1]["to_name"] == "Home"
    assert hard["total_seconds"] == pytest.approx(
        sum(s["hard_constraint"]["total_seconds"] for s in body["segments"])
    )
    assert all(leg["duration_seconds"] <= 1800 for leg in hard["legs"] if leg["mode"] == "RIDE")
    opts = body["options"]
    assert opts and all(len(o["choices"]) == 3 for o in opts)
    for a in opts:
        for b in opts:
            ia, ib = a["itinerary"], b["itinerary"]
            if a is not b:
                assert not (
                    ia["total_seconds"] <= ib["total_seconds"]
                    and ia["total_cost"] < ib["total_cost"]
                )


def test_trip_rejects_a_single_stop(client: TestClient) -> None:
    assert client.get("/trip", params={"stops": "46.81,-71.2"}).status_code == 422


def test_search_is_rate_limited_per_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AVELO_GBFS_ROOT", ROOT)
    monkeypatch.setenv("AVELO_GBFS_LANGUAGE", "en")
    monkeypatch.setenv("AVELO_ELEVATION_API", ELEV)
    monkeypatch.setenv("AVELO_USE_OSRM", "false")
    monkeypatch.setenv("AVELO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("AVELO_GOOGLE_MAPS_API_KEY", "")
    monkeypatch.setenv("AVELO_RATE_LIMIT_SEARCH_PER_MINUTE", "3")
    get_settings.cache_clear()
    from avelo.api import app as app_module

    app_module._limiter._hits.clear()
    with respx.mock(assert_all_called=False) as router:
        _mock_upstreams(router)
        router.get(url__startswith="https://photon.komoot.io/api/").mock(
            return_value=httpx.Response(200, json={"features": []})
        )
        with TestClient(app_module.app) as c:
            codes = [c.get("/geocode", params={"q": f"q{i}"}).status_code for i in range(4)]
            assert codes == [200, 200, 200, 429]
            assert c.get("/health").status_code == 200  # other routes are not limited
    get_settings.cache_clear()


def test_google_calls_stop_at_the_daily_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AVELO_GBFS_ROOT", ROOT)
    monkeypatch.setenv("AVELO_GBFS_LANGUAGE", "en")
    monkeypatch.setenv("AVELO_ELEVATION_API", ELEV)
    monkeypatch.setenv("AVELO_USE_OSRM", "false")
    monkeypatch.setenv("AVELO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("AVELO_GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setenv("AVELO_GOOGLE_DAILY_CAP", "2")
    get_settings.cache_clear()
    from avelo.api import app as app_module

    app_module._limiter._hits.clear()
    with respx.mock(assert_all_called=False) as router:
        _mock_upstreams(router)
        google = router.post(host="places.googleapis.com", path__startswith="/v1/places").mock(
            return_value=httpx.Response(200, json={"suggestions": []})
        )
        photon = router.get(url__startswith="https://photon.komoot.io/api/").mock(
            return_value=httpx.Response(200, json={"features": []})
        )
        with TestClient(app_module.app) as c:
            providers = [
                c.get("/geocode", params={"q": f"q{i}"}).json()["provider"] for i in range(3)
            ]
            assert providers == ["google", "google", "osm"]
            assert google.call_count == 2 and photon.call_count == 1
            assert c.get("/health").json()["google_calls_today"] == 2
    get_settings.cache_clear()
