"""Writeback knowledge commit (docs/TASKS.md Phase 6;
src/investment/writeback/writeback.py commit_knowledge). source='evaluation'
confrontations move weights through the shared primitive, with the
condition-active gate; evaluations nudge conviction. Against a real throwaway
SQLite."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.planner.post import Confrontation, PostPlannerResult
from investment.worker.result import EvaluationDraft
from investment.writeback.writeback import commit_knowledge

THRESHOLDS = {"recency_half_life_days": 365.0}


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
        "conditions, source, status, trace, created_at, updated_at) VALUES ('s1', 't', 'd', "
        "'4s', 60, 1, 'c', 'corpus', 'active', 'tr', '2026-01-01', '2026-01-01')"
    )
    # inv-active: always-active (empty condition); inv-dormant: condition can't fire
    for iid, cond in (
        ("inv-active", "[]"),
        ("inv-dormant", '[{"signal": "inflation", "feature": "level", "op": ">", "value": 99}]'),
    ):
        await cmd(
            "INSERT INTO invariant (id, title, description, source, status, condition, "
            "weight_initial, floor_weight, weight_effective, confirmation_count, "
            "infirmation_count, market_score, trace, created_at, updated_at) VALUES (:id, 't', "
            "'d', 's', 'integrated', :c, 0.6, 0.2, 0.6, 4, 1, 0.8, 'tr', '2026-01-01', "
            "'2026-01-01')",
            id=iid,
            c=cond,
        )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "k.db")
    await _seed(conn)
    yield conn
    await conn.close()


async def test_confrontation_moves_weight_and_logs_source_evaluation(db: InvestmentDB) -> None:
    before = (await db.query("SELECT confirmation_count, market_score FROM invariant "
                             "WHERE id='inv-active'"))[0]
    result = PostPlannerResult(
        confrontations=[Confrontation(invariant_id="inv-active", verdict="confirmed")]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.confrontations == 1

    after = (await db.query("SELECT confirmation_count, infirmation_count, market_score "
                            "FROM invariant WHERE id='inv-active'"))[0]
    assert after["confirmation_count"] == before["confirmation_count"] + 1  # 4 -> 5
    assert after["market_score"] == pytest.approx(5 / 6)  # 5 confirmed of 6

    conf = await db.query(
        "SELECT source, verdict FROM invariant_confrontations WHERE invariant_id='inv-active'"
    )
    assert conf[0]["source"] == "evaluation" and conf[0]["verdict"] == "confirmed"
    ev = await db.query("SELECT type FROM event_log WHERE type='ConfrontationEvent'")
    assert len(ev) == 1  # EventLog-first


async def test_dormant_invariant_is_not_confronted(db: InvestmentDB) -> None:
    before = (await db.query("SELECT confirmation_count FROM invariant "
                             "WHERE id='inv-dormant'"))[0]["confirmation_count"]
    result = PostPlannerResult(
        confrontations=[Confrontation(invariant_id="inv-dormant", verdict="confirmed")]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.confrontations == 0  # condition can't fire -> not confronted
    after = (await db.query("SELECT confirmation_count FROM invariant "
                            "WHERE id='inv-dormant'"))[0]["confirmation_count"]
    assert after == before  # untouched


async def test_evaluation_nudges_conviction(db: InvestmentDB) -> None:
    result = PostPlannerResult(
        evaluations=[
            EvaluationDraft(strategy_id="s1", verdict="confirms", conviction_delta=8.0,
                            events=["stag"], reasoning="r"),
        ]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.conviction_updates == 1
    conviction = (await db.query("SELECT conviction FROM strategy WHERE id='s1'"))[0]["conviction"]
    assert conviction == pytest.approx(68.0)  # 60 + 8
    assert len(await db.query("SELECT type FROM event_log WHERE type='EvaluationEvent'")) == 1


async def test_empty_result_is_a_clean_no_op(db: InvestmentDB) -> None:
    summary = await commit_knowledge(db, PostPlannerResult(), "stag", THRESHOLDS)
    assert summary.confrontations == 0
    assert summary.conviction_updates == 0
    assert await db.query("SELECT id FROM event_log") == []
