"""Network data models and sparse Ybus construction for the E4234 assignment.

Units: powers / impedances in per unit on ``Network.base_mva`` unless a field name says otherwise.
Loads are positive consumption; generator set-points are positive generation.
"""

from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import connected_components

PQ, PV, SLACK = "PQ", "PV", "SLACK"


@dataclass
class Bus:
    number: int
    kind: str = "PQ"                 # SLACK, PV, PQ
    vm: float = 1.0                  # pu; for PV / SLACK buses this is the voltage set-point
    va_deg: float = 0.0              # slack angle (degrees) / initial guess elsewhere
    p_load: float = 0.0              # pu, positive consumption (value at 1 pu voltage)
    q_load: float = 0.0              # pu, positive consumption (value at 1 pu voltage)
    p_gen: float = 0.0               # pu generation  (used only if the network has no Generator records)
    q_gen: float = 0.0               # pu generation / scheduled Q for PQ buses
    q_min: float = -np.inf           # pu, aggregate generator limits (legacy, no Generator records)
    q_max: float = np.inf
    g_shunt: float = 0.0             # pu at V=1
    b_shunt: float = 0.0             # pu at V=1
    v_min: float = 0.95
    v_max: float = 1.05
    in_service: bool = True
    name: str = ""
    load_model: str = "PQ"           # PQ, Z (constant impedance) or ZIP
    base_kv: float = 0.0             # nominal kV (0 = unknown)
    area: int = 1
    zip_z: float = 0.0               # ZIP shares of constant-Z / constant-I / constant-P (sum to 1)
    zip_i: float = 0.0
    zip_p: float = 1.0

    def load_factor(self, v):
        """Return (f, df/dV): load = S0 * f(V) for the bus load model."""
        m = self.load_model.upper()
        if m == "PQ":
            return 1.0, 0.0
        if m == "Z":
            return v * v, 2.0 * v
        if m == "ZIP":
            return (self.zip_z * v * v + self.zip_i * v + self.zip_p,
                    2.0 * self.zip_z * v + self.zip_i)
        raise ValueError(f"Unsupported load model {self.load_model!r}; use PQ, Z or ZIP")


@dataclass
class Branch:
    from_bus: int
    to_bus: int
    r: float
    x: float
    b: float = 0.0                   # TOTAL line charging susceptance (pu)
    tap: float = 1.0                 # off-nominal ratio
    shift_deg: float = 0.0
    rate_mva: float = 0.0            # 0 = unrated
    in_service: bool = True
    name: str = ""
    tap_side: str = "from"           # side carrying the tap / phase shifter: 'from' or 'to'
    kind: str = "line"               # 'line' | 'transformer'  (GUI information only)
    # ---- data-entry fields used by the GUI (the solver only needs r, x, b, tap, shift, rate) ----
    input_mode: str = "pu"           # lines: 'pu' or 'physical' (ohm/km, uS/km, km)
    length_km: float = 1.0
    r_ohm_km: float = 0.1
    x_ohm_km: float = 0.4
    b_us_km: float = 3.0
    rated_mva: float = 100.0         # transformer nameplate
    kv_hv: float = 0.0
    kv_lv: float = 0.0
    z_pct: float = 10.0
    x_r: float = 10.0
    tap_min: float = 0.9
    tap_max: float = 1.1
    tap_step: float = 0.0125

    def admittances(self):
        """(Yff, Yft, Ytf, Ytt) of the MATPOWER branch model (tap on either side)."""
        ys = 1.0 / complex(self.r, self.x)
        y0 = ys + 0.5j * self.b
        a = self.tap * np.exp(1j * np.deg2rad(self.shift_deg))
        a2 = abs(a) ** 2
        if self.tap_side == "to":
            return y0, -ys / a, -ys / np.conj(a), y0 / a2
        return y0 / a2, -ys / np.conj(a), -ys / a, y0


@dataclass
class Generator:
    """Individual generator.  When a network has Generator records they are the source of truth
    for generation data (several units may share a bus)."""

    bus_number: int
    p_gen: float = 0.0       # pu on network base
    q_gen: float = 0.0       # pu
    q_max: float = np.inf    # pu
    q_min: float = -np.inf   # pu
    v_setpoint: float = 1.0  # pu
    mbase_mva: float = 100.0
    in_service: bool = True
    p_max: float = np.inf    # pu
    p_min: float = -np.inf   # pu
    name: str = ""


@dataclass
class Issue:
    level: str       # 'error' | 'warning'
    message: str

    def __str__(self):
        return f"{self.level.upper()}: {self.message}"


@dataclass
class Network:
    buses: list
    branches: list
    base_mva: float = 100.0
    frequency_hz: float = 50.0
    metadata: dict = field(default_factory=dict)
    generators: list = field(default_factory=list)

    def __post_init__(self):
        nums = [b.number for b in self.buses]
        if len(nums) != len(set(nums)):
            raise ValueError("Bus numbers must be unique")
        if not self.buses:
            raise ValueError("Network must contain at least one bus")
        self._index = {number: i for i, number in enumerate(nums)}
        for br in self.branches:
            if br.from_bus not in self._index or br.to_bus not in self._index:
                raise ValueError(f"Branch references unknown bus: {br}")
        for gen in self.generators:
            if gen.bus_number not in self._index:
                raise ValueError(f"Generator references unknown bus: {gen.bus_number}")

    # ------------------------------------------------------------------ indexing
    def rebuild_index(self):
        self._index = {b.number: i for i, b in enumerate(self.buses)}

    @property
    def index(self):
        """Map external bus numbers to internal zero-based indices (rebuilt if buses changed)."""
        if len(self._index) != len(self.buses) or any(
                self._index.get(b.number) != i for i, b in enumerate(self.buses)):
            self.rebuild_index()
        return self._index

    def bus_kv(self, number):
        b = self.buses[self.index[number]]
        return b.base_kv if b.base_kv > 0 else self.metadata.get("base_kv_by_bus", {}).get(number, 0.0)

    # ------------------------------------------------------------------ generation data
    def bus_generation(self):
        """Per-bus (pg, qg, qmin, qmax, vset, has_gen) in pu from in-service generators.

        With Generator records: aggregated per bus, voltage set-point of the first unit.
        Without records (hand-built networks): the Bus fields are used.
        """
        n = len(self.buses)
        idx = self.index
        pg, qg = np.zeros(n), np.zeros(n)
        qmin, qmax = np.zeros(n), np.zeros(n)
        has = np.zeros(n, bool)
        vset = np.array([b.vm for b in self.buses], float)
        if self.generators:
            taken = np.zeros(n, bool)
            for g in self.generators:
                i = idx[g.bus_number]
                if not g.in_service or not self.buses[i].in_service:
                    continue
                pg[i] += g.p_gen
                qg[i] += g.q_gen
                qmin[i] += g.q_min
                qmax[i] += g.q_max
                has[i] = True
                if not taken[i]:
                    vset[i], taken[i] = g.v_setpoint, True
        else:
            for i, b in enumerate(self.buses):
                pg[i], qg[i], qmin[i], qmax[i] = b.p_gen, b.q_gen, b.q_min, b.q_max
                has[i] = b.kind.upper() in (PV, SLACK) or b.p_gen != 0 or b.q_gen != 0
        return pg, qg, qmin, qmax, vset, has

    # ------------------------------------------------------------------ checks
    def check(self):
        """Pre-run checks: bad data, dangling branches, missing slack, islands without a slack."""
        out = []
        err = lambda m: out.append(Issue("error", m))
        warn = lambda m: out.append(Issue("warning", m))
        if not self.buses:
            err("network has no buses")
            return out
        if self.base_mva <= 0:
            err("system base MVA must be positive")
        known = {b.number for b in self.buses}
        active = [b for b in self.buses if b.in_service]
        slacks = [b for b in active if b.kind.upper() == SLACK]
        if len(slacks) != 1:
            err(f"exactly one in-service slack bus is required (found {len(slacks)})")
        for b in self.buses:
            if b.kind.upper() not in (PQ, PV, SLACK):
                err(f"bus {b.number}: unknown type {b.kind!r}")
            if b.vm <= 0:
                err(f"bus {b.number}: voltage must be positive")
            if b.v_min >= b.v_max:
                err(f"bus {b.number}: Vmin must be below Vmax")
            if b.load_model.upper() not in ("PQ", "Z", "ZIP"):
                err(f"bus {b.number}: unsupported load model {b.load_model!r}")
            if b.load_model.upper() == "ZIP" and abs(b.zip_z + b.zip_i + b.zip_p - 1) > 1e-6:
                err(f"bus {b.number}: ZIP shares must sum to 1")
        for br in self.branches:
            tag = f"branch {br.from_bus}-{br.to_bus}"
            if br.from_bus not in known or br.to_bus not in known:
                err(f"{tag}: dangling branch (bus does not exist)")
                continue
            if br.from_bus == br.to_bus:
                err(f"{tag}: connects a bus to itself")
            if br.r == 0 and br.x == 0:
                err(f"{tag}: zero series impedance")
            if br.rate_mva < 0:
                err(f"{tag}: negative rating")
            if br.tap <= 0:
                err(f"{tag}: tap ratio must be positive")
            if br.tap_side not in ("from", "to"):
                err(f"{tag}: tap_side must be 'from' or 'to'")
            if br.in_service and not (self.buses[self.index[br.from_bus]].in_service
                                      and self.buses[self.index[br.to_bus]].in_service):
                warn(f"{tag}: attached to an out-of-service bus, ignored")
        for g in self.generators:
            if g.bus_number not in known:
                err(f"generator {g.name or g.bus_number}: bus does not exist")
            elif g.q_min > g.q_max:
                err(f"generator at bus {g.bus_number}: Qmin > Qmax")
        # islands
        if active:
            pos = {b.number: i for i, b in enumerate(active)}
            rows, cols = [], []
            for br in self.branches:
                if br.in_service and br.from_bus in pos and br.to_bus in pos:
                    rows.append(pos[br.from_bus]); cols.append(pos[br.to_bus])
            m = len(active)
            ncomp, lab = connected_components(
                coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(m, m)), directed=False)
            slack_ids = {pos[b.number] for b in slacks}
            for c in range(ncomp):
                members = [active[i].number for i in np.where(lab == c)[0]]
                if not any(i in slack_ids for i in np.where(lab == c)[0]):
                    err(f"island without a slack bus: buses {members}")
        pg, qg, qmin, qmax, vset, has = self.bus_generation()
        for i, b in enumerate(self.buses):
            if b.in_service and b.kind.upper() == PV and not has[i]:
                warn(f"bus {b.number}: PV bus without an in-service generator is treated as PQ")
        return out

    def errors(self):
        return [i for i in self.check() if i.level == "error"]

    # ------------------------------------------------------------------ Ybus
    def active_branches(self):
        """[(branch_index, i, j, branch)] of in-service branches between in-service buses."""
        idx = self.index
        out = []
        for k, br in enumerate(self.branches):
            i, j = idx[br.from_bus], idx[br.to_bus]
            if br.in_service and self.buses[i].in_service and self.buses[j].in_service:
                out.append((k, i, j, br))
        return out

    def ybus(self) -> csr_matrix:
        """Sparse bus admittance matrix (MATPOWER branch model, asymmetric with phase shifters)."""
        n = len(self.buses)
        rows, cols, vals = [], [], []
        for _, i, j, br in self.active_branches():
            yff, yft, ytf, ytt = br.admittances()
            rows += [i, i, j, j]
            cols += [i, j, i, j]
            vals += [yff, yft, ytf, ytt]
        for i, bus in enumerate(self.buses):
            if bus.in_service and (bus.g_shunt or bus.b_shunt):
                rows.append(i); cols.append(i); vals.append(complex(bus.g_shunt, bus.b_shunt))
        rows += list(range(n)); cols += list(range(n)); vals += [0j] * n      # keep the diagonal structure
        return coo_matrix((np.asarray(vals, dtype=complex), (rows, cols)), shape=(n, n)).tocsr()


def derive_bus_kinds(net):
    """Bus type 'set from what is connected': the slack bus keeps SLACK (external grid);
    other buses are PV if an in-service generator is attached, else PQ."""
    gen_buses = {g.bus_number for g in net.generators if g.in_service}
    for b in net.buses:
        if b.kind.upper() == SLACK:
            continue
        b.kind = PV if b.number in gen_buses else PQ
