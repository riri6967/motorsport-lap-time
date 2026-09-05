"""Racing line: the minimum-curvature seed and the lap-time refinement."""

import numpy as np
import pytest

from engine.config import VehicleSpec
from engine.qss import solve_lap
from engine.racing_line import (DEFAULT_CAR_WIDTH, _BSplineCorrection,
                                min_curvature_line, optimise_racing_line)
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

# Deliberately small so the suite stays quick; the physics is unaffected.
FAST_SCHEDULE = ((6, 25),)
FAST = {"schedule": FAST_SCHEDULE, "sweep_evaluations": 80}


@pytest.fixture
def car() -> Vehicle:
    return Vehicle(VehicleSpec.from_dict(SPEC))


@pytest.fixture
def oval() -> Track:
    return Track.from_segments("Oval", [
        {"type": "straight", "length": 400},
        {"type": "arc", "radius": 90, "angle": 180},
        {"type": "straight", "length": 400},
        {"type": "arc", "radius": 90, "angle": 180},
    ], ds=5.0, width=14.0)


def curvature_integral(track: Track, offset) -> float:
    ds, kappa = track.offset_geometry(offset)
    return float(np.sum(kappa ** 2 * ds))


# -- minimum curvature ---------------------------------------------------
def test_the_seed_stays_between_the_white_lines(oval):
    offset = min_curvature_line(oval, car_width=DEFAULT_CAR_WIDTH)
    lo, hi = oval.offset_bounds(DEFAULT_CAR_WIDTH, 0.1)
    assert np.all(offset >= lo - 1e-9)
    assert np.all(offset <= hi + 1e-9)


def test_the_seed_is_straighter_than_the_centreline(oval):
    offset = min_curvature_line(oval)
    assert curvature_integral(oval, offset) < curvature_integral(
        oval, np.zeros(len(oval)))


def test_the_seed_takes_the_widest_radius_round_a_constant_corner():
    """On a plain circle the least-curvature path is the outer edge.

    The normal points left and the circle turns left, so the outside of the
    corner is a negative offset.
    """
    circle = Track.from_segments(
        "Circle", [{"type": "arc", "radius": 100, "angle": 360}],
        ds=4.0, width=14.0)
    offset = min_curvature_line(circle, offset_penalty=0.0, iterations=1)
    lo, _ = circle.offset_bounds(DEFAULT_CAR_WIDTH, 0.1)
    assert offset.mean() == pytest.approx(float(np.min(lo)), abs=0.35)


def test_the_seed_leaves_a_straight_alone():
    """With no curvature to remove there is nothing to gain by moving over."""
    line = Track.from_segments(
        "Straight", [{"type": "straight", "length": 400}],
        ds=5.0, width=12.0, closed=False)
    offset = min_curvature_line(line)
    assert np.abs(offset).max() < 0.5


def test_a_track_narrower_than_the_car_does_not_break_the_solve():
    narrow = Track.from_segments(
        "Narrow", [{"type": "arc", "radius": 60, "angle": 360}],
        ds=4.0, width=1.0)
    offset = min_curvature_line(narrow, car_width=2.0)
    assert np.all(np.isfinite(offset))
    assert np.abs(offset).max() < 1e-6


def test_the_offset_penalty_pulls_towards_the_centreline(oval):
    loose = min_curvature_line(oval, offset_penalty=0.0)
    tight = min_curvature_line(oval, offset_penalty=1.0)
    assert np.abs(tight).mean() < np.abs(loose).mean()


# -- lap-time refinement -------------------------------------------------
def test_the_seed_alone_already_beats_the_centreline(car, oval):
    seeded = optimise_racing_line(car, oval, refine=False)
    assert seeded.lap_time < solve_lap(car, oval).lap_time
    assert seeded.method == "minimum curvature"


def test_refinement_never_returns_a_slower_line_than_its_seed(car, oval):
    """The search keeps the best lap it has seen, so this cannot regress."""
    line = optimise_racing_line(car, oval, **FAST)
    assert line.lap_time <= line.seed_lap_time + 1e-9
    assert line.gain >= 0.0


def test_refinement_finds_time_the_curvature_seed_cannot(car, oval):
    """The gain is real: it comes from opening corner exits onto straights."""
    line = optimise_racing_line(car, oval, schedule=((6, 40),),
                                sweep_evaluations=400)
    assert line.gain > 0.05


def test_the_optimised_line_stays_on_the_track(car, oval):
    line = optimise_racing_line(car, oval, **FAST)
    lo, hi = oval.offset_bounds(DEFAULT_CAR_WIDTH, 0.1)
    assert np.all(line.offset >= lo - 1e-9)
    assert np.all(line.offset <= hi + 1e-9)


def test_the_reported_lap_is_the_one_the_offsets_produce(car, oval):
    line = optimise_racing_line(car, oval, **FAST)
    recomputed = solve_lap(car, oval, offset=line.offset)
    assert recomputed.lap_time == pytest.approx(line.lap_time, rel=1e-12)


def test_history_is_monotonically_improving(car, oval):
    line = optimise_racing_line(car, oval, schedule=((6, 30),),
                                sweep_evaluations=100)
    times = [t for _, t in line.history]
    assert times == sorted(times, reverse=True)


def test_a_supplied_seed_is_used(car, oval):
    seed = np.full(len(oval), 1.0)
    line = optimise_racing_line(car, oval, seed=seed, refine=False)
    assert np.allclose(line.offset, seed)


def test_worn_tyres_slow_the_optimised_lap(car, oval):
    fresh = optimise_racing_line(car, oval, grip=1.0, **FAST)
    worn = optimise_racing_line(car, oval, grip=0.9, **FAST)
    assert worn.lap_time > fresh.lap_time


def test_a_yellow_flag_zone_is_respected_by_the_optimiser(car, oval):
    cap = np.full(len(oval), 1e3)
    zone = (oval.s > 100) & (oval.s < 250)
    cap[zone] = 25.0
    line = optimise_racing_line(car, oval, speed_limit=cap, **FAST)
    assert np.all(line.lap.v[zone] <= 25.0 + 1e-6)


# -- the correction basis -------------------------------------------------
def test_a_control_point_only_moves_its_own_stretch_of_track(oval):
    """Locality is what makes refinement work on a real circuit.

    With a global basis, adjusting the line through one corner ripples
    through every other corner on the lap, and the optimiser cannot place
    apexes independently.
    """
    basis = _BSplineCorrection(oval, 20)
    control = np.zeros(20)
    control[5] = 1.0
    influence = np.abs(basis.expand(control))
    touched = influence > 1e-9
    assert touched.sum() < 0.30 * len(oval)
    # The support is contiguous: four knot intervals around the control point.
    span = oval.s[touched].ptp()
    assert span == pytest.approx(4 * oval.length / 20, rel=0.2)


def test_the_basis_is_smooth_enough_to_differentiate_twice(oval):
    """Curvature is the second derivative, so a kink would be a fake corner.

    Compared against the piecewise-linear bump the same control point would
    produce: that one puts all of its second difference into a single spike
    at the knot, which the solver would read as a corner that is not there.
    """
    basis = _BSplineCorrection(oval, 20)
    control = np.zeros(20)
    control[7] = 2.0
    offset = basis.expand(control)

    peak = offset.max()
    centre = basis.spacing * 7
    linear = np.interp(oval.s,
                       [0.0, centre - basis.spacing, centre,
                        centre + basis.spacing, oval.length],
                       [0.0, 0.0, peak, 0.0, 0.0])

    smooth = np.abs(np.diff(offset, 2))
    kinked = np.abs(np.diff(linear, 2))
    assert smooth.max() < 0.25 * kinked.max()
    # And it is spread out rather than concentrated in one sample.
    assert smooth.max() < 6.0 * smooth[smooth > 0].mean()


def test_the_basis_wraps_around_the_start_line(oval):
    """A closed lap has no seam, and the line must not develop one."""
    basis = _BSplineCorrection(oval, 20)
    control = np.zeros(20)
    control[0] = 1.0
    offset = basis.expand(control)
    assert offset[0] > 0.5
    assert offset[-1] > 0.0          # still rising into the line from behind
    assert abs(offset[0] - offset[-1]) < 0.2


def test_applying_one_control_point_matches_a_full_expansion(oval):
    basis = _BSplineCorrection(oval, 20)
    lo, hi = oval.offset_bounds(0.0, 0.0)
    lo = np.broadcast_to(lo, (len(oval),)).copy()
    hi = np.broadcast_to(hi, (len(oval),)).copy()
    start = np.zeros(len(oval))
    control = np.zeros(20)
    control[9] = 1.3
    assert basis.apply_one(start, 9, 1.3, lo, hi) == pytest.approx(
        np.clip(basis.expand(control), lo, hi))


def test_knot_spacing_controls_how_finely_the_line_can_be_shaped(oval):
    """The bug this guards: knots so far apart that one spans several corners."""
    coarse = _BSplineCorrection(oval, max(8, int(oval.length / 300.0)))
    fine = _BSplineCorrection(oval, max(8, int(oval.length / 45.0)))
    assert fine.n_control > 3 * coarse.n_control
    assert fine.spacing == pytest.approx(45.0, rel=0.15)
