"""Sparse polar Newton-Raphson AC power flow solver.

Equations (polar form, V_i = |V_i| exp(j theta_i)):
    P_i = |V_i| sum_k |V_k| (G_ik cos(th_i-th_k) + B_ik sin(th_i-th_k))
    Q_i = |V_i| sum_k |V_k| (G_ik sin(th_i-th_k) - B_ik cos(th_i-th_k))
Unknowns: theta at PV and PQ buses, |V| at PQ buses.  Mismatch  F = [Pspec-Pcalc ; Qspec-Qcalc].
Voltage-dependent loads (Z / ZIP) make the specified power a function of |V|:
    Pspec(V) = Pgen - P0 f(V),  f(V) = kz V^2 + ki V + kp
so the Jacobian carries the extra term  +P0 f'(V)  on the diagonal of dP/dV (same for Q).
Each correction  J dx = F  is solved with a sparse LU factorisation (no explicit inverse).

Generator reactive limits are enforced in an OUTER loop: converge, test Qg of every PV bus,
switch violators to PQ at the limit (PV->PQ), switch back (PQ->PV) when the voltage recovers,
then re-converge.  Every switch is logged.
"""
import time

import numpy as np
from scipy.sparse import bmat, coo_matrix, diags
from scipy.sparse.linalg import splu

PQ_T, PV_T, REF_T = 0, 1, 2


class PowerFlowError(RuntimeError):
    pass


# --------------------------------------------------------------------------- basic maths
def calc_power(ybus, vm, va):
    """Calculated bus injections S = V conj(Ybus V) -> (P, Q) in pu."""
    V = vm * np.exp(1j * va)
    S = V * np.conj(ybus @ V)
    return S.real, S.imag


def _jacobian(ybus, vm, va, p, q, angle_buses, pq_buses, dpspec_dvm=None, dqspec_dvm=None):
    """Analytical polar Jacobian assembled as a sparse matrix.

    Blocks:  H = dP/dtheta,  N = dP/dV,  M = dQ/dtheta,  L = dQ/dV  (J = [[H, N], [M, L]]).
    Off-diagonal (i != k), with G_ik + jB_ik = Y_ik and th_ik = th_i - th_k:
        H_ik =  Vi Vk (G sin - B cos)        N_ik = Vi (G cos + B sin)
        M_ik = -Vi Vk (G cos + B sin)        L_ik = Vi (G sin - B cos)
    Diagonal:
        H_ii = -Q_i - B_ii Vi^2              N_ii = P_i/Vi + G_ii Vi
        M_ii =  P_i - G_ii Vi^2              L_ii = Q_i/Vi - B_ii Vi
    ``dpspec_dvm`` / ``dqspec_dvm`` (per bus) are d(Pspec)/d|V| and d(Qspec)/d|V| of
    voltage-dependent loads; they are subtracted from the diagonals of N and L.
    Only the structural non-zeros of Ybus are visited (vectorised, no dense matrices).
    """
    n = len(vm)
    y = ybus.tocoo()
    off = y.row != y.col
    i, k, yik = y.row[off], y.col[off], y.data[off]
    g, b = yik.real, yik.imag
    d = va[i] - va[k]
    s, c = np.sin(d), np.cos(d)
    h = vm[i] * vm[k] * (g * s - b * c)
    nn = vm[i] * (g * c + b * s)
    mm = -vm[i] * vm[k] * (g * c + b * s)
    ll = vm[i] * (g * s - b * c)

    yd = ybus.diagonal()
    gd, bd = yd.real, yd.imag
    ar = np.arange(n)
    h_d = -q - bd * vm ** 2
    n_d = p / vm + gd * vm
    m_d = p - gd * vm ** 2
    l_d = q / vm - bd * vm
    if dpspec_dvm is not None:
        n_d = n_d - dpspec_dvm
    if dqspec_dvm is not None:
        l_d = l_d - dqspec_dvm

    def build(off_vals, diag_vals):
        return coo_matrix((np.r_[off_vals, diag_vals], (np.r_[i, ar], np.r_[k, ar])), shape=(n, n)).tocsr()

    H, N, M, L = build(h, h_d), build(nn, n_d), build(mm, m_d), build(ll, l_d)
    H = H[angle_buses, :][:, angle_buses]
    N = N[angle_buses, :][:, pq_buses]
    M = M[pq_buses, :][:, angle_buses]
    L = L[pq_buses, :][:, pq_buses]
    return bmat([[H, N], [M, L]], format="csc")


def _allocate_with_limits(total, generators, lower_attr, upper_attr, clip=True):
    """Share ``total`` among generators (equal shares, respecting limits when ``clip``)."""
    m = len(generators)
    if m == 0:
        return []
    if not clip:
        return [total / m] * m
    lower = np.array([getattr(g, lower_attr) for g in generators], dtype=float)
    upper = np.array([getattr(g, upper_attr) for g in generators], dtype=float)
    alloc = np.full(m, total / m, dtype=float)
    for _ in range(m + 1):
        alloc = np.minimum(np.maximum(alloc, lower), upper)
        diff = total - float(alloc.sum())
        if abs(diff) < 1e-12:
            break
        free = np.flatnonzero((upper - alloc > 1e-12) if diff > 0 else (alloc - lower > 1e-12))
        if free.size == 0:
            break
        alloc[free] += diff / free.size
    alloc[0] += total - float(alloc.sum())      # anything the limits cannot absorb goes to unit 1
    return alloc.tolist()


# --------------------------------------------------------------------------- Newton core
def _newton(ybus, vm0, va0, pconst, qconst, P0, Q0, kz, ki, kp, ang, pq, tol, max_iter, on_iter):
    vm, va = vm0.copy(), va0.copy()
    npq_ang = len(ang)
    for it in range(max_iter + 1):
        f = kz * vm ** 2 + ki * vm + kp
        df = 2 * kz * vm + ki
        pspec, qspec = pconst - P0 * f, qconst - Q0 * f
        pc, qc = calc_power(ybus, vm, va)
        mis = np.r_[(pspec - pc)[ang], (qspec - qc)[pq]]
        nrm = float(np.max(np.abs(mis))) if mis.size else 0.0
        if on_iter:
            on_iter(it, nrm)
        if not np.isfinite(nrm) or nrm > 1e10:
            return False, it, vm, va, pc, qc, "diverged: mismatch became unbounded"
        if nrm <= tol:
            return True, it, vm, va, pc, qc, "converged"
        if it == max_iter:
            return False, it, vm, va, pc, qc, f"no convergence in {max_iter} iterations"
        J = _jacobian(ybus, vm, va, pc, qc, ang, pq, dpspec_dvm=-P0 * df, dqspec_dvm=-Q0 * df)
        try:
            dx = splu(J).solve(mis)
        except Exception as exc:                  # exactly singular Jacobian (islands, collapse)
            return False, it, vm, va, pc, qc, f"sparse LU failed ({exc})"
        if not np.all(np.isfinite(dx)):
            return False, it, vm, va, pc, qc, "singular Jacobian"
        va[ang] += dx[:npq_ang]
        vm[pq] += dx[npq_ang:]
        if np.any(vm[pq] <= 0):
            return False, it + 1, vm, va, pc, qc, "diverged: non-positive voltage magnitude"
    raise AssertionError("unreachable")


def _q_limit_step(types, orig_types, vm, qg_bus, qmin, qmax, vset, side, qfix, count, buses,
                  base, iteration, q_tol=1e-6, max_switches=6):
    """PV->PQ / PQ->PV decisions.  Modifies types/side/qfix/count in place; returns event dicts."""
    events = []
    for i in np.where(orig_types == PV_T)[0]:
        num = buses[i].number
        if types[i] == PV_T:
            q = qg_bus[i]
            if q > qmax[i] + q_tol:
                types[i], side[i], qfix[i] = PQ_T, 1, qmax[i]
                count[i] += 1
                events.append(dict(bus=num, bus_index=int(i), **{"from": "PV", "to": "PQ"}, limit="Qmax",
                                   iteration=iteration, q_required_pu=float(q), q_limit_pu=float(qmax[i]),
                                   vm_pu=float(vm[i])))
            elif q < qmin[i] - q_tol:
                types[i], side[i], qfix[i] = PQ_T, -1, qmin[i]
                count[i] += 1
                events.append(dict(bus=num, bus_index=int(i), **{"from": "PV", "to": "PQ"}, limit="Qmin",
                                   iteration=iteration, q_required_pu=float(q), q_limit_pu=float(qmin[i]),
                                   vm_pu=float(vm[i])))
        elif side[i] != 0 and count[i] < max_switches:
            back = (side[i] > 0 and vm[i] > vset[i] + 1e-9) or (side[i] < 0 and vm[i] < vset[i] - 1e-9)
            if back:
                events.append(dict(bus=num, bus_index=int(i), **{"from": "PQ", "to": "PV"},
                                   limit="Qmax" if side[i] > 0 else "Qmin", iteration=iteration,
                                   q_required_pu=float(qfix[i]), q_limit_pu=None, vm_pu=float(vm[i])))
                types[i], side[i] = PV_T, 0
                count[i] += 1
    return events


# --------------------------------------------------------------------------- driver
def solve_power_flow(
    net,
    tolerance=1e-8,
    max_iterations=30,
    enforce_q_limits=False,
    initial_vm=None,
    initial_va_deg=None,
    flat_start=True,
    callback=None,
    max_q_rounds=30,
):
    """Solve the AC load flow.  Returns a dict (never raises for non-convergence).

    flat_start=True : |V| = 1 at PQ buses, angle 0 (slack keeps its angle).
    flat_start=False: use ``initial_vm`` / ``initial_va_deg`` (e.g. the previous solution) if given,
                      otherwise the voltages stored in the bus data.
    PV and slack voltages are ALWAYS reset to their set-points.
    ``callback(str)`` receives progress lines (iteration mismatch, PV/PQ switches).
    Powers inside are per unit; reported branch/bus values are also given in MW/MVAr.
    """
    t_start = time.perf_counter()
    errs = net.errors()
    if errs:
        raise ValueError("Network check failed: " + "; ".join(i.message for i in errs))

    def say(msg):
        if callback:
            callback(msg)

    buses, n, base = net.buses, len(net.buses), net.base_mva
    ybus = net.ybus()
    active = np.array([b.in_service for b in buses])
    pg, qg_sched, qmin, qmax, vset, has_gen = net.bus_generation()

    orig_types = np.full(n, PQ_T, dtype=int)
    for i, b in enumerate(buses):
        if not active[i]:
            continue
        kind = b.kind.upper()
        if kind == "SLACK":
            orig_types[i] = REF_T
        elif kind == "PV" and has_gen[i]:
            orig_types[i] = PV_T
    ref = int(np.where(orig_types == REF_T)[0][0])
    types = orig_types.copy()

    P0 = np.array([b.p_load for b in buses], float)
    Q0 = np.array([b.q_load for b in buses], float)
    P0[~active] = 0.0
    Q0[~active] = 0.0
    # load model coefficients f(V) = kz V^2 + ki V + kp
    kz, ki, kp = np.zeros(n), np.zeros(n), np.ones(n)
    for i, b in enumerate(buses):
        m = b.load_model.upper()
        if m == "Z":
            kz[i], ki[i], kp[i] = 1.0, 0.0, 0.0
        elif m == "ZIP":
            kz[i], ki[i], kp[i] = b.zip_z, b.zip_i, b.zip_p

    # ---- initial point ------------------------------------------------------------------
    if flat_start:
        vm, va = np.ones(n), np.zeros(n)
    else:
        vm = np.array(initial_vm if initial_vm is not None else [b.vm for b in buses], float)
        va = np.deg2rad(np.array(initial_va_deg if initial_va_deg is not None
                                 else [b.va_deg for b in buses], float))
        if vm.shape != (n,) or va.shape != (n,):
            raise ValueError("Initial V and angle vectors must match bus count")
    va[ref] = np.deg2rad(buses[ref].va_deg)
    vm[~active] = 1.0

    side = np.zeros(n, int)
    qfix = np.zeros(n)
    count = np.zeros(n, int)
    log, switches = [], []
    total_iter, newton_time = 0, 0.0
    converged, message, rounds = False, "", 0
    pc = qc = np.zeros(n)

    for rounds in range(1, max_q_rounds + 1):
        fixed = active & (types != PQ_T)
        vm[fixed] = vset[fixed]                       # PV / slack voltages are always the set-point
        ang = np.where(active & (np.arange(n) != ref))[0]
        pq = np.where(active & (types == PQ_T))[0]
        pconst = pg.copy()
        qconst = np.where(side != 0, qfix, qg_sched)
        say(f"Round {rounds}: {int((types == PV_T).sum())} PV, {len(pq)} PQ buses")
        base_it = total_iter

        def on_iter(it, nrm, _b=base_it):
            log.append({"iteration": _b + it, "max_mismatch_pu": nrm, "round": rounds})
            say(f"  iteration {_b + it:3d}   max mismatch = {nrm:.4e} pu")

        t0 = time.perf_counter()
        ok, it, vm, va, pc, qc, message = _newton(ybus, vm, va, pconst, qconst, P0, Q0, kz, ki, kp,
                                                  ang, pq, tolerance, max_iterations, on_iter)
        newton_time += time.perf_counter() - t0
        total_iter += it
        if not ok:
            say("NOT converged: " + message)
            break
        if not enforce_q_limits:
            converged = True
            break
        f = kz * vm ** 2 + ki * vm + kp
        qg_bus = qc + Q0 * f                           # reactive generation required at each bus
        events = _q_limit_step(types, orig_types, vm, qg_bus, qmin, qmax, vset, side, qfix, count,
                               buses, base, total_iter)
        for ev in events:
            switches.append(ev)
            say(f"  SWITCH bus {ev['bus']}: {ev['from']}->{ev['to']} at {ev['limit']} "
                f"(Q={ev['q_required_pu'] * base:.3f} MVAr, V={ev['vm_pu']:.5f})")
            if ev["to"] == "PV":
                vm[ev["bus_index"]] = vset[ev["bus_index"]]
        if not events:
            converged = True
            break
    else:
        message = f"PV/PQ switching did not settle in {max_q_rounds} rounds"
        say(message)

    if converged:
        say(f"Converged in {total_iter} iteration(s), max mismatch {log[-1]['max_mismatch_pu']:.2e} pu")

    # ======================================================================= results
    f = kz * vm ** 2 + ki * vm + kp
    pload, qload = P0 * f, Q0 * f
    pc, qc = calc_power(ybus, vm, va)
    pgen_bus, qgen_bus = pc + pload, qc + qload
    pspec = pg - pload
    qspec = np.where(side != 0, qfix, qg_sched) - qload
    mis_bus = np.zeros(n)
    nonref = active & (np.arange(n) != ref)
    mis_bus[nonref] = np.abs(pspec - pc)[nonref]
    pqm = active & (types == PQ_T)
    mis_bus[pqm] = np.maximum(mis_bus[pqm], np.abs(qspec - qc)[pqm])
    worst = [buses[i].number for i in np.argsort(-mis_bus)[:5]] if not converged else []

    V = vm * np.exp(1j * va)
    ysh = np.array([complex(b.g_shunt, b.b_shunt) if b.in_service else 0j for b in buses])
    ssh = np.abs(V) ** 2 * np.conj(ysh)

    bus_results = []
    for i, b in enumerate(buses):
        kv = net.bus_kv(b.number)
        on = bool(active[i])
        bus_results.append({
            "bus": b.number, "name": b.name, "in_service": on,
            "type": ("PQ" if (types[i] == PQ_T and orig_types[i] == PV_T) else b.kind.upper()) if on else "OUT",
            "vm_pu": float(vm[i]) if on else 0.0,
            "vm_kv": float(vm[i] * kv) if on else 0.0,
            "va_deg": float(np.rad2deg(va[i])) if on else 0.0,
            "p_injection_pu": float(pc[i]), "q_injection_pu": float(qc[i]),
            "p_injection_mw": float(pc[i] * base), "q_injection_mvar": float(qc[i] * base),
            "p_gen_mw": float(pgen_bus[i] * base), "q_gen_mvar": float(qgen_bus[i] * base),
            "p_load_mw": float(pload[i] * base), "q_load_mvar": float(qload[i] * base),
            "p_shunt_mw": float(ssh[i].real * base), "q_shunt_mvar": float(ssh[i].imag * base),
            "q_limit_hit": bool(side[i] != 0),
            "limit": "Qmax" if side[i] > 0 else "Qmin" if side[i] < 0 else "",
            "mismatch_pu": float(mis_bus[i]),
        })

    # ---- generators ---------------------------------------------------------------------
    generator_results = []
    gens_by_bus = {}
    records = net.generators
    if records:
        for g in records:
            gens_by_bus.setdefault(net.index[g.bus_number], []).append(g)
    else:                                              # hand-built network: one pseudo unit per bus
        from network import Generator
        for i, b in enumerate(buses):
            if has_gen[i]:
                gens_by_bus[i] = [Generator(b.number, b.p_gen, b.q_gen, b.q_max, b.q_min, b.vm,
                                            name=f"Generator at bus {b.number}")]
    for i, gl in sorted(gens_by_bus.items()):
        b = buses[i]
        online = [g for g in gl if g.in_service] if active[i] else []
        for g in gl:
            if g not in online:
                generator_results.append({"bus": g.bus_number, "name": g.name, "p_gen_pu": 0.0,
                                          "q_gen_pu": 0.0, "p_gen_mw": 0.0, "q_gen_mvar": 0.0,
                                          "in_service": False, "limit": ""})
        if not online:
            continue
        if types[i] == REF_T:
            p_vals = _allocate_with_limits(float(pgen_bus[i]), online, "p_min", "p_max")
        else:
            p_vals = [g.p_gen for g in online]
        if types[i] == REF_T:
            q_vals = _allocate_with_limits(float(qgen_bus[i]), online, "q_min", "q_max", clip=False)
        elif orig_types[i] == PV_T:
            q_vals = _allocate_with_limits(float(qgen_bus[i]), online, "q_min", "q_max",
                                           clip=bool(enforce_q_limits))
        else:
            q_vals = [g.q_gen for g in online]
        for g, p_, q_ in zip(online, p_vals, q_vals):
            generator_results.append({"bus": g.bus_number, "name": g.name, "p_gen_pu": float(p_),
                                      "q_gen_pu": float(q_), "p_gen_mw": float(p_ * base),
                                      "q_gen_mvar": float(q_ * base), "in_service": True,
                                      "limit": ("Qmax" if side[i] > 0 else "Qmin" if side[i] < 0 else "")})

    # ---- branches (one row per branch, in the same order as net.branches) ------------------
    active_br = {k: (i, j) for k, i, j, _ in net.active_branches()}
    branch_results, total_loss = [], 0j
    for k, br in enumerate(net.branches):
        row = {"index": k, "name": br.name, "from_bus": br.from_bus, "to_bus": br.to_bus,
               "in_service": br.in_service, "active": k in active_br}
        if k in active_br:
            i, j = active_br[k]
            yff, yft, ytf, ytt = br.admittances()          # same model as Ybus
            Sf = V[i] * np.conj(yff * V[i] + yft * V[j]) * base
            St = V[j] * np.conj(ytf * V[i] + ytt * V[j]) * base
            loss = Sf + St
            total_loss += loss
            kv = net.bus_kv(br.from_bus)
            amps_ka = abs(yff * V[i] + yft * V[j]) * base / (np.sqrt(3) * kv) if kv > 0 else None
            row.update({"p_from_mw": float(Sf.real), "q_from_mvar": float(Sf.imag),
                        "p_to_mw": float(St.real), "q_to_mvar": float(St.imag),
                        "s_from_mva": float(abs(Sf)), "s_to_mva": float(abs(St)),
                        "current_from_ka": None if amps_ka is None else float(amps_ka),
                        "loading_percent": float(max(abs(Sf), abs(St)) / br.rate_mva * 100)
                        if br.rate_mva > 0 else None,
                        "p_loss_mw": float(loss.real), "q_loss_mvar": float(loss.imag)})
        else:
            row.update({"p_from_mw": 0.0, "q_from_mvar": 0.0, "p_to_mw": 0.0, "q_to_mvar": 0.0,
                        "s_from_mva": 0.0, "s_to_mva": 0.0, "current_from_ka": None,
                        "loading_percent": None, "p_loss_mw": 0.0, "q_loss_mvar": 0.0})
        branch_results.append(row)

    final_mis = log[-1]["max_mismatch_pu"] if log else 0.0
    return {
        "converged": bool(converged),
        "message": message if not converged else "converged",
        "iterations": total_iter,
        "q_rounds": rounds,
        "max_mismatch_pu": final_mis,
        "solve_time_s": time.perf_counter() - t_start,
        "newton_time_s": newton_time,
        "bus_results": bus_results,
        "generator_results": generator_results,
        "branch_results": branch_results,
        "total_p_loss_mw": float(total_loss.real),
        "total_q_loss_mvar": float(total_loss.imag),
        "iteration_log": log,
        "q_limit_switches": switches,
        "worst_buses": worst,
        "vm": vm.copy(),            # NumPy vectors, handy as the "previous solution" for a re-run
        "va_deg": np.rad2deg(va),
    }
