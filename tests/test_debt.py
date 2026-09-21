"""The debt leg's two composites (market/debt.py).

They exist because a 2026-09-20 recall study of Dalio's *Big Debt Crises*
found the curator's reading complete and its yield capped by a vocabulary that
held no debt aggregate. These tests pin the three properties that make the
series honest rather than merely present: the units, the point-in-time
alignment of two differently-dated legs, and the calendar (not observation)
year-on-year window.
"""

import pandas as pd
import pytest

from investment.market import debt
from investment.market.derivatives import OBSERVATION_LOOKBACK_TICKERS, compute_derivatives


def _q(dates: list[str], values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.to_datetime(dates))


def test_debt_to_gdp_is_a_percent_and_converts_millions_to_billions() -> None:
    """TCMDODNS prints millions, GDP billions. Skipping the conversion would
    give a ratio 1000x too large and every predicate written against it would
    be silently unsatisfiable."""
    d2g = debt.compute_debt_to_gdp(
        _q(["2026-01-01"], [75_000_000.0]),  # 75 trillion, in millions
        _q(["2026-01-01"], [25_000.0]),  # 25 trillion, in billions
    )
    assert d2g.iloc[0] == pytest.approx(300.0)


def test_the_ratio_prints_on_the_debt_calendar_not_on_the_union() -> None:
    """Both legs grow every quarter and print on different dates, so on the
    UNION of the two calendars the ratio sawtooths: a GDP print lifts the
    denominator alone and it falls, the next debt print lifts the numerator
    alone and it jumps back. `speed` would then measure which leg published
    last. One point per debt print, against the GDP known then."""
    d2g = debt.compute_debt_to_gdp(
        _q(["2026-03-15", "2026-06-15"], [75_000_000.0, 76_000_000.0]),
        _q(["2026-01-30", "2026-04-30"], [25_000.0, 25_200.0]),
    )
    assert list(d2g.index) == [pd.Timestamp("2026-03-15"), pd.Timestamp("2026-06-15")]
    assert d2g.iloc[0] == pytest.approx(300.0)  # 75.0 / 25.0
    assert d2g.iloc[1] == pytest.approx(301.587, abs=0.01)  # 76.0 / 25.2


def test_a_late_gdp_release_skips_the_quarter_rather_than_pairing_the_wrong_one() -> None:
    """GDP for a quarter normally prints ~50 days before that quarter's debt
    figure, which is what makes the pairing work. When a release slips it does
    not: Q3 2025 GDP appeared ten days AFTER the debt figure, so pairing on
    "latest known" divided Q3 debt by Q2 GDP and invented a 5.8-point jump.
    A stale denominator is not the same quarter, so the point is dropped."""
    d2g = debt.compute_debt_to_gdp(
        _q(["2025-09-13", "2025-12-13", "2026-03-15"], [78_000_000.0, 80_000_000.0, 81_000_000.0]),
        # No print between 2025-07-30 and 2025-12-23 — the real 2025 gap.
        _q(["2025-07-30", "2025-12-23", "2026-02-20"], [30_331.0, 31_095.0, 31_490.0]),
    )
    assert pd.Timestamp("2025-12-13") not in d2g.index, "a quarter-stale GDP is not this quarter"
    assert list(d2g.index) == [pd.Timestamp("2025-09-13"), pd.Timestamp("2026-03-15")]


def test_a_leg_with_no_history_yields_nothing_rather_than_raising() -> None:
    """A missing component must not abort a 35-year backfill."""
    assert debt.compute_debt_to_gdp(pd.Series(dtype=float), _q(["2026-01-01"], [1.0])).empty
    assert debt.compute_credit_growth(pd.Series(dtype=float)).empty


def test_credit_growth_window_is_calendar_not_twelve_observations() -> None:
    """The reason this is not `apply_transform`'s `yoy_pct`: bank credit is
    WEEKLY, so a 12-observation shift would be twelve weeks wearing a year's
    name. 52 weekly points, +10% over the year, must read ~10 and not ~2."""
    index = pd.date_range("2025-01-01", periods=105, freq="7D")  # two years
    level = pd.Series([100.0 * (1.10 ** (i / 52)) for i in range(105)], index=index)
    growth = debt.compute_credit_growth(level)
    assert growth.iloc[-1] == pytest.approx(10.0, abs=0.5)
    # What the 12-observation shift would have produced on this same series,
    # for contrast — twelve weeks of a 10%/year path is about 2%.
    assert (100.0 * (level.pct_change(periods=12))).iloc[-1] == pytest.approx(2.2, abs=0.3)
    # And it only begins once a full year is behind it — never against a
    # year-ago value the series does not have.
    assert growth.index.min() >= index.min() + pd.Timedelta(days=365)


def test_quarterly_series_take_the_one_observation_lookback() -> None:
    """A calendar window on a quarterly series is zero between prints. The
    set was named for monthly members only until the debt leg arrived."""
    for ticker in ("TCMDODNS", "GDP", "DRSFRMACBS", "DEBT_TO_GDP"):
        assert ticker in OBSERVATION_LOOKBACK_TICKERS
    quarterly = _q(["2025-01-01", "2025-04-01", "2025-07-01"], [300.0, 310.0, 315.0])
    deriv = compute_derivatives(quarterly, "DEBT_TO_GDP", default_lookback_days=30)
    # Quarter-on-quarter change in points of GDP — Dalio's "leveraging up at
    # 20 to 25 percent of GDP over three years" is read here.
    assert deriv["speed"].iloc[1] == pytest.approx(10.0)
    assert deriv["acceleration"].iloc[2] == pytest.approx(-5.0)


def test_every_composite_is_declared_once_and_built_the_same_way() -> None:
    """Four composites, and until 2026-09-21 each had a hand-written block in
    BOTH producers — eight that had to agree. They did not: `refresh_composites`
    still announced "GROWTH_COMPOSITE and GLOBAL_LIQUIDITY" after the debt leg
    made it four. A fifth composite is now a row and a function."""
    from investment.market.composites import COMPOSITES, missing_inputs, rows_for

    tickers = [c.ticker for c in COMPOSITES]
    # A set, not a sequence: the registry's ORDER is not a property anything
    # depends on, and pinning it would make a reordering fail for no reason.
    assert set(tickers) == {
        "GROWTH_COMPOSITE",
        "GLOBAL_LIQUIDITY",
        "DEBT_TO_GDP",
        "CREDIT_GROWTH",
    }
    assert len(set(tickers)) == len(tickers), "a ticker declared twice would be written twice"
    for c in COMPOSITES:
        assert c.inputs, f"{c.ticker} declares no inputs"
        # An input that is absent and one that is present but empty are the
        # same fact: the catch-up reads inputs back from SQLite and gets an
        # empty series for one that was never fetched.
        assert missing_inputs(c, {}) == list(c.inputs)
        assert missing_inputs(c, dict.fromkeys(c.inputs, pd.Series(dtype=float))) == list(c.inputs)

    debt_leg = next(c for c in COMPOSITES if c.ticker == "DEBT_TO_GDP")
    rows = rows_for(
        debt_leg,
        {
            "TCMDODNS": _q(["2026-03-15", "2026-06-15"], [75_000_000.0, 76_000_000.0]),
            "GDP": _q(["2026-01-30", "2026-04-30"], [25_000.0, 25_200.0]),
        },
        lookback=30,
        start=None,
    )
    assert [r["ticker"] for r in rows] == ["DEBT_TO_GDP", "DEBT_TO_GDP"]
    assert rows[0]["asset_class"] == "MACRO" and rows[0]["currency"] == "USD"
    assert rows[0]["level"] == pytest.approx(300.0)


def test_a_composite_starts_at_its_first_value_not_at_its_warm_up() -> None:
    """A trailing-window composite is NaN until its window fills, and
    `market_data_rows` turns NaN into SQL NULL — rows with no value but a real
    `ts`. Those rows dated the series, so `replace_ts_series(keep_earlier_rows
    =True)` bounded its delete by the warm-up's first date and the weekly
    catch-up overwrote the seed's real early values with its own NULLs. The
    live database carried 122 such NULLs for GROWTH_COMPOSITE (1991-2001) and
    406 for GLOBAL_LIQUIDITY (1991-2003) before this."""
    from investment.market.composites import Composite, rows_for

    warmed_up = pd.Series(
        [float("nan"), float("nan"), 100.0, 101.0],
        index=pd.to_datetime(["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]),
    )
    composite = Composite("Z", "MACRO", ("X",), lambda _s: warmed_up)
    rows = rows_for(composite, {"X": warmed_up}, lookback=30, start=None)
    assert [r["ts"] for r in rows] == ["2026-03-31", "2026-04-30"]
    assert rows[0]["level"] == pytest.approx(100.0)


def test_an_interior_gap_survives_because_it_is_a_real_hole() -> None:
    """LEADING NaNs only. A hole in the middle is missing input, not a warm-up,
    and closing it would make the 1-observation lookback difference two
    readings that are not adjacent."""
    from investment.market.composites import Composite, rows_for

    holed = pd.Series(
        [100.0, float("nan"), 102.0],
        index=pd.to_datetime(["2026-01-31", "2026-02-28", "2026-03-31"]),
    )
    composite = Composite("Z", "MACRO", ("X",), lambda _s: holed)
    rows = rows_for(composite, {"X": holed}, lookback=30, start=None)
    assert [r["ts"] for r in rows] == ["2026-01-31", "2026-02-28", "2026-03-31"]
    assert rows[1]["level"] is None
