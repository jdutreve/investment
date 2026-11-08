"""The gold switch — ON or OFF, a line of the digest (owner, 2026-11-08).

Two legs, either of which turns it ON:

- the 5-year REAL yield (DFII5, the TIPS market yield) below its 12-month
  average: real rates falling, so the opportunity cost of holding a metal that
  pays nothing is falling too;
- gold's own price (GLD) above its 200-session average: the trend is up.

CONTEXT, NOT A SIGNAL OF THE STACK. Nothing allocates on it and nothing has
measured it: it is printed so the owner, and the Worker who reads the digest,
see the two classic reasons to hold gold in one word. A leg whose series is
missing reads n/a and does not vote; the switch is ON when a leg that can be
read says so.

POINT IN TIME (ADR-003): both series are read on rows knowable by `today`, so
the averages hold nothing published later.
"""

import dataclasses
from datetime import date, timedelta

import pandas as pd

from investment.db.sqlite import InvestmentDB

REAL_YIELD_TICKER = "DFII5"
GOLD_TICKER = "GLD"
# Twelve calendar months: the window is a span of time, whatever the number of
# prints a holiday calendar leaves in it.
REAL_YIELD_AVERAGE_DAYS = 365
# Two hundred sessions: the classic trend line counts trading days.
GOLD_AVERAGE_SESSIONS = 200


@dataclasses.dataclass(frozen=True)
class Leg:
    """One reason for the switch, read on its series' latest knowable row."""

    as_of: str
    value: float
    average: float
    on: bool


@dataclasses.dataclass(frozen=True)
class GoldSwitch:
    real_yield: Leg | None  # ON when the real yield is BELOW its average
    gold_trend: Leg | None  # ON when gold is ABOVE its average

    @property
    def on(self) -> bool | None:
        """ON when a readable leg says so; None when neither can be read."""
        legs = [leg for leg in (self.real_yield, self.gold_trend) if leg is not None]
        return any(leg.on for leg in legs) if legs else None


def real_yield_leg(series: pd.Series) -> Leg | None:
    """The real yield against its average over the 12 months up to its latest
    print. None without a full year of history behind that print."""
    series = series.dropna().sort_index()
    if series.empty:
        return None
    last = series.index[-1]
    if series.index[0] > last - timedelta(days=REAL_YIELD_AVERAGE_DAYS):
        return None
    window = series[series.index > last - timedelta(days=REAL_YIELD_AVERAGE_DAYS)]
    value, average = float(series.iloc[-1]), float(window.mean())
    return Leg(last.date().isoformat(), value, average, value < average)


def gold_trend_leg(series: pd.Series) -> Leg | None:
    """Gold against its 200-session average. The stored level is a total-return
    index spliced back to 1991, not a quote, and the comparison is a ratio, so
    the scale does not matter."""
    series = series.dropna().sort_index()
    if len(series) < GOLD_AVERAGE_SESSIONS:
        return None
    value = float(series.iloc[-1])
    average = float(series.iloc[-GOLD_AVERAGE_SESSIONS:].mean())
    return Leg(series.index[-1].date().isoformat(), value, average, value > average)


async def load_gold_switch(db: InvestmentDB, today: date) -> GoldSwitch:
    """Both legs on the rows knowable by `today`."""
    return GoldSwitch(
        real_yield=real_yield_leg(await _series(db, REAL_YIELD_TICKER, today)),
        gold_trend=gold_trend_leg(await _series(db, GOLD_TICKER, today)),
    )


async def _series(db: InvestmentDB, ticker: str, today: date) -> pd.Series:
    since = today - timedelta(days=2 * REAL_YIELD_AVERAGE_DAYS)
    rows = await db.query(
        "SELECT ts, level FROM market_data WHERE ticker = :t AND ts > :since AND ts <= :today "
        "ORDER BY ts",
        t=ticker,
        since=since.isoformat(),
        today=today.isoformat(),
    )
    return pd.Series(
        [float(r["level"]) for r in rows if r["level"] is not None],
        index=pd.DatetimeIndex([r["ts"] for r in rows if r["level"] is not None]),
        dtype=float,
    )
