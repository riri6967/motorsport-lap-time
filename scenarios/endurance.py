"""Multi-class endurance racing: traffic, full-course yellows, day and night.

The engine gives a lap time for one car in one state. A race is that lap
repeated a few hundred times, for twenty cars at once, while everything
underneath it moves: fuel burns off, tyres go away, the sun sets and the
track cools, a car stops on circuit and neutralises the race, and a
prototype spends a measurable part of every lap getting past GT cars.

Traffic is the part that most needs stating, because it is easy to hand-wave.
Two cars lapping in T_i and T_j pass each other at a rate of (1/T_i - 1/T_j)
per second by definition -- no simulation of positions required -- which is
(1 - T_i/T_j) encounters per lap of the quicker car. That is exact for the
average, needs no assumption about where cars happen to be, and is the number
this model uses. What it cannot tell you is whether a particular pass happens
somewhere costly, so the cost per encounter is a parameter, not a prediction.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from engine.conditions import Conditions
from engine.qss import solve_lap
from engine.track import Track
from engine.tyres import TyreState
from engine.units import format_laptime
from engine.vehicle import Vehicle


class LapTimeModel:
    """Lap times for one car, cached over the states it actually reaches.

    A full-length race is several thousand lap solves per car, and the car
    barely changes from one lap to the next: a couple of kilos of fuel, a
    fraction of a percent of grip. Quantising those and caching turns the
    race from an hour of solving into a couple of minutes, with a lap-time
    error far below the modelling error. The quantisation is deliberately
    coarse in fuel and fine in grip, because grip is the sharper lever.
    """

    def __init__(self, vehicle: Vehicle, track: Track, offset=None,
                 grip_step: float = 0.002, fuel_step: float = 3.0,
                 temp_step: float = 2.0):
        self.vehicle = vehicle
        self.track = track
        self.offset = offset
        self.grip_step = grip_step
        self.fuel_step = fuel_step
        self.temp_step = temp_step
        self._cache: dict = {}
        self.solves = 0
        self.lookups = 0

    def lap(self, grip: float, fuel_kg: float, conditions: Conditions):
        """A solved lap for this state, from cache where possible."""
        key = (round(grip / self.grip_step),
               round(fuel_kg / self.fuel_step),
               round(conditions.track_temp_c / self.temp_step),
               round(conditions.air_temp_c / self.temp_step),
               round(conditions.wetness, 2))
        self.lookups += 1
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        car = self.vehicle.with_fuel(fuel_kg).with_conditions(conditions)
        result = solve_lap(car, self.track, offset=self.offset, grip=grip)
        self.solves += 1
        self._cache[key] = result
        return result

    @property
    def hit_rate(self) -> float:
        return 1.0 - self.solves / max(self.lookups, 1)


@dataclass
class PitRules:
    """When a car stops and what it costs.

    ``driver_max_stints`` stands in for the driving-time limits every
    endurance regulation imposes; ``tyre_stints`` is the strategic choice of
    how many fuel loads to run on one set.
    """

    stationary_s: float = 30.0
    pit_lane_loss_s: float = 45.0
    refuel_rate_kg_s: float = 2.5
    tyre_change_s: float = 22.0
    driver_change_s: float = 25.0
    tyre_stints: int = 2
    driver_max_stints: int = 3
    fcy_pit_discount: float = 0.55
    """Share of the usual pit loss paid when stopping under a neutralisation.

    The field is circulating slowly, so the time given up relative to
    everyone else is much less than under green. This is why a well-timed
    full-course yellow is worth more than a good stint.
    """


@dataclass
class Entry:
    """One car in the race."""

    number: str
    class_name: str
    vehicle: Vehicle
    offset: Optional[np.ndarray] = None
    drivers: tuple = ("A", "B", "C")
    pace_factor: float = 1.0
    """Multiplier on lap time for this crew: driver skill and car condition.

    A field of identical cars is not a race. Real spreads within a class run
    to a second or so a lap between the quickest and slowest crews.
    """
    pit: PitRules = field(default_factory=PitRules)


@dataclass
class Neutralisation:
    """A full-course yellow or safety car."""

    start_s: float
    end_s: float
    kind: str = "FCY"
    speed_ms: float = 22.0        # ~80 km/h, the usual FCY limit

    def covers(self, when: float) -> bool:
        return self.start_s <= when < self.end_s


def day_night_weather(start_hour: float = 15.0,
                      day_air_c: float = 24.0, night_air_c: float = 12.0,
                      day_track_c: float = 38.0, night_track_c: float = 16.0,
                      optimum_track_c: float = 32.0) -> Callable:
    """Temperatures following the sun, as a function of race time.

    A track lags the air and swings much further: the tarmac at three in the
    afternoon and at three in the morning are different surfaces. Grip
    follows through the tyre's working window, which is why a night stint at
    a hot circuit is quicker and a night stint at a cold one is not.
    """
    def weather(elapsed_s: float) -> Conditions:
        hour = (start_hour + elapsed_s / 3600.0) % 24.0
        # Peak at 15:00, trough at 03:00.
        phase = math.cos((hour - 15.0) / 24.0 * 2.0 * math.pi)
        warm = 0.5 * (1.0 + phase)
        return Conditions(
            air_temp_c=night_air_c + warm * (day_air_c - night_air_c),
            track_temp_c=night_track_c + warm * (day_track_c - night_track_c),
            humidity=0.7 - 0.3 * warm,
            optimum_track_temp_c=optimum_track_c)
    return weather


@dataclass
class _CarState:
    entry: Entry
    model: LapTimeModel
    tyres: TyreState
    fuel_kg: float
    elapsed_s: float = 0.0
    laps: int = 0
    stint_laps: int = 0
    stints: int = 0
    tyre_stints: int = 0
    driver_index: int = 0
    driver_stints: int = 0
    pit_stops: int = 0
    pit_time_s: float = 0.0
    traffic_loss_s: float = 0.0
    fcy_laps: int = 0
    lap_times: list = field(default_factory=list)
    retired: bool = False

    @property
    def driver(self) -> str:
        return self.entry.drivers[self.driver_index % len(self.entry.drivers)]


@dataclass
class RaceResult:
    """A finished race."""

    track_name: str
    duration_s: float
    cars: tuple = ()
    neutralisations: tuple = ()
    solves: int = 0
    lookups: int = 0

    @property
    def classification(self):
        """Cars in finishing order: most laps, then least time."""
        return sorted(self.cars, key=lambda c: (-c.laps, c.elapsed_s))

    def by_class(self):
        classes: dict = {}
        for car in self.classification:
            classes.setdefault(car.entry.class_name, []).append(car)
        return classes

    @property
    def cache_hit_rate(self) -> float:
        return 1.0 - self.solves / max(self.lookups, 1)

    def summary(self) -> str:
        lines = [f"{self.track_name}: {self.duration_s / 3600:.0f} hours, "
                 f"{len(self.cars)} cars"]
        neutral = sum(n.end_s - n.start_s for n in self.neutralisations)
        lines.append(f"  {len(self.neutralisations)} neutralisations, "
                     f"{neutral / 60:.0f} min of the race")
        lines.append(f"  {self.solves} lap solves for {self.lookups} laps "
                     f"({self.cache_hit_rate * 100:.0f}% cached)")
        for class_name, cars in self.by_class().items():
            winner = cars[0]
            lines.append("")
            lines.append(f"  {class_name}")
            lines.append(f"    {'car':>5} {'laps':>5} {'gap':>10} "
                         f"{'best':>9} {'stops':>6} {'in traffic':>11}")
            for car in cars:
                if car is winner:
                    gap = "-"
                elif car.laps < winner.laps:
                    gap = f"+{winner.laps - car.laps} lap" \
                          + ("s" if winner.laps - car.laps > 1 else "")
                else:
                    gap = f"+{car.elapsed_s - winner.elapsed_s:.1f} s"
                best = min(car.lap_times) if car.lap_times else float("nan")
                lines.append(
                    f"    {car.entry.number:>5} {car.laps:>5} {gap:>10} "
                    f"{format_laptime(best):>9} {car.pit_stops:>6} "
                    f"{car.traffic_loss_s:>10.0f}s")
        return "\n".join(lines)


def simulate_race(track: Track, entries, duration_s: float,
                  neutralisations=(), weather: Optional[Callable] = None,
                  pass_cost_s: float = 0.9, passed_cost_s: float = 0.35,
                  progress: Optional[Callable] = None,
                  progress_every_s: float = 1800.0) -> RaceResult:
    """Run a multi-class endurance race.

    Cars advance a lap at a time, always the one that is furthest behind in
    race time, so the field stays synchronised and traffic is computed
    against what the others are actually doing. Each lap costs its solved
    time, plus the traffic it meets, plus any pit stop, and is stretched to
    the neutralisation speed where one is running.
    """
    weather = weather or (lambda t: Conditions.dry())
    cars = []
    for entry in entries:
        model = LapTimeModel(entry.vehicle, track, offset=entry.offset)
        capacity = entry.vehicle.spec.mass.fuel_capacity_kg or 60.0
        cars.append(_CarState(entry=entry, model=model,
                              tyres=TyreState(entry.vehicle.spec.tyres),
                              fuel_kg=capacity))

    # Reference pace per car, for the traffic rate. Recomputed as the race
    # goes; the initial value just gets things started.
    pace = {}
    for car in cars:
        conditions = weather(0.0)
        pace[car.entry.number] = (
            car.model.lap(1.0, car.fuel_kg, conditions).lap_time
            * car.entry.pace_factor)

    queue = [(0.0, i) for i in range(len(cars))]
    heapq.heapify(queue)
    next_report = progress_every_s

    while queue:
        elapsed, index = heapq.heappop(queue)
        car = cars[index]
        if elapsed >= duration_s or car.retired:
            continue

        conditions = weather(elapsed)
        lap = car.model.lap(car.tyres.grip, car.fuel_kg, conditions)
        lap_time = lap.lap_time * car.entry.pace_factor

        # -- neutralisation ----------------------------------------------
        neutral = next((n for n in neutralisations if n.covers(elapsed)), None)
        if neutral is not None:
            lap_time = max(lap_time, track.length / neutral.speed_ms)
            car.fcy_laps += 1
            fuel_used = lap.fuel_burn_kg * 0.55   # circulating slowly
        else:
            fuel_used = lap.fuel_burn_kg
            # -- traffic --------------------------------------------------
            encounters = 0.0
            for other in cars:
                if other is car or other.retired:
                    continue
                mine, theirs = pace[car.entry.number], pace[other.entry.number]
                if theirs > mine:
                    encounters += 1.0 - mine / theirs
                elif mine > theirs:
                    # Being lapped costs less, but it is not free.
                    encounters -= 0.0
            loss = encounters * pass_cost_s
            faster_cars = sum(
                1.0 - pace[o.entry.number] / pace[car.entry.number]
                for o in cars
                if o is not car and not o.retired
                and pace[o.entry.number] < pace[car.entry.number])
            loss += faster_cars * passed_cost_s
            car.traffic_loss_s += loss
            lap_time += loss

        car.elapsed_s = elapsed + lap_time
        car.laps += 1
        car.stint_laps += 1
        car.lap_times.append(lap_time)
        car.fuel_kg -= fuel_used
        car.tyres.advance(lap.distance, mass_kg=car.entry.vehicle.mass,
                          accel_mag=lap.mean_combined_accel())
        pace[car.entry.number] = lap.lap_time * car.entry.pace_factor

        # -- pit stop ----------------------------------------------------
        rules = car.entry.pit
        capacity = car.entry.vehicle.spec.mass.fuel_capacity_kg or 60.0
        if car.fuel_kg < fuel_used and car.elapsed_s < duration_s:
            car.stints += 1
            car.tyre_stints += 1
            car.driver_stints += 1
            added = capacity - max(car.fuel_kg, 0.0)
            stop = rules.stationary_s + added / rules.refuel_rate_kg_s
            if car.tyre_stints >= rules.tyre_stints:
                stop = max(stop, rules.tyre_change_s + added / rules.refuel_rate_kg_s)
                car.tyres.reset()
                car.tyre_stints = 0
            if car.driver_stints >= rules.driver_max_stints:
                stop += rules.driver_change_s
                car.driver_index += 1
                car.driver_stints = 0
            total = stop + rules.pit_lane_loss_s
            in_neutral = any(n.covers(car.elapsed_s) for n in neutralisations)
            if in_neutral:
                total *= rules.fcy_pit_discount
            car.fuel_kg = capacity
            car.stint_laps = 0
            car.pit_stops += 1
            car.pit_time_s += total
            car.elapsed_s += total

        if car.elapsed_s < duration_s:
            heapq.heappush(queue, (car.elapsed_s, index))

        if progress is not None and elapsed >= next_report:
            next_report += progress_every_s
            progress(elapsed, cars, weather(elapsed))

    return RaceResult(track_name=track.name, duration_s=duration_s,
                      cars=tuple(cars), neutralisations=tuple(neutralisations),
                      solves=sum(c.model.solves for c in cars),
                      lookups=sum(c.model.lookups for c in cars))
