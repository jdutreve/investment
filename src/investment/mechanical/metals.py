"""The metals as indicators — gold, silver, copper, and how they compare — the
digest's metals block (owner, 2026-11-08).

GOLD IS A SWITCH, ON or OFF, with two legs, either of which turns it ON:

- the 5-year REAL yield (DFII5, the TIPS market yield) below its 12-month
  average: real rates falling, so the opportunity cost of holding a metal that
  pays nothing is falling too;
- gold's own price (GLD) above its 200-session average: the trend is up.

SILVER AND COPPER ARE SWITCHES ON THE SAME FOOTING ("on the same footing as
gold"), each by its own trend: its front-month price above its 200-session
average. They take no real-yield leg — that reason is gold's, the metal that
pays nothing and has no industrial use to speak of.

THE COMPARATORS are each a price in units of another metal (DERIVED_SIGNALS,
materialised weekly by `backtests._materialize_metal_ratios`), read against
their 12-month average: gold/silver rising = the defensive metal ahead of the
half-industrial one; copper/gold rising = the industrial metal ahead of the
defensive one, the market's growth-versus-fear gauge. Those are the customary
readings, not measured ones.

CONTEXT, NOT A SIGNAL OF THE STACK, AND MEASURED AS SUCH (2026-11-08,
docs/METAL_INDICATORS.md): on 2004-2026 none of these readings foretold the next
12 weeks, and holding GLD only while the gold switch was ON halved its return
without cutting its drawdown. Nothing allocates on them; they are printed so the
owner, and the Worker who reads the digest, see the metals in a few words. A leg
whose series is missing reads n/a and does not vote.

POINT IN TIME (ADR-003): every series is read on rows knowable by `today`, so
the averages hold nothing published later.
"""

import dataclasses
from datetime import date, timedelta

import pandas as pd

from investment.db.sqlite import InvestmentDB

REAL_YIELD_TICKER = "DFII5"
GOLD_TICKER = "GLD"
SILVER_TICKER = "SI=F"
COPPER_TICKER = "HG=F"
GOLD_SILVER = "gold_silver_ratio"
COPPER_GOLD = "copper_gold_ratio"
# Twelve calendar months: the window is a span of time, whatever the number of
# prints a holiday calendar leaves in it.
YEAR_AVERAGE_DAYS = 365
# Two hundred sessions: the classic trend line counts trading days.
TREND_SESSIONS = 200


@dataclasses.dataclass(frozen=True)
class Leg:
    """One reading against its average, on its series' latest knowable row.
    `on` is the side that counts for the switch (below for a real yield, above
    for a price); for a comparator it says the ratio is above its average."""

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


@dataclasses.dataclass(frozen=True)
class Metals:
    gold: GoldSwitch
    silver: Leg | None  # ON when silver is above its 200-session average
    copper: Leg | None  # ON when copper is above its 200-session average
    gold_silver: Leg | None  # rising: gold ahead of silver
    copper_gold: Leg | None  # rising: copper ahead of gold


def year_average_leg(series: pd.Series, *, on_below: bool) -> Leg | None:
    """The latest print against its average over the 12 months up to it. None
    without a full year of history behind that print."""
    series = series.dropna().sort_index()
    if series.empty:
        return None
    last = series.index[-1]
    if series.index[0] > last - timedelta(days=YEAR_AVERAGE_DAYS):
        return None
    window = series[series.index > last - timedelta(days=YEAR_AVERAGE_DAYS)]
    value, average = float(series.iloc[-1]), float(window.mean())
    return Leg(
        last.date().isoformat(), value, average, value < average if on_below else value > average
    )


def trend_leg(series: pd.Series) -> Leg | None:
    """A price against its 200-session average. A ratio of a series to itself,
    so GLD's spliced total-return scale does not matter."""
    series = series.dropna().sort_index()
    if len(series) < TREND_SESSIONS:
        return None
    value = float(series.iloc[-1])
    average = float(series.iloc[-TREND_SESSIONS:].mean())
    return Leg(series.index[-1].date().isoformat(), value, average, value > average)


async def load_metals(db: InvestmentDB, today: date) -> Metals:
    """Every reading on the rows knowable by `today`."""
    return Metals(
        gold=GoldSwitch(
            real_yield=year_average_leg(await _series(db, REAL_YIELD_TICKER, today), on_below=True),
            gold_trend=trend_leg(await _series(db, GOLD_TICKER, today)),
        ),
        silver=trend_leg(await _series(db, SILVER_TICKER, today)),
        copper=trend_leg(await _series(db, COPPER_TICKER, today)),
        gold_silver=year_average_leg(await _series(db, GOLD_SILVER, today), on_below=False),
        copper_gold=year_average_leg(await _series(db, COPPER_GOLD, today), on_below=False),
    )


async def _series(db: InvestmentDB, ticker: str, today: date) -> pd.Series:
    since = today - timedelta(days=2 * YEAR_AVERAGE_DAYS)
    rows = await db.query(
        "SELECT ts, level FROM market_data WHERE ticker = :t AND ts > :since AND ts <= :today "
        "AND level IS NOT NULL ORDER BY ts",
        t=ticker,
        since=since.isoformat(),
        today=today.isoformat(),
    )
    return pd.Series(
        [float(r["level"]) for r in rows],
        index=pd.DatetimeIndex([r["ts"] for r in rows]),
        dtype=float,
    )
