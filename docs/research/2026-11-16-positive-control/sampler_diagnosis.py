import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/jdutreve/projects/investment/docs/research/2026-11-16-positive-control")
from control import forward_excess, planted

from investment.mechanical.invariants import sample_moments

W = Path(os.path.expanduser("~/data/investment/research/2026-11-16-positive-control"))
con = sqlite3.connect(W / "control.db")
E = forward_excess(con)
above = E.median() < E
H = pd.Timedelta(weeks=12)
for p in (0.5, 0.6, 0.7):
    cur, grid = [], []
    for seed in range(20):
        s = planted(E, p, 1.0, 1000 * seed + int(p * 100))
        daily = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D")).ffill()
        m = sample_moments(daily > 0.5, H)
        wk = [E.index[E.index <= d].max() for d in m]
        cur.append(above.loc[wk].mean())
        g = E.index[::12]  # fixed grid, one date per horizon
        on = g[s.loc[g].to_numpy() > 0.5]
        grid.append(above.loc[on].mean())
    print(f"p={p}: current sampler {np.mean(cur):.3f}  fixed-grid sampler {np.mean(grid):.3f}")
