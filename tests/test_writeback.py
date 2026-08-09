"""Writeback: the caps algebra and candidate-book validation
(src/investment/writeback/writeback.py).

Most of this file was the cognitive reallocation's disposition — the six gates,
the cooldown, gate 0's mechanical sovereignty. ADR-012 removed the reallocation
and they went with it. What remains is what still runs: `effective_caps`'
stricter-of, and the shape checks a candidate book must pass before a proposed
strategy gets a portfolio to be measured through."""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
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


# -- gate 0: mechanical sovereignty (ADR-011) --------------------------------


# -- candidate book validation (_base_allocation) ----------------------------


def _spec(base: object) -> dict[str, object]:
    return {"scenarios": [{"name": "base", "target_allocation": base}]}


def test_base_allocation_accepts_a_well_formed_book() -> None:
    assert W._base_allocation(_spec({"SPY": 60, "GLD": 40})) == {"SPY": 60.0, "GLD": 40.0}


@pytest.mark.parametrize(
    ("label", "base"),
    [
        # every one of these reached a Portfolio row and a NAV before the shape
        # checks: NaN slips past every later comparison, a negative leg is a
        # short V1 cannot hold, and a book summing to 7 or 4000 prices the
        # candidate on a leverage the strategy never claimed.
        ("nan", {"SPY": 60, "GLD": float("nan")}),
        ("inf", {"SPY": 60, "GLD": float("inf")}),
        ("short leg", {"SPY": 130, "GLD": -30}),
        ("sums to 7", {"SPY": 4, "GLD": 3}),
        ("sums to 4000", {"SPY": 2000, "GLD": 2000}),
        ("empty", {}),
        ("non-numeric", {"SPY": "a lot"}),
    ],
)
def test_base_allocation_refuses_a_malformed_book(label: str, base: object) -> None:
    assert W._base_allocation(_spec(base)) is None, label


async def _candidate_fixture(db: InvestmentDB, strategy_id: str) -> None:
    """The two rows `_commit_candidate_portfolio` reads before it writes: the
    user profile (the binding caps + currency/benchmark/phase) and the strategy
    whose framework it inherits. Seeded here rather than in `_seed` so the
    reallocation tests keep the DB they were written against."""
    await db.command(
        "INSERT OR IGNORE INTO user_profile (user_id, currency, benchmark, max_drawdown_pct, "
        "max_single_asset_pct, phase, created_at, updated_at) VALUES ('u', 'USD', 'SPY', "
        "-25.0, 50.0, 'accumulation', '2026-01-01', '2026-01-01')"
    )
    await db.command(
        "INSERT INTO strategy (id, title, description, framework_id, conviction, enabled, "
        "conditions, source, status, date_opened, trace, created_at, updated_at) VALUES "
        "(:id, 't', 'd', '4s', 50, 0, '[]', 'agent-discovery', 'proposed', '2026-01-05', "
        "'tr', '2026-01-05', '2026-01-05')",
        id=strategy_id,
    )


async def _commit_candidate(db: InvestmentDB, strategy_id: str, base: object) -> list[dict]:
    await _candidate_fixture(db, strategy_id)
    await W._commit_candidate_portfolio(
        db, strategy_id, _spec(base), date(2026, 1, 5), "2026-01-05T00:00:00Z"
    )
    return await db.query(
        "SELECT id FROM portfolio WHERE id = :p", p=W.candidate_portfolio_id(strategy_id)
    )


async def test_a_compliant_candidate_does_get_its_portfolio(db: InvestmentDB) -> None:
    """The positive control the two refusals below need: without it they would
    pass just as well on an early return for a missing user_profile."""
    assert await _commit_candidate(db, "s-ok", {"SPY": 50, "GLD": 50}) != []


async def test_an_over_concentrated_candidate_gets_no_portfolio(db: InvestmentDB) -> None:
    """A candidate is not held, but it IS measured, and its NAV feeds FAVORS —
    which the reallocation blend leans on. The 50% cap binds it too."""
    assert await _commit_candidate(db, "s-conc", {"SPY": 40, "GLD": 60}) == []


async def test_a_candidate_holding_an_untradable_ticker_gets_no_portfolio(db: InvestmentDB) -> None:
    """No price series means an empty NAV and a strategy unmeasurable forever —
    better named at birth than diagnosed 24 weeks later by the backstop."""
    assert await _commit_candidate(db, "s-tick", {"SPY": 50, "MOONCOIN": 50}) == []
