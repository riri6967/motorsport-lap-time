"""Tyre grip: load sensitivity, the friction ellipse, and degradation."""

from __future__ import annotations

import numpy as np

from .config import TyreSpec


class TyreModel:
    """Peak friction as a function of vertical load.

    Racing tyres are load-sensitive: doubling the vertical load does not
    double the grip. The usual empirical form is used,

        mu(Fz) = mu_ref * (Fz / Fz_ref) ** -k

    with ``Fz_ref`` defaulting to the car's static weight, so ``mu_x``/``mu_y``
    in the class file mean "peak friction at rest" and stay directly
    comparable between classes. Load sensitivity is evaluated on the total
    normal load rather than per axle -- a point-mass model has no business
    claiming per-corner fidelity, and the whole-car form is what the
    published lap times can actually calibrate.
    """

    def __init__(self, spec: TyreSpec, fz_ref_n: float):
        self.spec = spec
        self.fz_ref = float(spec.fz_ref_n if spec.fz_ref_n is not None else fz_ref_n)
        if self.fz_ref <= 0:
            raise ValueError("tyre reference load must be > 0")

    def load_factor(self, fz):
        """Multiplier on peak mu at vertical load ``fz``."""
        fz = np.maximum(np.asarray(fz, dtype=float), 1.0)
        return (fz / self.fz_ref) ** (-self.spec.load_sensitivity)

    def mu_x(self, fz, grip: float = 1.0):
        """Longitudinal peak friction coefficient."""
        return self.spec.mu_x * self.load_factor(fz) * grip

    def mu_y(self, fz, grip: float = 1.0):
        """Lateral peak friction coefficient."""
        return self.spec.mu_y * self.load_factor(fz) * grip

    def ellipse_long_fraction(self, lateral_utilisation):
        """Share of longitudinal grip left while using some lateral grip.

        The friction ellipse: a tyre asked for cornering force has less left
        for accelerating or braking. With exponent ``p``,

            (ax/ax_max)**p + (ay/ay_max)**p = 1

        p = 2 is the classic circle-in-normalised-axes; real slicks measure
        slightly fuller, which ``ellipse_exponent`` lets a class capture.
        """
        u = np.clip(np.abs(np.asarray(lateral_utilisation, dtype=float)), 0.0, 1.0)
        p = self.spec.ellipse_exponent
        return np.maximum(0.0, 1.0 - u ** p) ** (1.0 / p)

    def __repr__(self) -> str:
        s = self.spec
        return (f"TyreModel(mu_x={s.mu_x}, mu_y={s.mu_y}, "
                f"k={s.load_sensitivity}, Fz_ref={self.fz_ref:.0f}N)")


class TyreState:
    """Accumulated wear for one set of tyres.

    Two models are offered. ``distance`` loses a fixed fraction of grip per
    reference lap and is what published stint data can support for most
    classes. ``energy`` integrates frictional work, so a lap spent in traffic
    or behind a safety car wears the tyres less than a qualifying lap -- which
    is the behaviour the endurance scenario needs.
    """

    def __init__(self, spec: TyreSpec):
        self.spec = spec
        self.wear = 0.0
        self.distance_m = 0.0
        self.energy_j = 0.0

    @property
    def grip(self) -> float:
        """Current grip multiplier, floored at the compound's ``min_grip``."""
        return float(max(self.spec.min_grip, 1.0 - self.wear))

    def advance(self, ds: float, mass_kg: float = 0.0,
                accel_mag: float = 0.0) -> float:
        """Advance ``ds`` metres of travel and return the new grip multiplier.

        ``accel_mag`` is the combined acceleration magnitude sqrt(ax^2+ay^2)
        in m/s^2, used only by the energy model.
        """
        self.distance_m += ds
        model = self.spec.wear_model
        if model == "none":
            return self.grip
        if model == "distance":
            per_m = self.spec.grip_loss_per_lap / self.spec.reference_lap_m
            self.wear += per_m * ds
        else:  # energy
            work = mass_kg * abs(accel_mag) * ds
            self.energy_j += work
            self.wear += work / self.spec.wear_energy_ref_j
        return self.grip

    def advance_lap(self, lap_distance_m: float, mean_accel: float = 0.0,
                    mass_kg: float = 0.0) -> float:
        """Convenience wrapper for whole-lap stepping in strategy code."""
        return self.advance(lap_distance_m, mass_kg=mass_kg, accel_mag=mean_accel)

    def reset(self) -> None:
        """Fresh set of tyres."""
        self.wear = 0.0
        self.distance_m = 0.0
        self.energy_j = 0.0

    def __repr__(self) -> str:
        return (f"TyreState(grip={self.grip:.4f}, "
                f"{self.distance_m/1000:.1f}km, model={self.spec.wear_model})")
