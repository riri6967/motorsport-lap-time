"""Loading and validation of vehicle-class configuration files.

Every number the solver uses about a *car* enters through here. The engine
itself stays class-agnostic; ``classes/*.yaml`` supplies LMP2, GT3, IndyCar
and the rest. Validation is deliberately strict: an unrecognised key is an
error, not a silent no-op, because these files are edited by hand and an
autonomous session must not spend a lap chasing a typo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

from .units import RHO_AIR_ISA


class ConfigError(ValueError):
    """Raised when a class/track configuration is malformed."""


def _require(d: dict, key: str, where: str) -> Any:
    if key not in d:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return d[key]


def _check_keys(d: dict, allowed: Sequence[str], where: str) -> None:
    unknown = set(d) - set(allowed)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {sorted(unknown)}; "
            f"allowed keys are {sorted(allowed)}"
        )


def _positive(value: float, name: str, where: str) -> float:
    value = float(value)
    if value <= 0:
        raise ConfigError(f"{where}: '{name}' must be > 0, got {value}")
    return value


def _fraction(value: float, name: str, where: str) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{where}: '{name}' must lie in [0, 1], got {value}")
    return value


@dataclass(frozen=True)
class MassSpec:
    """Mass and centre-of-gravity geometry."""

    vehicle_kg: float
    driver_kg: float = 80.0
    fuel_kg: float = 0.0
    weight_dist_front: float = 0.48
    cg_height_m: float = 0.30
    wheelbase_m: float = 2.90
    fuel_capacity_kg: float = 0.0

    @property
    def total_kg(self) -> float:
        return self.vehicle_kg + self.driver_kg + self.fuel_kg

    def with_fuel(self, fuel_kg: float) -> "MassSpec":
        """A copy at a different fuel load (endurance stint modelling)."""
        return MassSpec(
            vehicle_kg=self.vehicle_kg,
            driver_kg=self.driver_kg,
            fuel_kg=max(0.0, fuel_kg),
            weight_dist_front=self.weight_dist_front,
            cg_height_m=self.cg_height_m,
            wheelbase_m=self.wheelbase_m,
            fuel_capacity_kg=self.fuel_capacity_kg,
        )

    @staticmethod
    def from_dict(d: dict, where: str = "mass") -> "MassSpec":
        allowed = ("vehicle_kg", "driver_kg", "fuel_kg", "weight_dist_front",
                   "cg_height_m", "wheelbase_m", "fuel_capacity_kg")
        _check_keys(d, allowed, where)
        spec = MassSpec(
            vehicle_kg=_positive(_require(d, "vehicle_kg", where), "vehicle_kg", where),
            driver_kg=float(d.get("driver_kg", 80.0)),
            fuel_kg=float(d.get("fuel_kg", 0.0)),
            weight_dist_front=_fraction(
                d.get("weight_dist_front", 0.48), "weight_dist_front", where),
            cg_height_m=_positive(d.get("cg_height_m", 0.30), "cg_height_m", where),
            wheelbase_m=_positive(d.get("wheelbase_m", 2.90), "wheelbase_m", where),
            fuel_capacity_kg=float(d.get("fuel_capacity_kg", 0.0)),
        )
        if spec.driver_kg < 0 or spec.fuel_kg < 0:
            raise ConfigError(f"{where}: driver_kg and fuel_kg must be >= 0")
        return spec


@dataclass(frozen=True)
class AeroSpec:
    """Downforce and drag.

    Either constant coefficients (``cla``/``cda``) or a speed-indexed lookup
    table (``map``). CLAUDE.md rules out live CFD, so a precomputed table is
    the intended route for aero that varies with speed or ride height.
    Table rows are ``[v_ms, ClA, CdA, balance_front]`` with the balance
    column optional.
    """

    cla: float = 0.0
    cda: float = 0.0
    balance_front: float = 0.45
    rho: float = RHO_AIR_ISA
    map: Optional[tuple] = None

    @staticmethod
    def from_dict(d: dict, where: str = "aero") -> "AeroSpec":
        allowed = ("cla", "cda", "balance_front", "rho_air", "map")
        _check_keys(d, allowed, where)
        table = d.get("map")
        rows: Optional[tuple] = None
        if table is not None:
            if not isinstance(table, (list, tuple)) or len(table) < 2:
                raise ConfigError(f"{where}: 'map' needs at least two rows")
            parsed = []
            for i, row in enumerate(table):
                if len(row) not in (3, 4):
                    raise ConfigError(
                        f"{where}: map row {i} must be "
                        f"[v_ms, ClA, CdA] or [v_ms, ClA, CdA, balance_front]")
                v, cl, cd = float(row[0]), float(row[1]), float(row[2])
                bal = float(row[3]) if len(row) == 4 else float(
                    d.get("balance_front", 0.45))
                if cd <= 0:
                    raise ConfigError(f"{where}: map row {i} has CdA <= 0")
                parsed.append((v, cl, cd, bal))
            parsed.sort(key=lambda r: r[0])
            speeds = [r[0] for r in parsed]
            if len(set(speeds)) != len(speeds):
                raise ConfigError(f"{where}: map has duplicate speed entries")
            rows = tuple(parsed)
        elif "cda" not in d:
            raise ConfigError(f"{where}: provide either 'cda'/'cla' or 'map'")
        return AeroSpec(
            cla=float(d.get("cla", 0.0)),
            cda=float(d.get("cda", 0.0)) if rows is None else float(rows[0][2]),
            balance_front=_fraction(
                d.get("balance_front", 0.45), "balance_front", where),
            rho=_positive(d.get("rho_air", RHO_AIR_ISA), "rho_air", where),
            map=rows,
        )


@dataclass(frozen=True)
class PowertrainSpec:
    """Engine output at the crank plus driveline losses.

    ``power_curve`` rows are ``[v_ms, power_w]``. Without one the model is
    flat-rated at ``max_power_w`` above the speed where the tractive-force
    cap stops binding, which is a fair approximation for a modern racing
    engine driven through a close-ratio gearbox.
    """

    max_power_w: float
    efficiency: float = 0.92
    max_tractive_force_n: Optional[float] = None
    power_curve: Optional[tuple] = None
    drive: str = "rwd"
    # Brake thermal efficiency of the engine, and the energy in its fuel.
    # Together these turn the work the car does into fuel burnt, which is
    # what decides a stint length and therefore a race strategy.
    thermal_efficiency: float = 0.32
    fuel_energy_mj_per_kg: float = 43.0

    @staticmethod
    def from_dict(d: dict, where: str = "powertrain") -> "PowertrainSpec":
        allowed = ("max_power_w", "efficiency", "max_tractive_force_n",
                   "power_curve", "drive", "thermal_efficiency",
                   "fuel_energy_mj_per_kg")
        _check_keys(d, allowed, where)
        curve = d.get("power_curve")
        rows: Optional[tuple] = None
        if curve is not None:
            if not isinstance(curve, (list, tuple)) or len(curve) < 2:
                raise ConfigError(f"{where}: 'power_curve' needs >= 2 rows")
            parsed = sorted((float(r[0]), float(r[1])) for r in curve)
            rows = tuple(parsed)
        drive = str(d.get("drive", "rwd")).lower()
        if drive not in ("rwd", "fwd", "awd"):
            raise ConfigError(f"{where}: 'drive' must be rwd, fwd or awd")
        mtf = d.get("max_tractive_force_n")
        return PowertrainSpec(
            max_power_w=_positive(_require(d, "max_power_w", where),
                                  "max_power_w", where),
            efficiency=_fraction(d.get("efficiency", 0.92), "efficiency", where),
            max_tractive_force_n=(None if mtf is None else
                                  _positive(mtf, "max_tractive_force_n", where)),
            power_curve=rows,
            drive=drive,
            thermal_efficiency=_fraction(
                d.get("thermal_efficiency", 0.32), "thermal_efficiency", where),
            fuel_energy_mj_per_kg=_positive(
                d.get("fuel_energy_mj_per_kg", 43.0),
                "fuel_energy_mj_per_kg", where),
        )


@dataclass(frozen=True)
class TyreSpec:
    """Peak friction, load sensitivity and wear behaviour."""

    mu_x: float
    mu_y: float
    load_sensitivity: float = 0.15
    fz_ref_n: Optional[float] = None
    ellipse_exponent: float = 2.0
    grip_loss_per_lap: float = 0.0
    reference_lap_m: float = 5000.0
    wear_model: str = "distance"
    # Frictional work that would wear a set out completely. A 5 km lap at a
    # mean combined 8 m/s^2 in a 1000 kg car does ~4e7 J, so 4e9 J is ~1%
    # of grip per lap -- the right order for a racing slick.
    wear_energy_ref_j: float = 4.0e9
    min_grip: float = 0.80

    @staticmethod
    def from_dict(d: dict, where: str = "tyres") -> "TyreSpec":
        allowed = ("mu_x", "mu_y", "load_sensitivity", "fz_ref_n",
                   "ellipse_exponent", "grip_loss_per_lap", "reference_lap_m",
                   "wear_model", "wear_energy_ref_j", "min_grip")
        _check_keys(d, allowed, where)
        model = str(d.get("wear_model", "distance")).lower()
        if model not in ("distance", "energy", "none"):
            raise ConfigError(f"{where}: 'wear_model' must be distance, energy or none")
        fz_ref = d.get("fz_ref_n")
        return TyreSpec(
            mu_x=_positive(_require(d, "mu_x", where), "mu_x", where),
            mu_y=_positive(_require(d, "mu_y", where), "mu_y", where),
            load_sensitivity=float(d.get("load_sensitivity", 0.15)),
            fz_ref_n=None if fz_ref is None else _positive(fz_ref, "fz_ref_n", where),
            ellipse_exponent=_positive(
                d.get("ellipse_exponent", 2.0), "ellipse_exponent", where),
            grip_loss_per_lap=float(d.get("grip_loss_per_lap", 0.0)),
            reference_lap_m=_positive(
                d.get("reference_lap_m", 5000.0), "reference_lap_m", where),
            wear_model=model,
            wear_energy_ref_j=_positive(
                d.get("wear_energy_ref_j", 4.0e9), "wear_energy_ref_j", where),
            min_grip=_fraction(d.get("min_grip", 0.80), "min_grip", where),
        )


@dataclass(frozen=True)
class BrakeSpec:
    """Brake-system capability, separate from tyre grip.

    ``max_force_n`` is the ceiling the discs and calipers can apply; on a
    modern racing car the tyres normally give out first, but the distinction
    matters for heavy cars and for brake-limited hybrid classes.
    """

    max_force_n: Optional[float] = None

    @staticmethod
    def from_dict(d: dict, where: str = "brakes") -> "BrakeSpec":
        _check_keys(d, ("max_force_n",), where)
        val = d.get("max_force_n")
        return BrakeSpec(None if val is None else _positive(val, "max_force_n", where))


@dataclass(frozen=True)
class VehicleSpec:
    """Everything the solver needs to know about one car class."""

    name: str
    mass: MassSpec
    aero: AeroSpec
    powertrain: PowertrainSpec
    tyres: TyreSpec
    brakes: BrakeSpec = field(default_factory=BrakeSpec)
    rolling_resistance: float = 0.012
    v_max_ms: Optional[float] = None
    description: str = ""
    sources: tuple = ()

    @staticmethod
    def from_dict(d: dict, where: str = "<class config>") -> "VehicleSpec":
        allowed = ("name", "description", "mass", "aero", "powertrain", "tyres",
                   "brakes", "rolling_resistance", "v_max_ms", "sources", "bop")
        _check_keys(d, allowed, where)
        spec = VehicleSpec(
            name=str(_require(d, "name", where)),
            description=str(d.get("description", "")),
            mass=MassSpec.from_dict(_require(d, "mass", where), f"{where}.mass"),
            aero=AeroSpec.from_dict(_require(d, "aero", where), f"{where}.aero"),
            powertrain=PowertrainSpec.from_dict(
                _require(d, "powertrain", where), f"{where}.powertrain"),
            tyres=TyreSpec.from_dict(_require(d, "tyres", where), f"{where}.tyres"),
            brakes=BrakeSpec.from_dict(d.get("brakes", {}) or {}, f"{where}.brakes"),
            rolling_resistance=float(d.get("rolling_resistance", 0.012)),
            v_max_ms=(None if d.get("v_max_ms") is None else
                      _positive(d["v_max_ms"], "v_max_ms", where)),
            sources=tuple(d.get("sources", ()) or ()),
        )
        if spec.rolling_resistance < 0:
            raise ConfigError(f"{where}: 'rolling_resistance' must be >= 0")
        return spec


def load_yaml(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"no such configuration file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level")
    return data


def load_vehicle_spec(path: str | Path) -> VehicleSpec:
    """Read one ``classes/*.yaml`` into a validated :class:`VehicleSpec`."""
    path = Path(path)
    return VehicleSpec.from_dict(load_yaml(path), where=str(path))
