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

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import ExitStack
from dataclasses import replace
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
    "recency_half_life_days": 365.0,
    "invariant_min_confrontations": 3.0,
    "invariant_time_validation_score": 0.6,
    "invariant_verdict_confidence": 0.95,
    "invariant_null_score": 0.5,
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
    # ...with a track record dated BEFORE the episode. Without it the as-of
    # re-maturation rightly demotes it to 'proposed' (no evidence at t is not
    # evidence of an effect) and gate 6 refuses every citation — which is the
    # correct behaviour, and exactly what this fixture must not accidentally test.
    for i in range(9):
        await cmd(
            "INSERT INTO invariant_confrontations (id, invariant_id, moment_context, date, "
            "verdict, severity, source) VALUES (:id, 'inv-gold', '{}', :d, :v, 1.0, 'backtest')",
            id=f"conf-{i}",
            d=(date(2006, 1, 1) + timedelta(days=90 * i)).isoformat(),
            v="refuted" if i == 8 else "confirmed",
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


async def test_one_failing_date_does_not_burn_the_whole_episode(
    live: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An 84-call run must not lose six completed readings because the seventh
    raised. The failure is RECORDED, not swallowed: the date carries its error,
    `failed_dates` counts it, and the surviving dates still produce a curve."""
    calls = {"n": 0}
    real = AR.run_decision_cycle

    async def _flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("the Worker exploded")
        return await real(*args, **kwargs)

    monkeypatch.setattr(AR, "run_decision_cycle", _flaky)

    episode, _bound = await _run(live, tmp_path, reallocation=None)

    assert episode.failed_dates == 1
    assert len(episode.outcomes) == calls["n"]
    failed = [o for o in episode.outcomes if o.failure]
    assert "the Worker exploded" in failed[0].failure  # type: ignore[operator]
    # the survivors still carry their readings, and the book still prices
    assert any("fiscal impulse" in o.reading for o in episode.outcomes)
    assert len(episode.nav_agentic) > 0


async def test_a_stalled_date_is_cut_by_the_wall_clock(
    live: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured 2026-08-06: one stalled connection held a date for 55 minutes
    under a 300s per-REQUEST timeout, because that bounds a request and not a
    cycle. The wall clock bounds the unit the harness cares about."""
    real = AR.run_decision_cycle

    async def _hangs(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("today") == OPENS:
            await asyncio.sleep(60)  # never completes within the patched budget
        return await real(*args, **kwargs)

    monkeypatch.setattr(AR, "run_decision_cycle", _hangs)
    monkeypatch.setattr(AR, "DATE_TIMEOUT_SECONDS", 0.2)

    episode, _bound = await _run(live, tmp_path, reallocation=None)

    stalled = [o for o in episode.outcomes if o.as_of == OPENS]
    assert stalled[0].failure is not None
    assert "TimeoutError" in stalled[0].failure
    # ...and the episode carried on to the dates that follow it
    assert len(episode.outcomes) > 1


async def test_a_failed_date_is_loud_in_the_report(live: Path, tmp_path: Path) -> None:
    """A date that never ran is not a date the Worker chose to sit out, and a
    report that rendered them alike would overstate the coverage."""
    episode, _bound = await _run(live, tmp_path, reallocation=None)
    broken = AR.DateOutcome(
        as_of=episode.outcomes[0].as_of,
        mechanical=episode.outcomes[0].mechanical,
        reading="",
        proposed_allocation=None,
        gate=None,
        accepted=False,
        innovations=0,
        failure="RuntimeError: boom",
    )
    patched = replace(episode, outcomes=[*episode.outcomes, broken])

    text = AR.render_report([patched])

    assert "!! FAILED" in text
    assert "RuntimeError: boom" in text
    assert "1 failed" in text


async def test_the_three_curves_cover_the_same_window(live: Path, tmp_path: Path) -> None:
    """`run_replay` prices to the end of the CALENDAR, not to its `end`. Left
    uncut, arm B ran 549 daily points against the agentic arm's 123 and the three CAGRs were
    measured over different windows — a delta made of nothing."""
    episode, _bound = await _run(live, tmp_path, reallocation=None)
    assert len(episode.nav_agentic) == len(episode.nav_mechanical) == len(episode.nav_hold)
    for nav in (episode.nav_agentic, episode.nav_mechanical, episode.nav_hold):
        assert nav.index[0] >= pd.Timestamp(OPENS)
        assert nav.index[-1] <= pd.Timestamp(CLOSES)


async def test_agentic_replay_semipit(live: Path, tmp_path: Path) -> None:
    """M8b's Definition of Verified, third box: invariant weights read AS-OF-T,
    and a confrontation dated after t changes no weight before t.

    The fixture's invariant carries 9 confrontations before the episode and one
    crushing run of refutations after it. If the replay read the corpus as it
    stands today, that later evidence would drag the score to 0.31 and the
    verdict to 'rejected'; as-of t it must read 8/9 and stay integrated."""
    db = InvestmentDB(live)
    for i in range(40):
        await db.command(
            "INSERT INTO invariant_confrontations (id, invariant_id, moment_context, date, "
            "verdict, severity, source) VALUES (:id, 'inv-gold', '{}', :d, 'refuted', 1.0, "
            "'backtest')",
            id=f"after-{i}",
            d=(date(2009, 1, 1) + timedelta(days=7 * i)).isoformat(),
        )
    live_view = await db.query(
        "SELECT COUNT(*) AS n FROM invariant_confrontations WHERE invariant_id = 'inv-gold'"
    )
    assert live_view[0]["n"] == 49
    await db.close()

    episode, bound = await _run(live, tmp_path, reallocation=None)
    assert episode.outcomes

    snapshot = bound[0]._db_path
    seen = InvestmentDB(Path(snapshot))
    try:
        rows = await seen.query(
            "SELECT confirmation_count, infirmation_count, market_score, status "
            "FROM invariant WHERE id = 'inv-gold'"
        )
        # 8 confirmed / 1 refuted, all dated before the episode opened.
        assert (rows[0]["confirmation_count"], rows[0]["infirmation_count"]) == (8, 1)
        assert rows[0]["market_score"] == pytest.approx(8 / 9)
        assert rows[0]["status"] == "integrated"
        # and not one of the 40 later refutations reached the snapshot
        later = await seen.query(
            "SELECT COUNT(*) AS n FROM invariant_confrontations "
            "WHERE invariant_id = 'inv-gold' AND \"date\" > :t",
            t=CLOSES.isoformat(),
        )
        assert later[0]["n"] == 0
    finally:
        await seen.close()


async def test_the_report_shows_both_channels(live: Path, tmp_path: Path) -> None:
    """A NAV table alone answers half the STOP POINT. The readings are printed
    in full, not counted, because "does the Worker reason sensibly?" cannot be
    read off a number."""
    episode, _bound = await _run(live, tmp_path, reallocation=None)
    report = AR.render_report([episode])

    assert "semi-PIT" in report and "NOT go-live performance" in report
    assert "A' agentic" in report and "all-weather" in report
    assert "behavioural log" in report
    for out in episode.outcomes:
        assert str(out.as_of) in report
        assert out.reading in report  # in full
