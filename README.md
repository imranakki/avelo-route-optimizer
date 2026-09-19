# àVélo Route Optimizer

Constraint-aware multi-hop routing for the àVélo bike-share network in Québec City.

Bike-share plans cap each ride at **30 or 45 minutes**; past that you pay **$0.30/min**.
Ordinary navigation apps optimise for time or distance and ignore that limit, so they
routinely hand you a route that quietly costs money. This system plans a trip as a
sequence of rides on **the same bike**, docking and re-unlocking at intermediate stations
so the clock resets — and it models the thing that actually causes overruns here:
**hills**. It runs on live station data, real bicycle-network street distances, and
returns the whole time/money trade-off rather than one answer.

## Results

Measured over 1,000 sampled trips on live data (`docs/evaluation.md`, reproducible with
`make eval`):

| metric | naive (one ride, nearest stations) | this system (every leg under the limit) |
|---|---:|---:|
| trips exceeding the 30-min limit | **49.9 %** | **0.0 %** |
| mean overage charge per trip | **$2.75** | **$0.00** |
| mean door-to-door time | 39.9 min | 41.4 min |
| median door-to-door time | 36.4 min | 36.0 min |
| p95 planning latency | 0.6 ms | 89 ms |

Half of all trips in this network overrun a 30-minute plan when ridden naively. Routing
them as resettable legs removes every overage charge for a mean cost of 1.5 minutes —
and the median trip is actually *faster*, because the router is free to walk to a
better station than the nearest one.

One real trip, Place-Royale (Basse-Ville) → Université Laval on an EFIT:

```
naive        43.7 min   $2.70   direct ride, 38.5 min on the clock
constrained  43.7 min   $0.00   reset at Saint-Jean / Des Zouaves (9.2 + 27.7 min)
frontier     43.5 min   $2.10   |  43.7 min   $0.00
```

The Pareto search generated 53,209 labels for that trip and pruned 98 % of them by
dominance; the frontier is two points and the planner took 170 ms.

**Ablation.** The same evaluation re-run with the elevation model off is in
`docs/evaluation.md`. Honest result: for today's all-electric fleet the hill model
changes individual leg estimates (a 90 m climb costs an EFIT +39 % cycling time) but
rarely changes which route wins, because the e-bike penalty is mild and climbs and
descents average out. It would matter far more for mechanical bikes (+145 % on the same
climb), of which àVélo currently has none in service.

## What it does

- **Live data.** Station registry and availability from àVélo's GBFS feed, with per-feed
  TTL caching, stale-serving on upstream failure, and corrupt-row filtering (the live
  feed ships a station at `lat=0, lon=0`).
- **Real map data.** Station-to-station distances from an OSRM **bicycle** profile
  (50,400 ordered pairs, asymmetric, cached forever); walk legs from an OSRM **foot**
  profile; street polylines for the map. Public FOSSGIS instances by default, or
  self-hosted with `scripts/prepare_osrm.sh` + `docker compose` for production.
- **A grade-aware time model.** Elevation per station (open-meteo, 141 m of relief across
  the network), an asymmetric hill model calibrated so a 5 % climb halves a mechanical
  bike's speed, expected traffic-light delay, reset overhead and a safety buffer.
  Every parameter is config; the derivation is in `docs/cost-model.md`.
- **Two planners.**
  - *Hard constraint:* every ride leg under the limit. Because the constraint is
    per-edge, over-limit edges are removed and ordinary Dijkstra does the rest.
  - *Pareto frontier:* you may exceed the limit at $0.30/min. Minimising time and money
    together is the bi-criteria shortest-path problem; solved with Martins' label-setting
    algorithm and dominance pruning, instrumented so the pruning ratio is reported.
- **An honest baseline** (nearest-to-nearest, one ride) and an evaluation harness that
  compares all strategies on the same trips, costed with the same model.
- **A production API**: FastAPI, lifespan-managed clients, a TTL-cached routing graph
  behind a lock, CORS, gzip, typed responses, structured 404/503s, health endpoint,
  place search and reverse geocoding (`/geocode`), Dockerfile, docker-compose.
- **A web app** (`web/`, Next.js + MapLibre): search for places or drop pins, pick a
  bike type, and every strategy is planned and drawn on real streets with resets marked.

## Quick start

```bash
make install     # venv + dependencies
make test        # offline suite (71 tests, no network)
make run         # API at http://127.0.0.1:8000 (docs at /docs, a minimal map at /)
make web         # Next.js UI at http://127.0.0.1:3000
```

First boot fetches the street-distance matrix once (~25 requests) and caches it; the
committed cache means a fresh clone routes immediately.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | interactive map |
| `GET /health` | liveness, cache state |
| `GET /stations` | live stations with availability, vehicle types, elevation |
| `GET /route` | fastest itinerary with every ride leg under the limit |
| `GET /route/options` | the time/cost Pareto frontier (`max_solutions`) |
| `GET /route/baseline` | the naive control |
| `GET /route/compare` | all three side by side |
| `GET /geocode`, `/geocode/reverse` | place search within Québec City (Photon/OSM, cached) |

All routing endpoints take `from_lat, from_lon, to_lat, to_lon`, `vehicle` (`EFIT`,
`ICONIC`, …) and `geometry=true` for street polylines. OpenAPI docs at `/docs`.

```bash
curl "localhost:8000/route/options?from_lat=46.8127&from_lon=-71.2024&to_lat=46.7817&to_lon=-71.2747"
```

### Self-hosted routing (production)

The public OSRM servers are run by volunteers for light use. For anything beyond a demo:

```bash
./scripts/prepare_osrm.sh          # download Québec extract, clip, build bike + foot data
docker compose up -d               # web + api + osrm-bike + osrm-foot
make cache                         # full offline cache incl. every ride-leg polyline
```

### Other commands

```bash
make lint        # ruff + strict mypy
make live        # integration tests against the real feed
make eval        # regenerate docs/evaluation.md
./.venv/bin/python scripts/calibrate_detour.py   # measured street/straight-line ratio
```

## Architecture

```
GBFS feed ─────┐
elevation ─────┼──> StationSnapshot ──> StationGraph ──> RoutePlanner ──> FastAPI ──> map
OSRM matrix ───┘   (static + live)      (time-pruned      ├─ Dijkstra (hard limit)
                                         adjacency)       ├─ Martins (Pareto frontier)
                                                          └─ naive baseline
```

`src/avelo/`

| module | role |
|---|---|
| `config.py` | every tunable, overridable as `AVELO_*` env vars |
| `models.py` | frozen domain types: `Coord`, `Station`, `StationSnapshot`, `Leg`, `Itinerary` |
| `data/gbfs.py` | GBFS discovery, TTL cache, stale-serving, filtering |
| `data/elevation.py` | batched open-meteo lookups, permanent disk cache |
| `data/osrm.py` | distance matrices, one-to-many walks, polylines; disk cache |
| `routing/cost.py` | the time model — pure functions of geometry and config |
| `routing/graph.py` | station roles (start / reset / end) and time-pruned edges |
| `routing/planner.py` | Dijkstra, Martins' bi-criteria search, baseline |
| `evaluation/harness.py` | density-weighted trip sampling, metrics, ablation |
| `data/geocode.py` | place search / reverse geocoding via Photon, cached |
| `api/app.py` | FastAPI service and the static map |

`web/` — Next.js app: `PlaceSearch` (autocomplete), `MapView` (MapLibre), `ResultsPanel`,
`TripPlanner` (state), `lib/api.ts` (typed client).

Because it codes against the GBFS open standard rather than àVélo specifically, the same
system runs on Bixi, Citi Bike, Vélib' and ~600 other networks by changing one URL.

## Design decisions worth knowing

- **Transfers are clock resets, not bike swaps.** A transfer station needs a free dock
  and to be in service; it does not need a spare bike. Only the origin needs a bike of the
  requested type. This is more permissive than a bike-swap model and matches how riders
  actually reset the timer.
- **Edges are pruned by time, not by neighbour rank.** A nearest-K rule was tried first
  and rejected: in the dense core the 25 nearest stations are all within 1.5 km, so it
  dropped exactly the long hops that keep reset counts low. An edge now survives if its
  estimated duration is under 1.5× the limit.
- **Every leg is costed identically, including the last.** Adjusting the final leg after
  the search can silently produce a dominated pair in the returned frontier.
- **The detour factor is measured.** Over 49,626 station pairs the bicycle network is a
  median 1.267× the straight line; the original guess of 1.35 was wrong.
- **Decoration never fails a request.** Street-routed walk legs and polylines fall back
  to estimates when the router is unreachable, and the response says so.

## Tests

71 offline tests: property-based specs for the cost model, graph-role and pruning tests,
planner invariants (every leg under the limit; returned frontier mutually non-dominated;
sorted by time; includes the $0 option when one exists), OSRM client behaviour under
failure, and end-to-end HTTP tests with every upstream mocked. Integration tests against
the live feed are opt-in (`make live`).

## Licence

MIT

## Author

Imran Akki — Université Laval, AI / Computer Science
