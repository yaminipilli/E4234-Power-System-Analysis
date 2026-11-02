"""Independent solution of a MATPOWER case, used to cross-check the Newton-Raphson engine.

Deliberately shares NO numerical code with solver.py / network.py: it re-reads the raw
bus/gen/branch matrices, stamps a DENSE Ybus branch by branch, and solves the polar power-flow
equations with scipy.optimize.root (Powell hybrid).  It is a verification tool only - it is
never used by the simulator itself.
"""
import numpy as np
from pathlib import Path
from scipy.optimize import root

from matpower_import import _read_matrix, _read_base_mva


def read_raw(path):
    text = Path(path).read_text(encoding="utf-8")
    return (_read_base_mva(text), _read_matrix(text, "bus"),
            _read_matrix(text, "gen"), _read_matrix(text, "branch"))


def stamp_ybus(base, bus, branch):
    nb = len(bus)
    idx = {int(r[0]): i for i, r in enumerate(bus)}
    Y = np.zeros((nb, nb), complex)
    for r in branch:
        if r[10] <= 0:
            continue
        f, t = idx[int(r[0])], idx[int(r[1])]
        ys = 1 / complex(r[2], r[3])
        bc = r[4]
        a = (r[8] if r[8] != 0 else 1.0) * np.exp(1j * np.deg2rad(r[9]))
        Y[f, f] += (ys + 0.5j * bc) / (a * np.conj(a))
        Y[t, t] += ys + 0.5j * bc
        Y[f, t] += -ys / np.conj(a)
        Y[t, f] += -ys / a
    for i, r in enumerate(bus):
        Y[i, i] += complex(r[4], r[5]) / base
    return Y, idx


def solve_independent(path):
    """Return dict(vm, va_deg, pf, qf, pt, qt, loss_mw, pg_slack) for flat start, Q limits off."""
    base, bus, gen, branch = read_raw(path)
    Y, idx = stamp_ybus(base, bus, branch)
    nb = len(bus)
    typ = bus[:, 1].astype(int)
    vset = bus[:, 7].copy()
    pg = np.zeros(nb)
    qg = np.zeros(nb)
    has = np.zeros(nb, bool)
    for r in gen:
        if r[7] <= 0:
            continue
        i = idx[int(r[0])]
        pg[i] += r[1] / base
        qg[i] += r[2] / base
        if not has[i]:
            vset[i] = r[5]
        has[i] = True
    typ = np.where((typ == 2) & ~has, 1, typ)
    ref = np.where(typ == 3)[0]
    pv = np.where(typ == 2)[0]
    pq = np.where(typ == 1)[0]
    ang = np.r_[pv, pq]
    G, B = Y.real, Y.imag
    pd = bus[:, 2] / base
    qd = bus[:, 3] / base

    def unpack(x):
        th = np.zeros(nb)
        vm = np.ones(nb)
        vm[ref] = vset[ref]
        vm[pv] = vset[pv]
        th[ref] = np.deg2rad(bus[ref, 8])
        th[ang] = x[:len(ang)]
        vm[pq] = x[len(ang):]
        return vm, th

    def F(x):
        vm, th = unpack(x)
        d = th[:, None] - th[None, :]
        P = vm * ((G * np.cos(d) + B * np.sin(d)) @ vm)
        Q = vm * ((G * np.sin(d) - B * np.cos(d)) @ vm)
        return np.r_[(P - (pg - pd))[ang], (Q - (qg - qd))[pq]]

    x0 = np.r_[np.zeros(len(ang)), np.ones(len(pq))]
    sol = root(F, x0, method="hybr", tol=1e-13)
    if not sol.success or np.max(np.abs(F(sol.x))) > 1e-9:
        raise RuntimeError("independent solver did not converge: " + sol.message)
    vm, th = unpack(sol.x)
    V = vm * np.exp(1j * th)
    pf, qf, pt, qt = [], [], [], []
    for r in branch:
        if r[10] <= 0:
            pf.append(0.0); qf.append(0.0); pt.append(0.0); qt.append(0.0)
            continue
        f, t = idx[int(r[0])], idx[int(r[1])]
        ys = 1 / complex(r[2], r[3])
        a = (r[8] if r[8] != 0 else 1.0) * np.exp(1j * np.deg2rad(r[9]))
        yff = (ys + 0.5j * r[4]) / abs(a) ** 2
        yft = -ys / np.conj(a)
        ytf = -ys / a
        ytt = ys + 0.5j * r[4]
        sf = V[f] * np.conj(yff * V[f] + yft * V[t]) * base
        st = V[t] * np.conj(ytf * V[f] + ytt * V[t]) * base
        pf.append(sf.real); qf.append(sf.imag); pt.append(st.real); qt.append(st.imag)
    return {"vm": vm.tolist(), "va_deg": np.rad2deg(th).tolist(), "pf": pf, "qf": qf, "pt": pt,
            "qt": qt, "loss_mw": float(np.sum(pf) + np.sum(pt))}
