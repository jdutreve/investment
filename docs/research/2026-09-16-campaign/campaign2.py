"""Campaign, part 2: does a SIMPLER signal do as well? Same two questions."""

import asyncio
import sys

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios
from investment.mechanical import rule_revision as rr

WF, WS = "credit-spread-wide-yield-curve-flat", "credit-spread-wide-yield-curve-steep"
TF, TS = "credit-spread-tight-yield-curve-flat", "credit-spread-tight-yield-curve-steep"
B = ms.BOOKS

VARIANTS = {
    # the slope stops deciding: one book per credit state
    "no slope (credit only)": {WF: B[WS], WS: B[WS], TF: B[TF], TS: B[TF]},
    # the credit spread stops deciding: one book per curve state
    "no credit (slope only)": {WF: B[TF], WS: B[TS], TF: B[TF], TS: B[TS]},
    # the rule as it was before the 2x2 split of 2026-08-13
    "pre-2x2 (shared wide book)": {WF: B[WS], WS: B[WS], TF: B[TF], TS: B[TS]},
}


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        rf = await ratios.load_rf_daily(db)
        s = await ms.load_series(db)
        base_run = await ms.run_market_signal(db, series=s)
        base_m = await ms.stack_metrics(db, base_run)
        rows = [("baseline (the 2x2 rule)", base_run, base_m, "-", "-")]
        saved = ms.BOOKS
        for label, books in VARIANTS.items():
            try:
                ms.BOOKS = books
                run = await ms.run_market_signal(db, series=s)
                m = await ms.stack_metrics(db, run)
            finally:
                ms.BOOKS = saved
            evidence = rr.measure_evidence(base_run.nav.dropna(), run.nav.dropna(), rf)
            measurement = rr.RevisionMeasurement(
                overrides={"books": label},
                baseline=base_m,
                variant=m,
                baseline_turnover=base_run.turnover,
                variant_turnover=run.turnover,
                evidence=evidence,
            )
            chance = (
                "n/a" if evidence is None else f"{min(evidence.p_improve, evidence.p_degrade):.0%}"
            )
            rows.append((label, run, m, measurement.verdict, chance))
    finally:
        await db.close()
    print(
        f"{'candidate':<28}{'CAGR':>8}{'Sortino':>9}{'maxDD':>8}{'turnover':>10}  "
        f"{'verdict':<13} chance"
    )
    for label, run, m, verdict, chance in rows:
        print(
            f"{label:<28}{m.cagr * 100:>7.2f}%{m.sortino:>9.3f}{m.max_drawdown * 100:>7.1f}%"
            f"{run.turnover:>10.1f}  {verdict:<13} {chance}"
        )


asyncio.run(main())
