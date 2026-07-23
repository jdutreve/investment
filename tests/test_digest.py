"""Weekly digest render (docs/TASKS.md Task 6bis.1;
src/investment/telegram/digest.py). Pure rendering asserted line by line, plus
scoreboard assembly from a seeded proposal ledger."""

from collections.abc import AsyncIterator
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
        "regime": {"regime_name": "Stagflation", "regime_type_id": "stag", "confidence": 0.78},
        "global_liquidity": {"level": 98.4, "speed": -0.80},
        "ranking": [
            {"rank": 1, "portfolio_id": "4S Balanced", "defender": 1,
             "sortino_rolling": 1.18, "calmar_rolling": 1.9},
            {"rank": 2, "portfolio_id": "Momentum", "defender": 0,
             "sortino_rolling": 0.31, "calmar_rolling": 0.6, "max_drawdown": -0.182,
             "demoted": True},
        ],
        "invariants": [
            {"title": "TIPS inflation persistence", "weight_effective": 0.756,
             "confirmation_count": 8, "infirmation_count": 1, "author": "dalio"},
        ],
        "proposal": {
            "proposal_type": "reallocation",
            "current_allocation": {"TIP": 20, "GLD": 10, "TLT": 30},
            "proposed_allocation": {"TIP": 25, "GLD": 15, "TLT": 20},
            "reasoning": "bear scenario 55% (+35pts); gold tilt backed by GLD invariant",
        },
        "scoreboard": {"hit_rate": (1, 1), "paper_tests": [{}], "probations": [],
                       "calibration_flags": []},
        "defender_metrics": {
            "sharpe_rolling": 0.71, "sortino_rolling": 1.18, "calmar_rolling": 1.9,
            "return_3m": 0.038, "return_1y": 0.143,
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
    assert "⚠️ (demoted; drawdown -18.2% breaches the rule)" in text
    # invariant with weight (decimal, not percent) + confirmed counts
    assert "TIPS inflation persistence: 0.756 (8/9 confirmed) [dalio]" in text
    # reallocation old->new moves (sorted by ticker), unchanged sleeves omitted
    assert "GLD 10→15 | TIP 20→25 | TLT 30→20" in text
    # scoreboard hit-rate + paper-tests
    assert "Proposals hit-rate: 1/1 (100.0%) at +12w" in text
    assert "Paper-tests in progress: 1" in text
    # defender returns, signed percentages
    assert "3m +3.8%" in text and "1y +14.3%" in text


def test_no_proposal_reads_as_maintain() -> None:
    text = _digest(proposal=None)
    assert "No proposal this week — maintain." in text


# -- scoreboard assembly -----------------------------------------------------


async def _add(db: InvestmentDB, pid: str, verdict: str | None, paper: str | None) -> None:
    outcome = None if verdict is None else f'{{"verdict": "{verdict}"}}'
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, recommendation, "
        "market_context, reasoning, outcome, paper_started, trace, created_at) VALUES "
        "(:id, '2026-01-01', 'switch', 'd', 'monitor', '{}', 'r', :o, :p, 't', '2026-01-01')",
        id=pid, o=outcome, p=paper,
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
