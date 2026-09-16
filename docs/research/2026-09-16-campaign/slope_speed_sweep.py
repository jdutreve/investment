"""Sweep SLOPE_SPEED_VETO — the Worker's 2026-09-13 claim — over its useful domain,
on a COPY, with the evidence gate. Usage: slope_speed_sweep.py DB"""

import asyncio
import sys
from datetime import date

import numpy as np

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import rule_revision as rr

GRID = (0.05, 0.10, 0.15, 0.20)
WINDOWS = {
    "1993-2026": {},
    "1993-2008": {"end": date(2008, 12, 31)},
    "2009-2026": {"start": date(2009, 1, 2)},
}
TITLE = "read a steep curve as flat while the curve itself collapses (Worker, 2026-09-13)"


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        series = await ms.load_series(db)
        speed = series.slope_speed.dropna()
        falling = speed[speed < 0]
        pct = {p: round(float(np.quantile(-falling, p)), 3) for p in (0.5, 0.75, 0.9, 0.95)}
        print(
            f"T10Y2Y speed over {ms.SPEED_LOOKBACK_DAYS}d, falling days only: "
            f"{len(falling)} of {len(speed)}; |speed| percentiles {pct}"
        )
        for value in GRID:
            fired = int((speed <= -value).sum())
            print(
                f"\nslope_speed_veto = {value} (the curve falls that fast on {fired} days)",
                flush=True,
            )
            for name, window in WINDOWS.items():
                m = await rr.measure_revision(
                    db, {"slope_speed_veto": value}, title=TITLE, **window
                )
                e = m.evidence
                chance = "n/a" if e is None else f"{min(e.p_improve, e.p_degrade):.0%}"
                print(
                    f"  {name:<10} {m.verdict:<13} "
                    f"CAGR {m.baseline.cagr * 100:5.2f}% -> {m.variant.cagr * 100:5.2f}%"
                    f"  Sortino {m.baseline.sortino:.3f} -> {m.variant.sortino:.3f}"
                    f"  maxDD {m.variant.max_drawdown * 100:6.2f}%  chance {chance}",
                    flush=True,
                )
    finally:
        await db.close()


asyncio.run(main())
