"""Domain types.

Design note worth internalising: these are *immutable value objects* (`frozen=True`).
Routing algorithms mutate a lot of state; if the objects flowing through them can be
edited in place, a subtle bug in one branch corrupts data another branch already read.
Frozen models make a whole category of bug impossible, and let you use stations as
dict keys and set members.
"""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

EARTH_RADIUS_M = 6_371_000.0


class Coord(BaseModel):
    """A WGS84 point."""

    model_config = ConfigDict(frozen=True)

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)

    def haversine_m(self, other: Coord) -> float:
        """Great-circle distance in metres.

        This is straight-line ("as the crow flies") distance. Real cycling distance
        is longer -- see `Settings.detour_factor`, and eventually the OSRM adapter.
        """
        p1, p2 = math.radians(self.lat), math.radians(other.lat)
        dphi = p2 - p1
        dlambda = math.radians(other.lon - self.lon)
        a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
        return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class VehicleType(StrEnum):
    """àVélo vehicle types, from the live `vehicle_types` feed.

    This distinction matters more than it looks: an EFIT (electric) climbs the
    Côte de la Montagne at a very different speed than an ICONIC does. A model that
    ignores it will be wrong in exactly the cases this project exists to solve.
    """

    EFIT = "EFIT"  # electric
    BOOST = "BOOST"  # electric-assist
    ICONIC = "ICONIC"  # human-powered
    FIT = "FIT"  # human-powered

    @property
    def is_electric(self) -> bool:
        return self in (VehicleType.EFIT, VehicleType.BOOST)


class Station(BaseModel):
    """Static station facts, from the `station_information` feed."""

    model_config = ConfigDict(frozen=True)

    station_id: str
    name: str
    coord: Coord
    capacity: int
    # Populated lazily by the elevation client. `None` means "not looked up yet",
    # which is deliberately different from 0.0 metres (sea level).
    elevation_m: float | None = None


class StationStatus(BaseModel):
    """Live availability, from the `station_status` feed."""

    model_config = ConfigDict(frozen=True)

    station_id: str
    num_bikes_available: int
    num_docks_available: int
    bikes_by_type: dict[VehicleType, int] = Field(default_factory=dict)
    is_renting: bool
    is_returning: bool
    last_reported: int


class StationSnapshot(BaseModel):
    """A station joined with its live status: what the router actually consumes.

    Keeping this separate from `Station` means the routing layer never has to ask
    "is this data fresh?" -- it received a snapshot, so it is as fresh as the fetch.
    """

    model_config = ConfigDict(frozen=True)

    station: Station
    status: StationStatus

    @property
    def station_id(self) -> str:
        return self.station.station_id

    @property
    def coord(self) -> Coord:
        return self.station.coord

    def can_rent(self, min_bikes: int = 1, vehicle: VehicleType | None = None) -> bool:
        """Can I pick a bike up here right now?"""
        if not self.status.is_renting:
            return False
        # The live feed lists every vehicle type at every station, with count 0 when
        # none are present. A type that is *absent* from the list is therefore
        # "feed did not say", not "zero" -- fall back to the total in that case.
        if vehicle is not None and vehicle in self.status.bikes_by_type:
            return self.status.bikes_by_type[vehicle] >= min_bikes
        return self.status.num_bikes_available >= min_bikes

    def can_return(self, min_docks: int = 1) -> bool:
        """Can I dock a bike here right now?"""
        return self.status.is_returning and self.status.num_docks_available >= min_docks


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    INFEASIBLE = "INFEASIBLE"


class LegMode(StrEnum):
    WALK = "WALK"
    RIDE = "RIDE"


class Leg(BaseModel):
    """One continuous movement: a walk to/from a station, or one metered bike ride.

    The key invariant: a RIDE leg is exactly one billing period. The clock starts
    when you unlock and stops when you dock. The rider keeps the same bike across
    legs -- docking and re-unlocking at a station resets the clock -- which is why
    the constraint applies per-leg and not to the itinerary as a whole.
    """

    model_config = ConfigDict(frozen=True)

    mode: LegMode
    from_name: str
    to_name: str
    from_coord: Coord
    to_coord: Coord
    distance_m: float
    duration_seconds: float
    # Breakdown, so the API can explain itself rather than emit one opaque number.
    cycling_seconds: float = 0.0
    traffic_light_seconds: float = 0.0
    docking_seconds: float = 0.0
    elevation_gain_m: float = 0.0
    risk: RiskLevel = RiskLevel.LOW
    overage_cost: float = 0.0
    # Street polyline for drawing on a map. Optional: it is decoration, fetched
    # only when asked for, and never a reason for a routing request to fail.
    geometry: list[Coord] | None = None

    @property
    def duration_minutes(self) -> float:
        return self.duration_seconds / 60.0


class Itinerary(BaseModel):
    """A complete door-to-door plan."""

    model_config = ConfigDict(frozen=True)

    legs: list[Leg]
    total_seconds: float
    total_distance_m: float
    total_cost: float
    overall_risk: RiskLevel
    # Number of clock resets (dock + re-unlock the same bike) = ride legs - 1.
    num_transfers: int

    @property
    def total_minutes(self) -> float:
        return self.total_seconds / 60.0
