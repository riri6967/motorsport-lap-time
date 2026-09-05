#!/usr/bin/env python3
"""Check simulated lap times against published ones.

    python3 tools/validate.py                    # every reference lap
    python3 tools/validate.py --quick            # seed line only, no refinement
    python3 tools/validate.py --track Spa
    python3 tools/validate.py --class classes/lmp2.yaml

What "agreement" means here needs stating, because it is easy to claim more
than the comparison supports. The references are race lap records: set with
fuel aboard, on used tyres, in traffic, on whatever grip the race had left.
A clean single-lap simulation ought to come out somewhat quicker than one --
call it half a second to a second and a half on a ninety-second lap. Landing
exactly on a race lap record is not a success, it is a coincidence or a
thumb on the scale.

So the target band is deliberately one-sided, and the tool says which side of
it each circuit falls on rather than reporting an unsigned error.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.conditions import Conditions           # noqa: E402
from engine.config import load_vehicle_spec        # noqa: E402
from engine.qss import solve_lap                   # noqa: E402
from engine.racing_line import optimise_racing_line  # noqa: E402
from engine.track import Track                     # noqa: E402
from engine.units import format_laptime, parse_laptime  # noqa: E402
from engine.vehicle import Vehicle                 # noqa: E402

TRACK_CACHE = ROOT / "tracks" / "real"
REFERENCE_FILE = ROOT / "data" / "reference_laps.yaml"

# A simulated flying lap should sit inside this window ahead of a race lap
# record. Quicker than the fast edge means the model is optimistic; slower
# than the slow edge means it is leaving time on the table.
EXPECTED_FASTER_BY = (0.0, 2.5)     # seconds


def load_references(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def verdict(delta: float) -> str:
    """``delta`` is how much quicker the simulation is than the reference."""
    low, high = EXPECTED_FASTER_BY
    if delta < low:
        return "SLOW"        # simulation slower than a race lap: too pessimistic
    if delta > high:
        return "FAST"        # quicker than plausible for a clean lap
    return "ok"


def run(spec_path: Path, references: dict, only=None, quick: bool = False,
        ds: float = 4.0, schedule=None, verbose: bool = False) -> int:
    spec = load_vehicle_spec(spec_path)
    vehicle = Vehicle(spec, conditions=Conditions.dry())

    print(f"class:      {spec.name}  ({vehicle.mass:.0f} kg, "
          f"{spec.powertrain.max_power_w / 1000:.0f} kW)")
    print(f"reference:  {references.get('car', '?')} "
          f"({references.get('reference_class', '?')})")
    print(f"conditions: {vehicle.conditions!r}")
    print(f"line:       {'minimum curvature only' if quick else 'lap-time optimised'}"
          f", track sampled at {ds:.1f} m")
    print()
    print(vehicle.summary())
    print()

    header = (f"{'circuit':<13} {'sim':>9} {'published':>10} {'delta':>8} "
              f"{'':>5}  {'series':<7} {'layout':<8}")
    print(header)
    print("-" * len(header))

    rows = []
    for entry in references.get("laps", []):
        name = entry["track"]
        if only and name.lower() not in {o.lower() for o in only}:
            continue
        csv = TRACK_CACHE / f"{name}.csv"
        if not csv.is_file():
            print(f"{name:<13} {'-- no geometry; run tools/fetch_tracks.py --':>40}")
            continue

        track = Track.from_csv(csv, name=name, ds=ds)
        started = time.time()
        if quick:
            line = optimise_racing_line(vehicle, track, refine=False)
        else:
            kwargs = {"schedule": schedule} if schedule else {}
            line = optimise_racing_line(vehicle, track, verbose=verbose, **kwargs)
        elapsed = time.time() - started

        published = parse_laptime(entry["time"])
        delta = published - line.lap_time          # positive: simulation quicker
        rows.append((name, line, published, delta, elapsed, entry))
        print(f"{name:<13} {format_laptime(line.lap_time):>9} "
              f"{entry['time']:>10} {delta:>+8.3f} {verdict(delta):>5}  "
              f"{str(entry.get('series', '')):<7} {str(entry.get('layout', '')):<8}")

    if not rows:
        print("\nnothing to compare -- fetch track geometry first:")
        print("  python3 tools/fetch_tracks.py")
        return 1

    deltas = np.array([r[3] for r in rows])
    print("-" * len(header))
    print(f"{'mean':<13} {'':>9} {'':>10} {deltas.mean():>+8.3f}")
    print(f"{'spread':<13} {'':>9} {'':>10} "
          f"{deltas.max() - deltas.min():>8.3f}")
    print(f"\nsolved in {sum(r[4] for r in rows):.0f} s total")

    print("\nper-circuit detail")
    for name, line, published, delta, elapsed, entry in rows:
        print(f"\n  {name} -- {entry.get('note', '').strip() or 'no notes'}")
        print(f"    simulated {format_laptime(line.lap_time)}, "
              f"published {entry['time']} ({entry.get('year', '?')}), "
              f"{delta:+.3f} s")
        print(f"    top {line.lap.top_speed * 3.6:.1f} km/h, "
              f"min {line.lap.min_speed * 3.6:.1f} km/h, "
              f"peak {line.lap.ay.max() / 9.80665:.2f} g lateral, "
              f"{-line.lap.ax.min() / 9.80665:.2f} g braking")
        print(f"    line gained {line.gain:.3f} s over the seed "
              f"({line.evaluations} evaluations, {elapsed:.0f} s)")

    print(f"\nA simulated flying lap is expected to fall "
          f"{EXPECTED_FASTER_BY[0]:.1f} to {EXPECTED_FASTER_BY[1]:.1f} s "
          f"inside a race lap record.\nSee the caveats at the top of "
          f"{REFERENCE_FILE.relative_to(ROOT)}.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--class", dest="spec", default="classes/lmp2.yaml",
                        help="vehicle class file to validate")
    parser.add_argument("--track", action="append", dest="tracks",
                        help="limit to one circuit (repeatable)")
    parser.add_argument("--quick", action="store_true",
                        help="minimum-curvature line only; seconds, not minutes")
    parser.add_argument("--ds", type=float, default=4.0,
                        help="track sample spacing in metres")
    parser.add_argument("--verbose", action="store_true",
                        help="show refinement progress")
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = ROOT / spec_path
    if not REFERENCE_FILE.is_file():
        print(f"missing {REFERENCE_FILE}", file=sys.stderr)
        return 2

    return run(spec_path, load_references(REFERENCE_FILE), only=args.tracks,
               quick=args.quick, ds=args.ds, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
