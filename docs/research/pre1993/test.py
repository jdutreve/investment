"""THE PRE-REGISTERED TEST (PREREGISTRATION.md): the stack against its four frozen books and
the S&P proxy, monthly decisions 1977-07-01 .. 1993-10-29.

Usage: test.py WORK_DIR/pre1993/pre1993.db
"""

import asyncio
import sys
from collections import Counter
from datetime import date

import numpy as np
import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios, replay

START, END = date(1977, 7, 1), date(1993, 10, 29)
WINDOW = slice(pd.Timestamp(START), pd.Timestamp(END))
B, BLOCK, CHUNK = 2000, 63, 250
SP = "S&P proxy (buy-and-hold)"
rng = np.random.default_rng(20260913)


def sortino(excess):
    downside = np.sqrt((np.minimum(excess, 0) ** 2).mean(-1))
    return excess.mean(-1) / downside * np.sqrt(252)


def short(book):
    return book.replace("credit-spread-", "").replace("yield-curve-", "")


def paired_bootstrap(a, b, rf):
    """Sortino(a) - Sortino(b) over moving blocks drawn once for both arms.
    Returns (5% quantile, 95% quantile, P(delta <= 0))."""
    idx = a.index.intersection(b.index)
    f = rf.reindex(idx).ffill().to_numpy()[1:]
    ea = a.loc[idx].pct_change().to_numpy()[1:] - f
    eb = b.loc[idx].pct_change().to_numpy()[1:] - f
    n = len(ea)
    deltas = []
    for _ in range(0, B, CHUNK):
        starts = rng.integers(0, n - BLOCK, size=(CHUNK, n // BLOCK + 1))
        ix = (starts[:, :, None] + np.arange(BLOCK)).reshape(CHUNK, -1)[:, :n]
        deltas.append(sortino(ea[ix]) - sortino(eb[ix]))
    deltas = np.concatenate(deltas)
    return np.quantile(deltas, 0.05), np.quantile(deltas, 0.95), (deltas <= 0).mean()


async def main():
    db = InvestmentDB(sys.argv[1])
    try:
        rf = await ratios.load_rf_daily(db)
        s = await ms.load_series(db)
        run = await ms.run_market_signal(db, start=START, end=END, series=s)
        arms = {"STACK": run.nav.dropna().loc[WINDOW]}
        dates = replay.decision_dates(s.calendar, START, END, "monthly")
        for name, book in ms.BOOKS.items():
            targets = ms.trend_baseline_targets(
                dates, s.moving_averages, s.decision_prices, book=book
            )
            nav, _ = ms.shadow_book_nav(targets, s.prices, s.rf, ms.COST_BPS, s.calendar)
            arms[f"frozen {short(name)}"] = nav.dropna().loc[WINDOW]
        arms[SP] = s.prices["SPY"].loc[WINDOW]
    finally:
        await db.close()

    print(f"calendar opens {s.calendar[0].date()}; window {START}..{END}")
    held = Counter(short(decision.held) for decision in run.decisions)
    print(f"{len(run.decisions)} decisions; book held: {dict(held)}; turnover {run.turnover:.1f}")
    metrics = {name: replay.nav_metrics(nav, rf) for name, nav in arms.items()}
    for name, m in metrics.items():
        print(
            f"  {name:<28} CAGR {m.cagr * 100:6.2f}%  Sortino {m.sortino:.3f}  "
            f"maxDD {m.max_drawdown * 100:6.1f}%  Calmar {m.calmar:.2f}"
        )
    books = {name: m for name, m in metrics.items() if name.startswith("frozen")}
    best = max(books, key=lambda name: books[name].sortino)
    stack = metrics["STACK"].sortino
    verdict = "PASS" if stack > books[best].sortino else "FAIL"
    print(
        f"PRIMARY: stack Sortino {stack:.3f} vs best frozen book ({best}) "
        f"{books[best].sortino:.3f} -> {verdict}"
    )
    for other in (best, SP):
        lo, hi, p = paired_bootstrap(arms["STACK"], arms[other], rf)
        print(
            f"  paired bootstrap vs {other}: dSortino {stack - metrics[other].sortino:+.3f}, "
            f"90% CI [{lo:+.3f}, {hi:+.3f}], P(<=0) {p:.2f}"
        )


asyncio.run(main())
