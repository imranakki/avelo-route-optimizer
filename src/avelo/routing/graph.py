"""Station graph construction.

The routing graph is: nodes = stations, edge A->B = "ride from A to B, then dock".
The rider keeps one bike throughout; docking at B resets the billing clock.

The interesting engineering question is which edges to *omit*. 225 stations means
50,400 ordered pairs. Most are useless: nobody transfers to a station
6 km behind them. Every edge you keep costs pathfinding time; every edge you
wrongly drop can make an optimal route unreachable. That tension -- completeness
versus tractability -- is the heart of practical graph work.

Where we drew the line (all knobs live in config):
  * `max_edge_limit_multiple` (1.5x the limit): an edge is kept only if a rider
    could plausibly take it -- past 45 min on a 30-min plan the overage alone is
    $4.50 and such a hop never lands on a sensible time/cost frontier.
  * `max_edge_distance_km` (12 km): a cheap straight-line pre-filter before the
    cost model runs. Well above anything that survives the time rule.
  * `max_neighbours_per_station` (250): a safety valve on branching, not the
    primary rule. Pruning by nearest-neighbour rank was tried first and rejected:
    in the dense core the 25 nearest stations are all within 1.5 km, so a rank
    cap dropped exactly the long hops that keep transfer counts low.

Measured: the O(n^2) build over 225 stations with the full street matrix takes
~150 ms and yields ~25k edges. No spatial index needed at this size.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from avelo.config import Settings, get_settings
from avelo.models import RiskLevel, StationSnapshot, VehicleType
from avelo.routing.cost import CostModel

PairKey = tuple[str, str]


@dataclass(frozen=True)
class Edge:
    """A candidate bike leg between two stations, pre-costed with its breakdown."""

    from_id: str
    to_id: str
    distance_m: float
    duration_seconds: float
    elevation_gain_m: float
    overage_cost: float
    cycling_seconds: float = 0.0
    traffic_light_seconds: float = 0.0
    docking_seconds: float = 0.0
    buffer_seconds: float = 0.0
    risk: RiskLevel = RiskLevel.LOW
    measured: bool = False  # True when distance came from OSRM, not haversine*detour
    limit_seconds: float = 0.0

    @property
    def is_within_limit(self) -> bool:
        return self.duration_seconds <= self.limit_seconds


class StationGraph:
    """An adjacency structure over currently-usable stations.

    `distances` is an optional {(from_id, to_id): metres} map of real street
    distances (see `avelo.data.osrm`). Pairs missing from it fall back to the
    haversine * detour estimate, so a partial matrix is fine.
    """

    def __init__(
        self,
        snapshots: dict[str, StationSnapshot],
        cost_model: CostModel,
        settings: Settings | None = None,
        distances: Mapping[PairKey, float] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.snapshots = snapshots
        self.cost = cost_model
        self.distances: Mapping[PairKey, float] = distances or {}
        self._adjacency: dict[str, list[Edge]] = {}
        self._vehicle: VehicleType | None = None

    # -- station roles ------------------------------------------------------
    #
    # The rider keeps the SAME bike for the whole trip. A "transfer" is a clock
    # reset: dock the bike, let the system close the ride, unlock it again. So the
    # roles need different things:
    #   ORIGIN      a bike of the requested type to pick up
    #   TRANSFER    a free dock to reset in, and the station must be renting so
    #               the re-unlock is allowed -- but NO spare bike is needed
    #   DESTINATION a free dock
    # Ride edges therefore run from any station a rider can leave (origin or
    # transfer) to any station with a dock; the origin walk arcs apply the
    # bike-availability check on their own.

    def can_rent(self, station_id: str, vehicle: VehicleType | None = None) -> bool:
        """Can a trip START here: a bike of `vehicle` type is available."""
        snap = self.snapshots.get(station_id)
        return snap is not None and snap.can_rent(self.settings.min_bikes_required, vehicle)

    def can_return(self, station_id: str) -> bool:
        """Can a ride END here: a dock is free."""
        snap = self.snapshots.get(station_id)
        return snap is not None and snap.can_return(self.settings.min_docks_required)

    def can_transfer(self, station_id: str) -> bool:
        """Can the clock be RESET here: a free dock, and renting is enabled so the
        same bike can be unlocked again. Bike availability is irrelevant."""
        snap = self.snapshots.get(station_id)
        return snap is not None and snap.status.is_renting and self.can_return(station_id)

    def rentable_stations(self, vehicle: VehicleType | None = None) -> dict[str, StationSnapshot]:
        return {sid: s for sid, s in self.snapshots.items() if self.can_rent(sid, vehicle)}

    def returnable_stations(self) -> dict[str, StationSnapshot]:
        return {sid: s for sid, s in self.snapshots.items() if self.can_return(sid)}

    def usable_stations(self, vehicle: VehicleType | None = None) -> dict[str, StationSnapshot]:
        """Stations that can serve as a TRANSFER (clock reset) right now."""
        return {sid: s for sid, s in self.snapshots.items() if self.can_transfer(sid)}

    # -- construction -------------------------------------------------------

    def build(self, vehicle: VehicleType = VehicleType.EFIT) -> None:
        """Populate `self._adjacency` for one vehicle type.

        Edges go from every station a rider can leave to every station with a free
        dock, subject to the pruning rules in the module docstring. `vehicle` only
        affects the cost model (speed, grade penalty) and which stations can start
        a trip; the bike is kept for the whole itinerary. The
        straight-line pre-filter is cheap and a strict superset of what the street
        distance would prune, so it never drops a reachable neighbour.
        """
        self._vehicle = vehicle
        self._adjacency = {}
        max_m = self.settings.max_edge_distance_km * 1000.0
        k = self.settings.max_neighbours_per_station
        limit = self.settings.ride_limit_minutes * 60.0
        max_seconds = limit * self.settings.max_edge_limit_multiple

        # A rider can be leaving a station either because the trip started there
        # or because they reset the clock there.
        sources = {
            sid: snap
            for sid, snap in self.snapshots.items()
            if self.can_rent(sid, vehicle) or self.can_transfer(sid)
        }
        targets = self.returnable_stations()
        for sid, src in sources.items():
            candidates = [
                (src.coord.haversine_m(dst.coord), tid, dst)
                for tid, dst in targets.items()
                if tid != sid
            ]
            candidates = [c for c in candidates if c[0] <= max_m]
            candidates.sort(key=lambda c: c[0])
            edges: list[Edge] = []
            for _, tid, dst in candidates[:k]:
                measured = self.distances.get((sid, tid))
                est = self.cost.estimate_ride(
                    src.coord,
                    dst.coord,
                    src.station.elevation_m,
                    dst.station.elevation_m,
                    vehicle,
                    include_docking=True,
                    measured_m=measured,
                )
                if est.total_seconds > max_seconds:
                    continue
                edges.append(
                    Edge(
                        from_id=sid,
                        to_id=tid,
                        distance_m=est.distance_m,
                        duration_seconds=est.total_seconds,
                        elevation_gain_m=est.elevation_gain_m,
                        overage_cost=self.cost.overage_cost(est.total_seconds),
                        cycling_seconds=est.cycling_seconds,
                        traffic_light_seconds=est.traffic_light_seconds,
                        docking_seconds=est.docking_seconds,
                        buffer_seconds=est.buffer_seconds,
                        risk=self.cost.risk_for(est.total_seconds),
                        measured=measured is not None,
                        limit_seconds=limit,
                    )
                )
            self._adjacency[sid] = edges

    @property
    def vehicle(self) -> VehicleType | None:
        return self._vehicle

    def neighbours(self, station_id: str) -> list[Edge]:
        """Outgoing edges. Safe to call before `build()` -- returns empty."""
        return self._adjacency.get(station_id, [])

    @property
    def edge_count(self) -> int:
        return sum(len(v) for v in self._adjacency.values())

    @property
    def measured_edge_fraction(self) -> float:
        """Share of edges whose distance came from OSRM rather than the detour guess."""
        total = self.edge_count
        if total == 0:
            return 0.0
        return sum(1 for es in self._adjacency.values() for e in es if e.measured) / total
