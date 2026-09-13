"""Where the out-of-sample drawdown sits, and whether the bond book's lead belongs to the
post-1982 fall in rates. Usage: eras.py WORK_DIR/pre1993/pre1993.db
"""

import asyncio
import sys

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios, replay

START, END = pd.Timestamp("1977-07-01"), pd.Timestamp("1993-10-29")
ERAS = {
    "1977-07..1982-07 (rates rising)": ("1977-07-01", "1982-07-31"),
    "1982-08..1993-10 (rates falling)": ("1982-08-01", "1993-10-29"),
}
BOND_BOOK = "credit-spread-tight-yield-curve-steep"


def max_drawdown(nav):
    """(depth, peak date, trough date)."""
    draw = nav / nav.cummax() - 1
    trough = draw.idxmin()
    return draw.min(), nav.loc[:trough].idxmax().date(), trough.date()


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        rf = await ratios.load_rf_daily(db)
        s = await ms.load_series(db)
        run = await ms.run_market_signal(db, start=START.date(), end=END.date(), series=s)
        stack = run.nav.dropna().loc[START:END]
        dates = replay.decision_dates(s.calendar, START.date(), END.date(), "monthly")
        book = ms.BOOKS[BOND_BOOK]
        targets = ms.trend_baseline_targets(dates, s.moving_averages, s.decision_prices, book=book)
        nav, _ = ms.shadow_book_nav(targets, s.prices, s.rf, ms.COST_BPS, s.calendar)
        bond = nav.dropna().loc[START:END]
        dgs10 = (await ratios.load_price(db, "DGS10")).loc[START:END]
    finally:
        await db.close()

    for label, series in (("stack", stack), ("frozen tight-steep", bond)):
        depth, peak, trough = max_drawdown(series)
        print(f"{label:<20} max drawdown {depth * 100:.1f}% from {peak} to {trough}")
    print(
        f"DGS10: {dgs10.iloc[0]:.2f}% on {dgs10.index[0].date()}, "
        f"peak {dgs10.max():.2f}% on {dgs10.idxmax().date()}, "
        f"{dgs10.iloc[-1]:.2f}% on {dgs10.index[-1].date()}"
    )
    for era, (lo, hi) in ERAS.items():
        a = replay.nav_metrics(stack.loc[lo:hi], rf)
        b = replay.nav_metrics(bond.loc[lo:hi], rf)
        print(
            f"{era:<34} stack CAGR {a.cagr * 100:5.1f}% Sortino {a.sortino:.2f} | "
            f"bond book CAGR {b.cagr * 100:5.1f}% Sortino {b.sortino:.2f}"
        )


asyncio.run(main())
