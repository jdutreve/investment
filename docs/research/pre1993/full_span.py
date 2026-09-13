"""The stack against SPY and its four frozen books over the whole reconstructed span, 1977-07 to
the last priced day. Usage: full_span.py WORK_DIR/pre1993/pre1993.db
"""

import asyncio
import sys
from datetime import date

import numpy as np
import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios, replay

START = date(1977, 7, 1)
SEED, B, BLOCK, CHUNK = 20260913, 2000, 63, 250
DECADES = {
    "1977-79": ("1977-07-01", "1979-12-31"),
    "1980s": ("1980-01-01", "1989-12-31"),
    "1990s": ("1990-01-01", "1999-12-31"),
    "2000s": ("2000-01-01", "2009-12-31"),
    "2010s": ("2010-01-01", "2019-12-31"),
    "2020-26": ("2020-01-01", "2026-12-31"),
}
CRISES = {
    "1980-82 Volcker": ("1980-01-01", "1982-08-31"),
    "1987 crash": ("1987-08-01", "1987-12-31"),
    "2000-02 bear": ("2000-03-01", "2002-10-31"),
    "2008 crisis": ("2007-10-01", "2009-03-31"),
    "2020 covid": ("2020-02-01", "2020-04-30"),
    "2022": ("2022-01-01", "2022-10-31"),
}


def short(book):
    return book.replace("credit-spread-", "").replace("yield-curve-", "")


def cagr(nav):
    nav = nav.dropna()
    return (nav.iloc[-1] / nav.iloc[0]) ** (365.25 / (nav.index[-1] - nav.index[0]).days) - 1


def worst_drawdown(nav):
    return (nav / nav.cummax() - 1).min()


def sortino(excess):
    return excess.mean(-1) / np.sqrt((np.minimum(excess, 0) ** 2).mean(-1)) * np.sqrt(252)


def paired_bootstrap(a, b, rf):
    """a minus b over moving blocks drawn once for both arms, from a fresh generator per
    comparison. Returns [(point, 5%, 95%, P(delta <= 0))] for Sortino, then log return/yr."""
    rng = np.random.default_rng(SEED)
    idx = a.index.intersection(b.index)
    f = rf.reindex(idx).ffill().to_numpy()[1:]
    ra = a.loc[idx].pct_change().to_numpy()[1:]
    rb = b.loc[idx].pct_change().to_numpy()[1:]
    ea, eb, la, lb = ra - f, rb - f, np.log1p(ra), np.log1p(rb)
    n = len(ea)
    sortino_deltas, return_deltas = [], []
    for _ in range(0, B, CHUNK):
        starts = rng.integers(0, n - BLOCK, size=(CHUNK, n // BLOCK + 1))
        ix = (starts[:, :, None] + np.arange(BLOCK)).reshape(CHUNK, -1)[:, :n]
        sortino_deltas.append(sortino(ea[ix]) - sortino(eb[ix]))
        return_deltas.append((la[ix].mean(-1) - lb[ix].mean(-1)) * 252)
    results = []
    for point, parts in (
        (sortino(ea) - sortino(eb), sortino_deltas),
        ((la.mean() - lb.mean()) * 252, return_deltas),
    ):
        deltas = np.concatenate(parts)
        q05, q95 = np.quantile(deltas, 0.05), np.quantile(deltas, 0.95)
        results.append((point, q05, q95, (deltas <= 0).mean()))
    return results


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        rf = await ratios.load_rf_daily(db)
        s = await ms.load_series(db)
        end = s.calendar[-1].date()
        run = await ms.run_market_signal(db, start=START, end=end, series=s)
        first, last = pd.Timestamp(START), pd.Timestamp(end)
        arms = {"STACK": run.nav.dropna().loc[first:], "SPY": s.prices["SPY"].loc[first:last]}
        dates = replay.decision_dates(s.calendar, START, end, "monthly")
        for name, book in ms.BOOKS.items():
            targets = ms.trend_baseline_targets(
                dates, s.moving_averages, s.decision_prices, book=book
            )
            nav, _ = ms.shadow_book_nav(targets, s.prices, s.rf, ms.COST_BPS, s.calendar)
            arms[f"frozen {short(name)}"] = nav.dropna().loc[first:]
    finally:
        await db.close()

    print(f"window {START} .. {end} ({len(run.decisions)} monthly decisions)")
    print("arm                       CAGR  Sortino   maxDD  Calmar     $100 ->")
    for name, nav in arms.items():
        m = replay.nav_metrics(nav, rf)
        print(
            f"{name:<22}{m.cagr * 100:>7.2f}%{m.sortino:>9.3f}{m.max_drawdown * 100:>7.1f}%"
            f"{m.calmar:>8.2f}{100 * nav.iloc[-1] / nav.iloc[0]:>12,.0f}"
        )
    stack, spy = arms["STACK"], arms["SPY"]
    print("\nCAGR by decade          stack      SPY")
    for name, (lo, hi) in DECADES.items():
        print(
            f"  {name:<18}{cagr(stack.loc[lo:hi]) * 100:>8.1f}%{cagr(spy.loc[lo:hi]) * 100:>8.1f}%"
        )
    print("\nworst drawdown inside each crisis   stack      SPY")
    for name, (lo, hi) in CRISES.items():
        print(
            f"  {name:<32}{worst_drawdown(stack.loc[lo:hi]) * 100:>7.1f}%"
            f"{worst_drawdown(spy.loc[lo:hi]) * 100:>8.1f}%"
        )
    print("\npaired bootstrap, stack minus:")
    for name, nav in arms.items():
        if name == "STACK":
            continue
        (sp, s05, s95, sprob), (rp, r05, r95, rprob) = paired_bootstrap(stack, nav, rf)
        print(
            f"  {name:<22} Sortino {sp:+.3f} [{s05:+.3f}, {s95:+.3f}] P(<=0) {sprob:.3f} | "
            f"return/yr {rp * 100:+.2f}pp [{r05 * 100:+.2f}, {r95 * 100:+.2f}] P(<=0) {rprob:.3f}"
        )


asyncio.run(main())
