"""The weekly forward sweep (`invariants.confront_completed_moments`,
docs/INVARIANT_TASKS.md 2.1) against a real throwaway SQLite.

Each test names the GUARANTEE it holds, not the function it calls: mechanical
measurement outlives birth, a moment is confronted once and only when its
window has completed, and a re-sweep never counts a moment twice.
"""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import invariants
from investment.mechanical.invariants import (
    BIRTH_SOURCE,
    CONFRONTATION_EVENT,
    FORWARD_SOURCE,
    confront_completed_moments,
    mature_seed_invariants,
)

_THRESHOLDS = {
    "proposal_outcome_weeks": 12.0,
    "invariant_time_validation_score": 0.6,
    "invariant_checkpoint_spacing": 10.0,
    "invariant_checkpoint_last": 320.0,
    "invariant_verdict_confidence": 0.95,
    "invariant_null_score": 0.5,
    "confrontation_margin": 0.1,
    "confrontation_margin_return": 0.02,
}
_HORIZON = pd.Timedelta(weeks=12)
_FIRST_FRIDAY = pd.Timestamp("2024-01-05")
_WEEKS_AT_BIRTH = 78
_TODAY = date(2026, 1, 4)

# An unconditional claim, so its null is 0.0 and the fixture needs no signal:
# equities beat bonds by 5 points every week, far outside the 2-point margin.
_EFFECT = (
    '{"handle": "asset-class:equities", "metric": "return", '
    '"method": "cross_class", "direction": "outperform"}'
)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    database = InvestmentDB(tmp_path / "forward.db")
    for key, value in _THRESHOLDS.items():
        await database.command(
            "INSERT INTO system_thresholds (key, value, updated_at) VALUES (:k, :v, '2026-01-01')",
            k=key,
            v=value,
        )
    await database.command(
        "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
        "weight_initial, floor_weight, weight_effective, trace, created_at, updated_at) "
        "VALUES ('inv-eq', 'equities beat the rest', 'd', 'seed', 'proposed', '[]', :effect, "
        "0.5, 0.05, 0.5, 'tr', '2024-01-01', '2024-01-01')",
        effect=_EFFECT,
    )
    await _add_weeks(database, first_week=0, weeks=_WEEKS_AT_BIRTH)
    try:
        yield database
    finally:
        await database.close()


async def _add_weeks(db: InvestmentDB, *, first_week: int, weeks: int) -> None:
    """Weekly benchmark rows — what `benchmark-valuations` adds each Sunday."""
    for week in range(first_week, first_week + weeks):
        day = (_FIRST_FRIDAY + pd.Timedelta(weeks=week)).date().isoformat()
        for benchmark_id, period_return in (("equities", 0.05), ("bonds", 0.0)):
            await db.command(
                "INSERT INTO benchmark_valuation (id, benchmark_kind, benchmark_id, date, return) "
                "VALUES (:id, 'asset_class', :bid, :date, :ret)",
                id=f"{benchmark_id}-{week}",
                bid=benchmark_id,
                date=day,
                ret=period_return,
            )


async def _rows(db: InvestmentDB, source: str) -> list[dict[str, object]]:
    return await db.query(
        "SELECT signal_date, available_at, verdict FROM invariant_confrontations "
        "WHERE invariant_id = 'inv-eq' AND source = :source ORDER BY signal_date",
        source=source,
    )


async def _data_reaches(db: InvestmentDB) -> str:
    return str((await db.query("SELECT MAX(date) AS d FROM benchmark_valuation"))[0]["d"])


async def test_mechanical_confrontations_do_not_stop_at_birth(db: InvestmentDB) -> None:
    """The moment the birth sweep left open — sampled, its window not yet
    complete — is confronted once the data reaches the end of that window, and
    the invariant's standing counts it."""
    await mature_seed_invariants(db)
    birth = await _rows(db, BIRTH_SOURCE)
    left_open = [r for r in birth if r["verdict"] == invariants.NO_DATA]
    assert left_open, "the fixture must leave a moment whose window is still open"
    counted_at_birth = sum(r["verdict"] == invariants.CONFIRMED for r in birth)

    await _add_weeks(db, first_week=_WEEKS_AT_BIRTH, weeks=13)
    result = await confront_completed_moments(db, _TODAY)

    forward = await _rows(db, FORWARD_SOURCE)
    assert result.confirmed == len(forward) >= 1
    assert left_open[-1]["signal_date"] in {r["signal_date"] for r in forward}
    standing = (await db.query("SELECT confirmation_count FROM invariant WHERE id = 'inv-eq'"))[0]
    assert standing["confirmation_count"] == counted_at_birth + len(forward)
    events = await db.query("SELECT payload FROM event_log WHERE type = :t", t=CONFRONTATION_EVENT)
    assert len(events) == 1 and '"source": "forward"' in str(events[0]["payload"])


async def test_a_second_run_writes_nothing(db: InvestmentDB) -> None:
    await mature_seed_invariants(db)
    await _add_weeks(db, first_week=_WEEKS_AT_BIRTH, weeks=13)
    await confront_completed_moments(db, _TODAY)
    after_first = await _rows(db, FORWARD_SOURCE)

    again = await confront_completed_moments(db, _TODAY)

    assert await _rows(db, FORWARD_SOURCE) == after_first
    assert again.invariants_confronted == 0


async def test_a_moment_is_not_confronted_before_its_window_completes(db: InvestmentDB) -> None:
    """Nothing is written for a window still open — not even a placeholder —
    and the sweep says how many moments it left waiting."""
    await mature_seed_invariants(db)

    with_no_new_data = await confront_completed_moments(db, _TODAY)
    assert with_no_new_data.waiting >= 1
    assert await _rows(db, FORWARD_SOURCE) == []

    await _add_weeks(db, first_week=_WEEKS_AT_BIRTH, weeks=13)
    await confront_completed_moments(db, _TODAY)
    reaches = await _data_reaches(db)
    assert all(str(r["available_at"]) <= reaches for r in await _rows(db, FORWARD_SOURCE))


async def test_catching_up_writes_what_the_weekly_runs_would_have(tmp_path: Path) -> None:
    """A laptop asleep for three months runs one sweep where twelve were due.
    The moments are spaced, dated and judged the same."""

    async def swept(name: str, steps: list[int]) -> list[dict[str, object]]:
        database = InvestmentDB(tmp_path / name)
        try:
            for key, value in _THRESHOLDS.items():
                await database.command(
                    "INSERT INTO system_thresholds (key, value, updated_at) "
                    "VALUES (:k, :v, '2026-01-01')",
                    k=key,
                    v=value,
                )
            await database.command(
                "INSERT INTO invariant (id, title, description, source, status, condition, "
                "effect, weight_initial, floor_weight, weight_effective, trace, created_at, "
                "updated_at) VALUES ('inv-eq', 't', 'd', 'seed', 'proposed', '[]', :effect, "
                "0.5, 0.05, 0.5, 'tr', '2024-01-01', '2024-01-01')",
                effect=_EFFECT,
            )
            await _add_weeks(database, first_week=0, weeks=_WEEKS_AT_BIRTH)
            await mature_seed_invariants(database)
            week = _WEEKS_AT_BIRTH
            for step in steps:
                await _add_weeks(database, first_week=week, weeks=step)
                week += step
                await confront_completed_moments(database, _TODAY)
            return await _rows(database, FORWARD_SOURCE)
        finally:
            await database.close()

    weekly = await swept("weekly.db", [1] * 30)
    asleep = await swept("asleep.db", [30])
    assert weekly == asleep
    assert len(weekly) >= 2


async def test_a_re_sweep_does_not_count_a_moment_twice(db: InvestmentDB) -> None:
    """A re-sweep on repaired data covers the dates the forward sweep had
    covered. It replaces those rows; it does not add to them."""
    await mature_seed_invariants(db)
    await _add_weeks(db, first_week=_WEEKS_AT_BIRTH, weeks=13)
    await confront_completed_moments(db, _TODAY)
    assert await _rows(db, FORWARD_SOURCE)

    await mature_seed_invariants(db, remeasure_on_changed_data=True)

    assert await _rows(db, FORWARD_SOURCE) == []
    dates = [r["signal_date"] for r in await _rows(db, BIRTH_SOURCE)]
    assert len(dates) == len(set(dates))
    standing = (await db.query("SELECT confirmation_count FROM invariant WHERE id = 'inv-eq'"))[0]
    counted = sum(r["verdict"] == invariants.CONFIRMED for r in await _rows(db, BIRTH_SOURCE))
    assert standing["confirmation_count"] == counted


async def test_an_invariant_not_yet_swept_at_birth_is_left_to_the_birth_sweep(
    db: InvestmentDB,
) -> None:
    """Otherwise the forward sweep would replay 35 years as 'forward' evidence,
    judged against a baseline the birth sweep does not use."""
    result = await confront_completed_moments(db, _TODAY)
    assert result.invariants_swept == 0
    assert await _rows(db, FORWARD_SOURCE) == []


def test_a_forward_moment_is_judged_on_the_baseline_known_when_it_completed() -> None:
    """Owner decision D1: the birth sweep may look back with everything known
    today, a forward moment may not look ahead. What happens after its window
    closes does not move its null."""
    condition = [{"signal": "inflation", "feature": "level", "op": ">", "value": 3}]
    effect = {"metric": "return", "method": "cross_class"}
    idx = pd.date_range(_FIRST_FRIDAY, periods=60, freq="W-FRI")
    own = pd.DataFrame({"return": [0.01] * 20 + [0.50] * 40}, index=idx)
    others = {"bonds": pd.DataFrame({"return": [0.0] * 60}, index=idx)}
    knowable = idx[19]

    then = invariants._baseline_knowable_at(knowable, own, others, effect, condition, _HORIZON)
    assert then == pytest.approx(0.01)
    whole_sample = invariants.baseline_excess(
        invariants._all_excess(own, others, "return", "cross_class", _HORIZON), condition
    )
    assert whole_sample > then


async def test_the_summary_describes_the_record_the_weight_is_computed_from(
    db: InvestmentDB,
) -> None:
    """Birth and forward moments of the definition in force, with their lift —
    and nothing else: a Worker reading is not evidence, and a moment earned by
    another definition is not this claim's."""
    await mature_seed_invariants(db)
    await _add_weeks(db, first_week=_WEEKS_AT_BIRTH, weeks=13)
    await confront_completed_moments(db, _TODAY)
    for row_id, source, definition in (
        ("a-reading", "evaluation", None),
        ("another-claim", BIRTH_SOURCE, "000000000000"),
    ):
        await db.command(
            "INSERT INTO invariant_confrontations (id, invariant_id, moment_context, "
            "signal_date, available_at, verdict, source, definition) VALUES (:id, 'inv-eq', "
            "'x', '2020-01-01', '2020-01-01', 'refuted', :source, "
            "COALESCE(:definition, (SELECT definition FROM invariant_confrontations LIMIT 1)))",
            id=row_id,
            source=source,
            definition=definition,
        )

    summary = (await invariants.evidence_summaries(db, ["inv-eq"]))["inv-eq"]

    standing = (await db.query("SELECT confirmation_count, infirmation_count FROM invariant"))[0]
    assert summary.confirmed == standing["confirmation_count"]
    assert summary.refuted == standing["infirmation_count"] == 0
    # equities return 0.05, bonds 0.0, and an unconditional claim's null is 0.0
    assert summary.mean_lift == pytest.approx(0.05)
    assert summary.worst_lift == pytest.approx(0.05)
