"""The time-estimation model.

This module answers one question: *how long does it take to ride from A to B?*
Everything else in the project is plumbing around this answer. If this model is
bad, a perfect pathfinder returns perfectly-optimal nonsense.

    total = cycling_time + traffic_light_delay + docking_overhead + safety_buffer

where `cycling_time` accounts for GRADE, not just distance.

Model notes:

* Distance. Station-to-station legs use real bicycle-network distances from OSRM
  (see `avelo.data.osrm`), handed in as `measured_m`. The fallback, and the walk
  legs' estimate, is haversine * a detour factor.

* Grade. grade = elevation_change / distance, SIGNED. Descending is faster, but not
  symmetrically -- you gain less going down than you lose going up, because you
  brake and because there is a terminal velocity. A model that treats +5% and -5%
  as cancelling out underestimates every hilly round trip; this one does not
  (see `grade_speed_factor`).

* Electric vs mechanical. A pedal-assist motor flattens hills; an ICONIC does not.
  The grade penalty is much weaker for e-bikes. àVélo's fleet is all-electric at
  the time of writing, but the model distinguishes the two and the API accepts
  either.

* Sanity anchor. A 2 km flat ride lands at ~7 min of pure cycling on an EFIT and
  ~9 min on an ICONIC, before lights and overheads.

The full derivation and the numbers the model produces are in docs/cost-model.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from avelo.config import Settings, get_settings
from avelo.models import Coord, RiskLevel, VehicleType


@dataclass(frozen=True)
class RideEstimate:
    """A time estimate WITH its breakdown.

    Returning the breakdown rather than a single float is a deliberate choice: the
    API can then explain *why* a route was rejected ("3.2 min of that was traffic
    lights"), and you can unit-test each term in isolation.
    """

    distance_m: float
    elevation_gain_m: float
    cycling_seconds: float
    traffic_light_seconds: float
    docking_seconds: float
    buffer_seconds: float
    # False when elevation was unknown for either endpoint and the ride was costed
    # as flat. Surfaced so a caller can tell "flat" from "we did not know".
    elevation_known: bool = True

    @property
    def total_seconds(self) -> float:
        return (
            self.cycling_seconds
            + self.traffic_light_seconds
            + self.docking_seconds
            + self.buffer_seconds
        )

    @property
    def total_minutes(self) -> float:
        return self.total_seconds / 60.0


class CostModel:
    """Estimates durations. Pure functions of geometry + config -- no I/O, no state.

    Keeping this class free of network calls is what lets you unit-test it exhaustively
    and run your evaluation over thousands of pairs in milliseconds. Real street
    distances (OSRM) enter through the `measured_m` parameters: the caller that
    owns the network does the I/O and hands the number in.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ---------------------------------------------------------------- distance
    def street_distance_m(self, a: Coord, b: Coord, measured_m: float | None = None) -> float:
        """Estimated street-network distance between two points, in metres.

        `measured_m`, when given, is a real routed distance (OSRM) and wins outright.
        Otherwise: haversine * detour_factor. Same signature either way, so the rest
        of the model does not care where the number came from.
        """
        if measured_m is not None and math.isfinite(measured_m) and measured_m >= 0:
            return measured_m
        return a.haversine_m(b) * self.settings.detour_factor

    # ---------------------------------------------------------------- grade
    def _flat_speed_ms(self, vehicle: VehicleType) -> float:
        kmh = (
            self.settings.speed_electric_kmh
            if vehicle.is_electric
            else self.settings.speed_mechanical_kmh
        )
        return kmh / 3.6

    def grade_speed_factor(self, grade: float, vehicle: VehicleType) -> float:
        """Multiplier on flat speed for a given signed grade (rise / run).

        Uphill:   exp(-k * grade). First-order decay, the same family as Tobler's
                  hiking function. k differs by vehicle: a pedal-assist motor adds
                  ~250 W to a rider's ~100 W, so the e-bike curve is much flatter.
        Downhill: 1 + b * |grade|, capped at max_descent_speed. Gravity helps, but
                  you brake, and a shared city bike is not built for 40 km/h. The
                  asymmetry is deliberate: a hill round-trip must cost more than the
                  same distance on the flat, because the time lost climbing is not
                  recovered descending (you cannot ride at -infinity speed).
        """
        if not self.settings.enable_elevation_model:
            return 1.0
        g = max(-self.settings.max_abs_grade, min(self.settings.max_abs_grade, grade))
        if g >= 0:
            k = (
                self.settings.grade_penalty_electric
                if vehicle.is_electric
                else self.settings.grade_penalty_mechanical
            )
            return math.exp(-k * g)
        cap = (self.settings.max_descent_speed_kmh / 3.6) / self._flat_speed_ms(vehicle)
        return min(1.0 + self.settings.descent_bonus * (-g), cap)

    def cycling_seconds(
        self,
        distance_m: float,
        elevation_change_m: float,
        vehicle: VehicleType = VehicleType.EFIT,
    ) -> float:
        """Pure riding time, grade-adjusted.

        `elevation_change_m` is SIGNED: positive means the destination is higher.
        The grade is computed over the *street* distance handed in, which slightly
        understates true grade on winding climbs -- acceptable, and conservative in
        the direction that matters less (it makes climbs look gentler, and the
        detour already inflated the distance).
        """
        if distance_m <= 0:
            return 0.0
        if not math.isfinite(elevation_change_m):
            elevation_change_m = 0.0
        grade = elevation_change_m / distance_m
        speed = self._flat_speed_ms(vehicle) * self.grade_speed_factor(grade, vehicle)
        return distance_m / speed

    # ---------------------------------------------------------------- lights
    def traffic_light_seconds(self, distance_m: float) -> float:
        """Expected delay from intersections along a leg of this length.

        expected lights = distance / spacing; expected delay per light = config.
        Where that per-light number comes from: arriving at a uniformly random moment
        in a cycle of length C with red fraction r, you wait with probability r, and
        the remaining red is uniform on [0, rC], so E[wait] = r * rC/2. For a typical
        C = 60 s, r = 0.5 that is 7.5 s; add ~5 s to stop and re-accelerate a heavy
        bike and the default of 12 s falls out. It is the *expectation* that belongs in
        a routing cost, not the worst case.
        """
        if distance_m <= 0:
            return 0.0
        lights = distance_m / self.settings.traffic_light_spacing_m
        return lights * self.settings.traffic_light_delay_seconds

    # ---------------------------------------------------------------- compose
    def estimate_ride(
        self,
        a: Coord,
        b: Coord,
        elev_a: float | None = None,
        elev_b: float | None = None,
        vehicle: VehicleType = VehicleType.EFIT,
        include_docking: bool = True,
        measured_m: float | None = None,
    ) -> RideEstimate:
        """Compose distance, grade, lights, docking and buffer into one estimate.

        Missing elevation is treated as flat, and the estimate says so via
        `elevation_known=False`. "Flat" is the unbiased choice: the network has as
        much down as up, and a pessimistic default would make every unknown station
        look like a hill and push routes away from it for no reason.
        """
        distance = self.street_distance_m(a, b, measured_m)
        known = elev_a is not None and elev_b is not None
        dz = (elev_b - elev_a) if known and elev_a is not None and elev_b is not None else 0.0
        return RideEstimate(
            distance_m=distance,
            elevation_gain_m=max(dz, 0.0),
            cycling_seconds=self.cycling_seconds(distance, dz, vehicle),
            traffic_light_seconds=self.traffic_light_seconds(distance),
            docking_seconds=self.settings.docking_overhead_seconds if include_docking else 0.0,
            buffer_seconds=self.settings.safety_buffer_seconds,
            elevation_known=known,
        )

    # ---------------------------------------------------------------- walking
    def walk_distance_m(self, a: Coord, b: Coord) -> float:
        return a.haversine_m(b) * self.settings.walk_detour_factor

    def walk_seconds(self, a: Coord, b: Coord) -> float:
        """Walking time for first/last legs. Walkers detour less than cyclists --
        they can cut through parks and go the wrong way down one-way streets."""
        return self.walk_distance_m(a, b) / (self.settings.walking_speed_kmh / 3.6)

    # ---------------------------------------------------------------- money
    def overage_cost(self, ride_seconds: float) -> float:
        """Dollars charged for a single ride leg of this duration.

        Under the limit: $0. Over: minutes over * overage_cost_per_minute.
        ASSUMPTION (config `bill_partial_minutes_as_whole`): PBSC systems bill each
        *started* minute, so 10 min 1 s over costs 11 minutes. Rounding up is the
        conservative choice and the one that matches every receipt we have seen.
        """
        limit = self.settings.ride_limit_minutes * 60.0
        over = ride_seconds - limit
        if over <= 0:
            return 0.0
        minutes = over / 60.0
        if self.settings.bill_partial_minutes_as_whole:
            minutes = math.ceil(minutes - 1e-9)
        return round(minutes * self.settings.overage_cost_per_minute, 2)

    # ---------------------------------------------------------------- risk
    def risk_for(self, ride_seconds: float) -> RiskLevel:
        """Classify one ride leg against the limit using the configured thresholds.

        The estimate is a point prediction; the real duration is a distribution
        around it (wind, a red light streak, a slow rider). A leg at 95% of the limit
        is HIGH risk not because 95% is a big number but because a meaningful share of
        that distribution lies past 100%. The thresholds are where we draw the lines
        on that distribution; the safety buffer already in the estimate shifts its mean.
        """
        limit = self.settings.ride_limit_minutes * 60.0
        if ride_seconds > limit:
            return RiskLevel.INFEASIBLE
        fraction = ride_seconds / limit
        if fraction > self.settings.risk_high_threshold:
            return RiskLevel.HIGH
        if fraction > self.settings.risk_low_threshold:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
