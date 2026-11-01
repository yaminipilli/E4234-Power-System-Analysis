
"""Network data models and sparse Ybus construction for the E4234 assignment."""

from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix


@dataclass
class Bus:
    number: int
    kind: str = "PQ"                 # SLACK, PV, PQ
    vm: float = 1.0
    va_deg: float = 0.0
    p_load: float = 0.0              # pu, positive consumption
    q_load: float = 0.0              # pu, positive consumption
    p_gen: float = 0.0               # pu generation
    q_gen: float = 0.0               # pu generation / initial guess
    q_min: float = -np.inf           # pu, aggregate generator limits at bus
    q_max: float = np.inf
    g_shunt: float = 0.0             # pu at V=1
    b_shunt: float = 0.0             # pu at V=1
    v_min: float = 0.95
    v_max: float = 1.05
    in_service: bool = True
    name: str = ""
    load_model: str = "PQ"           # PQ or Z (constant impedance)


@dataclass
class Branch:
    from_bus: int
    to_bus: int
    r: float
    x: float
    b: float = 0.0
    tap: float = 1.0
    shift_deg: float = 0.0
    rate_mva: float = 0.0
    in_service: bool = True
    name: str = ""


@dataclass
class Generator:
    """Individual generator data imported from a MATPOWER case."""

    bus_number: int
    p_gen: float = 0.0       # pu on network base
    q_gen: float = 0.0       # pu on network base
    q_max: float = np.inf    # pu on network base
    q_min: float = -np.inf   # pu on network base
    v_setpoint: float = 1.0  # pu
    mbase_mva: float = 100.0
    in_service: bool = True
    p_max: float = np.inf    # pu on network base
    p_min: float = -np.inf   # pu on network base
    name: str = ""


@dataclass
class Network:
    buses: list[Bus]
    branches: list[Branch]
    base_mva: float = 100.0
    frequency_hz: float = 50.0
    metadata: dict = field(default_factory=dict)
    generators: list[Generator] = field(default_factory=list)

    def __post_init__(self):
        nums = [b.number for b in self.buses]

        if len(nums) != len(set(nums)):
            raise ValueError("Bus numbers must be unique")
        if not self.buses:
            raise ValueError("Network must contain at least one bus")
        if sum(
            b.in_service and b.kind.upper() == "SLACK"
            for b in self.buses
        ) != 1:
            raise ValueError("Exactly one in-service SLACK bus is required")

        self._index = {number: i for i, number in enumerate(nums)}

        for br in self.branches:
            if br.from_bus not in self._index or br.to_bus not in self._index:
                raise ValueError(f"Branch references unknown bus: {br}")
            if br.r == 0 and br.x == 0:
                raise ValueError("Branch impedance cannot be zero")
            if br.tap <= 0:
                raise ValueError("Transformer tap magnitude must be positive")

        for gen in self.generators:
            if gen.bus_number not in self._index:
                raise ValueError(
                    f"Generator references unknown bus: {gen.bus_number}"
                )

    @property
    def index(self):
        """Map external bus numbers to internal zero-based indices."""
        return self._index

    def ybus(self) -> csr_matrix:
        """Build Ybus using the MATPOWER-compatible branch model."""
        n = len(self.buses)
        rows, cols, vals = [], [], []
        diag = np.zeros(n, dtype=complex)

        for br in self.branches:
            i = self._index[br.from_bus]
            j = self._index[br.to_bus]

            # Exclude unavailable branches and branches connected to
            # out-of-service buses.
            if (
                not br.in_service
                or not self.buses[i].in_service
                or not self.buses[j].in_service
            ):
                continue

            y = 1.0 / complex(br.r, br.x)
            tap = br.tap * np.exp(1j * np.deg2rad(br.shift_deg))

            yff = (y + 1j * br.b / 2) / abs(tap) ** 2
            yft = -y / np.conj(tap)
            ytf = -y / tap
            ytt = y + 1j * br.b / 2

            diag[i] += yff
            diag[j] += ytt

            rows.extend([i, j])
            cols.extend([j, i])
            vals.extend([yft, ytf])

        # Fixed bus shunts are included only at in-service buses.
        for i, bus in enumerate(self.buses):
            if bus.in_service:
                diag[i] += complex(bus.g_shunt, bus.b_shunt)

        rows.extend(range(n))
        cols.extend(range(n))
        vals.extend(diag.tolist())

        return coo_matrix(
            (np.asarray(vals, dtype=complex), (rows, cols)),
            shape=(n, n),
        ).tocsr()
