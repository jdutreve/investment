"""The agentic replay loop (M8b — docs/TASKS.md Task 9.4;
src/investment/mechanical/agentic_replay.py).

What this pins is the LOOP, not the LLM: the models are PydanticAI `TestModel`s,
so the Worker's answer is fixed and everything else — the snapshot, the
hydration, the market-signal decision, the gates, the shadow book — is the
production code. Three properties matter and none of them is a NAV number:

- the Worker's world is the SNAPSHOT, never the live database. A single agent
  built against the wrong handle would leak the whole future silently, because
  every query would still answer.
- A' follows only the reallocations the GATES ACCEPTED. Crediting the Worker for
  a proposal Writeback refused would invert the screen's verdict.
- the clock ADVANCES across the episode, so month two knows what month one did.
"""

import json
from collections.abc import AsyncIterator
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from pydantic_ai.models.test import TestModel

from investment.db.sqlite import InvestmentDB
from investment.mechanical import agentic_replay as AR
from investment.mechanical.market_signal import BOOK_PORTFOLIO_IDS, STACK_TICKERS
from investment.mechanical.replay import load_inputs, load_thresholds
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.worker.agent import build_worker_agent

OPENS = date(2008, 7, 1)
CLOSES = date(2008, 10, 31)
START = date(2005, 1, 1)
END = date(2009, 12, 31)
USER = {"max_single_asset_pct": 50.0, "max_drawdown_pct": -25.0}
THRESHOLDS: dict[str, float] = {
    "rolling_window_days": 756.0,
    "ranking_tiebreak_window": 0.02,
    "min_backtest_periods": 3.0,
    "replay_cost_bps": 23.0,
    "replay_confirmation_weeks": 2.0,
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
    "proposal_outcome_weeks": 12.0,
}
DEFENDER_ALLOCATION = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
# Inside the caps, over the 5pt minimum change, under the 30% turnover cap.
WORKER_TARGET = {"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}


class _StubEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 4), dtype=np.float32)


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    for key, value in THRESHOLDS.items():
        await cmd(
            "INSERT INTO system_thresholds (key, value, description, updated_at) "
            "VALUES (:k, :v, 'd', '2026-01-01')",
            k=key,
            v=value,
        )
    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('fw', 'F', 1, 'tr', '2000-01-01')"
    )
    await cmd(
        "INSERT INTO regime_type (id, name, aliases, framework_id, description, created_at) "
        "VALUES ('stag', 'Stagflation', '[]', 'fw', 'd', '2000-01-01')"
    )
    await cmd(
        "INSERT INTO regime (id, regime_type_id, tags, start_date, is_current, events, "
        "confidence, trace, created_at, updated_at) VALUES ('r1', 'stag', '[]', '2008-01-15', 1, "
        "'[\"CPI up\"]', 78.0, 'tr', '2008-02-15', '2008-02-15')"
    )
    await cmd(
        "INSERT INTO user_profile (user_id, currency, benchmark, phase, horizon_years, "
        "max_drawdown_pct, max_single_asset_pct, created_at, updated_at) VALUES ('u', 'USD', "
        "'b', 'accumulation', 12, -25.0, 50.0, '2000-01-01', '2000-01-01')"
    )
    await cmd(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) VALUES "
        "('def-pf', 'D', 'fw', 1, 1, 'USD', 'b', :alloc, -25.0, 50.0, 'accumulation', 'tr', "
        "'2026-01-01')",
        alloc=json.dumps(DEFENDER_ALLOCATION),
    )
    for book_id in BOOK_PORTFOLIO_IDS.values():
        await cmd(
            "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, "
            "benchmark, allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, "
            "updated_at) VALUES (:id, :id, 'fw', 0, 0, 'USD', 'b', '{}', -25.0, 50.0, "
            "'accumulation', 'tr', '2026-01-01')",
            id=book_id,
        )
    # The invariant the Worker cites — integrated and ACTIVE ('always'), because
    # UC8-B gate 6 refuses a reallocation leaning on anything else.
    await cmd(
        "INSERT INTO invariant (id, title, description, source, status, condition, "
        "weight_initial, weight_effective, floor_weight, market_score, trace, created_at, "
        "updated_at) VALUES ('inv-gold', 'Gold in stress', 'd', 's', 'integrated', '[]', 0.8, "
        "0.8, 0.4, 1.0, 'tr', '2000-01-01', '2000-01-01')"
    )
    for ticker in (*STACK_TICKERS, *DEFENDER_ALLOCATION):
        await cmd(
            "INSERT OR IGNORE INTO allowed_tickers (ticker, asset_class, currency, source, "
            "transform, active) VALUES (:t, 'equities', 'USD', 'yahoo', 'none', 1)",
            t=ticker,
        )

    days = (END - START).days + 1
    await db.append_ts_batch(
        "portfolio_nav",
        [
            {
                "portfolio_id": "def-pf",
                "currency": "USD",
                "ts": (START + timedelta(days=i)).isoformat(),
                "nav": 100.0 + i * 0.05,
                "daily_return": 0.0005,
                "sharpe_rolling": 0.5,
                "sortino_rolling": 1.25,
                "calmar_rolling": 1.50,
                "drawdown": -0.08,
            }
            for i in range(days)
        ],
    )
    rising = [100.0 + i * 0.05 for i in range(days)]
    series: dict[str, list[float]] = {t: rising for t in (*STACK_TICKERS, *DEFENDER_ALLOCATION)}
    series["^IRX"] = [2.0] * days
    series["BAA10Y"] = [1.0] * days
    series["T10Y2Y"] = [2.0] * days
    for ticker, values in series.items():
        await db.append_ts_batch(
            "market_data",
            [
                {
                    "ts": (START + timedelta(days=i)).isoformat(),
                    "ticker": ticker,
                    "asset_class": "equities",
                    "currency": "USD",
                    "level": value,
                }
                for i, value in enumerate(values)
            ],
        )


@pytest.fixture
async def live(tmp_path: Path) -> AsyncIterator[Path]:
    path = tmp_path / "live.db"
    db = InvestmentDB(path)
    await _seed(db)
    await db.close()
    yield path


def _worker_output(reallocation: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "regime_assessment": "stagflation deepening",
        "ranking_commentary": "defender leads",
        "market_signal_assessment": (
            "the spread is wide; the book fits, the fiscal impulse is not in it"
        ),
        "scenario_adjustments": [],
        "evaluations": [],
        "reallocation_proposed": reallocation,
        "innovations_proposed": [],
        "reasoning": "tilt to gold",
    }


async def _run(live: Path, tmp_path: Path, *, reallocation: dict[str, Any] | None) -> Any:
    """Drive one episode with TestModels. The overrides ride on an ExitStack the
    FACTORY pushes onto, because the agents do not exist until the episode opens
    its snapshot — which is the whole point of the factory."""
    db = InvestmentDB(live)
    inputs = await load_inputs(db)
    thresholds = await load_thresholds(db)
    await db.close()

    query = TestModel(custom_output_args={"corpus_queries": [], "zooms": []})
    select = TestModel(
        custom_output_args={"invariant_ids": ["inv-gold"], "passage_ids": [], "notes": "storm"}
    )
    worker_out = TestModel(call_tools=[], custom_output_args=_worker_output(reallocation))
    extract = TestModel(
        custom_output_args={
            "evaluations": [],
            "scenario_updates": [],
            "confrontations": [],
            "innovations": [],
            "regime_notes": "coherent",
        }
    )
    bound: list[InvestmentDB] = []

    with ExitStack() as stack:

        def make_agents(snapshot_db: InvestmentDB) -> AR.CognitiveAgents:
            bound.append(snapshot_db)
            agents = AR.CognitiveAgents(
                planner_pre=PlannerPre(snapshot_db, _StubEmbedder(), "planner/x", "sk-test"),
                worker=build_worker_agent(snapshot_db, "worker/x", "sk-test"),
                planner_post=PlannerPost("planner/x", "sk-test"),
            )
            stack.enter_context(agents.planner_pre.query_agent.override(model=query))
            stack.enter_context(agents.planner_pre.context_agent.override(model=select))
            stack.enter_context(agents.worker.override(model=worker_out))
            stack.enter_context(agents.planner_post.agent.override(model=extract))
            return agents

        episode = await AR.run_agentic_episode(
            live,
            tmp_path,
            name="gfc",
            opens=OPENS,
            closes=CLOSES,
            inputs=inputs,
            thresholds=thresholds,
            make_agents=make_agents,
            user_profile=USER,
            system_thresholds=THRESHOLDS,
            cost_bps=THRESHOLDS["replay_cost_bps"],
            confirmation_weeks=THRESHOLDS["replay_confirmation_weeks"],
        )
    return episode, bound


async def test_the_worker_is_bound_to_the_snapshot_not_the_live_database(
    live: Path, tmp_path: Path
) -> None:
    """The leak that would be total and silent: agents built against the live
    handle answer every query, just with 2026 data."""
    _episode, bound = await _run(live, tmp_path, reallocation=None)
    assert bound and all(str(db._db_path) != str(live) for db in bound)
    assert all("agentic-gfc" in str(db._db_path) for db in bound)


async def test_an_accepted_reallocation_moves_the_book(live: Path, tmp_path: Path) -> None:
    episode, _bound = await _run(
        live,
        tmp_path,
        reallocation={
            "proposed_allocation": WORKER_TARGET,
            "scenario_delta": {},
            "favors_delta": {},
            "blend_note": "0.4/0.6",
            "supporting_invariants": ["inv-gold"],
            "reasoning": "gold above its 7y trend; tilt in",
        },
    )
    assert episode.name == "gfc"
    assert len(episode.outcomes) == 4  # monthly, July..October 2008
    assert episode.accepted_reallocations >= 1
    accepted = [o for o in episode.outcomes if o.accepted]
    assert accepted[0].proposed_allocation == WORKER_TARGET
    # A' followed it, so it cannot be the hold curve.
    assert not episode.nav_agentic.equals(episode.nav_hold)


async def test_a_worker_that_proposes_nothing_holds_the_book(live: Path, tmp_path: Path) -> None:
    """A Worker that proposes nothing moves no capital, so A' IS the hold curve.

    Not "the delta is zero", which is what this test asserted first and what the
    milestone's wording suggests: arm A runs the mechanical reallocation
    triggers and the bridge's switch arm on its own, so it moves when the Worker
    does not. A' - A compares two whole policies (module docstring), and a
    Worker that stands still is a policy too."""
    episode, _bound = await _run(live, tmp_path, reallocation=None)
    assert episode.accepted_reallocations == 0
    assert all(o.proposed_allocation is None for o in episode.outcomes)
    assert episode.nav_agentic.equals(episode.nav_hold)


async def test_the_behavioural_log_carries_the_worker_reading(live: Path, tmp_path: Path) -> None:
    """M8b's SECOND channel, weighted equally with the NAVs in its Definition of
    Verified — and the only one that answers "does it reason sensibly?"."""
    episode, _bound = await _run(live, tmp_path, reallocation=None)
    assert [o.as_of for o in episode.outcomes] == sorted(o.as_of for o in episode.outcomes)
    assert all("fiscal impulse" in o.reading for o in episode.outcomes)
    # the mechanical half ran at every one of them
    assert all(o.mechanical.portfolios_ranked >= 1 for o in episode.outcomes)


async def test_the_clock_advances_across_the_episode(live: Path, tmp_path: Path) -> None:
    """Month two must know what month one did — otherwise the market-signal
    decision replays as an opening entry every month."""
    episode, _bound = await _run(live, tmp_path, reallocation=None)
    decisions = [o.mechanical.market_signal_decision for o in episode.outcomes]
    assert decisions == [o.as_of.isoformat() for o in episode.outcomes]
    # ...and only the FIRST month emits the stack's opening proposal.
    emitted = [o for o in episode.outcomes if o.mechanical.market_signal_proposal_id]
    assert len(emitted) == 1


async def test_the_three_curves_cover_the_same_window(live: Path, tmp_path: Path) -> None:
    """`run_replay` prices to the end of the CALENDAR, not to its `end`. Left
    uncut, arm B ran 549 daily points against the agentic arm's 123 and the three CAGRs were
    measured over different windows — a delta made of nothing."""
    episode, _bound = await _run(live, tmp_path, reallocation=None)
    assert len(episode.nav_agentic) == len(episode.nav_mechanical) == len(episode.nav_hold)
    for nav in (episode.nav_agentic, episode.nav_mechanical, episode.nav_hold):
        assert nav.index[0] >= pd.Timestamp(OPENS)
        assert nav.index[-1] <= pd.Timestamp(CLOSES)
