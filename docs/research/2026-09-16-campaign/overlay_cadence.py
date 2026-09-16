"""PROTOTYPE, outside the rule: keep the MONTHLY book and re-read the trend overlay more
often. Candidate 1 of I-62 could not test this — a finer cadence moves the book too."""

import asyncio
import sys

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios, replay
from investment.mechanical import rule_revision as rr


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        rf = await ratios.load_rf_daily(db)
        s = await ms.load_series(db)
        base = await ms.run_market_signal(db, series=s)
        base_m = await ms.stack_metrics(db, base)
        held_at = pd.Series({d.date: d.held for d in base.decisions}).sort_index()
        rows = [("baseline: book and overlay monthly", base, base_m, "-", "-")]
        for cadence in ("weekly", "biweekly"):
            grid = replay.decision_dates(
                s.calendar, ms.PINNED_WINDOW[0], ms.PINNED_WINDOW[1], "weekly"
            )
            if cadence == "biweekly":
                grid = grid[::2]
            targets = {}
            for t in grid:
                earlier = held_at.loc[:t]
                if earlier.empty:
                    continue
                book = ms.BOOKS[str(earlier.iloc[-1])]
                shares = {
                    ticker: ms._trend_read(
                        ms._at(s.decision_prices[ticker], t),
                        [ms._at(s.moving_averages[w][ticker], t) for w in s.moving_averages],
                    ).share
                    for ticker in (*ms.TREND_SLEEVES, ms.TREND_HAVEN)
                    if ticker in s.decision_prices
                }
                targets[t] = ms.apply_trend_overlay(book, shares)
            nav, turnover = ms.shadow_book_nav(targets, s.prices, s.rf, ms.COST_BPS, s.calendar)
            run = ms.MarketSignalRun(nav=nav, targets=targets, turnover=turnover)
            m = await ms.stack_metrics(db, run)
            evidence = rr.measure_evidence(base.nav.dropna(), run.nav.dropna(), rf)
            verdict = rr.RevisionMeasurement(
                overrides={"overlay_cadence": cadence},
                baseline=base_m,
                variant=m,
                baseline_turnover=base.turnover,
                variant_turnover=turnover,
                evidence=evidence,
            ).verdict
            chance = (
                "n/a" if evidence is None else f"{min(evidence.p_improve, evidence.p_degrade):.0%}"
            )
            rows.append((f"overlay {cadence}, book monthly", run, m, verdict, chance))
    finally:
        await db.close()
    print(
        f"{'candidate':<36}{'CAGR':>8}{'Sortino':>9}{'maxDD':>8}{'turn':>8}  {'verdict':<13} chance"
    )
    for label, run, m, verdict, chance in rows:
        print(
            f"{label:<36}{m.cagr * 100:>7.2f}%{m.sortino:>9.3f}{m.max_drawdown * 100:>7.1f}%"
            f"{run.turnover:>8.1f}  {verdict:<13} {chance}"
        )


asyncio.run(main())
