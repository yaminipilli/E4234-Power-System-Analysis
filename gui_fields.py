"""Property-field definitions, validation and application for the GUI (no Tk code -> unit-testable).

Selection keys:  ('bus', number)   ('branch', index)   ('gen', index)
Every field shows its unit; MW/MVAr values are converted to per unit on the system base.
"""
import math
from dataclasses import dataclass
from typing import List, Optional

import units
from network import derive_bus_kinds


@dataclass
class Field:
    key: str
    label: str
    unit: str = ""
    type: str = "float"        # float | int | str | bool | choice | readonly
    choices: Optional[List[str]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    min_excl: bool = False
    allow_inf: bool = False
    group: str = ""


F = Field
BUS = [
    F("number", "Number", "", "readonly"), F("name", "Name", "", "str"),
    F("base_kv", "Nominal voltage", "kV", min=0), F("kind_text", "Type (from connected elements)", "", "readonly"),
    F("v_min", "Vmin", "pu", min=0, min_excl=True), F("v_max", "Vmax", "pu", min=0, min_excl=True),
    F("area", "Area", "", "int", min=0),
    F("slack", "External grid (slack bus)", "", "bool"),
    F("vm", "Voltage set-point (slack / PV)", "pu", min=0, min_excl=True),
    F("va_deg", "Angle (slack)", "deg"),
    F("p_load_mw", "Load P", "MW"), F("q_load_mvar", "Load Q", "MVAr"),
    F("pf", "Power factor (blank = use Q)", "", "str"), F("pf_lagging", "Lagging (inductive)", "", "bool"),
    F("load_model", "Load model", "", "choice", choices=["PQ", "Z", "ZIP"]),
    F("zip_z", "ZIP constant-Z share", "pu", min=0, max=1), F("zip_i", "ZIP constant-I share", "pu", min=0, max=1),
    F("zip_p", "ZIP constant-P share", "pu", min=0, max=1),
    F("g_shunt_mw", "Shunt G (at 1 pu)", "MW"), F("b_shunt_mvar", "Shunt B (at 1 pu, + = capacitor)", "MVAr"),
    F("in_service", "In service", "", "bool"),
]
LINE = [
    F("from_bus", "From bus", "", "readonly"), F("to_bus", "To bus", "", "readonly"), F("name", "Name", "", "str"),
    F("input_mode", "Data entry", "", "choice", choices=["pu", "physical"]),
    F("r", "R (series)", "pu", min=0), F("x", "X (series)", "pu", min=0), F("b", "B (total charging)", "pu", min=0),
    F("length_km", "Length", "km", min=0, min_excl=True), F("r_ohm_km", "R", "ohm/km", min=0),
    F("x_ohm_km", "X", "ohm/km", min=0), F("b_us_km", "B", "uS/km", min=0),
    F("rate_mva", "Rating (0 = unrated)", "MVA", min=0), F("in_service", "In service", "", "bool"),
]
TRAFO = [
    F("from_bus", "HV bus", "", "readonly"), F("to_bus", "LV bus", "", "readonly"), F("name", "Name", "", "str"),
    F("input_mode", "Data entry", "", "choice", choices=["pu", "nameplate"]),
    F("r", "R", "pu", min=0), F("x", "X", "pu", min=0),
    F("rated_mva", "Rated power", "MVA", min=0, min_excl=True),
    F("kv_hv", "Rated kV, HV side", "kV", min=0), F("kv_lv", "Rated kV, LV side", "kV", min=0),
    F("z_pct", "Impedance %Z (on rated MVA)", "%", min=0, min_excl=True), F("x_r", "X/R ratio", "", min=0, min_excl=True),
    F("tap", "Tap ratio (off-nominal)", "pu", min=0, min_excl=True),
    F("tap_side", "Tap side", "", "choice", choices=["from", "to"]),
    F("tap_min", "Tap range min", "pu", min=0, min_excl=True), F("tap_max", "Tap range max", "pu", min=0, min_excl=True),
    F("tap_step", "Tap step", "pu", min=0), F("shift_deg", "Phase shift", "deg"),
    F("b", "Magnetising B (total)", "pu", min=0),
    F("in_service", "In service", "", "bool"),
]
GEN = [
    F("bus_number", "Bus", "", "readonly"), F("name", "Name", "", "str"),
    F("p_mw", "P set-point", "MW"), F("v_setpoint", "V set-point", "pu", min=0, min_excl=True),
    F("q_min_mvar", "Qmin", "MVAr", allow_inf=True), F("q_max_mvar", "Qmax", "MVAr", allow_inf=True),
    F("in_service", "In service", "", "bool"),
]
SPECS = {"bus": BUS, "line": LINE, "transformer": TRAFO, "gen": GEN}


def spec_kind(net, kind, key):
    if kind == "branch":
        return "transformer" if net.branches[key].kind == "transformer" else "line"
    return kind


def _element(net, kind, key):
    if kind == "bus":
        return net.buses[net.index[key]]
    if kind == "branch":
        return net.branches[key]
    return net.generators[key]


def _inf(v):
    return "inf" if v == math.inf else "-inf" if v == -math.inf else v


def get_values(net, kind, key):
    el = _element(net, kind, key)
    base = net.base_mva
    sk = spec_kind(net, kind, key)
    v = {}
    for f in SPECS[sk]:
        k = f.key
        if sk == "bus":
            if k == "kind_text":
                v[k] = el.kind
            elif k == "slack":
                v[k] = el.kind.upper() == "SLACK"
            elif k == "p_load_mw":
                v[k] = el.p_load * base
            elif k == "q_load_mvar":
                v[k] = el.q_load * base
            elif k == "g_shunt_mw":
                v[k] = el.g_shunt * base
            elif k == "b_shunt_mvar":
                v[k] = el.b_shunt * base
            elif k == "pf":
                v[k] = ""
            elif k == "pf_lagging":
                v[k] = True
            else:
                v[k] = getattr(el, k)
        elif sk == "gen":
            if k == "p_mw":
                v[k] = el.p_gen * base
            elif k == "q_min_mvar":
                v[k] = _inf(el.q_min * base)
            elif k == "q_max_mvar":
                v[k] = _inf(el.q_max * base)
            else:
                v[k] = getattr(el, k)
        elif k == "input_mode" and sk == "transformer":
            v[k] = el.input_mode if el.input_mode in ("pu", "nameplate") else "pu"
        else:
            v[k] = getattr(el, k)
    return v


def _coerce(f, raw):
    if f.type == "bool":
        return bool(raw)
    if f.type == "choice":
        if str(raw) not in f.choices:
            raise ValueError(f"choose one of {', '.join(f.choices)}")
        return str(raw)
    if f.type == "str":
        return str(raw).strip()
    s = str(raw).strip()
    if f.type == "int":
        try:
            return int(float(s))
        except ValueError:
            raise ValueError("enter a whole number") from None
    try:
        x = float(s)
    except ValueError:
        raise ValueError("enter a number") from None
    if math.isnan(x) or (math.isinf(x) and not f.allow_inf):
        raise ValueError("enter a finite number")
    return x


def validate(net, kind, key, values):
    """{field: message} - empty when everything is acceptable."""
    sk = spec_kind(net, kind, key)
    errs, clean = {}, {}
    for f in SPECS[sk]:
        if f.type == "readonly" or f.key not in values:
            continue
        try:
            x = _coerce(f, values[f.key])
        except ValueError as e:
            if f.key == "pf" and str(values[f.key]).strip() == "":
                continue
            errs[f.key] = str(e)
            continue
        if f.type in ("float", "int") and not (isinstance(x, float) and math.isinf(x)):
            if f.min is not None and (x < f.min or (f.min_excl and x == f.min)):
                errs[f.key] = f"must be {'>' if f.min_excl else '>='} {f.min:g}"
            elif f.max is not None and x > f.max:
                errs[f.key] = f"must be <= {f.max:g}"
        clean[f.key] = x
    el = _element(net, kind, key)
    g = lambda k: clean.get(k, getattr(el, k, None))
    if sk == "bus":
        if "v_min" in clean and "v_max" in clean and clean["v_min"] >= clean["v_max"]:
            errs["v_min"] = "Vmin must be below Vmax"
        pf = str(values.get("pf", "")).strip()
        if pf:
            try:
                p = float(pf)
                if not 0 < abs(p) <= 1:
                    raise ValueError
            except ValueError:
                errs["pf"] = "power factor must be in (0, 1]"
        if clean.get("load_model") == "ZIP" and all(k in clean for k in ("zip_z", "zip_i", "zip_p")):
            if abs(clean["zip_z"] + clean["zip_i"] + clean["zip_p"] - 1) > 1e-6:
                errs["zip_p"] = "ZIP shares must sum to 1"
    if sk == "line":
        if g("input_mode") == "pu" and "r" in clean and "x" in clean and clean["r"] == 0 and clean["x"] == 0:
            errs["x"] = "R and X cannot both be zero"
        if g("input_mode") == "physical":
            if net.bus_kv(el.from_bus) <= 0:
                errs["input_mode"] = "set the from-bus nominal kV first (needed to convert ohm/km)"
    if sk == "transformer":
        lo, hi = g("tap_min"), g("tap_max")
        if lo is not None and hi is not None and lo > hi:
            errs["tap_min"] = "tap range min must not exceed max"
        elif "tap" in clean and lo is not None and hi is not None and not (lo - 1e-9 <= clean["tap"] <= hi + 1e-9):
            errs["tap"] = f"outside the tap range {lo:g}..{hi:g}"
        if g("input_mode") == "pu" and "r" in clean and "x" in clean and clean["r"] == 0 and clean["x"] == 0:
            errs["x"] = "R and X cannot both be zero"
    if sk == "gen" and "q_min_mvar" in clean and "q_max_mvar" in clean and clean["q_min_mvar"] > clean["q_max_mvar"]:
        errs["q_min_mvar"] = "Qmin must not exceed Qmax"
    return errs


def apply(net, kind, key, values):
    """Validate then write the values (converted to pu) into the model.  Returns {field: message}."""
    errs = validate(net, kind, key, values)
    if errs:
        return errs
    el = _element(net, kind, key)
    sk = spec_kind(net, kind, key)
    base = net.base_mva
    c = {f.key: _coerce(f, values[f.key]) for f in SPECS[sk]
         if f.type != "readonly" and f.key in values and not (f.key == "pf" and str(values[f.key]).strip() == "")}
    if sk == "bus":
        for k in ("name", "base_kv", "v_min", "v_max", "area", "vm", "va_deg", "load_model", "zip_z", "zip_i",
                  "zip_p", "in_service"):
            if k in c:
                setattr(el, k, c[k])
        el.p_load = c.get("p_load_mw", el.p_load * base) / base
        el.q_load = c.get("q_load_mvar", el.q_load * base) / base
        el.g_shunt = c.get("g_shunt_mw", el.g_shunt * base) / base
        el.b_shunt = c.get("b_shunt_mvar", el.b_shunt * base) / base
        if "pf" in c:                                   # power factor given: Q follows from P
            el.q_load = units.q_from_power_factor(el.p_load * base, abs(float(c["pf"])), c.get("pf_lagging", True)) / base
        if c.get("slack"):
            for o in net.buses:
                if o is not el and o.kind.upper() == "SLACK":
                    o.kind = "PQ"
            el.kind = "SLACK"
        elif "slack" in c and el.kind.upper() == "SLACK":
            el.kind = "PQ"
        derive_bus_kinds(net)
    elif sk == "gen":
        for k in ("name", "v_setpoint", "in_service"):
            if k in c:
                setattr(el, k, c[k])
        el.p_gen = c.get("p_mw", el.p_gen * base) / base
        el.q_min = c.get("q_min_mvar", el.q_min * base) / base
        el.q_max = c.get("q_max_mvar", el.q_max * base) / base
        derive_bus_kinds(net)
    else:
        for k, v in c.items():
            setattr(el, k, v)
        if sk == "line" and el.input_mode == "physical":
            el.r, el.x, el.b = units.line_pu_from_physical(el.length_km, el.r_ohm_km, el.x_ohm_km, el.b_us_km,
                                                           net.bus_kv(el.from_bus), base)
        if sk == "transformer":
            el.kind = "transformer"
            if el.input_mode == "nameplate":
                el.r, el.x = units.transformer_pu_from_nameplate(el.rated_mva, el.z_pct, el.x_r, base)
            el.rate_mva = el.rated_mva
    return {}


def derived_text(net, kind, key):
    """Per-unit values shown next to the physical inputs (B5)."""
    el = _element(net, kind, key)
    sk = spec_kind(net, kind, key)
    base = net.base_mva
    if sk in ("line", "transformer"):
        return (f"pu on {base:g} MVA:  R = {el.r:.5f}   X = {el.x:.5f}   B = {el.b:.5f}   |Z| = "
                f"{abs(complex(el.r, el.x)):.5f}")
    if sk == "bus":
        return (f"pu:  load = {el.p_load:.5f} + j{el.q_load:.5f}   shunt = {el.g_shunt:.5f} + j{el.b_shunt:.5f}")
    return f"P = {el.p_gen:.5f} pu on {base:g} MVA"
