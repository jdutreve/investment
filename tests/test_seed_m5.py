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
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from test_seed_market import _make_stub, _settings

from investment import seed
from investment.db.seed_data import INVARIANTS, SCENARIOS
from investment.db.sqlite import InvestmentDB
from investment.mechanical import backtests, outcomes
from investment.mechanical.invariants import REFERENCE_STATUS
from investment.planner.post import PostPlannerResult
from investment.worker.result import ImprovementProposal, ImprovementType
from investment.writeback import writeback

# The 3 scenarios every new_strategy spec must carry (ARCHITECTURE "New
# Strategy"); the BASE one is what becomes the candidate book.
# Every sleeve <= the 50% concentration cap: a candidate Portfolio is measured,
# and its NAV feeds FAVORS, so it is held to the same binding cap as anything
# else that reaches the ranking (writeback `_commit_candidate_portfolio`). Only
# the BASE book is built, but keeping all three compliant stops the fixture from
# encoding a book the system would refuse.
_SCENARIOS = [
    {"name": "bull", "probability": 25, "target_allocation": {"SPY": 50, "GLD": 50}},
    {"name": "base", "probability": 50, "target_allocation": {"SPY": 50, "GLD": 50}},
    {"name": "bear", "probability": 25, "target_allocation": {"SPY": 30, "GLD": 50, "TLT": 20}},
]


def _innovations(proposals: list[ImprovementProposal]) -> PostPlannerResult:
    """A guardrailed Call-2 result carrying only innovations — the shape
    `commit_innovations` consumes."""
    return PostPlannerResult(innovations=proposals)


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


async def test_m5_changing_the_verdict_rule_re_matures_everything(tmp_path: Path) -> None:
    """A verdict belongs to the RULE it was earned under, exactly as it
    belongs to its definition (ADR-006 M5-bis amendment). The M5 fingerprint
    keyed on (condition, effect) only, so tightening the integration bar left
    every already-matured invariant sitting on the verdict the OLD bar gave
    it — including the ones the new bar exists to catch."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)
        await seed._run_backtests_favors(db)
        await seed._mature_seed_invariants(db)

        again = {
            r["invariant_id"]: r for r in (await seed._mature_seed_invariants(db))["invariants"]
        }
        assert all(r["skipped_reason"] == "already_matured" for r in again.values())

        # Move a bar. Nothing about any invariant changed — only the rule.
        await db.command(
            "UPDATE system_thresholds SET value = 0.55 WHERE key = :k",
            k="invariant_time_validation_score",
        )
        after = {
            r["invariant_id"]: r for r in (await seed._mature_seed_invariants(db))["invariants"]
        }
        assert all(r["skipped_reason"] is None for r in after.values()), (
            "a rule change must re-mature every invariant, not just edited ones"
        )
    finally:
        await db.close()


async def test_m5_validated_at_is_not_a_ratchet(tmp_path: Path) -> None:
    """`validated_at` is 'null while still a candidate' (docs/DATA_MODELS.md),
    but only `_force_uncertified` ever cleared it — a verdict that LEFT
    'integrated' kept the date. The verdict is stateless (recomputed from
    current counts), so integration is not a ratchet and neither is its date.
    Surfaced live by the M5-bis rule change: TIPS went 'integrated' ->
    'proposed' and kept validated_at='2026-07-15'."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)
        await seed._run_backtests_favors(db)
        # The fixture's liquidity invariant lands on 31/50: score 0.62 clears
        # theta, but the 0.50 null still produces evidence that good 5.95% of
        # the time, so at alpha=0.05 the TAIL test alone holds it back. Relax
        # the confidence to 0.90 and it integrates — which is both what makes
        # this fixture an 'integrated' one and a live demonstration that the
        # M5-bis clause is the deciding one here.
        await db.command(
            "UPDATE system_thresholds SET value = 0.90 WHERE key = :k",
            k="invariant_verdict_confidence",
        )
        await seed._mature_seed_invariants(db)

        integrated = await db.query(
            "SELECT id, validated_at FROM invariant WHERE status = 'integrated'"
        )
        assert integrated, "fixture must integrate at least one invariant to be meaningful"
        assert all(r["validated_at"] is not None for r in integrated)

        # Move theta above every score: nothing can stay integrated.
        await db.command(
            "UPDATE system_thresholds SET value = 0.99 WHERE key = :k",
            k="invariant_time_validation_score",
        )
        await seed._mature_seed_invariants(db)

        rows = await db.query("SELECT id, status, validated_at FROM invariant")
        assert not [r for r in rows if r["status"] == "integrated"]
        assert not [r for r in rows if r["validated_at"] is not None], (
            "leaving 'integrated' must clear validated_at"
        )
    finally:
        await db.close()


async def test_m5_authoritative_write_replaces_orphans(tmp_path: Path) -> None:
    """docs/IMPROVEMENTS.md I-30 (re-dating half): INSERT OR REPLACE is keyed
    on (ticker, ts), so re-dating a series writes NEW rows beside the orphaned
    old ones. Live consequence: M2SL held 1768 rows at 7-day spacing (35y
    monthly is ~420), and `m2_yoy` read across the copies to produce YoY of
    -62.6%..+213.9% where reality is about -4%..+27%."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        stale = [
            {
                "ticker": "M2SL",
                "asset_class": "MACRO",
                "currency": "USD",
                "ts": d,
                "level": 100.0,
                "speed": None,
                "acceleration": None,
            }
            for d in ("2020-01-08", "2020-01-15", "2020-01-22")  # a weekly-dated copy
        ]
        await db.append_ts_batch("market_data", stale)
        fresh = [{**stale[0], "ts": f"2020-0{m}-01", "level": 200.0} for m in range(1, 4)]

        assert await seed._write_series_authoritatively(db, "M2SL", fresh) is None
        rows = await db.query("SELECT ts, level FROM market_data WHERE ticker='M2SL' ORDER BY ts")
        assert [r["ts"] for r in rows] == ["2020-01-01", "2020-02-01", "2020-03-01"]
        assert all(r["level"] == 200.0 for r in rows), "orphaned weekly copy survived"
    finally:
        await db.close()


async def test_m5_authoritative_write_guard_uses_span_not_row_count(tmp_path: Path) -> None:
    """The guard must not key on ROW COUNT — that is the signal the bug
    corrupts. Re-dating INFLATES the stored series (M2SL: 1768 rows for 418
    real monthly observations), so a count test reads the CLEAN fetch as a
    76% shortfall and refuses to replace precisely the series that needs it.
    Shipped that way once; the live run cleaned nothing."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        base = {
            "ticker": "M2SL",
            "asset_class": "MACRO",
            "currency": "USD",
            "level": 100.0,
            "speed": None,
            "acceleration": None,
        }
        # Stored: a weekly-dated copy — 3x the rows over the SAME span.
        polluted = [
            {**base, "ts": d.date().isoformat()}
            for d in pd.date_range("2020-01-01", "2020-12-31", freq="W")
        ]
        await db.append_ts_batch("market_data", polluted)
        # Fresh: the real monthly series — far FEWER rows, same span.
        clean = [
            {**base, "ts": d.date().isoformat(), "level": 200.0}
            for d in pd.date_range("2020-01-01", "2020-12-01", freq="MS")
        ]
        assert len(clean) < len(polluted) * 0.9, "fixture must trip a row-count guard"

        report = await seed._write_series_authoritatively(db, "M2SL", clean)

        assert report is None, "span still covered — the clean series must replace the copies"
        rows = await db.query("SELECT COUNT(*) AS n FROM market_data WHERE ticker='M2SL'")
        assert rows[0]["n"] == len(clean)
    finally:
        await db.close()


async def test_m5_authoritative_write_guard_refuses_to_wipe_history(tmp_path: Path) -> None:
    """I-30: "never delete on a hunch". A truncated vendor response must
    degrade to the old additive behaviour and REPORT — never delete 35y of
    history because Yahoo returned 100 rows today."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        stored = [
            {
                "ticker": "SPY",
                "asset_class": "US_EQUITY",
                "currency": "USD",
                "ts": f"2020-{m:02d}-01",
                "level": 100.0,
                "speed": None,
                "acceleration": None,
            }
            for m in range(1, 13)
        ]
        await db.append_ts_batch("market_data", stored)
        truncated = [{**stored[0], "ts": "2021-01-01", "level": 999.0}]

        report = await seed._write_series_authoritatively(db, "SPY", truncated)

        assert report is not None and "delete abandoned" in report
        rows = await db.query("SELECT COUNT(*) AS n FROM market_data WHERE ticker='SPY'")
        assert rows[0]["n"] == 13, "history was wiped by a truncated fetch"
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
            # Both land on the TERMINAL reference status rather than 'proposed':
            # (a) has no effect and (b) was demoted to reference by the
            # validation gate, so neither can ever be confronted and neither is
            # awaiting evidence. What this test pins is unchanged — the CLAIMED
            # 'integrated' does not survive an unmeasured invariant.
            assert row["status"] != "integrated", f"{row['id']} kept its claimed status"
            assert row["status"] == REFERENCE_STATUS, f"{row['id']} is not awaiting evidence"
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


async def test_a_proposed_strategy_reaches_a_probation_verdict_on_its_own(tmp_path: Path) -> None:
    """The full innovation->verdict path, with NO hand-inserted FAVORS row.

    This is the loop that used to be closed at both ends: a strategy born of an
    innovation is `proposed, enabled=0` with no Portfolio, the Backtest sweep
    only saw `enabled = 1`, and `strategy_probation_check` judged the strategy on
    FAVORS it could therefore never have — proposed forever, against ADR-006's
    "nothing stays proposed forever". Every probation test until now inserted the
    FAVORS row by hand, which is exactly what hid it.

    So the assertion that matters is the absence: nothing here writes to `favors`
    except `run_backtests_and_favors`."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)
        await seed._run_backtests_favors(db)  # the peers the candidate is judged against

        born = date(2026, 1, 5)
        proposal = ImprovementProposal(
            type=ImprovementType.new_strategy,
            title="Gold-heavy stagflation book",
            rationale="r",
            spec={
                "id": "stagflation-candidate-v1",
                "framework_id": "4seasons",
                "scenarios": _SCENARIOS,
            },
            trace="tr",
        )
        assert await writeback.commit_innovations(db, _innovations([proposal]), born) == 1

        # BORN MEASURABLE: a disabled candidate Portfolio with a real NAV series.
        candidate = writeback.candidate_portfolio_id("stagflation-candidate-v1")
        rows = await db.query(
            "SELECT enabled, defender, allocation FROM portfolio WHERE id = :id", id=candidate
        )
        assert (rows[0]["enabled"], rows[0]["defender"]) == (0, 0)  # invisible to the ranking
        assert json.loads(str(rows[0]["allocation"])) == {"SPY": 50.0, "GLD": 50.0}  # the BASE book
        nav = await db.query(
            "SELECT COUNT(*) AS n FROM portfolio_nav WHERE portfolio_id = :id", id=candidate
        )
        assert nav[0]["n"] > 0

        # The sweep now reaches it, and FAVORS appear WITHOUT a fixture.
        await seed._run_backtests_favors(db)
        favors = await db.query(
            "SELECT regime_type_id, sortino_rolling FROM favors WHERE strategy_id = :s",
            s="stagflation-candidate-v1",
        )
        assert favors, "the candidate produced no FAVORS — probation cannot conclude"

        # ...so probation reaches a real verdict at the window, either way.
        verdict_day = born + timedelta(weeks=13)
        results = await outcomes.strategy_probation_check(db, today=verdict_day)
        mine = [r for r in results if r.strategy_id == "stagflation-candidate-v1"]
        assert len(mine) == 1
        assert mine[0].verdict in {"keep", "review"}, mine[0].skipped_reason
        assert mine[0].sortino is not None and mine[0].median is not None
        status = await db.query("SELECT status FROM strategy WHERE id = 'stagflation-candidate-v1'")
        assert status[0]["status"] in {"active", "closed"}  # never still 'proposed'
    finally:
        await db.close()


async def test_an_unmeasurable_candidate_is_closed_not_left_pending(tmp_path: Path) -> None:
    """The backstop. A spec with no usable base allocation gets no candidate
    Portfolio and therefore no FAVORS ever — waiting for it is waiting for
    something that cannot arrive. Inside the window the verdict legitimately
    waits; past `UNMEASURABLE_PROBATION_MULTIPLIER` windows it is closed, with an
    OutcomeEvent, because ADR-006 does not allow a third state that lasts."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await seed._seed_portfolio_nav(db)
        await seed._materialize_benchmark_valuation(db)
        await seed._run_backtests_favors(db)

        born = date(2026, 1, 5)
        proposal = ImprovementProposal(
            type=ImprovementType.new_strategy,
            title="A strategy with no book",
            rationale="r",
            spec={"id": "no-book-v1", "framework_id": "4seasons", "scenarios": []},
            trace="tr",
        )
        assert await writeback.commit_innovations(db, _innovations([proposal]), born) == 1
        assert (
            await db.query(
                "SELECT id FROM portfolio WHERE id = :id",
                id=writeback.candidate_portfolio_id("no-book-v1"),
            )
            == []
        )

        # Window open: it waits, and says why — no verdict, no transition.
        waiting = await outcomes.strategy_probation_check(db, today=born + timedelta(weeks=13))
        mine = [r for r in waiting if r.strategy_id == "no-book-v1"]
        assert mine and mine[0].verdict == ""
        assert mine[0].skipped_reason == "no FAVORS in current regime"
        rows = await db.query("SELECT status FROM strategy WHERE id = 'no-book-v1'")
        assert rows[0]["status"] == "proposed"

        # Past 2 windows: closed as unmeasurable, EventLog and vertex together.
        late = born + timedelta(weeks=25)
        closed = await outcomes.strategy_probation_check(db, today=late)
        mine = [r for r in closed if r.strategy_id == "no-book-v1"]
        assert mine and mine[0].verdict == "review"
        rows = await db.query("SELECT status, enabled, trace FROM strategy WHERE id = 'no-book-v1'")
        assert (rows[0]["status"], rows[0]["enabled"]) == ("closed", 0)
        assert "unmeasurable" in str(rows[0]["trace"])  # the reason is on the vertex
        events = await db.query(
            "SELECT json_extract(payload, '$.unmeasurable') AS u FROM event_log "
            "WHERE type = 'OutcomeEvent' AND source_id = 'no-book-v1'"
        )
        assert [e["u"] for e in events] == [1]

        # ...and it is not re-judged afterwards.
        assert [
            r
            for r in await outcomes.strategy_probation_check(db, today=late + timedelta(weeks=4))
            if r.strategy_id == "no-book-v1"
        ] == []
    finally:
        await db.close()


async def test_an_effect_missing_metric_demotes_instead_of_crashing_the_sweep(
    tmp_path: Path,
) -> None:
    """Found by audit, 2026-08-09, before it cost a cycle.

    `maturation_fingerprint` reads `effect["metric"]` and ran twenty lines ABOVE
    the validation that guarantees the key exists, so an effect that is a dict
    but lacks `metric` raised `KeyError` out of the 35y sweep. The sweep runs
    after `commit_innovations`' loop, OUTSIDE its per-innovation guard, so one
    such row cost the whole cognitive cycle — the same failure a prose `effect`
    caused that morning, through a shape its fix does not cover:
    `{"handle": ...}` IS a dict.

    Nothing upstream stops it either: the UC8 path writes an invariant without
    validating it. The contract is `validate_invariant`'s own promise — "a
    malformed condition/effect never silently breaks maturation" — so the
    answer is a demotion, not a raise."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_through_step_10(db, settings)
        await db.command(
            "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
            "weight_initial, floor_weight, weight_effective, trace, created_at, updated_at) "
            "VALUES ('inv-no-metric', 't', 'd', 'agent-discovery', 'proposed', '[]', :eff, "
            "0.5, 0.2, 0.5, 'tr', '2026-01-01', '2026-01-01')",
            eff=json.dumps(
                {"handle": "asset:GLD", "method": "absolute", "direction": "outperform"}
            ),
        )

        await seed._mature_seed_invariants(db)

        row = (await db.query("SELECT status, trace FROM invariant WHERE id='inv-no-metric'"))[0]
        assert row["status"] == REFERENCE_STATUS
        assert "metric" in str(row["trace"])
    finally:
        await db.close()


async def _throwaway(tmp_path: Path) -> InvestmentDB:
    """A bare database for the retirement tests. This module drives the whole
    seed elsewhere and has no shared `db` fixture; three tests about one UPSERT
    do not justify adding one to it."""
    return InvestmentDB(tmp_path / "retire.db")


async def test_a_portfolio_the_seed_no_longer_defines_is_disabled(tmp_path: Path) -> None:
    """ADR-012 removed the two measurement books; the live database kept them,
    enabled, and `worker-book` was ranked SIXTH in the first real digest
    (2026-08-12). The seed only UPSERTs, so a deletion from `PORTFOLIOS` never
    reached the world.

    It is not inert: `build_snapshot` ranks every ENABLED portfolio, so a book
    an ADR deleted keeps competing for rank — and rank 1 is what
    `outcomes._best_ranked_allocation` hands the stack's opening entry as the
    baseline it must beat."""
    db = await _throwaway(tmp_path)
    await db.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4seasons', 'F', 1, 't', '2026-01-01')"
    )
    for portfolio_id in ("4s-balanced-defender", "worker-book"):
        await db.command(
            "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, "
            "benchmark, allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, "
            "updated_at) VALUES (:id, :id, '4seasons', 0, 1, 'USD', 'b', '{}', -25.0, 50.0, "
            "'accumulation', 't', '2026-01-01')",
            id=portfolio_id,
        )

    result = await seed._retire_removed_portfolios(db)

    assert result["retired"] == ["worker-book"]
    rows = {str(r["id"]): r["enabled"] for r in await db.query("SELECT id, enabled FROM portfolio")}
    assert not rows["worker-book"]
    assert rows["4s-balanced-defender"]  # still seeded, untouched
    await db.close()


async def test_retiring_is_idempotent_and_says_when_it_did_nothing(tmp_path: Path) -> None:
    """A run that disables nothing returns an empty list rather than a silent
    success — the difference between "checked, clean" and "never looked"."""
    db = await _throwaway(tmp_path)
    assert (await seed._retire_removed_portfolios(db))["retired"] == []
    await db.close()


async def test_a_retired_portfolio_keeps_its_history(tmp_path: Path) -> None:
    """DISABLED, not deleted. `portfolio_nav`, the weekly snapshots, `holds` and
    a Proposal's `defender_id` all reference these rows; deleting would either
    break those references or rewrite history that was true when it was
    written."""
    db = await _throwaway(tmp_path)
    await db.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4seasons', 'F', 1, 't', '2026-01-01')"
    )
    await db.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) VALUES "
        "('worker-book', 'w', '4seasons', 0, 1, 'USD', 'b', '{}', -25.0, 50.0, 'accumulation', "
        "'t', '2026-01-01')"
    )
    await db.append_ts_batch(
        "portfolio_nav",
        [{"portfolio_id": "worker-book", "currency": "USD", "ts": "2026-08-01", "nav": 100.0}],
    )

    await seed._retire_removed_portfolios(db)

    assert await db.query("SELECT id FROM portfolio WHERE id = 'worker-book'")
    assert await db.query("SELECT ts FROM portfolio_nav WHERE portfolio_id = 'worker-book'")
    await db.close()
