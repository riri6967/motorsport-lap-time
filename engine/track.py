"""Track geometry: centreline, curvature, width, and offset paths.

Two ways in. A **segment list** (straights and constant-radius arcs) is how
circuits are described in published corner data, so a track can be built from
figures anyone can look up. A **point list** takes surveyed or GPS centreline
data directly. Both end up as the same thing: arrays sampled at uniform arc
length, which is what the lap-time solver wants.

Sign convention throughout: curvature is positive for a left-hand turn, the
normal points left, and a lateral offset ``n`` is positive to the left of the
centreline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scipy.interpolate import CubicSpline
from scipy.signal import savgol_filter

from .config import ConfigError, load_yaml


def curvature_from_points(x, y, closed: bool = True):
    """Signed curvature of a polyline, via the circle through each point triple.

    The three-point (Menger) construction is exact for a circular arc and
    degrades gracefully on noisy data, which matters because the alternative
    -- differencing twice -- amplifies survey noise into curvature spikes the
    solver would read as corners that are not there.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    if n < 3:
        return np.zeros(n)

    if closed:
        ip, iN = np.roll(np.arange(n), 1), np.roll(np.arange(n), -1)
    else:
        ip = np.clip(np.arange(n) - 1, 0, n - 1)
        iN = np.clip(np.arange(n) + 1, 0, n - 1)

    ax, ay = x[ip], y[ip]
    bx, by = x, y
    cx, cy = x[iN], y[iN]

    # Twice the signed area of the triangle: sign gives the turn direction.
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d_ab = np.hypot(bx - ax, by - ay)
    d_bc = np.hypot(cx - bx, cy - by)
    d_ca = np.hypot(ax - cx, ay - cy)
    denom = d_ab * d_bc * d_ca
    k = np.divide(2.0 * cross, denom, out=np.zeros_like(denom),
                  where=denom > 1e-12)
    if not closed:
        k[0], k[-1] = k[1], k[-2]
    return k


def arclength_from_points(x, y, closed: bool = True):
    """Cumulative arc length along a polyline, starting at zero."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    dx, dy = np.diff(x), np.diff(y)
    steps = np.hypot(dx, dy)
    s = np.concatenate([[0.0], np.cumsum(steps)])
    if closed:
        closing = float(np.hypot(x[0] - x[-1], y[0] - y[-1]))
        return s, float(s[-1] + closing)
    return s, float(s[-1])


@dataclass
class Segment:
    """One straight or constant-radius arc of a circuit description."""

    kind: str            # 'straight' or 'arc'
    length: float        # m, along the centreline
    curvature: float     # 1/m, signed; zero for a straight
    name: str = ""

    @staticmethod
    def from_dict(d: dict, index: int) -> "Segment":
        where = f"segment[{index}]"
        kind = str(d.get("type", "straight")).lower()
        name = str(d.get("name", ""))
        if kind == "straight":
            length = float(d.get("length", 0.0))
            if length <= 0:
                raise ConfigError(f"{where}: straight needs a positive 'length'")
            return Segment("straight", length, 0.0, name)
        if kind != "arc":
            raise ConfigError(f"{where}: 'type' must be 'straight' or 'arc'")
        if "radius" not in d:
            raise ConfigError(f"{where}: arc needs a 'radius'")
        radius = float(d["radius"])
        if radius == 0:
            raise ConfigError(f"{where}: arc radius cannot be zero")
        # A left-hand turn is a positive angle; radius may carry the sign too.
        if "angle" in d:
            angle = np.deg2rad(float(d["angle"]))
            length = abs(angle * radius)
            curvature = np.sign(angle) / abs(radius)
        elif "length" in d:
            length = float(d["length"])
            if length <= 0:
                raise ConfigError(f"{where}: arc 'length' must be positive")
            curvature = 1.0 / radius
        else:
            raise ConfigError(f"{where}: arc needs either 'angle' or 'length'")
        return Segment("arc", length, float(curvature), name)


class Track:
    """A circuit sampled at uniform arc length.

    For a closed circuit the arrays do **not** repeat the start point at the
    end: index ``N-1`` is one step before the line, and the solver wraps.
    """

    def __init__(self, name: str, s, x, y, heading, curvature,
                 w_left, w_right, closed: bool = True,
                 sector_starts_m=(), description: str = "",
                 sources=()):
        self.name = name
        self.description = description
        self.sources = tuple(sources)
        self.s = np.asarray(s, dtype=float)
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self.heading = np.asarray(heading, dtype=float)
        self.curvature = np.asarray(curvature, dtype=float)
        self.w_left = np.broadcast_to(
            np.asarray(w_left, dtype=float), self.s.shape).copy()
        self.w_right = np.broadcast_to(
            np.asarray(w_right, dtype=float), self.s.shape).copy()
        self.closed = bool(closed)
        self.sector_starts_m = tuple(float(v) for v in sector_starts_m)
        self._length = None

    # -- construction ----------------------------------------------------
    @classmethod
    def from_segments(cls, name: str, segments, ds: float = 2.0,
                      width: float = 12.0, width_left=None, width_right=None,
                      closed: bool = True, **kw) -> "Track":
        """Build a centreline by integrating a list of straights and arcs.

        Positions are integrated in closed form per segment rather than by
        stepping a heading, so a 180-degree corner comes out as a true
        semicircle no matter how coarsely it is sampled.
        """
        segs = [s if isinstance(s, Segment) else Segment.from_dict(s, i)
                for i, s in enumerate(segments)]
        total = sum(s.length for s in segs)
        if total <= 0:
            raise ConfigError(f"{name}: track has zero length")

        n_samples = max(3, int(round(total / ds)))
        step = total / n_samples
        s_grid = np.arange(n_samples) * step
        if not closed:
            s_grid = np.linspace(0.0, total, n_samples)

        # Where each segment starts, and the pose there.
        starts = np.cumsum([0.0] + [s.length for s in segs])
        poses = [(0.0, 0.0, 0.0)]
        for seg in segs:
            x0, y0, h0 = poses[-1]
            poses.append(_advance(x0, y0, h0, seg.curvature, seg.length))

        idx = np.clip(np.searchsorted(starts, s_grid, side="right") - 1,
                      0, len(segs) - 1)
        local = s_grid - starts[idx]
        kappa = np.array([segs[i].curvature for i in idx])
        x = np.empty(n_samples)
        y = np.empty(n_samples)
        heading = np.empty(n_samples)
        for j in range(n_samples):
            i = idx[j]
            x0, y0, h0 = poses[i]
            x[j], y[j], heading[j] = _advance(x0, y0, h0, segs[i].curvature,
                                              local[j])

        wl = width / 2.0 if width_left is None else width_left
        wr = width / 2.0 if width_right is None else width_right
        return cls(name, s_grid, x, y, heading, kappa, wl, wr,
                   closed=closed, **kw)

    @classmethod
    def from_points(cls, name: str, x, y, w_left=6.0, w_right=6.0,
                    ds: float | None = 2.0, closed: bool = True,
                    smooth_m: float | None = None, **kw) -> "Track":
        """Build from a surveyed centreline, resampled to uniform spacing.

        ``smooth_m`` low-pass filters the centreline over that distance
        before curvature is taken. Curvature is a second derivative, so
        sub-metre noise in a surveyed trace becomes corners that do not
        exist, and a light filter removes them while moving the centreline
        by centimetres.

        Most of what looks like survey noise is not, though. Resampling
        linearly, as this used to, drops each new sample onto the chord
        between two old ones -- millimetres -- and differentiating that twice
        invents far more curvature than any real GPS error: it was reporting
        86 corners at Monza, tightest radius 8 m, against a real 11 and about
        20. Splining the resample instead brought that to 15 with no
        filtering at all. Reach for a bigger ``smooth_m`` only when the source
        data is genuinely noisy, and check what it does to the geometry first.
        """
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        if len(x) != len(y) or len(x) < 3:
            raise ConfigError(f"{name}: need at least 3 matching x/y points")

        wl = np.broadcast_to(np.asarray(w_left, dtype=float), x.shape).astype(float)
        wr = np.broadcast_to(np.asarray(w_right, dtype=float), x.shape).astype(float)

        if closed and (x[0] != x[-1] or y[0] != y[-1]):
            xs = np.append(x, x[0])
            ys = np.append(y, y[0])
            wls = np.append(wl, wl[0])
            wrs = np.append(wr, wr[0])
        else:
            xs, ys, wls, wrs = x, y, wl, wr

        s_raw = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xs), np.diff(ys)))])
        total = float(s_raw[-1])
        if ds is None:
            s_grid = s_raw[:-1] if closed else s_raw
        else:
            n_samples = max(3, int(round(total / ds)))
            s_grid = (np.arange(n_samples) * (total / n_samples) if closed
                      else np.linspace(0.0, total, n_samples))

        # Resample through a cubic spline, not linearly. Linear interpolation
        # puts the new samples on the chords between the old ones, and the
        # sagitta it drops them by -- millimetres -- is differentiated twice
        # into a curvature ripple worth tens of per cent. The geometry looks
        # untouched and the corners are wrong.
        if len(s_raw) >= 4:
            bc = "periodic" if (closed and xs[0] == xs[-1] and ys[0] == ys[-1]) \
                else "natural"
            xi = CubicSpline(s_raw, xs, bc_type=bc)(s_grid)
            yi = CubicSpline(s_raw, ys, bc_type=bc)(s_grid)
        else:
            xi = np.interp(s_grid, s_raw, xs)
            yi = np.interp(s_grid, s_raw, ys)
        wli = np.interp(s_grid, s_raw, wls)
        wri = np.interp(s_grid, s_raw, wrs)
        if smooth_m:
            step = total / len(s_grid)
            window = int(round(smooth_m / step))
            if window % 2 == 0:
                window += 1
            if window >= 5 and window < len(xi):
                mode = "wrap" if closed else "interp"
                # Quadratic, not cubic: a cubic through a short window is
                # flexible enough to follow the noise it is meant to remove.
                xi = savgol_filter(xi, window, 2, mode=mode)
                yi = savgol_filter(yi, window, 2, mode=mode)

        kappa = curvature_from_points(xi, yi, closed=closed)
        heading = _heading_from_points(xi, yi, closed=closed)
        return cls(name, s_grid, xi, yi, heading, kappa, wli, wri,
                   closed=closed, **kw)

    @classmethod
    def from_csv(cls, path: str | Path, name: str | None = None,
                 ds: float = 3.0, closed: bool = True,
                 order: str = "x,y,w_right,w_left",
                 smooth_m: float | None = 15.0, **kw) -> "Track":
        """Read a surveyed centreline from a plain CSV.

        Four columns: two of position and two of track width either side.
        ``order`` names them, because the two common conventions disagree on
        which width comes first and silently mirroring a circuit is a hard
        mistake to spot afterwards.
        """
        path = Path(path)
        if not path.is_file():
            raise ConfigError(f"no such track file: {path}")
        data = np.loadtxt(path, delimiter=",", comments="#")
        if data.ndim != 2 or data.shape[1] < 4:
            raise ConfigError(f"{path}: expected at least 4 comma-separated columns")
        fields = [f.strip() for f in order.split(",")]
        expected = {"x", "y", "w_left", "w_right"}
        if set(fields) != expected or len(fields) != 4:
            raise ConfigError(f"'order' must name exactly {sorted(expected)}")
        col = {f: data[:, i] for i, f in enumerate(fields)}
        return cls.from_points(name or path.stem, col["x"], col["y"],
                               w_left=col["w_left"], w_right=col["w_right"],
                               ds=ds, closed=closed, smooth_m=smooth_m, **kw)

    @classmethod
    def from_yaml(cls, path: str | Path, ds: float = 2.0) -> "Track":
        """Read a ``tracks/*.yaml`` description."""
        path = Path(path)
        d = load_yaml(path)
        where = str(path)
        allowed = ("name", "description", "closed", "width", "width_left",
                   "width_right", "segments", "points", "sectors", "sources",
                   "reference_lap_times")
        unknown = set(d) - set(allowed)
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
        name = str(d.get("name", path.stem))
        closed = bool(d.get("closed", True))
        common = dict(
            description=str(d.get("description", "")),
            sources=tuple(d.get("sources", ()) or ()),
            sector_starts_m=tuple(d.get("sectors", ()) or ()),
        )
        if "segments" in d:
            return cls.from_segments(
                name, d["segments"], ds=ds,
                width=float(d.get("width", 12.0)),
                width_left=d.get("width_left"), width_right=d.get("width_right"),
                closed=closed, **common)
        if "points" in d:
            pts = np.asarray(d["points"], dtype=float)
            if pts.ndim != 2 or pts.shape[1] not in (2, 4):
                raise ConfigError(
                    f"{where}: 'points' rows must be [x, y] or [x, y, w_left, w_right]")
            if pts.shape[1] == 4:
                return cls.from_points(name, pts[:, 0], pts[:, 1],
                                       pts[:, 2], pts[:, 3], ds=ds,
                                       closed=closed, **common)
            half = float(d.get("width", 12.0)) / 2.0
            return cls.from_points(name, pts[:, 0], pts[:, 1], half, half,
                                   ds=ds, closed=closed, **common)
        raise ConfigError(f"{where}: needs either 'segments' or 'points'")

    # -- geometry --------------------------------------------------------
    def __len__(self) -> int:
        return len(self.s)

    @property
    def ds(self):
        """Spacing between samples (m), wrapping for a closed circuit."""
        step = np.diff(self.s)
        if self.closed:
            step = np.append(step, self.length - self.s[-1])
        else:
            step = np.append(step, step[-1])
        return step

    @property
    def length(self) -> float:
        """Centreline lap distance (m)."""
        if self._length is None:
            if self.closed:
                n = len(self.s)
                self._length = float(self.s[-1] + (self.s[-1] - self.s[0]) / (n - 1)) \
                    if n > 1 else 0.0
            else:
                self._length = float(self.s[-1])
        return self._length

    def normals(self):
        """Unit normals pointing to the left of the direction of travel."""
        return -np.sin(self.heading), np.cos(self.heading)

    def offset_points(self, n):
        """Cartesian points of the path offset ``n`` metres left of centre."""
        nx, ny = self.normals()
        n = np.asarray(n, dtype=float)
        return self.x + n * nx, self.y + n * ny

    def offset_bounds(self, car_width: float = 0.0, margin: float = 0.0):
        """Widest offsets that keep the car inside the white lines."""
        half = 0.5 * car_width + margin
        return -(self.w_right - half), (self.w_left - half)

    def offset_geometry(self, n):
        """Arc-length steps and curvature of the offset path.

        Curvature is measured from the constructed points rather than from
        the Frenet expansion, so it stays valid for the large offsets a
        racing line actually uses -- a metre or two either side of the
        centreline is not a small perturbation on a 25 m hairpin.
        """
        px, py = self.offset_points(n)
        kappa = curvature_from_points(px, py, closed=self.closed)
        if self.closed:
            dx = np.roll(px, -1) - px
            dy = np.roll(py, -1) - py
            ds = np.hypot(dx, dy)
        else:
            ds = np.append(np.hypot(np.diff(px), np.diff(py)), 0.0)
        return ds, kappa

    def closure_error(self) -> float:
        """Gap between the end of the lap and its start (m).

        A circuit reconstructed from published straight lengths and corner
        radii will not close perfectly. This is the honest measure of how
        far the description is from a real survey.
        """
        if not self.closed:
            return 0.0
        step = float(self.length - self.s[-1])
        x_end, y_end, _ = _advance(self.x[-1], self.y[-1],
                                   self.heading[-1], self.curvature[-1], step)
        return float(np.hypot(x_end - self.x[0], y_end - self.y[0]))

    def corners(self, min_curvature: float = 1.0 / 400.0):
        """Contiguous stretches curved enough to count as corners.

        Returns ``(start_m, end_m, min_radius_m, direction)`` per corner,
        which is the form published circuit data comes in and therefore the
        form a track description can be checked against.
        """
        curved = np.abs(self.curvature) >= min_curvature
        if not curved.any():
            return []
        idx = np.arange(len(self.s))
        if self.closed and curved[0] and curved[-1]:
            shift = int(np.argmin(curved))     # rotate to start on a straight
            curved = np.roll(curved, -shift)
            idx = np.roll(idx, -shift)
        out = []
        start = None
        for j, flag in enumerate(curved):
            if flag and start is None:
                start = j
            elif not flag and start is not None:
                out.append(self._corner_record(idx[start:j]))
                start = None
        if start is not None:
            out.append(self._corner_record(idx[start:]))
        return out

    def _corner_record(self, indices):
        k = self.curvature[indices]
        peak = np.max(np.abs(k))
        return (float(self.s[indices[0]]), float(self.s[indices[-1]]),
                float(1.0 / peak) if peak > 0 else float("inf"),
                "left" if np.mean(k) > 0 else "right")

    def summary(self) -> str:
        corner_list = self.corners()
        radii = sorted(c[2] for c in corner_list)
        lines = [
            f"{self.name}: {self.length:.0f} m, {len(self.s)} samples "
            f"@ {self.length / len(self.s):.2f} m",
            f"  corners          {len(corner_list)} "
            f"({sum(1 for c in corner_list if c[3] == 'left')} left, "
            f"{sum(1 for c in corner_list if c[3] == 'right')} right)",
            f"  width            {self.w_left.mean() + self.w_right.mean():.1f} m mean",
        ]
        if radii:
            lines.append(f"  tightest corner  {radii[0]:.0f} m radius")
        if self.closed:
            lines.append(f"  closure error    {self.closure_error():.2f} m")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Track({self.name!r}, {self.length:.0f} m, {len(self.s)} pts)"


def _advance(x0: float, y0: float, h0: float, curvature: float, length: float):
    """Exact pose after travelling ``length`` at constant curvature."""
    if abs(curvature) < 1e-12:
        return x0 + length * np.cos(h0), y0 + length * np.sin(h0), h0
    h1 = h0 + curvature * length
    x1 = x0 + (np.sin(h1) - np.sin(h0)) / curvature
    y1 = y0 - (np.cos(h1) - np.cos(h0)) / curvature
    return x1, y1, h1


def _heading_from_points(x, y, closed: bool = True):
    """Tangent direction at each sample of a polyline."""
    if closed:
        dx = np.roll(x, -1) - np.roll(x, 1)
        dy = np.roll(y, -1) - np.roll(y, 1)
    else:
        dx = np.gradient(x)
        dy = np.gradient(y)
    return np.unwrap(np.arctan2(dy, dx))
