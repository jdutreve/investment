"""Writeback knowledge commit (docs/TASKS.md Phase 6;
src/investment/writeback/writeback.py commit_knowledge). source='evaluation'
confrontations move weights through the shared primitive, with the
condition-active gate; evaluations nudge conviction. Against a real throwaway
SQLite."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.planner.post import Confrontation, PostPlannerResult
from investment.worker.result import EvaluationDraft, ImprovementProposal, ScenarioAdjustment
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
    before = (
        await db.query(
            "SELECT confirmation_count, market_score FROM invariant WHERE id='inv-active'"
        )
    )[0]
    result = PostPlannerResult(
        confrontations=[Confrontation(invariant_id="inv-active", verdict="confirmed")]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.confrontations == 1

    after = (
        await db.query(
            "SELECT confirmation_count, infirmation_count, market_score "
            "FROM invariant WHERE id='inv-active'"
        )
    )[0]
    assert after["confirmation_count"] == before["confirmation_count"] + 1  # 4 -> 5
    assert after["market_score"] == pytest.approx(5 / 6)  # 5 confirmed of 6

    conf = await db.query(
        "SELECT source, verdict FROM invariant_confrontations WHERE invariant_id='inv-active'"
    )
    assert conf[0]["source"] == "evaluation" and conf[0]["verdict"] == "confirmed"
    ev = await db.query("SELECT type FROM event_log WHERE type='ConfrontationEvent'")
    assert len(ev) == 1  # EventLog-first


async def test_dormant_invariant_is_not_confronted(db: InvestmentDB) -> None:
    before = (await db.query("SELECT confirmation_count FROM invariant WHERE id='inv-dormant'"))[0][
        "confirmation_count"
    ]
    result = PostPlannerResult(
        confrontations=[Confrontation(invariant_id="inv-dormant", verdict="confirmed")]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.confrontations == 0  # condition can't fire -> not confronted
    after = (await db.query("SELECT confirmation_count FROM invariant WHERE id='inv-dormant'"))[0][
        "confirmation_count"
    ]
    assert after == before  # untouched


async def test_evaluation_nudges_conviction(db: InvestmentDB) -> None:
    result = PostPlannerResult(
        evaluations=[
            EvaluationDraft(
                strategy_id="s1",
                verdict="confirms",
                conviction_delta=8.0,
                events=["stag"],
                reasoning="r",
            ),
        ]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.conviction_updates == 1
    conviction = (await db.query("SELECT conviction FROM strategy WHERE id='s1'"))[0]["conviction"]
    assert conviction == pytest.approx(68.0)  # 60 + 8
    assert len(await db.query("SELECT type FROM event_log WHERE type='EvaluationEvent'")) == 1


async def test_evaluation_is_persisted_as_a_vertex_not_just_a_conviction_nudge(
    db: InvestmentDB,
) -> None:
    """docs/DATA_MODELS.md "Persistence Routing": `Evaluation -> EventLog ->
    vertex (events[] filled) -> UPDATES`. Without the row the observed events,
    the reasoning and the delta that moved conviction were all discarded on
    application — `conviction` is one float the next evaluation overwrites."""
    result = PostPlannerResult(
        evaluations=[
            EvaluationDraft(
                strategy_id="s1",
                verdict="weakens",
                conviction_delta=-5.0,
                events=["CPI level 3.1 (speed +0.30)"],
                reasoning="the inflation leg of the thesis is not holding",
            ),
        ]
    )
    await commit_knowledge(db, result, "stag", THRESHOLDS)
    row = (await db.query("SELECT * FROM evaluation"))[0]
    assert row["strategy_id"] == "s1"  # UPDATES is the FK on the child
    assert row["verdict"] == "weakens"
    assert row["conviction_delta"] == pytest.approx(-5.0)
    assert json.loads(str(row["events"])) == ["CPI level 3.1 (speed +0.30)"]
    assert "inflation leg" in str(row["reasoning"])


async def test_a_neutral_evaluation_is_recorded_though_it_moves_no_conviction(
    db: InvestmentDB,
) -> None:
    """Evidence weighed and found not to move anything is still evidence — the
    case where the reasoning IS the whole of the value. The returned count stays
    the conviction updates, which is what the digest reports."""
    result = PostPlannerResult(
        evaluations=[
            EvaluationDraft(
                strategy_id="s1", verdict="neutral", conviction_delta=0.0, events=[], reasoning="r"
            ),
        ]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.conviction_updates == 0
    assert len(await db.query("SELECT id FROM evaluation")) == 1


async def test_an_evaluation_of_an_unknown_strategy_does_not_abort_the_commit(
    db: InvestmentDB,
) -> None:
    """`evaluation.strategy_id` REFERENCES strategy(id) with foreign_keys=ON, so
    one hallucinated id would take the confrontations, the scenarios and the
    innovations down with it."""
    result = PostPlannerResult(
        evaluations=[
            EvaluationDraft(
                strategy_id="s-does-not-exist",
                verdict="confirms",
                conviction_delta=9.0,
                events=[],
                reasoning="r",
            ),
            EvaluationDraft(
                strategy_id="s1", verdict="confirms", conviction_delta=4.0, events=[], reasoning="r"
            ),
        ]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.conviction_updates == 1  # only the real one
    rows = await db.query("SELECT strategy_id FROM evaluation")
    assert [r["strategy_id"] for r in rows] == ["s1"]


def _scen(strategy: str, kind: str, prob: float) -> ScenarioAdjustment:
    return ScenarioAdjustment(strategy_id=strategy, scenario=kind, probability=prob, rationale="r")


async def test_scenario_update_commits_a_coherent_triple(db: InvestmentDB) -> None:
    # the strategy's three scenarios (name -> id), keyed by id in scenario_probability
    for sid, name in (("sc-s1-bull", "bull"), ("sc-s1-base", "base"), ("sc-s1-bear", "bear")):
        await db.command(
            "INSERT INTO scenario (id, strategy_id, name, probability, triggers, "
            "target_allocation, currency, trace, updated_at) VALUES (:id, 's1', :n, 33.0, '[]', "
            "'{}', 'USD', 't', '2026-01-01')",
            id=sid,
            n=name,
        )
    result = PostPlannerResult(
        scenario_updates=[
            _scen("s1", "bull", 55.0),
            _scen("s1", "base", 30.0),
            _scen("s1", "bear", 15.0),
        ]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.scenario_updates == 3  # all three written
    probs = await db.query(
        "SELECT scenario, probability FROM scenario_probability WHERE strategy_id='s1' "
        "ORDER BY scenario"
    )
    assert {p["scenario"]: p["probability"] for p in probs} == {
        "sc-s1-base": 30.0,
        "sc-s1-bear": 15.0,
        "sc-s1-bull": 55.0,
    }
    assert len(await db.query("SELECT type FROM event_log WHERE type='ScenarioEvent'")) == 1


async def test_incoherent_scenario_update_is_skipped(db: InvestmentDB) -> None:
    for sid, name in (("sc-s1-bull", "bull"), ("sc-s1-base", "base"), ("sc-s1-bear", "bear")):
        await db.command(
            "INSERT INTO scenario (id, strategy_id, name, probability, triggers, "
            "target_allocation, currency, trace, updated_at) VALUES (:id, 's1', :n, 33.0, '[]', "
            "'{}', 'USD', 't', '2026-01-01')",
            id=sid,
            n=name,
        )
    # only two scenarios, and they don't sum to 100 -> the whole strategy is skipped
    result = PostPlannerResult(
        scenario_updates=[_scen("s1", "bull", 55.0), _scen("s1", "base", 30.0)]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.scenario_updates == 0
    assert await db.query("SELECT scenario FROM scenario_probability") == []


async def test_new_strategy_innovation_is_born_proposed_and_disabled(db: InvestmentDB) -> None:
    result = PostPlannerResult(
        innovations=[
            ImprovementProposal(
                type="new_strategy",
                title="Counter-cyclical credit tilt",
                rationale="tilt into credit when spreads gap",
                spec={
                    "id": "strat-cc",
                    "framework_id": "4s",
                    "conviction": 55,
                    "conditions": "credit_spread > 2",
                },
                trace="agent-discovery",
            )
        ]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.innovations == 1
    row = (await db.query("SELECT status, enabled, source FROM strategy WHERE id='strat-cc'"))[0]
    assert row["status"] == "proposed"
    assert row["enabled"] == 0  # disabled until probation passes (ADR-006)
    assert row["source"] == "agent-discovery"  # -> will enter strategy_probation_check
    ev = await db.query("SELECT source_id FROM event_log WHERE type='InnovationEvent'")
    assert ev[0]["source_id"] == "strat-cc"


async def test_strategy_innovation_survives_an_invented_fk(db: InvestmentDB) -> None:
    """`INSERT OR IGNORE` does NOT absorb foreign-key violations in SQLite, so an
    id the model invented would raise IntegrityError and abort the Monday chain.
    The unresolvable reference is dropped; the innovation still lands."""
    result = PostPlannerResult(
        innovations=[
            ImprovementProposal(
                type="new_strategy",
                title="Ghost-anchored tilt",
                rationale="r",
                spec={
                    "id": "strat-ghost",
                    "regime_type_id": "no-such-regime",  # invented FK
                    "framework_id": "no-such-framework",  # invented FK
                    "conviction": "quite high",  # prose where a number belongs
                },
                trace="agent-discovery",
            )
        ]
    )
    summary = await commit_knowledge(db, result, "stag", THRESHOLDS)
    assert summary.innovations == 1
    row = (
        await db.query(
            "SELECT regime_type_id, framework_id, conviction, status FROM strategy "
            "WHERE id='strat-ghost'"
        )
    )[0]
    assert row["regime_type_id"] is None  # dropped, not fabricated
    # framework_id is NOT NULL, so it degrades to an EXISTING framework (here the
    # fixture's only one) rather than to the unseeded '4seasons' default
    assert row["framework_id"] == "4s"
    assert row["conviction"] == 50.0
    assert row["status"] == "proposed"


async def test_strategy_innovation_records_its_spec_for_activation(db: InvestmentDB) -> None:
    """The InnovationEvent must carry the spec: probation activation builds the 3
    Scenario vertices and the BACKED_BY edges from it, and a proposed strategy row
    has nowhere to keep them meanwhile."""
    spec = {
        "id": "strat-spec",
        "framework_id": "4s",
        "scenarios": [{"name": "bull", "probability": 30, "target_allocation": {"SPY": 100}}],
        "cites": ["inv-active"],
    }
    result = PostPlannerResult(
        innovations=[
            ImprovementProposal(
                type="new_strategy", title="t", rationale="r", spec=spec, trace="agent-discovery"
            )
        ]
    )
    await commit_knowledge(db, result, "stag", THRESHOLDS)
    payload = (
        await db.query(
            "SELECT json_extract(payload, '$.spec.cites[0]') AS cite FROM event_log "
            "WHERE type='InnovationEvent'"
        )
    )[0]
    assert payload["cite"] == "inv-active"


async def test_empty_result_is_a_clean_no_op(db: InvestmentDB) -> None:
    summary = await commit_knowledge(db, PostPlannerResult(), "stag", THRESHOLDS)
    assert summary.confrontations == 0
    assert summary.conviction_updates == 0
    assert await db.query("SELECT id FROM event_log") == []
