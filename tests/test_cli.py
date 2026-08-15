"""The `invest` CLI front (docs/TASKS.md Task 6ter.2; ADR-005 "three fronts,
one command layer").

WHY THIS FILE EXISTS. Measured 2026-08-15: `ops/cli.py` was at **0% coverage**
— 261 statements, not one of them executed by a test — while being one of the
three fronts ADR-005 promises are interchangeable, and while carrying the
read-only path the owner uses to inspect a live database. It had been edited
that same day (return and Sharpe added to the ranking table) with nothing able
to notice a broken column.

WHAT IS PINNED HERE is what a FRONT owes: it reads what the layer below wrote,
renders every column it claims to, and never decides anything. The cross-front
equivalence M10's DoV asks for (bot vs dashboard vs CLI telling the same story)
is not here — the dashboard does not exist yet — but the columns this front
shows are, because they are what the owner reads on a Sunday.

Real throwaway SQLite, no mocks (CLAUDE.md tests), through the CLI's own
read-only connection.
"""

import json
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.ops import cli

SNAPSHOT_DATE = "2026-08-15"


async def _seed_snapshot(db: InvestmentDB) -> None:
    """Two ranked rows: the defender, and a benchmark that outranks it on
    return while carrying the deeper drawdown — the shape the 2026-08-15
    display change exists to make visible."""
    for rank, pid, defender, one_year, sortino, sharpe, calmar, mdd in (
        (1, "spy-USD", 0, 0.217, 1.60, 1.08, 1.16, -0.188),
        (2, "4s-balanced-defender", 1, 0.127, 1.09, 0.76, 1.60, -0.070),
    ):
        await db.command(
            "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
            "allocation, rank, market_context, recommendation, trace, return_1y, "
            "sortino_rolling, sharpe_rolling, calmar_rolling, max_drawdown) "
            "VALUES (:d, :p, :def, '4seasons', '{}', :rank, '{}', 'monitor', 't', :r1y, "
            ":sortino, :sharpe, :calmar, :mdd)",
            d=SNAPSHOT_DATE,
            p=pid,
            rank=rank,
            r1y=one_year,
            sortino=sortino,
            sharpe=sharpe,
            calmar=calmar,
            mdd=mdd,
            **{"def": defender},
        )


@pytest.fixture
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "cli.db"
    conn = InvestmentDB(path)
    try:
        await _seed_snapshot(conn)
    finally:
        await conn.close()
    return path


async def test_ranking_shows_the_return_and_the_sharpe_of_every_row(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 2026-08-15: return and Sharpe on every display. This front ranked
    on risk-adjusted numbers while never saying what a row RETURNED, which is
    how a portfolio can sit second on Sortino having returned a fifth of the row
    below it."""
    cli.cmd_ranking(db_path, None, as_json=False)
    out = capsys.readouterr().out

    assert "1y" in out and "sharpe" in out  # the header names both
    assert "+21.7%" in out and "1.08" in out  # the benchmark's return and Sharpe
    assert "+12.7%" in out and "0.76" in out  # the defender's
    assert "0.188" in out  # and the drawdown that return was bought with


async def test_ranking_json_is_the_row_not_a_rendering(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` is what a script reads, so it must carry the columns rather than
    the formatting: a percentage sign in this output would mean the front had
    decided something."""
    cli.cmd_ranking(db_path, None, as_json=True)
    rows = json.loads(capsys.readouterr().out)

    assert [r["portfolio_id"] for r in rows] == ["spy-USD", "4s-balanced-defender"]
    assert rows[0]["return_1y"] == pytest.approx(0.217)
    assert rows[0]["sharpe_rolling"] == pytest.approx(1.08)


async def test_an_empty_database_says_so_instead_of_printing_a_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A front that prints an empty table on an unseeded database reads as "the
    ranking is empty" rather than "there is no ranking yet"."""
    empty = tmp_path / "empty.db"
    conn = InvestmentDB(empty)
    await conn.close()

    cli.cmd_ranking(empty, None, as_json=False)
    out = capsys.readouterr().out
    assert "no snapshot yet" in out
    assert "sortino" not in out


async def test_a_date_that_has_no_snapshot_is_not_silently_the_latest(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--date` asks a question about ONE Sunday. Answering it with a different
    Sunday's ranking would be the worst possible failure of a read-only
    inspection tool."""
    cli.cmd_ranking(db_path, "2020-01-01", as_json=False)
    out = capsys.readouterr().out
    assert "no snapshot yet" in out
    assert "spy-USD" not in out
