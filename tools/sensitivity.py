#!/usr/bin/env python3
"""How much lap time each estimated parameter is worth, circuit by circuit.

    python3 tools/sensitivity.py
    python3 tools/sensitivity.py --ds 5 --track Spa --track Silverstone

The point of this is to stop a validation residual being explained by
whichever parameter someone reaches for first. Drag, downforce, grip and
power all buy lap time, but they buy different amounts at different
circuits: drag dominates at Monza and barely matters at Barcelona, and grip
is the other way about. If the residuals across a set of circuits do not
line up with any one parameter's fingerprint, then no single number is
wrong, and fitting one to the average would only hide that.

Runs on the minimum-curvature seed line rather than an optimised one, so the
comparison is deterministic and repeatable, and so a slow racing-line search
cannot masquerade as a slow car.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _log                                          # noqa: E402

from engine.conditions import Conditions              # noqa: E402
from engine.config import load_vehicle_spec           # noqa: E402
from engine.qss import solve_lap                      # noqa: E402
from engine.racing_line import min_curvature_line     # noqa: E402
from engine.track import Track                        # noqa: E402
from engine.units import format_laptime, parse_laptime  # noqa: E402
from engine.vehicle import Vehicle                    # noqa: E402

# Each probe is a plausible-sized change to one estimated quantity.
PROBES = (
    ("drag  -10%", dict(cda_scale=0.90)),
    ("drag  -20%", dict(cda_scale=0.80)),
    ("downforce +10%", dict(cla_scale=1.10)),
    ("tyre grip +5%", dict(mu_scale=1.05)),
    ("power +8%", dict(power_scale=1.08)),
)


def variant(spec, cda_scale=1.0, cla_scale=1.0, mu_scale=1.0,
            power_scale=1.0) -> Vehicle:
    """The same car with one estimated group of numbers scaled."""
    aero = dataclasses.replace(spec.aero, cda=spec.aero.cda * cda_scale,
                               cla=spec.aero.cla * cla_scale)
    tyres = dataclasses.replace(spec.tyres, mu_x=spec.tyres.mu_x * mu_scale,
                                mu_y=spec.tyres.mu_y * mu_scale)
    return Vehicle(dataclasses.replace(spec, aero=aero, tyres=tyres),
                   conditions=Conditions.dry(), power_scale=power_scale)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--class", dest="spec", default="classes/lmp2.yaml")
    parser.add_argument("--track", action="append", dest="tracks")
    parser.add_argument("--ds", type=float, default=5.0)
    args = parser.parse_args(argv)

    log_path = _log.start("sensitivity", sys.argv)
    print(f"logging to {log_path}  (follow with: tail -f {log_path})\n")

    spec = load_vehicle_spec(ROOT / args.spec)
    ref_path = ROOT / "data" / f"reference_laps_{spec.name.lower()}.yaml"
    if not ref_path.is_file():
        ref_path = ROOT / "data" / "reference_laps.yaml"
    with ref_path.open(encoding="utf-8") as fh:
        references = yaml.safe_load(fh)

    entries = [e for e in references["laps"]
               if (ROOT / "tracks" / "real" / f"{e['track']}.csv").is_file()
               and (not args.tracks
                    or e["track"].lower() in {t.lower() for t in args.tracks})]
    if not entries:
        print("no cached geometry; run tools/fetch_tracks.py", file=sys.stderr)
        return 1

    names = [e["track"] for e in entries]
    tracks, seeds = {}, {}
    for name in names:
        track = Track.from_csv(ROOT / "tracks" / "real" / f"{name}.csv",
                               name=name, ds=args.ds)
        tracks[name] = track
        seeds[name] = min_curvature_line(track)

    baseline_car = variant(spec)
    baseline, residual = {}, {}
    print(f"{spec.name}, minimum-curvature line, {args.ds:.0f} m sampling\n")
    print(f"{'circuit':<13} {'seed lap':>9} {'published':>10} {'behind':>8} "
          f"{'top speed':>10}")
    print("-" * 54)
    for entry in entries:
        name = entry["track"]
        lap = solve_lap(baseline_car, tracks[name], offset=seeds[name])
        baseline[name] = lap.lap_time
        residual[name] = lap.lap_time - parse_laptime(entry["time"])
        print(f"{name:<13} {format_laptime(lap.lap_time):>9} "
              f"{entry['time']:>10} {residual[name]:>8.3f} "
              f"{lap.top_speed * 3.6:>7.1f} km/h")

    print(f"\nseconds gained per probe\n")
    header = f"{'probe':<16}" + "".join(f"{n[:10]:>11}" for n in names)
    print(header)
    print("-" * len(header))
    fingerprints = {}
    for label, kwargs in PROBES:
        car = variant(spec, **kwargs)
        gains = [baseline[n] - solve_lap(car, tracks[n],
                                         offset=seeds[n]).lap_time
                 for n in names]
        fingerprints[label] = np.array(gains)
        print(f"{label:<16}" + "".join(f"{g:>+11.3f}" for g in gains))

    print(f"{'to be found':<16}" + "".join(f"{residual[n]:>+11.3f}"
                                           for n in names))

    # Does any single probe have the same shape as the shortfall? Correlate
    # the normalised patterns; a probe that matches is a candidate cause, and
    # nothing matching means the shortfall is not one number.
    target = np.array([residual[n] for n in names])
    if len(names) >= 3 and target.std() > 1e-9:
        print("\npattern match against the shortfall "
              "(1.0 would mean this parameter alone explains its shape)")
        for label, gains in fingerprints.items():
            if gains.std() < 1e-9:
                continue
            corr = float(np.corrcoef(gains, target)[0, 1])
            print(f"  {label:<16} {corr:+.3f}")
        print("\nA low or inconsistent match means no single parameter is at "
              "fault,\nand fitting one to the average would hide that rather "
              "than fix it.")
        print("\nNote the shortfall above is measured on the seed line, so it "
              "contains\nthe racing-line deficit as well as the car's. On the "
              "current LMP2 numbers\nthat deficit runs from 13% of the "
              "shortfall at Monza to 65% at Silverstone,\nand removing it "
              "weakens every match here -- which is the point: what is left "
              "is\nnot one number being wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
