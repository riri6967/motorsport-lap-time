"""Quasi-steady-state solver: equilibria, limits and grid convergence."""

import numpy as np
import pytest

from engine.conditions import Conditions
from engine.config import VehicleSpec
from engine.qss import AccelerationTable, solve_lap
from engine.track import Track
from engine.vehicle import Vehicle

SPEC = {
    "name": "TestCar",
    "mass": {"vehicle_kg": 950, "driver_kg": 80, "cg_height_m": 0.30,
             "wheelbase_m": 2.95, "weight_dist_front": 0.45},
    "aero": {"cla": 5.0, "cda": 1.15, "balance_front": 0.44},
    "powertrain": {"max_power_w": 415000, "efficiency": 0.92, "drive": "rwd"},
    "tyres": {"mu_x": 1.55, "mu_y": 1.62, "load_sensitivity": 0.14},
}


@pytest.fixture
def car() -> Vehicle:
    return Vehicle(VehicleSpec.from_dict(SPEC))


@pytest.fixture
def oval() -> Track:
    return Track.from_segments("Test Oval", [
        {"type": "straight", "length": 500},
        {"type": "arc", "radius": 100, "angle": 180},
        {"type": "straight", "length": 500},
        {"type": "arc", "radius": 100, "angle": 180},
    ], ds=2.0, width=12.0)


def skidpad(radius: float = 100.0, ds: float = 2.0) -> Track:
    return Track.from_segments(
        "Skidpad", [{"type": "arc", "radius": radius, "angle": 360}],
        ds=ds, width=15.0)


# -- analytical cases ----------------------------------------------------
def test_skidpad_settles_at_a_true_equilibrium(car):
    """A constant-radius lap must come out uniform, with zero net acceleration."""
    result = solve_lap(car, skidpad(), max_sweeps=40)
    assert result.converged
    assert result.v.ptp() == pytest.approx(0.0, abs=1e-6)
    assert np.abs(result.ax).max() == pytest.approx(0.0, abs=1e-6)


def test_skidpad_lap_time_is_circumference_over_speed(car):
    radius = 100.0
    result = solve_lap(car, skidpad(radius), max_sweeps=40)
    assert result.lap_time == pytest.approx(
        2 * np.pi * radius / result.v.mean(), rel=1e-6)


def test_sustained_cornering_sits_below_the_pure_lateral_limit(car):
    """Holding a corner at the limit still costs grip to beat drag.

    A car cannot simultaneously use 100% of its grip sideways and overcome
    aerodynamic drag, so the sustainable skidpad speed is a little under the
    textbook mu-based figure. The gap is small but it is real physics, not
    solver error.
    """
    radius = 100.0
    result = solve_lap(car, skidpad(radius), max_sweeps=40)
    pure = car.corner_speed(1.0 / radius)
    assert result.v.mean() < pure
    assert result.v.mean() > 0.97 * pure
    # At the settled speed the car is exactly balancing drag.
    assert car.max_long_accel(result.v.mean(),
                              result.v.mean() ** 2 / radius) == pytest.approx(0.0, abs=5e-3)


def test_result_is_insensitive_to_sample_spacing(car):
    """Grid convergence: halving the step must not move the answer much."""
    coarse = solve_lap(car, skidpad(ds=4.0), max_sweeps=40).lap_time
    fine = solve_lap(car, skidpad(ds=1.0), max_sweeps=40).lap_time
    assert coarse == pytest.approx(fine, rel=2e-3)


def test_oval_lap_time_is_insensitive_to_sample_spacing(car):
    def lap(ds):
        track = Track.from_segments("Oval", [
            {"type": "straight", "length": 500},
            {"type": "arc", "radius": 100, "angle": 180},
            {"type": "straight", "length": 500},
            {"type": "arc", "radius": 100, "angle": 180},
        ], ds=ds, width=12.0)
        return solve_lap(car, track).lap_time
    assert lap(4.0) == pytest.approx(lap(1.0), rel=3e-3)


# -- structural invariants -----------------------------------------------
def test_lap_time_is_the_sum_of_the_step_times(car, oval):
    result = solve_lap(car, oval)
    assert result.lap_time == pytest.approx(float(result.dt.sum()))
    assert np.all(result.dt > 0)
    assert np.all(result.v > 0)


def test_solver_converges_on_a_normal_lap(car, oval):
    result = solve_lap(car, oval)
    assert result.converged
    assert result.sweeps <= 6


def test_speed_never_exceeds_the_cornering_limit(car, oval):
    result = solve_lap(car, oval)
    limit = car.corner_speed(result.curvature)
    assert np.all(result.v <= limit + 1e-6)


def test_distance_matches_the_track_length(car, oval):
    result = solve_lap(car, oval)
    assert result.distance == pytest.approx(oval.length, rel=1e-3)


def test_sector_times_sum_to_the_lap_time(car):
    track = Track.from_segments("Sectored", [
        {"type": "straight", "length": 400},
        {"type": "arc", "radius": 80, "angle": 180},
        {"type": "straight", "length": 400},
        {"type": "arc", "radius": 80, "angle": 180},
    ], ds=2.0, width=12.0, sector_starts_m=(0.0, 500.0, 1000.0))
    result = solve_lap(car, track)
    assert len(result.sector_times) == 3
    assert sum(result.sector_times) == pytest.approx(result.lap_time, rel=1e-9)


# -- the levers that change a lap time -----------------------------------
def test_more_grip_means_a_quicker_lap(car, oval):
    assert solve_lap(car, oval, grip=1.05).lap_time < solve_lap(car, oval).lap_time


def test_worn_tyres_cost_lap_time(car, oval):
    assert solve_lap(car, oval, grip=0.90).lap_time > solve_lap(car, oval).lap_time


def test_a_power_cut_costs_lap_time(car, oval):
    slower = car.with_power_scale(0.9)
    assert solve_lap(slower, oval).lap_time > solve_lap(car, oval).lap_time


def test_rain_costs_lap_time(car, oval):
    wet = car.with_conditions(Conditions(wetness=1.0, track_temp_c=32.0))
    assert solve_lap(wet, oval).lap_time > solve_lap(car, oval).lap_time


def test_fuel_load_costs_lap_time(car, oval):
    heavy = solve_lap(car.with_fuel(80.0), oval).lap_time
    light = solve_lap(car.with_fuel(0.0), oval).lap_time
    assert heavy > light


# -- flags, safety cars and blocked track --------------------------------
def test_a_speed_cap_is_respected_and_costs_time(car, oval):
    cap = np.full(len(oval), 1e3)
    zone = (oval.s > 100) & (oval.s < 300)
    cap[zone] = 30.0
    capped = solve_lap(car, oval, speed_limit=cap)
    assert np.all(capped.v[zone] <= 30.0 + 1e-6)
    assert capped.lap_time > solve_lap(car, oval).lap_time


def test_the_car_brakes_into_a_yellow_zone_rather_than_teleporting(car, oval):
    """The cap must produce a physical profile, not a discontinuity."""
    cap = np.full(len(oval), 1e3)
    zone = (oval.s > 200) & (oval.s < 400)
    cap[zone] = 25.0
    result = solve_lap(car, oval, speed_limit=cap)
    # The car accelerates away from the previous corner, then brakes: the
    # profile must contain a braking phase that arrives at the cap, not a
    # step change at the boundary.
    braking = (oval.s > 150) & (oval.s <= 200)
    assert np.all(np.diff(result.v[braking]) < 1e-6)
    assert result.v[np.argmax(zone)] <= 25.0 + 1e-6
    assert result.ax[braking].min() < -5.0
    # And it is climbing again once clear.
    exit_zone = (oval.s > 400) & (oval.s < 480)
    assert np.all(np.diff(result.v[exit_zone]) > -1e-6)


def test_a_full_course_yellow_slows_the_whole_lap(car, oval):
    fcy = solve_lap(car, oval, speed_limit=np.full(len(oval), 22.0))
    assert fcy.lap_time == pytest.approx(oval.length / 22.0, rel=0.02)


# -- lines ----------------------------------------------------------------
def test_an_offset_line_changes_the_lap(car, oval):
    offset = np.full(len(oval), 2.0)
    shifted = solve_lap(car, oval, offset=offset)
    assert shifted.lap_time != pytest.approx(solve_lap(car, oval).lap_time)
    assert shifted.distance != pytest.approx(oval.length, rel=1e-4)


def test_the_acceleration_table_matches_the_vehicle_model(car):
    """The interpolated envelope must not drift from the real one."""
    table = AccelerationTable(car, grip=1.0)
    rng = np.random.default_rng(0)
    for v_test in rng.uniform(5.0, table.v_max * 0.98, 40):
        ay_max = float(car.max_lateral_accel(v_test))
        for u in (0.0, 0.35, 0.7, 0.95, 1.0):
            curvature = (u * ay_max) / v_test ** 2
            assert table.accel_at(v_test, curvature) == pytest.approx(
                float(car.max_long_accel(v_test, u * ay_max)), abs=5e-3)
            assert table.decel_at(v_test, curvature) == pytest.approx(
                float(car.max_long_decel(v_test, u * ay_max)), abs=5e-3)
