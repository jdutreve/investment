"""Planner context assembly (docs/TASKS.md Task 4.1;
src/investment/planner/context.py). Pure selection/validation/summary logic
directly; the condition-active-now evaluation against a real throwaway SQLite
with seeded conditions + latest signal readings."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.planner import context as C
from investment.planner.baseline import Baseline
from investment.planner.retrieval import RetrievalPool


def _baseline(**over: object) -> Baseline:
    base: dict[str, object] = {
        "regime": {"regime_name": "Stagflation", "regime_type_id": "stag", "confidence": 0.7,
                   "events": ["CPI up"]},
        "global_liquidity": {"level": 95.0, "speed": -0.1},
        "ranking": [{"rank": 1, "portfolio_id": "pf1", "sortino_rolling": 1.2,
                     "calmar_rolling": 1.1, "recommendation": "maintain"}],
        "scenarios": [{"strategy_id": "s1", "scenario": "bull", "probability": 60.0, "shift": 8.0},
                      {"strategy_id": "s1", "scenario": "base", "probability": 30.0, "shift": 0.0}],
        "top_invariants": [{"id": "i-base", "title": "from baseline", "weight_effective": 0.9}],
        "recent_proposals": [],
    }
    base.update(over)
    return Baseline(**base)  # type: ignore[arg-type]


def _pool(**over: object) -> RetrievalPool:
    base: dict[str, object] = {
        "passages": [{"id": "p1", "excerpt": "x", "similarity": 0.8}],
        "invariants": [{"id": "i-pool", "title": "from pool", "weight_effective": 0.5}],
        "zoom_results": [],
    }
    base.update(over)
    return RetrievalPool(**base)  # type: ignore[arg-type]


# -- pure: pool / validation / assembly --------------------------------------


def test_invariant_pool_unions_and_baseline_wins_collision() -> None:
    baseline = _baseline(top_invariants=[{"id": "dup", "title": "baseline copy"}])
    pool = _pool(invariants=[{"id": "dup", "title": "pool copy"}, {"id": "i-pool", "title": "x"}])
    merged = C.invariant_pool(baseline, pool)
    assert set(merged) == {"dup", "i-pool"}
    assert merged["dup"]["title"] == "baseline copy"  # baseline provenance wins


def test_unknown_ids_surfaces_invented_selections() -> None:
    inv_pool = C.invariant_pool(_baseline(), _pool())
    pas_pool = C.passage_pool(_pool())
    sel = C.ContextSelection(invariant_ids=["i-base", "ghost"], passage_ids=["p1", "no-such"])
    assert C.unknown_ids(sel, inv_pool, pas_pool) == ["ghost", "no-such"]


def test_assemble_drops_unknowns_keeps_order_and_marks_active() -> None:
    sel = C.ContextSelection(
        invariant_ids=["i-pool", "ghost", "i-base"], passage_ids=["p1"], notes="why"
    )
    ctx = C.assemble_context(_baseline(), _pool(), sel, active_ids={"i-base"})
    # ghost dropped, selection order preserved (pool then base)
    assert [i["id"] for i in ctx.top_invariants] == ["i-pool", "i-base"]
    assert ctx.top_invariants[0]["active"] is False
    assert ctx.top_invariants[1]["active"] is True
    assert [p["id"] for p in ctx.passages] == ["p1"]
    assert ctx.notes == "why"
    # baseline structures pass through verbatim
    assert ctx.regime["regime_type_id"] == "stag"
    assert ctx.ranking == _baseline().ranking


# -- pure: condition-active-now ----------------------------------------------


def test_empty_condition_is_always_active() -> None:
    assert C.condition_active_now([], {}, "stag") is True


def test_signal_predicate_uses_latest_reading() -> None:
    latest = {"inflation": {"level": 3.0, "speed": 0.1, "acceleration": None}}
    assert C.condition_active_now(
        [{"signal": "inflation", "feature": "level", "op": ">", "value": 2.5}], latest, None
    ) is True
    assert C.condition_active_now(
        [{"signal": "inflation", "feature": "level", "op": ">", "value": 5.0}], latest, None
    ) is False


def test_missing_signal_reads_as_inactive_not_active() -> None:
    assert C.condition_active_now(
        [{"signal": "inflation", "feature": "level", "op": ">", "value": 2.5}], {}, "stag"
    ) is False


def test_regime_predicate_matches_current_type() -> None:
    cond = [{"signal": "regime", "feature": "type", "op": "==", "value": "stag"}]
    assert C.condition_active_now(cond, {}, "stag") is True
    assert C.condition_active_now(cond, {}, "goldilocks") is False


# -- pure: summary -----------------------------------------------------------


def test_summary_carries_the_deltas_call_1a_needs() -> None:
    text = C.render_baseline_summary(_baseline())
    assert "Stagflation (stag)" in text
    assert "Global liquidity" in text
    assert "pf1" in text
    assert "s1/bull" in text  # the moved scenario is shown; the 0-shift base is not
    assert "s1/base" not in text


# -- integration: active_invariant_ids ---------------------------------------


async def _seed(db: InvestmentDB) -> None:
    async def cmd(stmt: str, **p: object) -> None:
        await db.command(stmt, **p)

    # latest readings: inflation (CPIAUCSL) 3.0; growth (GROWTH_COMPOSITE) speed +1
    await cmd(
        "INSERT INTO market_data (ticker, asset_class, currency, ts, level, speed) "
        "VALUES ('CPIAUCSL', 'MACRO', 'USD', '2026-07-01', 3.0, 0.2)"
    )
    await cmd(
        "INSERT INTO market_data (ticker, asset_class, currency, ts, level, speed) "
        "VALUES ('GROWTH_COMPOSITE', 'MACRO', 'USD', '2026-07-01', 99.0, 1.0)"
    )
    conds = {
        "i-active": '[{"signal": "inflation", "feature": "level", "op": ">", "value": 2.5}]',
        "i-inactive": '[{"signal": "growth", "feature": "speed", "op": "<", "value": 0}]',
        "i-regime": '[{"signal": "regime", "feature": "type", "op": "==", "value": "stag"}]',
        "i-always": "[]",
    }
    for iid, cond in conds.items():
        await cmd(
            "INSERT INTO invariant (id, title, description, source, status, condition, "
            "weight_initial, floor_weight, trace, created_at, updated_at) VALUES (:id, 't', 'd', "
            "'s', 'integrated', :c, 0.5, 0.2, 'tr', '2026-01-01', '2026-01-01')",
            id=iid,
            c=cond,
        )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "c.db")
    await _seed(conn)
    yield conn
    await conn.close()


async def test_active_invariant_ids_evaluates_conditions_against_now(db: InvestmentDB) -> None:
    ids = ["i-active", "i-inactive", "i-regime", "i-always"]
    active = await C.active_invariant_ids(db, ids, regime_type="stag")
    assert active == {"i-active", "i-regime", "i-always"}  # i-inactive (growth rising) excluded
