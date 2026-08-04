"""Strategy probation + paper-test tracking (docs/ARCHITECTURE.md
"strategy_probation_check" + "System Evolution"; src/investment/mechanical/
outcomes.py). Against a real throwaway SQLite.

An innovation-born strategy is `status='proposed', enabled=0` for the whole
probation window and the verdict is APPLIED mechanically (ADR-006): 'keep'
activates the vertex with its Scenarios and BACKED_BY edges, 'review' closes it.
"""

import json
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import outcomes

TODAY = date(2026, 7, 20)
OLD = (TODAY - timedelta(weeks=13)).isoformat()  # past the 12w probation window

# What writeback._commit_strategy_innovation puts in the InnovationEvent payload:
# activation reads the spec back from here to build the Scenarios + BACKED_BY.
# A spec that SATISFIES the activation contract (docs/ARCHITECTURE.md: the 3
# bull/base/bear definitions, probabilities summing to 100, each
# target_allocation summing to 100 and complying with the binding user caps).
# `backed_by` is the documented key for the cited invariants — `cites` appears
# in no spec and no prompt, and reading it is what silently dropped every
# BACKED_BY edge.
SPEC = {
    "scenarios": [
        {"name": "bull", "probability": 25, "target_allocation": {"SPY": 50, "GLD": 50}},
        {"name": "base", "probability": 50, "target_allocation": {"SPY": 50, "GLD": 50}},
        {"name": "bear", "probability": 25, "target_allocation": {"SPY": 30, "GLD": 50, "IEF": 20}},
    ],
    "backed_by": ["inv-gold", "inv-ghost"],  # the ghost must be skipped, not raise
}


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO system_thresholds (key, value, updated_at) "
        "VALUES ('strategy_probation_weeks', 12.0, '2026-01-01')"
    )
    # Activation now checks each scenario book against the BINDING caps and the
    # tradable universe, so probation needs both to reach a verdict at all.
    await cmd(
        "INSERT INTO user_profile (user_id, currency, benchmark, max_drawdown_pct, "
        "max_single_asset_pct, phase, created_at, updated_at) VALUES ('u', 'USD', 'SPY', "
        "-25.0, 50.0, 'accumulation', '2026-01-01', '2026-01-01')"
    )
    for ticker, asset_class in (("SPY", "equities"), ("GLD", "gold-commodities"), ("IEF", "bonds")):
        await cmd(
            "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, "
            "active) VALUES (:t, :c, 'USD', 'yahoo', 'none', 1)",
            t=ticker,
            c=asset_class,
        )
    await cmd(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime_type (id, name, aliases, framework_id, description, created_at) "
        "VALUES ('stag', 'Stag', '[]', '4s', 'd', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO regime (id, regime_type_id, tags, start_date, is_current, events, trace, "
        "created_at, updated_at) VALUES ('r1', 'stag', '[]', '2026-01-01', 1, '[]', 't', "
        "'2026-01-01', '2026-01-01')"
    )
    await cmd(
        "INSERT INTO invariant (id, title, description, source, status, condition, "
        "weight_initial, floor_weight, weight_effective, confirmation_count, infirmation_count, "
        "market_score, trace, created_at, updated_at) VALUES ('inv-gold', 't', 'd', 's', "
        "'integrated', '[]', 0.5, 0.2, 0.7, 5, 1, 0.83, 'tr', '2026-01-01', '2026-01-01')"
    )
    # two innovation-born strategies past their window (proposed, disabled), one
    # seeded baseline (never enters probation), and the superseded incumbent.
    for sid, source, status, enabled in (
        ("s-good", "agent-discovery", "proposed", 0),
        ("s-bad", "agent-discovery", "proposed", 0),
        ("s-seed", "corpus", "active", 1),
        ("s-old", "corpus", "active", 1),
    ):
        await cmd(
            "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
            "conditions, source, status, date_opened, trace, created_at, updated_at) VALUES "
            "(:id, 't', 'd', '4s', 60, :en, 'c', :src, :st, :o, 'tr', '2026-01-01', "
            "'2026-01-01')",
            id=sid,
            src=source,
            st=status,
            en=enabled,
            o=OLD,
        )
    # the InnovationEvents that gave birth to them (s-good supersedes s-old)
    for sid, spec in (("s-good", {**SPEC, "supersedes": "s-old"}), ("s-bad", SPEC)):
        await db.append_event(
            type="InnovationEvent",
            source_uc="UC8",
            source_id=sid,
            payload={"type": "new_strategy", "title": "t", "spec": spec},
            event_date=date.fromisoformat(OLD),
        )
    # FAVORS in the current regime: s-good above the median, s-bad below
    for sid, sortino in (("s-good", 1.4), ("s-bad", 0.3), ("s-seed", 0.9)):
        await cmd(
            "INSERT INTO favors (regime_type_id, strategy_id, sortino_rolling, sharpe_rolling, "
            "calmar_rolling, max_drawdown, n_periods, last_updated) VALUES ('stag', :id, :s, 0.5, "
            "1.0, -0.1, 40, '2026-01-01')",
            id=sid,
            s=sortino,
        )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "p.db")
    await _seed(conn)
    yield conn
    await conn.close()


async def test_probation_keeps_the_top_half_reviews_the_rest(db: InvestmentDB) -> None:
    results = await outcomes.strategy_probation_check(db, today=TODAY)
    verdicts = {r.strategy_id: r.verdict for r in results}
    # median of {1.4, 0.3, 0.9} = 0.9: s-good (1.4) keeps, s-bad (0.3) reviews.
    # s-seed is source='corpus' -> never enters probation.
    assert verdicts == {"s-good": "keep", "s-bad": "review"}
    events = await db.query(
        "SELECT source_id, json_extract(payload, '$.verdict') AS v FROM event_log "
        "WHERE type = 'OutcomeEvent' AND json_extract(payload, '$.kind') = 'probation'"
    )
    assert {(e["source_id"], e["v"]) for e in events} == {("s-good", "keep"), ("s-bad", "review")}


async def test_probation_pass_activates_with_scenarios_and_edges(db: InvestmentDB) -> None:
    await outcomes.strategy_probation_check(db, today=TODAY)

    rows = await db.query("SELECT status, enabled FROM strategy WHERE id = 's-good'")
    assert (rows[0]["status"], rows[0]["enabled"]) == ("active", 1)

    scenarios = await db.query(
        "SELECT name, probability, target_allocation FROM scenario "
        "WHERE strategy_id = 's-good' ORDER BY name"
    )
    assert [s["name"] for s in scenarios] == ["base", "bear", "bull"]
    assert json.loads(str(scenarios[2]["target_allocation"])) == {"SPY": 50, "GLD": 50}

    # BACKED_BY only for the invariant that EXISTS — the ghost is skipped, not an
    # IntegrityError that would abort the activation.
    backed = await db.query("SELECT invariant_id FROM backed_by WHERE strategy_id = 's-good'")
    assert [b["invariant_id"] for b in backed] == ["inv-gold"]

    # the superseded incumbent is closed, in the same transaction
    old = await db.query("SELECT status, enabled, date_revised FROM strategy WHERE id = 's-old'")
    assert (old[0]["status"], old[0]["enabled"], old[0]["date_revised"]) == (
        "closed",
        0,
        TODAY.isoformat(),
    )


async def test_probation_fail_closes_the_strategy(db: InvestmentDB) -> None:
    await outcomes.strategy_probation_check(db, today=TODAY)
    rows = await db.query("SELECT status, enabled, trace FROM strategy WHERE id = 's-bad'")
    assert (rows[0]["status"], rows[0]["enabled"]) == ("closed", 0)
    assert "probation" in str(rows[0]["trace"])  # the reason is on the vertex
    # a failed revision leaves no scenarios behind
    assert await db.query("SELECT id FROM scenario WHERE strategy_id = 's-bad'") == []


async def test_probation_is_idempotent(db: InvestmentDB) -> None:
    first = await outcomes.strategy_probation_check(db, today=TODAY)
    assert len(first) == 2
    again = await outcomes.strategy_probation_check(db, today=TODAY)
    assert again == []  # already judged — not re-emitted, and no second transition
    events = await db.query(
        "SELECT count(*) AS n FROM event_log WHERE type = 'OutcomeEvent' "
        "AND json_extract(payload, '$.kind') = 'probation'"
    )
    assert events[0]["n"] == 2  # still just the two


async def test_probation_waits_while_the_window_is_open(db: InvestmentDB) -> None:
    """A strategy born 4 weeks ago is not judged at all: probation is a WINDOW,
    and judging early would close a strategy for lack of history."""
    early = date.fromisoformat(OLD) + timedelta(weeks=4)
    assert await outcomes.strategy_probation_check(db, today=early) == []
    rows = await db.query("SELECT status FROM strategy WHERE id = 's-good'")
    assert rows[0]["status"] == "proposed"  # still maturing


async def test_paper_test_progress_lists_live_accepted_tests(db: InvestmentDB) -> None:
    # a proposal accepted as a paper-test, still pending
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, recommendation, "
        "market_context, reasoning, paper_started, trace, created_at) VALUES ('pt', '2026-06-01', "
        "'reallocation', 'd', 'paper-test', '{}', 'r', '2026-06-01', 't', '2026-06-01')"
    )
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, recommendation, "
        "market_context, reasoning, outcome, trace, created_at) VALUES ('done', '2026-01-01', "
        "'switch', 'd', 'monitor', '{}', 'r', '{\"verdict\": \"won\"}', 't', '2026-01-01')"
    )
    progress = await outcomes.paper_test_progress(db, today=TODAY)
    # only the live accepted paper-test is tracked (the decided one is excluded)
    assert [p["proposal_id"] for p in progress] == ["pt"]


async def test_a_strategy_scored_in_ANOTHER_regime_is_not_closed_as_unmeasurable(
    db: InvestmentDB,
) -> None:
    """The backstop's reason line was "no FAVORS in any regime", but the query
    behind it only ever looked at the CURRENT one. A strategy scored in
    stagflation and unscored in today's regime is MEASURABLE — the system simply
    has not been in its regime — and closing it at 24 weeks made a claim its own
    evidence contradicted."""
    await db.command(
        "INSERT INTO regime_type (id, name, aliases, framework_id, description, created_at) "
        "VALUES ('disinf', 'Disinflation', '[]', '4s', 'd', '2026-01-01')"
    )
    await db.command(
        "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
        "conditions, source, status, date_opened, trace, created_at, updated_at) VALUES "
        "('s-elsewhere', 't', 'd', '4s', 60, 0, 'c', 'agent-discovery', 'proposed', :o, 'tr', "
        "'2026-01-01', '2026-01-01')",
        # older than UNMEASURABLE_PROBATION_MULTIPLIER windows, so the backstop is live
        o=(TODAY - timedelta(weeks=30)).isoformat(),
    )
    # scored in 'disinf', absent from 'stag' — the regime probation reads
    await db.command(
        "INSERT INTO favors (regime_type_id, strategy_id, sortino_rolling, sharpe_rolling, "
        "calmar_rolling, max_drawdown, n_periods, last_updated) VALUES ('disinf', 's-elsewhere', "
        "1.1, 0.5, 1.0, -0.1, 40, '2026-01-01')"
    )
    results = await outcomes.strategy_probation_check(db, today=TODAY)
    mine = [r for r in results if r.strategy_id == "s-elsewhere"]
    assert len(mine) == 1
    assert mine[0].verdict == ""  # waiting, not judged
    assert mine[0].skipped_reason == "scored in another regime, waiting for this one"
    status = await db.query("SELECT status FROM strategy WHERE id = 's-elsewhere'")
    assert status[0]["status"] == "proposed"  # NOT closed


async def test_a_strategy_scored_NOWHERE_is_still_closed_by_the_backstop(
    db: InvestmentDB,
) -> None:
    """The other half: no FAVORS anywhere is the genuine unmeasurability
    ADR-006 requires terminated — a defect of the strategy's own that no later
    run repairs."""
    await db.command(
        "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
        "conditions, source, status, date_opened, trace, created_at, updated_at) VALUES "
        "('s-nowhere', 't', 'd', '4s', 60, 0, 'c', 'agent-discovery', 'proposed', :o, 'tr', "
        "'2026-01-01', '2026-01-01')",
        o=(TODAY - timedelta(weeks=30)).isoformat(),
    )
    results = await outcomes.strategy_probation_check(db, today=TODAY)
    mine = [r for r in results if r.strategy_id == "s-nowhere"]
    assert len(mine) == 1 and mine[0].verdict == "review"
    status = await db.query("SELECT status FROM strategy WHERE id = 's-nowhere'")
    assert status[0]["status"] == "closed"


# -- the activation contract (docs/ARCHITECTURE.md innovation spec) ----------


async def _propose(db: InvestmentDB, sid: str, spec: dict[str, object]) -> None:
    await db.command(
        "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
        "conditions, source, status, date_opened, trace, created_at, updated_at) VALUES "
        "(:id, 't', 'd', '4s', 60, 0, 'c', 'agent-discovery', 'proposed', :o, 'tr', "
        "'2026-01-01', '2026-01-01')",
        id=sid,
        o=OLD,
    )
    await db.append_event(
        type="InnovationEvent",
        source_uc="UC8",
        source_id=sid,
        payload={"type": "new_strategy", "title": "t", "spec": spec},
        event_date=date.fromisoformat(OLD),
    )
    # FAVORS above the peer median, so probation says 'keep' on the merits and
    # only the spec contract can stop the activation.
    await db.command(
        "INSERT INTO favors (regime_type_id, strategy_id, sortino_rolling, sharpe_rolling, "
        "calmar_rolling, max_drawdown, n_periods, last_updated) VALUES ('stag', :id, 9.9, 0.5, "
        "1.0, -0.1, 40, '2026-01-01')",
        id=sid,
    )


def _spec_with(scenarios: list[dict[str, object]]) -> dict[str, object]:
    return {"scenarios": scenarios, "backed_by": []}


@pytest.mark.parametrize(
    ("label", "scenarios"),
    [
        (
            "probabilities sum to 270",
            [
                {"name": n, "probability": 90, "target_allocation": {"SPY": 50, "GLD": 50}}
                for n in ("bull", "base", "bear")
            ],
        ),
        (
            "a short leg",
            [
                {"name": "bull", "probability": 25, "target_allocation": {"SPY": 130, "GLD": -30}},
                {"name": "base", "probability": 50, "target_allocation": {"SPY": 50, "GLD": 50}},
                {"name": "bear", "probability": 25, "target_allocation": {"SPY": 50, "GLD": 50}},
            ],
        ),
        (
            "a NaN weight",
            [
                {
                    "name": "bull",
                    "probability": 25,
                    "target_allocation": {"SPY": 50, "GLD": float("nan")},
                },
                {"name": "base", "probability": 50, "target_allocation": {"SPY": 50, "GLD": 50}},
                {"name": "bear", "probability": 25, "target_allocation": {"SPY": 50, "GLD": 50}},
            ],
        ),
        (
            "over the concentration cap",
            [
                {"name": "bull", "probability": 25, "target_allocation": {"SPY": 40, "GLD": 60}},
                {"name": "base", "probability": 50, "target_allocation": {"SPY": 50, "GLD": 50}},
                {"name": "bear", "probability": 25, "target_allocation": {"SPY": 50, "GLD": 50}},
            ],
        ),
        (
            "an untradable ticker",
            [
                {
                    "name": "bull",
                    "probability": 25,
                    "target_allocation": {"SPY": 50, "MOONCOIN": 50},
                },
                {"name": "base", "probability": 50, "target_allocation": {"SPY": 50, "GLD": 50}},
                {"name": "bear", "probability": 25, "target_allocation": {"SPY": 50, "GLD": 50}},
            ],
        ),
        (
            "only two scenarios",
            [
                {"name": "bull", "probability": 50, "target_allocation": {"SPY": 50, "GLD": 50}},
                {"name": "base", "probability": 50, "target_allocation": {"SPY": 50, "GLD": 50}},
            ],
        ),
    ],
)
async def test_a_malformed_spec_is_closed_not_activated(
    db: InvestmentDB, label: str, scenarios: list[dict[str, object]]
) -> None:
    """Every one of these persisted straight into `scenario` before the contract
    was enforced, and `scenarios.py` plus the reallocation blend then read them
    as fact."""
    await _propose(db, "s-bad-spec", _spec_with(scenarios))
    results = await outcomes.strategy_probation_check(db, today=TODAY)
    mine = next(r for r in results if r.strategy_id == "s-bad-spec")
    assert mine.verdict == "review", label
    row = (await db.query("SELECT status, trace FROM strategy WHERE id = 's-bad-spec'"))[0]
    assert row["status"] == "closed", label
    assert "malformed scenario spec" in str(row["trace"]), label
    # nothing half-written: no scenario row survived the refusal
    assert await db.query("SELECT id FROM scenario WHERE strategy_id = 's-bad-spec'") == []


async def test_the_journal_records_the_verdict_that_was_acted_on(db: InvestmentDB) -> None:
    """The OutcomeEvent is appended BEFORE the vertex writes, so the spec check
    has to run first — deciding after the journal is written is how a log comes
    to say 'keep' over a closed strategy."""
    await _propose(
        db,
        "s-bad-spec",
        _spec_with(
            [
                {"name": n, "probability": 90, "target_allocation": {"SPY": 50, "GLD": 50}}
                for n in ("bull", "base", "bear")
            ]
        ),
    )
    await outcomes.strategy_probation_check(db, today=TODAY)
    events = await db.query(
        "SELECT json_extract(payload, '$.verdict') AS v, "
        "json_extract(payload, '$.spec_defect') AS d FROM event_log "
        "WHERE type = 'OutcomeEvent' AND source_id = 's-bad-spec'"
    )
    assert events[0]["v"] == "review"  # not 'keep'
    assert "sum to 270" in str(events[0]["d"])  # and WHY, distinctly from a merits loss


async def test_backed_by_edges_are_read_from_the_documented_spec_key(db: InvestmentDB) -> None:
    """`cites` appears in no spec and no prompt: a doc-conforming innovation
    used to activate with no invariant behind it, leaving the confrontation
    machinery nothing to credit or blame."""
    results = await outcomes.strategy_probation_check(db, today=TODAY)
    assert next(r for r in results if r.strategy_id == "s-good").verdict == "keep"
    edges = await db.query("SELECT invariant_id FROM backed_by WHERE strategy_id = 's-good'")
    assert [e["invariant_id"] for e in edges] == ["inv-gold"]  # the ghost skipped, not raised
