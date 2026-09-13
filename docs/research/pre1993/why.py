"""Why the stack did not hold the bond book (tight-steep) through 1977-1993, year by year: the
book it held, what the signal read, and what each earned.
Usage: why.py WORK_DIR/pre1993/pre1993.db
"""

import asyncio
import sys
from collections import Counter
from datetime import date

import numpy as np
import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import replay

START, END = date(1977, 7, 1), date(1993, 10, 29)
BOND_BOOK = "credit-spread-tight-yield-curve-steep"
ABBR = {
    "credit-spread-wide-yield-curve-flat": "WF",
    "credit-spread-wide-yield-curve-steep": "WS",
    "credit-spread-tight-yield-curve-flat": "TF",
    BOND_BOOK: "TS",
}


def yearly_returns(nav):
    window = nav.loc[pd.Timestamp(START) : pd.Timestamp(END)]
    ends = pd.concat([window.iloc[:1], window.resample("YE").last()])
    years = [t.year for t in ends.index[1:]]
    return dict(zip(years, ends.pct_change().dropna(), strict=True))


def counts(counter):
    return " ".join(f"{k}{v}" for k, v in sorted(counter.items()))


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        s = await ms.load_series(db)
        run = await ms.run_market_signal(db, start=START, end=END, series=s)
        dates = replay.decision_dates(s.calendar, START, END, "monthly")
        book = ms.BOOKS[BOND_BOOK]
        targets = ms.trend_baseline_targets(dates, s.moving_averages, s.decision_prices, book=book)
        bond_nav, _ = ms.shadow_book_nav(targets, s.prices, s.rf, ms.COST_BPS, s.calendar)
    finally:
        await db.close()

    stack_years = yearly_returns(run.nav.dropna())
    bond_years = yearly_returns(bond_nav.dropna())
    years = {}
    for d in run.decisions:
        year = years.setdefault(
            d.date.year,
            {"held": Counter(), "signal": Counter(), "spread": [], "slope": [], "bonds": []},
        )
        year["held"][ABBR[d.held]] += 1
        year["signal"][ABBR[d.signalled]] += 1
        if d.spread_median is not None:
            year["spread"].append(d.spread - d.spread_median)
        if d.slope_median is not None:
            year["slope"].append(d.slope - d.slope_median)
        if d.held == BOND_BOOK:
            year["bonds"].append((d.target.get("VCIT", 0.0) + d.target.get("IEF", 0.0)) / 90.0)

    print(
        "year  held           signalled      spread-med  slope-med  bonds kept   stack  bond book"
    )
    for yr, y in sorted(years.items()):
        slope = f"{np.mean(y['slope']):+.2f}" if y["slope"] else "n/a"
        kept = f"{np.mean(y['bonds']) * 100:.0f}%" if y["bonds"] else "-"
        print(
            f"{yr}  {counts(y['held']):<14} {counts(y['signal']):<14}"
            f"{np.mean(y['spread']):>+10.2f} {slope:>10} {kept:>11}"
            f"{stack_years[yr] * 100:>+7.1f}%{bond_years[yr] * 100:>+9.1f}%"
        )

    late = [d for d in run.decisions if d.date >= pd.Timestamp("1982-08-01")]
    by_state = Counter(ABBR[d.signalled] for d in late)
    above = sum(d.spread_median is not None and d.spread > d.spread_median for d in late)
    wide_read = by_state["WF"] + by_state["WS"]
    held = sum(d.held == BOND_BOOK for d in late)
    print(
        f"1982-08..1993-10: {len(late)} decisions. Signal read {counts(by_state)}; "
        f"the spread LEVEL was above its median on {above}, read wide on {wide_read} (the veto "
        f"deferred {above - wide_read} to the slope). Bond book held on {held}."
    )


asyncio.run(main())
