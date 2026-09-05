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


class _SplineCorrection:
    """A smooth, periodic correction added on top of a seed line.

    The refinement optimises a *correction* rather than the line itself, and
    that choice is what makes it work at all. Projecting a minimum-curvature
    line onto a few dozen control points throws away most of what makes it
    good -- measured at nearly a second a lap on a short circuit -- so an
    optimiser parameterised that way spends its whole budget climbing back to
    where it started. As a correction, zero means "the seed, exactly", and
    every evaluation is spent on the part the seed gets wrong.

    That part is also genuinely low-frequency: the difference between the
    least-curvature line and the quickest one is a smooth shifting of apexes
    towards corner exits, so a coarse basis is the right shape for it.

    Cubic, because curvature is the second derivative of this curve and a
    piecewise-linear correction would plant a curvature spike at every knot.
    """

    def __init__(self, track: Track, n_control: int):
        self.track = track
        self.n_control = n_control
        self.knots = np.linspace(0.0, track.length, n_control, endpoint=False)
        self._extended = np.append(self.knots, track.length)
        self._closed = track.closed

    def expand(self, control: np.ndarray) -> np.ndarray:
        if self._closed:
            values = np.append(control, control[0])
            spline = CubicSpline(self._extended, values, bc_type="periodic")
        else:
            values = np.append(control, control[-1])
            spline = CubicSpline(self._extended, values, bc_type="natural")
        return spline(self.track.s)


DEFAULT_SCHEDULE = ((6, 60), (12, 50), (24, 40))
"""Refinement levels as ``(control points, evaluations per control point)``.

Coarse first: a handful of control points settles the overall shape cheaply,
and each finer level starts from the previous answer and only adds detail.
Going straight to a fine basis wastes most of the budget resolving structure
the coarse levels would have found in a fraction of the evaluations.
"""


def optimise_racing_line(
        vehicle: Vehicle, track: Track,
        schedule=DEFAULT_SCHEDULE,
        car_width: float = DEFAULT_CAR_WIDTH, margin: float = DEFAULT_MARGIN,
        grip: float = 1.0, seed: Optional[np.ndarray] = None,
        refine: bool = True, speed_limit=None,
        callback: Optional[Callable] = None,
        verbose: bool = False) -> RacingLine:
    """Search for the quickest way round.

    Seeds with the minimum-curvature line, then refines it against real lap
    times through a coarse-to-fine sequence of smooth corrections. Set
    ``refine=False`` to stop at the seed, which is what a first look at a new
    circuit usually wants and costs a fraction of a second.

    The refinement is a local search: it improves the seed, it does not
    prove the result is a global optimum. For a lap time that is the right
    trade -- the seed is a convex solution to a closely related problem, so
    it starts in the right basin.
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

    for n_control, per_control in schedule:
        basis = _SplineCorrection(track, n_control)
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
                history.append((evaluations, lap.lap_time))
                if callback is not None:
                    callback(offset, lap)
            return lap.lap_time

        # Bounds are applied by clipping inside the objective rather than
        # handed to Powell: scipy's bounded Powell gives up early here, and
        # stops a long way short of what the same search finds unbounded.
        minimize(objective, np.zeros(n_control), method="Powell",
                 options={"maxfev": n_control * per_control,
                          "xtol": 1e-2, "ftol": 1e-7})

        best_offset, best_time = level["offset"], level["time"]
        if verbose:
            print(f"    {n_control:3d} control points -> "
                  f"{format_laptime(best_time)}  ({evaluations} evaluations)")

    levels = "+".join(str(k) for k, _ in schedule)
    return RacingLine(
        offset=best_offset, lap=evaluate(best_offset),
        seed_lap_time=seed_lap.lap_time,
        method=f"minimum curvature, refined at {levels} control points",
        evaluations=evaluations, history=tuple(history))
