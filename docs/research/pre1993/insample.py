"""Side finding: the in-sample walk (1993-11..2026) on the live copy against the spliced copy.
Usage: insample.py WORK_DIR/measure.db WORK_DIR/pre1993/pre1993.db
"""

import asyncio
import sys

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms


async def walk(path):
    db = InvestmentDB(path)
    try:
        run = await ms.run_market_signal(db)
        return run, await ms.stack_metrics(db, run)
    finally:
        await db.close()


async def main():
    (live_run, live_m), (long_run, long_m) = await walk(sys.argv[1]), await walk(sys.argv[2])
    for label, m in (("live copy", live_m), ("with pre-1993 history", long_m)):
        print(
            f"{label:<24} CAGR {m.cagr * 100:.2f}%  Sortino {m.sortino:.3f}  "
            f"maxDD {m.max_drawdown * 100:.2f}%"
        )
    pairs = zip(live_run.decisions, long_run.decisions, strict=True)
    differing = [a.date.date() for a, b in pairs if a.held != b.held]
    print(
        f"held book differs on {len(differing)} of {len(live_run.decisions)} decisions, "
        f"from {differing[0]} to {differing[-1]}"
    )


asyncio.run(main())
