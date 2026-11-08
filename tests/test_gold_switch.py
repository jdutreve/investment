"""The digest's gold switch (src/investment/mechanical/gold_switch.py): pure
legs on synthetic series, the loader on a real throwaway SQLite, and the line
the owner reads."""

from datetime import date
from pathlib import Path

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import gold_switch as G
from investment.telegram.digest import _gold_block, _gold_standing


def _daily(values: list[float], end: str = "2026-11-06") -> pd.Series:
    return pd.Series(values, index=pd.bdate_range(end=end, periods=len(values)), dtype=float)


def test_the_real_yield_leg_is_on_below_its_twelve_month_average() -> None:
    falling = _daily([2.0] * 300 + [1.0])
    rising = _daily([1.0] * 300 + [2.0])
    assert G.real_yield_leg(falling).on is True  # type: ignore[union-attr]
    assert G.real_yield_leg(rising).on is False  # type: ignore[union-attr]
    # Less than a year behind the last print: no average to compare with.
    assert G.real_yield_leg(_daily([1.0] * 200)) is None


def test_the_gold_leg_is_on_above_its_200_session_average() -> None:
    assert G.gold_trend_leg(_daily([100.0] * 199 + [120.0])).on is True  # type: ignore[union-attr]
    assert G.gold_trend_leg(_daily([100.0] * 199 + [80.0])).on is False  # type: ignore[union-attr]
    assert G.gold_trend_leg(_daily([100.0] * 199)) is None


def test_either_leg_turns_it_on_and_an_unreadable_leg_does_not_vote() -> None:
    on = G.Leg("2026-11-06", 1.0, 2.0, True)
    off = G.Leg("2026-11-06", 3.0, 2.0, False)
    assert G.GoldSwitch(on, off).on is True
    assert G.GoldSwitch(off, off).on is False
    assert G.GoldSwitch(None, on).on is True
    assert G.GoldSwitch(None, None).on is None


async def test_the_switch_reads_only_what_was_knowable(tmp_path: Path) -> None:
    db = InvestmentDB(tmp_path / "gold.db")
    try:
        for ts, level in zip(
            pd.bdate_range(end="2026-11-06", periods=250), range(250), strict=True
        ):
            await db.command(
                "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
                "VALUES ('GLD', 'GOLD', 'USD', :ts, :lvl)",
                ts=ts.date().isoformat(),
                lvl=1000.0 + level,
            )
        # A print knowable only after `today` changes nothing.
        await db.command(
            "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
            "VALUES ('GLD', 'GOLD', 'USD', '2026-11-10', 1.0)"
        )
        switch = await G.load_gold_switch(db, date(2026, 11, 8))
    finally:
        await db.close()
    assert switch.real_yield is None
    assert switch.gold_trend is not None and switch.gold_trend.as_of == "2026-11-06"
    assert switch.on is True  # a rising line sits above its own average


def test_the_digest_line_says_the_word_then_the_legs() -> None:
    switch = G.GoldSwitch(
        G.Leg("2026-11-08", 2.33, 1.72, False), G.Leg("2026-11-06", 90.7, 100.0, False)
    )
    assert _gold_block(_gold_standing(switch)) == [
        "🥇 Gold: OFF — ON when either leg is on",
        "   5y real yield (DFII5) 2.33% over its 12-month average 1.72% → off (2026-11-08)",
        "   GLD -9.3% vs its 200-day average → off (2026-11-06)",
        "   Role: context only — not measured as an allocation signal",
    ]
    assert _gold_block(_gold_standing(G.GoldSwitch(None, None))) == []
