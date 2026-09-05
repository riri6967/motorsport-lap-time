# Motorsport Lap-Time & Racing-Line Simulator

A car-agnostic lap-time optimisation engine: given a circuit and a class of
car, it works out the quickest way round and how long that takes.

The engine knows nothing about any particular car. Every vehicle number
arrives through a `classes/*.yaml` file, so the same solver runs an LMP2, a
GT3 or an IndyCar without a line of code changing. See `CLAUDE.md` for the
project's scope and `PROGRESS.md` for where it currently stands.

## Quick start

```bash
python3 tools/fetch_tracks.py          # download real circuit geometry
python3 tools/validate.py --quick      # simulated vs published lap times
python3 tools/plot_lap.py --track Monza
```

## How it works

**Vehicle** (`engine/vehicle.py`) is a point mass carrying aerodynamic load,
with longitudinal load transfer onto the driven axle — which is what stops a
500 kW rear-drive car pretending it can use all its power at a hairpin exit.
Cornering speed is solved by bisection, because downforce puts the unknown on
both sides: more speed buys more grip.

**Tyres** (`engine/tyres.py`) are load-sensitive — doubling the vertical load
does not double the grip — and spend their friction on a tunable ellipse, so
grip used sideways is not available for braking.

**The solver** (`engine/qss.py`) is the three-pass quasi-steady-state method:
a lateral limit, a forward acceleration sweep, a backward braking sweep,
repeated until the speed crossing the start line stops moving. Flags, safety
cars and blocked track enter as a per-point speed cap that the same sweeps
blend into a physical profile.

**The line** (`engine/racing_line.py`) is found in two stages. A convex
minimum-curvature solve gets close in a fraction of a second; a coarse-to-fine
refinement against real lap times finds what curvature alone cannot, which is
almost entirely about corner exits — giving up entry speed to open the exit
radius and get on the power sooner pays back all the way down the next
straight.

## Checking it against reality

`tools/validate.py` compares simulated laps against published LMP2 race lap
records, cited in `data/reference_laps.yaml`. The comparison is deliberately
one-sided: those are race laps, set on fuel and used tyres in traffic, so a
clean simulated lap should come out somewhat quicker. Landing exactly on a
race lap record would be a coincidence or a thumb on the scale.

Where the numbers in a class file are estimates rather than measurements, the
file says so. No manufacturer publishes an LMP2 aero map, and proprietary
team data is out of scope by design.

## Tests

```bash
python3 -m pytest
```

The suite checks force balances against closed-form answers where they exist
— a skidpad has one, and the solver reproduces the real equilibrium, in which
the car settles just below the textbook lateral limit because it must reserve
grip to overcome drag.

## Layout

```
engine/      the solver. Class-agnostic; no car's numbers belong here
classes/     one YAML per car class
tracks/      circuit descriptions; tracks/real/ is a fetched cache
scenarios/   multi-class and endurance scenarios, built on top of engine/
data/        reference lap times and provenance
tools/       fetch, validate, plot
tests/
```
