#!/usr/bin/env python3
"""A multi-class endurance race, Le Mans style.

    python3 scenarios/lemans24h.py                    # 6 hours at Spa
    python3 scenarios/lemans24h.py --hours 24
    python3 scenarios/lemans24h.py --track Silverstone --lmp2 8 --gt3 12
    python3 scenarios/lemans24h.py --hours 24 --plot

Two classes sharing a circuit, running through a night, interrupted by
full-course yellows, stopping for fuel and tyres and drivers. What the
scenario is really for is the interaction between those: a prototype's lap
time is not its lap time when there are twelve GT cars in the way, a stint
plan built around tyre life falls apart when a neutralisation arrives at the
wrong moment, and the track at three in the morning is not the track at three
in the afternoon.

On the circuit: the public geometry database this project uses does not
include the Circuit de la Sarthe, so the default is Spa, which hosts a real
six-hour race. Nothing here is specific to a circuit -- point it at any
cached track with --track.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import _log                                            # noqa: E402

from engine.conditions import Conditions               # noqa: E402
from engine.config import load_vehicle_spec            # noqa: E402
from engine.lines import cached_racing_line            # noqa: E402
from engine.track import Track                         # noqa: E402
from engine.units import format_laptime                # noqa: E402
from engine.vehicle import Vehicle                     # noqa: E402
from scenarios.endurance import (Entry, Neutralisation, PitRules,  # noqa: E402
                                 day_night_weather, simulate_race)

# Crew names are placeholders. A real entry list would come from an entry
# list; inventing plausible-looking driver names would be inventing data.
CREWS = ("crew A", "crew B", "crew C")


def build_field(track: Track, classes, rng: random.Random, spread: float):
    """One Entry per car, each on its class's racing line."""
    entries = []
    for class_file, count, prefix in classes:
        spec = load_vehicle_spec(ROOT / class_file)
        vehicle = Vehicle(spec, conditions=Conditions.dry())
        print(f"  {spec.name}: preparing the racing line ...", flush=True)
        line, source = cached_racing_line(vehicle, track, ROOT / "lines")
        print(f"  {spec.name}: line {source}, "
              f"{format_laptime(line.lap_time)}", flush=True)
        for i in range(count):
            # A field of identical cars is not a race; real within-class
            # spreads run to about a second a lap between best and worst.
            pace = 1.0 + rng.uniform(0.0, spread)
            entries.append(Entry(
                number=f"{prefix}{i + 1}", class_name=spec.name,
                vehicle=vehicle, offset=line.offset,
                drivers=CREWS, pace_factor=pace,
                pit=PitRules(tyre_stints=2, driver_max_stints=3)))
    return entries


def build_neutralisations(duration_s: float, rng: random.Random,
                          per_hour: float, mean_minutes: float):
    """Full-course yellows scattered through the race.

    Endurance races are neutralised often and unpredictably -- a car in a
    barrier takes as long to recover as it takes. Poisson arrivals with an
    exponential duration is the standard way to say "several, at no
    particular time, mostly short but occasionally long".
    """
    events = []
    when = 0.0
    while True:
        when += rng.expovariate(per_hour / 3600.0) if per_hour > 0 else 1e18
        if when >= duration_s:
            break
        length = min(rng.expovariate(1.0 / (mean_minutes * 60.0)),
                     duration_s - when)
        if length < 120.0:
            continue
        events.append(Neutralisation(start_s=when, end_s=when + length))
        when += length
    return events


def plot_race(result, weather, out: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                  constrained_layout=True)
    colours = {}
    palette = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd")
    for car in result.cars:
        name = car.entry.class_name
        colours.setdefault(name, palette[len(colours) % len(palette)])
    for car in result.cars:
        times = np.array(car.lap_times)
        hours = np.cumsum(times) / 3600.0
        ax.plot(hours, times, lw=0.6, alpha=0.5,
                color=colours[car.entry.class_name])
    for name, colour in colours.items():
        ax.plot([], [], color=colour, label=name)
    for event in result.neutralisations:
        for axis in (ax, ax2):
            axis.axvspan(event.start_s / 3600.0, event.end_s / 3600.0,
                         color="#f1c40f", alpha=0.25, lw=0)
    ax.set_ylabel("lap time (s)")
    ax.set_title(f"{result.track_name}: {result.duration_s / 3600:.0f} hours, "
                 f"{len(result.cars)} cars  "
                 f"(shaded = full-course yellow)")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(alpha=0.25)
    top = np.percentile([t for c in result.cars for t in c.lap_times], 97)
    ax.set_ylim(None, top * 1.05)

    hours = np.linspace(0, result.duration_s / 3600.0, 400)
    ax2.plot(hours, [weather(h * 3600).track_temp_c for h in hours],
             color="#e67e22", label="track")
    ax2.plot(hours, [weather(h * 3600).air_temp_c for h in hours],
             color="#3498db", label="air")
    ax2.set_ylabel("temperature (C)")
    ax2.set_xlabel("race hour")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.25)

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--track", default="Spa")
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--lmp2", type=int, default=6)
    parser.add_argument("--gt3", type=int, default=10)
    parser.add_argument("--ds", type=float, default=5.0)
    parser.add_argument("--start-hour", type=float, default=13.0,
                        help="clock time the race starts, for day and night")
    parser.add_argument("--fcy-per-hour", type=float, default=0.7)
    parser.add_argument("--fcy-minutes", type=float, default=11.0)
    parser.add_argument("--pace-spread", type=float, default=0.010,
                        help="within-class pace spread, as a fraction")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args(argv)

    log_path = _log.start("lemans24h", sys.argv)
    print(f"logging to {log_path}\n")

    csv = ROOT / "tracks" / "real" / f"{args.track}.csv"
    if not csv.is_file():
        print(f"no geometry for {args.track}; run tools/fetch_tracks.py",
              file=sys.stderr)
        return 1
    track = Track.from_csv(csv, name=args.track, ds=args.ds)
    rng = random.Random(args.seed)
    duration_s = args.hours * 3600.0

    print(f"{track.name}, {args.hours:g} hours, starting at "
          f"{args.start_hour:02.0f}:00\n")
    field = build_field(track, [("classes/lmp2.yaml", args.lmp2, "P"),
                                ("classes/gt3.yaml", args.gt3, "G")],
                        rng, args.pace_spread)
    events = build_neutralisations(duration_s, rng, args.fcy_per_hour,
                                   args.fcy_minutes)
    weather = day_night_weather(start_hour=args.start_hour)

    print(f"\n{len(field)} cars, {len(events)} full-course yellows\n")
    print(f"{'hour':>5} {'clock':>6} {'track':>7} {'leader':>7} "
          f"{'best lap so far':>16}")
    print("-" * 46)

    def report(elapsed, cars, conditions):
        leader = max(cars, key=lambda c: c.laps)
        best = min((min(c.lap_times) for c in cars if c.lap_times),
                   default=float("nan"))
        clock = (args.start_hour + elapsed / 3600.0) % 24.0
        print(f"{elapsed / 3600.0:>5.1f} {clock:>5.1f}h "
              f"{conditions.track_temp_c:>6.1f}C {leader.laps:>7} "
              f"{format_laptime(best):>16}", flush=True)

    result = simulate_race(track, field, duration_s, neutralisations=events,
                           weather=weather, progress=report)
    print()
    print(result.summary())

    print("\nwhat the race cost, beyond driving")
    print(f"{'car':>5} {'class':>6} {'pits':>5} {'pit time':>9} "
          f"{'traffic':>9} {'FCY laps':>9}")
    for car in result.classification:
        print(f"{car.entry.number:>5} {car.entry.class_name:>6} "
              f"{car.pit_stops:>5} {car.pit_time_s:>8.0f}s "
              f"{car.traffic_loss_s:>8.0f}s {car.fcy_laps:>9}")

    if args.plot:
        out = plot_race(result, weather,
                        ROOT / "out" / f"race-{track.name}-{args.hours:g}h.png")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
