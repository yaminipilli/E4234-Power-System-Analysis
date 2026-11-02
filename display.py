"""Result-display conventions shared by the GUI and the reports (toolkit independent)."""
import math

COL_NORMAL = "#334155"
COL_UNDER = "#2563eb"     # bus below Vmin
COL_OVER = "#9333ea"      # bus above Vmax
COL_AMBER = "#f59e0b"     # branch loading 80-100 %
COL_RED = "#dc2626"       # branch loading > 100 %
COL_GREY = "#9ca3af"      # out of service / stale
COL_WORST = "#ea580c"     # largest mismatch after divergence
AMBER_AT, RED_AT = 80.0, 100.0

LEGEND = [(COL_UNDER, "Bus below Vmin"), (COL_OVER, "Bus above Vmax"),
          (COL_AMBER, "Branch loading 80-100 %"), (COL_RED, "Branch loading > 100 %"),
          (COL_GREY, "Out of service (dashed) / stale results")]


def bus_state(vm, vmin, vmax):
    return "under" if vm < vmin else "over" if vm > vmax else "ok"


def loading_state(pct):
    if pct is None or (isinstance(pct, float) and math.isnan(pct)):
        return "ok"
    return "red" if pct > RED_AT else "amber" if pct >= AMBER_AT else "ok"


def bus_colour(state, in_service=True, stale=False):
    if not in_service:
        return COL_GREY
    if stale:
        return COL_GREY
    return {"under": COL_UNDER, "over": COL_OVER}.get(state, COL_NORMAL)


def branch_colour(pct, in_service=True, stale=False, has_result=True):
    if not in_service:
        return COL_GREY
    if not has_result:
        return COL_NORMAL
    if stale:
        return COL_GREY
    return {"red": COL_RED, "amber": COL_AMBER}.get(loading_state(pct), COL_NORMAL)


def bus_box_text(bus_row, show_kv=False):
    """V in pu (3 decimals) or kV, and angle in degrees (2 decimals)."""
    if show_kv and bus_row.get("vm_kv"):
        return f"V = {bus_row['vm_kv']:.2f} kV\n\u03b8 = {bus_row['va_deg']:.2f}\u00b0"
    return f"V = {bus_row['vm_pu']:.3f} pu\n\u03b8 = {bus_row['va_deg']:.2f}\u00b0"


def branch_box_text(br_row):
    """Sending-end P, Q with an arrow showing flow direction, and loading %."""
    arrow = "\u2192" if br_row["p_from_mw"] >= 0 else "\u2190"
    s = f"{arrow} {abs(br_row['p_from_mw']):.1f} MW  {br_row['q_from_mvar']:.1f} MVAr"
    if br_row.get("loading_percent") is not None:
        s += f"\n{br_row['loading_percent']:.0f} %"
    return s
