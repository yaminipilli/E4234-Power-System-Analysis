"""Physical-unit <-> per-unit conversions used by the property dialogs (B5)."""
from __future__ import annotations

import math


def zbase_ohm(kv: float, base_mva: float) -> float:
    return kv * kv / base_mva


def line_pu_from_physical(length_km, r_ohm_km, x_ohm_km, b_us_km, kv, base_mva):
    """Return (R, X, B_total) in pu on ``base_mva`` for a line at nominal voltage ``kv``."""
    if kv <= 0:
        raise ValueError("nominal kV of the from-bus must be > 0 to convert ohm/km values")
    zb = zbase_ohm(kv, base_mva)
    return (r_ohm_km * length_km / zb,
            x_ohm_km * length_km / zb,
            b_us_km * 1e-6 * length_km * zb)


def line_physical_from_pu(r, x, b, length_km, kv, base_mva):
    """Inverse of the above (per km values) - for display."""
    zb = zbase_ohm(kv, base_mva)
    L = length_km if length_km > 0 else 1.0
    return r * zb / L, x * zb / L, b / zb / L * 1e6


def transformer_pu_from_nameplate(rated_mva, z_pct, x_r, base_mva):
    """(R, X) in pu on the system base from %Z and X/R on the transformer's own MVA rating."""
    if rated_mva <= 0:
        raise ValueError("rated MVA must be > 0")
    z = z_pct / 100.0 * base_mva / rated_mva
    r = z / math.sqrt(1.0 + x_r ** 2)
    return r, r * x_r


def q_from_power_factor(p_mw: float, pf: float, lagging: bool = True) -> float:
    """Reactive power for a load of P at the given power factor (lagging = inductive, Q > 0)."""
    if not 0 < pf <= 1:
        raise ValueError("power factor must be in (0, 1]")
    q = abs(p_mw) * math.tan(math.acos(pf))
    q = math.copysign(q, p_mw) if p_mw != 0 else 0.0
    return q if lagging else -q


def ohm_to_pu(z_ohm: float, kv: float, base_mva: float) -> float:
    return z_ohm / zbase_ohm(kv, base_mva)


def pu_to_ohm(z_pu: float, kv: float, base_mva: float) -> float:
    return z_pu * zbase_ohm(kv, base_mva)
