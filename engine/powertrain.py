"""Tractive effort available at the driven wheels."""

from __future__ import annotations

import numpy as np

from .config import PowertrainSpec

_V_EPS = 0.5  # m/s; below this P/v is meaningless and traction rules anyway


class PowertrainModel:
    """Engine output mapped to a force at the contact patch.

    Without a ``power_curve`` the engine is flat-rated: a close-ratio racing
    gearbox keeps a competition engine near peak power for most of its
    operating range, so constant power is a better first approximation than
    it would be for a road car.
    """

    def __init__(self, spec: PowertrainSpec, power_scale: float = 1.0):
        self.spec = spec
        self.power_scale = float(power_scale)
        if spec.power_curve is not None:
            curve = np.asarray(spec.power_curve, dtype=float)
            self._v = curve[:, 0]
            self._p = curve[:, 1]
        else:
            self._v = None
            self._p = None

    @property
    def drive(self) -> str:
        return self.spec.drive

    def power(self, v):
        """Crank power at road speed ``v`` (W), before driveline losses."""
        if self._v is None:
            base = self.spec.max_power_w
            return (np.full(np.shape(v), base) if np.ndim(v) else base) * self.power_scale
        return np.interp(v, self._v, self._p) * self.power_scale

    def tractive_force(self, v):
        """Force the engine can deliver at the wheels (N), ignoring grip.

        Capped by ``max_tractive_force_n`` where given -- first gear and the
        clutch impose a ceiling that P/v does not know about.
        """
        v_arr = np.asarray(v, dtype=float)
        v_eff = np.maximum(v_arr, _V_EPS)
        force = self.power(v_eff) * self.spec.efficiency / v_eff
        cap = self.spec.max_tractive_force_n
        if cap is not None:
            force = np.minimum(force, cap)
        return force if np.ndim(v) else float(force)

    def with_power_scale(self, scale: float) -> "PowertrainModel":
        """A copy at a different power level -- how BoP is usually applied."""
        return PowertrainModel(self.spec, power_scale=scale)

    def __repr__(self) -> str:
        kind = "curve" if self._v is not None else f"{self.spec.max_power_w/1e3:.0f}kW flat"
        return f"PowertrainModel({kind}, {self.spec.drive}, eta={self.spec.efficiency})"
