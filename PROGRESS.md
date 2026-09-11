# Progress

## Status: ENGINE + FIRST TWO CLASSES + ENDURANCE SCENARIO WORKING

The solver, the racing-line optimiser, the validation harness, a race stint
(fuel burn + tyre wear together), and the multi-class endurance scenario
(traffic, FCY, day/night, pit strategy) all work end to end, for LMP2 and
GT3. The car models are not yet calibrated against the reference laps, and
say so.

**This entry corrects the previous one**, which was stale: the two commits
before this session (GT3 + fuel/stints) landed without a PROGRESS.md update,
so the "Next up" list below no longer matched the repo. Everything in it has
now been checked against what the code actually does, not just against the
old list.

## Next up

- [ ] Same calibration pass for GT3, once LMP2's is settled and the
      approach is trusted. `tools/calibrate.py --class classes/gt3.yaml`
      already works generically; nothing GT3-specific is needed to try it.
- [ ] **Decide how to handle per-circuit aero trim** — still genuinely
      open, not just stale. `classes/lmp2.yaml`'s docstring already commits
      to the coarse answer (a different aero *package*, e.g. the low-drag
      Le Mans kit, is a different class file — see its `description:`
      field), which sidesteps needing an in-file per-circuit override. What
      is still missing is that mechanism for anything finer-grained than a
      whole new file: e.g. Monza and Spa both run LMP2 in the same sprint
      package but at somewhat different levels of wing, and that has no
      home yet. Not blocking anything today because only one package
      (LMP2 sprint) is modelled.
- [ ] Add IndyCar, next in CLAUDE.md's build order after LMP2 and GT3.
- [ ] `scenarios/lemans24h.py` currently only reports a text classification.
      A plot exists (`--plot`, lap-time-vs-hour with FCY shading and the
      day/night temperature curve) but has only been eyeballed on a 2-hour
      test run, not checked against `out/`. Worth a look at 24 h scale
      before trusting the pit-cycle spacing over a full race.
- [ ] Real Circuit de la Sarthe geometry is still not in the fetchable
      database (see `scenarios/lemans24h.py`'s docstring) — the scenario
      runs on Spa instead. If a source ever has it, this is a one-line
      `--track` argument, not a code change.

## Done

- [x] Project skeleton: `engine/`, `classes/`, `tracks/`, `scenarios/`,
      `data/`, plus `tools/` and `tests/`.
- [x] Point-mass vehicle dynamics with aerodynamic load and longitudinal
      load transfer onto the driven axle.
- [x] Load-sensitive tyre model with a tunable friction ellipse, and both
      distance- and energy-based degradation.
- [x] Weather and track conditions, reaching the solver only as a grip
      multiplier and an air density.
- [x] Track geometry from segment lists (straights and arcs) or surveyed
      point/CSV data, resampled to uniform arc length.
- [x] Quasi-steady-state lap-time solver: three-pass, converged for a closed
      lap, with per-point speed caps for flags and blocked track.
- [x] Minimum-lap-time racing line: convex minimum-curvature seed, then
      coarse-to-fine refinement against real lap times.
- [x] LMP2 class configuration, with every number marked REGULATED,
      PUBLISHED or ESTIMATED.
- [x] Real circuit geometry fetched from a public database; five circuits
      within 0.1% of their published lengths.
- [x] Validation against five cited LMP2 race lap records, plus a
      sensitivity tool that attributes a residual to a parameter instead of
      guessing.
- [x] Solved lines persisted to `lines/` and reused, fingerprinted against
      the geometry they were solved on.
- [x] Live progress from the solver, and every tool mirrored to `logs/`.
- [x] GT3 class configuration, as an SRO Balance-of-Performance target
      rather than any one manufacturer's car (`classes/gt3.yaml`) — the
      right comparison against a class lap record, the wrong one for a
      specific model.
- [x] Per-class reference laps (`data/reference_laps_<class>.yaml`),
      resolved automatically by class name so LMP2 and GT3 don't share a
      file that neither layout nor era actually matches between them.
- [x] The Catalunya layout question from the previous entry: resolved by
      making `tools/fetch_references.py` match the record to whichever
      layout the cached geometry actually is, rather than always taking
      the newest record. The cached geometry has the final chicane, so it
      is now checked against the 2021 record (1:35.797) that was set on
      that layout, not the 2024 record (1:30.174) that wasn't — see
      `geometry_note` and `other_layouts` in the reference file for the
      evidence.
- [x] Fuel burn, counted only over distance the engine was actually
      driving, and consecutive-lap stint simulation (`engine/stint.py`,
      `tools/run_stint.py`) with tyres and fuel changing together lap to
      lap. A stint gets quicker before it gets slower; the crossover is
      the whole of pit strategy, and it's now visible.
- [x] A calibration tool (`tools/calibrate.py`) that fits only the
      ESTIMATED coefficients against a fixed racing line per circuit, with
      leave-one-out cross-validation to catch a fit that's absorbing
      per-circuit accidents rather than learning the car.
- [x] **LMP2 calibration run to completion, applied, and confirmed.**
      `python3 tools/calibrate.py --apply` (run manually outside a session,
      `logs/calibrate-5786.log`): fitted RMS 1.071 s (from 2.898 s
      uncalibrated), held-out (leave-one-out) RMS 1.528 s — a 1.4x
      generalisation gap, comfortably inside the ~2.5x bar this file set.
      Fitted coefficients in `classes/lmp2.yaml`: `cda` 1.140 → 0.798,
      `cla` 4.900 → 7.770, `mu_y` 1.620 → 1.425 (drag and grip both fitted
      down, downforce up — a different balance than the estimated
      starting point, not just a scale on it).
      `make clean-lines && make validate` afterwards
      (`logs/validate-5848.log`) confirms the fitted car re-optimises to a
      sane line rather than the fit being an artifact of the held-fixed
      one: 4 of 5 circuits are now `ok` (simulated quicker than the
      published race lap, as a clean lap should be) — Monza +0.649,
      Silverstone +1.567, Sakhir +1.279, Catalunya +2.496. Spa is still
      flagged `SLOW` at −0.653 s, down from −4.439 s before calibration —
      much smaller, but the one circuit where the car remains on the wrong
      side of the record. Consistent with "The Spa residual is the car,
      not the optimiser" below: that section's numbers predate this
      calibration and are now stale on magnitude, but its conclusion
      (Spa's shortfall is a car-model residual, not an under-converged
      line) still stands — calibration shrank it, it didn't explain it
      away.
- [x] A `Makefile` covering the whole pipeline (`make setup test validate
      sensitivity calibrate plots stint endurance`), so a session can run
      one command and watch it with `make watch` instead of juggling nine
      separate scripts.
- [x] The multi-class endurance scenario (`scenarios/endurance.py`,
      `scenarios/lemans24h.py`): traffic between classes as an exact
      average encounter rate, full-course yellows that floor lap time and
      discount pit stops, a day/night temperature curve, and pit stops
      triggered by fuel with separate tyre-change and driver-change
      cadences. Verified on a 2-hour Spa test race (3 LMP2 + 4 GT3, one
      FCY) — sane relative pace, pit cycles where the fuel model says they
      should land, correct FCY slowing and pit discount.
- [x] 129 tests (124 plus 5 for the endurance scenario).

## Fixed this session

Found while resuming from a stale PROGRESS.md (see "Status" above) — none
of these were caused by this session's own work, they were latent breakage
from the two commits before it, plus one environment problem:

- **`tools/calibrate.py` and `tools/sensitivity.py` were broken.** The GT3
  commit renamed `data/reference_laps.yaml` to
  `data/reference_laps_lmp2.yaml` and taught `validate.py` to resolve the
  per-class name, but not these two, which still opened the now-deleted
  fixed filename and would `FileNotFoundError` on the first line. Neither
  had been run since that rename. Both now use the same per-class lookup.
- **Four tests errored on numpy 2.0.** `ndarray.ptp()` was removed in
  numpy 2.0; the four call sites used the method form. Switched to the
  `np.ptp(arr)` function form, which still exists.
- **The local environment's scipy (1.8.0, from `apt`) was binary-incompatible
  with its numpy (2.2.6, from `pip --user`)** — `import scipy.interpolate`
  raised `numpy.dtype size changed, may indicate binary incompatibility`,
  which failed test collection entirely before a single test ran. Not a
  repo problem, but worth recording here since it will recur in a fresh
  clone on a similarly mismatched machine: `pip3 install --user --upgrade
  "scipy>=1.11"` shadows the broken system package with one built for the
  numpy actually installed.

## The Spa residual is the car, not the optimiser

Spa is the cleanest reference in the set — 2024, current specification,
layout matches — and was 4.4 s slow, so the first question was whether the
racing-line search had simply run out of budget. It had not. Quadrupling the
budget to 25000 evaluations found 0.27 s more and then *converged*, stopping
at 15963 of its own accord:

| sweep budget | lap | gain over seed | evaluations used |
|---|---|---|---|
| 6000 | 2:05.696 | +1.883 | 6927 (budget-limited) |
| 25000 | 2:05.423 | +2.156 | 15963 (converged) |

So a fully converged line at Spa is still 4.17 s off the published lap, and
the shortfall belongs to the car model. The default sweep budget now scales
with the number of control points rather than being a flat number, since a
flat 6000 converges on a short circuit and stops a long one early.

## Where it stands against real lap times

Simulated flying lap against published race lap record, optimised line,
4 m sampling, **after calibration** (`classes/lmp2.yaml`'s fitted `cda`,
`cla`, `mu_y` — see "Done" above). Negative means the simulation is slower:

| circuit | simulated | published | delta | caveat |
|---|---|---|---|---|
| Monza | 1:35.339 | 1:35.988 | +0.649 | reference set under the pre-2021 power limit |
| Spa | 2:01.910 | 2:01.257 | −0.653 | cleanest comparison in the set; still SLOW |
| Silverstone | 1:41.549 | 1:43.116 | +1.567 | current spec, current layout |
| Sakhir | 1:47.300 | 1:48.579 | +1.279 | reference set under the pre-2021 power limit |
| Catalunya | 1:33.301 | 1:35.797 | +2.496 | layout may not match |

A clean simulated lap should be *quicker* than a race lap record, which is
set on fuel and used tyres in traffic. Four of five circuits are now on the
right side of that; Spa is the exception (see "Fixed this session" /
calibration entry above). Reproduce with `python3 tools/validate.py`.

The pre-calibration table (kept for reference — this is what the numbers
above replaced): Monza −3.386, Spa −4.439, Silverstone −1.284, Sakhir
−0.528, Catalunya −4.463, all simulated slower than published.

## Notes / decisions

**The engine stays class-agnostic.** No car's numbers appear in `engine/`.
Everything arrives through a validated `classes/*.yaml`, and unknown keys in
those files are errors rather than silent no-ops, because they are edited by
hand and an autonomous session should not lose a lap to a typo.

**Track geometry is fetched, not committed.** The source database
(TUMFTM/racetrack-database, from OpenStreetMap traces) is LGPL-3.0.
Vendoring it would settle this project's licence by accident, so
`tools/fetch_tracks.py` downloads into a gitignored cache and
`tracks/real/PROVENANCE.md` records where it came from.

**Validation is deliberately one-sided.** The references are race laps, not
qualifying laps. Matching one exactly would be a coincidence or a thumb on
the scale, so `tools/validate.py` reports which side of the expected band a
circuit falls on rather than an unsigned error.

**Estimated parameters are labelled as estimated.** No LMP2 aero map or
Goodyear tyre model is public and CLAUDE.md puts proprietary data out of
scope, so those coefficients are guesses in the right envelope. The class
file says so rather than implying a precision it does not have.

### Three bugs worth remembering, because each looked like something else

**Minimising the second difference of the path points is not minimising
curvature.** The second difference is `k·ds²`, and `ds` shrinks on the inside
of a corner, so that objective is weighted by `ds⁴` and rewards hugging the
inside kerb — very nearly the opposite of a racing line. It has to be
curvature itself, linearised as `k + n'' + k²n`.

**Resampling a centreline linearly invents corners.** Each new sample lands
on the chord between two old ones — millimetres — and curvature
differentiates that twice. It was reporting 86 corners at Monza with a
tightest radius of 8 m, against a real 11 and about 20. Splining the
resample gives 15 with no filtering at all. What looked like GPS noise was
self-inflicted, and the fix was in the resampler, not the filter.

**A refinement basis has to be local and fine enough.** Refining at a fixed
24 control points is one knot every 292 m on the 7 km of Spa: a single
control point spanning several corners, unable to move one apex without
dragging its neighbours. It was finding six thousandths of a second. Local
cubic B-splines at roughly one knot per corner find whole seconds —
Silverstone went from +0.17 s to +3.07 s.

### One open observation

The optimised Silverstone lap shows narrow spikes in lateral acceleration
that the seed line does not (see `out/silverstone.png`). A first check says
they are probably real rather than artefacts: the seed line is *smoother*
than the centreline it came from (99th-percentile curvature change 9.4e-4
against 2.5e-3), and the sharpest points sit at the Loop and at Club, which
are genuinely the tightest corners on the circuit. A spike would also slow
the car, so the optimiser has no reason to create one. Not fully settled,
because checking it properly means keeping the optimised offsets rather than
re-solving — see the task above.

### Things known to be missing or approximate

- Grip that varies *within* a lap (a damp patch, a marble line) falls back
  to a slower direct evaluation path; only a per-lap scalar uses the fast
  table. Fine so far, wanted for the endurance scenario.
- The corner detector splits compound corners at its curvature threshold, so
  it over-counts: 15 at Monza against a real 11. It is used for reporting
  and for plotting, not by the solver, so this is cosmetic.
- The racing-line search is local. It improves the seed; it does not prove
  the result optimal.
- No transient dynamics: quasi-steady state assumes weight transfer and tyre
  slip settle faster than the car moves through a corner.
