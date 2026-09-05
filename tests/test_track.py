"""Track geometry: construction, curvature, closure and offset paths."""

import numpy as np
import pytest

from engine.config import ConfigError
from engine.track import Track, arclength_from_points, curvature_from_points


def oval(ds: float = 2.0) -> Track:
    """1628 m oval: two 500 m straights and two 180-degree, 100 m bends."""
    return Track.from_segments("Oval", [
        {"type": "straight", "length": 500},
        {"type": "arc", "radius": 100, "angle": 180},
        {"type": "straight", "length": 500},
        {"type": "arc", "radius": 100, "angle": 180},
    ], ds=ds, width=12.0)


def circle(radius: float = 100.0, ds: float = 1.0) -> Track:
    return Track.from_segments(
        "Circle", [{"type": "arc", "radius": radius, "angle": 360}],
        ds=ds, width=12.0)


# -- construction --------------------------------------------------------
def test_segment_lengths_add_up_to_the_lap_distance():
    assert oval().length == pytest.approx(2 * 500 + 2 * np.pi * 100, rel=1e-9)


def test_a_well_formed_circuit_closes_on_itself():
    assert oval().closure_error() == pytest.approx(0.0, abs=1e-6)


def test_closure_error_exposes_a_description_that_does_not_join_up():
    """A circuit reconstructed from published figures rarely closes exactly."""
    broken = Track.from_segments("Broken", [
        {"type": "straight", "length": 500},
        {"type": "arc", "radius": 100, "angle": 180},
        {"type": "straight", "length": 560},          # 60 m too long
        {"type": "arc", "radius": 100, "angle": 180},
    ], ds=2.0, width=12.0)
    assert broken.closure_error() == pytest.approx(60.0, rel=1e-3)


def test_arc_curvature_is_the_reciprocal_of_its_radius():
    track = circle(80.0)
    assert np.allclose(track.curvature, 1.0 / 80.0)


def test_a_left_hand_turn_has_positive_curvature():
    left = Track.from_segments("L", [{"type": "arc", "radius": 100, "angle": 90},
                                     {"type": "straight", "length": 50}],
                               ds=2.0, closed=False)
    right = Track.from_segments("R", [{"type": "arc", "radius": 100, "angle": -90},
                                      {"type": "straight", "length": 50}],
                                ds=2.0, closed=False)
    assert left.curvature.max() > 0
    assert right.curvature.min() < 0


def test_a_full_circle_returns_to_where_it_started():
    track = circle(100.0)
    assert track.x[0] == pytest.approx(0.0, abs=1e-9)
    assert track.closure_error() == pytest.approx(0.0, abs=1e-6)
    # The centre sits one radius to the left of the start.
    assert np.hypot(track.x - 0.0, track.y - 100.0) == pytest.approx(
        np.full(len(track), 100.0), abs=1e-8)


def test_a_straight_has_no_curvature():
    line = Track.from_segments("Straight", [{"type": "straight", "length": 300}],
                               ds=2.0, closed=False)
    assert np.allclose(line.curvature, 0.0)


def test_sampling_is_uniform_in_arc_length():
    """A closed lap has to divide exactly, so the step lands near ds, not on it."""
    track = oval(ds=2.0)
    steps = np.diff(track.s)
    assert steps.ptp() == pytest.approx(0.0, abs=1e-9)
    assert steps[0] == pytest.approx(2.0, rel=1e-3)
    assert track.ds.sum() == pytest.approx(track.length, rel=1e-12)


def test_a_closed_track_does_not_repeat_its_first_point():
    track = oval()
    assert track.s[-1] < track.length
    assert track.length - track.s[-1] == pytest.approx(track.ds[-1])


# -- curvature from raw points -------------------------------------------
def test_menger_curvature_recovers_a_known_circle():
    theta = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    radius = 55.0
    k = curvature_from_points(radius * np.cos(theta), radius * np.sin(theta))
    assert np.allclose(k, 1.0 / radius, rtol=1e-4)


def test_menger_curvature_is_zero_on_a_straight_line():
    x = np.linspace(0, 100, 50)
    k = curvature_from_points(x, np.zeros_like(x), closed=False)
    assert np.allclose(k, 0.0, atol=1e-9)


def test_arclength_of_a_circle_matches_its_circumference():
    theta = np.linspace(0, 2 * np.pi, 2000, endpoint=False)
    _, total = arclength_from_points(40 * np.cos(theta), 40 * np.sin(theta))
    assert total == pytest.approx(2 * np.pi * 40, rel=1e-5)


def test_a_point_built_track_reproduces_the_segment_built_one():
    reference = circle(100.0, ds=1.0)
    rebuilt = Track.from_points("Rebuilt", reference.x, reference.y, ds=1.0)
    assert rebuilt.length == pytest.approx(reference.length, rel=1e-4)
    assert np.allclose(rebuilt.curvature, reference.curvature, rtol=2e-3)


# -- offsets --------------------------------------------------------------
def test_moving_inside_a_corner_shortens_the_path_and_tightens_it():
    """The normal points left, so a positive offset cuts inside a left-hander."""
    track = circle(100.0, ds=0.5)
    ds, kappa = track.offset_geometry(np.full(len(track), 3.0))
    assert ds.sum() == pytest.approx(2 * np.pi * 97.0, rel=1e-4)
    assert np.allclose(kappa, 1.0 / 97.0, rtol=1e-3)


def test_moving_outside_a_corner_lengthens_the_path_and_opens_it():
    track = circle(100.0, ds=0.5)
    ds, kappa = track.offset_geometry(np.full(len(track), -3.0))
    assert ds.sum() == pytest.approx(2 * np.pi * 103.0, rel=1e-4)
    assert np.allclose(kappa, 1.0 / 103.0, rtol=1e-3)


def test_a_constant_offset_on_a_straight_changes_nothing():
    line = Track.from_segments("Straight", [{"type": "straight", "length": 300}],
                               ds=2.0, closed=False)
    ds, kappa = line.offset_geometry(np.full(len(line), 2.5))
    assert np.allclose(kappa, 0.0, atol=1e-9)
    assert ds[:-1].sum() == pytest.approx(line.s[-1], rel=1e-9)


def test_offset_bounds_leave_room_for_the_car():
    track = oval()
    lo, hi = track.offset_bounds(car_width=1.9, margin=0.1)
    assert hi == pytest.approx(6.0 - 0.95 - 0.1)
    assert lo == pytest.approx(-(6.0 - 0.95 - 0.1))


def test_asymmetric_width_gives_asymmetric_bounds():
    track = Track.from_segments("Asym", [{"type": "arc", "radius": 100, "angle": 360}],
                                ds=2.0, width_left=8.0, width_right=4.0)
    lo, hi = track.offset_bounds(car_width=2.0)
    assert hi == pytest.approx(7.0)
    assert lo == pytest.approx(-3.0)


# -- corner detection -----------------------------------------------------
def test_corners_are_found_with_the_right_radius_and_hand():
    found = oval().corners()
    assert len(found) == 2
    assert all(c[3] == "left" for c in found)
    assert all(c[2] == pytest.approx(100.0, rel=1e-3) for c in found)


def test_a_gentle_kink_is_not_counted_as_a_corner():
    track = Track.from_segments("Kink", [
        {"type": "straight", "length": 500},
        {"type": "arc", "radius": 2000, "angle": 20},
        {"type": "straight", "length": 500},
        {"type": "arc", "radius": 300, "angle": 340},
    ], ds=2.0, width=12.0)
    assert len(track.corners(min_curvature=1.0 / 400.0)) == 1


# -- YAML round trip ------------------------------------------------------
def test_yaml_description_loads(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text(
        "name: YAML Oval\nwidth: 14.0\nsectors: [0, 500, 1100]\n"
        "segments:\n"
        "  - {type: straight, length: 500}\n"
        "  - {type: arc, radius: 100, angle: 180}\n"
        "  - {type: straight, length: 500}\n"
        "  - {type: arc, radius: 100, angle: 180}\n")
    track = Track.from_yaml(path, ds=2.0)
    assert track.name == "YAML Oval"
    assert track.length == pytest.approx(oval().length)
    assert track.sector_starts_m == (0.0, 500.0, 1100.0)
    assert track.w_left.mean() == pytest.approx(7.0)


def test_yaml_rejects_an_unknown_key(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: X\nsegmnets: []\n")
    with pytest.raises(ConfigError, match="unknown key"):
        Track.from_yaml(path)


def test_an_arc_without_a_radius_is_rejected():
    with pytest.raises(ConfigError, match="radius"):
        Track.from_segments("bad", [{"type": "arc", "angle": 90}])
