"""The Worker's forecasts, scored when due (src/investment/mechanical/forecasts.py).

Pure tests for `resolve` — the outcome is read on the last print knowable on
the forecast's date, against the reference the rule itself computes — and a
real throwaway SQLite for the record → score → record-back cycle."""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical.forecasts import (
    FORECAST_SCORED_EVENT,
    Resolution,
    describe_record,
    load_forecast_record,
    record_forecasts,
    record_summary,
    resolve,
    score_due_forecasts,
)
from investment.worker.result import Forecast

MADE_ON = date(2026, 10, 18)


def _daily(levels: list[float], start: date = date(2026, 1, 1)) -> pd.Series:
    return pd.Series(levels, index=pd.date_range(start, periods=len(levels), freq="D"))


# -- resolve: pure ------------------------------------------------------------


def test_a_value_forecast_reads_the_last_print_on_or_before_its_date() -> None:
    """ADR-003: what was knowable on the date, never a later print."""
    series = _daily([10.0, 11.0, 12.0, 13.0])  # 1 to 4 January
    outcome = resolve(
        series,
        resolve_on=date(2026, 1, 3),
        side="above",
        compared_with="value",
        threshold=11.5,
        window_days=None,
    )
    assert outcome == Resolution(
        came_true=True, observed=12.0, observed_on=date(2026, 1, 3), reference_value=11.5
    )


def test_a_moving_average_forecast_is_read_against_the_line_that_day() -> None:
    series = _daily([10.0, 10.0, 10.0, 7.0])
    outcome = resolve(
        series,
        resolve_on=date(2026, 1, 4),
        side="below",
        compared_with="moving_average",
        threshold=None,
        window_days=3,
    )
    assert isinstance(outcome, Resolution)
    assert outcome.reference_value == pytest.approx(9.0)  # (10 + 10 + 7) / 3
    assert outcome.came_true


def test_a_median_forecast_uses_the_rules_own_ten_year_median() -> None:
    """A median needs `MEDIAN_MIN_DAYS` prints before it says anything; before
    that, the forecast is unscorable and says why rather than reading a
    median of a handful."""
    long = _daily([1.0] * 200 + [3.0] * 200, start=date(2025, 1, 1))
    outcome = resolve(
        long,
        resolve_on=long.index[-1].date(),
        side="above",
        compared_with="ten_year_median",
        threshold=None,
        window_days=None,
    )
    assert isinstance(outcome, Resolution)
    assert outcome.reference_value == pytest.approx(2.0)  # median of 200 ones and 200 threes
    assert outcome.came_true

    short = resolve(
        _daily([1.0] * 10),
        resolve_on=date(2026, 1, 10),
        side="above",
        compared_with="ten_year_median",
        threshold=None,
        window_days=None,
    )
    assert short == "too little history for its ten year median"


def test_no_print_by_the_date_is_said_not_guessed() -> None:
    outcome = resolve(
        _daily([10.0], start=date(2026, 2, 1)),
        resolve_on=date(2026, 1, 15),
        side="above",
        compared_with="value",
        threshold=5.0,
        window_days=None,
    )
    assert outcome == "no print on or before 2026-01-15"


# -- record, score, read back: real SQLite --------------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "forecasts.db")
    yield conn
    await conn.close()


async def _prices(db: InvestmentDB, ticker: str, start: date, levels: list[float]) -> None:
    await db.append_ts_batch(
        "market_data",
        [
            {
                "ts": (start + timedelta(days=i)).isoformat(),
                "ticker": ticker,
                "asset_class": "bonds",
                "currency": "USD",
                "level": level,
            }
            for i, level in enumerate(levels)
        ],
    )


def _forecast(ticker: str, threshold: float, probability: float) -> Forecast:
    return Forecast(
        statement=f"{ticker} above {threshold}",
        ticker=ticker,
        side="above",
        compared_with="value",
        threshold=threshold,
        horizon_weeks=2,
        probability=probability,
    )


async def _record(db: InvestmentDB, forecasts: list[Forecast]) -> None:
    async with db.transaction():
        event_id = await db.append_event(
            type="WorkerReadingEvent", source_uc="UC8", source_id=None, payload={}
        )
        await record_forecasts(db, reading_event_id=event_id, made_on=MADE_ON, forecasts=forecasts)


async def test_a_forecast_is_scored_once_when_due_and_comes_back_in_the_record(
    db: InvestmentDB,
) -> None:
    # IEF ends at 500 on the due date (two weeks after MADE_ON).
    await _prices(db, "IEF", MADE_ON - timedelta(days=5), [480.0] * 10 + [500.0] * 30)
    await _record(db, [_forecast("IEF", 490.0, 0.8), _forecast("IEF", 510.0, 0.4)])

    # Not due yet: nothing is scored, both are open.
    assert await score_due_forecasts(db, MADE_ON + timedelta(days=13)) == 0
    assert (await load_forecast_record(db)).open == 2

    due = MADE_ON + timedelta(weeks=2)
    assert await score_due_forecasts(db, due) == 2
    assert await score_due_forecasts(db, due) == 0  # idempotent: scored once

    record = await load_forecast_record(db)
    assert (record.scored, record.open, record.unscorable) == (2, 0, 0)
    # 80% on a claim that held, 40% on one that did not: both on the right side.
    assert record.right_side == 2
    assert record.brier == pytest.approx(((0.8 - 1) ** 2 + (0.4 - 0) ** 2) / 2)
    assert "Brier 0.10" in record_summary(record)
    assert "TRUE" in "\n".join(describe_record(record))

    events = await db.query("SELECT type FROM event_log WHERE type = :t", t=FORECAST_SCORED_EVENT)
    assert len(events) == 2  # one event per scored forecast, before its row


async def test_a_series_nobody_prices_is_closed_at_once_as_unscorable(db: InvestmentDB) -> None:
    """The Worker reads next cycle that it named a series the database does not
    hold, instead of waiting weeks to learn that a forecast never could be scored."""
    await _record(db, [_forecast("NOPE", 1.0, 0.7)])
    record = await load_forecast_record(db)
    assert (record.scored, record.open, record.unscorable) == (0, 0, 1)
    assert record.brier is None
    assert "UNSCORABLE, no series named NOPE" in "\n".join(describe_record(record))
    assert record_summary(record) == "Forecast record: none scored yet, 0 open, 1 unscorable."
