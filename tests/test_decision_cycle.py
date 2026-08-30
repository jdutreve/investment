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
    render_context_for_worker,
    run_decision_cycle,
)
from investment.planner.context import PlannerContext
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


def _worker_output() -> dict:
    return {
        "regime_assessment": "stagflation deepening",
        "ranking_commentary": "defender leads on Sortino",
        "market_signal_assessment": "the wide-spread book reads the stress correctly, "
        "but the signal cannot see the fiscal impulse building",
        "scenario_adjustments": [],
        "evaluations": [],
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


async def test_every_cycle_is_knowledge_only(rig) -> None:  # type: ignore[no-untyped-def]
    """ADR-012: the Worker does not allocate, so EVERY cycle is knowledge-only.
    This used to be the "no reallocation" branch of two; it is now the whole
    behaviour, and what it must still prove is that a cycle proposing nothing
    is not traceless."""
    db, pre, worker, post = rig
    q, s, w, p = _overrides(pre, worker, post, _worker_output())
    with q, s, w, p:
        result = await run_decision_cycle(
            db, pre, worker, post, trigger="weekly", thresholds=THRESHOLDS
        )
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
            # light, and still shown: weight is a fact the reader weighs, not a
            # threshold to pass, now that nothing is being qualified for a gate
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
    # EVERY lighthouse is rendered, and none of them is qualified for anything.
    # Until ADR-012 each line carried [CITABLE] / [not citable: <reason>] and the
    # block closed on "a reallocation MUST cite at least one of them" — teaching
    # gate 6 to a Worker that can no longer allocate, cite, or be refused.
    for inv_id in ("inv-gold", "inv-dormant", "inv-light", "inv-unproven"):
        assert f"  {inv_id} — " in text
    assert "citable" not in text.lower()
    # DORMANT SURVIVES the removal, because it is a fact about the market and
    # not about a gate: a lighthouse whose condition does not hold today
    # describes a world that is not present.
    assert "(weight 0.7, null, integrated, dormant)" in text
    assert "(weight 0.7, null, integrated)" in text  # inv-gold, condition holding
    assert "COACH NOTES: framed" in text


# -- the two clocks ---------------------------------------------------------


def _two_clock_context(macro: list[dict[str, object]]) -> PlannerContext:
    return PlannerContext(
        regime={"regime_name": "Stag", "regime_type_id": "stag", "confidence": 0.7},
        global_liquidity={"level": 95.0},
        ranking=[],
        scenarios=[],
        top_invariants=[],
        recent_proposals=[],
        passages=[],
        notes="",
        macro=macro,
        market_signal={
            "decision_date": "2026-08-03",
            "held_book": "credit-spread-tight-yield-curve-steep",
            "signal_state": "credit-spread-tight-yield-curve-steep",
            "held_allocation": {"VCIT": 50.0},
            "target_allocation": {"VCIT": 50.0},
            "signals": {
                "T10Y2Y": {
                    "value": 0.45,
                    "trailing_median": 0.41,
                    "knowable_at": "2026-08-01",
                }
            },
            "trend_overlay": {"windows_days": [150, 300], "below_trend": []},
        },
    )


def test_a_signal_is_shown_on_both_clocks_with_the_drift_named() -> None:
    """Measured 2026-08-23. The Worker wrote "0.45 vs a 0.41 median is 4bp of
    headroom, and T10Y2Y's own speed (+0.14) is the only thing keeping this book
    from flipping flat" — level off the DECISION (2026-08-01), speed off the
    LIVE tape (2026-08-22). Both numbers were correct, both were dated, and they
    were in two separate blocks; the pair never coexisted. Live it was 0.50/+0.14
    — roughly double the headroom, and steepening rather than drifting flat.
    Pairing the two clocks on adjacent lines is what makes that impossible to
    write."""
    ctx = _two_clock_context(
        [
            {
                "ticker": "T10Y2Y",
                "ts": "2026-08-22",
                "level": 0.5,
                "speed": 0.14,
                "acceleration": 0.05,
            }
        ]
    )
    text = render_context_for_worker(ctx)
    assert "T10Y2Y: DECIDED ON 0.45 vs its 10y trailing median 0.41 (knowable 2026-08-01)" in text
    assert "now 0.5 (speed 0.14, accel 0.05) as of 2026-08-22, 0.09 above that median" in text
    # The framing has to travel with the numbers, or two dated blocks is all it is.
    assert "TWO CLOCKS BELOW" in text
    assert "proxy for what the NEXT monthly decision will see" in text
    # And the pairing rule, which `baseline._macro` enforces on the prompt path
    # and cannot enforce on the tool path — `db_query` takes arbitrary SQL, so
    # the Worker is the only place that rule can be applied there.
    assert "BOTH SIGNALS ARE READ ON ONE DATE" in text
    assert "describes a state that existed on no day" in text


def test_a_signal_absent_from_the_tape_still_renders_its_decision_line() -> None:
    """A macro feed can die (`alerts.macro_freshness_alert`). The decision line
    is what explains the book, so it must survive the tape it is paired with."""
    ctx = _two_clock_context([])
    text = render_context_for_worker(ctx)
    assert "T10Y2Y: DECIDED ON 0.45" in text
    assert "  now " not in text


def test_float_noise_is_not_sent_to_the_model() -> None:
    """Binary subtraction leaves the tape full of `0.039999999999999813` where
    FRED published 0.04 — tokens that carry no information and invite a
    precision the series does not have."""
    ctx = _two_clock_context(
        [
            {
                "ticker": "T10Y2Y",
                "ts": "2026-08-22",
                "level": 0.5,
                "speed": 0.039999999999999813,
                "acceleration": -0.050000000000000266,
            }
        ]
    )
    text = render_context_for_worker(ctx)
    assert "0.039999999999999813" not in text
    assert "speed 0.04, accel -0.05" in text
