#!/usr/bin/env python3
"""Fit the estimated coefficients of a car class to published lap times.

    python3 tools/calibrate.py                  # fit and cross-validate
    python3 tools/calibrate.py --apply          # write the result into the class file
    python3 tools/calibrate.py --target 1.5

Only the coefficients marked ESTIMATED in a class file are touched -- drag,
downforce and tyre friction. Mass, engine and dimensions are regulated and
are not free parameters; moving them to improve a fit would be fitting the
rules to the data.

Two things keep this honest.

**A fixed racing line.** Each circuit's line is solved once and reused for
every trial, so a change in lap time is attributable to the car rather than
to the search wandering somewhere different. The line the fitted car would
actually drive is slightly different, so the fit is confirmed afterwards by
a full re-validation rather than trusted as it stands.

**Leave-one-out cross-validation.** Three parameters against five circuits
will always fit; the question is whether it has learnt anything. Each
circuit is held out in turn, the rest are fitted, and the held-out circuit
is predicted. If the held-out error is much worse than the fitted error, the
parameters are absorbing per-circuit accidents -- aero trim, track
condition, a layout that does not quite match -- and should not be believed.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _log                                            # noqa: E402

from engine.conditions import Conditions               # noqa: E402
from engine.config import load_vehicle_spec            # noqa: E402
from engine.lines import cached_racing_line            # noqa: E402
from engine.qss import solve_lap                       # noqa: E402
from engine.track import Track                         # noqa: E402
from engine.units import format_laptime, parse_laptime  # noqa: E402
from engine.vehicle import Vehicle                     # noqa: E402

# How far inside a race lap record a clean simulated lap should sit. A race
# lap record is the quickest lap anyone managed in a race there: low fuel,
# late in a stint, often with a tow. So it is closer to qualifying pace than
# to a typical race lap, and the margin is smaller than it first looks.
DEFAULT_TARGET_S = 1.0

PARAMETERS = ("drag", "downforce", "tyre grip")
BOUNDS = ([0.70, 0.70, 0.80], [1.40, 1.60, 1.25])


def variant(spec, scales, power_scale: float = 1.0) -> Vehicle:
    """The class with its estimated coefficients scaled."""
    cda_s, cla_s, mu_s = scales
    aero = dataclasses.replace(spec.aero, cda=spec.aero.cda * cda_s,
                               cla=spec.aero.cla * cla_s)
    tyres = dataclasses.replace(spec.tyres, mu_x=spec.tyres.mu_x * mu_s,
                                mu_y=spec.tyres.mu_y * mu_s)
    return Vehicle(dataclasses.replace(spec, aero=aero, tyres=tyres),
                   conditions=Conditions.dry(), power_scale=power_scale)


class Fit:
    """Circuits, their fixed lines, and the residuals a parameter set gives."""

    def __init__(self, spec, entries, ds: float, target: float,
                 line_cache: Path, verbose: bool = True):
        self.spec = spec
        self.target = target
        self.entries = entries
        self.tracks, self.lines, self.published, self.power = [], [], [], []
        baseline = variant(spec, (1.0, 1.0, 1.0))
        for entry in entries:
            name = entry["track"]
            track = Track.from_csv(ROOT / "tracks" / "real" / f"{name}.csv",
                                   name=name, ds=ds)
            era = float(entry.get("era_power_scale", 1.0))
            car = baseline if era == 1.0 else baseline.with_power_scale(era)
            if verbose:
                print(f"  {name:<13} preparing line ...", flush=True)
            line, source = cached_racing_line(car, track, line_cache,
                                              verbose=False)
            if verbose:
                print(f"  {name:<13} line {source}, "
                      f"{format_laptime(line.lap_time)}", flush=True)
            self.tracks.append(track)
            self.lines.append(line.offset)
            self.published.append(parse_laptime(entry["time"]))
            self.power.append(era)

    def lap_times(self, scales, subset=None):
        """Simulated lap on each circuit's fixed line."""
        indices = range(len(self.tracks)) if subset is None else subset
        out = []
        for i in indices:
            car = variant(self.spec, scales, power_scale=self.power[i])
            out.append(solve_lap(car, self.tracks[i],
                                 offset=self.lines[i]).lap_time)
        return np.array(out)

    def residuals(self, scales, subset=None):
        """How far each circuit is from where it should sit, in seconds.

        Positive means the simulation is slower than it ought to be.
        """
        indices = list(range(len(self.tracks)) if subset is None else subset)
        published = np.array([self.published[i] for i in indices])
        wanted = published - self.target
        return self.lap_times(scales, indices) - wanted

    def solve(self, subset=None, start=(1.0, 1.0, 1.0)):
        result = least_squares(lambda x: self.residuals(x, subset),
                               np.asarray(start, dtype=float),
                               bounds=BOUNDS, diff_step=0.02, xtol=1e-8)
        return result.x


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--class", dest="spec", default="classes/lmp2.yaml")
    parser.add_argument("--ds", type=float, default=4.0)
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET_S,
                        help="seconds a clean lap should sit inside the record")
    parser.add_argument("--apply", action="store_true",
                        help="write the fitted coefficients into the class file")
    args = parser.parse_args(argv)

    log_path = _log.start("calibrate", sys.argv)
    print(f"logging to {log_path}\n")

    spec_path = ROOT / args.spec
    spec = load_vehicle_spec(spec_path)
    ref_path = ROOT / "data" / f"reference_laps_{spec.name.lower()}.yaml"
    if not ref_path.is_file():
        ref_path = ROOT / "data" / "reference_laps.yaml"
    with ref_path.open(encoding="utf-8") as fh:
        references = yaml.safe_load(fh)
    entries = [e for e in references["laps"]
               if (ROOT / "tracks" / "real" / f"{e['track']}.csv").is_file()]
    if len(entries) < 3:
        print("need at least three reference circuits", file=sys.stderr)
        return 1

    print(f"calibrating {spec.name} against {len(entries)} circuits, "
          f"target {args.target:+.2f} s inside each record\n")
    fit = Fit(spec, entries, args.ds, args.target, ROOT / "lines")
    names = [e["track"] for e in entries]

    before = fit.residuals((1.0, 1.0, 1.0))
    print(f"\nbefore: rms {np.sqrt(np.mean(before ** 2)):.3f} s, "
          f"worst {np.max(np.abs(before)):.3f} s")

    scales = fit.solve()
    after = fit.residuals(scales)
    print(f"after:  rms {np.sqrt(np.mean(after ** 2)):.3f} s, "
          f"worst {np.max(np.abs(after)):.3f} s\n")

    print(f"{'parameter':<12} {'scale':>7}   {'was':>8} -> {'now':>8}")
    print("-" * 42)
    values = ((spec.aero.cda, "CdA (m2)"), (spec.aero.cla, "ClA (m2)"),
              (spec.tyres.mu_y, "mu_y"))
    for (label, scale, (old, unit)) in zip(PARAMETERS, scales, values):
        print(f"{label:<12} {scale:>7.4f}   {old:>8.3f} -> {old * scale:>8.3f}"
              f"   {unit}")

    print(f"\n{'circuit':<13} {'before':>8} {'after':>8}   published")
    print("-" * 46)
    for i, name in enumerate(names):
        print(f"{name:<13} {before[i]:>+8.3f} {after[i]:>+8.3f}   "
              f"{entries[i]['time']}")

    # -- does it generalise? ---------------------------------------------
    print("\nleave-one-out cross-validation")
    print("  (fit on the others, then predict the held-out circuit)")
    print(f"\n{'held out':<13} {'predicted':>10} {'published':>10} {'error':>8}")
    print("-" * 45)
    held_errors = []
    for i, name in enumerate(names):
        subset = [j for j in range(len(names)) if j != i]
        trained = fit.solve(subset=subset)
        error = float(fit.residuals(trained, [i])[0])
        held_errors.append(error)
        predicted = fit.lap_times(trained, [i])[0]
        print(f"{name:<13} {format_laptime(predicted):>10} "
              f"{entries[i]['time']:>10} {error:>+8.3f}")
    held = np.array(held_errors)
    fitted_rms = float(np.sqrt(np.mean(after ** 2)))
    held_rms = float(np.sqrt(np.mean(held ** 2)))
    print("-" * 45)
    print(f"{'fitted rms':<13} {fitted_rms:>29.3f} s")
    print(f"{'held-out rms':<13} {held_rms:>29.3f} s")

    ratio = held_rms / max(fitted_rms, 1e-9)
    print()
    if ratio > 2.5:
        print(f"Held-out error is {ratio:.1f}x the fitted error. The fit is "
              f"absorbing\nper-circuit accidents rather than learning the "
              f"car. Do not apply it;\nfind out what differs about the "
              f"circuits it cannot predict.")
        verdict = 2
    elif held_rms > 1.5:
        print(f"The fit generalises ({ratio:.1f}x), but a held-out circuit is "
              f"still {held_rms:.2f} s out.\nUsable, and worth understanding "
              f"before it is trusted.")
        verdict = 0
    else:
        print(f"The fit generalises: held-out error {held_rms:.3f} s at "
              f"{ratio:.1f}x the fitted error.")
        verdict = 0

    if args.apply:
        if ratio > 2.5:
            print("\nrefusing --apply: the fit does not generalise")
            return 2
        text = spec_path.read_text(encoding="utf-8")
        for old, new, key in ((spec.aero.cda, spec.aero.cda * scales[0], "cda"),
                              (spec.aero.cla, spec.aero.cla * scales[1], "cla")):
            text = text.replace(f"  {key}: {old}", f"  {key}: {new:.4f}")
        for attr, scale in (("mu_x", scales[2]), ("mu_y", scales[2])):
            old = getattr(spec.tyres, attr)
            text = text.replace(f"  {attr}: {old}", f"  {attr}: {old * scale:.4f}")
        spec_path.write_text(text, encoding="utf-8")
        print(f"\nwrote fitted coefficients to {spec_path}")
        print("confirm with a full re-validation -- the fitted car drives a "
              "slightly\ndifferent line than the one this fit held fixed:")
        print("  make clean-lines && make validate")
    else:
        print("\nre-run with --apply to write these into the class file")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
