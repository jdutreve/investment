"""M5 orchestration integration test (docs/MILESTONES.md M5 Definition of
Verified) — exercises `seed._materialize_benchmark_valuation` /
`_run_backtests_favors` / `_mature_seed_invariants` /
`_warm_start_scenario_probabilities` / `_check_invariant_contradictions` end
to end against a real throwaway SQLite (CLAUDE.md: real DB, no mocks),
reusing `test_seed_market.py`'s 46y synthetic market-data fixture (the
cheapest way to get a real, non-trivial regime history + macro signals
without duplicating that fixture) and `test_seed_nav.py`'s style of driving
`seed._seed_*` steps directly rather than the full `run_seed()` (which also
does a live network fetch by default).
"""

import json
from pathlib import Path

from test_seed_market import _make_stub, _settings

from investment import seed
from investment.db.seed_data import INVARIANTS, SCENARIOS
from investment.db.sqlite import InvestmentDB
from investment.mechanical import backtests


async def _seed_through_step_10(db: InvestmentDB, settings) -> None:  # type: ignore[no-untyped-def]
    await seed._seed_reference_tables(db, settings)
    await seed._seed_frameworks(db)
    await seed._seed_regime_types(db)
    await seed._seed_invariants(db)
    await seed._seed_strategies(db)
    await seed._seed_scenarios(db)
    await seed._seed_portfolios(db)
    await seed._seed_market_data(db, settings, fetch_raw=_make_stub(), yahoo_rate_limit_seconds=0.0)
    await seed._materialize_regimes(db)


async def test_m5_steps_populate_benchmark_valuation_backtests_favors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        # Step 12 must run before 10b/11 (see seed.py run_seed() ordering note).
        await seed._seed_portfolio_nav(db)

        bv_inventory = await seed._materialize_benchmark_valuation(db)
        # Every DERIVED_SIGNALS real-rate variant must materialize — a signal
        # silently absent would DEMOTE any invariant conditioning on it.
        assert bv_inventory["derived_signal_rows"]
        assert all(n > 0 for n in bv_inventory["derived_signal_rows"].values())
        assert any(n > 0 for n in bv_inventory["asset_class_rows"].values())
        assert any(n > 0 for n in bv_inventory["strategy_rows"].values())
        assert any(n > 0 for n in bv_inventory["asset_rows"].values())

        bv_rows = await db.query("SELECT COUNT(*) AS n FROM benchmark_valuation")
        assert bv_rows[0]["n"] > 0
        # Every PORTFOLIOS-backing Strategy that got a non-empty NAV should
        # have at least one 'strategy' benchmark_valuation row.
        strategy_rows = await db.query(
            "SELECT DISTINCT benchmark_id FROM benchmark_valuation "
            "WHERE benchmark_kind = 'strategy'"
        )
        assert len(strategy_rows) > 0

        bf_inventory = await seed._run_backtests_favors(db)
        assert bf_inventory["backtests_written"] > 0
        assert bf_inventory["favors_edges"] > 0

        favors_rows = await db.query("SELECT regime_type_id, strategy_id, n_periods FROM favors")
        assert len(favors_rows) == bf_inventory["favors_edges"]
        assert all(r["n_periods"] >= 3 for r in favors_rows)  # min_backtest_periods gate

        backtest_rows = await db.query("SELECT strategy_id, regime_id, overlap_pct FROM backtest")
        assert len(backtest_rows) == bf_inventory["backtests_written"]
        assert all(0.0 <= r["overlap_pct"] <= 100.0 for r in backtest_rows)
    finally:
        await db.close()


async def test_m5_invariant_maturation_and_contradiction_check(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)
        await seed._run_backtests_favors(db)

        maturation = await seed._mature_seed_invariants(db)
        results = maturation["invariants"]
        assert len(results) == len(INVARIANTS)
        for r in results:
            assert r["status"] in ("proposed", "integrated", "rejected")
            assert 0.0 <= r["market_score"] <= 1.0

        # A second run must be idempotent: no duplicated invariant_confrontations,
        # and every already-matured invariant is skipped (not re-confronted).
        confrontations_before = (
            await db.query("SELECT COUNT(*) AS n FROM invariant_confrontations")
        )[0]["n"]
        maturation_again = await seed._mature_seed_invariants(db)
        assert all(r["skipped_reason"] is not None for r in maturation_again["invariants"])
        confrontations_after = (
            await db.query("SELECT COUNT(*) AS n FROM invariant_confrontations")
        )[0]["n"]
        assert confrontations_after == confrontations_before

        invariant_rows = await db.query(
            "SELECT id, status, confirmation_count, infirmation_count, weight_effective "
            "FROM invariant"
        )
        assert len(invariant_rows) == len(INVARIANTS)
        for row in invariant_rows:
            assert row["weight_effective"] is not None
            assert row["weight_effective"] > 0.0

        contradiction_result = await seed._check_invariant_contradictions(db)
        # No assertion on emptiness (a real contradiction on synthetic random
        # data is possible) — just that the check runs cleanly and returns
        # well-formed pairs, if any.
        for pair in contradiction_result["contradictions"]:
            assert pair["invariant_a"] != pair["invariant_b"]
    finally:
        await db.close()


async def test_m5_reseed_does_not_clobber_matured_state(tmp_path: Path) -> None:
    """Regression: a full re-seed (re-running the STATIC step 4
    `_seed_invariants`, not just re-calling `_mature_seed_invariants` in
    isolation) must not reset an already-matured invariant's
    status/market_score/trace back to the pristine 'proposed' seed
    defaults, and must not duplicate `invariant_confrontations` rows — this
    is what a real `python -m investment.seed` re-run does (docs/
    MILESTONES.md "Incremental seed": idempotent, re-run at M5/M7)."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)
        await seed._run_backtests_favors(db)
        await seed._mature_seed_invariants(db)

        before = {
            r["id"]: (r["status"], r["market_score"], r["weight_effective"])
            for r in await db.query(
                "SELECT id, status, market_score, weight_effective FROM invariant"
            )
        }
        confrontations_before = (
            await db.query("SELECT COUNT(*) AS n FROM invariant_confrontations")
        )[0]["n"]
        assert confrontations_before > 0

        # A full re-seed: static step 4 re-runs FIRST (as `run_seed()` does),
        # then maturation runs again.
        await seed._seed_invariants(db)
        maturation_again = await seed._mature_seed_invariants(db)
        assert all(r["skipped_reason"] == "already_matured" for r in maturation_again["invariants"])

        after = {
            r["id"]: (r["status"], r["market_score"], r["weight_effective"])
            for r in await db.query(
                "SELECT id, status, market_score, weight_effective FROM invariant"
            )
        }
        assert after == before

        confrontations_after = (
            await db.query("SELECT COUNT(*) AS n FROM invariant_confrontations")
        )[0]["n"]
        assert confrontations_after == confrontations_before
    finally:
        await db.close()


async def test_m5_editing_an_invariant_re_matures_it(tmp_path: Path) -> None:
    """A verdict belongs to the definition it was earned under. Both are
    mutable — `_seed_invariants` rewrites condition/effect on every run while
    preserving the maturation fields, and M7's consolidation revises them —
    so a guard keyed on "was ever matured" lets an EDITED invariant keep a
    score measured against its OLD condition.

    Reproduced on the live DB before the fix: rewriting the gold invariant's
    condition to one that can NEVER fire preserved 0.646/integrated, and gate
    6 would have cited it."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)
        await seed._run_backtests_favors(db)
        await seed._mature_seed_invariants(db)

        target = "inv-rising-growth-equities"
        before = (await db.query("SELECT * FROM invariant WHERE id = :i", i=target))[0]
        assert before["confirmation_count"] + before["infirmation_count"] > 0

        # Unchanged definition -> still skipped (the sweep stays once-only).
        again = {
            r["invariant_id"]: r for r in (await seed._mature_seed_invariants(db))["invariants"]
        }
        assert again[target]["skipped_reason"] == "already_matured"

        # Now EDIT the condition to one that can never fire.
        await db.command(
            "UPDATE invariant SET condition = :c WHERE id = :i",
            c=json.dumps([{"signal": "growth", "feature": "speed", "op": ">", "value": 999}]),
            i=target,
        )
        edited = {
            r["invariant_id"]: r for r in (await seed._mature_seed_invariants(db))["invariants"]
        }
        assert edited[target]["skipped_reason"] is None, "an edited invariant must re-mature"

        after = (await db.query("SELECT * FROM invariant WHERE id = :i", i=target))[0]
        # No moments can exist for the new condition, so the old evidence is gone.
        assert after["confirmation_count"] == 0
        assert after["infirmation_count"] == 0
        assert after["status"] == "proposed"

        # The stale birth-sweep rows went with it, rather than stacking.
        rows = await db.query(
            "SELECT COUNT(*) AS n FROM invariant_confrontations "
            "WHERE invariant_id = :i AND source = 'backtest'",
            i=target,
        )
        assert rows[0]["n"] == 0
    finally:
        await db.close()


async def test_m5_prune_removes_ghosts_but_spares_derived_signals(tmp_path: Path) -> None:
    """docs/IMPROVEMENTS.md I-30: `allowed_tickers` is owned by seed_data but
    the seed only INSERT-OR-REPLACEs, so a RETIRED ticker's row and series
    survive forever — and `investable_tickers` reads that table, so the ghost
    becomes a valid `asset:<retired>` handle scored on a frozen series.

    The trap this pins: `real_rate`/`real_yield_10y` exist ONLY in
    DERIVED_SIGNALS, never in ALLOWED_TICKERS, so a keep-set built from
    ALLOWED_TICKERS alone would delete the gold invariant's own signal."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)

        # A ghost, exactly as BIL survives on the live DB.
        await db.command(
            "INSERT OR REPLACE INTO allowed_tickers "
            "(ticker, asset_class, currency, source, transform, availability_lag_days, active) "
            "VALUES ('GHOST', 'US_TBILL', 'USD', 'yahoo', 'none', 0, 1)"
        )
        await db.append_ts_batch(
            "market_data",
            [
                {
                    "ticker": "GHOST",
                    "asset_class": "US_TBILL",
                    "currency": "USD",
                    "ts": "2020-01-02",
                    "level": 1.0,
                    "speed": None,
                    "acceleration": None,
                }
            ],
        )
        assert "GHOST" in await backtests.investable_tickers(db)

        pruned = await seed._prune_retired_series(db)

        assert "GHOST" in pruned["retired_tickers"]
        assert pruned["market_data_rows_pruned"] >= 1
        assert "GHOST" not in await backtests.investable_tickers(db)
        rows = await db.query("SELECT COUNT(*) AS n FROM market_data WHERE ticker = 'GHOST'")
        assert rows[0]["n"] == 0

        # The derived signals must be untouched — the whole point.
        for signal in ("real_rate", "real_yield_10y"):
            rows = await db.query(
                "SELECT COUNT(*) AS n FROM market_data WHERE ticker = :t", t=signal
            )
            assert rows[0]["n"] > 0, f"{signal} was pruned — keep-set forgot DERIVED_SIGNALS"

        # Idempotent: a second prune is a no-op, not a slow re-delete.
        assert (await seed._prune_retired_series(db))["retired_tickers"] == []
    finally:
        await db.close()


async def test_m5_author_claimed_status_never_survives_unmeasured(tmp_path: Path) -> None:
    """ADR-006: belief does not grant integration, history does. An author
    CAN supply `status='integrated'` — the owner-submitted gold invariant
    arrived that way, with validated_at and a hand-authored market_score.
    Every path that cannot produce a verdict (reference knowledge, demotion,
    no benchmark) returns before the verdict is persisted, so the claimed
    status must be forced back to 'proposed' explicitly or it silently
    stands — and gate 6 would then cite an invariant nothing ever measured."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)

        claimed = {
            "title": "Self-certifying claim",
            "description": "Arrives asserting its own verdict.",
            "source": "test",
            "author": None,
            "status": "integrated",  # the author's claim
            "validated_at": "2020-01-01",
            "weight_initial": 0.70,
            "floor_weight": 0.20,
            "trace": "t",
        }
        # (a) reference knowledge — no effect at all to measure.
        await db.upsert_vertex("invariant", "inv-claims-ref", {**claimed, "condition": []})
        # (b) malformed effect -> demoted by the validation gate.
        await db.upsert_vertex(
            "invariant",
            "inv-claims-bad-metric",
            {
                **claimed,
                "condition": [],
                "effect": json.dumps(
                    {
                        "handle": "asset:GLD",
                        "metric": "relative_return",
                        "method": "cross_class",
                        "direction": "outperform",
                    }
                ),
            },
        )

        await seed._mature_seed_invariants(db)

        rows = await db.query(
            "SELECT id, status, validated_at FROM invariant "
            "WHERE id IN ('inv-claims-ref', 'inv-claims-bad-metric')"
        )
        assert len(rows) == 2
        for row in rows:
            assert row["status"] == "proposed", f"{row['id']} kept its claimed status"
            assert row["validated_at"] is None, f"{row['id']} kept a bogus certification date"
    finally:
        await db.close()


async def test_m5_scenario_probability_warm_start(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)

        warm_start = await seed._warm_start_scenario_probabilities(db)
        assert set(warm_start.keys()) == {str(s["strategy_id"]) for s in SCENARIOS}
        for probabilities in warm_start.values():
            assert set(probabilities.keys()) == {"bull", "base", "bear"}
            assert sum(probabilities.values()) > 0.0

        rows = await db.query("SELECT strategy_id, scenario, probability FROM scenario_probability")
        assert len(rows) == len(SCENARIOS)
        # Each strategy's 3 scenario probabilities sum to ~100 (normalized or
        # the hand-set fallback, both of which sum to 100).
        by_strategy: dict[str, float] = {}
        for r in rows:
            by_strategy[r["strategy_id"]] = (
                by_strategy.get(r["strategy_id"], 0.0) + r["probability"]
            )
        for total in by_strategy.values():
            assert 99.0 <= total <= 101.0
    finally:
        await db.close()
