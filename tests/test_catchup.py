"""The Monday 08:00 catch-up (`mechanical/catchup.py`, docs/USE_CASES.md UC1).

Real throwaway SQLite, a synthetic fetcher, no network (CLAUDE.md "Tests"). What
these pin is the property the whole job exists for: the tail comes from the
network, the HISTORY comes from the database, and the numbers that result are
the ones a full re-seed would have produced for the same day. Everything else
here is about the failure modes — a dead endpoint, a ticker with no history, a
restated close.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from investment.config import Settings
from investment.db.sqlite import InvestmentDB
from investment.market import derivatives
from investment.mechanical import catchup

TODAY = date(2026, 8, 10)
LOOKBACK = 30

# One daily series, two years of it, so the stored history is long enough for
# the derivative lookback to have something to read.
_DAYS = 730
_START = TODAY - timedelta(days=_DAYS)


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        openrouter_api_key="x",
        fred_api_key="x",
        planner_model="p",
        worker_model="w",
        telegram_bot_token="t",
        telegram_chat_id="c",
        db_path=tmp_path / "catchup.db",
        inbox_path=tmp_path / "inbox",
        sources_path=tmp_path / "sources",
    )


def _rising(days: int, start: date, base: float = 100.0, step: float = 0.1) -> pd.Series:
    index = pd.to_datetime([start + timedelta(days=n) for n in range(days)])
    return pd.Series([base + n * step for n in range(days)], index=index)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "catchup.db")
    yield conn
    await conn.close()


async def _store(db: InvestmentDB, ticker: str, series: pd.Series) -> None:
    """Persist a series the way step 9 does — through the same row builder, so
    the fixture cannot disagree with production about the row shape."""
    deriv = derivatives.compute_derivatives(series, ticker, LOOKBACK)
    await db.append_ts_batch(
        "market_data", derivatives.market_data_rows(ticker, "equities", "USD", deriv, None)
    )


# SPY is SPLICED (`HISTORY_PROXIES`), so its stored level is a ratio-chained
# index and not its price — the case that has to be chained.
_SPY = {
    "ticker": "SPY",
    "source": "yahoo",
    "asset_class": "equities",
    "currency": "USD",
    "transform": "none",
}
# QQQ is not spliced: stored level IS the adjusted close, so a fetch is directly
# comparable and replaces it.
_QQQ = {**_SPY, "ticker": "QQQ"}


def _tail_overlapping(stored: pd.Series, days: int = 5, drift: float = 0.1) -> pd.Series:
    """A fetch window as the real one arrives: OVERLAPPING the stored tail and
    extending past it. On the raw quote's own scale (~1/20th of a spliced index
    level here), which is the whole point of the chained path."""
    anchor = stored.index.max()
    overlap = stored.loc[stored.index >= anchor - pd.Timedelta(days=30)] / 20.0
    extra = pd.Series(
        [float(overlap.iloc[-1]) + drift * (n + 1) for n in range(days)],
        index=pd.to_datetime([anchor.date() + timedelta(days=n + 1) for n in range(days)]),
    )
    return pd.concat([overlap, extra])


async def test_the_tail_is_fetched_and_the_history_is_not(db: InvestmentDB) -> None:
    """The point of the job. The fetcher is asked for a BOUNDED window — it
    records what it was asked for — while the stored history stays untouched and
    still feeds the derivatives."""
    stored = _rising(_DAYS, _START)
    await _store(db, "SPY", stored)
    asked: list[date | None] = []

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        asked.append(start)
        return _tail_overlapping(stored)

    written = await catchup.refresh_series(db, _SPY, "k", fetch_raw=fetch, lookback=LOOKBACK)

    assert asked == [stored.index.max().date() - timedelta(days=catchup.FETCH_MARGIN_DAYS)]
    assert written == 5  # only the five days past the anchor, not the whole window
    level = await catchup.stored_level(db, "SPY")
    assert level.index.max() == stored.index.max() + pd.Timedelta(days=5)
    assert level.index.min().date() == _START  # nothing older was dropped


async def test_the_derivatives_match_what_a_full_rebuild_would_produce(db: InvestmentDB) -> None:
    """THE INVARIANT THAT MATTERS (CLAUDE.md: "two implementations must produce
    the same numbers"). The catch-up computes speed/acceleration over the MERGED
    series, so its newest rows must equal what a whole-history pass would have
    written for the same day — a windowed recompute would read a truncated level
    and get both wrong."""
    stored = _rising(_DAYS, _START)
    tail = _rising(5, TODAY + timedelta(days=1), base=100.0 + _DAYS * 0.1)
    await _store(db, "QQQ", stored)

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        return pd.concat([stored.iloc[-30:], tail])

    await catchup.refresh_series(db, _QQQ, "k", fetch_raw=fetch, lookback=LOOKBACK)

    whole = derivatives.compute_derivatives(pd.concat([stored, tail]), "QQQ", LOOKBACK)
    rows = await db.query(
        "SELECT ts, level, speed, acceleration FROM market_data "
        "WHERE ticker = 'QQQ' ORDER BY ts DESC LIMIT 3"
    )
    for row in rows:
        expected = whole.loc[pd.Timestamp(str(row["ts"]))]
        assert row["level"] == pytest.approx(float(expected["level"]))
        assert row["speed"] == pytest.approx(float(expected["speed"]))
        assert row["acceleration"] == pytest.approx(float(expected["acceleration"]))


async def test_a_restated_close_wins_over_the_stored_one(db: InvestmentDB) -> None:
    """Why the window OVERLAPS at all, on the UNSPLICED path where stored and
    fetched are the same quantity. Yahoo revises adjusted closes after a
    dividend, and only the re-read window gets the correction — `combine_first`
    is ordered so the fetched value wins."""
    await _store(db, "QQQ", _rising(_DAYS, _START))
    restated_day = TODAY - timedelta(days=3)

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        return pd.Series([999.0], index=pd.to_datetime([restated_day]))

    await catchup.refresh_series(db, _QQQ, "k", fetch_raw=fetch, lookback=LOOKBACK)

    level = await catchup.stored_level(db, "QQQ")
    assert level.loc[pd.Timestamp(restated_day)] == 999.0


async def test_a_ticker_with_no_history_is_refused_rather_than_half_filled(
    db: InvestmentDB,
) -> None:
    """A bounded window would give a newly added ticker a truncated history that
    LOOKS complete, and every rolling window computed on it afterwards would be
    quietly wrong. Backfilling is the seed's job."""

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        return _rising(10, TODAY)

    with pytest.raises(LookupError, match="run the seed"):
        await catchup.refresh_series(db, _SPY, "k", fetch_raw=fetch, lookback=LOOKBACK)


async def test_an_empty_fetch_writes_nothing_and_is_not_an_error(db: InvestmentDB) -> None:
    """A market holiday, or a series that simply has not printed. Distinct from
    a FAILURE, which the caller records by name."""
    await _store(db, "QQQ", _rising(_DAYS, _START))

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        return pd.Series(dtype=float)

    assert await catchup.refresh_series(db, _QQQ, "k", fetch_raw=fetch, lookback=LOOKBACK) == 0


async def test_one_dead_endpoint_does_not_cost_the_others(db: InvestmentDB, tmp_path: Path) -> None:
    """A single dead FRED endpoint must not cost the week's ranking. It is
    RECORDED by name — "3 skipped" would not tell the owner which feed died, and
    the freshness alert is what turns a persistent skip into something read on
    Monday."""
    for ticker in ("SPY", "IEF"):
        await _store(db, ticker, _rising(_DAYS, _START))

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        if str(row["ticker"]) == "SPY":
            raise RuntimeError("yahoo said no")
        return pd.Series(dtype=float)

    refreshed, _rows, skipped = await catchup.refresh_market_data(
        db,
        _settings(tmp_path),
        fetch_raw=fetch,
        yahoo_rate_limit_seconds=0.0,
        lookback=LOOKBACK,
    )
    assert "SPY" in skipped and "yahoo said no" in skipped["SPY"]
    assert "IEF" not in skipped  # it had no data to add, which is not a failure
    assert refreshed >= 1


async def test_a_second_run_changes_nothing(db: InvestmentDB) -> None:
    """Idempotent, which is what lets the cron and the heartbeat both reach it:
    every write is an UPSERT keyed on (ticker, ts)."""
    stored = _rising(_DAYS, _START)
    await _store(db, "SPY", stored)
    tail = _tail_overlapping(stored)

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        return tail

    await catchup.refresh_series(db, _SPY, "k", fetch_raw=fetch, lookback=LOOKBACK)
    first = await db.query("SELECT count(*) AS n FROM market_data WHERE ticker = 'SPY'")
    await catchup.refresh_series(db, _SPY, "k", fetch_raw=fetch, lookback=LOOKBACK)
    second = await db.query("SELECT count(*) AS n FROM market_data WHERE ticker = 'SPY'")
    assert first[0]["n"] == second[0]["n"]


async def test_the_composites_are_recomputed_over_the_whole_history(db: InvestmentDB) -> None:
    """Both composites are z-scores against a TRAILING window — 10 years for
    growth, 5 for liquidity. Computed over a short window they would be scored
    against a different distribution than the seed used and would drift from it
    on the same date, so the inputs are read back from the database rather than
    from whatever the fetch returned."""
    months = pd.to_datetime([date(2016, 1, 1) + timedelta(days=30 * n) for n in range(130)])
    await db.append_ts_batch(
        "market_data",
        [
            {
                "ticker": ticker,
                "asset_class": "MACRO",
                "currency": "USD",
                "ts": ts.date().isoformat(),
                "level": value,
            }
            for ticker, base in (("INDPRO", 2.0), ("UNRATE", 5.0))
            for ts, value in zip(months, [base + 0.01 * n for n in range(len(months))], strict=True)
        ],
    )

    written, rows = await catchup.refresh_composites(db, LOOKBACK)

    assert "GROWTH_COMPOSITE" in written and rows > 0
    composite = await catchup.stored_level(db, "GROWTH_COMPOSITE")
    assert not composite.empty
    # GLOBAL_LIQUIDITY has no components here: skipped, never invented.
    assert "GLOBAL_LIQUIDITY" not in written


async def test_a_spliced_tickers_tail_is_chained_and_never_pasted(db: InvestmentDB) -> None:
    """THE REGRESSION. A spliced ticker's stored level is a ratio-chained INDEX,
    not its price: measured live on 2026-08-12, SPY read 16223 in `market_data`
    while Yahoo quoted 773 — a factor of 21. Pasting the quote in writes a -95%
    single-day return into the series, and this project has already paid for
    that once (`splice._construct_spliced_level`: a raw ETF price left in a
    spliced series produced "a ~-91% return followed by a ~+1000% one, straight
    into every NAV built on them", found at M4 by the All Weather check).

    The unit tests passed through it: a synthetic fixture has no splice, so
    stored and fetched shared a scale and the defect was invisible. It took a
    real ticker against a copy of the live database to see it."""
    stored = _rising(_DAYS, _START, base=16000.0, step=1.0)  # an index, not a price
    await _store(db, "SPY", stored)
    quote = stored / 21.0  # the same shape on the quote's own scale

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        anchor = stored.index.max()
        extra = pd.Series(
            [float(quote.iloc[-1]) * 1.01, float(quote.iloc[-1]) * 1.02],
            index=pd.to_datetime([anchor + pd.Timedelta(days=1), anchor + pd.Timedelta(days=2)]),
        )
        return pd.concat([quote.iloc[-40:], extra])

    await catchup.refresh_series(db, _SPY, "k", fetch_raw=fetch, lookback=LOOKBACK)

    level = await catchup.stored_level(db, "SPY")
    # The join carries a REAL return, not a change of scale.
    returns = level.pct_change().dropna()
    assert returns.abs().max() < 0.05, "a scale break would show as a ~-95% day"
    # ...and the new points sit on the STORED scale, carrying the quote's moves.
    assert float(level.iloc[-2]) == pytest.approx(float(stored.iloc[-1]) * 1.01, rel=1e-9)
    assert float(level.iloc[-1]) == pytest.approx(float(stored.iloc[-1]) * 1.02, rel=1e-9)


async def test_a_gap_too_wide_to_chain_is_refused_rather_than_guessed(db: InvestmentDB) -> None:
    """No overlap means no anchor, and the return spanning the gap is unknown.
    Inventing it would put a fabricated move into a 35-year series; refusing
    leaves the stored history intact and lets the freshness alert say so."""
    stored = _rising(_DAYS, _START, base=16000.0, step=1.0)
    await _store(db, "SPY", stored)

    async def fetch(row: Any, key: str, start: date | None) -> pd.Series:
        far = stored.index.max() + pd.Timedelta(days=90)
        return pd.Series([800.0, 801.0], index=pd.to_datetime([far, far + pd.Timedelta(days=1)]))

    with pytest.raises(LookupError, match="too wide to chain"):
        await catchup.refresh_series(db, _SPY, "k", fetch_raw=fetch, lookback=LOOKBACK)


async def test_a_macro_series_is_never_chained(db: InvestmentDB) -> None:
    """Chaining assumes a strictly positive price index. `T10Y2Y` goes NEGATIVE
    at every curve inversion, and a percentage change across zero is
    meaningless — so a macro fetch is taken as it came."""
    slope = pd.Series(
        [0.5, 0.2, -0.1, -0.3, 0.1],
        index=pd.to_datetime([TODAY - timedelta(days=n) for n in range(5, 0, -1)]),
    )
    await _store(db, "T10Y2Y", slope)
    fetched = pd.Series([-0.2, 0.4], index=pd.to_datetime([TODAY - timedelta(days=1), TODAY]))

    rebased = catchup.rebase_onto(fetched, slope, chain=False)
    assert list(rebased) == [-0.2, 0.4]  # as fetched, sign intact
