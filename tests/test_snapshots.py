"""M4 unit tests for the pure ranking rule (docs/DATA_MODELS.md 'Ranking
rule'; docs/TASKS.md Phase 8 `test_portfolio_ranking`).

Pure for everything the rule decides; the tail of the file adds the ONE thing
that cannot be asserted without I/O — that `build_snapshot` PERSISTS the
drawdown-exclusion verdict and its per-row reason (real throwaway SQLite, no
mocks).
"""

import itertools
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical.snapshots import ValuationRow, build_snapshot, rank_portfolios

TIEBREAK_WINDOW = 0.02


def _row(
    portfolio_id: str,
    *,
    defender: bool = False,
    sortino: float | None = 1.0,
    calmar: float | None = 2.0,
    max_drawdown: float | None = -0.1,
    max_drawdown_rule: float | None = None,
) -> ValuationRow:
    return ValuationRow(
        portfolio_id=portfolio_id,
        defender=defender,
        framework_id="4seasons",
        designed_regime_type_id=None,
        primary_strategy_id=None,
        allocation={"SPY": 100.0},
        max_drawdown_rule=max_drawdown_rule,
        max_single_asset_pct=None,
        sharpe_rolling=sortino,
        sortino_rolling=sortino,
        calmar_rolling=calmar,
        max_drawdown=max_drawdown,
        volatility=0.1,
        cagr=None,
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


def test_sortino_chain_is_grouped_against_the_leader_not_pairwise() -> None:
    """The case that motivated the grouped rule (docs/DATA_MODELS.md 'Ranking
    rule'): Sortinos 1.00 / 1.015 / 1.03 chain pairwise — A ties B (0.015), B
    ties C (0.015), but C beats A outright (0.03 > 0.02). Under a pairwise
    "tied within 0.02" comparator no consistent order exists. Grouping against
    the LEADER resolves it: C leads, B is within 0.02 of C so joins it, A is
    0.03 below the leader so opens its own group — and A ranks last despite
    having the best Calmar, because it is NOT tied with the leader."""
    rows = [
        _row("A_low_sortino_best_calmar", defender=True, sortino=1.00, calmar=9.0),
        _row("B_mid", sortino=1.015, calmar=2.0),
        _row("C_top", sortino=1.03, calmar=1.5),
    ]
    ranked = rank_portfolios(rows, TIEBREAK_WINDOW)

    # C leads its group; B is tied with C (within 0.02 of it) and wins the
    # group on calmar 2.0 > 1.5. A trails in its own group.
    assert [rr.row.portfolio_id for rr in ranked] == ["B_mid", "C_top", "A_low_sortino_best_calmar"]


def test_ranking_is_invariant_to_input_order() -> None:
    """Transitivity, stated as the property that actually matters: the ranking
    must not depend on the order rows arrive in. This is what a pairwise
    within-window comparator could not guarantee, and what the Phase 9 replay
    needs (M6 calibrates thresholds on this output)."""
    rows = [
        _row("defender", defender=True, sortino=1.00, calmar=9.0),
        _row("b", sortino=1.015, calmar=2.0),
        _row("c", sortino=1.03, calmar=1.5),
        _row("d", sortino=1.04, calmar=3.0),
        _row("e", sortino=0.50, calmar=1.1),
    ]
    expected = [rr.row.portfolio_id for rr in rank_portfolios(rows, TIEBREAK_WINDOW)]

    for permutation in itertools.permutations(rows):
        got = [rr.row.portfolio_id for rr in rank_portfolios(list(permutation), TIEBREAK_WINDOW)]
        assert got == expected, f"ranking changed under input permutation: {got} != {expected}"


def test_rank_portfolios_requires_a_defender() -> None:
    with pytest.raises(ValueError, match="defender"):
        rank_portfolios([_row("only")], TIEBREAK_WINDOW)


def test_rank_portfolios_empty_input() -> None:
    assert rank_portfolios([], TIEBREAK_WINDOW) == []


# -- the user-drawdown exclusion flag (CLAUDE.md "Ranking rule") --------------

USER_CAPS = {"max_drawdown_pct": -25.0, "max_single_asset_pct": 50.0}


def test_a_drawdown_breach_excludes_without_moving_the_row() -> None:
    """The rule is "keeps the row ranked but excludes it from defender role and
    proposal candidacy" — so the flag must NOT reorder anything. The breaching
    row here has the best Sortino and Calmar and stays rank 1."""
    rows = [
        _row("deep", sortino=2.0, calmar=3.0, max_drawdown=-0.40),
        _row("shallow", defender=True, sortino=1.0, calmar=2.0, max_drawdown=-0.05),
    ]
    ranked = rank_portfolios(rows, TIEBREAK_WINDOW, USER_CAPS)
    assert [rr.row.portfolio_id for rr in ranked] == ["deep", "shallow"]
    assert [rr.excluded_from_candidacy for rr in ranked] == [True, False]


def test_the_exclusion_binds_on_the_stricter_of_user_and_portfolio_rules() -> None:
    """-18% clears the user's -25% but breaches a portfolio's own -15% rule.
    This is not hypothetical: the bridge books carry -15 and barbell -10 under a
    -25 user cap, so reading the user cap alone would under-report every one of
    them (CLAUDE.md "Binding caps": per-portfolio rules may only be STRICTER)."""
    rows = [
        _row("own_stricter_rule", max_drawdown=-0.18, max_drawdown_rule=-15.0),
        _row("user_rule_only", defender=True, max_drawdown=-0.18),
    ]
    ranked = {
        rr.row.portfolio_id: rr.excluded_from_candidacy
        for rr in rank_portfolios(rows, TIEBREAK_WINDOW, USER_CAPS)
    }
    assert ranked == {"own_stricter_rule": True, "user_rule_only": False}


def test_the_same_row_flips_when_the_cap_moves() -> None:
    """WHY THE FLAG IS STORED AND NOT DERIVED AT RENDER TIME (db/schema.py).
    One unchanged portfolio, two caps: the binding cap already moved once
    (-15 -> -25, ADR-007) and the live DB holds a -18.37% row written across
    that change. A digest that re-derived the flag would re-judge July under
    December's rule."""
    row = [_row("d", defender=True, max_drawdown=-0.18)]
    assert rank_portfolios(row, TIEBREAK_WINDOW, {**USER_CAPS, "max_drawdown_pct": -15.0})[
        0
    ].excluded_from_candidacy
    assert not rank_portfolios(row, TIEBREAK_WINDOW, USER_CAPS)[0].excluded_from_candidacy


def test_unmeasured_and_capless_rows_assert_nothing() -> None:
    """ "Unmeasured is not bad" (gates.drawdown_ok), and a ranking run with no
    user_profile in hand must not silently certify compliance either."""
    unmeasured = _row("no_indicator", defender=True, max_drawdown=None)
    assert not rank_portfolios([unmeasured], TIEBREAK_WINDOW, USER_CAPS)[0].excluded_from_candidacy
    deep = _row("deep", defender=True, max_drawdown=-0.40)
    assert not rank_portfolios([deep], TIEBREAK_WINDOW)[0].excluded_from_candidacy


# -- persistence: the flag and its reason survive the write ------------------


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "s.db")
    await conn.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'F', 1, 't', '2026-01-01')"
    )
    await conn.command(
        "INSERT INTO user_profile (user_id, currency, benchmark, phase, horizon_years, "
        "max_drawdown_pct, max_single_asset_pct, created_at, updated_at) VALUES ('u', 'CHF', "
        "'all-weather-USD', 'accumulation', 12, -25.0, 50.0, '2026-01-01', '2026-01-01')"
    )
    # A defender inside every rule, and a challenger at -18%: inside the user's
    # -25% but breaching its OWN -15% rule — the live bridge's exact shape.
    for pid, defender, rule, dd, calmar in (
        ("keeper", 1, -15.0, -0.05, 2.0),
        ("breacher", 0, -15.0, -0.18, 3.0),
    ):
        await conn.command(
            "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, "
            "benchmark, allocation, max_drawdown_rule, max_single_asset_pct, phase, "
            "sortino_rolling, calmar_rolling, max_drawdown, trace, updated_at) VALUES "
            "(:id, 'n', '4s', :d, 1, 'CHF', 'b', '{\"SPY\": 100}', :rule, 50.0, "
            "'accumulation', 1.0, :calmar, :dd, 't', '2026-01-01')",
            id=pid,
            d=defender,
            rule=rule,
            dd=dd,
            calmar=calmar,
        )
    yield conn
    await conn.close()


async def test_build_snapshot_persists_the_exclusion_and_its_reason(db: InvestmentDB) -> None:
    """The whole point of the column: the verdict is recorded AT the snapshot's
    date, against the cap in force then, so re-rendering that Monday later
    cannot re-judge it under a cap that has since moved."""
    await build_snapshot(db, TIEBREAK_WINDOW)
    rows = {
        str(r["portfolio_id"]): r
        for r in await db.query(
            "SELECT portfolio_id, rank, excluded_from_candidacy, trace "
            "FROM portfolio_weekly_snapshot"
        )
    }
    # flagged, and NOT moved: the breacher's better Calmar still wins rank 1
    assert rows["breacher"]["excluded_from_candidacy"] == 1
    assert rows["breacher"]["rank"] == 1
    assert rows["keeper"]["excluded_from_candidacy"] == 0
    # the per-row trace carries the reason, not one sentence copied batch-wide
    assert "-18.0% breaches the binding -15% rule" in str(rows["breacher"]["trace"])
    assert "excluded from defender role and proposal candidacy" in str(rows["breacher"]["trace"])
    assert "breaches" not in str(rows["keeper"]["trace"])
