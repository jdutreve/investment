"""Simulated Monday end to end (docs/MILESTONES.md M8 Definition of Verified:
"simulated Monday on fixtures end to end"). Wires the real UC8 cognitive cycle
and the real digest render through the real chain runner, against a seeded DB
that stands in for the morning's mechanical jobs (each already tested on its
own). The LLM roles are driven by PydanticAI TestModel; everything else — gates,
persistence, EventLog ordering, digest — is the production code.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic_ai.models.test import TestModel

from investment.chain import run_chain
from investment.db.sqlite import InvestmentDB
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.telegram.digest import build_scoreboard, render_digest
from investment.uc8 import UC8Result, run_decision_cycle
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


class _StubEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 4), dtype=np.float32)


async def _seed(db: InvestmentDB) -> None:
    """The state the 08:00-08:55 mechanical jobs would have left: a current
    regime, a ranked defender snapshot, the tradable universe, and an
    integrated invariant the Worker can lean on."""

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
        "INSERT INTO regime (id, regime_type_id, tags, start_date, is_current, events, "
        "confidence, trace, created_at, updated_at) VALUES ('r1', 'stag', '[]', '2026-06-01', 1, "
        "'[\"CPI up\"]', 0.78, 't', '2026-06-01', '2026-06-01')"
    )
    await cmd(
        "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
        "allocation, rank, sortino_rolling, calmar_rolling, market_context, recommendation, "
        "trace) VALUES ('2026-07-20', 'def-pf', 1, '4s', "
        "'{\"SPY\": 50, \"GLD\": 25, \"IEF\": 25}', 1, 1.18, 1.9, '{}', 'maintain', 't')"
    )
    for tk, cls in (("SPY", "equities"), ("GLD", "gold-commodities"), ("IEF", "bonds")):
        await cmd(
            "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, "
            "active) VALUES (:t, :c, 'USD', 'yahoo', 'none', 1)",
            t=tk,
            c=cls,
        )
    await cmd(
        "INSERT INTO invariant (id, title, description, source, status, condition, "
        "weight_initial, floor_weight, weight_effective, confirmation_count, infirmation_count, "
        "market_score, author, trace, created_at, updated_at) VALUES ('inv-gold', "
        "'GLD stagflation hedge', 'd', 's', 'integrated', '[]', 0.5, 0.2, 0.7, 5, 1, 0.83, "
        "'dalio', 'tr', '2026-01-01', '2026-01-01')"
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "monday.db")
    await _seed(conn)
    yield conn
    await conn.close()


def _proposal_view(cycle: UC8Result) -> dict[str, Any]:
    realloc = cycle.worker_result.reallocation_proposed
    assert realloc is not None
    defender = next(r for r in cycle.context.ranking if r.get("defender"))
    return {
        "proposal_type": "reallocation",
        "current_allocation": defender["allocation"],
        "proposed_allocation": realloc.proposed_allocation,
        "reasoning": realloc.reasoning,
    }


async def test_simulated_monday_runs_the_chain_and_emits_a_digest(db: InvestmentDB) -> None:
    pre = PlannerPre(db, _StubEmbedder(), "planner/x", "sk-test")
    worker = build_worker_agent(db, "anthropic/x", "sk-test")
    post = PlannerPost("planner/x", "sk-test")

    query = TestModel(custom_output_args={"corpus_queries": [], "zooms": []})
    select = TestModel(
        custom_output_args={"invariant_ids": ["inv-gold"], "passage_ids": [], "notes": "storm"}
    )
    worker_out = TestModel(
        call_tools=[],
        custom_output_args={
            "regime_assessment": "stagflation deepening",
            "ranking_commentary": "defender leads",
            "scenario_adjustments": [],
            "evaluations": [],
            "reallocation_proposed": {
                "proposed_allocation": {"SPY": 40.0, "GLD": 35.0, "IEF": 25.0},
                "scenario_delta": {},
                "favors_delta": {},
                "blend_note": "0.4/0.6",
                "supporting_invariants": ["inv-gold"],
                "reasoning": "gold above its 7y trend; tilt in",
            },
            "innovations_proposed": [],
            "reasoning": "tilt to gold",
        },
    )
    extract = TestModel(
        custom_output_args={
            "evaluations": [],
            "scenario_updates": [],
            "confrontations": [],
            "innovations": [],
            "regime_notes": "coherent",
        }
    )

    holder: dict[str, Any] = {}

    async def uc8_step() -> None:
        holder["cycle"] = await run_decision_cycle(
            db, pre, worker, post, trigger="monday", user_profile=USER, thresholds=THRESHOLDS
        )

    async def digest_step() -> None:
        cycle: UC8Result = holder["cycle"]
        holder["digest"] = render_digest(
            regime=cycle.context.regime,
            global_liquidity=cycle.context.global_liquidity,
            ranking=cycle.context.ranking,
            invariants=cycle.context.top_invariants,
            proposal=_proposal_view(cycle),
            scoreboard=await build_scoreboard(db),
        )

    with (
        pre.query_agent.override(model=query),
        pre.context_agent.override(model=select),
        worker.override(model=worker_out),
        post.agent.override(model=extract),
    ):
        result = await run_chain(
            db, [("uc8", uc8_step), ("digest", digest_step)], "monday-2026-07-20"
        )

    # the whole chain completed in order
    assert result.ok is True
    assert result.completed == ["uc8", "digest"]

    # UC8 produced a passing reallocation, persisted EventLog-first
    cycle: UC8Result = holder["cycle"]
    assert cycle.proposal_id is not None
    events = await db.query("SELECT type FROM event_log WHERE type = 'ProposalEvent'")
    assert len(events) == 1

    # the digest is readable and complete
    digest = holder["digest"]
    assert "Regime: Stagflation (78.0% — stag)" in digest
    assert "def-pf: 1.18 ★ (defender)" in digest
    assert "GLD stagflation hedge" in digest
    assert "GLD 25→35" in digest  # the tilt into gold
    assert "Proposals hit-rate: 0/0" in digest  # no decided proposals yet
