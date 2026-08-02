"""Writeback reallocation disposition (docs/TASKS.md Phase 6; docs/USE_CASES.md
UC8-B; src/investment/writeback/writeback.py). effective_caps + gate 6 pure/async,
and the full dispose->commit (pass + block paths) against a real throwaway
SQLite, asserting the EventLog precedes the Proposal vertex."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from investment.db.seed_data import TIME_VARYING_PORTFOLIOS
from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal
from investment.mechanical.gates import cited_invariant_eligible
from investment.worker.result import ReallocationProposal
from investment.writeback import writeback as W

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


# -- pure: effective_caps + gate-6 predicate ---------------------------------


def test_effective_caps_takes_the_stricter_of_user_and_portfolio() -> None:
    caps = W.effective_caps(USER, {"max_single_asset_pct": 40.0, "max_drawdown_rule": -15.0})
    assert caps.max_single_asset_pct == 40.0  # min: portfolio stricter
    assert caps.max_drawdown_pct == -15.0  # max (both negative): portfolio stricter
    # a laxer portfolio cannot loosen the binding user cap
    laxer = W.effective_caps(USER, {"max_single_asset_pct": 90.0, "max_drawdown_rule": -40.0})
    assert laxer.max_single_asset_pct == 50.0
    assert laxer.max_drawdown_pct == -25.0


def test_cited_invariant_eligible_predicate() -> None:
    kw = {"weight_min": 0.1, "refuted_min": 4, "refuted_score": 0.35}
    assert cited_invariant_eligible("integrated", 0.7, 6, 0.83, True, **kw) is True
    assert cited_invariant_eligible("proposed", 0.7, 6, 0.83, True, **kw) is False  # not integrated
    assert cited_invariant_eligible("integrated", 0.05, 6, 0.83, True, **kw) is False  # underweight
    assert cited_invariant_eligible("integrated", 0.7, 6, 0.83, False, **kw) is False  # dormant
    assert cited_invariant_eligible("integrated", 0.7, 8, 0.30, True, **kw) is False  # refuted


# -- integration -------------------------------------------------------------


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    for tk, cls in (("SPY", "equities"), ("GLD", "gold-commodities"), ("IEF", "bonds")):
        await cmd(
            "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, "
            "active) VALUES (:t, :c, 'USD', 'yahoo', 'none', 1)",
            t=tk,
            c=cls,
        )
    invs = [
        # id, status, weight_effective, conf, infirm, score, condition
        ("inv-ok", "integrated", 0.7, 5, 1, 0.83, "[]"),
        ("inv-proposed", "proposed", 0.7, 5, 1, 0.83, "[]"),
        ("inv-refuted", "integrated", 0.5, 3, 5, 0.30, "[]"),
        (
            "inv-dormant",
            "integrated",
            0.7,
            5,
            1,
            0.83,
            '[{"signal": "inflation", "feature": "level", "op": ">", "value": 99}]',
        ),
    ]
    for iid, status, weff, conf, infirm, score, cond in invs:
        await cmd(
            "INSERT INTO invariant (id, title, description, source, status, condition, "
            "weight_initial, floor_weight, weight_effective, confirmation_count, "
            "infirmation_count, market_score, trace, created_at, updated_at) VALUES (:id, 't', "
            "'d', 's', :st, :cond, 0.5, 0.2, :w, :cc, :ic, :ms, 'tr', '2026-01-01', '2026-01-01')",
            id=iid,
            st=status,
            cond=cond,
            w=weff,
            cc=conf,
            ic=infirm,
            ms=score,
        )
    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
        "allocation, rank, market_context, recommendation, trace) VALUES ('2026-07-01', 'def-pf', "
        "1, '4s', '{\"SPY\": 50, \"GLD\": 25, \"IEF\": 25}', 1, '{}', 'maintain', 't')"
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "w.db")
    await _seed(conn)
    yield conn
    await conn.close()


def _realloc(alloc: dict[str, float], cite: list[str]) -> ReallocationProposal:
    return ReallocationProposal(
        proposed_allocation=alloc,
        scenario_delta={},
        favors_delta={},
        blend_note="0.4/0.6",
        supporting_invariants=cite,
        reasoning="gold above trend, tilt in",
    )


async def test_gate6_paths(db: InvestmentDB) -> None:
    async def g(ids: list[str]) -> str | None:
        return (await W.gate6_cited_invariants(db, ids, THRESHOLDS, "stag")).failed_gate

    assert await g([]) == "gate6_no_cited_invariant"
    assert await g(["inv-ok"]) is None  # integrated, active (always), heavy, not refuted
    assert await g(["inv-proposed"]) == "gate6_cited_invariant_eligibility"
    assert await g(["inv-refuted"]) == "gate6_cited_invariant_eligibility"
    assert await g(["inv-dormant"]) == "gate6_cited_invariant_eligibility"  # condition can't fire
    assert await g(["ghost"]) == "gate6_unknown_invariant"


async def test_dispose_pass_commits_proposal_eventlog_first(db: InvestmentDB) -> None:
    current = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
    realloc = _realloc({"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}, ["inv-ok"])
    outcome, pid = await W.dispose_reallocation(
        db, realloc, "def-pf", current, USER, THRESHOLDS, "stag", {"regime": "stag"}
    )
    assert outcome.passed is True
    assert pid is not None
    rows = await db.query("SELECT proposal_type, recommendation FROM proposal WHERE id=:i", i=pid)
    row = rows[0]
    assert row["proposal_type"] == "reallocation"
    assert row["recommendation"] == "paper-test"
    # EventLog append precedes the vertex: the ProposalEvent's ULID sorts before
    # nothing here, but it must EXIST and reference the proposal
    ev = await db.query("SELECT source_id FROM event_log WHERE type='ProposalEvent'")
    assert [e["source_id"] for e in ev] == [pid]
    # cited invariants persisted as a relation, for the +12w confrontations
    cites = await db.query("SELECT invariant_id FROM proposal_cites WHERE proposal_id=:i", i=pid)
    assert [c["invariant_id"] for c in cites] == ["inv-ok"]
    # snapshot recommendation upgraded
    snap = await db.query(
        "SELECT recommendation FROM portfolio_weekly_snapshot WHERE portfolio_id='def-pf'"
    )
    assert snap[0]["recommendation"] == "paper-test"


async def test_dispose_pass_starts_the_paper_test(db: InvestmentDB) -> None:
    """ADR-006: there is no user 'accept' step left, so a proposal that passed
    every gate IS the paper-test — `paper_started` must be set at commit, or
    outcomes.paper_test_progress() and the digest scoreboard track nothing."""
    current = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
    realloc = _realloc({"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}, ["inv-ok"])
    _outcome, pid = await W.dispose_reallocation(
        db,
        realloc,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
        today=date(2026, 7, 20),
    )
    rows = await db.query(
        "SELECT date, paper_started, created_at FROM proposal WHERE id = :i", i=pid
    )
    assert rows[0]["paper_started"] == "2026-07-20"
    # created_at is a UTC TIMESTAMP, not the business date (CLAUDE.md dev standards)
    assert str(rows[0]["created_at"]).startswith(datetime.now(UTC).strftime("%Y-%m-%dT"))
    assert str(rows[0]["created_at"]) != rows[0]["date"]


async def test_cooldown_refuses_a_near_identical_repeat(db: InvestmentDB) -> None:
    """The anti-repetition pre-gate: the same tilt again, inside
    proposal_cooldown_weeks and in the same regime, is refused."""
    current = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
    first = _realloc({"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}, ["inv-ok"])
    _o, pid = await W.dispose_reallocation(
        db,
        first,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
        today=date(2026, 7, 6),
    )
    assert pid is not None

    # 2 weeks later, the same allocation +-2 pts: no asset differs by the 5-pt
    # min-change, so it is the SAME proposal.
    repeat = _realloc({"SPY": 38.0, "GLD": 37.0, "IEF": 25.0}, ["inv-ok"])
    outcome, pid2 = await W.dispose_reallocation(
        db,
        repeat,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
        today=date(2026, 7, 20),
    )
    assert outcome.failed_gate == "cooldown_repeat_proposal"
    assert pid2 is None
    assert len(await db.query("SELECT id FROM proposal")) == 1  # nothing new persisted


async def test_cooldown_admits_a_materially_different_tilt(db: InvestmentDB) -> None:
    current = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
    first = _realloc({"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}, ["inv-ok"])
    await W.dispose_reallocation(
        db,
        first,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
        today=date(2026, 7, 6),
    )
    # GLD 35 -> 25 and IEF 25 -> 35: a 10-pt move away from the first proposal.
    different = _realloc({"SPY": 40.0, "GLD": 25.0, "IEF": 35.0}, ["inv-ok"])
    outcome, pid2 = await W.dispose_reallocation(
        db,
        different,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
        today=date(2026, 7, 20),
    )
    assert outcome.passed is True
    assert pid2 is not None


async def test_cooldown_lifts_when_the_regime_type_changed(db: InvestmentDB) -> None:
    """The escape clause docs/USE_CASES.md UC8-A keeps ("unless the regime type
    has changed"): the same tilt is a NEW argument in a new world."""
    current = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
    realloc = _realloc({"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}, ["inv-ok"])
    await W.dispose_reallocation(
        db,
        realloc,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
        today=date(2026, 7, 6),
    )
    outcome, pid2 = await W.dispose_reallocation(
        db,
        realloc,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "reflation",
        {"regime": "reflation"},
        today=date(2026, 7, 20),
    )
    assert outcome.passed is True
    assert pid2 is not None


async def test_cooldown_expires_after_the_window(db: InvestmentDB) -> None:
    current = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
    realloc = _realloc({"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}, ["inv-ok"])
    await W.dispose_reallocation(
        db,
        realloc,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
        today=date(2026, 5, 4),
    )
    outcome, pid2 = await W.dispose_reallocation(  # 11 weeks later, well past the 4
        db,
        realloc,
        "def-pf",
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
        today=date(2026, 7, 20),
    )
    assert outcome.passed is True
    assert pid2 is not None


async def test_dispose_blocks_on_concentration_without_persisting(db: InvestmentDB) -> None:
    current = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
    realloc = _realloc({"SPY": 60.0, "GLD": 20.0, "IEF": 20.0}, ["inv-ok"])  # 60 > 50 cap
    outcome, pid = await W.dispose_reallocation(
        db, realloc, "def-pf", current, USER, THRESHOLDS, "stag", {}
    )
    assert outcome.failed_gate == "max_single_asset_pct"
    assert pid is None
    assert await db.query("SELECT id FROM proposal") == []  # nothing persisted


async def test_dispose_blocks_on_gate6_ineligible_citation(db: InvestmentDB) -> None:
    current = {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0}
    realloc = _realloc({"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}, ["inv-proposed"])
    outcome, pid = await W.dispose_reallocation(
        db, realloc, "def-pf", current, USER, THRESHOLDS, "stag", {}
    )
    assert outcome.failed_gate == "gate6_cited_invariant_eligibility"
    assert pid is None
    assert await db.query("SELECT id FROM proposal") == []


# -- gate 0: mechanical sovereignty (ADR-011) --------------------------------


async def test_gate0_refuses_a_reallocation_aimed_at_a_mechanical_portfolio(
    db: InvestmentDB,
) -> None:
    """ADR-011. The Worker's reallocation lever lands on whatever carries the
    `defender` flag, and until this gate the separation from the adopted
    market-signal stack held only because `ms-stack.defender` happens to be
    False — a data flag, checked by nothing. Retiring the bridge
    (docs/V1_STRATEGY.md Step 6) is precisely when that flag flips.

    The proposal used here is otherwise IMPECCABLE: it would pass every cap,
    the turnover ceiling, the min-change floor and gate 6. Only its target is
    wrong, and that alone must refuse it."""
    current = {"SPY": 50.0, "IWN": 40.0, "GLD": 10.0}
    realloc = _realloc({"SPY": 40.0, "IWN": 35.0, "GLD": 25.0}, ["inv-ok"])
    outcome, pid = await W.dispose_reallocation(
        db,
        realloc,
        market_signal.STACK_PORTFOLIO_ID,
        current,
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
    )
    assert outcome.passed is False
    assert outcome.failed_gate == "mechanical_allocation"
    assert pid is None
    # Nothing persisted — not even the EventLog, since gate 0 precedes commit.
    assert await db.query("SELECT id FROM proposal") == []
    assert await db.query("SELECT id FROM event_log WHERE type='ProposalEvent'") == []


async def test_gate0_runs_before_the_merit_gates(db: InvestmentDB) -> None:
    """Jurisdiction, not merit: a proposal that ALSO breaches a cap must be
    refused for the reason that actually disqualifies it, or the digest would
    report a fixable cap problem and hide the fact that the Worker aimed at
    something it may never touch."""
    realloc = _realloc({"SPY": 90.0, "GLD": 10.0}, ["inv-ok"])  # 90 breaches the 50 cap
    outcome, _ = await W.dispose_reallocation(
        db,
        realloc,
        market_signal.STACK_PORTFOLIO_ID,
        {"SPY": 50.0, "IWN": 40.0, "GLD": 10.0},
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
    )
    assert outcome.failed_gate == "mechanical_allocation"


async def test_gate0_leaves_the_cognitive_bridge_defender_alone(db: InvestmentDB) -> None:
    """The retained bridge stays the Worker's, deliberately (ADR-011): it is the
    benchmark the adopted strategy is measured against, and a benchmark the
    Worker cannot influence is a different experiment."""
    assert "def-pf" not in TIME_VARYING_PORTFOLIOS
    outcome, pid = await W.dispose_reallocation(
        db,
        _realloc({"SPY": 40.0, "GLD": 35.0, "IEF": 25.0}, ["inv-ok"]),
        "def-pf",
        {"SPY": 50.0, "GLD": 25.0, "IEF": 25.0},
        USER,
        THRESHOLDS,
        "stag",
        {"regime": "stag"},
    )
    assert outcome.passed is True and pid is not None
