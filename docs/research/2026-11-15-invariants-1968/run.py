"""Run the seed's measurement steps on a copy with 58 years of history (docs/INVARIANTS_1968.md).

Usage: run.py WORK_DIR — copies the live database to WORK_DIR/extended.db (sqlite
backup API, consistent while the agent runs) unless it already exists, then runs
on it, in production's order, the seed steps the protocol names: market data
(with the pre-history of `prehistory.py`), regimes, portfolio NAVs, benchmark
valuations, backtests and FAVORS, and the maturation of every invariant. The
corpus, curation and snapshot steps are skipped. The live database is only read.

Each step's end is logged with its duration, so a run that dies says where.
"""

import asyncio
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

from prehistory import ExtendedFetcher

from investment import seed
from investment.config import Settings
from investment.db.sqlite import InvestmentDB
from investment.mechanical import invariants

BACKFILL_YEARS = 58
log = logging.getLogger("invariants-1968")


def copy_live(target: Path) -> None:
    src = sqlite3.connect(
        "file:" + os.path.expanduser("~/data/investment/investment.db") + "?mode=ro", uri=True
    )
    dst = sqlite3.connect(target)
    src.backup(dst)
    dst.close()
    src.close()


async def main(work: Path) -> None:
    target = work / "extended.db"
    if not target.exists():
        copy_live(target)
        log.info("copied the live database to %s", target)
    settings = Settings(  # type: ignore[call-arg]
        db_path=target, market_backfill_years=BACKFILL_YEARS
    )
    db = InvestmentDB(target)
    fetch = ExtendedFetcher(work / "data")
    steps = [
        ("market data", lambda: seed._seed_market_data(db, settings, fetch_raw=fetch)),
        ("regimes", lambda: seed._materialize_regimes(db)),
        ("portfolio NAV", lambda: seed._seed_portfolio_nav(db)),
        ("benchmark valuation", lambda: seed._materialize_benchmark_valuation(db)),
        ("backtests and FAVORS", lambda: seed._run_backtests_favors(db)),
        (
            "maturation",
            lambda: invariants.mature_seed_invariants(db, remeasure_on_changed_data=True),
        ),
    ]
    try:
        for name, step in steps:
            started = time.monotonic()
            result = await step()
            summary = result if isinstance(result, dict) else f"{len(result)} results"
            log.info(
                "STEP %s done in %.0fs: %s", name, time.monotonic() - started, str(summary)[:600]
            )
    finally:
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(Path(sys.argv[1]) / "run.log"), logging.StreamHandler()],
    )
    asyncio.run(main(Path(sys.argv[1])))
