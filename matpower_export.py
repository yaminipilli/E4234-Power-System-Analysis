"""Export a Network as a MATPOWER case file (File > Export case as MATPOWER)."""
import numpy as np


def _num(v, inf_value=9999.0):
    if v is None or not np.isfinite(v):
        return inf_value if (v is None or v > 0) else -inf_value
    return v


def save_matpower_case(net, path, function_name="case_export"):
    """Write bus / gen / branch matrices.  Z and ZIP loads are exported at their nominal (1 pu) values."""
    base = net.base_mva
    code = {"PQ": 1, "PV": 2, "SLACK": 3}
    pg, qg, qmin, qmax, vset, has = net.bus_generation()
    lines = [f"function mpc = {function_name}", "mpc.version = '2';", f"mpc.baseMVA = {base:g};", "mpc.bus = ["]
    for i, b in enumerate(net.buses):
        t = code[b.kind.upper()] if b.in_service else 4
        kv = net.bus_kv(b.number)
        vm = vset[i] if b.kind.upper() != "PQ" else b.vm
        lines.append(f"\t{b.number}\t{t}\t{b.p_load * base:.6g}\t{b.q_load * base:.6g}\t{b.g_shunt * base:.6g}\t"
                     f"{b.b_shunt * base:.6g}\t{b.area}\t{vm:.6g}\t{b.va_deg:.6g}\t{kv:g}\t1\t{b.v_max:g}\t{b.v_min:g};")
    lines += ["];", "mpc.gen = ["]
    gens = net.generators
    if gens:
        for g in gens:
            lines.append(f"\t{g.bus_number}\t{g.p_gen * base:.6g}\t{g.q_gen * base:.6g}\t{_num(g.q_max * base):.6g}\t"
                         f"{_num(g.q_min * base):.6g}\t{g.v_setpoint:.6g}\t{g.mbase_mva:g}\t{int(g.in_service)}\t"
                         f"{_num(g.p_max * base):.6g}\t{_num(g.p_min * base, 0.0):.6g};")
    else:
        for i, b in enumerate(net.buses):
            if has[i]:
                lines.append(f"\t{b.number}\t{pg[i] * base:.6g}\t{qg[i] * base:.6g}\t{_num(qmax[i] * base):.6g}\t"
                             f"{_num(qmin[i] * base):.6g}\t{vset[i]:.6g}\t{base:g}\t1\t9999\t0;")
    lines += ["];", "mpc.branch = ["]
    for br in net.branches:
        if br.tap_side == "to" and (br.tap != 1.0 or br.shift_deg != 0.0):
            raise ValueError("MATPOWER cannot represent a tap / phase shifter on the 'to' side; "
                             "swap the branch end points first")
        lines.append(f"\t{br.from_bus}\t{br.to_bus}\t{br.r:.8g}\t{br.x:.8g}\t{br.b:.8g}\t{br.rate_mva:g}\t0\t0\t"
                     f"{br.tap if br.tap != 1.0 else 0:g}\t{br.shift_deg:g}\t{int(br.in_service)}\t-360\t360;")
    lines.append("];")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
