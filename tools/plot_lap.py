#!/usr/bin/env python3
"""Draw a solved lap: the line taken, and what the car was doing on it.

    python3 tools/plot_lap.py --track Monza
    python3 tools/plot_lap.py --track Spa --quick --out spa.png
    python3 tools/plot_lap.py --track-file tracks/oval.yaml

Three panels. The circuit with the racing line coloured by speed, which is
where a wrong answer usually shows itself -- a line that clips a kerb it
should not, or a corner taken flat that should not be. The speed trace
against distance, with the reference lap time if there is one. And the
friction usage, showing how much of the tyre is being spent sideways against
how much is left for braking and acceleration.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _log                                          # noqa: E402

from engine.conditions import Conditions              # noqa: E402
from engine.config import load_vehicle_spec           # noqa: E402
from engine.lines import cached_racing_line           # noqa: E402
from engine.racing_line import optimise_racing_line   # noqa: E402
from engine.track import Track                        # noqa: E402
from engine.units import G, format_laptime            # noqa: E402
from engine.vehicle import Vehicle                    # noqa: E402


def load_track(args) -> Track:
    if args.track_file:
        path = Path(args.track_file)
        if not path.is_absolute():
            path = ROOT / path
        if path.suffix == ".csv":
            return Track.from_csv(path, ds=args.ds)
        return Track.from_yaml(path, ds=args.ds)
    path = ROOT / "tracks" / "real" / f"{args.track}.csv"
    if not path.is_file():
        raise SystemExit(f"no geometry for {args.track}; run:\n"
                         f"  python3 tools/fetch_tracks.py {args.track}")
    return Track.from_csv(path, name=args.track, ds=args.ds)


def coloured_line(ax, x, y, values, cmap="viridis", width=2.4):
    """A polyline whose colour follows a per-point value."""
    points = np.array([x, y]).T.reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)
    lc = LineCollection(segments, cmap=cmap, linewidths=width)
    lc.set_array(values[:-1])
    ax.add_collection(lc)
    return lc


def plot(vehicle: Vehicle, track: Track, line, out: Path,
         reference: str | None = None) -> Path:
    lap = line.lap
    speed_kmh = lap.v * 3.6
    px, py = track.offset_points(line.offset)
    nx, ny = track.normals()
    lo, hi = track.offset_bounds(0.0, 0.0)

    fig = plt.figure(figsize=(13, 9.5), constrained_layout=True)
    grid = fig.add_gridspec(3, 3, height_ratios=[2.3, 1.0, 1.0],
                            width_ratios=[1.0, 1.0, 1.0])

    # -- the circuit ----------------------------------------------------
    ax = fig.add_subplot(grid[0, :2])
    ax.plot(track.x + hi * nx, track.y + hi * ny, color="0.75", lw=0.9)
    ax.plot(track.x + lo * nx, track.y + lo * ny, color="0.75", lw=0.9)
    ax.plot(track.x, track.y, color="0.88", lw=0.7, ls=(0, (6, 6)))
    lc = coloured_line(ax, px, py, speed_kmh)
    fig.colorbar(lc, ax=ax, label="speed (km/h)", pad=0.01, fraction=0.03)
    slowest = int(np.argmin(lap.v))
    ax.plot(px[slowest], py[slowest], "o", ms=6, mfc="none", mec="crimson")
    ax.annotate(f"slowest {speed_kmh[slowest]:.0f} km/h",
                (px[slowest], py[slowest]), textcoords="offset points",
                xytext=(9, 9), fontsize=8, color="crimson")
    ax.set_aspect("equal")
    ax.set_axis_off()
    title = (f"{vehicle.name} at {track.name} -- "
             f"{format_laptime(lap.lap_time)}")
    if reference:
        title += f"   (published {reference})"
    ax.set_title(title, fontsize=13)

    # -- the friction ellipse, as actually used -------------------------
    ax4 = fig.add_subplot(grid[0, 2])
    ax4.scatter(lap.ay / G, lap.ax / G, s=3, alpha=0.35, color="#444444")
    ax4.axhline(0, color="0.8", lw=0.7)
    ax4.axvline(0, color="0.8", lw=0.7)
    ax4.set_xlabel("lateral (g)")
    ax4.set_ylabel("longitudinal (g)")
    ax4.set_title("friction usage", fontsize=10)
    ax4.grid(alpha=0.25)

    # -- speed ----------------------------------------------------------
    ax2 = fig.add_subplot(grid[1, :])
    ax2.plot(lap.s, speed_kmh, color="#1f77b4", lw=1.2)
    ax2.fill_between(lap.s, 0, speed_kmh, color="#1f77b4", alpha=0.10)
    ax2.set_ylabel("speed (km/h)")
    ax2.set_xlim(0, track.length)
    ax2.set_ylim(0, speed_kmh.max() * 1.08)
    ax2.grid(alpha=0.25)
    for start, end, radius, hand in track.corners(min_curvature=1 / 250.0):
        ax2.axvspan(start, end, color="0.85", alpha=0.5, lw=0)

    # -- what the tyres are doing ---------------------------------------
    ax3 = fig.add_subplot(grid[2, :])
    ax3.plot(lap.s, lap.ay / G, color="#d62728", lw=0.9, label="lateral")
    ax3.plot(lap.s, lap.ax / G, color="#2ca02c", lw=0.9, label="longitudinal")
    ax3.set_xlabel("distance (m)")
    ax3.set_ylabel("acceleration (g)")
    ax3.set_xlim(0, track.length)
    ax3.grid(alpha=0.25)
    ax3.legend(fontsize=8, loc="upper right", ncol=2)

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--track", default="Monza", help="cached circuit name")
    parser.add_argument("--track-file", help="path to a track yaml or csv")
    parser.add_argument("--class", dest="spec", default="classes/lmp2.yaml")
    parser.add_argument("--ds", type=float, default=4.0)
    parser.add_argument("--quick", action="store_true",
                        help="minimum-curvature line only")
    parser.add_argument("--out", help="output png path")
    parser.add_argument("--force", action="store_true",
                        help="re-solve the line instead of reusing lines/")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the live refinement progress line")
    args = parser.parse_args(argv)

    log_path = _log.start("plot_lap", sys.argv)
    print(f"logging to {log_path}  (follow with: tail -f {log_path})\n")

    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = ROOT / spec_path
    vehicle = Vehicle(load_vehicle_spec(spec_path), conditions=Conditions.dry())
    track = load_track(args)

    print(f"solving {track.name} ({track.length:.0f} m) for {vehicle.name} ...")
    if args.quick:
        line = optimise_racing_line(vehicle, track, refine=False)
    else:
        line, _source = cached_racing_line(
            vehicle, track, ROOT / "lines", force=args.force,
            verbose=not args.quiet)
    print(line.summary())

    out = Path(args.out) if args.out else ROOT / "out" / f"{track.name}.png"
    if not out.is_absolute():
        out = ROOT / out
    plot(vehicle, track, line, out)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
