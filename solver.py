
"""Sparse polar Newton-Raphson AC power flow solver."""
import time

import numpy as np
from scipy.sparse import bmat, coo_matrix
from scipy.sparse.linalg import splu


class PowerFlowError(RuntimeError):
    pass


def _specified_power(net, vm):
    p = np.zeros(len(net.buses))
    q = np.zeros(len(net.buses))

    for i, b in enumerate(net.buses):
        if not b.in_service:
            continue

        model = b.load_model.upper()
        if model == "PQ":
            p[i] = b.p_gen - b.p_load
            q[i] = b.q_gen - b.q_load
        elif model == "Z":
            p[i] = b.p_gen - b.p_load * vm[i] ** 2
            q[i] = b.q_gen - b.q_load * vm[i] ** 2
        else:
            raise ValueError(
                f"Unsupported load model {b.load_model!r}; use PQ or Z"
            )

    return p, q


def calc_power(ybus, vm, va):
    V = vm * np.exp(1j * va)
    S = V * np.conj(ybus @ V)
    return S.real, S.imag



def _allocate_with_limits(total, generators, lower_attr, upper_attr):
    """Allocate a required total among generators subject to their limits."""
    if not generators:
        return []

    lower = np.array([getattr(g, lower_attr) for g in generators], dtype=float)
    upper = np.array([getattr(g, upper_attr) for g in generators], dtype=float)

    # Begin with equal sharing, then redistribute any amount clipped by limits.
    allocation = np.full(len(generators), total / len(generators), dtype=float)
    for _ in range(len(generators) + 1):
        allocation = np.maximum(allocation, lower)
        allocation = np.minimum(allocation, upper)
        difference = total - float(allocation.sum())
        if abs(difference) < 1e-10:
            break

        if difference > 0:
            available = np.flatnonzero(upper - allocation > 1e-12)
        else:
            available = np.flatnonzero(allocation - lower > 1e-12)
        if available.size == 0:
            break
        allocation[available] += difference / available.size

    return allocation.tolist()

def _jacobian(ybus, vm, va, p, q, angle_buses, pq_buses):
    """Build the analytical polar Jacobian directly from sparse Ybus entries.

    The four blocks are H=dP/dtheta, N=dP/dV, M=dQ/dtheta,
    and L=dQ/dV. No dense n-by-n matrices are created.
    """
    y = ybus.tocoo()
    n = len(vm)

    h_rows, h_cols, h_data = [], [], []
    n_rows, n_cols, n_data = [], [], []
    m_rows, m_cols, m_data = [], [], []
    l_rows, l_cols, l_data = [], [], []

    # Off-diagonal derivatives are nonzero only where Ybus has a
    # corresponding nonzero off-diagonal entry.
    for i, k, yik in zip(y.row, y.col, y.data):
        if i == k:
            continue
        g = float(yik.real)
        b = float(yik.imag)
        d = va[i] - va[k]
        sin_d = np.sin(d)
        cos_d = np.cos(d)
        h = vm[i] * vm[k] * (g * sin_d - b * cos_d)
        nn = vm[i] * (g * cos_d + b * sin_d)
        mm = -vm[i] * vm[k] * (g * cos_d + b * sin_d)
        ll = vm[i] * (g * sin_d - b * cos_d)

        # Keep the original full bus indices here. The slack/PV/PQ
        # submatrices are selected after the sparse blocks are assembled.
        h_rows.append(i); h_cols.append(k); h_data.append(h)
        n_rows.append(i); n_cols.append(k); n_data.append(nn)
        m_rows.append(i); m_cols.append(k); m_data.append(mm)
        l_rows.append(i); l_cols.append(k); l_data.append(ll)

    # Add diagonal derivatives explicitly, even if a zero diagonal entry
    # was not stored in the sparse Ybus structure.
    y_diag = ybus.diagonal()
    for i in range(n):
        g = float(y_diag[i].real)
        b = float(y_diag[i].imag)
        h = -q[i] - b * vm[i] ** 2
        nn = p[i] / vm[i] + g * vm[i]
        mm = p[i] - g * vm[i] ** 2
        ll = q[i] / vm[i] - b * vm[i]
        h_rows.append(i); h_cols.append(i); h_data.append(h)
        n_rows.append(i); n_cols.append(i); n_data.append(nn)
        m_rows.append(i); m_cols.append(i); m_data.append(mm)
        l_rows.append(i); l_cols.append(i); l_data.append(ll)

    shape = (n, n)
    H = coo_matrix((h_data, (h_rows, h_cols)), shape=shape).tocsr()
    N = coo_matrix((n_data, (n_rows, n_cols)), shape=shape).tocsr()
    M = coo_matrix((m_data, (m_rows, m_cols)), shape=shape).tocsr()
    L = coo_matrix((l_data, (l_rows, l_cols)), shape=shape).tocsr()

    H = H[angle_buses, :][:, angle_buses]
    N = N[angle_buses, :][:, pq_buses]
    M = M[pq_buses, :][:, angle_buses]
    L = L[pq_buses, :][:, pq_buses]

    return bmat([[H, N], [M, L]], format="csc")


def solve_power_flow(
    net,
    tolerance=1e-8,
    max_iterations=30,
    enforce_q_limits=True,
    initial_vm=None,
    initial_va_deg=None,
):
    """Solve AC load flow; powers are pu, reported branch flows are MW/MVAr."""
    start = time.perf_counter()
    ybus = net.ybus()
    n = len(net.buses)

    vm = np.array(
        initial_vm if initial_vm is not None else [b.vm for b in net.buses],
        dtype=float,
    )
    va = np.deg2rad(
        np.array(
            initial_va_deg
            if initial_va_deg is not None
            else [b.va_deg for b in net.buses],
            dtype=float,
        )
    )

    if vm.shape != (n,) or va.shape != (n,):
        raise ValueError("Initial V and angle vectors must match bus count")

    active = np.array([b.in_service for b in net.buses])
    slack = np.array(
        [
            i for i, b in enumerate(net.buses)
            if active[i] and b.kind.upper() == "SLACK"
        ],
        dtype=int,
    )
    if len(slack) != 1:
        raise ValueError("Exactly one in-service slack bus is required")

    slack_i = int(slack[0])
    original_pv = {
        i for i, b in enumerate(net.buses)
        if active[i] and b.kind.upper() == "PV"
    }
    pv = set(original_pv)
    pq = {
        i for i, b in enumerate(net.buses)
        if active[i] and b.kind.upper() == "PQ"
    }

    # Save generator settings so a solve does not permanently alter the model.
    original_q_gen = {i: b.q_gen for i, b in enumerate(net.buses)}

    limited = set()
    switches = []
    logs = []

    try:
        for iteration in range(max_iterations + 1):
            angle_buses = np.array(
                [i for i in range(n) if active[i] and i != slack_i],
                dtype=int,
            )
            pq_buses = np.array(sorted(pq), dtype=int)

            p_spec, q_spec = _specified_power(net, vm)
            p_calc, q_calc = calc_power(ybus, vm, va)

            mismatch = np.r_[
                p_spec[angle_buses] - p_calc[angle_buses],
                q_spec[pq_buses] - q_calc[pq_buses],
            ]
            maxmis = (
                float(np.max(np.abs(mismatch))) if mismatch.size else 0.0
            )
            logs.append(
                {"iteration": iteration, "max_mismatch_pu": maxmis}
            )

            if maxmis <= tolerance:
                break
            if iteration == max_iterations:
                break

            J = _jacobian(
                ybus, vm, va, p_calc, q_calc, angle_buses, pq_buses
            )
            try:
                dx = splu(J).solve(mismatch)
            except Exception as exc:
                raise PowerFlowError(
                    f"Sparse LU failed at iteration {iteration}: {exc}"
                ) from exc

            na = len(angle_buses)
            va[angle_buses] += dx[:na]
            vm[pq_buses] += dx[na:]

            if np.any(vm[pq_buses] <= 0):
                raise PowerFlowError(
                    "Newton-Raphson produced a non-positive voltage magnitude"
                )

            if not enforce_q_limits:
                continue

            p_calc, q_calc = calc_power(ybus, vm, va)
            switched = False
            newly_limited = set()

            # PV -> PQ: clamp generator Q at its violated limit.
            for i in list(pv):
                b = net.buses[i]
                load_factor = vm[i] ** 2 if b.load_model.upper() == "Z" else 1.0
                q_required = q_calc[i] + b.q_load * load_factor

                if (
                    q_required < b.q_min - 1e-10
                    or q_required > b.q_max + 1e-10
                ):
                    q_limit = min(max(q_required, b.q_min), b.q_max)
                    b.q_gen = q_limit

                    pv.remove(i)
                    pq.add(i)
                    limited.add(i)
                    newly_limited.add(i)
                    switched = True

                    switches.append(
                        {
                            "bus": b.number,
                            "from": "PV",
                            "to": "PQ",
                            "iteration": iteration,
                            "q_required_pu": float(q_required),
                            "q_limit_pu": float(q_limit),
                        }
                    )

            # PQ -> PV: only consider buses originally specified as PV
            # that were previously converted because of a Q limit.
            for i in list(limited):
                if i in newly_limited:
                    continue
                b = net.buses[i]

                # Estimate required Q at the original voltage setpoint.
                vm_trial = vm.copy()
                vm_trial[i] = b.vm
                _, q_trial = calc_power(ybus, vm_trial, va)

                load_factor = (
                    b.vm ** 2 if b.load_model.upper() == "Z" else 1.0
                )
                q_required = q_trial[i] + b.q_load * load_factor
                margin = 1e-6

                if (
                    b.q_min + margin < q_required
                    and q_required < b.q_max - margin
                ):
                    vm[i] = b.vm
                    b.q_gen = original_q_gen[i]

                    pq.remove(i)
                    pv.add(i)
                    limited.remove(i)
                    switched = True

                    switches.append(
                        {
                            "bus": b.number,
                            "iteration": iteration,
                            "from": "PQ",
                            "to": "PV",
                            "q_required_pu": float(q_required),
                            "q_limit_pu": None,
                        }
                    )

            # Bus-type changes require rebuilding the NR equations.
            if switched:
                continue

        if logs[-1]["max_mismatch_pu"] > tolerance:
            raise PowerFlowError(
                f"Did not converge in {max_iterations} iterations; "
                f"final mismatch={logs[-1]['max_mismatch_pu']:.3e} pu"
            )

        p_calc, q_calc = calc_power(ybus, vm, va)
        bus_results = []

        # Individual generator outputs. Values are in per unit internally;
        # the returned MW/MVAr values use the network base MVA.
        generator_results = []
        for i, b in enumerate(net.buses):
            bus_generators = [
                g for g in net.generators if g.bus_number == b.number
            ]
            if not bus_generators:
                continue

            if not b.in_service:
                for g in bus_generators:
                    generator_results.append({
                        "bus": g.bus_number,
                        "name": g.name,
                        "p_gen_pu": 0.0,
                        "q_gen_pu": 0.0,
                        "p_gen_mw": 0.0,
                        "q_gen_mvar": 0.0,
                        "in_service": False,
                    })
                continue

            online_generators = [g for g in bus_generators if g.in_service]
            for g in bus_generators:
                if not g.in_service:
                    generator_results.append({
                        "bus": g.bus_number,
                        "name": g.name,
                        "p_gen_pu": 0.0,
                        "q_gen_pu": 0.0,
                        "p_gen_mw": 0.0,
                        "q_gen_mvar": 0.0,
                        "in_service": False,
                    })

            if not online_generators:
                continue

            load_factor = vm[i] ** 2 if b.load_model.upper() == "Z" else 1.0
            p_load = b.p_load * load_factor
            q_load = b.q_load * load_factor
            p_total = float(p_calc[i] + p_load)
            q_total = float(q_calc[i] + q_load)


            # MATPOWER treats non-slack generator active powers as specified.
            # Slack generation is solved by the load flow and shared among
            # online generators subject to their individual P limits.
            if b.kind.upper() == "SLACK":
                p_values = _allocate_with_limits(
                    p_total, online_generators, "p_min", "p_max"
                )
            else:
                p_values = [g.p_gen for g in online_generators]

            # Reactive output is solved by the power-flow solution at
            # slack and PV buses, and at buses converted from PV to PQ.
            if b.kind.upper() in ("SLACK", "PV") or i in limited:
                if enforce_q_limits:
                    q_values = _allocate_with_limits(
                        q_total, online_generators, "q_min", "q_max"
                    )
                else:
                    # With Q-limit enforcement disabled, report the solved
                    # reactive power without clipping it to generator limits.
                    # For multiple generators at one bus, share it equally.
                    q_share = q_total / len(online_generators)
                    q_values = [q_share] * len(online_generators)
            else:
                q_values = [g.q_gen for g in online_generators]

            for g, pg, qg in zip(online_generators, p_values, q_values):
                generator_results.append({
                    "bus": g.bus_number,
                    "name": g.name,
                    "p_gen_pu": float(pg),
                    "q_gen_pu": float(qg),
                    "p_gen_mw": float(pg * net.base_mva),
                    "q_gen_mvar": float(qg * net.base_mva),
                    "in_service": True,
                })

        for i, b in enumerate(net.buses):
            bus_type = (
                "PQ" if i in limited else b.kind.upper()
            )
            bus_results.append(
                {
                    "bus": b.number,
                    "name": b.name,
                    "type": bus_type,
                    "vm_pu": float(vm[i]),
                    "vm_kv": float(
                        vm[i]
                        * net.metadata.get("base_kv_by_bus", {}).get(
                            b.number, 1.0
                        )
                    ),
                    "va_deg": float(np.rad2deg(va[i])),
                    "p_injection_pu": float(p_calc[i]),
                    "q_injection_pu": float(q_calc[i]),
                    "p_injection_mw": float(p_calc[i] * net.base_mva),
                    "q_injection_mvar": float(q_calc[i] * net.base_mva),
                    "q_limit_hit": i in limited,
                }
            )

        branch_results = []
        total_loss = 0j

        for br in net.branches:
            if (
               not br.in_service
               or not net.buses[net.index[br.from_bus]].in_service
               or not net.buses[net.index[br.to_bus]].in_service
             ):
                continue

            i = net.index[br.from_bus]
            j = net.index[br.to_bus]
            y = 1 / complex(br.r, br.x)
            tap = br.tap * np.exp(1j * np.deg2rad(br.shift_deg))

            yff = (y + 1j * br.b / 2) / abs(tap) ** 2
            yft = -y / np.conj(tap)
            ytf = -y / tap
            ytt = y + 1j * br.b / 2

            Vi = vm[i] * np.exp(1j * va[i])
            Vj = vm[j] * np.exp(1j * va[j])

            Sf = Vi * np.conj(yff * Vi + yft * Vj) * net.base_mva
            St = Vj * np.conj(ytf * Vi + ytt * Vj) * net.base_mva
            loss = Sf + St
            total_loss += loss

            loading = (
                max(abs(Sf), abs(St)) / br.rate_mva * 100
                if br.rate_mva > 0
                else None
            )

            branch_results.append(
                {
                    "from_bus": br.from_bus,
                    "to_bus": br.to_bus,
                    "p_from_mw": float(Sf.real),
                    "q_from_mvar": float(Sf.imag),
                    "p_to_mw": float(St.real),
                    "q_to_mvar": float(St.imag),
                    "s_from_mva": float(abs(Sf)),
                    "s_to_mva": float(abs(St)),
                    "loading_percent": (
                        float(loading) if loading is not None else None
                    ),
                    "p_loss_mw": float(loss.real),
                    "q_loss_mvar": float(loss.imag),
                }
            )

        return {
            "converged": True,
            "iterations": logs[-1]["iteration"],
            "max_mismatch_pu": logs[-1]["max_mismatch_pu"],
            "solve_time_s": time.perf_counter() - start,
            "bus_results": bus_results,
            "generator_results": generator_results,
            "branch_results": branch_results,
            "total_p_loss_mw": float(total_loss.real),
            "total_q_loss_mvar": float(total_loss.imag),
            "iteration_log": logs,
            "q_limit_switches": switches,
        }

    finally:
        # Do not leave temporary Q-limit values in the network model.
        for i, b in enumerate(net.buses):
            b.q_gen = original_q_gen[i]
