"""M4 unit tests for the pure ranking rule (docs/DATA_MODELS.md 'Ranking
rule'; docs/TASKS.md Phase 8 `test_portfolio_ranking`) — no DB, no I/O.
"""

import pytest

from investment.mechanical.snapshots import ValuationRow, rank_portfolios

TIEBREAK_WINDOW = 0.02


def _row(
    portfolio_id: str,
    *,
    defender: bool = False,
    sortino: float | None = 1.0,
    calmar: float | None = 2.0,
    max_drawdown: float | None = -0.1,
) -> ValuationRow:
    return ValuationRow(
        portfolio_id=portfolio_id,
        defender=defender,
        framework_id="4seasons",
        designed_regime_type_id=None,
        primary_strategy_id=None,
        allocation={"SPY": 100.0},
        sharpe_rolling=sortino,
        sortino_rolling=sortino,
        calmar_rolling=calmar,
        max_drawdown=max_drawdown,
        volatility=0.1,
        return_3m=None,
        return_6m=None,
        return_1y=None,
        return_3y=None,
        return_5y=None,
    )


def test_rank_by_sortino_desc_when_gap_exceeds_window() -> None:
    rows = [
        _row("low", defender=True, sortino=0.5),
        _row("high", sortino=1.5),
        _row("mid", sortino=1.0),
    ]
    ranked = rank_portfolios(rows, TIEBREAK_WINDOW)
    assert [rr.row.portfolio_id for rr in ranked] == ["high", "mid", "low"]
    assert [rr.rank for rr in ranked] == [1, 2, 3]


def test_tiebreak_within_window_uses_calmar_desc() -> None:
    # sortino gap 0.01 < 0.02 window -> tie-break on calmar.
    rows = [
        _row("defender", defender=True, sortino=1.00, calmar=1.5),
        _row("higher_calmar", sortino=1.01, calmar=3.0),
        _row("lower_calmar", sortino=0.99, calmar=1.2),
    ]
    ranked = rank_portfolios(rows, TIEBREAK_WINDOW)
    assert [rr.row.portfolio_id for rr in ranked] == [
        "higher_calmar",
        "defender",
        "lower_calmar",
    ]


def test_final_tiebreak_is_max_drawdown_less_negative_wins() -> None:
    rows = [
        _row("defender", defender=True, sortino=1.0, calmar=2.0, max_drawdown=-0.10),
        # Same sortino (within window) AND same calmar -> max_drawdown decides.
        _row("shallower_dd", sortino=1.005, calmar=2.0, max_drawdown=-0.05),
        _row("deeper_dd", sortino=1.005, calmar=2.0, max_drawdown=-0.20),
    ]
    ranked = rank_portfolios(rows, TIEBREAK_WINDOW)
    assert [rr.row.portfolio_id for rr in ranked] == [
        "shallower_dd",
        "defender",
        "deeper_dd",
    ]


def test_calmar_below_1_is_demoted_regardless_of_sortino() -> None:
    rows = [
        _row("defender", defender=True, sortino=0.1, calmar=1.5),
        _row("great_sortino_bad_calmar", sortino=5.0, calmar=0.9),
        _row("ok", sortino=0.5, calmar=1.2),
    ]
    ranked = rank_portfolios(rows, TIEBREAK_WINDOW)
    # "ok" outranks the defender on sortino (never privileged, CLAUDE.md
    # 'Ranking rule'); "great_sortino_bad_calmar" has by far the best
    # sortino but calmar < 1.0 demotes it to the bottom regardless.
    assert [rr.row.portfolio_id for rr in ranked] == ["ok", "defender", "great_sortino_bad_calmar"]


def test_gap_to_defender_null_only_for_defender() -> None:
    rows = [
        _row("defender", defender=True, sortino=1.0, calmar=2.0, max_drawdown=-0.10),
        _row("challenger", sortino=1.5, calmar=2.5, max_drawdown=-0.05),
    ]
    ranked = rank_portfolios(rows, TIEBREAK_WINDOW)
    defender_row = next(rr for rr in ranked if rr.row.defender)
    challenger_row = next(rr for rr in ranked if not rr.row.defender)

    assert defender_row.gap_to_defender is None
    assert challenger_row.gap_to_defender is not None
    assert challenger_row.gap_to_defender["sortino_rolling"] == pytest.approx(0.5)
    assert challenger_row.gap_to_defender["calmar_rolling"] == pytest.approx(0.5)
    assert challenger_row.gap_to_defender["max_drawdown"] == pytest.approx(0.05)


def test_rank_portfolios_requires_a_defender() -> None:
    with pytest.raises(ValueError, match="defender"):
        rank_portfolios([_row("only")], TIEBREAK_WINDOW)


def test_rank_portfolios_empty_input() -> None:
    assert rank_portfolios([], TIEBREAK_WINDOW) == []
