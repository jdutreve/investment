"""Re-measurement after a data repair (`invariants.MeasuredData`, seed step 11b).

The birth sweep's fingerprint knows what was asked, not what it was measured
on: until 2026-11-14 a seed that repaired a series left every record measured
on the old values (142 of 272 after the 2026-10-17 year-on-year repair). The
seed now photographs what a sweep reads, compares after it rewrote the data,
and re-measures the invariants that read a revised value — and only those.
Real throwaway SQLite, the forward-sweep fixture's shape."""

from collections.abc import AsyncIterator
from pathlib import Path

import pandas as pd
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import invariants
from investment.mechanical.invariants import (
    BIRTH_SOURCE,
    ChangedInputs,
    changed_inputs,
    invariants_reading,
    mature_seed_invariants,
    read_measured_data,
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
_FIRST_FRIDAY = pd.Timestamp("2024-01-05")
_WEEKS = 78
_CROSS_CLASS = (
    '{"handle": "asset-class:equities", "metric": "return", '
    '"method": "cross_class", "direction": "outperform"}'
)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    database = InvestmentDB(tmp_path / "repair.db")
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
        effect=_CROSS_CLASS,
    )
    await _add_weeks(database, first_week=0, weeks=_WEEKS)
    try:
        yield database
    finally:
        await database.close()


async def _add_weeks(db: InvestmentDB, *, first_week: int, weeks: int) -> None:
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


async def _revise_bonds(db: InvestmentDB, period_return: float) -> None:
    """A repair: every existing bonds row rewritten, as a re-fetched history is."""
    await db.command(
        "UPDATE benchmark_valuation SET return = :r WHERE benchmark_id = 'bonds'", r=period_return
    )


async def _confirmed(db: InvestmentDB) -> int:
    rows = await db.query(
        "SELECT COUNT(*) AS n FROM invariant_confrontations WHERE invariant_id = 'inv-eq' "
        "AND source = :birth AND verdict = :confirmed",
        birth=BIRTH_SOURCE,
        confirmed=invariants.CONFIRMED,
    )
    return int(rows[0]["n"])


def _row(invariant_id: str, condition: str, effect: str) -> dict[str, object]:
    return {"id": invariant_id, "status": "proposed", "condition": condition, "effect": effect}


async def test_appended_rows_are_new_data_not_a_revision(db: InvestmentDB) -> None:
    """The weekly forward sweep is for new data; a seed that only extends the
    series must re-measure nothing."""
    before = await read_measured_data(db)
    await _add_weeks(db, first_week=_WEEKS, weeks=13)

    assert not changed_inputs(before, await read_measured_data(db))


async def test_a_revised_value_is_found_and_names_who_reads_it(db: InvestmentDB) -> None:
    before = await read_measured_data(db)
    await _revise_bonds(db, 0.10)

    changed = changed_inputs(before, await read_measured_data(db))

    assert changed.benchmarks == {("asset_class", "bonds")}
    rows = await db.query("SELECT * FROM invariant")
    assert invariants_reading(rows, changed) == {"inv-eq"}  # compared against bonds


async def test_a_repair_is_re_measured_and_the_rest_is_left_alone(db: InvestmentDB) -> None:
    """THE PROPERTY. Bonds rewritten from 0% to +10% a period: equities no
    longer beat the rest. Without the re-measurement the record keeps its old
    confirmations (what happened for four weeks); with it, it follows the data."""
    await mature_seed_invariants(db)
    confirmed_on_old_data = await _confirmed(db)
    assert confirmed_on_old_data > 0

    before = await read_measured_data(db)
    await _revise_bonds(db, 0.10)
    await mature_seed_invariants(db)
    assert await _confirmed(db) == confirmed_on_old_data  # the fingerprint alone: stale

    changed = changed_inputs(before, await read_measured_data(db))
    remeasure = invariants_reading(await db.query("SELECT * FROM invariant"), changed)
    await mature_seed_invariants(db, remeasure=remeasure)
    assert await _confirmed(db) == 0


def test_float_noise_of_a_re_derived_history_is_not_a_revision() -> None:
    idx = pd.DatetimeIndex(["2024-01-05", "2024-01-12"])
    frame = pd.DataFrame({"level": [1.0, 2.0], "speed": [0.1, 0.2]}, index=idx)
    noisy = frame * (1.0 + 1e-13)
    data = invariants.MeasuredData({"^IRX": frame}, {}, [])
    again = invariants.MeasuredData({"^IRX": noisy}, {}, [])

    assert not changed_inputs(data, again)


def test_a_condition_signal_selects_its_readers_and_an_absolute_claim_ignores_others() -> None:
    reads_irx = _row(
        "inv-irx",
        '[{"signal": "irx", "feature": "speed", "op": "<", "value": 0}]',
        '{"handle": "asset-class:equities", "metric": "return", "method": "absolute", '
        '"direction": "outperform"}',
    )
    absolute_gold = _row(
        "inv-gold",
        "[]",
        '{"handle": "asset-class:gold-commodities", "metric": "return", "method": "absolute", '
        '"direction": "outperform"}',
    )
    in_regime = _row(
        "inv-regime",
        '[{"signal": "regime", "feature": "type", "op": "==", "value": "stagflation"}]',
        '{"handle": "asset-class:gold-commodities", "metric": "return", "method": "absolute", '
        '"direction": "outperform"}',
    )
    rows = [reads_irx, absolute_gold, in_regime]

    assert invariants_reading(rows, ChangedInputs(frozenset({"^IRX"}), frozenset(), False)) == {
        "inv-irx"
    }
    # an absolute claim reads its own series only: another class's repair is not its business
    bonds = ChangedInputs(frozenset(), frozenset({("asset_class", "bonds")}), False)
    assert invariants_reading(rows, bonds) == set()
    assert invariants_reading(rows, ChangedInputs(frozenset(), frozenset(), True)) == {"inv-regime"}
