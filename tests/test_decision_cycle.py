"""The cognitive decision cycle end to end (docs/USE_CASES.md UC8;
src/investment/decision_cycle.py).

The full cognitive chain — PlannerPre -> Worker -> PlannerPost -> Writeback —
driven by PydanticAI TestModel on a real throwaway SQLite. Covers M8's Definition
of Verified item: a reallocation the Worker proposes passes the gates and is
persisted; and the knowledge-only path where nothing is proposed."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest
from pydantic_ai.models.test import TestModel

from investment.db.sqlite import InvestmentDB
from investment.decision_cycle import (
    WORKER_READING_EVENT,
    UC8Result,
    render_context_for_worker,
    run_decision_cycle,
)
from investment.mechanical.market_signal import STACK_PORTFOLIO_ID
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
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
    "proposal_cooldown_weeks": 4.0,
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
        "market_signal_assessment": "the wide-spread book reads the stress correctly, "
        "but the signal cannot see the fiscal impulse building",
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
    await db.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) VALUES "
        "('def-pf', 'D', '4s', 1, 1, 'CHF', 'b', "
        "'{\"SPY\": 50, \"GLD\": 25, \"IEF\": 25}', -25.0, 50.0, 'accumulation', 'tr', "
        "'2026-01-01')"
    )
    # The COGNITIVE BOOK the Worker reallocates (decision_cycle.WORKER_BOOK_ID).
    # Seeded at the defender's allocation, as db/seed_data.py does: before the
    # first accepted reallocation the two are identical by construction.
    await cmd(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) VALUES "
        "('worker-book', 'Cognitive Book', '4s', 0, 1, 'CHF', 'b', "
        "'{\"SPY\": 50, \"GLD\": 25, \"IEF\": 25}', -25.0, 50.0, 'accumulation', 'tr', "
        "'2026-01-01')"
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
    db = InvestmentDB(tmp_path / "decision_cycle.db")
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
    prop = await db.query(
        "SELECT proposal_type, recommendation FROM proposal WHERE id=:i", i=result.proposal_id
    )
    assert prop[0]["recommendation"] == "paper-test"
    ev = await db.query("SELECT source_id FROM event_log WHERE type='ProposalEvent'")
    assert [e["source_id"] for e in ev] == [result.proposal_id]


async def test_defender_stricter_single_asset_cap_binds(rig) -> None:  # type: ignore[no-untyped-def]
    """CLAUDE.md "Binding caps": the TARGET's own cap may be stricter than the
    user's, and Writeback enforces the stricter of the two. The ranking snapshot
    carries no cap columns, so the cycle must fetch the `portfolio` row and
    thread it in — this proves that wiring. SPY 45 clears the user's 50 cap but
    breaches the cognitive book's own 40, so the proposal must be blocked."""
    db, pre, worker, post = rig
    # the TARGET book with a STRICTER 40 single-asset cap
    await db.command("UPDATE portfolio SET max_single_asset_pct = 40.0 WHERE id = 'worker-book'")
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

    # ...but the cycle is NOT traceless (ADR-011). Before this event, a week that
    # proposed and confronted nothing left no row anywhere, so it could not be
    # told apart from a week the chain never ran — and the Worker's prose, the
    # thing the system prompt calls "your contribution", was discarded whole.
    ev = await db.query(
        "SELECT source_uc, payload FROM event_log WHERE type = :t", t=WORKER_READING_EVENT
    )
    assert len(ev) == 1
    assert ev[0]["source_uc"] == "UC8"
    payload = json.loads(str(ev[0]["payload"]))
    assert payload["market_signal_assessment"].startswith("the wide-spread book")
    assert payload["regime_assessment"] == "stagflation deepening"
    assert payload["trigger"] == "weekly"


async def test_the_reading_is_journalled_even_when_the_allocation_is_refused(rig) -> None:  # type: ignore[no-untyped-def]
    """ADR-011 end to end through the REAL cycle, not just the Writeback unit:
    with the mechanically-allocated stack as defender, gate 0 refuses the
    Worker's reallocation on jurisdiction — and its qualitative reading survives
    anyway. That is the whole trade the ADR makes: the Worker cannot move the
    allocation, and what it CAN contribute must not be thrown away with the
    proposal that was refused.

    Refused here on a CAP, not on gate 0. Since 2026-08-08 the Worker
    reallocates its own book and nothing threads a portfolio id in from the
    ranking, so ADR-011's mechanical sovereignty stopped being a gate the cycle
    can reach and became structural: there is no input by which the Worker can
    aim at `ms-stack`. Gate 0 stays as defence in depth and is exercised
    directly in test_writeback.py."""
    db, pre, worker, post = rig
    await db.command("UPDATE portfolio SET max_single_asset_pct = 10.0 WHERE id = 'worker-book'")
    q, s, w, p = _overrides(pre, worker, post, _worker_output(_REALLOC))
    with q, s, w, p:
        result = await run_decision_cycle(
            db, pre, worker, post, trigger="weekly", user_profile=USER, thresholds=THRESHOLDS
        )
    assert result.gate_outcome is not None
    assert result.gate_outcome.failed_gate == "max_single_asset_pct"
    assert result.proposal_id is None
    assert await db.query("SELECT id FROM proposal") == []
    assert await db.query("SELECT id FROM event_log WHERE type = 'ProposalEvent'") == []

    # ...and the reading survives the refusal, which is the point of the test.
    ev = await db.query("SELECT payload FROM event_log WHERE type = :t", t=WORKER_READING_EVENT)
    assert len(ev) == 1
    assert json.loads(str(ev[0]["payload"]))["market_signal_assessment"]

    # The stack is unreachable from this path by construction: the cycle names
    # its target, and it is never a time-varying portfolio.
    assert STACK_PORTFOLIO_ID not in {r["id"] for r in await db.query("SELECT id FROM proposal")}


def test_render_context_marks_the_defender_and_active_lighthouses() -> None:
    from investment.planner.context import PlannerContext

    ctx = PlannerContext(
        regime={"regime_name": "Stag", "regime_type_id": "stag", "confidence": 0.7},
        global_liquidity={"level": 95.0},
        ranking=[{"rank": 1, "portfolio_id": "def-pf", "defender": 1, "allocation": {"SPY": 100}}],
        scenarios=[{"strategy_id": "s1", "scenario": "bull", "probability": 60.0, "shift": 5.0}],
        top_invariants=[
            {
                "id": "inv-gold",
                "title": "gold",
                "weight_effective": 0.7,
                "active": True,
                "status": "integrated",
            },
            # dormant: right status, wrong moment
            {
                "id": "inv-dormant",
                "title": "dormant",
                "weight_effective": 0.7,
                "active": False,
                "status": "integrated",
            },
            # active and integrated, but under the citation floor — the case the
            # Worker was never told about and could not deduce
            {
                "id": "inv-light",
                "title": "light",
                "weight_effective": 0.05,
                "active": True,
                "status": "integrated",
            },
            {
                "id": "inv-unproven",
                "title": "unproven",
                "weight_effective": 0.9,
                "active": True,
                "status": "proposed",
            },
        ],
        recent_proposals=[],
        passages=[],
        notes="framed",
    )
    text = render_context_for_worker(ctx)
    assert "def-pf *" in text  # defender starred
    assert "[CITABLE] inv-gold" in text
    # ...and every refusal states its OWN reason, so gate 6 cannot refuse for
    # something the Worker had no way to know (docs/MILESTONES.md M8 gate-6
    # watch item). Being told "cite ACTIVE and integrated" was not enough: the
    # weight floor and the refuted test were invisible.
    assert "[not citable: dormant" in text
    assert "below the 0.10 floor" in text
    assert "not citable: status proposed" in text
    assert "1 citable" in text
    assert "COACH NOTES: framed" in text


def test_render_says_so_when_nothing_is_citable() -> None:
    """Measured on the M8b covid episode: the Worker proposed three times and
    was refused twice on gate 6, with nothing citable available whatever it
    chose. Proposing into a vacuum is not a reasoning error, and it must be
    told rather than left to discover it through a refusal it never sees."""
    from investment.planner.context import PlannerContext

    ctx = PlannerContext(
        regime={"regime_name": "Stag", "regime_type_id": "stag", "confidence": 0.7},
        global_liquidity={},
        ranking=[],
        scenarios=[],
        top_invariants=[
            {
                "id": "inv-a",
                "title": "a",
                "weight_effective": 0.7,
                "active": False,
                "status": "integrated",
            }
        ],
        recent_proposals=[],
        passages=[],
        notes="",
    )
    text = render_context_for_worker(ctx)
    assert "NONE is citable this cycle" in text
    assert "a fact about the corpus" in text


async def test_the_worker_moves_its_own_book_and_leaves_the_defender_alone(rig) -> None:  # type: ignore[no-untyped-def]
    """The reason the cognitive book exists (owner decision 2026-08-08). While
    the Worker reallocated the BRIDGE DEFENDER, the same portfolio was the canvas
    for two policies — so M8b's "A' - A" compared them both at once and could
    never isolate the cognitive one. And judging the Worker meant stitching
    disjoint 12-week proposal outcomes: five observations across the whole
    screen, which carry no signal.

    An accepted proposal must therefore MOVE the book, exactly as
    `dispose_market_signal` moves `ms-stack`: the `allocation` column records
    what is HELD, and a row that never moves describes a book nobody holds."""
    db, pre, worker, post = rig
    before = await db.query("SELECT allocation FROM portfolio WHERE id = 'def-pf'")

    q, s, w, p = _overrides(pre, worker, post, _worker_output(_REALLOC))
    with q, s, w, p:
        result = await run_decision_cycle(
            db, pre, worker, post, trigger="weekly", user_profile=USER, thresholds=THRESHOLDS
        )
    assert result.proposal_id is not None

    book = await db.query("SELECT allocation FROM portfolio WHERE id = 'worker-book'")
    assert json.loads(str(book[0]["allocation"])) == _REALLOC["proposed_allocation"]
    # ...and the bridge defender is untouched: it is the benchmark, not the canvas
    assert await db.query("SELECT allocation FROM portfolio WHERE id = 'def-pf'") == before
    # the proposal names the book it moved
    prop = await db.query("SELECT defender_id FROM proposal WHERE id = :i", i=result.proposal_id)
    assert prop[0]["defender_id"] == "worker-book"
