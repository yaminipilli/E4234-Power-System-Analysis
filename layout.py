"""Automatic layout (force-directed, Fruchterman-Reingold) for imported cases.  No GUI toolkit needed."""
import math

import numpy as np

GRID = 20.0


def snap(v, grid=GRID):
    return round(v / grid) * grid


def force_directed_layout(net, spacing=170.0, iterations=300, seed=1):
    """Return {bus_number: (x, y)} in world coordinates, snapped to the grid."""
    nums = [b.number for b in net.buses]
    n = len(nums)
    if n == 0:
        return {}
    idx = {b: i for i, b in enumerate(nums)}
    rng = np.random.default_rng(seed)
    side = spacing * math.sqrt(n)
    ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
    pos = np.c_[np.cos(ang), np.sin(ang)] * side / 3 + rng.normal(0, 5, (n, 2))
    edges = np.array([(idx[b.from_bus], idx[b.to_bus]) for b in net.branches
                      if b.from_bus in idx and b.to_bus in idx and b.from_bus != b.to_bus], int).reshape(-1, 2)
    temp = side / 8
    for _ in range(iterations):
        d = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(d, axis=2) + 1e-9
        disp = (d / dist[:, :, None] * (spacing ** 2 / dist)[:, :, None]).sum(axis=1)
        if len(edges):
            dv = pos[edges[:, 0]] - pos[edges[:, 1]]
            dl = np.linalg.norm(dv, axis=1) + 1e-9
            f = (dl * dl / spacing)[:, None] * dv / dl[:, None]
            np.add.at(disp, edges[:, 0], -f)
            np.add.at(disp, edges[:, 1], f)
        ln = np.linalg.norm(disp, axis=1) + 1e-9
        pos += disp / ln[:, None] * np.minimum(ln, temp)[:, None]
        temp *= 0.985
    pos -= pos.min(axis=0)
    out, taken = {}, []
    for b, (x, y) in zip(nums, pos):
        x, y = snap(x * 1.2 + 80), snap(y + 80)
        while any(abs(x - tx) < 80 and abs(y - ty) < 30 for tx, ty in taken):
            y += GRID
        taken.append((x, y))
        out[b] = (x, y)
    return out
