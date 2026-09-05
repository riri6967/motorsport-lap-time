"""Point-mass vehicle model: force balances, limits and their interactions."""

import dataclasses

import numpy as np
import pytest

from engine.conditions import Conditions
from engine.config import VehicleSpec
from engine.units import G
from engine.vehicle import Vehicle

BASE = {
    "name": "TestCar",
    "mass": {"vehicle_kg": 950, "driver_kg": 80, "cg_height_m": 0.30,
             "wheelbase_m": 2.95, "weight_dist_front": 0.45},
    "aero": {"cla": 5.0, "cda": 1.15, "balance_front": 0.44},
    "powertrain": {"max_power_w": 415000, "efficiency": 0.92, "drive": "rwd"},
    "tyres": {"mu_x": 1.55, "mu_y": 1.62, "load_sensitivity": 0.14},
}


def make(**overrides) -> Vehicle:
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in BASE.items()}
    for key, value in overrides.items():
        section, _, field = key.partition(".")
        if field:
            cfg[section][field] = value
        else:
            cfg[section] = value
    return Vehicle(VehicleSpec.from_dict(cfg))


# -- corner speed --------------------------------------------------------
def test_corner_speed_satisfies_the_lateral_force_balance():
    v = make()
    for radius in (25.0, 60.0, 120.0):
        speed = v.corner_speed(1.0 / radius)
        demand = speed ** 2 / radius              # required lateral accel
        available = v.max_lateral_accel(speed)
        assert demand == pytest.approx(available, rel=1e-6)


def test_corner_speed_rises_with_radius():
    v = make()
    radii = np.array([20.0, 40.0, 80.0, 160.0])
    speeds = v.corner_speed(1.0 / radii)
    assert np.all(np.diff(speeds) > 0)


def test_a_straight_is_limited_only_by_top_speed():
    v = make()
    assert v.corner_speed(0.0) == pytest.approx(v.top_speed())


def test_fast_corners_pull_more_g_than_slow_ones():
    """Downforce grows with v^2, so grip is speed-dependent, not a constant."""
    v = make()
    slow = v.corner_speed(1.0 / 30.0)
    fast = v.corner_speed(1.0 / 150.0)
    assert v.max_lateral_accel(fast) > 1.4 * v.max_lateral_accel(slow)


def test_corner_speed_is_vectorised_consistently():
    v = make()
    k = np.array([0.0, 0.005, 0.02, 0.05])
    batch = v.corner_speed(k)
    one_by_one = [v.corner_speed(float(x)) for x in k]
    assert batch == pytest.approx(one_by_one)


# -- top speed -----------------------------------------------------------
def test_top_speed_balances_tractive_effort_against_resistance():
    v = make()
    vmax = v.top_speed()
    drive = v.powertrain.tractive_force(vmax)
    resist = v.aero.drag(vmax) + v.spec.rolling_resistance * v.normal_load(vmax)
    assert drive == pytest.approx(resist, rel=1e-6)


def test_more_drag_means_less_top_speed():
    assert make(**{"aero.cda": 1.6}).top_speed() < make().top_speed()


def test_v_max_override_caps_top_speed():
    v = make(v_max_ms=70.0)
    assert v.top_speed() == pytest.approx(70.0)


# -- friction ellipse coupling -------------------------------------------
def test_cornering_eats_into_acceleration_where_traction_binds():
    v = make()
    speed = 15.0                      # slow enough that grip, not power, rules
    ay_max = v.max_lateral_accel(speed)
    free = v.max_long_accel(speed, 0.0)
    half = v.max_long_accel(speed, 0.6 * ay_max)
    limit = v.max_long_accel(speed, ay_max)
    assert free > half > limit
    # At the lateral limit nothing is left to fight drag, so the car slows.
    assert limit < 0.0


def test_moderate_cornering_is_free_while_power_limited():
    """Why a driver can be full throttle through a fast corner.

    At 40 m/s this car has more grip than engine, so spending some of it
    sideways costs no acceleration at all until the surplus runs out.
    """
    v = make()
    speed = 40.0
    ay_max = v.max_lateral_accel(speed)
    free = v.max_long_accel(speed, 0.0)
    assert v.max_long_accel(speed, 0.6 * ay_max) == pytest.approx(free, rel=1e-9)
    assert v.max_long_accel(speed, 0.98 * ay_max) < free


def test_cornering_eats_into_braking():
    v = make()
    speed = 70.0
    ay_max = v.max_lateral_accel(speed)
    assert v.max_long_decel(speed, 0.0) > v.max_long_decel(speed, 0.8 * ay_max)


def test_at_the_lateral_limit_only_drag_still_slows_the_car():
    v = make()
    speed = 70.0
    ay_max = v.max_lateral_accel(speed)
    drag_only = (v.aero.drag(speed)
                 + v.spec.rolling_resistance * v.normal_load(speed)) / v.mass
    assert v.max_long_decel(speed, ay_max) == pytest.approx(drag_only, rel=1e-9)


# -- longitudinal --------------------------------------------------------
def test_braking_outperforms_acceleration():
    """Every wheel brakes, only two drive, and drag helps in one direction."""
    v = make()
    assert v.max_long_decel(60.0) > v.max_long_accel(60.0)


def test_acceleration_falls_away_with_speed():
    v = make()
    a = v.max_long_accel(np.array([20.0, 40.0, 60.0, 75.0]))
    assert np.all(np.diff(a) < 0)


def test_all_wheel_drive_beats_rear_drive_where_traction_binds():
    rwd, awd = make(), make(**{"powertrain.drive": "awd"})
    assert awd.max_long_accel(15.0) > rwd.max_long_accel(15.0)


def test_drive_layout_is_irrelevant_once_power_limited():
    rwd, awd = make(), make(**{"powertrain.drive": "awd"})
    assert awd.max_long_accel(75.0) == pytest.approx(rwd.max_long_accel(75.0), rel=1e-6)


def test_load_transfer_helps_a_rear_drive_car_off_the_line():
    """Raising the CG shifts more load rearward under power."""
    low = make(**{"mass.cg_height_m": 0.20})
    high = make(**{"mass.cg_height_m": 0.40})
    assert high.max_long_accel(15.0) > low.max_long_accel(15.0)


def test_load_transfer_hurts_a_front_drive_car_off_the_line():
    low = make(**{"mass.cg_height_m": 0.20, "powertrain.drive": "fwd"})
    high = make(**{"mass.cg_height_m": 0.40, "powertrain.drive": "fwd"})
    assert high.max_long_accel(15.0) < low.max_long_accel(15.0)


def test_brake_force_cap_limits_deceleration():
    free = make()
    capped = make(brakes={"max_force_n": 15000.0})
    assert capped.max_long_decel(70.0) < free.max_long_decel(70.0)


# -- mass, grip and weather ----------------------------------------------
def test_fuel_load_costs_acceleration_and_corner_speed():
    light = make().with_fuel(0.0)
    heavy = make().with_fuel(80.0)
    assert heavy.mass > light.mass
    assert heavy.max_long_accel(50.0) < light.max_long_accel(50.0)
    assert heavy.corner_speed(1.0 / 60.0) < light.corner_speed(1.0 / 60.0)


def test_rain_costs_corner_speed():
    dry = make()
    wet = dry.with_conditions(Conditions(wetness=1.0, track_temp_c=32.0))
    assert wet.corner_speed(1.0 / 60.0) < dry.corner_speed(1.0 / 60.0)
    assert wet.max_long_decel(60.0) < dry.max_long_decel(60.0)


def test_worn_tyres_cost_corner_speed():
    v = make()
    assert v.corner_speed(1.0 / 60.0, grip=0.9) < v.corner_speed(1.0 / 60.0, grip=1.0)


def test_thin_air_costs_downforce_and_gains_top_speed():
    """A hot day: less drag to fight, but less grip to lean on."""
    cool = make().with_conditions(Conditions(air_temp_c=10.0, track_temp_c=32.0))
    hot = make().with_conditions(Conditions(air_temp_c=38.0, track_temp_c=32.0))
    assert hot.top_speed() > cool.top_speed()
    assert hot.corner_speed(1.0 / 100.0) < cool.corner_speed(1.0 / 100.0)


def test_bop_power_cut_slows_the_car_down_the_straight():
    full = make()
    pegged = full.with_power_scale(0.90)
    assert pegged.top_speed() < full.top_speed()
    assert pegged.max_long_accel(60.0) < full.max_long_accel(60.0)


# -- plausibility guards --------------------------------------------------
def test_performance_stays_in_the_range_a_prototype_actually_occupies():
    """A loose envelope check, so a broken change shows up as absurd physics."""
    v = make()
    assert 70.0 < v.top_speed() < 100.0                       # 250-360 km/h
    assert 1.4 < v.max_lateral_accel(30.0) / G < 2.6          # slow corner
    assert 3.0 < v.max_lateral_accel(80.0) / G < 5.5          # fast corner
    assert 0.7 < v.max_long_accel(20.0) / G < 1.6             # traction limited
    assert 2.5 < v.max_long_decel(70.0) / G < 6.0             # heavy braking
