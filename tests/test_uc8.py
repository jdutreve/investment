"""UC8 decision cycle end to end (docs/USE_CASES.md UC8; src/investment/uc8.py).
The full cognitive chain — PlannerPre -> Worker -> PlannerPost -> Writeback —
driven by PydanticAI TestModel on a real throwaway SQLite. Covers M8's Definition
of Verified item: a reallocation the Worker proposes passes the gates and is
persisted; and the knowledge-only path where nothing is proposed."""

from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest
from pydantic_ai.models.test import TestModel

from investment.db.sqlite import InvestmentDB
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.uc8 import UC8Result, render_context_for_worker, run_decision_cycle
from investment.worker.agent import build_worker_agent

USER = {"max_single_asset_pct": 50.0, "max_drawdown_pct": -25.0}
THRESHOLDS = {
    "proposal_sortino_gap_min": 0.02,
    "proposal_calmar_min": 1.5,
    "proposal_min_allocation_change_pts": 5.0,
    "proposal_max_turnover_pct": 30.0,
    "blend_scenario_weight": 0.4,
    "blend_favors_weight": 0.6,
    "proposal_invariant_weight_min": 0.1,
    "invariant_refuted_min_confrontations": 4.0,
    "invariant_refuted_score": 0.35,
}

_REALLOC = {
    "proposed_allocation": {"SPY": 40.0, "GLD": 35.0, "IEF": 25.0},
    "scenario_delta": {},
    "favors_delta": {},
    "blend_note": "0.4 tactical + 0.6 structural",
    "supporting_invariants": ["inv-gold"],
    "reasoning": "gold above its 7y trend and rising; tilt in",
}


def _worker_output(reallocation: dict | None) -> dict:
    return {
        "regime_assessment": "stagflation deepening",
        "ranking_commentary": "defender leads on Sortino",
        "scenario_adjustments": [],
        "evaluations": [],
        "reallocation_proposed": reallocation,
        "innovations_proposed": [],
        "reasoning": "tilt to gold as the storm builds",
    }


class _StubEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 4), dtype=np.float32)


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime_type (id, name, aliases, framework_id, description, created_at) "
        "VALUES ('stag', 'Stagflation', '[]', '4s', 'd', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime (id, regime_type_id, tags, start_date, is_current, events, trace, "
        "created_at, updated_at) VALUES ('r1', 'stag', '[]', '2026-06-01', 1, '[]', 't', "
        "'2026-06-01', '2026-06-01')"
    )
    await cmd(
        "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
        "allocation, rank, sortino_rolling, calmar_rolling, market_context, recommendation, "
        "trace) VALUES ('2026-07-01', 'def-pf', 1, '4s', "
        "'{\"SPY\": 50, \"GLD\": 25, \"IEF\": 25}', 1, 1.2, 1.6, '{}', 'maintain', 't')"
    )
    for tk, cls in (("SPY", "equities"), ("GLD", "gold-commodities"), ("IEF", "bonds")):
        await cmd(
            "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, "
            "active) VALUES (:t, :c, 'USD', 'yahoo', 'none', 1)",
            t=tk,
            c=cls,
        )
    # an integrated, always-active, well-confirmed invariant the Worker can cite
    await cmd(
        "INSERT INTO invariant (id, title, description, source, status, condition, "
        "weight_initial, floor_weight, weight_effective, confirmation_count, infirmation_count, "
        "market_score, trace, created_at, updated_at) VALUES ('inv-gold', 'gold above trend', "
        "'d', 's', 'integrated', '[]', 0.5, 0.2, 0.7, 5, 1, 0.83, 'tr', '2026-01-01', '2026-01-01')"
    )


_Rig = tuple[InvestmentDB, PlannerPre, object, PlannerPost]


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[_Rig]:
    db = InvestmentDB(tmp_path / "uc8.db")
    await _seed(db)
    pre = PlannerPre(db, _StubEmbedder(), "planner/x", "sk-test")
    worker = build_worker_agent(db, "anthropic/x", "sk-test")
    post = PlannerPost("planner/x", "sk-test")
    yield db, pre, worker, post
    await db.close()


def _overrides(pre: PlannerPre, worker: object, post: PlannerPost, worker_out: dict):  # type: ignore[no-untyped-def]
    query = TestModel(custom_output_args={"corpus_queries": [], "zooms": []})
    # select the integrated invariant surfaced by the baseline's global bucket
    select = TestModel(
        custom_output_args={"invariant_ids": ["inv-gold"], "passage_ids": [], "notes": "storm"}
    )
    wk = TestModel(call_tools=[], custom_output_args=worker_out)
    pp = TestModel(
        custom_output_args={
            "evaluations": [],
            "scenario_updates": [],
            "confrontations": [],
            "innovations": [],
            "regime_notes": "coherent",
        }
    )
    return (
        pre.query_agent.override(model=query),
        pre.context_agent.override(model=select),
        worker.override(model=wk),  # type: ignore[attr-defined]
        post.agent.override(model=pp),
    )


async def test_bear_shift_reallocation_passes_gates_and_persists(rig) -> None:  # type: ignore[no-untyped-def]
    db, pre, worker, post = rig
    q, s, w, p = _overrides(pre, worker, post, _worker_output(_REALLOC))
    with q, s, w, p:
        result: UC8Result = await run_decision_cycle(
            db, pre, worker, post, trigger="weekly", user_profile=USER, thresholds=THRESHOLDS
        )
    assert result.gate_outcome is not None and result.gate_outcome.passed is True
    assert result.proposal_id is not None
    # the cited invariant is shown to the Worker as ACTIVE
    assert result.context.top_invariants[0]["active"] is True
    # persisted, EventLog-first
    prop = await db.query("SELECT proposal_type, recommendation FROM proposal WHERE id=:i",
                          i=result.proposal_id)
    assert prop[0]["recommendation"] == "paper-test"
    ev = await db.query("SELECT source_id FROM event_log WHERE type='ProposalEvent'")
    assert [e["source_id"] for e in ev] == [result.proposal_id]


async def test_defender_stricter_single_asset_cap_binds(rig) -> None:  # type: ignore[no-untyped-def]
    """CLAUDE.md "Binding caps": the defender's OWN cap may be stricter than the
    user's, and Writeback enforces the stricter of the two. The snapshot carries
    no cap columns, so uc8 must fetch the `portfolio` row and thread it in — this
    proves that wiring. SPY 45 clears the user's 50 cap but breaches the
    defender's own 40, so the proposal must be blocked, not persisted."""
    db, pre, worker, post = rig
    # the defender as a portfolio with a STRICTER 40 single-asset cap
    await db.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) VALUES "
        "('def-pf', 'D', '4s', 1, 1, 'CHF', 'SPY', '{\"SPY\": 50, \"GLD\": 25, \"IEF\": 25}', "
        "-0.15, 40.0, 'accumulation', 't', '2026-01-01')"
    )
    realloc = dict(_REALLOC, proposed_allocation={"SPY": 45.0, "GLD": 30.0, "IEF": 25.0})
    q, s, w, p = _overrides(pre, worker, post, _worker_output(realloc))
    with q, s, w, p:
        result: UC8Result = await run_decision_cycle(
            db, pre, worker, post, trigger="weekly", user_profile=USER, thresholds=THRESHOLDS
        )
    assert result.gate_outcome is not None
    assert result.gate_outcome.failed_gate == "max_single_asset_pct"
    assert result.proposal_id is None
    assert await db.query("SELECT id FROM proposal") == []  # nothing persisted


async def test_no_reallocation_is_a_knowledge_only_cycle(rig) -> None:  # type: ignore[no-untyped-def]
    db, pre, worker, post = rig
    q, s, w, p = _overrides(pre, worker, post, _worker_output(None))
    with q, s, w, p:
        result = await run_decision_cycle(
            db, pre, worker, post, trigger="weekly", user_profile=USER, thresholds=THRESHOLDS
        )
    assert result.gate_outcome is None
    assert result.proposal_id is None
    assert result.post_result.regime_notes == "coherent"
    assert await db.query("SELECT id FROM proposal") == []  # nothing disposed


def test_render_context_marks_the_defender_and_active_lighthouses() -> None:
    from investment.planner.context import PlannerContext

    ctx = PlannerContext(
        regime={"regime_name": "Stag", "regime_type_id": "stag", "confidence": 0.7},
        global_liquidity={"level": 95.0},
        ranking=[{"rank": 1, "portfolio_id": "def-pf", "defender": 1, "allocation": {"SPY": 100}}],
        scenarios=[{"strategy_id": "s1", "scenario": "bull", "probability": 60.0, "shift": 5.0}],
        top_invariants=[
            {"id": "inv-gold", "title": "gold", "weight_effective": 0.7, "active": True}
        ],
        recent_proposals=[],
        passages=[],
        notes="framed",
    )
    text = render_context_for_worker(ctx)
    assert "def-pf *" in text  # defender starred
    assert "[ACTIVE] inv-gold" in text
    assert "COACH NOTES: framed" in text
