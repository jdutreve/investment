"""UC0 Seed — `python -m investment.seed` (docs/USE_CASES.md UC0).

Idempotent: every vertex/edge write is an UPSERT, safe to re-run.

M1 scope only: steps 1-5, 7, 8 — the static graph (reference tables,
Framework, RegimeType, Invariant, Strategy + BACKED_BY, Scenario, Portfolio
+ HOLDS/DESIGNED_FOR). Steps 6/6b (corpus), 9 (market data), 10/10b (regime
materialization + benchmark valuation), 11/11b/11c (backtests/FAVORS/
maturation/warm-start), 12-13 (NAV/snapshot) are added by later milestones
(docs/MILESTONES.md "Incremental seed") — this run logs them as SKIPPED,
not silently omitted.

UC0 is the one documented exemption to the "EventLog precedes commit" rule
(CLAUDE.md "EventLog" rule): the closing
SeedEvent is a summary appended AFTER the vertices it describes, not before.
"""

import asyncio
import logging
from datetime import UTC, date, datetime

from investment.config import Settings
from investment.db.seed_data import (
    ALLOWED_TICKERS,
    BACKED_BY_EDGES,
    DESIGNED_FOR_EDGES,
    FRAMEWORKS,
    HOLDS_EDGES,
    INVARIANT_AUTHOR_CONFIG,
    INVARIANTS,
    PORTFOLIOS,
    REGIME_TYPES,
    SCENARIOS,
    STRATEGIES,
    SYSTEM_THRESHOLDS,
)
from investment.db.sqlite import InvestmentDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

# Steps deferred to later milestones (docs/MILESTONES.md "Incremental seed").
DEFERRED_STEPS = {
    "6": "corpus seed (M7)",
    "6b": "initial curation pass (M7)",
    "9": "MarketData TS backfill (M2)",
    "10": "historical Regime materialization (M3)",
    "10b": "benchmark_valuation materialization (M5)",
    "11": "initial Backtests + FAVORS (M5)",
    "11b": "invariant birth maturation over 35y (M5)",
    "11c": "scenario probability warm-start over 35y (M5)",
    "12": "PortfolioNAV TS backfill (M4)",
    "13": "portfolio_weekly_snapshot bootstrap (M4)",
}


def _without_id(props: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in props.items() if k != "id"}


def _vertex_id(props: dict[str, object]) -> str:
    return str(props["id"])


async def _seed_reference_tables(db: InvestmentDB, settings: Settings) -> int:
    """Step 1: user_profile, allowed_tickers, system_thresholds,
    invariant_author_config — document tables, no trace/EventLog."""
    now = datetime.now(UTC).isoformat()
    await db.command(
        "INSERT OR REPLACE INTO user_profile "
        "(user_id, currency, benchmark, max_drawdown_pct, max_single_asset_pct, "
        " phase, auto_validation_hours, telegram_chat_id, created_at, updated_at) "
        "VALUES (:user_id, :currency, :benchmark, :max_drawdown_pct, "
        " :max_single_asset_pct, :phase, :auto_validation_hours, :telegram_chat_id, "
        " :now, :now)",
        user_id="default",
        currency=settings.user_currency,
        benchmark=settings.user_benchmark,
        max_drawdown_pct=settings.user_max_drawdown_pct,
        max_single_asset_pct=settings.user_max_single_asset_pct,
        phase=settings.user_phase,
        auto_validation_hours=settings.user_auto_validation_hours,
        telegram_chat_id=settings.telegram_chat_id,
        now=now,
    )
    for ticker in ALLOWED_TICKERS:
        await db.command(
            "INSERT OR REPLACE INTO allowed_tickers "
            "(ticker, asset_class, currency, source, transform, availability_lag_days, active) "
            "VALUES (:ticker, :asset_class, :currency, :source, :transform, "
            " :availability_lag_days, 1)",
            **{"availability_lag_days": 0, **ticker},
        )
    for author_cfg in INVARIANT_AUTHOR_CONFIG:
        await db.command(
            "INSERT OR REPLACE INTO invariant_author_config "
            "(author, floor_weight, initial_weight_min, initial_weight_max) "
            "VALUES (:author, :floor_weight, :initial_weight_min, :initial_weight_max)",
            **author_cfg,
        )
    for key, value in SYSTEM_THRESHOLDS.items():
        await db.command(
            "INSERT OR REPLACE INTO system_thresholds (key, value, updated_at) "
            "VALUES (:key, :value, :now)",
            key=key, value=value, now=now,
        )
    return 1 + len(ALLOWED_TICKERS) + len(INVARIANT_AUTHOR_CONFIG) + len(SYSTEM_THRESHOLDS)


async def _seed_frameworks(db: InvestmentDB) -> int:
    """Step 2."""
    for fw in FRAMEWORKS:
        await db.upsert_vertex("framework", _vertex_id(fw), _without_id(fw))
    return len(FRAMEWORKS)


async def _seed_regime_types(db: InvestmentDB) -> int:
    """Step 3 — seeded once, never mutated afterwards."""
    for rt in REGIME_TYPES:
        await db.upsert_vertex("regime_type", _vertex_id(rt), _without_id(rt))
    return len(REGIME_TYPES)


async def _seed_invariants(db: InvestmentDB) -> int:
    """Step 4 — status='proposed'; matured over 35y at M5 (ADR-006: belief
    does not grant integration, history does). market_score/recency_factor
    default to 1.0 pre-confrontation; weight_effective follows the pinned
    formula (CLAUDE.md 'Invariant weight model')."""
    for inv in INVARIANTS:
        props = _without_id(inv)
        props["market_score"] = 1.0
        props["recency_factor"] = 1.0
        props["confirmation_count"] = 0
        props["infirmation_count"] = 0
        props["weight_effective"] = max(
            float(props["weight_initial"]), float(props["floor_weight"])  # type: ignore[arg-type]
        )
        await db.upsert_vertex("invariant", _vertex_id(inv), props)
    return len(INVARIANTS)


async def _seed_strategies(db: InvestmentDB) -> int:
    """Step 5 — 4 strategies, all enabled, + BACKED_BY edges."""
    today = date.today().isoformat()
    for st in STRATEGIES:
        props = _without_id(st)
        props.setdefault("date_opened", today)
        await db.upsert_vertex("strategy", _vertex_id(st), props)
    for strategy_id, invariant_id in BACKED_BY_EDGES:
        await db.create_edge(
            "backed_by", strategy_id, invariant_id,
            {"strength": 1.0, "added_at": today},
        )
    return len(STRATEGIES)


async def _seed_scenarios(db: InvestmentDB) -> int:
    """Step 7 — 3 per Strategy = 12; HAS_SCENARIO is scenario.strategy_id
    (1:N FK column), no separate edge write needed."""
    for sc in SCENARIOS:
        await db.upsert_vertex("scenario", _vertex_id(sc), _without_id(sc))
    return len(SCENARIOS)


async def _seed_portfolios(db: InvestmentDB) -> int:
    """Step 8 — exactly one defender=true (mechanically enforced by the
    partial unique index ux_portfolio_defender); + HOLDS + DESIGNED_FOR."""
    for pf in PORTFOLIOS:
        await db.upsert_vertex("portfolio", _vertex_id(pf), _without_id(pf))
    today = date.today().isoformat()
    for portfolio_id, strategy_id, is_primary in HOLDS_EDGES:
        await db.create_edge(
            "holds", portfolio_id, strategy_id,
            # 0-1 fraction, not a percent — matches every other "weight"-like
            # field in this schema (Invariant.weight_*, BACKED_BY.strength).
            {"is_primary": is_primary, "weight": 1.0, "since": today},
        )
    for portfolio_id, regime_type_id, rationale in DESIGNED_FOR_EDGES:
        await db.create_edge(
            "designed_for", portfolio_id, regime_type_id, {"rationale": rationale}
        )
    return len(PORTFOLIOS)


async def run_seed(settings: Settings) -> None:
    db = InvestmentDB(settings.db_path)
    inventory: dict[str, int] = {}
    try:
        inventory["user_profile+reference_rows"] = await _seed_reference_tables(db, settings)
        inventory["framework"] = await _seed_frameworks(db)
        inventory["regime_type"] = await _seed_regime_types(db)
        inventory["invariant"] = await _seed_invariants(db)
        inventory["strategy"] = await _seed_strategies(db)
        inventory["scenario"] = await _seed_scenarios(db)
        inventory["portfolio"] = await _seed_portfolios(db)

        for step, reason in DEFERRED_STEPS.items():
            logger.warning("UC0 step %s SKIPPED (%s)", step, reason)

        event_id = await db.append_event(
            type="SeedEvent",
            source_uc="UC0",
            source_id=None,
            payload={
                "schema_version": SCHEMA_VERSION,
                "inventory": inventory,
                "deferred_steps": DEFERRED_STEPS,
            },
        )
        logger.info("SeedEvent appended: %s", event_id)
        logger.info("Seed inventory: %s", inventory)
    finally:
        await db.close()


def main() -> None:
    # pydantic-settings populates required fields from .env at runtime;
    # mypy can't see that, hence the inline ignore + reason (CLAUDE.md
    # "Dev standards" mypy rule).
    settings = Settings()  # type: ignore[call-arg]
    asyncio.run(run_seed(settings))


if __name__ == "__main__":
    main()
