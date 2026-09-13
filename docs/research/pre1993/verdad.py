"""Fidelity check, not a criterion: Verdad's three-portfolio rule, gross of costs, on the
reconstructed data, decade by decade against the paper's Figure 18.
Usage: verdad.py WORK_DIR/pre1993/pre1993.db
"""

import asyncio
import sys
from datetime import date

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios

GROWTH = {"SPY": 50.0, "IWN": 40.0, "GLD": 10.0}
# The paper's rule expressed in the stack's own knobs: the growth portfolio whatever the slope
# when spreads are wide; trend read on the S&P and gold only, one 200-day line, redirected to
# Treasuries; no hysteresis and no trajectory knobs.
VERDAD_RULE = {
    "BOOKS": {
        "credit-spread-wide-yield-curve-flat": GROWTH,
        "credit-spread-wide-yield-curve-steep": GROWTH,
        "credit-spread-tight-yield-curve-flat": {"SPY": 50.0, "GLD": 40.0, "IWN": 10.0},
        "credit-spread-tight-yield-curve-steep": {"VCIT": 50.0, "IEF": 40.0, "IWN": 10.0},
    },
    "TREND_SLEEVES": ("SPY", "GLD"),
    "MA_WINDOWS": (200,),
    "CONFIRM_DECISIONS": 1,
    "SPREAD_SPEED_VETO": None,
    "SPREAD_STRESS_SLEEVE_GATE": None,
    "STRESS_GATED_SLEEVES": (),
    "TREND_FALLBACK_HAVEN": "IEF",
}
PAPER = {"1980s": 24.7, "1990s": 12.6, "2000s": 12.5, "2010s+2020": 13.3}
DECADES = {
    "1980s": ("1980-01-01", "1989-12-31"),
    "1990s": ("1990-01-01", "1999-12-31"),
    "2000s": ("2000-01-01", "2009-12-31"),
    "2010s+2020": ("2010-01-01", "2020-12-31"),
}


def cagr(nav, lo, hi):
    x = nav.loc[lo:hi].dropna()
    return (x.iloc[-1] / x.iloc[0]) ** (365.25 / (x.index[-1] - x.index[0]).days) - 1


async def main():
    db = InvestmentDB(sys.argv[1])
    saved = {knob: getattr(ms, knob) for knob in VERDAD_RULE}
    try:
        for knob, value in VERDAD_RULE.items():
            setattr(ms, knob, value)
        runs = {}
        for cadence in ("quarterly", "monthly"):
            run = await ms.run_market_signal(
                db, start=date(1977, 7, 1), end=date(2020, 12, 31), cadence=cadence, cost_bps=0.0
            )
            runs[cadence] = run.nav
        spy = await ratios.load_price(db, "SPY")
    finally:
        for knob, value in saved.items():
            setattr(ms, knob, value)
        await db.close()

    print("decade         paper  replica Q  replica M  S&P proxy")
    lines = [(name, lo, hi, f"{PAPER[name]:>7.1f}%") for name, (lo, hi) in DECADES.items()]
    lines.append(("1980-2020", "1980-01-01", "2020-12-31", " " * 8))
    for name, lo, hi, paper in lines:
        cells = (cagr(runs["quarterly"], lo, hi), cagr(runs["monthly"], lo, hi), cagr(spy, lo, hi))
        print(f"{name:<12}{paper}" + "".join(f"{c * 100:>10.1f}%" for c in cells))


asyncio.run(main())
