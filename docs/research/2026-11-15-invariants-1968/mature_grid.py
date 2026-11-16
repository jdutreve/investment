import asyncio
import os
import time
from pathlib import Path

from investment.db.sqlite import InvestmentDB
from investment.mechanical import invariants


async def main():
    db = InvestmentDB(
        Path(
            os.path.expanduser(
                "~/data/investment/research/2026-11-15-invariants-1968/extended_grid.db"
            )
        )
    )
    t = time.monotonic()
    r = await invariants.mature_seed_invariants(db, remeasure_on_changed_data=True)
    print(f"matured {len(r)} in {time.monotonic() - t:.0f}s")
    await db.close()


asyncio.run(main())
