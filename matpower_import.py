
"""Import standard MATPOWER case files into our Network model."""

import re
from pathlib import Path

import numpy as np

from network import Bus, Branch, Generator, Network


def _read_matrix(text, name):
    """Read an mpc.bus / mpc.gen / mpc.branch numeric matrix."""
    match = re.search(
        rf"\bmpc\.{re.escape(name)}\s*=\s*\[(.*?)\]\s*;",
        text,
        flags=re.S,
    )
    if not match:
        raise ValueError(f"Could not find mpc.{name} matrix")

    rows = []
    for line in match.group(1).splitlines():
        line = line.split("%", 1)[0].strip()
        if not line:
            continue

        # MATPOWER rows are separated by semicolons or newlines.
        line = line.rstrip(";").strip()
        if not line:
            continue

        values = []
        for token in line.replace(",", " ").split():
            token = token.replace("D", "E").replace("d", "e")
            values.append(float(token))

        rows.append(values)

    if not rows:
        raise ValueError(f"mpc.{name} contains no rows")

    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError(f"Inconsistent row widths in mpc.{name}")

    return np.asarray(rows, dtype=float)


def _read_base_mva(text):
    """Read mpc.baseMVA."""
    match = re.search(
        r"\bmpc\.baseMVA\s*=\s*([0-9.eEdD+-]+)\s*;",
        text,
    )
    if not match:
        raise ValueError("Could not find mpc.baseMVA")

    return float(match.group(1).replace("D", "E").replace("d", "e"))


def load_matpower_case(filename):
    """Load a MATPOWER case into the project's Bus/Branch/Network classes."""
    text = Path(filename).read_text(encoding="utf-8")
    base_mva = _read_base_mva(text)

    bus_data = _read_matrix(text, "bus")
    gen_data = _read_matrix(text, "gen")
    branch_data = _read_matrix(text, "branch")

    # Preserve individual generator records.
    # MATPOWER generator powers and limits are in MW/MVAr;
    # convert them to per-unit on the network base.
    generator_records = []

    for index, row in enumerate(gen_data, start=1):
        bus_no = int(row[0])
        pg, qg = row[1], row[2]
        qmax, qmin = row[3], row[4]
        vg, mbase, status = row[5], row[6], row[7]
        pmax, pmin = row[8], row[9]

        generator_records.append(
            Generator(
                bus_number=bus_no,
                p_gen=float(pg / base_mva),
                q_gen=float(qg / base_mva),
                q_max=float(qmax / base_mva),
                q_min=float(qmin / base_mva),
                v_setpoint=float(vg),
                mbase_mva=float(mbase),
                in_service=bool(status > 0),
                p_max=float(pmax / base_mva),
                p_min=float(pmin / base_mva),
                name=f"Generator {index} at bus {bus_no}",
            )
        )

    # Aggregate online generators at each bus for the existing solver.
    generators = {}

    for row in gen_data:
        bus_no = int(row[0])
        pg, qg = row[1], row[2]
        qmax, qmin = row[3], row[4]
        vg, status = row[5], row[7]

        if status <= 0:
            continue

        entry = generators.setdefault(
            bus_no,
            {
                "pg": 0.0,
                "qg": 0.0,
                "qmin": 0.0,
                "qmax": 0.0,
                "vg_values": [],
            },
        )
        entry["pg"] += pg
        entry["qg"] += qg
        entry["qmin"] += qmin
        entry["qmax"] += qmax
        entry["vg_values"].append(vg)

    buses = []

    for row in bus_data:
        number = int(row[0])
        bus_type = int(row[1])

        pd, qd = row[2], row[3]
        gs, bs = row[4], row[5]
        vm, va = row[7], row[8]
        base_kv = row[9]
        vmax, vmin = row[11], row[12]

        gen = generators.get(number)

        if bus_type == 3:
            kind = "SLACK"
        elif bus_type == 2 and gen is not None:
            kind = "PV"
        else:
            kind = "PQ"

        # Use an online generator's voltage setpoint for PV/slack buses.
        if kind in ("PV", "SLACK") and gen is not None:
            vm = gen["vg_values"][0]

        buses.append(
            Bus(
                number=number,
                kind=kind,
                vm=float(vm),
                va_deg=float(va),
                p_load=float(pd / base_mva),
                q_load=float(qd / base_mva),
                p_gen=float(gen["pg"] / base_mva) if gen else 0.0,
                q_gen=float(gen["qg"] / base_mva) if gen else 0.0,
                q_min=float(gen["qmin"] / base_mva) if gen else -np.inf,
                q_max=float(gen["qmax"] / base_mva) if gen else np.inf,
                g_shunt=float(gs / base_mva),
                b_shunt=float(bs / base_mva),
                v_min=float(vmin),
                v_max=float(vmax),
                in_service=(bus_type != 4),
                name=f"Bus {number}",
                base_kv=float(base_kv),
                area=int(row[6]),
            )
        )

    branches = []

    for row in branch_data:
        from_bus, to_bus = int(row[0]), int(row[1])
        r, x, b = row[2], row[3], row[4]
        rate_a = row[5]
        tap = row[8]
        shift = row[9]
        status = row[10]

        # MATPOWER uses zero to indicate no off-nominal tap.
        if tap == 0:
            tap = 1.0

        branches.append(
            Branch(
                from_bus=from_bus,
                to_bus=to_bus,
                r=float(r),
                x=float(x),
                b=float(b),
                tap=float(tap),
                shift_deg=float(shift),
                rate_mva=float(rate_a),
                in_service=bool(status > 0),
                kind="transformer" if (tap != 1.0 or shift != 0.0) else "line",
            )
        )

    base_kv_by_bus = {
        int(row[0]): float(row[9]) for row in bus_data
    }

    net = Network(
        buses=buses,
        branches=branches,
        base_mva=base_mva,
        metadata={"base_kv_by_bus": base_kv_by_bus},
        generators=generator_records,
    )

    return net
