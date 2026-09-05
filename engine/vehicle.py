"""The point-mass vehicle model.

A point mass carrying aerodynamic load, with longitudinal load transfer onto
the driven axle. That last term is what stops a 500 kW rear-drive car from
pretending it can use all of its power at the exit of a hairpin, and it is
the main reason this is not simply "F = ma with a friction circle".

Nothing here knows what an LMP2 is. Every number arrives in a
:class:`~engine.config.VehicleSpec`.
"""

from __future__ import annotations

import numpy as np

from .aero import AeroModel
from .conditions import Conditions
from .config import VehicleSpec
from .powertrain import PowertrainModel
from .tyres import TyreModel
from .units import G, format_laptime

_BISECT_STEPS = 50      # 2^-50 of the speed bracket: exact for our purposes
_LOAD_TRANSFER_ITERS = 8


class Vehicle:
    """Performance envelope of one car class in one set of conditions."""

    def __init__(self, spec: VehicleSpec, conditions: Conditions | None = None,
                 power_scale: float = 1.0):
        self.spec = spec
        self.conditions = conditions or Conditions()
        self.aero = AeroModel(spec.aero, rho=self.conditions.rho)
        self.powertrain = PowertrainModel(spec.powertrain, power_scale=power_scale)
        self.mass = spec.mass.total_kg
        self.tyres = TyreModel(spec.tyres, fz_ref_n=self.mass * G)
        self._v_ceiling = float(spec.v_max_ms) if spec.v_max_ms else 150.0

    # -- derived quantities ---------------------------------------------
    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def weight_n(self) -> float:
        """Static weight (N)."""
        return self.mass * G

    def environment_grip(self) -> float:
        """Grip multiplier from weather and track temperature."""
        return self.conditions.grip_multiplier()

    def normal_load(self, v):
        """Total vertical load on the tyres at speed ``v`` (N)."""
        return self.weight_n + self.aero.downforce(v)

    def _driven_axle_load(self, v, ax):
        """Vertical load on the driven axle, including longitudinal transfer."""
        m = self.spec.mass
        down = self.aero.downforce(v)
        bal_f = self.aero.balance_front(v)
        transfer = self.mass * np.asarray(ax, dtype=float) * m.cg_height_m / m.wheelbase_m
        drive = self.powertrain.drive
        if drive == "awd":
            return self.weight_n + down
        if drive == "fwd":
            load = m.weight_dist_front * self.weight_n + bal_f * down - transfer
        else:  # rwd
            load = ((1.0 - m.weight_dist_front) * self.weight_n
                    + (1.0 - bal_f) * down + transfer)
        return np.maximum(load, 0.0)

    # -- lateral ---------------------------------------------------------
    def max_lateral_accel(self, v, grip: float = 1.0):
        """Peak cornering acceleration available at speed ``v`` (m/s^2)."""
        g_total = grip * self.environment_grip()
        fz = self.normal_load(v)
        return self.tyres.mu_y(fz, g_total) * fz / self.mass

    def corner_speed(self, curvature, grip: float = 1.0):
        """Fastest steady-state speed through a corner of the given curvature.

        Solves ``m v^2 |k| = mu_y(Fz(v)) Fz(v)`` by bisection. Downforce puts
        the unknown on both sides -- more speed buys more grip -- so there is
        no closed form once the tyres are load-sensitive. Where aerodynamic
        grip outruns the demand entirely the corner is not grip-limited at
        all and the car's top speed is returned.
        """
        k = np.abs(np.asarray(curvature, dtype=float))
        scalar = k.ndim == 0
        k = np.atleast_1d(k)
        v_hi = np.full(k.shape, self.top_speed(grip=grip))

        def excess(v):
            """Grip surplus: positive means the car can go faster still."""
            return self.max_lateral_accel(v, grip=grip) - v * v * k

        # Corners the car cannot outgrow aerodynamically are top-speed limited.
        unbounded = excess(v_hi) >= 0.0
        lo = np.zeros_like(k)
        hi = v_hi.copy()
        for _ in range(_BISECT_STEPS):
            mid = 0.5 * (lo + hi)
            go_faster = excess(mid) >= 0.0
            lo = np.where(go_faster, mid, lo)
            hi = np.where(go_faster, hi, mid)
        out = np.where(unbounded, v_hi, 0.5 * (lo + hi))
        return float(out[0]) if scalar else out

    # -- longitudinal ----------------------------------------------------
    def ellipse_fraction(self, v, lateral_accel, grip: float = 1.0):
        """Share of longitudinal grip left over at ``v`` while pulling ``ay``."""
        ay_max = np.maximum(self.max_lateral_accel(v, grip=grip), 1e-9)
        return self.tyres.ellipse_long_fraction(np.abs(lateral_accel) / ay_max)

    def longitudinal_limits(self, v, ellipse_fraction, grip: float = 1.0):
        """Acceleration each constraint would allow on its own (m/s^2).

        Returns ``(a_grip, a_engine)``, both already net of drag and rolling
        resistance. They are kept apart rather than combined because the
        ``min`` of the two has a kink in it: interpolating across that kink
        smears the traction-to-power transition, whereas taking the minimum
        of two separately smooth functions puts it back exactly where it
        belongs. :class:`~engine.qss.AccelerationTable` depends on this.
        """
        g_total = grip * self.environment_grip()
        v = np.asarray(v, dtype=float)
        frac = np.asarray(ellipse_fraction, dtype=float)
        fz = self.normal_load(v)
        mu_x = self.tyres.mu_x(fz, g_total)
        resist = self.aero.drag(v) + self.spec.rolling_resistance * fz

        # Grip-limited: the driven-axle load depends on the very acceleration
        # being solved for, so iterate. The map contracts at roughly
        # mu_x * h_cg / wheelbase per pass, so this converges in a handful.
        ax = np.zeros(np.broadcast(v, frac).shape, dtype=float)
        for _ in range(_LOAD_TRANSFER_ITERS):
            f_grip = mu_x * self._driven_axle_load(v, ax) * frac
            ax = (f_grip - resist) / self.mass
        a_engine = (self.powertrain.tractive_force(v) - resist) / self.mass
        return ax, np.broadcast_to(a_engine, ax.shape)

    def braking_limit(self, v, ellipse_fraction, grip: float = 1.0):
        """Deceleration available (positive m/s^2) for a given ellipse share.

        All four wheels brake, so the whole normal load is available -- load
        transfer moves grip between axles but does not create or destroy it.
        Drag and rolling resistance help, which is why a high-downforce car
        stops far shorter than its tyres alone would suggest. Note this is
        affine in ``ellipse_fraction``: the tyre and brake terms both scale
        with it while the aerodynamic term does not.
        """
        g_total = grip * self.environment_grip()
        v = np.asarray(v, dtype=float)
        frac = np.asarray(ellipse_fraction, dtype=float)
        fz = self.normal_load(v)
        f_tyre = self.tyres.mu_x(fz, g_total) * fz
        cap = self.spec.brakes.max_force_n
        if cap is not None:
            f_tyre = np.minimum(f_tyre, cap)
        f_total = f_tyre * frac + self.aero.drag(v) + self.spec.rolling_resistance * fz
        return f_total / self.mass

    def max_long_accel(self, v, lateral_accel=0.0, grip: float = 1.0):
        """Best longitudinal acceleration at speed ``v`` while pulling ``ay``.

        Limited by whichever binds first: engine power, or grip on the driven
        axle after the friction ellipse has taken its lateral share.
        """
        frac = self.ellipse_fraction(v, lateral_accel, grip=grip)
        a_grip, a_engine = self.longitudinal_limits(v, frac, grip=grip)
        ax = np.minimum(a_grip, a_engine)
        return ax if np.ndim(v) or np.ndim(lateral_accel) else float(
            np.atleast_1d(ax)[0])

    def max_long_decel(self, v, lateral_accel=0.0, grip: float = 1.0):
        """Best braking deceleration (positive m/s^2) at ``v`` while pulling ``ay``."""
        frac = self.ellipse_fraction(v, lateral_accel, grip=grip)
        ax = self.braking_limit(v, frac, grip=grip)
        return ax if np.ndim(v) or np.ndim(lateral_accel) else float(
            np.atleast_1d(ax)[0])

    def top_speed(self, grip: float = 1.0) -> float:
        """Speed at which tractive effort and resistance balance (m/s)."""
        lo, hi = 1.0, 200.0

        def surplus(v: float) -> float:
            fz = self.normal_load(v)
            return float(self.powertrain.tractive_force(v)
                         - self.aero.drag(v)
                         - self.spec.rolling_resistance * fz)

        if surplus(hi) > 0:
            v = hi
        else:
            for _ in range(_BISECT_STEPS):
                mid = 0.5 * (lo + hi)
                if surplus(mid) > 0:
                    lo = mid
                else:
                    hi = mid
            v = 0.5 * (lo + hi)
        if self.spec.v_max_ms:
            v = min(v, float(self.spec.v_max_ms))
        return v

    # -- variants --------------------------------------------------------
    def with_conditions(self, conditions: Conditions) -> "Vehicle":
        """Same car, different weather."""
        return Vehicle(self.spec, conditions=conditions,
                       power_scale=self.powertrain.power_scale)

    def with_fuel(self, fuel_kg: float) -> "Vehicle":
        """Same car at a different fuel load -- a stint gets quicker as it burns off."""
        import dataclasses
        spec = dataclasses.replace(self.spec, mass=self.spec.mass.with_fuel(fuel_kg))
        return Vehicle(spec, conditions=self.conditions,
                       power_scale=self.powertrain.power_scale)

    def with_power_scale(self, scale: float) -> "Vehicle":
        """Same car under a different BoP power adjustment."""
        return Vehicle(self.spec, conditions=self.conditions, power_scale=scale)

    # -- reporting -------------------------------------------------------
    def summary(self) -> str:
        """Headline performance numbers, for eyeballing a new class file."""
        vmax = self.top_speed()
        lines = [
            f"{self.spec.name}  ({self.mass:.0f} kg, "
            f"{self.powertrain.power(vmax) / 1000:.0f} kW)",
            f"  top speed        {vmax:6.1f} m/s  ({vmax * 3.6:5.1f} km/h)",
            f"  downforce @vmax  {self.aero.downforce(vmax) / 1000:6.1f} kN  "
            f"({self.aero.downforce(vmax) / self.weight_n:4.2f} x weight)",
            f"  drag @vmax       {self.aero.drag(vmax) / 1000:6.1f} kN  "
            f"(L/D {self.aero.efficiency(vmax):4.2f})",
            f"  lateral @50 m/s  {self.max_lateral_accel(50.0) / G:6.2f} g",
            f"  lateral @80 m/s  {self.max_lateral_accel(80.0) / G:6.2f} g",
            f"  accel  @20 m/s   {self.max_long_accel(20.0) / G:6.2f} g",
            f"  braking @70 m/s  {self.max_long_decel(70.0) / G:6.2f} g",
            f"  min corner r     {1.0 / max(self._min_curvature_probe(), 1e-9):6.1f} m"
            f" at 30 m/s",
        ]
        return "\n".join(lines)

    def _min_curvature_probe(self) -> float:
        """Curvature the car can sustain at 30 m/s -- a slow-corner grip proxy."""
        return float(self.max_lateral_accel(30.0) / (30.0 ** 2))

    def __repr__(self) -> str:
        return f"Vehicle({self.spec.name}, {self.mass:.0f}kg, {self.conditions!r})"
