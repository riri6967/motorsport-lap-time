"""Running a car for many laps, and watching it change while it does.

A single lap is a snapshot of a car that never varies. A stint is the real
thing: the tyres give up grip as they wear, the fuel load burns off and the
car gets lighter and quicker, and the track cools or warms under it. Those
three pull in different directions -- a stint usually gets quicker before it
gets slower -- and where they cross is what a pit strategy is about.

Built on the lap solver rather than inside it. The solver stays a function of
a car and a track; everything that makes a car change over time lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .conditions import Conditions
from .qss import solve_lap
from .track import Track
from .tyres import TyreState
from .units import format_laptime
from .vehicle import Vehicle


@dataclass
class StintLap:
    """One lap of a stint, and the state of the car that produced it."""

    number: int
    lap_time: float
    grip: float
    mass_kg: float
    fuel_start_kg: float
    fuel_used_kg: float
    tyre_distance_km: float
    top_speed: float
    mean_combined_g: float

    @property
    def fuel_end_kg(self) -> float:
        return self.fuel_start_kg - self.fuel_used_kg


@dataclass
class StintResult:
    """A completed stint."""

    vehicle_name: str
    track_name: str
    laps: tuple = ()
    ended_because: str = ""
    conditions_start: Optional[Conditions] = None
    conditions_end: Optional[Conditions] = None
    extra: dict = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.laps)

    @property
    def total_time(self) -> float:
        return float(sum(lap.lap_time for lap in self.laps))

    @property
    def fuel_used(self) -> float:
        return float(sum(lap.fuel_used_kg for lap in self.laps))

    @property
    def best(self) -> Optional[StintLap]:
        return min(self.laps, key=lambda lap: lap.lap_time) if self.laps else None

    @property
    def times(self) -> np.ndarray:
        return np.array([lap.lap_time for lap in self.laps])

    def degradation_per_lap(self) -> float:
        """Seconds a lap lost per lap, fitted after the car stops improving.

        A stint gets quicker while the fuel burning off outweighs the tyres
        going away, so fitting from lap one measures the fuel, not the
        tyres. The slope is taken from the quickest lap onwards.
        """
        if self.count < 4:
            return float("nan")
        times = self.times
        start = int(np.argmin(times))
        if self.count - start < 3:
            return float("nan")
        laps = np.arange(start, self.count, dtype=float)
        return float(np.polyfit(laps, times[start:], 1)[0])

    def crossover_lap(self) -> Optional[int]:
        """The lap the stint stopped getting quicker: tyres overtaking fuel."""
        return self.best.number if self.best else None

    def summary(self) -> str:
        if not self.laps:
            return f"{self.vehicle_name} at {self.track_name}: no laps run"
        first, last = self.laps[0], self.laps[-1]
        lines = [
            f"{self.vehicle_name} at {self.track_name}: {self.count} laps, "
            f"{self.total_time / 60:.1f} min ({self.ended_because})",
            f"  first lap      {format_laptime(first.lap_time)}",
            f"  best lap       {format_laptime(self.best.lap_time)} "
            f"(lap {self.best.number})",
            f"  last lap       {format_laptime(last.lap_time)}  "
            f"({last.lap_time - self.best.lap_time:+.3f} s off the best)",
            f"  fuel used      {self.fuel_used:.1f} kg "
            f"({self.fuel_used / self.count:.2f} kg per lap)",
            f"  tyre grip      {first.grip:.3f} -> {last.grip:.3f}",
        ]
        slope = self.degradation_per_lap()
        if slope == slope:                     # not NaN
            lines.append(f"  degradation    {slope:+.3f} s per lap after the "
                         f"crossover")
            lines.append(f"  crossover      lap {self.crossover_lap()} -- "
                         f"past here the tyres cost more than the fuel saves")
        else:
            lines.append("  degradation    still improving at the end -- the "
                         "fuel burning off is")
            lines.append("                 outpacing the tyres for the whole "
                         "stint, so the")
            lines.append("                 crossover is beyond this many laps")
        return "\n".join(lines)


def simulate_stint(vehicle: Vehicle, track: Track, offset=None,
                   laps: Optional[int] = None,
                   fuel_kg: Optional[float] = None,
                   tyres: Optional[TyreState] = None,
                   conditions_at: Optional[Callable] = None,
                   speed_limit_at: Optional[Callable] = None,
                   min_grip: float = 0.0,
                   progress: Optional[Callable] = None) -> StintResult:
    """Run consecutive laps until the fuel, the laps or the tyres run out.

    ``conditions_at(lap_number, elapsed_s)`` returns the weather for that
    lap, which is how a stint runs into dusk. ``speed_limit_at`` does the
    same for a per-point speed cap, which is how it runs into a safety car.

    Each lap is solved against the car as it is at the start of that lap:
    that fuel load, that tyre grip, that track temperature. Nothing is
    carried over from a lap solved under different conditions.
    """
    spec_fuel = vehicle.spec.mass.fuel_kg
    capacity = vehicle.spec.mass.fuel_capacity_kg
    fuel = float(fuel_kg if fuel_kg is not None
                 else (capacity or spec_fuel or 0.0))
    state = tyres if tyres is not None else TyreState(vehicle.spec.tyres)

    records: list[StintLap] = []
    elapsed = 0.0
    reason = "lap count reached"
    conditions_start = vehicle.conditions
    conditions_now = vehicle.conditions
    lap_number = 0

    while True:
        lap_number += 1
        if laps is not None and lap_number > laps:
            reason = f"{laps} laps completed"
            break

        conditions_now = (conditions_at(lap_number, elapsed)
                          if conditions_at else vehicle.conditions)
        car = vehicle.with_fuel(fuel)
        if conditions_now is not vehicle.conditions:
            car = car.with_conditions(conditions_now)
        grip = state.grip
        if grip < min_grip:
            reason = f"tyres below the {min_grip:.2f} grip floor"
            lap_number -= 1
            break

        cap = speed_limit_at(lap_number, elapsed) if speed_limit_at else None
        lap = solve_lap(car, track, offset=offset, grip=grip, speed_limit=cap)

        if fuel_kg is not None or capacity:
            if lap.fuel_burn_kg > fuel + 1e-9:
                reason = "out of fuel"
                lap_number -= 1
                break

        records.append(StintLap(
            number=lap_number, lap_time=lap.lap_time, grip=grip,
            mass_kg=car.mass, fuel_start_kg=fuel,
            fuel_used_kg=lap.fuel_burn_kg,
            tyre_distance_km=state.distance_m / 1000.0,
            top_speed=lap.top_speed,
            mean_combined_g=lap.mean_combined_accel() / 9.80665))
        elapsed += lap.lap_time
        fuel -= lap.fuel_burn_kg
        state.advance(lap.distance, mass_kg=car.mass,
                      accel_mag=lap.mean_combined_accel())

        if progress is not None:
            progress(records[-1])
        if laps is None and not capacity and fuel_kg is None:
            reason = "no fuel or lap limit given"
            break

    return StintResult(vehicle_name=vehicle.name, track_name=track.name,
                       laps=tuple(records), ended_because=reason,
                       conditions_start=conditions_start,
                       conditions_end=conditions_now)
