"""Storing and reloading solved racing lines."""

import numpy as np
import pytest

from engine.config import VehicleSpec
from engine.lines import (StaleLineError, cached_racing_line, find_line,
                          line_path, load_line, save_line, track_fingerprint)
from engine.track import Track
from engine.vehicle import Vehicle

SPEC = {
    "name": "TestCar",
    "mass": {"vehicle_kg": 950, "driver_kg": 80},
    "aero": {"cla": 5.0, "cda": 1.15},
    "powertrain": {"max_power_w": 415000},
    "tyres": {"mu_x": 1.55, "mu_y": 1.62, "load_sensitivity": 0.14},
}


def oval(ds: float = 5.0, width: float = 14.0) -> Track:
    return Track.from_segments("Oval", [
        {"type": "straight", "length": 400},
        {"type": "arc", "radius": 90, "angle": 180},
        {"type": "straight", "length": 400},
        {"type": "arc", "radius": 90, "angle": 180},
    ], ds=ds, width=width)


@pytest.fixture
def car() -> Vehicle:
    return Vehicle(VehicleSpec.from_dict(SPEC))


# -- fingerprints ---------------------------------------------------------
def test_the_same_geometry_fingerprints_the_same():
    assert track_fingerprint(oval()) == track_fingerprint(oval())


def test_different_sampling_fingerprints_differently():
    """A line solved at 5 m spacing has nothing to say about 2 m spacing."""
    assert track_fingerprint(oval(ds=5.0)) != track_fingerprint(oval(ds=2.0))


def test_different_width_fingerprints_differently():
    """Track width sets the bounds, so it changes what the line may do."""
    assert track_fingerprint(oval(width=14.0)) != track_fingerprint(oval(width=10.0))


# -- round trip -----------------------------------------------------------
def test_a_saved_line_comes_back_unchanged(tmp_path):
    track = oval()
    offset = np.linspace(-2.0, 2.0, len(track))
    path = save_line(tmp_path / "line.npz", offset, track,
                     vehicle_name="TestCar", lap_time=31.5, method="unit test")
    loaded, meta = load_line(path, track)
    assert loaded == pytest.approx(offset)
    assert meta["vehicle"] == "TestCar"
    assert meta["lap_time_s"] == pytest.approx(31.5)
    assert meta["track"] == "Oval"


def test_loading_against_different_geometry_is_refused(tmp_path):
    track = oval(ds=5.0)
    path = save_line(tmp_path / "line.npz", np.zeros(len(track)), track)
    with pytest.raises(StaleLineError, match="different geometry"):
        load_line(path, oval(ds=2.5))


def test_a_mismatch_can_be_reported_instead_of_raised(tmp_path):
    track = oval(ds=5.0)
    offset = np.zeros(len(track))
    path = save_line(tmp_path / "line.npz", offset, track)
    same_size = oval(ds=5.0, width=10.0)          # same samples, new bounds
    loaded, meta = load_line(path, same_size, strict=False)
    assert loaded == pytest.approx(offset)
    assert "different geometry" in meta["mismatch"]


def test_a_length_mismatch_is_always_refused(tmp_path):
    track = oval(ds=5.0)
    path = save_line(tmp_path / "line.npz", np.zeros(len(track)), track)
    with pytest.raises(StaleLineError):
        load_line(path, oval(ds=2.5), strict=False)


# -- cache layout ---------------------------------------------------------
def test_the_path_separates_cars_grips_and_geometry(tmp_path):
    track = oval()
    base = line_path(tmp_path, track, "LMP2")
    assert line_path(tmp_path, track, "GT3") != base
    assert line_path(tmp_path, track, "LMP2", grip=0.9) != base
    assert line_path(tmp_path, oval(ds=2.0), "LMP2") != base


def test_nothing_is_found_before_anything_is_stored(tmp_path):
    assert find_line(tmp_path, oval(), "LMP2") is None


# -- the cached solve -----------------------------------------------------
def test_the_second_solve_reuses_the_first(tmp_path, car):
    track = oval()
    first, source = cached_racing_line(
        car, track, tmp_path, schedule=((6, 20),), sweep_evaluations=60)
    assert source == "solved"
    assert find_line(tmp_path, track, car.name) is not None

    second, source = cached_racing_line(car, track, tmp_path)
    assert source == "cache"
    assert second.offset == pytest.approx(first.offset)
    assert second.lap_time == pytest.approx(first.lap_time, rel=1e-12)


def test_force_re_solves_rather_than_reusing(tmp_path, car):
    track = oval()
    cached_racing_line(car, track, tmp_path, schedule=((6, 20),),
                       sweep_evaluations=60)
    _, source = cached_racing_line(car, track, tmp_path, force=True,
                                   schedule=((6, 20),), sweep_evaluations=60)
    assert source == "solved"


def test_a_reused_line_is_re_solved_through_current_physics(tmp_path, car):
    """The search is skipped; the lap time is not taken on trust.

    This is what makes the calibration workflow honest. Variants of a car
    share a cache entry, because the cache is keyed on the car's *name* --
    deliberately, so that a parameter sweep runs every variant down the same
    line and a change in lap time can be attributed to the car rather than
    to the search wandering somewhere else. But the lap time itself is
    always recomputed, so it always reflects the car actually being asked
    about.
    """
    track = oval()
    baseline, _ = cached_racing_line(car, track, tmp_path,
                                     schedule=((6, 20),), sweep_evaluations=60)
    slower, source = cached_racing_line(car.with_power_scale(0.85), track,
                                        tmp_path)
    assert source == "cache"
    assert slower.offset == pytest.approx(baseline.offset)   # same line
    assert slower.lap_time > baseline.lap_time               # different car


def test_a_different_car_gets_its_own_stored_line(tmp_path, car):
    track = oval()
    cached_racing_line(car, track, tmp_path, schedule=((6, 20),),
                       sweep_evaluations=60)
    import dataclasses
    other = Vehicle(dataclasses.replace(car.spec, name="OtherCar"))
    _, source = cached_racing_line(other, track, tmp_path,
                                   schedule=((6, 20),), sweep_evaluations=60)
    assert source == "solved"
    assert find_line(tmp_path, track, "OtherCar") is not None


def test_a_stored_line_is_small(tmp_path):
    track = oval(ds=2.0)
    path = save_line(tmp_path / "line.npz", np.zeros(len(track)), track)
    assert path.stat().st_size < 20_000
