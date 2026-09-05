"""Track and weather conditions, reduced to what the solver consumes.

Everything the environment does to a lap time reaches the solver as either a
grip multiplier or an air density. That keeps the weather model swappable --
a rain radar feed and a constant could both drive it -- without the vehicle
model needing to know where the number came from.
"""

from __future__ import annotations

from dataclasses import dataclass

from .units import RHO_AIR_ISA, air_density


@dataclass(frozen=True)
class Conditions:
    """Ambient state for one lap or one stint.

    ``wetness`` runs 0 (dry line) to 1 (standing water). ``wet_grip_penalty``
    is the grip lost at wetness 1 on the appropriate tyre for the conditions,
    so the default 0.35 describes a wet-shod car on a properly wet track, not
    a slick-shod one -- that case is a much larger penalty and belongs in a
    scenario, where the tyre choice is an explicit decision.
    """

    air_temp_c: float = 20.0
    track_temp_c: float = 30.0
    pressure_pa: float = 101325.0
    humidity: float = 0.4
    wetness: float = 0.0
    wet_grip_penalty: float = 0.35
    optimum_track_temp_c: float = 32.0
    thermal_grip_penalty: float = 0.10
    grip_scale: float = 1.0

    @property
    def rho(self) -> float:
        """Air density (kg/m^3) for the current ambient state."""
        return air_density(self.air_temp_c, self.pressure_pa, self.humidity)

    def thermal_factor(self) -> float:
        """Grip multiplier from track temperature alone.

        A quadratic falloff either side of the compound's working window: a
        cold track and an overheating one both cost grip, which is why the
        same car is quicker at dusk than at noon.
        """
        t_opt = self.optimum_track_temp_c
        if t_opt <= 0:
            return 1.0
        rel = (self.track_temp_c - t_opt) / t_opt
        return max(0.0, 1.0 - self.thermal_grip_penalty * rel * rel)

    def wet_factor(self) -> float:
        """Grip multiplier from standing water."""
        w = min(max(self.wetness, 0.0), 1.0)
        return max(0.0, 1.0 - self.wet_grip_penalty * w)

    def grip_multiplier(self) -> float:
        """Total environmental grip multiplier applied to peak friction."""
        return max(0.0, self.grip_scale * self.wet_factor() * self.thermal_factor())

    @staticmethod
    def dry() -> "Conditions":
        """ISA-ish dry reference used for validation runs."""
        return Conditions(air_temp_c=15.0, track_temp_c=32.0, humidity=0.0,
                          pressure_pa=101325.0)

    def __repr__(self) -> str:
        return (f"Conditions({self.air_temp_c:.0f}C air, {self.track_temp_c:.0f}C track, "
                f"wet={self.wetness:.2f}, grip x{self.grip_multiplier():.3f}, "
                f"rho={self.rho:.3f})")
