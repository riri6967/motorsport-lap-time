#!/usr/bin/env python3
"""Run a race stint and show how the car changes through it.

    python3 tools/run_stint.py --track Spa
    python3 tools/run_stint.py --track Monza --laps 30 --plot

A stint is where tyre degradation and fuel burn argue with each other. The
car gets lighter and quicker; the tyres go away and it gets slower. Which
wins, and when they cross over, is the whole of a pit strategy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _log                                            # noqa: E402

from engine.conditions import Conditions               # noqa: E402
from engine.config import load_vehicle_spec            # noqa: E402
from engine.lines import cached_racing_line            # noqa: E402
from engine.stint import simulate_stint                # noqa: E402
from engine.track import Track                         # noqa: E402
from engine.units import format_laptime                # noqa: E402
from engine.vehicle import Vehicle                     # noqa: E402


def plot_stint(result, out: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    numbers = [lap.number for lap in result.laps]
    times = [lap.lap_time for lap in result.laps]
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                                  constrained_layout=True)
    ax.plot(numbers, times, "o-", ms=3, color="#1f77b4")
    best = result.best
    ax.axvline(best.number, color="crimson", lw=0.9, ls="--")
    ax.annotate(f"quickest lap {best.number}", (best.number, best.lap_time),
                textcoords="offset points", xytext=(8, 10), color="crimson",
                fontsize=9)
    ax.set_ylabel("lap time (s)")
    ax.set_title(f"{result.vehicle_name} stint at {result.track_name} -- "
                 f"{result.count} laps, {result.ended_because}")
    ax.grid(alpha=0.25)

    ax2.plot(numbers, [lap.grip for lap in result.laps], color="#d62728",
             label="tyre grip")
    ax2.set_ylabel("grip multiplier", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")
    ax2.grid(alpha=0.25)
    fuel_axis = ax2.twinx()
    fuel_axis.plot(numbers, [lap.fuel_start_kg for lap in result.laps],
                   color="#2ca02c", label="fuel")
    fuel_axis.set_ylabel("fuel (kg)", color="#2ca02c")
    fuel_axis.tick_params(axis="y", labelcolor="#2ca02c")
    ax2.set_xlabel("lap")

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--track", default="Spa")
    parser.add_argument("--class", dest="spec", default="classes/lmp2.yaml")
    parser.add_argument("--laps", type=int, default=None,
                        help="lap limit; without it the stint runs to fuel")
    parser.add_argument("--fuel", type=float, default=None,
                        help="starting fuel in kg (default: tank capacity)")
    parser.add_argument("--ds", type=float, default=5.0)
    parser.add_argument("--centreline", action="store_true",
                        help="skip the racing line and drive the centreline")
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args(argv)

    log_path = _log.start("run_stint", sys.argv)
    print(f"logging to {log_path}\n")

    vehicle = Vehicle(load_vehicle_spec(ROOT / args.spec),
                      conditions=Conditions.dry())
    csv = ROOT / "tracks" / "real" / f"{args.track}.csv"
    if not csv.is_file():
        print(f"no geometry for {args.track}; run tools/fetch_tracks.py",
              file=sys.stderr)
        return 1
    track = Track.from_csv(csv, name=args.track, ds=args.ds)

    offset = None
    if not args.centreline:
        print("preparing the racing line ...", flush=True)
        line, source = cached_racing_line(vehicle, track, ROOT / "lines")
        offset = line.offset
        print(f"  line {source}: {format_laptime(line.lap_time)}\n")

    capacity = vehicle.spec.mass.fuel_capacity_kg
    fuel = args.fuel if args.fuel is not None else (capacity or 55.0)
    print(f"{vehicle.name} at {track.name}, {fuel:.1f} kg of fuel\n")
    print(f"{'lap':>4} {'time':>10} {'delta':>8} {'grip':>7} {'fuel':>7} "
          f"{'mass':>7}")
    print("-" * 48)

    reference = {"value": None}

    def show(lap):
        if reference["value"] is None:
            reference["value"] = lap.lap_time
        print(f"{lap.number:>4} {format_laptime(lap.lap_time):>10} "
              f"{lap.lap_time - reference['value']:>+8.3f} {lap.grip:>7.4f} "
              f"{lap.fuel_start_kg:>7.1f} {lap.mass_kg:>7.1f}", flush=True)

    result = simulate_stint(vehicle, track, offset=offset, laps=args.laps,
                            fuel_kg=fuel, progress=show)
    print()
    print(result.summary())

    if args.plot:
        out = plot_stint(result, ROOT / "out" / f"stint-{track.name}.png")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
