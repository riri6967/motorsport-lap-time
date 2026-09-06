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

        # Flat Python lists for the sweep loop. Indexing a numpy array with
        # scalars builds a numpy scalar object every time, which at a few
        # thousand lookups per lap is most of the solver's runtime; plain
        # lists of floats are several times quicker.
        self.n_frac = n_frac
        self.flat_grip = self.accel_grip.ravel().tolist()
        self.flat_decel = self.decel.ravel().tolist()
        self.flat_engine = self.accel_engine.tolist()
        self.n_speed = n_speed

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
    drive_energy_j: float = 0.0
    fuel_burn_kg: float = 0.0

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
            f"  fuel burnt   {self.fuel_burn_kg:8.2f} kg",
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
    kappa_abs = np.abs(kappa).tolist()
    v_lim_list = v_lim.tolist()

    sweeps, converged = _run_sweeps(
        v, v_lim_list, ds_list, kappa_abs, table, closed, v_start,
        max_sweeps, tol)

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

    # Work the engine actually did: what it took to accelerate the car plus
    # what drag and rolling resistance took away, counted only where the
    # engine was driving. Under braking the car is giving energy up, not
    # spending it, and a lap that coasts is not a lap that burns fuel.
    resist = vehicle.aero.drag(v) + vehicle.spec.rolling_resistance * \
        vehicle.normal_load(v)
    drive_force = vehicle.mass * ax + resist
    drive_energy = float(np.sum(np.maximum(drive_force, 0.0) * ds))
    powertrain = vehicle.spec.powertrain
    fuel_burn = drive_energy / max(
        powertrain.thermal_efficiency * powertrain.fuel_energy_mj_per_kg * 1e6,
        1e-9)
    sectors = _sector_times(track, np.cumsum(dt) - dt, dt)

    return LapResult(
        track_name=track.name, vehicle_name=vehicle.name,
        s=track.s.copy(), ds=ds, v=v, curvature=kappa, offset=offset,
        ax=ax, ay=ay, dt=dt, lap_time=lap_time, distance=distance,
        sector_times=sectors, limit_mode=limit_mode,
        sweeps=sweeps, converged=converged,
        drive_energy_j=drive_energy, fuel_burn_kg=fuel_burn)


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


def _run_sweeps(v, v_lim_list, ds_list, kappa_abs, table, closed, v_start,
                max_sweeps: int, tol: float):
    """Forward and backward sweeps, with the envelope lookups inlined.

    This is the solver's hot loop and it is written flat on purpose. Every
    quantity the vehicle model would compute per point is hoisted into a
    local, the tables are plain lists, and the bilinear interpolation is
    spelled out -- the same code expressed as method calls spends most of its
    time in call overhead rather than arithmetic.
    """
    n = len(v)
    grip_tab = table.flat_grip
    decel_tab = table.flat_decel
    engine_tab = table.flat_engine
    n_frac = table.n_frac
    n_speed = table.n_speed
    inv_dv = 1.0 / table._dv
    inv_df = 1.0 / table._df
    v_ceiling = float(table.v_grid[-1])
    mg = table._mg
    mass = table._mass
    fz_ref = table._fz_ref
    k_load = table._k_load
    mu_y = table._mu_y_eff
    const_cla = table._const_cla
    ellipse_p = table._ellipse_p
    circular = abs(ellipse_p - 2.0) < 1e-12
    aero = table._aero
    v_floor = _V_FLOOR

    work = v.tolist()
    sweeps = 0
    converged = False

    for sweeps in range(1, max_sweeps + 1):
        before = list(work)

        for direction in (0, 1):
            order = range(n if closed else n - 1) if direction == 0 \
                else range(n - 1, -1 if closed else 0, -1)
            for i in order:
                if direction == 0:
                    j = i + 1
                    if j == n:
                        j = 0
                    step = ds_list[i]
                else:
                    j = i - 1
                    if j < 0:
                        j = n - 1
                    step = ds_list[j]

                vi = work[i]
                # -- lateral limit, exactly (the ellipse is singular here) --
                if const_cla is not None:
                    fz = mg + const_cla * vi * vi
                else:
                    fz = mg + float(aero.downforce(vi))
                ay_max = mu_y * (fz / fz_ref) ** (-k_load) * fz / mass
                u = vi * vi * kappa_abs[i] / ay_max
                if u > 1.0:
                    u = 1.0
                if circular:
                    frac = (1.0 - u * u) ** 0.5
                else:
                    rem = 1.0 - u ** ellipse_p
                    frac = 0.0 if rem <= 0.0 else rem ** (1.0 / ellipse_p)

                # -- bilinear lookup on (speed, ellipse fraction) --
                fv = vi * inv_dv
                if fv < 0.0:
                    fv = 0.0
                elif fv > v_ceiling * inv_dv:
                    fv = v_ceiling * inv_dv
                iv = int(fv)
                if iv > n_speed - 2:
                    iv = n_speed - 2
                tv = fv - iv
                ff = frac * inv_df
                jf = int(ff)
                if jf > n_frac - 2:
                    jf = n_frac - 2
                tf = ff - jf
                base = iv * n_frac + jf
                table_src = grip_tab if direction == 0 else decel_tab
                a00 = table_src[base]
                a01 = table_src[base + 1]
                a10 = table_src[base + n_frac]
                a11 = table_src[base + n_frac + 1]
                accel = ((1.0 - tv) * ((1.0 - tf) * a00 + tf * a01)
                         + tv * ((1.0 - tf) * a10 + tf * a11))
                if direction == 0:
                    engine = ((1.0 - tv) * engine_tab[iv]
                              + tv * engine_tab[iv + 1])
                    if engine < accel:
                        accel = engine

                reach_sq = vi * vi + 2.0 * accel * step
                reach = reach_sq ** 0.5 if reach_sq > 0.0 else v_floor
                if reach < v_floor:
                    reach = v_floor
                if reach < work[j]:
                    work[j] = reach

            if v_start is not None:
                start = v_start if v_start < v_lim_list[0] else v_lim_list[0]
                work[0] = start

        biggest = 0.0
        for i in range(n):
            delta = work[i] - before[i]
            if delta < 0.0:
                delta = -delta
            if delta > biggest:
                biggest = delta
        if biggest < tol:
            converged = True
            break

    v[:] = work
    return sweeps, converged
