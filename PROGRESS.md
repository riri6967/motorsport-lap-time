# Progress

## Status: ENGINE WORKING, FIRST CLASS VALIDATED (results below are honest, not good yet)

The solver, the racing-line optimiser and the validation harness all work
end to end. An LMP2 lap can be computed on a real circuit and compared
against a published lap time. The car model itself is not yet calibrated,
and says so.

## Next up

- [ ] **Confirm the Catalunya layout.** `data/reference_laps.yaml` flags it
      `layout: check`. The Grand Prix configuration lost its final chicane
      for 2023; if the cached geometry is the older one and the 2024 ELMS
      record was set on the newer, the comparison is measuring the wrong
      circuit and several seconds of the 4.5 s residual are not the model's
      fault. Either find matching geometry or drop the entry.
- [ ] **Decide how to handle per-circuit aero trim.** `classes/lmp2.yaml`
      describes one sprint package, but a real LMP2 runs materially less
      downforce and drag at Monza than at Barcelona. The config format
      already supports a speed-indexed aero map, so the mechanism exists;
      what is missing is a way to say "this circuit, this package" without
      duplicating a whole class file per event.
- [ ] **Then, and only then, calibrate the ESTIMATED LMP2 coefficients.**
      Not before: `tools/sensitivity.py` shows the shortfall does not have
      the shape of any single parameter being wrong (best pattern match 0.35
      once the racing-line deficit is removed), so fitting one number to the
      average would bury the cause instead of finding it.
- [ ] Add GT3/GT4, next in CLAUDE.md's build order. SRO publishes BoP every
      event, so this is the first class where published data can constrain
      the numbers directly rather than through lap times.
- [ ] Exercise tyre degradation over a stint. The model and its state are
      implemented and unit-tested but nothing yet runs multiple laps.
- [ ] `scenarios/lemans24h.py` — not started.

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
- [x] 124 tests.

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
4 m sampling. Negative means the simulation is slower:

| circuit | simulated | published | delta | caveat |
|---|---|---|---|---|
| Monza | 1:39.374 | 1:35.988 | −3.386 | reference set under the pre-2021 power limit |
| Spa | 2:05.696 | 2:01.257 | −4.439 | cleanest comparison in the set |
| Silverstone | 1:44.400 | 1:43.116 | −1.284 | current spec, current layout |
| Sakhir | 1:49.107 | 1:48.579 | −0.528 | reference set under the pre-2021 power limit |
| Catalunya | 1:34.637 | 1:30.174 | −4.463 | layout may not match |

A clean simulated lap should be *quicker* than a race lap record, which is
set on fuel and used tyres in traffic. Every circuit is on the wrong side of
that, so the car model is conservative. Reproduce with
`python3 tools/validate.py`.

Silverstone is quoted after the budget change above; the other four are from
the run before it and will each be a few tenths quicker on a rerun.

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
