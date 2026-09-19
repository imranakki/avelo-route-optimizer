"""Tests for the OSRM client. Offline: `respx` intercepts every HTTP call."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from avelo.config import Settings
from avelo.data.osrm import OSRMClient
from avelo.models import Coord

ROOT = "https://osrm.test/routed-bike"

A = Coord(lat=46.8139, lon=-71.2080)
B = Coord(lat=46.8087, lon=-71.2245)
C = Coord(lat=46.8000, lon=-71.2300)


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(
        osrm_root=ROOT,
        cache_dir=str(tmp_path),
        osrm_table_max_coords=100,
        osrm_request_delay_seconds=0.0,
    )


def _table(distances: list[list[float | None]]) -> httpx.Response:
    return httpx.Response(200, json={"code": "Ok", "distances": distances})


@respx.mock
async def test_distance_matrix_returns_ordered_pairs(cfg: Settings) -> None:
    route = respx.get(url__regex=rf"{ROOT}/table/v1/driving/.*").mock(
        return_value=_table([[0, 1672.4, 3386.4], [1569.2, 0, 1714], [3134.3, 1701.7, 0]])
    )
    async with OSRMClient(cfg) as client:
        matrix = await client.distance_matrix({"a": A, "b": B, "c": C})
    assert route.call_count == 1
    assert matrix[("a", "b")] == pytest.approx(1672.4)
    assert matrix[("b", "a")] == pytest.approx(1569.2)  # asymmetric: one-way streets
    assert ("a", "a") not in matrix


@respx.mock
async def test_matrix_is_cached_on_disk_across_instances(cfg: Settings) -> None:
    route = respx.get(url__regex=rf"{ROOT}/table/v1/driving/.*").mock(
        return_value=_table([[0, 1000.0], [1100.0, 0]])
    )
    async with OSRMClient(cfg) as first:
        await first.distance_matrix({"a": A, "b": B})
    async with OSRMClient(cfg) as second:
        matrix = await second.distance_matrix({"a": A, "b": B})
    assert route.call_count == 1, "second instance must be served from disk"
    assert matrix[("a", "b")] == pytest.approx(1000.0)
    assert (Path(cfg.cache_dir) / "osrm_bike_distances.json").exists()


@respx.mock
async def test_matrix_is_fetched_in_blocks_under_the_coordinate_cap(tmp_path: Path) -> None:
    """With a cap of 4 coordinates, 5 points need blocks of 2x2 -> 9 requests."""
    cfg = Settings(
        osrm_root=ROOT,
        cache_dir=str(tmp_path),
        osrm_table_max_coords=4,
        osrm_request_delay_seconds=0.0,
    )
    seen: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        n = request.url.path.split("/")[-1].count(";") + 1
        seen.append(n)
        srcs = request.url.params["sources"].count(";") + 1
        dsts = request.url.params["destinations"].count(";") + 1
        return _table([[100.0] * dsts for _ in range(srcs)])

    respx.get(url__regex=rf"{ROOT}/table/v1/driving/.*").mock(side_effect=respond)
    points = {str(i): Coord(lat=46.80 + i * 0.01, lon=-71.20) for i in range(5)}
    async with OSRMClient(cfg) as client:
        matrix = await client.distance_matrix(points)
    assert max(seen) <= 4
    assert len(matrix) == 5 * 4


@respx.mock
async def test_unroutable_pairs_are_absent_not_zero(cfg: Settings) -> None:
    respx.get(url__regex=rf"{ROOT}/table/v1/driving/.*").mock(
        return_value=_table([[0, None], [1100.0, 0]])
    )
    async with OSRMClient(cfg) as client:
        matrix = await client.distance_matrix({"a": A, "b": B})
    assert ("a", "b") not in matrix
    assert matrix[("b", "a")] == pytest.approx(1100.0)


@respx.mock
async def test_server_failure_degrades_to_empty_matrix(cfg: Settings) -> None:
    respx.get(url__regex=rf"{ROOT}/table/v1/driving/.*").mock(
        return_value=httpx.Response(500, text="boom")
    )
    async with OSRMClient(cfg) as client:
        matrix = await client.distance_matrix({"a": A, "b": B})
    assert matrix == {}


@respx.mock
async def test_one_to_many_keys_by_target_id(cfg: Settings) -> None:
    respx.get(url__regex=rf"{ROOT}/table/v1/driving/.*").mock(
        return_value=_table([[640.0, 910.0]])  # one source row, two destination columns
    )
    async with OSRMClient(cfg) as client:
        out = await client.one_to_many(A, {"b": B, "c": C})
    assert out == {"b": pytest.approx(640.0), "c": pytest.approx(910.0)}


@respx.mock
async def test_route_geometry_is_lat_lon_and_cached_when_persisted(cfg: Settings) -> None:
    route = respx.get(url__regex=rf"{ROOT}/route/v1/driving/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [
                    {"geometry": {"coordinates": [[-71.2080, 46.8139], [-71.2245, 46.8087]]}}
                ],
            },
        )
    )
    async with OSRMClient(cfg) as client:
        line = await client.route_geometry(A, B, persist=True)
        again = await client.route_geometry(A, B, persist=True)
    assert line is not None and line[0] == A and line[-1] == B  # GeoJSON is lon,lat; we flip
    assert again == line
    assert route.call_count == 1
    cached = json.loads((Path(cfg.cache_dir) / "osrm_bike_geometry.json").read_text())
    assert len(cached) == 1


@respx.mock
async def test_route_geometry_not_persisted_for_transient_points(cfg: Settings) -> None:
    respx.get(url__regex=rf"{ROOT}/route/v1/driving/.*").mock(
        return_value=httpx.Response(
            200, json={"code": "Ok", "routes": [{"geometry": {"coordinates": [[-71.2, 46.8]]}}]}
        )
    )
    async with OSRMClient(cfg) as client:
        assert await client.route_geometry(A, B, persist=False) is not None
    assert not (Path(cfg.cache_dir) / "osrm_bike_geometry.json").exists()
