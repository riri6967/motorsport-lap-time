"""Finding the line: where to place the car across the width of the track.

Two stages, because the honest objective is badly behaved on its own.

**Minimum curvature** comes first. Straightening the path as much as the
white lines allow is a convex least-squares problem with a global optimum
and no local minima to get stuck in, and it lands within a fraction of a
second of the right answer. It is not the right answer, though: it treats
every corner alike, and a driver does not. Which leads to --

**Minimum lap time**, refining that seed against the actual solver. The
difference is worth having and is entirely about corner exits. The quickest
way through a corner leading onto a long straight is not the quickest way
through the corner; you give up entry speed to open the exit radius and get
on the power sooner, and you carry that gain all the way down the straight.
No curvature-based objective can express that, because it does not know
which way the car is about to be travelling for the next ten seconds.

The line is parameterised as a lateral offset from the centreline, control
points interpolated by a periodic cubic spline. The spline matters: the
offset is differentiated twice to get curvature, so a piecewise-linear
parameterisation would put an infinite curvature spike at every control
point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from scipy import sparse
from scipy.interpolate import CubicSpline
from scipy.optimize import lsq_linear, minimize

from .qss import AccelerationTable, LapResult, solve_lap
from .track import Track
from .units import format_laptime
from .vehicle import Vehicle

DEFAULT_CAR_WIDTH = 1.90     # m; overridden per class where it matters
DEFAULT_MARGIN = 0.10        # m of white line the driver is not using


def _second_difference(n: int, closed: bool) -> sparse.csr_matrix:
    """Discrete second-difference operator, wrapping for a closed circuit."""
    main = -2.0 * np.ones(n)
    off = np.ones(n)
    mat = sparse.diags([off[:-1], main, off[:-1]], [-1, 0, 1],
                       shape=(n, n), format="lil")
    if closed:
        mat[0, n - 1] = 1.0
        mat[n - 1, 0] = 1.0
    else:
        # Endpoints have no second difference; leave their rows empty.
        mat[0, :] = 0.0
        mat[n - 1, :] = 0.0
    return sparse.csr_matrix(mat)


def _solve_box_qp(hessian, linear, lo, hi, x0, max_iter: int = 20000):
    """Minimise ``x'Hx - 2c'x`` inside a box.

    A second-difference operator squared is desperately ill-conditioned, and
    scipy's trust-region least-squares gives up on the first step because the
    residuals (of order ds^2 * curvature) look like noise to it. L-BFGS-B on
    the same problem with an analytic gradient handles the conditioning and
    the bounds together, and every iteration is one sparse mat-vec.
    """
    def objective(x):
        hx = hessian @ x
        return float(x @ hx - 2.0 * linear @ x), 2.0 * (hx - linear)

    result = minimize(objective, x0, jac=True, method="L-BFGS-B",
                      bounds=list(zip(lo, hi)),
                      options={"maxiter": max_iter, "maxfun": 2 * max_iter,
                               "ftol": 1e-16, "gtol": 1e-12})
    return np.clip(result.x, lo, hi)


def _curvature_design(curvature, ds, closed: bool):
    """Linear map from lateral offset to the curvature it produces.

    To first order in the offset ``n``, a path displaced from a reference of
    curvature ``k`` has curvature ``k + n'' + k^2 n``. The ``n''`` term is the
    path bending to get across the track; the ``k^2 n`` term is the reference
    curve itself carrying the offset around a shorter or longer radius.

    Working in curvature rather than in the second difference of the path
    points matters more than it looks. The second difference is ``k ds^2``,
    and ``ds`` shrinks on the inside of a corner -- so minimising it rewards
    hugging the inside kerb, which is very nearly the opposite of a racing
    line. Minimising curvature itself does not have that bias.
    """
    n = len(curvature)
    diff2 = _second_difference(n, closed)
    inv_ds2 = sparse.diags(1.0 / np.maximum(ds, 1e-9) ** 2)
    return (inv_ds2 @ diff2) + sparse.diags(curvature ** 2)


def min_curvature_line(track: Track, car_width: float = DEFAULT_CAR_WIDTH,
                       margin: float = DEFAULT_MARGIN,
                       offset_penalty: float = 1e-6,
                       iterations: int = 3) -> np.ndarray:
    """The path of least curvature that stays between the white lines.

    Minimises the arc-length integral of squared curvature. Convex once
    linearised, and re-linearised about its own answer a few times because
    the linearisation is only good for offsets small against the corner
    radius -- six metres across a 25 m hairpin is not small.

    ``offset_penalty`` is a light pull towards the centreline. It is there
    for conditioning as much as for shape: it makes the problem strictly
    convex, which turns a crawling solve into a quick one, and it settles
    the otherwise arbitrary lateral position along a straight.
    """
    n = len(track)
    lo, hi = track.offset_bounds(car_width, margin)
    lo = np.broadcast_to(np.asarray(lo, dtype=float), (n,)).copy()
    hi = np.broadcast_to(np.asarray(hi, dtype=float), (n,)).copy()
    narrow = hi < lo                       # a track narrower than the car
    if narrow.any():
        mid = 0.5 * (lo[narrow] + hi[narrow])
        lo[narrow] = hi[narrow] = mid

    nx0, ny0 = track.normals()
    px, py = track.x.copy(), track.y.copy()
    offset = np.zeros(n)

    for _ in range(max(1, iterations)):
        # Geometry of the line as it currently stands.
        ds, kappa = track.offset_geometry(offset)
        nx, ny = _normals_from_points(px, py, track.closed)

        design = _curvature_design(kappa, ds, track.closed)
        weight = sparse.diags(np.sqrt(np.maximum(ds, 1e-9)))
        wd = (weight @ design).tocsr()
        hessian = (wd.T @ wd).tocsr()
        linear = -(wd.T @ (weight @ kappa))
        if offset_penalty > 0:
            # Relative to the curvature term, not absolute: the curvature
            # objective is of order 1e-4 per point while offsets are metres,
            # so an absolute penalty of any useful size simply pins the line
            # to the centreline.
            scale = offset_penalty * float(np.mean(hessian.diagonal()))
            hessian = hessian + scale * sparse.identity(n, format="csr")
            linear = linear - scale * offset

        delta = _solve_box_qp(hessian, linear, lo - offset, hi - offset,
                              np.zeros(n))
        px = px + delta * nx
        py = py + delta * ny
        # Re-express the moved path against the original centreline frame,
        # so the answer is always an offset from the track as given.
        offset = np.clip((px - track.x) * nx0 + (py - track.y) * ny0, lo, hi)
        px, py = track.offset_points(offset)

    return offset


def _normals_from_points(x, y, closed: bool):
    """Left-pointing unit normals of a polyline."""
    if closed:
        dx = np.roll(x, -1) - np.roll(x, 1)
        dy = np.roll(y, -1) - np.roll(y, 1)
    else:
        dx, dy = np.gradient(x), np.gradient(y)
    norm = np.maximum(np.hypot(dx, dy), 1e-12)
    return -dy / norm, dx / norm


@dataclass
class RacingLine:
    """An optimised line and the lap it produces."""

    offset: np.ndarray
    lap: LapResult
    seed_lap_time: float
    method: str
    evaluations: int = 0
    history: tuple = field(default=())

    @property
    def lap_time(self) -> float:
        return self.lap.lap_time

    @property
    def gain(self) -> float:
        """Seconds found relative to the seed line."""
        return self.seed_lap_time - self.lap.lap_time

    def summary(self) -> str:
        return "\n".join([
            self.lap.summary(),
            f"  line             {self.method}, {self.evaluations} evaluations",
            f"  gain vs seed     {self.gain:+.3f} s "
            f"(from {format_laptime(self.seed_lap_time)})",
            f"  offset range     {self.offset.min():+.2f} .. "
            f"{self.offset.max():+.2f} m",
        ])


class _BSplineCorrection:
    """A smooth, local, periodic correction added on top of a seed line.

    The refinement optimises a *correction* rather than the line itself, and
    that choice is what makes it work at all. Projecting a minimum-curvature
    line onto a few dozen control points throws away most of what makes it
    good -- measured at nearly a second a lap on a short circuit -- so an
    optimiser parameterised that way spends its whole budget climbing back to
    where it started. As a correction, zero means "the seed, exactly", and
    every evaluation is spent on the part the seed gets wrong.

    The basis is a uniform cubic B-spline, chosen for **local support**: each
    control point touches four knot intervals and nothing beyond. An
    interpolating spline is global -- move one control point and the whole
    lap ripples -- which is fatal for a circuit of twenty corners, because
    the line through Eau Rouge has nothing to say about the line through the
    Bus Stop and should not be able to disturb it.

    Cubic, because curvature is the second derivative of this curve and
    anything less smooth would plant a curvature spike at every knot.
    """

    def __init__(self, track: Track, n_control: int):
        self.track = track
        self.n_control = n_control
        length = track.length
        self.spacing = length / n_control
        knots = np.arange(n_control) * self.spacing
        # Distance from each sample to each knot, wrapped the short way round.
        gap = track.s[:, None] - knots[None, :]
        if track.closed:
            gap = (gap + length / 2.0) % length - length / 2.0
        t = np.abs(gap) / self.spacing
        basis = np.zeros_like(t)
        inner = t < 1.0
        outer = (t >= 1.0) & (t < 2.0)
        basis[inner] = (4.0 - 6.0 * t[inner] ** 2 + 3.0 * t[inner] ** 3) / 6.0
        basis[outer] = (2.0 - t[outer]) ** 3 / 6.0
        self.basis = basis
        # Which samples each control point can reach, for cheap local edits.
        self.support = [np.flatnonzero(basis[:, k] > 1e-12)
                        for k in range(n_control)]

    def expand(self, control: np.ndarray) -> np.ndarray:
        return self.basis @ control

    def apply_one(self, offset: np.ndarray, k: int, amount: float,
                  lo, hi) -> np.ndarray:
        """``offset`` with a single control point nudged, clipped to the track."""
        rows = self.support[k]
        out = offset.copy()
        out[rows] = np.clip(offset[rows] + amount * self.basis[rows, k],
                            lo[rows], hi[rows])
        return out


DEFAULT_SCHEDULE = ((8, 45), (20, 30))
"""Powell levels as ``(control points, evaluations per control point)``.

Coarse only. Powell needs work quadratic in the dimension to turn its
directions over, so it is the right tool for settling the overall shape of
the line and the wrong one for placing twenty individual apexes; the
coordinate sweeps take over from there.
"""

EVALUATIONS_PER_CONTROL_POINT = 120
"""Coordinate-sweep budget, per control point.

Enough for the sweeps to run out of improvements rather than out of budget
on the circuits tried so far. Raising it further is cheap to test and, at
Spa, worth nothing: quadrupling the budget converged at the same line.
"""

DEFAULT_KNOT_SPACING_M = 45.0
"""Knot spacing for the coordinate-descent stage.

This is the number that decides whether refinement does anything at all on a
real circuit. An earlier version refined at a fixed 24 control points, which
on the 7 km of Spa is one knot every 292 m -- a single control point
spanning several corners, unable to move one apex without dragging its
neighbours along. It found six thousandths of a second. At 45 m a knot has
roughly one corner to itself, and the same circuit yields whole seconds.
"""


def _coordinate_sweeps(objective, offset: np.ndarray, basis: _BSplineCorrection,
                       lo, hi, best_time: float, max_evaluations: int,
                       step0: float = 1.5, step_min: float = 0.05,
                       min_sweep_gain: float = 0.004, patience: int = 3,
                       progress: Optional[Callable] = None):
    """Nudge one control point at a time, shrinking the step when stuck.

    Powell's cost grows with the square of the dimension; this grows linearly,
    which is what a circuit needing a hundred-odd control points requires.
    Each trial touches four knot intervals, so the sweeps read as a driver
    working through the lap corner by corner.

    The step halves whenever a sweep stops earning its keep -- either no
    improvement at all, or less than ``min_sweep_gain`` of lap time -- and
    the search gives up once ``patience`` consecutive sweeps have between
    them found less than that. Thousandths of a second are below the
    modelling error by orders of magnitude and not worth a minute of
    grinding.

    ``progress`` is called at each point with a dict describing where the
    search has got to. These sweeps run for minutes on a real circuit, and
    silence for minutes is indistinguishable from a hang.
    """
    evaluations = 0
    step = step0
    sweep = 0
    started = time.time()
    recent: list[float] = []
    while step >= step_min and evaluations < max_evaluations:
        sweep += 1
        improved = 0
        sweep_start_time = best_time
        for k in range(basis.n_control):
            for direction in (1.0, -1.0):
                if evaluations >= max_evaluations:
                    break
                trial = basis.apply_one(offset, k, direction * step, lo, hi)
                if np.array_equal(trial, offset):
                    continue
                trial_time = objective(trial)
                evaluations += 1
                if trial_time < best_time - 1e-6:
                    best_time = trial_time
                    offset = trial
                    improved += 1
                    break
            if progress is not None:
                progress({"stage": "sweep", "sweep": sweep, "step_m": step,
                          "point": k + 1, "points": basis.n_control,
                          "improved": improved, "lap_time": best_time,
                          "evaluations": evaluations,
                          "budget": max_evaluations,
                          "elapsed_s": time.time() - started,
                          "done": False})
        if progress is not None:
            progress({"stage": "sweep", "sweep": sweep, "step_m": step,
                      "point": basis.n_control, "points": basis.n_control,
                      "improved": improved, "lap_time": best_time,
                      "evaluations": evaluations, "budget": max_evaluations,
                      "elapsed_s": time.time() - started, "done": True})
        # Stop on the rate of improvement, not only on the step size. A
        # sweep that finds a thousandth of a second has told us this step is
        # spent, and grinding out several more of them costs a minute for
        # nothing -- which is exactly what the old rule did, halving only
        # when a sweep found literally zero.
        gain = sweep_start_time - best_time
        recent.append(gain)
        if improved == 0 or gain < min_sweep_gain:
            step *= 0.5
        if len(recent) >= patience and sum(recent[-patience:]) < min_sweep_gain:
            break
    return offset, best_time, evaluations


def terminal_progress(min_interval: float = 2.0, stream=None) -> Callable:
    """A progress printer for a terminal: one rewriting line, throttled.

    Prints where the search is, how fast it is going and what it has found,
    rewriting a single line so a long run does not scroll the screen away.
    Sweep boundaries are committed to their own line so the history of the
    search survives above the live one.
    """
    import sys
    stream = stream or sys.stdout
    state = {"last": 0.0, "sweep": 0}
    interactive = hasattr(stream, "isatty") and stream.isatty()

    def show(info: dict) -> None:
        now = time.time()
        boundary = info.get("done")
        if not boundary and now - state["last"] < min_interval:
            return
        state["last"] = now
        rate = info["evaluations"] / max(info["elapsed_s"], 1e-9)
        line = (f"    sweep {info['sweep']:>2d}  step {info['step_m']:4.2f} m  "
                f"point {info['point']:>3d}/{info['points']:<3d}  "
                f"{info['improved']:>3d} improved  "
                f"{format_laptime(info['lap_time'])}  "
                f"{info['evaluations']:>5d}/{info['budget']} evals  "
                f"{info['elapsed_s']:>4.0f}s  {rate:4.1f}/s")
        if interactive and not boundary:
            stream.write("\r" + line.ljust(110))
        else:
            stream.write(("\r" if interactive else "") + line.ljust(110) + "\n")
        stream.flush()

    return show


def optimise_racing_line(
        vehicle: Vehicle, track: Track,
        schedule=DEFAULT_SCHEDULE,
        knot_spacing_m: float = DEFAULT_KNOT_SPACING_M,
        car_width: float = DEFAULT_CAR_WIDTH, margin: float = DEFAULT_MARGIN,
        grip: float = 1.0, seed: Optional[np.ndarray] = None,
        refine: bool = True, speed_limit=None,
        sweep_evaluations: Optional[int] = None,
        callback: Optional[Callable] = None,
        progress: Optional[Callable] = None,
        verbose: bool = False) -> RacingLine:
    # ``callback(offset, lap)`` fires on each new best line, with the solved
    # LapResult, for progress display or for animating the search.
    """Search for the quickest way round.

    Three stages. The minimum-curvature line seeds it. Powell settles the
    broad shape over a handful of control points. Coordinate sweeps then work
    the line corner by corner at roughly one knot per corner, which is where
    almost all of the time on a real circuit is found.

    Set ``refine=False`` to stop at the seed -- a first look at a new circuit
    usually wants that, and it costs a fraction of a second.

    This is a local search. It improves the seed; it does not prove the
    result optimal. For a lap time that is the right trade, since the seed
    solves a convex problem closely related to this one and so starts in the
    right basin.
    """
    n = len(track)
    lo, hi = track.offset_bounds(car_width, margin)
    lo_arr = np.broadcast_to(np.asarray(lo, dtype=float), (n,)).copy()
    hi_arr = np.broadcast_to(np.asarray(hi, dtype=float), (n,)).copy()
    narrow = hi_arr < lo_arr
    if narrow.any():
        mid = 0.5 * (lo_arr[narrow] + hi_arr[narrow])
        lo_arr[narrow] = hi_arr[narrow] = mid

    if seed is None:
        seed = min_curvature_line(track, car_width=car_width, margin=margin)
    seed = np.clip(np.asarray(seed, dtype=float), lo_arr, hi_arr)

    table = AccelerationTable(vehicle, grip=grip)

    def evaluate(offset: np.ndarray) -> LapResult:
        return solve_lap(vehicle, track, offset=offset, grip=grip,
                         table=table, speed_limit=speed_limit)

    seed_lap = evaluate(seed)
    if not refine:
        return RacingLine(offset=seed, lap=seed_lap,
                          seed_lap_time=seed_lap.lap_time,
                          method="minimum curvature", evaluations=1)

    best_offset = seed
    best_time = seed_lap.lap_time
    evaluations = 1
    history = [(1, best_time)]

    def record(offset: np.ndarray, lap: LapResult) -> None:
        history.append((evaluations, lap.lap_time))
        if callback is not None:
            callback(offset, lap)

    # -- coarse shape, by Powell over a global basis ----------------------
    for n_control, per_control in schedule:
        basis = _BSplineCorrection(track, n_control)
        anchor = best_offset.copy()
        level = {"time": best_time, "offset": best_offset}

        def objective(control: np.ndarray) -> float:
            nonlocal evaluations
            offset = np.clip(anchor + basis.expand(control), lo_arr, hi_arr)
            lap = evaluate(offset)
            evaluations += 1
            if lap.lap_time < level["time"]:
                level["time"] = lap.lap_time
                level["offset"] = offset
                record(offset, lap)
            return lap.lap_time

        # Bounds are applied by clipping inside the objective rather than
        # handed to Powell: scipy's bounded Powell gives up early here, and
        # stops a long way short of what the same search finds unbounded.
        minimize(objective, np.zeros(n_control), method="Powell",
                 options={"maxfev": n_control * per_control,
                          "xtol": 1e-2, "ftol": 1e-7})
        best_offset, best_time = level["offset"], level["time"]
        if verbose:
            print(f"    Powell, {n_control:3d} control points -> "
                  f"{format_laptime(best_time)}  ({evaluations} evaluations)",
                  flush=True)

    # -- corner by corner, by coordinate sweeps ---------------------------
    n_fine = max(8, int(round(track.length / knot_spacing_m)))
    fine = _BSplineCorrection(track, n_fine)
    if sweep_evaluations is None:
        # Scale with the number of control points rather than fixing a
        # number: a flat budget that converges on a short circuit stops a
        # long one early. Spa needs about 16000 evaluations to run out of
        # improvements at 156 control points, and a flat 6000 was leaving
        # 0.27 s of it unfound.
        sweep_evaluations = EVALUATIONS_PER_CONTROL_POINT * n_fine

    def sweep_objective(offset: np.ndarray) -> float:
        nonlocal evaluations
        evaluations += 1
        return evaluate(offset).lap_time

    if progress is None and verbose:
        progress = terminal_progress()
    best_offset, best_time, _used = _coordinate_sweeps(
        sweep_objective, best_offset, fine, lo_arr, hi_arr, best_time,
        max_evaluations=sweep_evaluations, progress=progress)
    final_lap = evaluate(best_offset)
    record(best_offset, final_lap)
    if verbose:
        print(f"    sweeps, {n_fine:3d} control points "
              f"({track.length / n_fine:.0f} m apart) -> "
              f"{format_laptime(best_time)}  ({evaluations} evaluations)",
              flush=True)

    levels = "+".join(str(k) for k, _ in schedule)
    return RacingLine(
        offset=best_offset, lap=final_lap,
        seed_lap_time=seed_lap.lap_time,
        method=(f"minimum curvature, Powell at {levels}, "
                f"then {n_fine} sweep points"),
        evaluations=evaluations, history=tuple(history))
