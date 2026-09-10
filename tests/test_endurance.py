"""Multi-class endurance race: traffic, pit stops, neutralisation, weather."""

import math

import pytest

from engine.config import VehicleSpec
from engine.track import Track
from engine.vehicle import Vehicle
from scenarios.endurance import (Entry, Neutralisation, PitRules,
                                 day_night_weather, simulate_race)

SPEC = {
    "name": "TestCar",
    "mass": {"vehicle_kg": 800, "driver_kg": 80, "cg_height_m": 0.30,
             "wheelbase_m": 2.95, "weight_dist_front": 0.45,
             "fuel_capacity_kg": 1.0},
    "aero": {"cla": 3.0, "cda": 0.9, "balance_front": 0.44},
    "powertrain": {"max_power_w": 300000, "drive": "rwd"},
    "tyres": {"mu_x": 1.5, "mu_y": 1.55, "load_sensitivity": 0.14,
             "wear_model": "distance", "grip_loss_per_lap": 0.05,
             "reference_lap_m": 300.0},
}


@pytest.fixture
def track() -> Track:
    return Track.from_segments("Short Oval", [
        {"type": "straight", "length": 100},
        {"type": "arc", "radius": 40, "angle": 180},
        {"type": "straight", "length": 100},
        {"type": "arc", "radius": 40, "angle": 180},
    ], ds=5.0, width=12.0)


def make_entry(number: str, pace_factor: float = 1.0, **pit_kwargs) -> Entry:
    vehicle = Vehicle(VehicleSpec.from_dict(SPEC))
    return Entry(number=number, class_name="Test", vehicle=vehicle,
                pace_factor=pace_factor, pit=PitRules(**pit_kwargs))


def test_traffic_loss_is_zero_alone_and_positive_with_company(track):
    """Nothing to pass, nothing to lose; add a car of different pace and it costs.

    Both cars pay something on average -- the model charges the car doing the
    catching-up (``pass_cost_s``) and the one it catches (``passed_cost_s``)
    rather than only one of them, see scenarios/endurance.py.
    """
    solo = simulate_race(track, [make_entry("A")], duration_s=300.0)
    assert solo.cars[0].traffic_loss_s == pytest.approx(0.0)

    paired = simulate_race(track, [make_entry("A"), make_entry("B", 1.15)],
                           duration_s=300.0)
    assert all(c.traffic_loss_s > 0.0 for c in paired.cars)


def test_pit_stops_happen_when_the_tank_runs_low(track):
    """A 1 kg tank against an ~0.08 kg/lap burn forces several stops."""
    entry = make_entry("P", tyre_stints=1, stationary_s=5.0,
                       pit_lane_loss_s=5.0)
    result = simulate_race(track, [entry], duration_s=900.0)
    car = result.cars[0]
    assert car.pit_stops > 1
    assert car.pit_time_s > 0.0
    assert car.fuel_kg <= entry.vehicle.spec.mass.fuel_capacity_kg


def test_neutralisation_floors_the_lap_at_the_fcy_speed(track):
    """A full-course yellow covering the whole race caps every lap time."""
    entry = make_entry("N")
    fcy = Neutralisation(start_s=0.0, end_s=1e9, speed_ms=20.0)
    result = simulate_race(track, [entry], duration_s=300.0,
                           neutralisations=[fcy])
    car = result.cars[0]
    expected = track.length / fcy.speed_ms
    assert all(t == pytest.approx(expected, rel=1e-6) for t in car.lap_times)
    assert car.fcy_laps == car.laps


def test_day_night_weather_peaks_at_15h_and_troughs_at_03h():
    weather = day_night_weather(start_hour=15.0, day_track_c=38.0,
                                night_track_c=16.0)
    peak = weather(0.0)
    trough = weather(12.0 * 3600.0)
    assert peak.track_temp_c == pytest.approx(38.0, abs=1e-6)
    assert trough.track_temp_c == pytest.approx(16.0, abs=1e-6)


def test_neutralisation_covers_is_a_half_open_interval():
    n = Neutralisation(start_s=10.0, end_s=20.0)
    assert n.covers(10.0)
    assert n.covers(19.999)
    assert not n.covers(20.0)
    assert not n.covers(9.999)
