"""Candidates that are not registry knobs, judged by the same pair of questions:
did it move an indicator, and does the sample's own luck reproduce the move?"""

import asyncio
import sys

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios
from investment.mechanical import rule_revision as rr

VIX_LEVELS = (25.0, 30.0, 35.0)


def verdict_of(baseline_run, variant_run, baseline_m, variant_m, rf, label):
    evidence = rr.measure_evidence(baseline_run.nav.dropna(), variant_run.nav.dropna(), rf)
    m = rr.RevisionMeasurement(
        overrides={"candidate": label},
        baseline=baseline_m,
        variant=variant_m,
        baseline_turnover=baseline_run.turnover,
        variant_turnover=variant_run.turnover,
        evidence=evidence,
    )
    chance = "n/a" if evidence is None else f"{min(evidence.p_improve, evidence.p_degrade):.0%}"
    return m.verdict, chance


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        rf = await ratios.load_rf_daily(db)
        s = await ms.load_series(db)
        vix = await ratios.load_price(db, "^VIX")
        base_run = await ms.run_market_signal(db, series=s)
        base_m = await ms.stack_metrics(db, base_run)
        rows = [("baseline (monthly rule)", base_run, base_m, "-", "-")]

        for cadence in ("weekly", "quarterly"):
            run = await ms.run_market_signal(db, series=s, cadence=cadence)
            m = await ms.stack_metrics(db, run)
            v, c = verdict_of(base_run, run, base_m, m, rf, f"cadence={cadence}")
            rows.append((f"whole rule {cadence}", run, m, v, c))

        # VIX brake PROTOTYPE: on a decision date whose previous VIX close is above the
        # level, the equity sleeves go to cash before the book is priced. Targets are
        # rewritten and repriced by the real engine; nothing in the rule is changed.
        vix_prev = vix.reindex(s.calendar).ffill().shift(1)
        for level in VIX_LEVELS:
            targets = {}
            for d in base_run.decisions:
                target = dict(d.target)
                if float(vix_prev.get(d.date, 0.0) or 0.0) > level:
                    moved = sum(target.pop(t, 0.0) for t in ms.EQUITY_SLEEVES)
                    if moved:
                        target[ratios.CASH_TICKER] = target.get(ratios.CASH_TICKER, 0.0) + moved
                targets[d.date] = target
            nav, turnover = ms.shadow_book_nav(targets, s.prices, s.rf, ms.COST_BPS, s.calendar)
            run = ms.MarketSignalRun(nav=nav, targets=targets, turnover=turnover)
            m = await ms.stack_metrics(db, run)
            v, c = verdict_of(base_run, run, base_m, m, rf, f"vix_brake={level}")
            rows.append((f"VIX brake > {level:.0f}", run, m, v, c))
    finally:
        await db.close()

    print(
        f"{'candidate':<26}{'CAGR':>8}{'Sortino':>9}{'maxDD':>8}{'turnover':>10}  "
        f"{'verdict':<13} chance"
    )
    for label, run, m, verdict, chance in rows:
        print(
            f"{label:<26}{m.cagr * 100:>7.2f}%{m.sortino:>9.3f}{m.max_drawdown * 100:>7.1f}%"
            f"{run.turnover:>10.1f}  {verdict:<13} {chance}"
        )


asyncio.run(main())
