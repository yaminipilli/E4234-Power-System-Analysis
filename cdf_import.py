"""IEEE Common Data Format (CDF) reader -> Network.

Format: 'BUS DATA FOLLOWS' ... '-999', 'BRANCH DATA FOLLOWS' ... '-999'.  Bus name = columns 6-17, the rest of
each line is parsed as numbers.  Bus type: 0/1 PQ, 2 PV, 3 swing.  Tap ratio 0 means 1.
NOTE: written from the published format description; check it against your own CDF files.
"""
import numpy as np
from pathlib import Path

from network import Bus, Branch, Generator, Network


def load_cdf_case(path_or_text):
    p = Path(str(path_or_text))
    text = p.read_text(errors="ignore") if (len(str(path_or_text)) < 260 and p.exists()) else str(path_or_text)
    lines = text.splitlines()
    try:
        base = float(lines[0][31:37])
    except ValueError:
        base = 100.0
    buses, branches, gens, mode = [], [], [], None
    for ln in lines[1:]:
        s = ln.strip()
        up = s.upper()
        if not s:
            continue
        if up.startswith("END OF DATA"):
            break
        if up.startswith("BUS DATA FOLLOWS"):
            mode = "bus"; continue
        if up.startswith("BRANCH DATA FOLLOWS"):
            mode = "branch"; continue
        if up.startswith("-999") or "FOLLOWS" in up:
            mode = None; continue
        if mode == "bus":
            num = int(ln[:4])
            t = ln[17:].split()
            area, typ = int(t[0]), int(t[2])
            vm, va = float(t[3]), float(t[4])
            pl, ql, pgen, qgen = (float(x) for x in t[5:9])
            kv, vdes = float(t[9]), float(t[10])
            qmax, qmin = float(t[11]), float(t[12])
            gsh, bsh = float(t[13]), float(t[14])      # per unit on the case base
            kind = "SLACK" if typ == 3 else "PV" if typ == 2 else "PQ"
            vset = vdes if (kind != "PQ" and vdes > 0) else vm
            buses.append(Bus(num, kind, vm=vset if kind != "PQ" else vm, va_deg=va if typ == 3 else 0.0,
                             p_load=pl / base, q_load=ql / base, g_shunt=gsh, b_shunt=bsh,
                             name=ln[5:17].strip() or f"Bus {num}", base_kv=kv, area=area))
            if kind != "PQ" or pgen != 0:
                gens.append(Generator(num, p_gen=pgen / base, q_gen=qgen / base,
                                      q_max=(qmax / base) if typ == 2 else np.inf,
                                      q_min=(qmin / base) if typ == 2 else -np.inf,
                                      v_setpoint=vset, name=f"Generator at bus {num}"))
        elif mode == "branch":
            t = ln.split()
            btype = int(t[5])
            ratio = float(t[14]) if len(t) > 14 else 0.0
            shift = float(t[15]) if len(t) > 15 else 0.0
            tap = ratio if ratio > 0 else 1.0
            branches.append(Branch(int(t[0]), int(t[1]), float(t[6]), float(t[7]), float(t[8]),
                                   tap=tap, shift_deg=shift, rate_mva=float(t[9]),
                                   kind="transformer" if (btype > 0 or tap != 1.0 or shift) else "line"))
    return Network(buses, branches, base_mva=base, generators=gens)
