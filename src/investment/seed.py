"""UC0 Seed — `python -m investment.seed` (docs/USE_CASES.md UC0).

Idempotent: every vertex/edge write is an UPSERT (or an idempotent
INSERT OR REPLACE for the market_data TS), safe to re-run.

M1+M2 scope: steps 1-5, 7, 8 (the static graph — reference tables,
Framework, RegimeType, Invariant, Strategy + BACKED_BY, Scenario, Portfolio
+ HOLDS/DESIGNED_FOR) plus step 9 (MarketData TS backfill: Yahoo + FRED/
ALFRED, HISTORY_PROXIES splice, GROWTH_COMPOSITE/GLOBAL_LIQUIDITY
composites). Steps 6/6b (corpus), 10/10b (regime materialization +
benchmark valuation), 11/11b/11c (backtests/FAVORS/maturation/warm-start),
12-13 (NAV/snapshot) are added by later milestones (docs/MILESTONES.md
"Incremental seed") — this run logs them as SKIPPED, not silently omitted.

UC0 is the one documented exemption to the "EventLog precedes commit" rule
(CLAUDE.md "EventLog" rule): the closing
SeedEvent is a summary appended AFTER the vertices it describes, not before.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pandas as pd

from investment.config import Settings
from investment.db.seed_data import (
    ALLOWED_TICKERS,
    BACKED_BY_EDGES,
    DESIGNED_FOR_EDGES,
    FRAMEWORKS,
    HISTORY_PROXIES,
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
from investment.market import derivatives, fetcher, growth, liquidity, splice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

# Injection point for the one external-I/O boundary in this module (docs/
# CLAUDE.md tests: real SQLite, no mocks — but the network fetch is not the
# DB, and a live Yahoo/FRED call has no place in a unit test). Defaults to
# the real fetcher; tests pass a synthetic stub.
FetchRawFn = Callable[[Mapping[str, Any], str, date | None], Awaitable[pd.Series]]

# ETFs whose daily RETURNS are too noisy (vs their proxy) to validate at
# daily resolution, even though the proxy itself is genuinely daily — see
# splice.splice_with_resampled_validation. Keyed by the ETF ticker, not the
# proxy's source: SHY's proxy (VFISX) is fetched the same "yahoo" way as
# every clean daily pair, but SHY's own ultra-low volatility (~1.5%/y)
# means day-to-day noise swamps the correlation signal — verified live at
# M2 build time: SHY vs VFISX correlates 0.81 daily but 0.963 at monthly
# resolution. GLD's case is different in cause (a fixing-time mismatch,
# not low volatility) but identical in mechanism and fix.
RESAMPLED_VALIDATION_TICKERS = frozenset({"GLD", "SHY"})

# Steps deferred to later milestones (docs/MILESTONES.md "Incremental seed").
DEFERRED_STEPS = {
    "6": "corpus seed (M7)",
    "6b": "initial curation pass (M7)",
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
            key=key,
            value=value,
            now=now,
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
        weight_initial = cast("float", props["weight_initial"])
        floor_weight = cast("float", props["floor_weight"])
        props["weight_effective"] = max(weight_initial, floor_weight)
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
            "backed_by",
            strategy_id,
            invariant_id,
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
            "holds",
            portfolio_id,
            strategy_id,
            # 0-1 fraction, not a percent — matches every other "weight"-like
            # field in this schema (Invariant.weight_*, BACKED_BY.strength).
            {"is_primary": is_primary, "weight": 1.0, "since": today},
        )
    for portfolio_id, regime_type_id, rationale in DESIGNED_FOR_EDGES:
        await db.create_edge("designed_for", portfolio_id, regime_type_id, {"rationale": rationale})
    return len(PORTFOLIOS)


def _rows_from_derivatives(
    ticker: str, asset_class: str, currency: str, deriv: pd.DataFrame, start: date | None
) -> list[dict[str, Any]]:
    df = deriv if start is None else deriv[deriv.index >= pd.Timestamp(start)]
    df = df.astype(object).where(pd.notna(df), None)
    # pandas-stubs types to_dict("records") keys as Hashable (the general
    # case); every column here is a plain string (level/speed/acceleration).
    records = cast("list[dict[str, Any]]", df.to_dict("records"))
    return [
        {
            "ticker": ticker,
            "asset_class": asset_class,
            "currency": currency,
            "ts": ts.date().isoformat(),
            **record,
        }
        for ts, record in zip(df.index, records, strict=True)
    ]


async def _seed_market_data(
    db: InvestmentDB,
    settings: Settings,
    *,
    fetch_raw: FetchRawFn = fetcher.fetch_raw_series,
    yahoo_rate_limit_seconds: float = fetcher.YAHOO_RATE_LIMIT_SECONDS,
) -> dict[str, Any]:
    """Step 9: 35y macro backfill (FRED/ALFRED, publication-dated per
    ADR-003) + tradable ETFs spliced with HISTORY_PROXIES back to ~1991
    (docs/USE_CASES.md UC0 step 9) + GROWTH_COMPOSITE/GLOBAL_LIQUIDITY
    (docs/TASKS.md Task 2.2). A per-ticker fetch or splice failure is logged
    and recorded in the returned inventory rather than aborting the whole
    step — there is no scheduler/Telegram alerting yet to escalate to
    (that lands at M9); the owner inspects `skipped` in the SeedEvent.
    `fetch_raw` defaults to the real network fetcher; tests inject a
    synthetic stub (no live Yahoo/FRED call in a unit test) and zero out
    `yahoo_rate_limit_seconds` — that pacing only matters against the real
    Yahoo API."""
    api_key = settings.fred_api_key
    target_start = date.today() - timedelta(days=365 * settings.market_backfill_years)
    default_lookback = int(SYSTEM_THRESHOLDS["derivative_lookback_short"])

    # Post-transform, pre-truncation — the composites below read from here
    # so their own rolling windows see the full fetched history, not just
    # the truncated-for-storage tail.
    transformed: dict[str, pd.Series] = {}
    tradable_floor: dict[str, str] = {}
    splice_reports: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    row_count = 0
    yahoo_calls = 0

    fetchable = [t for t in ALLOWED_TICKERS if t["source"] in ("yahoo", "fred")]
    for ticker_row in fetchable:
        ticker = str(ticker_row["ticker"])
        source = str(ticker_row["source"])
        if source == "yahoo":
            if yahoo_calls:
                await asyncio.sleep(yahoo_rate_limit_seconds)
            yahoo_calls += 1
        try:
            raw = await fetch_raw(ticker_row, api_key, None)
        except Exception as exc:
            logger.warning("step 9: %s fetch failed, skipped: %s", ticker, exc)
            skipped[ticker] = str(exc)
            continue
        raw = fetcher.forward_fill_gaps(raw)

        level = raw
        proxy_cfg = HISTORY_PROXIES.get(ticker)
        if proxy_cfg is not None:
            proxy_ticker, proxy_source, _inception = proxy_cfg
            if proxy_source == "yahoo":
                if yahoo_calls:
                    await asyncio.sleep(yahoo_rate_limit_seconds)
                yahoo_calls += 1
            try:
                proxy_row = {
                    "ticker": proxy_ticker,
                    "source": proxy_source,
                    "availability_lag_days": 0,
                }
                proxy_raw = await fetch_raw(proxy_row, api_key, None)
                splice_fn = (
                    splice.splice_with_resampled_validation
                    if ticker in RESAMPLED_VALIDATION_TICKERS
                    else splice.splice_level_series
                )
                level, report = splice_fn(ticker, proxy_ticker, raw, proxy_raw)
                splice_reports.append(dataclasses.asdict(report))
            except Exception as exc:
                logger.warning(
                    "step 9: splice %s/%s rejected, ETF-only floor: %s",
                    ticker,
                    proxy_ticker,
                    exc,
                )

        tradable_floor[ticker] = str(level.sort_index().index.min().date())

        transformed_level = derivatives.apply_transform(level, str(ticker_row["transform"]))
        transformed[ticker] = transformed_level

        deriv = derivatives.compute_derivatives(transformed_level, ticker, default_lookback)
        persist_start = target_start if source == "fred" else None
        asset_class, currency = str(ticker_row["asset_class"]), str(ticker_row["currency"])
        rows = _rows_from_derivatives(ticker, asset_class, currency, deriv, persist_start)
        await db.append_ts_batch("market_data", rows)
        row_count += len(rows)

    # Composites (docs/TASKS.md Task 2.2) — computed from the full as-known
    # history collected above, truncated to target_start before persisting.
    if "INDPRO" in transformed and "UNRATE" in transformed:
        gc = growth.compute_growth_composite(transformed["INDPRO"], transformed["UNRATE"])
        deriv = derivatives.compute_derivatives(gc, "GROWTH_COMPOSITE", default_lookback)
        rows = _rows_from_derivatives("GROWTH_COMPOSITE", "MACRO", "USD", deriv, target_start)
        await db.append_ts_batch("market_data", rows)
        row_count += len(rows)
    else:
        skipped["GROWTH_COMPOSITE"] = "missing INDPRO/UNRATE inputs"

    liquidity_tickers = ("M2SL", "WALCL", "ECBASSETSW", "JPNASSETS")
    if all(t in transformed for t in (*liquidity_tickers, "DEXUSEU", "DEXJPUS")):
        eurusd, usdjpy = transformed["DEXUSEU"], transformed["DEXJPUS"]
        usd_components = {
            t: liquidity.usd_convert(t, transformed[t], eurusd, usdjpy) for t in liquidity_tickers
        }
        gl = liquidity.compute_global_liquidity(usd_components)
        deriv = derivatives.compute_derivatives(gl, "GLOBAL_LIQUIDITY", default_lookback)
        rows = _rows_from_derivatives(
            "GLOBAL_LIQUIDITY", "GLOBAL_LIQUIDITY", "USD", deriv, target_start
        )
        await db.append_ts_batch("market_data", rows)
        row_count += len(rows)
    else:
        skipped["GLOBAL_LIQUIDITY"] = "missing component inputs"

    tickers_ok = len(fetchable) - len([k for k in skipped if k in {t["ticker"] for t in fetchable}])
    return {
        "market_data_rows": row_count,
        "tickers_ok": tickers_ok,
        "tickers_skipped": skipped,
        "tradable_floor": tradable_floor,
        "splice_reports": splice_reports,
    }


async def run_seed(
    settings: Settings,
    *,
    fetch_raw: FetchRawFn = fetcher.fetch_raw_series,
    yahoo_rate_limit_seconds: float = fetcher.YAHOO_RATE_LIMIT_SECONDS,
) -> None:
    """`fetch_raw` defaults to the real Yahoo/FRED fetcher; tests inject a
    synthetic stub (and zero the rate limit) so the run stays hermetic and
    fast (see `_seed_market_data`)."""
    db = InvestmentDB(settings.db_path)
    inventory: dict[str, Any] = {}
    try:
        inventory["user_profile+reference_rows"] = await _seed_reference_tables(db, settings)
        inventory["framework"] = await _seed_frameworks(db)
        inventory["regime_type"] = await _seed_regime_types(db)
        inventory["invariant"] = await _seed_invariants(db)
        inventory["strategy"] = await _seed_strategies(db)
        inventory["scenario"] = await _seed_scenarios(db)
        inventory["portfolio"] = await _seed_portfolios(db)
        inventory["market_data"] = await _seed_market_data(
            db, settings, fetch_raw=fetch_raw, yahoo_rate_limit_seconds=yahoo_rate_limit_seconds
        )

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
