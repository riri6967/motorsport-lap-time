"""Unit helpers and physical constants.

The engine is SI-internal end to end: metres, seconds, kilograms, newtons,
radians. Conversions live here so no other module invents its own.
"""

G = 9.80665           # m/s^2, standard gravity
RHO_AIR_ISA = 1.225   # kg/m^3, ISA sea-level density at 15 degC


def kmh_to_ms(v: float) -> float:
    return v / 3.6


def ms_to_kmh(v: float) -> float:
    return v * 3.6


def mph_to_ms(v: float) -> float:
    return v * 0.44704


def hp_to_w(p: float) -> float:
    """Metric horsepower (PS) to watts."""
    return p * 735.49875


def w_to_hp(p: float) -> float:
    return p / 735.49875


def air_density(temp_c: float, pressure_pa: float = 101325.0,
                humidity: float = 0.0) -> float:
    """Density of moist air from temperature, pressure and relative humidity.

    Used by the weather model: a hot day costs both downforce and drag, and
    also engine power, so track conditions cannot be reduced to grip alone.
    """
    t_k = temp_c + 273.15
    # Saturation vapour pressure, Tetens' formula (Pa).
    p_sat = 610.78 * 10.0 ** (7.5 * temp_c / (temp_c + 237.3))
    p_v = humidity * p_sat
    p_d = pressure_pa - p_v
    return (p_d / (287.058 * t_k)) + (p_v / (461.495 * t_k))


def format_laptime(seconds: float) -> str:
    """Seconds to the m:ss.sss form used on every timing screen."""
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return "--:--.---"
    minutes = int(seconds // 60)
    rem = seconds - 60 * minutes
    return f"{minutes}:{rem:06.3f}"


def parse_laptime(text: str) -> float:
    """Inverse of :func:`format_laptime`; accepts 'm:ss.sss' or plain seconds."""
    text = text.strip()
    if ":" in text:
        minutes, _, rem = text.partition(":")
        return int(minutes) * 60 + float(rem)
    return float(text)
