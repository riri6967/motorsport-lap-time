# Motorsport Lap-Time & Racing-Line Simulator

## Goal
A car-agnostic lap-time optimization engine that computes the ideal racing 
line lap-by-lap, accounting for: tyre degradation, weather/grip changes, 
flags/blockages (yellow/red/safety car), and aero balance vs. speed. 
Extended with a multi-class endurance scenario (Le Mans 24h style: traffic 
between classes, day/night grip transitions, pit/driver-change strategy).

## Architecture
- `engine/` — core solver. Vehicle dynamics (bicycle or point-mass model), 
  friction-ellipse-constrained trajectory optimization, tyre degradation 
  model, weather-as-grip-multiplier, flag/blockage constraints. Written 
  ONCE, class-agnostic — never hardcode a specific car's numbers here.
- `classes/*.yaml` — one config per car class: mass, downforce/drag curve, 
  power curve, tyre compound behavior, BoP adjustments where applicable.
- `tracks/*` — track centerline + width data for real circuits.
- `scenarios/lemans24h.py` — multi-class traffic, FCY, day/night, pit 
  strategy logic. Built on top of engine/, not inside it.
- `data/` — reference data pulled from public sources (SRO BoP docs, 
  ACO technical regs, published timing sheets) used to validate output 
  against real lap times.

## Build order (data availability, easiest first)
1. LMP2 (spec chassis — Oreca 07, single tyre/engine supplier)
2. GT3 / GT4 (SRO publishes BoP data every event)
3. IndyCar (spec chassis, moderately open data)
4. LMP4 (regional spec series, thinner telemetry)
5. F1 (richest fan data, least accessible aero/CFD internals — do last, 
   use offline aero-lookup tables, not live CFD)

## Explicitly out of scope
- Real-time/live CFD per corner. Not feasible outside an F1 team's HPC 
  cluster. Aero data is precomputed offline into a lookup table instead.
- Proprietary team data. Use only public specs, BoP docs, technical 
  regulations, and sim-racing (iRacing/ACC) physics as validation proxies.

## Working rules for autonomous sessions
- Read PROGRESS.md before doing anything. Resume from the next unchecked 
  item.
- Commit to git after every meaningful milestone. Never leave the repo 
  in a broken or uncommitted state at the end of a session.
- Update PROGRESS.md before ending a session: what was done, what's next, 
  any decisions made.
- Don't stop to ask about things already decided above. Only flag a 
  question if it's something this file doesn't cover and getting it 
  wrong would be expensive to undo.
- If PROGRESS.md says the project is complete, write DONE at the top 
  of PROGRESS.md and stop.
