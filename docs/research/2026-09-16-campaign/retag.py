"""Re-measure every ledger experiment with the evidence gate, on a COPY."""

import asyncio
import json
import sys
from datetime import date

from investment.db.sqlite import InvestmentDB
from investment.mechanical import rule_revision as rr

WINDOWS = {
    "full": {},
    "1993-2008": {"end": date(2008, 12, 31)},
    "2009-2026": {"start": date(2009, 1, 2)},
}


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        rows = await db.query(
            "SELECT DISTINCT overrides FROM revision_measurement ORDER BY overrides"
        )
        print(f"{len(rows)} experiments in the ledger")
        for row in rows:
            overrides = json.loads(str(row["overrides"]))
            retired = [k for k in overrides if k not in rr.TESTABLE_PARAMETERS]
            if retired:
                print(f"{overrides!s:<55} SKIPPED — retired knob {retired}")
                continue
            for name, window in WINDOWS.items():
                try:
                    m = await rr.measure_revision(db, overrides, title=None, **window)
                except Exception as exc:
                    print(f"{overrides!s:<55} {name:<10} FAILED {exc}")
                    continue
                e = m.evidence
                chance = "n/a" if e is None else f"{min(e.p_improve, e.p_degrade):.0%}"
                delta = "n/a" if m.sortino_delta is None else f"{m.sortino_delta:+.3f}"
                print(
                    f"{overrides!s:<55} {name:<10} {m.verdict:<13} sortino {delta} chance {chance}",
                    flush=True,
                )
    finally:
        await db.close()


asyncio.run(main())
