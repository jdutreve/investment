"""The metals as indicators (src/investment/mechanical/metals.py): pure legs on
synthetic series, the loader and the weekly comparators on a real throwaway
SQLite, and the block the owner reads."""

from datetime import date
from pathlib import Path

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import metals as M
from investment.mechanical.backtests import _materialize_metal_ratios
from investment.telegram.digest import _metals_block, _metals_standing


def _daily(values: list[float], end: str = "2026-11-06") -> pd.Series:
    return pd.Series(values, index=pd.bdate_range(end=end, periods=len(values)), dtype=float)


def test_the_real_yield_leg_is_on_below_its_twelve_month_average() -> None:
    falling = M.year_average_leg(_daily([2.0] * 300 + [1.0]), on_below=True)
    rising = M.year_average_leg(_daily([1.0] * 300 + [2.0]), on_below=True)
    assert falling is not None and falling.on
    assert rising is not None and not rising.on
    # Less than a year behind the last print: no average to compare with.
    assert M.year_average_leg(_daily([1.0] * 200), on_below=True) is None


def test_a_price_is_on_above_its_200_session_average() -> None:
    up = M.trend_leg(_daily([100.0] * 199 + [120.0]))
    down = M.trend_leg(_daily([100.0] * 199 + [80.0]))
    assert up is not None and up.on
    assert down is not None and not down.on
    assert M.trend_leg(_daily([100.0] * 199)) is None


def test_either_gold_leg_turns_it_on_and_an_unreadable_leg_does_not_vote() -> None:
    on = M.Leg("2026-11-06", 1.0, 2.0, True)
    off = M.Leg("2026-11-06", 3.0, 2.0, False)
    assert M.GoldSwitch(on, off).on is True
    assert M.GoldSwitch(off, off).on is False
    assert M.GoldSwitch(None, on).on is True
    assert M.GoldSwitch(None, None).on is None


async def _price(db: InvestmentDB, ticker: str, ts: str, level: float) -> None:
    await db.command(
        "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
        "VALUES (:t, 'METAL', 'USD', :ts, :lvl)",
        t=ticker,
        ts=ts,
        lvl=level,
    )


async def test_the_comparators_are_read_on_the_days_both_metals_print(tmp_path: Path) -> None:
    """Gold/silver is the ounces of silver an ounce of gold buys; a day only one
    future printed makes no ratio, rather than meeting another day's price."""
    db = InvestmentDB(tmp_path / "ratios.db")
    try:
        await _price(db, "GC=F", "2026-11-05", 4000.0)
        await _price(db, "GC=F", "2026-11-06", 4080.0)
        await _price(db, "SI=F", "2026-11-06", 51.0)
        await _price(db, "HG=F", "2026-11-06", 6.12)
        written = await _materialize_metal_ratios(db, 30)
        rows = await db.query(
            "SELECT ticker, ts, level FROM market_data WHERE ticker LIKE '%ratio' ORDER BY ticker"
        )
    finally:
        await db.close()
    assert written == {"gold_silver_ratio": 1, "copper_gold_ratio": 1}
    assert [(r["ticker"], r["ts"], round(r["level"], 4)) for r in rows] == [
        ("copper_gold_ratio", "2026-11-06", 1.5),
        ("gold_silver_ratio", "2026-11-06", 80.0),
    ]


async def test_the_metals_read_only_what_was_knowable(tmp_path: Path) -> None:
    db = InvestmentDB(tmp_path / "metals.db")
    try:
        for n, ts in enumerate(pd.bdate_range(end="2026-11-06", periods=250)):
            await _price(db, "GLD", ts.date().isoformat(), 1000.0 + n)
            await _price(db, "SI=F", ts.date().isoformat(), 60.0 - n / 10)
        # A print knowable only after `today` changes nothing.
        await _price(db, "GLD", "2026-11-10", 1.0)
        metals = await M.load_metals(db, date(2026, 11, 8))
    finally:
        await db.close()
    assert metals.gold.real_yield is None and metals.copper is None
    assert metals.gold.gold_trend is not None and metals.gold.gold_trend.as_of == "2026-11-06"
    assert metals.gold.on is True  # a rising line sits above its own average
    assert metals.silver is not None and not metals.silver.on  # a falling one below


def test_the_block_says_each_metal_in_a_word_then_the_comparators() -> None:
    metals = M.Metals(
        gold=M.GoldSwitch(
            M.Leg("2026-11-08", 2.33, 1.72, False), M.Leg("2026-11-06", 90.7, 100.0, False)
        ),
        silver=M.Leg("2026-11-06", 52.0, 48.0, True),
        copper=None,
        gold_silver=M.Leg("2026-11-06", 78.5, 85.2, False),
        copper_gold=M.Leg("2026-11-06", 1.53, 1.41, True),
    )
    assert _metals_block(_metals_standing(metals)) == [
        "🥇 Gold: OFF — ON when either leg is on",
        "   5y real yield (DFII5) 2.33% over its 12-month average 1.72% → off (2026-11-08)",
        "   GLD -9.3% vs its 200-day average → off (2026-11-06)",
        "🥈 Silver: ON — SI=F +8.3% vs its 200-day average → on (2026-11-06)",
        "⚖️ Gold/silver 78.5 vs its 12-month average 85.2: silver ahead of gold "
        "(risk appetite) (2026-11-06)",
        "⚖️ Copper/gold (per 1000) 1.53 vs its 12-month average 1.41: copper ahead of gold "
        "(growth) (2026-11-06)",
        "   Role: a description of the present, not a forecast — measured 2026-11-08 on "
        "2004-2026, no reading held over the next 12 weeks, and timing GLD on the "
        "switch halved its return (docs/METAL_INDICATORS.md)",
    ]
    nothing = M.Metals(M.GoldSwitch(None, None), None, None, None, None)
    assert _metals_block(_metals_standing(nothing)) == []
