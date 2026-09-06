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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _log                                          # noqa: E402

from engine.conditions import Conditions           # noqa: E402
from engine.config import load_vehicle_spec        # noqa: E402
from engine.qss import solve_lap                   # noqa: E402
from engine.lines import cached_racing_line          # noqa: E402
from engine.racing_line import optimise_racing_line  # noqa: E402
from engine.track import Track                     # noqa: E402
from engine.units import format_laptime, parse_laptime  # noqa: E402
from engine.vehicle import Vehicle                 # noqa: E402

TRACK_CACHE = ROOT / "tracks" / "real"
REFERENCE_FILE = ROOT / "data" / "reference_laps.yaml"
LINE_CACHE = ROOT / "lines"

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
        ds: float = 4.0, schedule=None, verbose: bool = True,
        cache: bool = True, force: bool = False) -> int:
    spec = load_vehicle_spec(spec_path)
    vehicle = Vehicle(spec, conditions=Conditions.dry())

    print(f"class:      {spec.name}  ({vehicle.mass:.0f} kg, "
          f"{spec.powertrain.max_power_w / 1000:.0f} kW)")
    print(f"reference:  {references.get('reference_class', '?')} race lap "
          f"records, retrieved {references.get('retrieved', '?')}")
    print(f"conditions: {vehicle.conditions!r}")
    print(f"line:       {'minimum curvature only' if quick else 'lap-time optimised'}"
          f", track sampled at {ds:.1f} m")
    print()
    print(vehicle.summary())
    print()

    header = (f"{'circuit':<13} {'sim':>9} {'published':>10} {'delta':>8} "
              f"{'':>5}  {'year':>4}  {'layout':<34}")
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
        # Run the car at the power its reference was set under, so a record
        # from an earlier specification is not held against the current one.
        era = float(entry.get("era_power_scale", 1.0))
        car = vehicle if era == 1.0 else vehicle.with_power_scale(era)
        started = time.time()
        if quick:
            line = optimise_racing_line(car, track, refine=False)
            source = "seed"
        elif cache:
            kwargs = {"schedule": schedule} if schedule else {}
            line, source = cached_racing_line(
                car, track, LINE_CACHE, force=force, verbose=verbose,
                **kwargs)
        else:
            kwargs = {"schedule": schedule} if schedule else {}
            line = optimise_racing_line(car, track, verbose=verbose, **kwargs)
            source = "solved"
        elapsed = time.time() - started

        published = parse_laptime(entry["time"])
        delta = published - line.lap_time          # positive: simulation quicker
        rows.append((name, line, published, delta, elapsed, entry, source))
        print(f"{name:<13} {format_laptime(line.lap_time):>9} "
              f"{entry['time']:>10} {delta:>+8.3f} {verdict(delta):>5}  "
              f"{str(entry.get('year', '')):>4}  "
              f"{str(entry.get('layout', ''))[:34]:<34}", flush=True)

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
    for name, line, published, delta, elapsed, entry, source in rows:
        print(f"\n  {name} -- {entry.get('layout', 'layout unrecorded')}")
        print(f"    simulated {format_laptime(line.lap_time)}, "
              f"published {entry['time']} ({entry.get('year', '?')}, "
              f"{entry.get('driver') or 'driver unrecorded'}), {delta:+.3f} s")
        if entry.get("other_layouts"):
            print(f"    not compared against: "
                  f"{'; '.join(entry['other_layouts'][:2])}")
        print(f"    top {line.lap.top_speed * 3.6:.1f} km/h, "
              f"min {line.lap.min_speed * 3.6:.1f} km/h, "
              f"peak {line.lap.ay.max() / 9.80665:.2f} g lateral, "
              f"{-line.lap.ax.min() / 9.80665:.2f} g braking")
        if source == "cache":
            print(f"    line reused from lines/ "
                  f"({line.gain:.3f} s over its seed, {elapsed:.0f} s to check)")
        else:
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
    parser.add_argument("--quiet", dest="verbose", action="store_false",
                        help="suppress the live refinement progress line")
    parser.add_argument("--no-cache", dest="cache", action="store_false",
                        help="always re-solve; do not read or write lines/")
    parser.add_argument("--force", action="store_true",
                        help="re-solve and overwrite any stored line")
    args = parser.parse_args(argv)

    log_path = _log.start("validate", sys.argv)
    print(f"logging to {log_path}  (follow with: tail -f {log_path})\n")

    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = ROOT / spec_path
    if not REFERENCE_FILE.is_file():
        print(f"missing {REFERENCE_FILE}", file=sys.stderr)
        return 2

    return run(spec_path, load_references(REFERENCE_FILE), only=args.tracks,
               quick=args.quick, ds=args.ds, verbose=args.verbose,
               cache=args.cache, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
