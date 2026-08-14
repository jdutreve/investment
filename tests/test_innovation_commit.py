"""new_invariant innovation commit through the SHARED dedup gate (docs/TASKS.md
Phase 6; src/investment/writeback/writeback.py commit_innovations). A stub
embedder stands in for the model (find_duplicate works on vectors); the
structural-identity dedup needs no cosine, so it is deterministic."""

import dataclasses
import json
import logging
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from investment.corpus.embedding import to_blob
from investment.db.seed_data import INVARIANT_AUTHOR_CONFIG
from investment.db.sqlite import InvestmentDB
from investment.mechanical import rule_revision
from investment.mechanical.invariants import REFERENCE_STATUS, mature_seed_invariants
from investment.mechanical.replay import NavMetrics
from investment.planner.post import PostPlannerResult
from investment.worker.result import ImprovementProposal
from investment.writeback import writeback
from investment.writeback.writeback import commit_innovations

# the invariant-maturation thresholds mature_seed_invariants reads
_MATURATION_THRESHOLDS = {
    "proposal_outcome_weeks": 12.0,
    "recency_half_life_days": 365.0,
    "invariant_min_confrontations": 3.0,
    "invariant_time_validation_score": 0.6,
    "invariant_refuted_min_confrontations": 4.0,
    "invariant_refuted_score": 0.35,
    "invariant_verdict_confidence": 0.95,
    "invariant_null_score": 0.5,
    "confrontation_margin": 0.1,
    "confrontation_margin_return": 0.02,
}

_CONDITION = [{"signal": "real_yield", "feature": "level", "op": "<", "value": 0.0}]
_EFFECT = {
    "handle": "asset-class:gold-commodities",
    "metric": "return",
    "method": "cross_class",
    "direction": "outperform",
}


class _StubEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)


def _innovation(title: str) -> ImprovementProposal:
    return ImprovementProposal(
        type="new_invariant",
        title=title,
        rationale="gold outperforms when real yields are negative",
        spec={"id": "inv-new", "condition": _CONDITION, "effect": _EFFECT, "tags": ["gold"]},
        weight_initial=0.5,
        floor_weight=0.2,
        trace="UC8 agent-discovery",
    )


async def _seed_thresholds(db: InvestmentDB) -> None:
    for key, value in _MATURATION_THRESHOLDS.items():
        await db.command(
            "INSERT INTO system_thresholds (key, value, updated_at) VALUES (:k, :v, '2026-01-01')",
            k=key,
            v=value,
        )


async def _seed_author_bands(db: InvestmentDB) -> None:
    """The SEEDED tier bands, imported rather than retyped: the assertions below
    pin exact numbers (0.05 floor, 0.15-0.25 initial on the 'system' tier that
    `ImprovementProposal.author` defaults to), so a local copy would keep
    testing the old bands, greenly, after a re-tune of the real ones."""
    for row in INVARIANT_AUTHOR_CONFIG:
        await db.command(
            "INSERT INTO invariant_author_config "
            "(author, floor_weight, initial_weight_min, initial_weight_max) "
            "VALUES (:author, :floor_weight, :initial_weight_min, :initial_weight_max)",
            **row,
        )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "inv.db")
    await _seed_thresholds(conn)
    await _seed_author_bands(conn)
    yield conn
    await conn.close()


async def test_new_invariant_is_created_proposed_and_agent_discovery(db: InvestmentDB) -> None:
    result = PostPlannerResult(
        innovations=[_innovation("Gold beats when real yields are negative")]
    )
    n = await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    assert n == 1
    row = (await db.query("SELECT source, status FROM invariant WHERE id='inv-new'"))[0]
    assert row["source"] == "agent-discovery"
    assert row["status"] == "proposed"  # earns its verdict from the 35y sweep (ADR-006)
    ev = await db.query("SELECT source_id FROM event_log WHERE type='InnovationEvent'")
    assert ev[0]["source_id"] == "inv-new"


async def test_a_structural_duplicate_is_merged_not_recreated(db: InvestmentDB) -> None:
    # an existing invariant with the SAME condition+effect and an embedding
    await db.command(
        "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
        "weight_initial, floor_weight, weight_effective, embedding, trace, created_at, updated_at) "
        "VALUES ('inv-existing', 't', 'd', 'curator', 'integrated', :cond, :eff, 0.5, 0.2, 0.6, "
        ":emb, 'tr', '2026-01-01', '2026-01-01')",
        cond=json.dumps(_CONDITION),
        eff=json.dumps(_EFFECT),
        emb=to_blob(np.ones(4, dtype=np.float32)),
    )
    result = PostPlannerResult(innovations=[_innovation("Same claim, different words")])
    n = await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    assert n == 0  # merged into the incumbent, not recreated
    assert await db.query("SELECT id FROM invariant WHERE id='inv-new'") == []  # never created
    ev = await db.query(
        "SELECT json_extract(payload, '$.merged_into') AS m FROM event_log "
        "WHERE type='InnovationEvent'"
    )
    assert ev[0]["m"] == "inv-existing"


async def test_the_author_tier_band_binds_the_worker_proposed_weights(db: InvestmentDB) -> None:
    """CLAUDE.md "Invariant weight model": the floor is the TIER's, not the
    LLM's. `_innovation` proposes 0.5/0.2 on the 'system' tier, whose band is
    0.05 floor and 0.15-0.25 initial."""
    result = PostPlannerResult(innovations=[_innovation("Gold beats on negative real yields")])
    await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    row = (await db.query("SELECT weight_initial, floor_weight FROM invariant WHERE id='inv-new'"))[
        0
    ]
    assert row["floor_weight"] == 0.05  # the tier's, not the proposed 0.2
    assert row["weight_initial"] == 0.25  # 0.5 clamped down to the band's max


async def test_a_zero_weight_proposal_is_lifted_to_the_band_not_born_inert(
    db: InvestmentDB,
) -> None:
    """`ImprovementProposal` defaults both weights to 0.0 (worker/result.py), and
    `weight_effective = max(0 x score x recency, 0)` is zero forever — an
    invariant that could never influence anything however often confirmed."""
    innovation = _innovation("Gold beats on negative real yields")
    result = PostPlannerResult(
        innovations=[innovation.model_copy(update={"weight_initial": 0.0, "floor_weight": 0.0})]
    )
    await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    row = (
        await db.query(
            "SELECT weight_initial, floor_weight, weight_effective "
            "FROM invariant WHERE id='inv-new'"
        )
    )[0]
    assert row["weight_initial"] == 0.15  # lifted to the band's min
    assert row["floor_weight"] == 0.05
    assert float(row["weight_effective"]) > 0.0


async def test_two_near_identical_innovations_in_ONE_batch_dedup_against_each_other(
    db: InvestmentDB,
) -> None:
    """The corpus is read once, before the loop: without growing it in flight,
    the second member of a batch deduplicates against a snapshot that predates
    the first and both are created."""
    first = _innovation("Gold beats when real yields are negative")
    second = _innovation("Negative real yields favour gold")
    # distinct ids, so a failure creates TWO rows rather than colliding on the PK
    result = PostPlannerResult(
        innovations=[
            first,
            second.model_copy(update={"spec": {**second.spec, "id": "inv-new-2"}}),
        ]
    )
    n = await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())
    assert n == 1  # the second merged into the first
    assert len(await db.query("SELECT id FROM invariant")) == 1
    merged = await db.query(
        "SELECT json_extract(payload, '$.merged_into') AS m FROM event_log "
        "WHERE type='InnovationEvent' AND json_extract(payload, '$.merged_into') IS NOT NULL"
    )
    assert [r["m"] for r in merged] == ["inv-new"]


async def test_reference_knowledge_gets_its_own_terminal_status(db: InvestmentDB) -> None:
    """209 of the live corpus's 238 'proposed' invariants were reference notes —
    no condition, no effect, so no confrontation can ever move them. Filing them
    as 'proposed' made ADR-006's "nothing stays proposed forever" an open promise
    the system could not keep for 88% of the rows carrying that status."""
    await db.command(
        "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
        "weight_initial, floor_weight, weight_effective, trace, created_at, updated_at) "
        "VALUES ('inv-ref', 'a fact', 'd', 'curator', 'proposed', '[]', NULL, 0.4, 0.2, "
        "0.4, 'tr', '2026-01-01', '2026-01-01')"
    )
    await mature_seed_invariants(db)
    row = (await db.query("SELECT status FROM invariant WHERE id = 'inv-ref'"))[0]
    assert row["status"] == REFERENCE_STATUS


async def test_the_backfill_is_the_maturation_sweep_itself(db: InvestmentDB) -> None:
    """No migration script: the reference branch runs BEFORE the
    already-matured fingerprint check, so every existing note is re-stamped on
    the next sweep. The 209 live rows convert themselves."""
    for n in range(3):
        await db.command(
            "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
            "weight_initial, floor_weight, weight_effective, trace, created_at, updated_at) "
            "VALUES (:id, 'a fact', 'd', 'curator', 'proposed', '[]', NULL, 0.4, 0.2, 0.4, "
            "'tr', '2026-01-01', '2026-01-01')",
            id=f"inv-old-{n}",
        )
    await mature_seed_invariants(db)
    await mature_seed_invariants(db)  # idempotent
    rows = await db.query("SELECT status FROM invariant WHERE id LIKE 'inv-old-%'")
    assert [r["status"] for r in rows] == [REFERENCE_STATUS] * 3


def _revision(parameters: dict[str, object]) -> ImprovementProposal:
    return ImprovementProposal(
        type="strategy_revision",
        title="Shorten the trend-overlay window",
        rationale="the 200d line lags the turn that the credit spread already called",
        spec={"id": "strat-rev", "parameters": parameters},
        trace="UC8 agent-discovery",
    )


def _measurement(sortino_delta: float, drawdown_delta: float) -> rule_revision.RevisionMeasurement:
    """A measurement with the two deltas the acceptance test reads. Built from
    NavMetrics rather than stubbed, so `verdict` is computed by the real property."""
    base = NavMetrics(cagr=0.10, sortino=1.0, calmar=0.5, max_drawdown=-0.20)
    return rule_revision.RevisionMeasurement(
        overrides={"ma_windows": [100, 200]},
        baseline=base,
        variant=dataclasses.replace(
            base,
            sortino=1.0 + sortino_delta,
            max_drawdown=-0.20 + drawdown_delta,
        ),
        baseline_turnover=61.1,
        variant_turnover=98.8,
    )


async def _framework(db: InvestmentDB) -> None:
    """`_resolve_framework` skips the innovation entirely when no framework row
    exists — the revision would never be born, and the test would pass on the
    wrong reason."""
    await db.command(
        "INSERT INTO framework (id, name, description, enabled, trace, created_at) "
        "VALUES ('4seasons', 'All Weather', 'd', 1, 't', '2026-01-01')"
    )


async def test_a_measured_bad_rule_revision_is_CLOSED_not_left_to_rot(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this repairs: a rule revision changes CONSTANTS, so
    `_base_allocation` finds no book, no candidate portfolio is born, no NAV
    exists, and probation closes it months later as 'never had evidence' — while
    the evidence was one second of walking away.

    BOTH indicators degrade here. Until 2026-08-13 this test used a revision
    that improved the drawdown and paid for it in Sortino, which is a TRADE-OFF
    and no longer closes anything — see the test below, which took that case."""
    await _framework(db)

    async def _measured(_db: object, _o: object) -> rule_revision.RevisionMeasurement:
        return _measurement(-0.07, -0.025)

    monkeypatch.setattr(writeback, "measure_revision", _measured)
    result = PostPlannerResult(innovations=[_revision({"ma_windows": [100, 200]})])
    assert await commit_innovations(db, result, today=date(2026, 8, 8), embedder=None) == 1

    row = (await db.query("SELECT status, enabled, trace FROM strategy WHERE id='strat-rev'"))[0]
    assert row["status"] == "closed"  # nothing improved and two things got worse
    assert row["enabled"] == 0
    assert "-0.070" in row["trace"] and "-2.50pp" in row["trace"]  # the numbers, not a verdict word
    ev = await db.query("SELECT payload FROM event_log WHERE type='RuleRevisionMeasuredEvent'")
    assert json.loads(ev[0]["payload"])["verdict"] == "reject"


async def test_a_measured_TRADE_OFF_stays_open_and_carries_its_exchange(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE FOURTH VERDICT (owner decision, 2026-08-13).

    -0.07 of Sortino for +2.5pp of drawdown is the `ma_windows` shape —
    the largest safety gain ever measured on this stack, refused by Pareto and,
    until now, CLOSED under the same word as a revision that made everything
    worse. The owner never saw it.

    Three things must hold, and the third is the one that makes the other two
    worth anything: the verdict names the case, the strategy stays OPEN, and the
    exchange travels in the payload so `alerts.rule_tradeoff_alert` can render
    it into the digest without re-running a 35-year walk.

    It still adopts nothing. Rule #1 is intact — the constant is a git edit and
    an owner signature (ADR-006/ADR-007)."""
    await _framework(db)

    async def _measured(_db: object, _o: object) -> rule_revision.RevisionMeasurement:
        return _measurement(-0.07, +0.025)

    monkeypatch.setattr(writeback, "measure_revision", _measured)
    result = PostPlannerResult(innovations=[_revision({"ma_windows": [125, 250]})])
    assert await commit_innovations(db, result, today=date(2026, 8, 8), embedder=None) == 1

    row = (await db.query("SELECT status, trace FROM strategy WHERE id='strat-rev'"))[0]
    assert row["status"] == "proposed"  # NOT closed — the machine declines to decide
    assert "rejected" not in row["trace"]

    payload = json.loads(
        (await db.query("SELECT payload FROM event_log WHERE type='RuleRevisionMeasuredEvent'"))[0][
            "payload"
        ]
    )
    assert payload["verdict"] == "trade-off"
    assert "max_drawdown" in payload["traded"] and "sortino" in payload["traded"]
    assert payload["traded"].index("buys") < payload["traded"].index("costs")


async def test_a_measured_GOOD_revision_stays_proposed_because_the_gate_is_git(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one place ADR-006's no-user-gate does not reach: the knobs are module
    constants in source, so nothing in the writeback can adopt them. A favourable
    measurement is recorded and left standing for the owner."""
    await _framework(db)

    async def _measured(_db: object, _o: object) -> rule_revision.RevisionMeasurement:
        return _measurement(+0.05, +0.03)

    monkeypatch.setattr(writeback, "measure_revision", _measured)
    result = PostPlannerResult(innovations=[_revision({"ma_windows": [100, 200]})])
    await commit_innovations(db, result, today=date(2026, 8, 8), embedder=None)

    row = (await db.query("SELECT status FROM strategy WHERE id='strat-rev'"))[0]
    assert row["status"] == "proposed"
    ev = await db.query("SELECT payload FROM event_log WHERE type='RuleRevisionMeasuredEvent'")
    assert json.loads(ev[0]["payload"])["verdict"] == "adopt"


async def test_a_revision_naming_no_testable_knob_is_not_measured_at_all(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Gate the aggressive book on spread DECELERATION' is a real critique that
    no knob expresses. The Worker left `parameters` empty rather than forcing it
    into a button that does not fit — and an empty spec must not be answered
    with a measurement of nothing."""
    await _framework(db)

    def _fail(_db: object, _o: object) -> None:
        raise AssertionError("measured a revision that named no testable knob")

    monkeypatch.setattr(writeback, "measure_revision", _fail)
    result = PostPlannerResult(innovations=[_revision({"spread_acceleration_gate": True})])
    await commit_innovations(db, result, today=date(2026, 8, 8), embedder=None)

    row = (await db.query("SELECT status FROM strategy WHERE id='strat-rev'"))[0]
    assert row["status"] == "proposed"
    assert not await db.query("SELECT 1 FROM event_log WHERE type='RuleRevisionMeasuredEvent'")


async def test_a_prose_effect_is_demoted_to_reference_knowledge_not_followed_downstream(
    db: InvestmentDB,
) -> None:
    """Measured on the on-stack M8b run, 2008-09-02 and 2008-11-03.

    A Worker wrote `spec["effect"]` as prose. `spec` is `dict[str, Any]`, so
    Pydantic validated the envelope and let it through, and the string was
    followed downstream into FOUR readers that each assume an object: the dedup
    gate's two comparators, `validate_invariant`, and — after the row had
    already been persisted — the 35y maturation sweep reading it back with
    `json.loads`. Each raised out through Writeback and the decision cycle,
    costing the date a whole second attempt.

    Normalising at the WRITE boundary is what makes those four safe at once.
    The claim is not discarded: `effect=None` is reference knowledge, a state
    the system already understands, and the prose survives in the trace."""
    malformed = _innovation("Effect written as prose")
    malformed.spec["effect"] = "gold outperforms when real yields are negative"
    good = _innovation("A well-formed neighbour")
    good.spec = {**good.spec, "id": "inv-good"}

    result = PostPlannerResult(innovations=[malformed, good])
    n = await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())

    assert n == 2  # demoted, not dropped — and the neighbour is unaffected
    row = (await db.query("SELECT effect, trace FROM invariant WHERE id='inv-new'"))[0]
    assert row["effect"] is None  # the column keeps the shape every reader assumes
    assert "effect dropped, not an object" in row["trace"]
    assert "real yields are negative" in row["trace"]  # the claim itself survives
    assert await db.query("SELECT id FROM invariant WHERE id='inv-good'") != []


async def test_one_failing_innovation_does_not_cost_the_others(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The floor under the shape checks, which cannot be exhaustive against a
    generative source. `_measure_rule_revision` had cited
    "`_commit_innovation_safely`'s bargain, one level up" since it shipped, and
    the wrapper did not exist — `commit_innovations` iterated bare, so anything
    unforeseen in one innovation took the cycle's reading, its other
    innovations and its reallocation with it.

    The bad one goes FIRST, which is the ordering a bare loop fails."""
    boom = _innovation("The one that raises")
    good = _innovation("A well-formed neighbour")
    good.spec = {**good.spec, "id": "inv-good"}

    real = writeback._commit_invariant_innovation

    async def _raise_for_the_first(
        db_: object, proposal: object, *args: object, **kwargs: object
    ) -> object:
        if getattr(proposal, "title", "") == "The one that raises":
            raise RuntimeError("something no shape check anticipated")
        return await real(db_, proposal, *args, **kwargs)  # type: ignore[arg-type]  # passthrough

    monkeypatch.setattr(writeback, "_commit_invariant_innovation", _raise_for_the_first)
    result = PostPlannerResult(innovations=[boom, good])
    n = await commit_innovations(db, result, today=date(2026, 7, 20), embedder=_StubEmbedder())

    assert n == 1  # the neighbour survived
    assert await db.query("SELECT id FROM invariant WHERE id='inv-good'") != []


async def test_a_revision_naming_an_unusable_value_is_reported_not_crashed(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Measured twice in two days on the M8b runs: a Worker proposed a haven of
    `dynamic_best_of(GLD,IEF)` (2026-08-08) and then of `SHY` (2026-08-09).

    Both are values, not knobs — the knob name was right. `measure_revision`
    set the module constant and the error surfaced as a `KeyError` deep in a
    pandas price frame, caught one level up and logged as a traceback. It
    degraded cleanly enough, but a revision then carries a stack trace where
    the owner needed a sentence, and the 35y walk is paid for twice before
    anything notices.

    A value the walk cannot apply is now an ANSWER, reported like an unknown
    parameter, and the walk is never started.

    THE EXAMPLE MOVED FROM SHY TO TIP, and the move is the point: SHY was
    refused for being "not a tradable sleeve with a price series", which was
    simply false — it is active with 8755 points back to 1991, and the real
    constraint was that the run loaded prices for the five book sleeves only.
    Fixed 2026-08-11, measured, rejected on its merits. TIP is the honest
    example: its series starts 2003-12-05, so a verdict on it could only ever
    cover two thirds of the sample and would not be comparable to the baseline
    it is judged against (docs/IMPROVEMENTS.md I-48)."""

    def _fail(_db: object, _o: object) -> None:
        raise AssertionError("started a 35y walk on a value it cannot apply")

    monkeypatch.setattr(writeback, "measure_revision", _fail)
    await _framework(db)

    with caplog.at_level(logging.INFO, logger="investment.writeback.writeback"):
        result = PostPlannerResult(innovations=[_revision({"trend_haven": "TIP"})])
        await commit_innovations(db, result, today=date(2026, 8, 9), embedder=None)

    assert any("cannot apply" in r.getMessage() and "TIP" in r.getMessage() for r in caplog.records)
    # `cash` is legal as the FALLBACK haven and nowhere else — the primary is
    # trend-CHECKED, so it needs a price series. Caught by the measurement sweep
    # the check exists to protect, an hour after the check shipped one notch too
    # permissive: `trend_haven: cash` passed and raised KeyError in the frame.
    assert rule_revision.untestable_values({"trend_haven": "cash"})
    assert not rule_revision.untestable_values({"trend_fallback_haven": "cash"})
    # The strategy itself still landed: an unmeasurable revision is still a claim.
    assert (await db.query("SELECT status FROM strategy WHERE id='strat-rev'"))[0]["status"] == (
        "proposed"
    )
