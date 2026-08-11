"""Weekly digest render (docs/TASKS.md Task 6bis.1;
src/investment/telegram/digest.py). Pure rendering asserted line by line, plus
scoreboard assembly from a seeded proposal ledger."""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.telegram import digest as D


def test_pct_formats_fractions_and_none() -> None:
    assert D.pct(0.038) == "3.8%"
    assert D.pct(0.038, signed=True) == "+3.8%"
    assert D.pct(-0.182) == "-18.2%"
    assert D.pct(None) == "n/a"


def _digest(**over: object) -> str:
    kwargs: dict[str, object] = {
        "regime": {"regime_name": "Stagflation", "regime_type_id": "stag", "confidence": 78.0},
        # Full-precision REALs, as they come off the DB — the live values that
        # exposed the raw-repr rendering. A round 98.4 would have hidden it.
        "global_liquidity": {"level": 95.83616874214097, "speed": -0.80},
        "ranking": [
            {
                "rank": 1,
                "portfolio_id": "4S Balanced",
                "defender": 1,
                "sortino_rolling": 1.18,
                "calmar_rolling": 1.9,
            },
            {
                "rank": 2,
                "portfolio_id": "Momentum",
                "defender": 0,
                "sortino_rolling": 0.31,
                "calmar_rolling": 0.6,
                "max_drawdown": -0.182,
                # READ from the snapshot, not recomputed: it was decided against
                # the cap in force on that date (db/schema.py).
                "excluded_from_candidacy": 1,
                # NO `demoted` key, and its absence is the assertion: the
                # snapshot rows the ranking job writes have no such column, so
                # the digest must derive the demotion from `calmar_rolling`
                # (snapshots.is_demoted). Handing it a flag here is what kept
                # the unreachable warning looking tested.
            },
        ],
        "invariants": [
            {
                "title": "TIPS inflation persistence",
                "weight_effective": 0.756,
                "confirmation_count": 8,
                "infirmation_count": 1,
                "author": "dalio",
            },
        ],
        "proposal": {
            "proposal_type": "switch",
            "recommendation": "switch",
            "date": "2026-07-20",
            "challenger_id": "chal-pf",
            "defender_id": "def-pf",
            "reasoning": "bear scenario 55% (+35pts); gold tilt backed by GLD invariant",
        },
        "scoreboard": {
            "hit_rate": (1, 1),
            "paper_tests": [
                {
                    "proposal_id": "p-42",
                    "proposed_return": 0.061,
                    "incumbent_return": 0.038,
                    "excess": 0.023,
                },
            ],
            "probations": [],
            "calibration_flags": [],
        },
        "defender_metrics": {
            "sharpe_rolling": 0.6479648177000503,  # ditto — the live value
            "sortino_rolling": 1.18,
            "calmar_rolling": 1.9,
            "return_3m": 0.038,
            "return_1y": 0.143,
        },
    }
    kwargs.update(over)
    return D.render_digest(**kwargs)  # type: ignore[arg-type]


def test_render_is_complete_and_readable() -> None:
    text = _digest()
    # regime header with percent-formatted confidence
    assert "Regime: Stagflation (78.0% — stag)" in text
    # ranking with defender star and a demoted warning
    assert "1. 4S Balanced: 1.18 ★ (defender)  Calmar 1.9" in text
    assert "⚠️ (demoted: Calmar 0.6 below 1.0)" in text
    # ... and the eligible row above it carries no warning at all
    assert "1.9 ⚠️" not in text
    # the drawdown breach is a SEPARATE rule: it restricts, it does not demote
    assert "⛔ excluded from defender role and proposal candidacy (drawdown -18.2%)" in text
    # invariant with weight (decimal, not percent) + confirmed counts
    assert "TIPS inflation persistence: 0.756 (8/9 confirmed) [dalio]" in text
    # the bridge's switch slot (ADR-012 removed the reallocation branch)
    assert "🔀 Switch proposal (switch)" in text
    # scoreboard hit-rate + paper-tests
    assert "Proposals hit-rate: 1/1 (100.0%) at +12w" in text
    assert "Paper-tests in progress: 1" in text
    # ... each with its running proposed-vs-incumbent (docs/TASKS.md Task 6bis.1)
    assert "p-42: +2.3% vs incumbent since paper_started" in text
    # every indicator formatted, never a raw float repr — this file's one job
    assert "Global liquidity: level 95.84, speed -0.80" in text
    assert "Defender (USD, 36M): Sharpe 0.65 | Sortino 1.18 | Calmar 1.90" in text
    # defender returns, signed percentages
    assert "3m +3.8%" in text and "1y +14.3%" in text


def test_no_proposal_reads_as_maintain() -> None:
    text = _digest(proposal=None)
    assert "No bridge proposal this week — maintain." in text


# -- scoreboard assembly -----------------------------------------------------


async def _add(db: InvestmentDB, pid: str, verdict: str | None, paper: str | None) -> None:
    outcome = None if verdict is None else f'{{"verdict": "{verdict}"}}'
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, recommendation, "
        "market_context, reasoning, outcome, paper_started, trace, created_at) VALUES "
        "(:id, '2026-01-01', 'switch', 'd', 'monitor', '{}', 'r', :o, :p, 't', '2026-01-01')",
        id=pid,
        o=outcome,
        p=paper,
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "d.db")
    yield conn
    await conn.close()


async def test_build_scoreboard_counts_the_ledger(db: InvestmentDB) -> None:
    await _add(db, "p1", "won", None)
    await _add(db, "p2", "won", None)
    await _add(db, "p3", "lost", None)
    await _add(db, "p4", "pending", "2026-01-01")  # accepted paper-test, still running
    await _add(db, "p5", None, None)  # not yet evaluated
    board = await D.build_scoreboard(db)
    assert board["hit_rate"] == (2, 3)  # 2 won of 3 decided (pending/null excluded)
    assert len(board["paper_tests"]) == 1  # p4


async def test_scoreboard_counts_strategies_in_probation_not_past_it(db: InvestmentDB) -> None:
    """In probation = born of an innovation, `status='proposed'`, not yet judged.
    An activated one is PAST probation and must not be counted."""
    await db.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    for sid, source, status in (
        ("in-probation", "agent-discovery", "proposed"),
        ("activated", "agent-discovery", "active"),
        ("seeded", "corpus", "proposed"),  # not innovation-born
    ):
        await db.command(
            "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
            "conditions, source, status, date_opened, trace, created_at, updated_at) VALUES "
            "(:id, 't', 'd', '4s', 60, 0, 'c', :src, :st, '2026-01-01', 'tr', '2026-01-01', "
            "'2026-01-01')",
            id=sid,
            src=source,
            st=status,
        )
    board = await D.build_scoreboard(db)
    assert [r["id"] for r in board["probations"]] == ["in-probation"]


# -- the DB -> digest assembler ----------------------------------------------


async def test_build_digest_renders_from_the_db_alone(db: InvestmentDB) -> None:
    """M8 DoV "digest rendered in terminal": every input comes from committed
    rows, so no caller has to reassemble the payload by hand."""
    await db.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await db.command(
        "INSERT INTO regime_type (id, name, aliases, framework_id, description, created_at) "
        "VALUES ('stag', 'Stagflation', '[]', '4s', 'd', '2026-01-01')"
    )
    await db.command(
        "INSERT INTO regime (id, regime_type_id, tags, start_date, is_current, events, confidence, "
        "trace, created_at, updated_at) VALUES ('r1', 'stag', '[]', '2026-06-01', 1, '[]', 78.0, "
        "'t', '2026-06-01', '2026-06-01')"
    )
    await db.command(
        "INSERT INTO market_data (ticker, asset_class, currency, ts, level, speed) "
        "VALUES ('GLOBAL_LIQUIDITY', 'MACRO', 'USD', '2026-07-20', 98.4, -0.8)"
    )
    await db.command(
        "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
        "allocation, rank, sortino_rolling, calmar_rolling, return_3m, market_context, "
        "recommendation, trace) VALUES ('2026-07-20', 'def-pf', 1, '4s', "
        "'{\"SPY\": 50, \"GLD\": 25, \"IEF\": 25}', 1, 1.18, 1.9, 0.038, '{}', 'paper-test', 't')"
    )
    await db.command(
        "INSERT INTO invariant (id, title, description, source, status, condition, weight_initial, "
        "floor_weight, weight_effective, confirmation_count, infirmation_count, market_score, "
        "author, trace, created_at, updated_at) VALUES ('inv-gold', 'GLD stagflation hedge', 'd', "
        "'s', 'integrated', '[]', 0.5, 0.2, 0.756, 8, 1, 0.89, 'dalio', 'tr', '2026-01-01', "
        "'2026-01-01')"
    )
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
        "recommendation, market_context, reasoning, paper_started, trace, created_at) VALUES "
        "('pr1', '2026-07-20', 'switch', 'def-pf', "
        "'{\"SPY\": 40, \"GLD\": 35, \"IEF\": 25}', 'switch', '{}', 'tilt into gold', "
        "'2026-07-20', 't', '2026-07-20T09:00:00+00:00')"
    )

    # Pinned to the proposal's own Monday: the bridge slot is windowed on the
    # digest's date, so an undated `build_digest()` here would (correctly) drop a
    # proposal from 2026-07-20 rather than reprint it as this week's.
    text = await D.build_digest(db, today=date(2026, 7, 20))
    assert "Regime: Stagflation (78.0% — stag)" in text
    assert "def-pf: 1.18 ★ (defender)" in text
    assert "GLD stagflation hedge: 0.756 (8/9 confirmed) [dalio]" in text
    # the bridge's switch slot; ADR-012 removed the reallocation branch that
    # used to render an old->new move here
    assert "🔀 Switch proposal" in text
    assert "Paper-tests in progress: 1" in text
    assert "3m +3.8%" in text


async def test_build_digest_on_an_empty_db_says_no_proposal(db: InvestmentDB) -> None:
    text = await D.build_digest(db)
    assert "No bridge proposal this week — maintain." in text
    assert "Proposals hit-rate: 0/0" in text


# -- the Worker's reading of the mechanical decision (ADR-011) ---------------


_DECISION = {
    "decision_date": "2026-08-03",
    "held_book": "credit-spread-wide",
    "signal_state": "credit-spread-wide",
    "gate": "passed",
    "held_allocation": {"SPY": 50, "IWN": 40, "GLD": 10},
    "target_allocation": {"SPY": 50, "IWN": 40, "GLD": 10},
    "signals": {},
    "trend_overlay": {"below_trend": []},
}


def test_the_worker_challenge_renders_inside_the_market_signal_block() -> None:
    """ADR-011 promises the Worker's reading is "journalled and RENDERED". It
    belongs in the decision's own block: a critique printed three blocks away
    from its subject is not a reading, it is a loose opinion."""
    text = _digest(
        market_signal=_DECISION,
        worker_reading={
            "market_signal_decision_date": "2026-08-03",
            "market_signal_assessment": "right book for the spread, blind to the fiscal impulse",
        },
    )
    assert "🗣 Worker challenge: right book for the spread" in text
    block = text.split("🧭 Market-signal decision")[1].split("🧱")[0]
    assert "Worker challenge" in block  # inside the decision's block, not after it


def test_a_reading_of_a_previous_decision_is_shown_but_flagged() -> None:
    """The decision is monthly and the Worker runs weekly, so a failed or skipped
    cognitive cycle leaves the latest reading pointing at LAST month's book.
    Dropping it silently would recreate the disappearance this feature exists to
    fix, one field over — so it prints with its own date attached."""
    text = _digest(
        market_signal=_DECISION,
        worker_reading={
            "market_signal_decision_date": "2026-07-06",
            "market_signal_assessment": "the steep-curve book is late",
        },
    )
    assert "reading of the 2026-07-06 decision — NOT the one above" in text


def test_no_reading_and_an_empty_reading_both_render_nothing() -> None:
    """A Worker that said nothing must not print an empty bullet — and the
    pre-UC8 state (mechanical path run, cognitive path never) is normal."""
    assert "Worker challenge" not in _digest(market_signal=_DECISION)
    assert "Worker challenge" not in _digest(
        market_signal=_DECISION,
        worker_reading={
            "market_signal_decision_date": "2026-08-03",
            "market_signal_assessment": "",
        },
    )


async def test_a_proposal_from_a_past_week_is_not_reprinted_as_this_weeks(
    db: InvestmentDB,
) -> None:
    """The slot took the latest bridge proposal in the WHOLE ledger, undated, so
    once any had ever been emitted the digest reprinted it every Monday under
    "🔧 Reallocation proposal (paper-test)" — indistinguishable from one decided
    that morning, on the page the owner places orders from. Under ADR-007 the
    bridge proposes rarely, so this was the common case, not the corner one."""
    await _snapshot(db)
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
        "recommendation, market_context, reasoning, paper_started, trace, created_at) VALUES "
        "('old-1', '2026-01-05', 'reallocation', 'def-pf', '{\"SPY\": 100}', 'paper-test', "
        "'{}', 'a tilt from six months ago', '2026-01-05', 't', '2026-01-05T09:00:00+00:00')"
    )
    text = await D.build_digest(db, today=date(2026, 7, 20))
    assert "a tilt from six months ago" not in text
    assert "No bridge proposal this week — maintain." in text


async def test_a_proposal_inside_the_window_is_shown_with_its_date(db: InvestmentDB) -> None:
    """The positive control, and the date the render now carries — a UC9 ad-hoc
    re-run's mid-week proposal is inside the window and must still appear."""
    await _snapshot(db)
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
        "recommendation, market_context, reasoning, paper_started, trace, created_at) VALUES "
        "('mid-1', '2026-07-22', 'reallocation', 'def-pf', '{\"SPY\": 100}', 'paper-test', "
        "'{}', 'an ad-hoc tilt', '2026-07-22', 't', '2026-07-22T14:00:00+00:00')"
    )
    text = await D.build_digest(db, today=date(2026, 7, 24))
    assert "an ad-hoc tilt" in text
    assert "decided 2026-07-22" in text


async def _snapshot(db: InvestmentDB) -> None:
    """The defender's ranking row — `_latest_proposal` resolves
    `current_allocation` off it, so the reallocation block needs one to render."""
    await db.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await db.command(
        "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
        "allocation, rank, sortino_rolling, calmar_rolling, market_context, recommendation, "
        "trace) VALUES ('2026-07-20', 'def-pf', 1, '4s', '{\"SPY\": 50, \"GLD\": 50}', 1, "
        "1.18, 1.9, '{}', 'maintain', 't')"
    )


async def test_recurring_critiques_survive_commas_in_their_titles(db: InvestmentDB) -> None:
    """Caught on the real corpus the hour the ledger was first filled: the block
    joined a theme's wordings with `group_concat`, whose separator is a comma —
    and these titles contain commas, so "Gate the book on spread trajectory, not
    only spread level" printed as two entries.

    A fixture of comma-free titles would have passed. This one carries the
    verbatim wording that broke it."""
    titles = [
        "Gate the credit-spread-wide book on spread trajectory, not only spread level",
        "Market-signal book selection should read credit-spread VELOCITY, not only level",
        "Credit-regime gate on the IWN sleeve of the credit-spread-wide book",
    ]
    for n, title in enumerate(titles):
        await db.command(
            "INSERT INTO innovation (id, type, title, rationale, spec, theme_id, date, trace, "
            "created_at) VALUES (:id, 'strategy_revision', :t, 'r', '{}', 'thm-1', '2026-08-09', "
            "'tr', :now)",
            id=f"inn-{n}",
            t=title,
            now=f"2026-08-09T09:0{n}:00+00:00",
        )

    block = D._recurring_block(await D._recurring_themes(db))

    assert any("3x" in line for line in block)
    for title in titles:
        assert any(title in line for line in block), f"{title!r} was split or dropped"
