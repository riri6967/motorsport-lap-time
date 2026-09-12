# Progress

## Status: ENGINE + THREE CLASSES + ENDURANCE SCENARIO WORKING

The solver, the racing-line optimiser, the validation harness, a race stint
(fuel burn + tyre wear together), and the multi-class endurance scenario
(traffic, FCY, day/night, pit strategy) all work end to end. LMP2 and GT3
are calibrated against real lap times; IndyCar (added last session) is not
— it has one validatable circuit, and calibration needs several to mean
anything (see "Next up").

**This session** added oval banking to the track/vehicle model (tested
against the closed-form banked-curve solution, not just approximately) and
a synthetic Indianapolis oval to exercise it; built the per-circuit
aero-trim mechanism the last two sessions' GT3 work had been waiting on,
deliberately left unpopulated pending real evidence; checked the endurance
scenario at full 24-hour scale; and researched (without executing, for
reasons given inline) the two remaining geometry gaps, Circuit de la
Sarthe and LMP4's home circuits. Every item left in "Next up" is blocked on
external data or evidence this session could not manufacture responsibly —
see each entry for exactly what is missing and how to tell when it stops
being missing.

## Next up

- [ ] **Calibrate IndyCar once a second circuit exists.** Right now
      `data/reference_laps_indycar.yaml` has exactly one entry (Austin/COTA),
      because it is the only circuit in the fetchable track database on a
      layout IndyCar actually races (see "IndyCar added" under "Done" for
      why `IMS` doesn't count). `tools/calibrate.py`'s leave-one-out check
      needs several circuits to mean anything — fitting to one point is
      indistinguishable from memorising it — so the class file's ESTIMATED
      aero and tyre numbers are first-pass engineering estimates, the same
      as LMP2 and GT3 started out, not a fit. Uncalibrated, IndyCar is
      10.744 s SLOW at Austin — proportionally worse than LMP2 or GT3's
      starting residuals (see the validation table below) — and
      `tools/sensitivity.py --class classes/indycar.yaml` puts tyre grip
      far out ahead of drag/downforce/power as the biggest single lever
      (+2.901 s for +5% grip, against well under a second for the others),
      which is a plausible direction (peak lateral g in the simulated lap
      is 2.61, on the low side for what a real IndyCar pulls through a fast
      corner) but is one data point's fingerprint, not a diagnosis — don't
      hand-tune to it before there's a second circuit to check the tune
      against, for the same reason `tools/sensitivity.py`'s own docstring
      gives for not fitting one parameter to an average.
- [ ] **Get IndyCar a second circuit that is actually comparable.** Oval
      banking now exists (see "Oval banking" under "Done") and a synthetic
      Indianapolis oval (`tracks/indianapolis.yaml`) solves to a sane,
      physically plausible lap — but it is deliberately *not* wired in as a
      reference-lap entry, because the only "IndyCar" lap record Wikipedia's
      Indianapolis Motor Speedway article has for the oval is Arie
      Luyendyk's 38.119 s from 1996, and that is a closed-course qualifying
      lap (roughly 236 mph, matching the well-known 1996 qualifying record,
      not a race lap despite sitting in a table headed "Race lap records")
      set by a CART-era car with a completely different chassis formula,
      turbo boost and aero rules to the 2012+ Dallara DW12/IR-18
      `classes/indycar.yaml` describes -- not an era-of-the-same-car gap
      like LMP2's 2021 power cut, which `era_power_scale` can honestly
      paper over, but a different car wearing the same class name. Using it
      would be worse than the layout mismatches this project has otherwise
      been careful to catch (see Catalunya's chicane note), so it is left
      out rather than faked. What's still needed is a *current-generation*
      Indy oval reference (a recent 500 pole/qualifying speed, correctly
      labelled as qualifying rather than race pace, from a source other
      than this Wikipedia table) or a road/street course TUMFTM hasn't
      digitised (Long Beach, Mid-Ohio, Road America, Barber, the IMS road
      course itself). Wiring either in follows Austin's pattern: geometry
      into `VALIDATION_SET`/a segment or CSV file, a `CIRCUITS` entry in
      `tools/fetch_references.py` (check the article's heading text and
      depth first, see "IndyCar added" below), a `--circuit` fetch.
- [ ] **GT3's Monza and Spa residuals, post-calibration.** Unlike LMP2
      (only Spa left slow, by 0.653 s), GT3's full re-validation leaves
      *two* circuits slow: Monza by 2.718 s and Spa by 0.713 s (see the GT3
      calibration entry under "Done" below for the full table). The LOO
      generalisation gap is fine (1.4x, same as LMP2), so this isn't the
      fit absorbing per-circuit accidents. **Checked this session**: ran
      `tools/sensitivity.py --class classes/gt3.yaml` (fresh, on the
      calibrated coefficients) to see whether Monza is something
      circuit-specific or part of a wider pattern. It's the latter, not
      Monza alone: Monza (281.1 km/h simulated top speed) and Spa
      (279.7 km/h) are the two highest-top-speed circuits in the set by a
      clear margin over Silverstone/Sakhir/Catalunya (264-271 km/h), and
      they are also the two circuits with a shortfall instead of a margin.
      The `drag -10%`/`drag -20%` probes are the best pattern match to the
      shortfall's shape (+0.873/+0.874 correlation, clearly ahead of
      downforce at +0.270 and power at +0.603; tyre grip is +0.003 —
      essentially uncorrelated, so more grip is not the fix), and gain the
      most exactly where the shortfall is worst. Reading this together:
      real GT3 teams run a lower-drag aero trim at power circuits like
      Monza and Spa than at technical ones like Silverstone, Sakhir and
      Catalunya — the same *shape* of effect the LMP2 sprint-vs-Le-Mans
      split already models, just continuous within one BoP package instead
      of a discrete swap between two of them. A single fitted `cda` across
      all five circuits is necessarily a compromise, undershooting drag
      where real cars trim it down. The mechanism for this now exists
      (`aero_trim:`, see "Per-circuit aero trim mechanism" under "Done")
      but is deliberately not populated for GT3 or anything else yet — with
      five circuits and up to two free numbers each, fitting a trim to
      close this residual would fit every point exactly and prove nothing.
      An entry belongs here only once there is independent evidence (a
      cited, known lower-drag GT3 setup for Monza or Spa specifically), not
      derived from this file's own validation gap.
- [ ] Real Circuit de la Sarthe geometry is still not in the fetchable
      database (see `scenarios/lemans24h.py`'s docstring) — the scenario
      runs on Spa instead. **Looked into this session**: TUMFTM's own
      database (the project's existing source) doesn't have it, but the
      circuit is very likely traceable in OpenStreetMap directly — it's a
      real, permanently-signed road network (much of the lap runs on public
      D-roads the rest of the year) rather than a private facility, which
      is exactly the kind of thing OSM tends to have. Nobody has done what
      TUMFTM did for their 25 circuits, though: stitched the right sequence
      of ways into one closed loop, picked the current 24h configuration
      (chicanes and all) out of however many are tagged, and derived a
      width. Doing that correctly from a raw Overpass query, without a
      reference lap-length or corner list to check the result against the
      way `tools/fetch_tracks.py`'s docstring can point at TUMFTM's
      provenance, is real GIS work with a real chance of silently getting
      the chicane configuration or a sector's width wrong -- not a fetch
      script, and not attempted this session on that basis. If a future
      session picks this up: start from Overpass (`way[ref~"Circuit de la
      Sarthe"]` or similar, `overpass-turbo.eu` is the fastest way to see
      what's tagged before writing a query), and check the stitched result
      against the published 13.626 km current-configuration length before
      trusting it for anything.
- [ ] **LMP4, next in CLAUDE.md's build order after IndyCar, is blocked on
      both geometry and lap times — checked this session, not a surprise.**
      LMP4 (the Ligier JS P4 category, a smaller, cheaper prototype than
      LMP3, run mostly in French regional/club series) doesn't race at any
      circuit in `tools/fetch_tracks.py`'s `ALL_TRACKS` — no Paul Ricard,
      Nogaro, Magny-Cours, Dijon, or any other of its actual home circuits
      are in the TUMFTM database (`python3 tools/fetch_tracks.py --list`
      confirms it) — and turned up zero Wikipedia lap records at any of the
      six circuits this project *does* have geometry for (checked Monza,
      Spa, Silverstone, Sakhir, Catalunya, Austin directly; the search
      found plenty of LMP3 entries at the same circuits, which is a
      different, better-documented, one-step-up category CLAUDE.md's build
      order does not name). This is exactly the "thinner telemetry" CLAUDE.md
      already flagged for this class, not new information that changes the
      plan — so the honest status is "still blocked," not "swap in LMP3
      instead," which would be substituting a different car for the one the
      build order actually specifies. Revisit if a French regional-circuit
      source ever surfaces, or if CLAUDE.md is explicitly amended to prefer
      LMP3.

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
- [x] **GT3 calibration, run the same way.** `python3 tools/calibrate.py
      --class classes/gt3.yaml` (dry run, `logs/calibrate-6553.log`):
      fitted RMS 2.276 s (from 5.986 s uncalibrated), held-out RMS 3.090 s
      — also a 1.4x generalisation gap, the same margin as LMP2's, so
      applied (`--apply`). `classes/gt3.yaml`: `cda` 1.230 → 0.861, `cla`
      3.300 → 3.717, `mu_x` 1.420 → 1.517, `mu_y` 1.470 → 1.571 (tyre grip
      scales `mu_x` and `mu_y` together, same as LMP2's fit did). GT3 had
      no cached lines yet, so both the fit and the full re-validation used
      fresh solves rather than reusing anything from LMP2's run.
      `tools/validate.py --class classes/gt3.yaml` afterwards
      (`logs/validate-7322.log`, GT3-only lines cleared first so the
      re-optimised line reflects the fitted car, not the one the fit held
      fixed) is a less clean result than LMP2's: Silverstone +2.860 FAST,
      Sakhir +2.862 FAST, Catalunya +2.191 ok, but Monza -2.718 SLOW and
      Spa -0.713 SLOW — two circuits on the wrong side of the record, not
      just one. See the new "Next up" item above; the LOO ratio says the
      fit itself is fine, so this is a car-model gap to understand, not a
      reason to revert the calibration.
- [x] **IndyCar added** (`classes/indycar.yaml`), third in CLAUDE.md's build
      order: Dallara DW12/IR-18, universal road/street-course aero kit,
      2.2 L twin-turbo V6, Firestone spec tyre — road/street courses only,
      explicitly excluding ovals (see "Oval banking" under "Done"; the
      class file's header explains why at more length). Finding a
      validatable circuit took more work than writing the class file:
      IndyCar's calendar is almost entirely ovals and road/street courses
      TUMFTM hasn't digitised, and of the two `ALL_TRACKS` entries that
      looked plausible, `IMS` turned out to be the 2.5-mile oval (its
      closed-loop length comes out to 4022.3 m against the standard oval's
      4023.36 m — an exact match, and nothing like the ~3.9-4.2 km IndyCar
      road course) rather than any road-course layout, so it's unusable
      without the banking term neither track nor vehicle model has. `Austin`
      (Circuit of the Americas) is the one that works: same layout IndyCar,
      F1 and everyone else has always used there, and the digitised length
      (5507.5 m) matches the official 5.513 km to 0.1%. Added to
      `VALIDATION_SET` in `tools/fetch_tracks.py` and to `CIRCUITS` in
      `tools/fetch_references.py`; `data/reference_laps_indycar.yaml` has
      one entry, the 2019 IndyCar Classic race lap (1:48.8953, Colton
      Herta). `tools/validate.py --class classes/indycar.yaml`: 1:59.639
      simulated against that, −10.744 s (SLOW) — see "Next up" for why this
      is left uncalibrated rather than fitted to the one point available.
- [x] **Two `tools/fetch_references.py` bugs, found while wiring up
      Austin.** Neither affected the five LMP2/GT3 circuits already
      committed (re-ran both classes after the fix and diffed: identical
      apart from the retrieval date and the new Austin row), but both would
      have bitten the next circuit added regardless of class:
      - The lap-record heading regex was hardcoded to `==Lap records==` at
        exactly two `=`. Circuit of the Americas' article has
        `===Official record race lap times===` (three `=`, under a
        `==Records==` parent) instead, so it matched nothing. Both the
        heading text and its depth are now parameters (`heading=` on
        `lap_record_section`), and the section's end is wherever a heading
        of the same depth *or shallower* next appears, computed from the
        matched depth rather than assumed to be 2.
      - The time-value regex only accepted a 2-3 digit fraction
        (`\d{2,3}`); IndyCar's own timing publishes to four
        (`1:48.8953`), which made the whole row invisible to the parser —
        not a wrong year, no row at all. Now `\d{2,4}`.
      - Found chasing the above, not caused by it: year extraction searched
        the *raw* wikitext of the driver/car/event cells
        (`" ".join(cells[time_index + 1:])`) rather than the markup-stripped
        text, so a wikilink target containing a year-shaped number anywhere
        before the pipe — e.g. `[[Dallara DW12#IR–18 ... (2018–2027)|Dallara
        IR-18]]` — could be picked up ahead of the real year in the event
        name. Silverstone GT3's `other_layouts` entry silently had this bug
        already (logged as 2015, corrected to 2021 by this fix) but it
        never touched a `year` field that `era_power_scale` reads, so it
        never changed a lap time. Fixed by searching `plain[...]` instead.
- [x] **Oval banking.** `engine/track.py`'s `Segment` gained an optional
      `bank_deg` (arcs only; validated `>= 0`, since a bank's sign follows
      its own segment's curvature rather than being set independently), and
      `Track` a `bank` array alongside `curvature` — zero everywhere unless
      `from_segments` built it, since no CSV/surveyed geometry has a banking
      column and none of the real circuits in `tracks/real/` need one
      anyway. The physics (`Vehicle.banked_normal_load`, and its inlined
      twin in `engine.qss._run_sweeps`'s hot loop, which duplicates the
      vehicle model's formulas for speed the same way it already did for
      the flat-track case): resolving gravity, the normal reaction and the
      tyre's lateral force along axes aligned with the banked surface gives

          N     = cos(theta) (mg + downforce) + sin(theta) m v^2 |k|
          f_lat = cos(theta) m v^2 |k| - sin(theta) (mg + downforce)

      which is exactly the flat-track `N = mg + downforce`,
      `f_lat = m v^2 |k|` at `theta = 0` (`cos=1, sin=0`), so every existing
      track and every existing test is completely unaffected — confirmed
      both by the full suite staying green and by rerunning
      `tools/validate.py --quick --class classes/lmp2.yaml`, whose numbers
      are unchanged. Solving `f_lat = mu_y(N) N` for `v` at the limit
      reduces to the textbook banked-curve-with-friction result,
      `v^2 = R g (tan(theta) + mu) / (1 - mu tan(theta))` — checked exactly
      (not approximately) in `test_banked_corner_speed_matches_the_textbook_formula`,
      with downforce and load-sensitivity zeroed out so the comparison has
      no term the textbook formula lacks. **Deliberately out of scope**:
      banking only changes the *lateral* limit (`Vehicle.corner_speed` and
      the qss lookup's ellipse fraction) — `normal_load`, `braking_limit`,
      `longitudinal_limits` and everything the `AccelerationTable`
      precomputes over `(v, frac)` stay flat-track, i.e. accelerating or
      braking mid-corner on a banked track is modelled slightly
      conservatively (a little less grip than a real banked corner would
      give it). On an oval that is a minor effect on the straights-to-corner
      transitions and none at all on a flat-out lap (see below); folding
      banking into the longitudinal tables too would need a third table
      axis and touch every function that calls `normal_load`, which is a
      much bigger and riskier change for a benefit this class doesn't need
      yet. Also built `tracks/indianapolis.yaml`, a synthetic (segment-based,
      not surveyed) reconstruction of the Indianapolis Motor Speedway 2.5-mile
      oval from published dimensions (2 x 3,300 ft straights, 2 x 660 ft
      short chutes, 4 x quarter-mile/9.2-degree-banked turns; closes to
      within 3e-13 m) — the first non-surveyed circuit in the repo, and a
      real exercise of the banking code, not just a unit-test fixture:
      `classes/indycar.yaml` solves it at a completely flat-out 0:41.653
      (347.3 km/h the whole lap, min speed equal to top speed), which is
      the right qualitative answer -- the Indy 500 really is run
      essentially flat-out, banking and all -- and a concrete, physical
      sign that the banked lateral limit is doing its job rather than just
      passing its unit tests. Not wired in as a reference lap; see "Get
      IndyCar a second circuit" under "Next up" for why.
- [x] **Per-circuit aero trim mechanism**, resolving the "decide how to
      handle it" item that was open across the last two sessions.
      `VehicleSpec` gained an `aero_trim:` block (optional, per class file):
      `{circuit: {cda, cla, balance_front}}`, validated the same strict way
      as everything else in `engine/config.py` (unknown fields rejected, an
      empty override rejected outright since it can't mean anything).
      `VehicleSpec.aero_for(circuit)` returns the base `aero:` block
      overridden by that circuit's entry, or unchanged if it has none, and
      `Vehicle.with_aero_trim(circuit)` is the same pattern as
      `with_power_scale`/`with_fuel`. Wired into `tools/validate.py`,
      `tools/sensitivity.py` and `tools/calibrate.py` (the last two needed
      more care than a one-line call: both scale the ESTIMATED coefficients
      per probe/fit, and that scaling has to apply *on top of* a circuit's
      trim rather than replace it, or a probe would do nothing on a trimmed
      circuit — see `variant()`'s docstring in each). All three are
      currently no-ops on every class file, since `aero_trim` is empty
      everywhere; confirmed byte-for-byte unchanged output
      (`tools/sensitivity.py --class classes/gt3.yaml` reproduces the exact
      correlations from the entry above) before and after wiring it in.
      **The decision that matters more than the code**: this must never be
      populated by fitting it to close a validation residual. With one
      override per circuit and one circuit's worth of data to check it
      against, that fits every point exactly and proves nothing — exactly
      the failure `tools/calibrate.py`'s leave-one-out check exists to
      catch for the *global* coefficients, just sidestepped instead of
      caught, since a per-circuit table has no held-out circuit to be wrong
      about. An entry is only warranted by independent evidence (a cited,
      known setup choice for a named circuit), the same evidentiary bar
      `sources:` already holds the rest of a class file to. Documented at
      point of use in `engine/config.py` (`_aero_trim_table`'s docstring)
      and in `classes/gt3.yaml`'s header, since GT3 is where the evidence
      for wanting this showed up.
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
- [x] **Checked at full 24-hour scale** (`python3 scenarios/lemans24h.py
      --hours 24 --plot`, the default 6 LMP2 + 10 GT3 field at Spa): nothing
      that only showed up at 2 hours' worth of running. LMP2 winner did 620
      laps on 26 pit stops (~23-lap stints, ~47 min — matches
      `tools/run_stint.py --class classes/lmp2.yaml`'s own 22-lap, 45-minute
      fuel stint for this car at Spa); GT3 did 566 laps on 15 stops. 13
      full-course yellows over
      the race, correctly flooring every lap caught in one at exactly
      `track.length / neutral.speed_ms` (318.4 s at Spa) — visible as flat-
      topped spikes in `out/race-Spa-24h.png`, aligned with the shaded FCY
      windows. The day/night temperature curve completes one full cycle
      (peaking mid-afternoon, trough at 3 a.m., 38C to 16C track temp as
      configured) with no drift or discontinuity across the wrap from hour
      24 back to hour 0. Cache hit rate was only 15% (7979 solves for 9366
      laps) — lower than the 2-hour test, because `LapTimeModel` caches
      per-*car*, not per-class, so ten near-identical GT3s each rebuild
      their own cache rather than sharing one; correct, and not slow enough
      to matter here (the whole run took a few minutes), but a real
      speed-up if a much bigger field ever makes it not.
- [x] 141 tests (129 plus 7 for banking and 5 for the aero-trim mechanism,
      all in `test_vehicle.py` and `test_track.py`).

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
