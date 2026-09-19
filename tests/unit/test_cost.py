"""Specification for `CostModel`.

These test PROPERTIES, not exact numbers -- deliberately. The exact shape of the
grade model is a design choice; what is not negotiable is that uphill is slower
than downhill and that an e-bike suffers less on a climb. Property-based specs
let the model be tuned without rewriting the suite every time.
"""

from __future__ import annotations

import math

import pytest

from avelo.config import Settings
from avelo.models import Coord, RiskLevel, VehicleType
from avelo.routing.cost import CostModel

FLAT_A = Coord(lat=46.8139, lon=-71.2080)
FLAT_B = Coord(lat=46.8139, lon=-71.1820)  # ~2 km due east


@pytest.fixture
def model(settings: Settings) -> CostModel:
    return CostModel(settings=settings)


# --- street distance -------------------------------------------------


def test_street_distance_exceeds_straight_line(model: CostModel) -> None:
    straight = FLAT_A.haversine_m(FLAT_B)
    assert model.street_distance_m(FLAT_A, FLAT_B) > straight


def test_street_distance_is_zero_for_identical_points(model: CostModel) -> None:
    assert model.street_distance_m(FLAT_A, FLAT_A) == pytest.approx(0.0)


def test_street_distance_is_symmetric(model: CostModel) -> None:
    """Not physically true on one-way streets, but it must hold for your estimator --
    an asymmetric distance function would silently break Dijkstra's assumptions."""
    assert model.street_distance_m(FLAT_A, FLAT_B) == pytest.approx(
        model.street_distance_m(FLAT_B, FLAT_A)
    )


# --- grade-adjusted cycling time -------------------------------------


def test_uphill_slower_than_flat_slower_than_downhill(model: CostModel) -> None:
    up = model.cycling_seconds(2000, +90, VehicleType.ICONIC)
    flat = model.cycling_seconds(2000, 0, VehicleType.ICONIC)
    down = model.cycling_seconds(2000, -90, VehicleType.ICONIC)
    assert up > flat > down


def test_electric_bike_less_penalised_by_climb(model: CostModel) -> None:
    """The whole point of distinguishing vehicle types: measure the PENALTY (time
    above flat), not the absolute time, so a faster base speed cannot fake a pass."""
    e_penalty = model.cycling_seconds(2000, +90, VehicleType.EFIT) - model.cycling_seconds(
        2000, 0, VehicleType.EFIT
    )
    m_penalty = model.cycling_seconds(2000, +90, VehicleType.ICONIC) - model.cycling_seconds(
        2000, 0, VehicleType.ICONIC
    )
    assert e_penalty < m_penalty


def test_climb_is_not_cancelled_by_equal_descent(model: CostModel) -> None:
    """A round trip over a hill must cost MORE than the same distance on the flat.
    If your model is linear in signed grade, this test fails -- and it should, because
    that model is wrong. This is the subtlest of the cost-model requirements."""
    over_hill = model.cycling_seconds(2000, +90, VehicleType.ICONIC) + model.cycling_seconds(
        2000, -90, VehicleType.ICONIC
    )
    on_flat = 2 * model.cycling_seconds(2000, 0, VehicleType.ICONIC)
    assert over_hill > on_flat


def test_zero_distance_is_zero_time(model: CostModel) -> None:
    assert model.cycling_seconds(0, 0, VehicleType.EFIT) == pytest.approx(0.0)


@pytest.mark.parametrize("dist,elev", [(1.0, 500.0), (100000.0, -5000.0), (500.0, 0.0)])
def test_cycling_time_always_finite_and_positive(
    model: CostModel, dist: float, elev: float
) -> None:
    """Robustness against absurd inputs. A cliff-like grade must not produce a negative
    time, a division by zero, or infinity -- any of those will corrupt the priority
    queue in your planner and give you a bug that takes hours to find."""
    t = model.cycling_seconds(dist, elev, VehicleType.ICONIC)
    assert math.isfinite(t) and t > 0


def test_flat_ride_lands_in_a_sane_range(model: CostModel) -> None:
    """Sanity anchor: 2 km on the flat is roughly 7-10 min on a city bike."""
    minutes = model.cycling_seconds(2000, 0, VehicleType.EFIT) / 60
    assert 5.0 < minutes < 12.0, f"got {minutes:.1f} min for a flat 2 km -- model is off"


# --- traffic lights --------------------------------------------------


def test_traffic_light_delay_grows_with_distance(model: CostModel) -> None:
    assert model.traffic_light_seconds(4000) > model.traffic_light_seconds(1000)


def test_no_traffic_light_delay_over_zero_distance(model: CostModel) -> None:
    assert model.traffic_light_seconds(0) == pytest.approx(0.0)


# --- composition -----------------------------------------------------


def test_estimate_breakdown_sums_to_total(model: CostModel) -> None:
    """The breakdown must be internally consistent, or your API will lie to users."""
    est = model.estimate_ride(FLAT_A, FLAT_B, 10.0, 10.0, VehicleType.EFIT)
    assert est.total_seconds == pytest.approx(
        est.cycling_seconds + est.traffic_light_seconds + est.docking_seconds + est.buffer_seconds
    )


def test_estimate_records_elevation_gain(model: CostModel) -> None:
    est = model.estimate_ride(FLAT_A, FLAT_B, 10.0, 100.0, VehicleType.ICONIC)
    assert est.elevation_gain_m == pytest.approx(90.0)


def test_missing_elevation_treated_as_flat(model: CostModel) -> None:
    """Unknown elevation must degrade gracefully, not crash. But think about whether
    'assume flat' is the right default, or whether it should be pessimistic."""
    est = model.estimate_ride(FLAT_A, FLAT_B, None, None, VehicleType.EFIT)
    assert math.isfinite(est.total_seconds) and est.total_seconds > 0


def test_docking_overhead_can_be_excluded(model: CostModel) -> None:
    """The final leg has no transfer, so it should not pay the docking cost."""
    with_dock = model.estimate_ride(FLAT_A, FLAT_B, 10.0, 10.0, include_docking=True)
    without = model.estimate_ride(FLAT_A, FLAT_B, 10.0, 10.0, include_docking=False)
    assert with_dock.total_seconds > without.total_seconds


# --- walking ---------------------------------------------------------


def test_walking_is_slower_than_cycling(model: CostModel) -> None:
    ride = model.estimate_ride(FLAT_A, FLAT_B, 10.0, 10.0).cycling_seconds
    assert model.walk_seconds(FLAT_A, FLAT_B) > ride


# --- money -----------------------------------------------------------


def test_no_charge_under_the_limit(model: CostModel, settings: Settings) -> None:
    assert model.overage_cost(settings.ride_limit_minutes * 60 - 1) == pytest.approx(0.0)


def test_charge_scales_with_minutes_over(model: CostModel, settings: Settings) -> None:
    limit = settings.ride_limit_minutes * 60
    five_over = model.overage_cost(limit + 300)
    ten_over = model.overage_cost(limit + 600)
    assert five_over > 0
    assert ten_over > five_over


def test_charge_matches_published_rate(model: CostModel, settings: Settings) -> None:
    """VERIFIED against the live system_pricing_plans feed: $0.30/min past 30 min.
    10 minutes over = $3.00. Allow slack for a round-up-partial-minutes policy."""
    cost = model.overage_cost(settings.ride_limit_minutes * 60 + 600)
    assert 2.90 <= cost <= 3.35, f"expected ~$3.00 for 10 min over, got ${cost:.2f}"


# --- risk ------------------------------------------------------------


def test_risk_increases_with_duration(model: CostModel, settings: Settings) -> None:
    limit = settings.ride_limit_minutes * 60
    order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.INFEASIBLE]
    levels = [model.risk_for(f * limit) for f in (0.4, 0.85, 0.98, 1.5)]
    ranks = [order.index(x) for x in levels]
    assert ranks == sorted(ranks), f"risk must be monotonic in duration, got {levels}"


def test_over_limit_is_infeasible(model: CostModel, settings: Settings) -> None:
    assert model.risk_for(settings.ride_limit_minutes * 60 * 1.5) == RiskLevel.INFEASIBLE
