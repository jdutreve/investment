"""Strategy probation + paper-test tracking (docs/ARCHITECTURE.md
"strategy_probation_check"; src/investment/mechanical/outcomes.py). Against a
real throwaway SQLite."""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import outcomes

TODAY = date(2026, 7, 20)
OLD = (TODAY - timedelta(weeks=13)).isoformat()  # past the 12w probation window


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    await cmd(
        "INSERT INTO system_thresholds (key, value, updated_at) "
        "VALUES ('strategy_probation_weeks', 12.0, '2026-01-01')"
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
    # two agent-discovery strategies activated 13w ago, one seeded baseline
    for sid, source, opened in (
        ("s-good", "agent-discovery", OLD),
        ("s-bad", "agent-discovery", OLD),
        ("s-seed", "corpus", OLD),
    ):
        await cmd(
            "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
            "conditions, source, status, date_opened, trace, created_at, updated_at) VALUES "
            "(:id, 't', 'd', '4s', 60, 1, 'c', :src, 'active', :o, 'tr', '2026-01-01', "
            "'2026-01-01')",
            id=sid,
            src=source,
            o=opened,
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


async def test_probation_is_idempotent(db: InvestmentDB) -> None:
    first = await outcomes.strategy_probation_check(db, today=TODAY)
    assert len(first) == 2
    again = await outcomes.strategy_probation_check(db, today=TODAY)
    assert again == []  # already judged — not re-emitted
    events = await db.query(
        "SELECT count(*) AS n FROM event_log WHERE type = 'OutcomeEvent' "
        "AND json_extract(payload, '$.kind') = 'probation'"
    )
    assert events[0]["n"] == 2  # still just the two


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
