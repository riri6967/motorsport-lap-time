"""Quasi-steady-state lap-time solver.

The classic three-pass method. At every point on the track the car is assumed
to be at a steady state -- the transients of weight transfer settling and
tyres building slip are fast compared with the time spent in a corner -- so
the speed profile is fixed by three limits:

1. **Lateral.** How fast the corner can be taken at all.
2. **Acceleration.** Sweeping forwards, how fast the car can build speed out
   of each corner given what the engine and the friction ellipse allow.
3. **Braking.** Sweeping backwards, how late it can still stop for the next.

The speed at each point is the smallest of the three. On a closed circuit the
sweeps are repeated until the speed crossing the start line stops changing,
because the exit of the last corner sets the entry to the first.

Flags, safety cars and blocked track enter as an extra per-point speed cap,
which the same two sweeps then blend into a physical profile -- the car
brakes for a yellow zone and accelerates out of it exactly as it would for a
corner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .track import Track
from .units import format_laptime
from .vehicle import Vehicle

_V_FLOOR = 0.5          # m/s; keeps a standing start from dividing by zero
_DEFAULT_SWEEPS = 12


class AccelerationTable:
    """Precomputed longitudinal limits, for speed.

    The solver's sweeps are inherently sequential, so the vehicle model gets
    called once per point per sweep. Evaluating it directly means running the
    load-transfer fixed point thousands of times per lap, far too slow to sit
    inside a racing-line optimiser. So the envelope is tabulated once and read
    back by interpolation.

    Two details keep that from costing accuracy where it matters most:

    * The second axis is the **ellipse fraction**, not lateral utilisation.
      The fraction is ``(1 - u**p)**(1/p)``, which has an infinite slope at
      the limit; interpolating in ``u`` therefore smears the envelope exactly
      where the car spends its cornering time. In the fraction the underlying
      functions are near-affine, and the fraction itself is evaluated exactly
      at lookup rather than interpolated.
    * The grip and engine limits are tabulated **separately** and combined
      with ``min`` at lookup, so the traction-to-power transition stays a
      sharp corner instead of being rounded off by the interpolation.
    """

    def __init__(self, vehicle: Vehicle, grip: float = 1.0,
                 n_speed: int = 160, n_frac: int = 33):
        self.vehicle = vehicle
        self.grip = float(grip)
        v_max = vehicle.top_speed(grip=grip)
        self.v_grid = np.linspace(0.0, v_max * 1.02, n_speed)
        self.f_grid = np.linspace(0.0, 1.0, n_frac)
        self._dv = self.v_grid[1] - self.v_grid[0]
        self._df = self.f_grid[1] - self.f_grid[0]

        vv = np.broadcast_to(self.v_grid[:, None], (n_speed, n_frac))
        ff = np.broadcast_to(self.f_grid[None, :], (n_speed, n_frac))
        self.ay_max = np.maximum(
            vehicle.max_lateral_accel(self.v_grid, grip=grip), 1e-9)
        a_grip, a_engine = vehicle.longitudinal_limits(vv, ff, grip=grip)
        self.accel_grip = np.asarray(a_grip, dtype=float)
        self.accel_engine = np.asarray(a_engine, dtype=float)[:, 0]
        self.decel = np.asarray(
            vehicle.braking_limit(vv, ff, grip=grip), dtype=float)
        self.v_max = float(v_max)
        self._ellipse_p = vehicle.tyres.spec.ellipse_exponent

        # Scalar constants for an exact lateral limit at lookup time. The
        # ellipse fraction has an infinite slope at the limit, so even a
        # part-per-million error in ay_max turns into a visible error in the
        # grip left over -- interpolating this one is not good enough.
        self._mg = vehicle.weight_n
        self._mass = vehicle.mass
        self._fz_ref = vehicle.tyres.fz_ref
        self._k_load = vehicle.tyres.spec.load_sensitivity
        self._mu_y_eff = (vehicle.tyres.spec.mu_y * self.grip
                          * vehicle.environment_grip())
        self._const_cla = (None if vehicle.aero.spec.map is not None
                           else 0.5 * vehicle.aero.rho * vehicle.aero.spec.cla)
        self._aero = vehicle.aero

    # -- interpolation ---------------------------------------------------
    def _speed_index(self, v: float):
        fv = min(max(v, 0.0), self.v_grid[-1]) / self._dv
        i = min(int(fv), len(self.v_grid) - 2)
        return i, fv - i

    def _bilinear(self, table, v: float, frac: float) -> float:
        i, tv = self._speed_index(v)
        ff = min(max(frac, 0.0), 1.0) / self._df
        j = min(int(ff), len(self.f_grid) - 2)
        tf = ff - j
        a00, a01 = table[i, j], table[i, j + 1]
        a10, a11 = table[i + 1, j], table[i + 1, j + 1]
        return ((1 - tv) * ((1 - tf) * a00 + tf * a01)
                + tv * ((1 - tf) * a10 + tf * a11))

    def lateral_limit(self, v: float) -> float:
        """Peak lateral acceleration at speed ``v``, evaluated exactly."""
        if self._const_cla is not None:
            fz = self._mg + self._const_cla * v * v
        else:
            fz = self._mg + float(self._aero.downforce(v))
        return (self._mu_y_eff * (fz / self._fz_ref) ** (-self._k_load)
                * fz / self._mass)

    def ellipse_fraction(self, v: float, curvature: float) -> float:
        """Longitudinal grip left at ``v`` on a path of the given curvature.

        Evaluated exactly, not interpolated -- this is the sharp part.
        """
        u = min(1.0, (v * v * abs(curvature)) / self.lateral_limit(v))
        p = self._ellipse_p
        return max(0.0, 1.0 - u ** p) ** (1.0 / p)

    def accel_at(self, v: float, curvature: float) -> float:
        f = self.ellipse_fraction(v, curvature)
        i, t = self._speed_index(v)
        engine = (1 - t) * self.accel_engine[i] + t * self.accel_engine[i + 1]
        return min(self._bilinear(self.accel_grip, v, f), engine)

    def decel_at(self, v: float, curvature: float) -> float:
        return self._bilinear(self.decel, v, self.ellipse_fraction(v, curvature))


@dataclass
class LapResult:
    """A solved lap: the speed trace and everything derived from it."""

    track_name: str
    vehicle_name: str
    s: np.ndarray
    ds: np.ndarray
    v: np.ndarray
    curvature: np.ndarray
    offset: np.ndarray
    ax: np.ndarray
    ay: np.ndarray
    dt: np.ndarray
    lap_time: float
    distance: float
    sector_times: tuple = ()
    limit_mode: np.ndarray = field(default=None)
    sweeps: int = 0
    converged: bool = True

    @property
    def top_speed(self) -> float:
        return float(self.v.max())

    @property
    def min_speed(self) -> float:
        return float(self.v.min())

    @property
    def mean_speed(self) -> float:
        return float(self.distance / self.lap_time)

    @property
    def combined_accel(self) -> np.ndarray:
        """Magnitude of the total acceleration vector, for tyre-energy wear."""
        return np.hypot(self.ax, self.ay)

    def mean_combined_accel(self) -> float:
        """Distance-weighted mean combined acceleration (m/s^2)."""
        return float(np.sum(self.combined_accel * self.ds) / np.sum(self.ds))

    def time_fraction_at_limit(self, tol: float = 0.02) -> float:
        """Share of the lap spent within ``tol`` of the lateral limit."""
        at_limit = self.limit_mode == 0 if self.limit_mode is not None else None
        if at_limit is None:
            return float("nan")
        return float(np.sum(self.dt[at_limit]) / self.lap_time)

    def summary(self) -> str:
        lines = [
            f"{self.vehicle_name} @ {self.track_name}: "
            f"{format_laptime(self.lap_time)}",
            f"  distance     {self.distance:8.1f} m",
            f"  mean speed   {self.mean_speed * 3.6:8.1f} km/h",
            f"  top speed    {self.top_speed * 3.6:8.1f} km/h",
            f"  min speed    {self.min_speed * 3.6:8.1f} km/h",
            f"  peak braking {self.ax.min() / 9.80665:8.2f} g",
            f"  peak lateral {self.ay.max() / 9.80665:8.2f} g",
        ]
        if self.sector_times:
            sectors = "  ".join(f"S{i+1} {t:6.3f}"
                                for i, t in enumerate(self.sector_times))
            lines.append(f"  {sectors}")
        if not self.converged:
            lines.append("  WARNING: speed profile did not converge")
        return "\n".join(lines)


def solve_lap(vehicle: Vehicle, track: Track, offset=None, grip: float = 1.0,
              speed_limit=None, v_start: float | None = None,
              table: AccelerationTable | None = None,
              max_sweeps: int = _DEFAULT_SWEEPS,
              tol: float = 1e-4) -> LapResult:
    """Minimum-time speed profile along a fixed path.

    ``offset`` is the racing line as a lateral displacement from the
    centreline (positive left); ``None`` means drive the centreline.
    ``speed_limit`` is an optional per-point cap in m/s for yellow-flag
    zones, pit lanes or a car in the way. ``v_start`` forces the speed at
    the line -- give it for a standing start, leave it out for a flying lap
    on a closed circuit and the sweeps will find the periodic answer.
    """
    n = len(track)
    if offset is None:
        offset = np.zeros(n)
        ds = track.ds
        kappa = track.curvature
    else:
        offset = np.asarray(offset, dtype=float)
        ds, kappa = track.offset_geometry(offset)

    if table is None or table.grip != grip:
        table = AccelerationTable(vehicle, grip=grip)

    # 1. Lateral limit, then any externally imposed cap (flags, traffic).
    v_lat = np.asarray(vehicle.corner_speed(kappa, grip=grip), dtype=float)
    v_lim = np.minimum(v_lat, table.v_max)
    if speed_limit is not None:
        v_lim = np.minimum(v_lim, np.asarray(speed_limit, dtype=float))
    v_lim = np.maximum(v_lim, _V_FLOOR)

    closed = track.closed
    v = v_lim.copy()
    if v_start is not None:
        v[0] = min(v_start, v_lim[0])

    ds_list = ds.tolist()
    kappa_list = kappa.tolist()
    v_lim_list = v_lim.tolist()

    sweeps = 0
    converged = False
    for sweeps in range(1, max_sweeps + 1):
        v_before = v.copy()
        work = v.tolist()

        # 2. Forward sweep: how hard can the car accelerate out of here?
        stop = n if closed else n - 1
        for i in range(stop):
            j = (i + 1) % n
            a = table.accel_at(work[i], kappa_list[i])
            reachable_sq = work[i] * work[i] + 2.0 * a * ds_list[i]
            reachable = np.sqrt(reachable_sq) if reachable_sq > 0.0 else _V_FLOOR
            if reachable < work[j]:
                work[j] = max(reachable, _V_FLOOR)
        if v_start is not None:
            work[0] = min(v_start, v_lim_list[0])

        # 3. Backward sweep: how late can it still brake for what is coming?
        for i in range(n - 1, 0, -1):
            j = i - 1
            a = table.decel_at(work[i], kappa_list[i])
            reachable_sq = work[i] * work[i] + 2.0 * a * ds_list[j]
            reachable = np.sqrt(reachable_sq) if reachable_sq > 0.0 else _V_FLOOR
            if reachable < work[j]:
                work[j] = max(reachable, _V_FLOOR)
        if closed:
            # Wrap the braking sweep across the line for the periodic answer.
            a = table.decel_at(work[0], kappa_list[0])
            reachable = np.sqrt(max(work[0] ** 2 + 2.0 * a * ds_list[n - 1], 0.0))
            if reachable < work[n - 1]:
                work[n - 1] = max(reachable, _V_FLOOR)
        if v_start is not None:
            work[0] = min(v_start, v_lim_list[0])

        v = np.asarray(work, dtype=float)
        if np.max(np.abs(v - v_before)) < tol:
            converged = True
            break

    # 4. Accelerations implied by the converged profile, and the lap time.
    v_next = np.roll(v, -1) if closed else np.append(v[1:], v[-1])
    ax = (v_next ** 2 - v ** 2) / (2.0 * np.maximum(ds, 1e-9))
    ay = v * v * kappa
    v_mean = 0.5 * (v + v_next)
    dt = ds / np.maximum(v_mean, _V_FLOOR)

    # Which limit is binding where -- 0 lateral, 1 acceleration, 2 braking.
    limit_mode = np.full(n, 1, dtype=np.int8)
    limit_mode[v >= v_lim - 1e-6] = 0
    limit_mode[ax < -1e-6] = 2

    lap_time = float(np.sum(dt))
    distance = float(np.sum(ds))
    sectors = _sector_times(track, np.cumsum(dt) - dt, dt)

    return LapResult(
        track_name=track.name, vehicle_name=vehicle.name,
        s=track.s.copy(), ds=ds, v=v, curvature=kappa, offset=offset,
        ax=ax, ay=ay, dt=dt, lap_time=lap_time, distance=distance,
        sector_times=sectors, limit_mode=limit_mode,
        sweeps=sweeps, converged=converged)


def lap_time(vehicle: Vehicle, track: Track, offset=None, grip: float = 1.0,
             table: AccelerationTable | None = None, **kw) -> float:
    """Lap time alone -- the objective a racing-line optimiser minimises."""
    return solve_lap(vehicle, track, offset=offset, grip=grip,
                     table=table, **kw).lap_time


def _sector_times(track: Track, t_cum, dt) -> tuple:
    """Split the lap at the track's declared sector boundaries."""
    if not track.sector_starts_m:
        return ()
    bounds = list(track.sector_starts_m)
    if bounds and bounds[0] != 0.0:
        bounds = [0.0] + bounds
    edges = [float(np.interp(b, track.s, t_cum)) for b in bounds]
    total = float(np.sum(dt))
    edges.append(total)
    return tuple(edges[i + 1] - edges[i] for i in range(len(edges) - 1))
