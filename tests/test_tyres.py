"""Tyre model: load sensitivity and the friction ellipse."""

import numpy as np
import pytest

from engine.config import TyreSpec
from engine.tyres import TyreModel, TyreState

FZ_REF = 10000.0


def make_model(**kw) -> TyreModel:
    spec = TyreSpec.from_dict({"mu_x": 1.5, "mu_y": 1.6, **kw})
    return TyreModel(spec, fz_ref_n=FZ_REF)


def test_mu_equals_reference_at_reference_load():
    m = make_model()
    assert m.mu_x(FZ_REF) == pytest.approx(1.5)
    assert m.mu_y(FZ_REF) == pytest.approx(1.6)


def test_grip_falls_as_load_rises():
    m = make_model(load_sensitivity=0.15)
    assert m.mu_y(2 * FZ_REF) < m.mu_y(FZ_REF) < m.mu_y(0.5 * FZ_REF)
    # Total force still rises with load even though the coefficient falls.
    assert m.mu_y(2 * FZ_REF) * 2 * FZ_REF > m.mu_y(FZ_REF) * FZ_REF


def test_zero_load_sensitivity_is_load_independent():
    m = make_model(load_sensitivity=0.0)
    assert m.mu_y(5 * FZ_REF) == pytest.approx(m.mu_y(FZ_REF))


def test_ellipse_endpoints():
    m = make_model()
    assert m.ellipse_long_fraction(0.0) == pytest.approx(1.0)
    assert m.ellipse_long_fraction(1.0) == pytest.approx(0.0)


def test_ellipse_is_circular_for_exponent_two():
    m = make_model(ellipse_exponent=2.0)
    # 3-4-5: using 60% of lateral grip leaves 80% of longitudinal.
    assert m.ellipse_long_fraction(0.6) == pytest.approx(0.8)


def test_ellipse_clamps_beyond_the_limit():
    m = make_model()
    assert m.ellipse_long_fraction(1.7) == pytest.approx(0.0)
    assert m.ellipse_long_fraction(-0.6) == pytest.approx(0.8)


def test_higher_exponent_is_a_fuller_ellipse():
    circle = make_model(ellipse_exponent=2.0)
    fuller = make_model(ellipse_exponent=2.6)
    assert fuller.ellipse_long_fraction(0.6) > circle.ellipse_long_fraction(0.6)


def test_ellipse_is_vectorised():
    m = make_model()
    out = m.ellipse_long_fraction(np.array([0.0, 0.6, 1.0]))
    assert out == pytest.approx([1.0, 0.8, 0.0])


def test_ellipse_decreases_monotonically():
    m = make_model()
    u = np.linspace(0.0, 1.0, 50)
    assert np.all(np.diff(m.ellipse_long_fraction(u)) <= 1e-12)


def test_distance_wear_reaches_expected_loss_after_one_lap():
    spec = TyreSpec.from_dict({
        "mu_x": 1.5, "mu_y": 1.6, "wear_model": "distance",
        "grip_loss_per_lap": 0.01, "reference_lap_m": 5000.0})
    state = TyreState(spec)
    assert state.grip == pytest.approx(1.0)
    state.advance(5000.0)
    assert state.grip == pytest.approx(0.99)


def test_wear_is_floored_at_min_grip():
    spec = TyreSpec.from_dict({
        "mu_x": 1.5, "mu_y": 1.6, "wear_model": "distance",
        "grip_loss_per_lap": 0.05, "reference_lap_m": 5000.0, "min_grip": 0.85})
    state = TyreState(spec)
    state.advance(5000.0 * 100)
    assert state.grip == pytest.approx(0.85)


def test_energy_wear_responds_to_how_hard_the_lap_was():
    spec = TyreSpec.from_dict({
        "mu_x": 1.5, "mu_y": 1.6, "wear_model": "energy",
        "wear_energy_ref_j": 4.0e9})
    hard, easy = TyreState(spec), TyreState(spec)
    hard.advance(5000.0, mass_kg=1000.0, accel_mag=15.0)
    easy.advance(5000.0, mass_kg=1000.0, accel_mag=5.0)
    assert hard.grip < easy.grip < 1.0


def test_none_model_never_wears():
    spec = TyreSpec.from_dict({"mu_x": 1.5, "mu_y": 1.6, "wear_model": "none"})
    state = TyreState(spec)
    state.advance(1.0e6, mass_kg=1000.0, accel_mag=20.0)
    assert state.grip == pytest.approx(1.0)


def test_reset_restores_fresh_tyres():
    spec = TyreSpec.from_dict({
        "mu_x": 1.5, "mu_y": 1.6, "wear_model": "distance",
        "grip_loss_per_lap": 0.02})
    state = TyreState(spec)
    state.advance(10000.0)
    assert state.grip < 1.0
    state.reset()
    assert state.grip == pytest.approx(1.0)
    assert state.distance_m == 0.0
