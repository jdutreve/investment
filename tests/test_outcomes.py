"""Proposal outcome evaluation (docs/ARCHITECTURE.md "Unified improvement
cycle"; src/investment/mechanical/outcomes.py). Pure helpers directly; the
verdict end-to-end against a real throwaway SQLite with deterministic price
fixtures — SPY rises 20%, TLT is flat — so 'won'/'lost' is exact, not
approximate (CLAUDE.md: real DB, no mocks)."""

import json
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import outcomes

START = date(2026, 1, 5)  # a weekly run
WINDOW = timedelta(weeks=12)
END = START + WINDOW
TODAY = END + timedelta(days=1)  # window complete


# -- pure helpers ------------------------------------------------------------


def test_normalize_makes_fractions_and_rejects_empty() -> None:
    assert outcomes.normalize({"SPY": 60, "TLT": 40}) == {"SPY": 0.6, "TLT": 0.4}
    assert outcomes.normalize({}) == {}
    assert outcomes.normalize({"SPY": 0}) == {}


def test_turnover_is_the_unhalved_per_side_sum() -> None:
    # a full switch TLT -> SPY moves 1.0 out and 1.0 in = 2.0 (20 bps at 10/side)
    assert outcomes.turnover({"TLT": 1.0}, {"SPY": 1.0}) == pytest.approx(2.0)
    assert outcomes.turnover({"SPY": 0.5, "TLT": 0.5}, {"SPY": 0.5, "TLT": 0.5}) == 0.0


def test_verdict_gives_a_tie_to_the_incumbent() -> None:
    assert outcomes.verdict(0.10, 0.05) == "won"
    assert outcomes.verdict(0.05, 0.10) == "lost"
    assert outcomes.verdict(0.05, 0.05) == "lost"  # burden of proof on the challenger


# -- integration -------------------------------------------------------------


async def _seed_common(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    for key, value in (
        ("proposal_outcome_weeks", 12.0),
        ("replay_cost_bps", 10.0),
        ("recency_half_life_days", 365.0),
    ):
        await cmd(
            "INSERT INTO system_thresholds (key, value, updated_at) VALUES (:k, :v, '2026-01-01')",
            k=key,
            v=value,
        )
    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4seasons', 'F', 1, 't', '2026-01-01')"
    )
    # daily prices across the window: SPY 100 -> 120 linearly, TLT flat, IRX 4%
    days = (END - START).days
    rows = []
    for i in range(days + 5):  # a few days past END so the window is covered
        ts = (START + timedelta(days=i)).isoformat()
        spy = 100.0 + 20.0 * (i / days)
        for ticker, level in (("SPY", spy), ("TLT", 100.0), ("^IRX", 4.0)):
            rows.append(
                {"ticker": ticker, "asset_class": "x", "currency": "USD", "ts": ts, "level": level}
            )
    await db.append_ts_batch("market_data", rows)
    # snapshots at START: defender holds TLT (flat), challenger holds SPY (up)
    for pid, alloc in (("defender-pf", '{"TLT": 100}'), ("challenger-pf", '{"SPY": 100}')):
        await cmd(
            "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
            "allocation, rank, market_context, recommendation, trace) "
            "VALUES (:d, :p, 0, '4seasons', :a, 1, '{}', 'maintain', 't')",
            d=START.isoformat(),
            p=pid,
            a=alloc,
        )


async def _add_proposal(
    db: InvestmentDB,
    pid: str,
    ptype: str,
    d: date,
    *,
    challenger: str | None = None,
    proposed_allocation: str | None = None,
    outcome: str | None = None,
) -> None:
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, challenger_id, "
        "proposed_allocation, recommendation, market_context, reasoning, outcome, trace, "
        "created_at) VALUES (:id, :d, :t, 'defender-pf', :c, :pa, 'monitor', '{}', 'r', :o, "
        "'t', :d)",
        id=pid,
        d=d.isoformat(),
        t=ptype,
        c=challenger,
        pa=proposed_allocation,
        o=outcome,
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "o.db")
    await _seed_common(conn)
    yield conn
    await conn.close()


async def test_a_due_switch_beats_a_flat_incumbent(db: InvestmentDB) -> None:
    await _add_proposal(db, "p-win", "switch", START, challenger="challenger-pf")
    (res,) = await outcomes.evaluate_proposals(db, today=TODAY)
    assert res.verdict == "won"  # SPY ~+20% (net ~19.8% of cost) beats flat TLT
    assert res.incumbent_return == pytest.approx(0.0, abs=1e-6)
    assert res.proposed_return == pytest.approx(0.20 - 0.002, abs=2e-3)
    # persisted: Proposal.outcome + evaluated_at, and an OutcomeEvent (EventLog)
    row = (await db.query("SELECT outcome, evaluated_at FROM proposal WHERE id='p-win'"))[0]
    assert json.loads(row["outcome"])["verdict"] == "won"
    assert row["evaluated_at"] == TODAY.isoformat()
    events = await db.query("SELECT source_id, payload FROM event_log WHERE type = 'OutcomeEvent'")
    assert len(events) == 1
    assert events[0]["source_id"] == "p-win"
    assert json.loads(events[0]["payload"])["kind"] == "proposal"


async def test_a_reallocation_is_valued_from_its_inline_target(db: InvestmentDB) -> None:
    # reallocation INTO the rising asset -> also wins vs the flat defender
    await _add_proposal(db, "p-realloc", "reallocation", START, proposed_allocation='{"SPY": 100}')
    (res,) = await outcomes.evaluate_proposals(db, today=TODAY)
    assert res.verdict == "won"


async def test_the_scored_window_pays_its_drift_rebalances(db: InvestmentDB) -> None:
    """I-45 / ADR-010 point 2: the temporary NAVs built to score a proposal are
    charged the same rate as the persisted ones, so "every NAV pays" is true of
    the code and not only of the ADR.

    TWO SLEEVES ARE WHAT MAKES IT VISIBLE, and that is why no other test here
    moved when the charge was added: every other allocation in this file is a
    single ticker, and a lone sleeve cannot drift away from a 100% target — its
    rebalance trades nothing at any rate."""
    await _add_proposal(
        db, "p-cost", "reallocation", START, proposed_allocation='{"SPY": 50, "TLT": 50}'
    )
    (res,) = await outcomes.evaluate_proposals(db, today=TODAY)
    assert res.proposed_return is not None

    gross = await outcomes._window_return(
        db, {"SPY": 0.5, "TLT": 0.5}, pd.Timestamp(START), pd.Timestamp(END), 0.0
    )
    assert gross is not None
    # The switch out of TLT 100 into 50/50 moves 0.5 in and 0.5 out = 1.0 turnover
    # at the seeded 10 bps, charged once on entry by `_evaluate_one`.
    entry_cost = 0.001
    rebalances = gross - entry_cost - res.proposed_return
    assert rebalances > 0.0, "the drift-rebalances inside the window are free again"
    # Two of them (the Feb and Mar month boundaries inside a 2026-01-05 -> 03-30
    # window). The SPY ramp drifts the pair ~1.7 pt off target in a month, so
    # each rebalance trades sum|dW| ~= 0.034 at 10 bps = 3.4e-5, twice = 6.6e-5.
    # I-45's estimate was ~1.5 bp at the LIVE 23 bps; 0.66 bp at the fixture's 10
    # is the same measurement, and it is the size that made this a wording fix
    # rather than a verdict fix.
    assert rebalances == pytest.approx(6.6e-5, abs=5e-6)


async def test_the_scoreboard_prices_at_the_pinned_rate_on_a_bare_db(tmp_path: Path) -> None:
    """`paper_test_progress` feeds the DIGEST, which must render from whatever
    the database holds — so a missing `replay_cost_bps` falls back to
    `ratios.TRADING_COST_BPS` instead of raising, which is what
    `evaluate_proposals` does with the same missing row (it decides a verdict; a
    broken seed must stop it). ADR-010 pins the two equal, so the fallback cannot
    price at a rate the verdict would not have used.

    Named because the digest tests only cover it by accident: they build empty
    databases for other reasons, and would start failing for a reason nobody
    would connect to this rule."""
    db = InvestmentDB(tmp_path / "bare.db")
    try:
        await db.command(
            "INSERT INTO proposal (id, date, proposal_type, defender_id, recommendation, "
            "market_context, reasoning, paper_started, trace, created_at) VALUES ('bare', "
            ":d, 'reallocation', 'defender-pf', 'paper-test', '{}', 'r', :d, 't', :d)",
            d=START.isoformat(),
        )
        # No system_thresholds row, no prices: the run must complete and report
        # the unvaluable legs as None rather than raise on the missing rate.
        (progress,) = await outcomes.paper_test_progress(db, today=TODAY)
        assert progress["proposal_id"] == "bare"
        assert progress["excess"] is None
    finally:
        await db.close()


async def test_a_proposal_before_its_window_is_left_pending(db: InvestmentDB) -> None:
    await _add_proposal(
        db, "p-young", "switch", TODAY - timedelta(weeks=3), challenger="challenger-pf"
    )
    (res,) = await outcomes.evaluate_proposals(db, today=TODAY)
    assert res.verdict == ""
    assert res.skipped_reason == "outcome window not yet reached"
    # untouched: still pending, no evaluated_at
    row = (await db.query("SELECT outcome, evaluated_at FROM proposal WHERE id='p-young'"))[0]
    assert row["outcome"] is None
    assert row["evaluated_at"] is None


async def test_won_reallocation_confirms_its_cited_invariants(db: InvestmentDB) -> None:
    # a reallocation into the rising asset, citing an invariant via proposal_cites
    await db.command(
        "INSERT INTO invariant (id, title, description, source, status, condition, "
        "weight_initial, floor_weight, weight_effective, confirmation_count, infirmation_count, "
        "market_score, trace, created_at, updated_at) VALUES ('inv-c', 't', 'd', 's', "
        "'integrated', '[]', 0.6, 0.2, 0.6, 4, 1, 0.8, 'tr', '2026-01-01', '2026-01-01')"
    )
    await _add_proposal(db, "p-cite", "reallocation", START, proposed_allocation='{"SPY": 100}')
    await db.command(
        "INSERT INTO proposal_cites (proposal_id, invariant_id) VALUES ('p-cite', 'inv-c')"
    )
    (res,) = await outcomes.evaluate_proposals(db, today=TODAY)
    assert res.verdict == "won"  # SPY beats flat TLT defender
    inv = (
        await db.query("SELECT confirmation_count, market_score FROM invariant WHERE id='inv-c'")
    )[0]
    assert inv["confirmation_count"] == 5  # 4 -> 5, a won proposal confirms its citation
    assert inv["market_score"] == pytest.approx(5 / 6)
    conf = await db.query(
        "SELECT source, verdict, source_id FROM invariant_confrontations WHERE invariant_id='inv-c'"
    )
    assert conf[0]["source"] == "proposal" and conf[0]["verdict"] == "confirmed"
    assert conf[0]["source_id"] == "p-cite"


async def test_paper_tracking_prices_a_market_signal_test_against_what_was_held(
    db: InvestmentDB,
) -> None:
    """The weekly paper-test tracking must resolve the incumbent the SAME way the
    +12w verdict does (`_incumbent_allocation`), not by looking up the defender's
    snapshot.

    The regression is specific to the ADOPTED path and it silently erased the
    measurement rather than skewing it: a market-signal proposal's `defender_id`
    is a BOOK portfolio, the books are `enabled = 0` (ADR-009) so they never get
    a weekly snapshot row — hence `ms-book-pf` below has none deliberately. The
    old lookup returned `{}`, the incumbent leg was unvaluable, and every live
    paper-test reported `excess: None` week after week.
    """
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
        "recommendation, market_context, reasoning, paper_started, trace, created_at) VALUES "
        "(:id, :d, 'market-signal', 'ms-book-pf', '{\"SPY\": 100}', 'paper-test', :ctx, 'r', "
        ":d, 't', :d)",
        id="p-ms",
        d=START.isoformat(),
        ctx=json.dumps({"held_allocation": {"TLT": 100}}),
    )
    (progress,) = await outcomes.paper_test_progress(db, today=TODAY)
    assert progress["proposal_id"] == "p-ms"
    assert progress["incumbent_return"] == pytest.approx(0.0, abs=1e-6)  # TLT flat
    # Slightly PAST +20%: tracking runs to `today`, one day beyond the 12w window
    # the verdict stops at, and the SPY fixture keeps ramping. That the two
    # differ here is the point of the function — it reports progress to date.
    assert progress["proposed_return"] == pytest.approx(0.2024, abs=1e-3)
    assert progress["excess"] == pytest.approx(0.2024, abs=1e-3)


async def test_already_decided_proposals_are_not_re_evaluated(db: InvestmentDB) -> None:
    decided = json.dumps({"proposed_return": 0.1, "incumbent_return": 0.0, "verdict": "won"})
    await _add_proposal(db, "p-done", "switch", START, challenger="challenger-pf", outcome=decided)
    results = await outcomes.evaluate_proposals(db, today=TODAY)
    assert results == []  # filtered out by the pending-only query
