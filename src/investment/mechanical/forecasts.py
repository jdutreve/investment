"""The Worker's forecasts: recorded with the reading that made them, scored
mechanically when due, and read back by the Worker as its own record.

WHY (owner, 2026-10-18: "the worker only repeats the facts it is given.
Nothing relevant comes out of it"). The reading's "what to watch" was the
rule's mechanics written as conditions — true by construction, so nothing could
ever say whether the Worker reads the market well. A dated probability can be
wrong, and this module is what finds out. No LLM here: the outcome is a print in
`market_data`, the reference is computed the way the rule computes it.

POINT IN TIME (ADR-003). A forecast is scored on the last print whose `ts` is on
or before its `resolve_on`, which is by construction what was knowable that day,
so a monthly series is read as it stood then and never revised by a later print.
The catch-up runs first in the chain, so by the time this job runs on or after
`resolve_on`, everything knowable on that day is in the table.

THE SCORE IS THE BRIER SCORE, the mean of (probability - outcome)², and it is
always shown beside 0.25 — what saying 50% to everything scores — because a
number with nothing to compare it to reads as good whatever it is.

AND BESIDE HISTORY (owner, 2026-11-01). 50% is too easy a line: "GLD back above
its 150-day line within 8 weeks: 15%" is right most of the time without any
judgement in it. Each forecast carries a BASE RATE (`base_rate`): how often the
same move, from the same distance to the same reference, came true over the
series' history known the day it was made. The Worker's Brier is shown beside
the base rate's: a forecast that only restates history earns nothing.
"""

import dataclasses
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from ulid import ULID

from investment.db.seed_data import ALLOWED_TICKERS
from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios
from investment.mechanical.market_signal import MEDIAN_MIN_DAYS, MEDIAN_WINDOW_YEARS
from investment.worker.result import Forecast

logger = logging.getLogger(__name__)

FORECAST_SCORED_EVENT = "ForecastScoredEvent"
SOURCE_UC = "UC8"

# What saying 50% to every forecast scores: the line a record has to beat.
COIN_BRIER = 0.25
# How many scored forecasts the Worker reads back in full, newest first.
RECENT_SCORED = 5
# Below this many past moves a base rate is not computed: a year of daily
# prints, or twenty years of a monthly series.
MIN_BASE_MOVES = 240


@dataclasses.dataclass(frozen=True)
class Resolution:
    """What the market said on a forecast's date."""

    came_true: bool
    observed: float
    observed_on: date
    reference_value: float


async def record_forecasts(
    db: InvestmentDB,
    *,
    reading_event_id: str,
    made_on: date,
    forecasts: Sequence[Forecast],
) -> None:
    """Insert the forecasts of one reading. Called INSIDE the transaction that
    appended the reading's event, after it (CLAUDE.md "EventLog": the event
    before the rows it explains).

    A ticker the database does not hold is recorded AND closed at once as
    unscorable, rather than left open for weeks: the Worker reads next cycle
    that it named a series nobody prices, which is the lesson worth having."""
    now = datetime.now(UTC).isoformat()
    for forecast in forecasts:
        held = await db.query(
            "SELECT 1 FROM market_data WHERE ticker = :t LIMIT 1", t=forecast.ticker
        )
        await db.command(
            "INSERT INTO forecast (id, reading_event_id, made_on, resolve_on, statement, "
            "ticker, side, compared_with, threshold, window_days, probability, scored_on, "
            "unscorable_reason, trace, created_at) VALUES (:id, :reading, :made, :resolve, "
            ":statement, :ticker, :side, :compared, :threshold, :window, :p, :scored, "
            ":reason, :trace, :now)",
            id=f"fc-{ULID()}",
            reading=reading_event_id,
            made=made_on.isoformat(),
            resolve=(made_on + timedelta(weeks=forecast.horizon_weeks)).isoformat(),
            statement=forecast.statement,
            ticker=forecast.ticker,
            side=forecast.side,
            compared=forecast.compared_with,
            threshold=forecast.threshold,
            window=forecast.window_days,
            p=forecast.probability,
            scored=None if held else made_on.isoformat(),
            reason=None if held else f"no series named {forecast.ticker} in market_data",
            trace="UC8 Worker forecast",
            now=now,
        )
    # Frozen now, from what is knowable today: the line this forecast is scored
    # against cannot be chosen after its outcome is known.
    await fill_base_rates(db)


def resolve(
    series: pd.Series,
    *,
    resolve_on: date,
    side: str,
    compared_with: str,
    threshold: float | None,
    window_days: int | None,
) -> Resolution | str:
    """The outcome of one forecast, or the reason it has none. Pure.

    The two computed references are the rule's own (`market_signal.load_series`):
    the ten-year median over a TIME window with at least `MEDIAN_MIN_DAYS`
    observations, and a moving average over a COUNT of rows. The rule reads them
    on its calendar-aligned, forward-filled copy of the series; this reads the
    series as published, which differs only on a day one has a print and the
    other does not."""
    known = series[series.index <= pd.Timestamp(resolve_on)].dropna()
    if known.empty:
        return f"no print on or before {resolve_on.isoformat()}"
    references = reference_series(
        known, compared_with=compared_with, threshold=threshold, window_days=window_days
    )
    if isinstance(references, str):
        return references
    reference = float(references.iloc[-1])
    if pd.isna(reference):
        return f"too little history for its {compared_with.replace('_', ' ')}"
    observed = float(known.iloc[-1])
    came_true = observed > reference if side == "above" else observed < reference
    return Resolution(
        came_true=came_true,
        observed=observed,
        observed_on=known.index[-1].date(),
        reference_value=reference,
    )


def reference_series(
    known: pd.Series, *, compared_with: str, threshold: float | None, window_days: int | None
) -> pd.Series | str:
    """The forecast's reference at every date of `known`, or why it has none.
    One computation for the score (`resolve`) and the base rate (`base_rate`)."""
    if compared_with == "value":
        if threshold is None:
            return "no threshold"
        return pd.Series(float(threshold), index=known.index)
    if compared_with == "ten_year_median":
        return known.rolling(f"{365 * MEDIAN_WINDOW_YEARS}D", min_periods=MEDIAN_MIN_DAYS).median()
    if compared_with == "moving_average" and window_days:
        return known.rolling(window_days, min_periods=window_days).mean()
    return f"unknown reference {compared_with!r}"


def base_rate(
    series: pd.Series,
    *,
    made_on: date,
    horizon_weeks: int,
    side: str,
    compared_with: str,
    threshold: float | None,
    window_days: int | None,
    relative: bool,
) -> float | None:
    """How often the claim would have come true from where the series stood on
    `made_on`, moved by each of its own past moves over the same horizon. Pure,
    and point in time: only prints dated on or before `made_on` are read.

    The quantity that has to cross is the GAP between the series and its
    reference: in log terms for a price (`relative`), whose moves scale with its
    level, and in its own units for a rate or a spread. Every past date gives one
    move of that gap over the horizon; the base rate is the share of them that,
    added to today's gap, land on the claimed side. None when the series has too
    little history to say (fewer than `MIN_BASE_MOVES` moves)."""
    known = series[series.index <= pd.Timestamp(made_on)].dropna()
    references = reference_series(
        known, compared_with=compared_with, threshold=threshold, window_days=window_days
    )
    if isinstance(references, str) or known.empty:
        return None
    if relative:
        if (known <= 0).any() or (references.dropna() <= 0).any():
            return None
        gap = pd.Series(np.log((known / references).to_numpy()), index=known.index)
    else:
        gap = known - references
    gap = gap.dropna()
    if gap.empty:
        return None
    later = gap.index + pd.Timedelta(weeks=horizon_weeks)
    within = later <= gap.index[-1]
    positions = gap.index.searchsorted(later[within], side="right") - 1
    moves = gap.to_numpy()[positions] - gap.to_numpy()[: int(within.sum())]
    if len(moves) < MIN_BASE_MOVES:
        return None
    landed = gap.iloc[-1] + moves
    return float((landed > 0).mean() if side == "above" else (landed < 0).mean())


def is_price(ticker: str) -> bool:
    """A traded price, whose moves are read in log terms by `base_rate`: a
    Yahoo series that is not a rate (^IRX is quoted in percent)."""
    row = next((r for r in ALLOWED_TICKERS if r["ticker"] == ticker), None)
    return row is not None and row["source"] == "yahoo" and not ticker.startswith("^")


async def fill_base_rates(db: InvestmentDB) -> int:
    """Compute the base rate of every forecast that has none, each as of its
    own `made_on`. Idempotent and resumable: a filled row is never selected
    again. Run at recording time and before each scoring, which is what fills
    the forecasts made before the column existed."""
    rows = await db.query(
        "SELECT id, ticker, made_on, resolve_on, side, compared_with, threshold, window_days "
        "FROM forecast WHERE base_rate IS NULL AND unscorable_reason IS NULL"
    )
    filled = 0
    series_by_ticker: dict[str, pd.Series] = {}
    for row in rows:
        ticker = str(row["ticker"])
        if ticker not in series_by_ticker:
            series_by_ticker[ticker] = await ratios.load_price(db, ticker)
        made_on = date.fromisoformat(str(row["made_on"]))
        weeks = (date.fromisoformat(str(row["resolve_on"])) - made_on).days // 7
        rate = base_rate(
            series_by_ticker[ticker],
            made_on=made_on,
            horizon_weeks=weeks,
            side=str(row["side"]),
            compared_with=str(row["compared_with"]),
            threshold=row["threshold"],
            window_days=row["window_days"],
            relative=is_price(ticker),
        )
        if rate is None:
            continue
        await db.command("UPDATE forecast SET base_rate = :r WHERE id = :id", r=rate, id=row["id"])
        filled += 1
    if filled:
        logger.info("forecasts: %d base rate(s) filled", filled)
    return filled


async def score_due_forecasts(db: InvestmentDB, today: date) -> int:
    """Score every forecast whose date has come. Idempotent: a scored row has
    `scored_on` and is never selected again. Returns how many were scored."""
    await fill_base_rates(db)
    due = await db.query(
        "SELECT * FROM forecast WHERE scored_on IS NULL AND resolve_on <= :today "
        "ORDER BY resolve_on, id",
        today=today.isoformat(),
    )
    series_by_ticker: dict[str, pd.Series] = {}
    for row in due:
        ticker = str(row["ticker"])
        if ticker not in series_by_ticker:
            series_by_ticker[ticker] = await ratios.load_price(db, ticker)
        outcome = resolve(
            series_by_ticker[ticker],
            resolve_on=date.fromisoformat(str(row["resolve_on"])),
            side=str(row["side"]),
            compared_with=str(row["compared_with"]),
            threshold=row["threshold"],
            window_days=row["window_days"],
        )
        payload: dict[str, Any] = {
            "forecast_id": row["id"],
            "statement": row["statement"],
            "probability": row["probability"],
            "resolve_on": row["resolve_on"],
        }
        if isinstance(outcome, Resolution):
            payload |= {
                "came_true": outcome.came_true,
                "observed": outcome.observed,
                "observed_on": outcome.observed_on.isoformat(),
                "reference_value": outcome.reference_value,
            }
        else:
            payload["unscorable_reason"] = outcome
        async with db.transaction():
            await db.append_event(
                type=FORECAST_SCORED_EVENT,
                source_uc=SOURCE_UC,
                source_id=str(row["id"]),
                payload=payload,
                event_date=today,
            )
            await db.command(
                "UPDATE forecast SET scored_on = :today, came_true = :came_true, "
                "observed = :observed, observed_on = :observed_on, "
                "reference_value = :reference, unscorable_reason = :reason WHERE id = :id",
                today=today.isoformat(),
                came_true=payload.get("came_true"),
                observed=payload.get("observed"),
                observed_on=payload.get("observed_on"),
                reference=payload.get("reference_value"),
                reason=payload.get("unscorable_reason"),
                id=row["id"],
            )
    if due:
        logger.info("forecasts: %d scored", len(due))
    return len(due)


@dataclasses.dataclass(frozen=True)
class ForecastRecord:
    """The Worker's record as forecaster. `brier` is None until one is scored."""

    scored: int
    right_side: int  # said more than 50% and it happened, or less than 50% and it did not
    brier: float | None
    unscorable: int
    open: int
    recent: list[dict[str, Any]]
    # The base rates' Brier on the scored forecasts that have one, and how many.
    base_brier: float | None = None
    with_base: int = 0
    # The base rate of each forecast of the latest reading, by its statement.
    latest_base_rates: dict[str, float] = dataclasses.field(default_factory=dict)


async def load_forecast_record(db: InvestmentDB) -> ForecastRecord:
    rows = await db.query(
        "SELECT * FROM forecast WHERE scored_on IS NOT NULL ORDER BY resolve_on DESC, id DESC"
    )
    scored = [r for r in rows if r["came_true"] is not None]
    open_rows = await db.query("SELECT COUNT(*) AS n FROM forecast WHERE scored_on IS NULL")
    errors = [(float(r["probability"]) - float(bool(r["came_true"]))) ** 2 for r in scored]
    paired = [r for r in scored if r["base_rate"] is not None]
    base_errors = [(float(r["base_rate"]) - float(bool(r["came_true"]))) ** 2 for r in paired]
    latest = await db.query(
        "SELECT statement, base_rate FROM forecast WHERE base_rate IS NOT NULL AND "
        "reading_event_id = (SELECT reading_event_id FROM forecast ORDER BY made_on DESC, "
        "id DESC LIMIT 1)"
    )
    return ForecastRecord(
        scored=len(scored),
        right_side=sum(
            (float(r["probability"]) > 0.5) == bool(r["came_true"])
            for r in scored
            if float(r["probability"]) != 0.5
        ),
        brier=sum(errors) / len(errors) if errors else None,
        unscorable=len(rows) - len(scored),
        open=int(open_rows[0]["n"]) if open_rows else 0,
        recent=[dict(r) for r in rows[:RECENT_SCORED]],
        base_brier=sum(base_errors) / len(base_errors) if base_errors else None,
        with_base=len(paired),
        latest_base_rates={str(r["statement"]): float(r["base_rate"]) for r in latest},
    )


def record_summary(record: ForecastRecord) -> str:
    """The record in one sentence — the line the digest, the mail and the
    dashboard print under the Worker's forecasts (`digest.collect_digest_inputs`
    carries it, so the three fronts print one sentence)."""
    r = dataclasses.asdict(record)
    unscorable = f", {r['unscorable']} unscorable" if r["unscorable"] else ""
    if not r["scored"]:
        return f"Forecast record: none scored yet, {r['open']} open{unscorable}."
    history = (
        f", history's own frequency {r['base_brier']:.2f} on {r['with_base']} of them"
        if r["base_brier"] is not None
        else ""
    )
    return (
        f"Forecast record: {r['scored']} scored, Brier {r['brier']:.2f} (saying 50% to "
        f"everything scores {COIN_BRIER:.2f}{history}; lower is better), {r['right_side']} on "
        f"the right side of 50%, {r['open']} open{unscorable}."
    )


def describe_record(record: ForecastRecord) -> list[str]:
    """The record as the Worker reads it in its context: the summary, then the
    last forecasts scored with what the market said, and those it could not be
    scored on."""
    lines = ["YOUR FORECAST RECORD (scored mechanically on the date each was due):"]
    lines.append(f"  {record_summary(record)}")
    for row in record.recent:
        said = f"said {float(row['probability']):.0%}"
        if row.get("base_rate") is not None:
            said += f", history {float(row['base_rate']):.0%}"
        if row["came_true"] is None:
            lines.append(
                f"  - {row['statement']} ({said}, due {row['resolve_on']}): UNSCORABLE, "
                f"{row['unscorable_reason']}"
            )
            continue
        verdict = "TRUE" if row["came_true"] else "FALSE"
        lines.append(
            f"  - {row['statement']} ({said}, due {row['resolve_on']}): {verdict} — "
            f"{row['ticker']} {row['observed']:.4g} on {row['observed_on']} against "
            f"{row['reference_value']:.4g}"
        )
    return lines
