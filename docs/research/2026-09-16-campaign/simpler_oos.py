"""I-62 candidate 3 on the window the rule never saw: 1977-07..1993-10."""

import asyncio
import sys
from datetime import date

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios, replay
from investment.mechanical import rule_revision as rr

START, END = date(1977, 7, 1), date(1993, 10, 29)
WF, WS = "credit-spread-wide-yield-curve-flat", "credit-spread-wide-yield-curve-steep"
TF, TS = "credit-spread-tight-yield-curve-flat", "credit-spread-tight-yield-curve-steep"
B = ms.BOOKS
VARIANTS = {
    "no slope (credit only)": {WF: B[WS], WS: B[WS], TF: B[TF], TS: B[TF]},
    "no credit (slope only)": {WF: B[TF], WS: B[TS], TF: B[TF], TS: B[TS]},
    "pre-2x2 (shared wide book)": {WF: B[WS], WS: B[WS], TF: B[TF], TS: B[TS]},
}


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        rf = await ratios.load_rf_daily(db)
        s = await ms.load_series(db)
        base = await ms.run_market_signal(db, series=s, start=START, end=END)
        base_nav = base.nav.dropna().loc[str(START) : str(END)]
        base_m = replay.nav_metrics(base_nav, rf)
        rows = [("the 2x2 rule (baseline)", base_m, base.turnover, "-", "-")]
        saved = ms.BOOKS
        for label, books in VARIANTS.items():
            try:
                ms.BOOKS = books
                run = await ms.run_market_signal(db, series=s, start=START, end=END)
            finally:
                ms.BOOKS = saved
            nav = run.nav.dropna().loc[str(START) : str(END)]
            m = replay.nav_metrics(nav, rf)
            evidence = rr.measure_evidence(base_nav, nav, rf)
            verdict = rr.RevisionMeasurement(
                overrides={"books": label},
                baseline=base_m,
                variant=m,
                baseline_turnover=base.turnover,
                variant_turnover=run.turnover,
                evidence=evidence,
            ).verdict
            chance = (
                "n/a" if evidence is None else f"{min(evidence.p_improve, evidence.p_degrade):.0%}"
            )
            rows.append((label, m, run.turnover, verdict, chance))
    finally:
        await db.close()
    print("1977-07..1993-10 (out of sample)")
    print(
        f"{'variant':<28}{'CAGR':>8}{'Sortino':>9}{'maxDD':>8}{'turn':>8}  {'verdict':<13} chance"
    )
    for label, m, turnover, verdict, chance in rows:
        print(
            f"{label:<28}{m.cagr * 100:>7.2f}%{m.sortino:>9.3f}{m.max_drawdown * 100:>7.1f}%"
            f"{turnover:>8.1f}  {verdict:<13} {chance}"
        )


asyncio.run(main())
