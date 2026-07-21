"""UC0 Seed — `python -m investment.seed` (docs/USE_CASES.md UC0).

Idempotent: every vertex/edge write is an UPSERT (or an idempotent
INSERT OR REPLACE for the market_data TS), safe to re-run.

M1+M2+M3+M4 scope: steps 1-5, 7, 8 (the static graph — reference tables,
Framework, RegimeType, Invariant, Strategy + BACKED_BY, Scenario, Portfolio
+ HOLDS/DESIGNED_FOR), step 9 (MarketData TS backfill: Yahoo + FRED/ALFRED,
HISTORY_PROXIES splice, GROWTH_COMPOSITE/GLOBAL_LIQUIDITY composites), step
10 (historical Regime materialization — market/regime.py `detect()`, the
same code path the live catch-up uses), step 12 (PortfolioNAV TS backfill —
mechanical/ratios.py `backfill_nav`) and step 13 (UC6 valuation + UC7
ranking bootstrap — mechanical/ratios.py `value_portfolios` +
mechanical/snapshots.py `build_snapshot`). M5 adds step 10b (benchmark_
valuation + the `real_rate` derived signal — mechanical/backtests.py
`materialize_benchmark_valuation`), step 11 (Backtest rows + FAVORS edges —
mechanical/backtests.py `run_backtests_and_favors`), step 11b (invariant
birth maturation over the full 35y history — mechanical/invariants.py
`mature_seed_invariants`), step 11c (ScenarioProbability warm-start from 35y
base rates — mechanical/scenarios.py `warm_start_scenario_probabilities`),
and the invariant contradiction check that runs after 11b/11c
(mechanical/invariants.py `check_contradictions`). Step 6/6b (corpus) is
added by M7 (docs/MILESTONES.md "Incremental seed") — this run logs it as
SKIPPED, not silently omitted.

UC0 is the one documented exemption to the "EventLog precedes commit" rule
(CLAUDE.md "EventLog" rule): the closing
SeedEvent is a summary appended AFTER the vertices it describes, not before.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pandas as pd

from investment.config import Settings
from investment.corpus import embedding
from investment.corpus import ingester as corpus_ingester
from investment.db.seed_data import (
    ALL_WEATHER_BENCHMARK,
    ALLOWED_TICKERS,
    BACKED_BY_EDGES,
    DERIVED_SIGNALS,
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
from investment.market import derivatives, fetcher, growth, liquidity, regime, splice
from investment.mechanical import backtests, invariants, ratios, scenarios, snapshots
from investment.worker import curator as curator_mod
from investment.writeback import knowledge

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
#
# TIP is deliberately NOT here — see docs/IMPROVEMENTS.md I-34. Its
# TIP/VIPSX splice IS rejected daily (0.890 vs MIN_RETURN_CORR 0.94) and
# WOULD clear resampled (0.9953 monthly), but M2 already weighed that pair
# and answered it in the portfolio instead (TIP -> IEF), so admitting it now
# is a change of an owner decision, not a bug fix.
# VCIT (IG corporate credit) joins on the GLD-style cause: its daily returns
# vs VFICX carry a duration/segment noise (0.835 daily) that washes out at
# monthly resolution (0.978). Owner-requested addition, so no TIP-style prior
# decision is being overridden.
RESAMPLED_VALIDATION_TICKERS = frozenset({"GLD", "SHY", "VCIT"})

# Steps deferred to later milestones (docs/MILESTONES.md "Incremental seed").
# Steps 6/6b (corpus + initial curation) landed with M7 and are no longer here.
DEFERRED_STEPS: dict[str, str] = {}

# Filename substring -> document author, for step 6. Only authors with their
# own weight tier need an entry (CLAUDE.md "Invariant weight model": dalio
# 0.40, marks 0.35); everything else is the 'other' tier by default and needs
# no mapping. Keyed on the filename because the corpus lives outside the repo
# and the file IS the only metadata we have.
CORPUS_AUTHORS = {
    "principles": "Ray Dalio",
    "big_debt": "Ray Dalio",
    "changing_world_order": "Ray Dalio",
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


# The guard compares the fresh series' date SPAN against the stored one, and
# tolerates a small contraction (a vendor revising away a stale tail).
#
# NOT row count. Row count is the signal the bug corrupts: re-dating INFLATES
# the stored series (M2SL held 1768 rows for 418 real monthly observations),
# so a count test reads the CLEAN fetch as a 76% shortfall and refuses to
# replace exactly the series that needs replacing — the guard fires hardest
# on the worst pollution. Span answers the question actually being asked:
# does the source still carry the history we hold?
_SPAN_TOLERANCE = 0.90


async def _write_series_authoritatively(
    db: InvestmentDB, ticker: str, rows: list[dict[str, Any]]
) -> str | None:
    """Make `market_data` MIRROR the source for one successfully-fetched
    ticker, instead of accumulating every code version's dating
    (docs/IMPROVEMENTS.md I-30, re-dating half).

    `append_ts_batch` is INSERT OR REPLACE keyed on (ticker, ts), so a change
    to `availability_lag_days` / source / frequency re-dates every
    observation and writes NEW rows BESIDE the orphaned old ones. Measured on
    the live DB: M2SL held 1768 rows at 7-day spacing where 35y monthly is
    ~420 — several overlapping copies of one series. That is not cosmetic:
    the first consumer to read M2SL from the DB (`m2_yoy`, M5) computed
    year-over-year growth spanning -62.6%..+213.9%, because a 365d lookback
    landed on a DIFFERENT copy. Real M2 YoY spans about -4%..+27%. The
    invariant resting on it got a confident, meaningless verdict.

    GUARD (I-30: "never delete on a hunch"): the delete is abandoned when the
    fresh series' date SPAN no longer covers the stored one — a truncated
    vendor response must never wipe 35 years. The additive write still
    happens, so the run degrades to the old accumulate-behaviour for that
    ticker and REPORTS, rather than losing data. Returns a description when
    it trips, else None."""
    if not rows:
        return None
    stored = (
        await db.query(
            "SELECT COUNT(*) AS n, MIN(ts) AS lo, MAX(ts) AS hi FROM market_data WHERE ticker = :t",
            t=ticker,
        )
    )[0]
    if stored["n"]:
        # ISO-8601 dates sort lexicographically — no parsing needed to bound.
        timestamps = [str(r["ts"]) for r in rows]
        fresh_span = date.fromisoformat(max(timestamps)) - date.fromisoformat(min(timestamps))
        stored_span = date.fromisoformat(str(stored["hi"])) - date.fromisoformat(str(stored["lo"]))
        if fresh_span < stored_span * _SPAN_TOLERANCE:
            message = (
                f"fetched span {fresh_span.days}d vs {stored_span.days}d stored — "
                "delete abandoned, wrote additively"
            )
            logger.warning("step 9: %s %s", ticker, message)
            await db.append_ts_batch("market_data", rows)
            return message
    # NOT wrapped in `db.transaction()`: `append_ts_batch` issues its own
    # BEGIN/COMMIT (its whole reason for existing is one fsync per batch), and
    # SQLite has no nested transactions. The window between the two is
    # tolerable precisely because this step is idempotent — a crash in it
    # leaves the ticker empty until the next run re-fetches and rewrites,
    # which is the same recovery any other mid-seed failure gets.
    await db.command("DELETE FROM market_data WHERE ticker = :t", t=ticker)
    await db.append_ts_batch("market_data", rows)
    return None


async def _prune_retired_series(db: InvestmentDB) -> dict[str, Any]:
    """Step 1b (M5): drop rows for tickers no longer in the AUTHORITATIVE
    universe (`ALLOWED_TICKERS` + `DERIVED_SIGNALS`).

    `allowed_tickers` is wholly owned by db/seed_data.py, but the seed only
    ever INSERT-OR-REPLACEs, so a retired ticker's row — and its whole
    series — survives every later run (docs/IMPROVEMENTS.md I-30). That is
    NOT inert, which is why it is fixed here rather than left deferred:
    `allowed_tickers` is what `mechanical/backtests.py investable_tickers`
    reads, so a ghost row makes `asset:<retired>` a VALID invariant handle,
    matured against a series frozen at its retirement date; and it is the
    same table that will gate the Worker's `market_fetch` (M8). Retired at
    M2: BIL (superseded by the synthetic 'cash' sleeve), EURUSD=X / JPY=X
    (superseded by FRED DEXUSEU / DEXJPUS).

    The DERIVED signals (`real_rate`, `real_yield_10y`) live ONLY in
    DERIVED_SIGNALS, never in ALLOWED_TICKERS — hence the union. Omitting it
    would delete the very signals step 10b materialises.

    Safe by construction: nothing FKs to `allowed_tickers`, and every row
    removed is reconstructible from seed_data + the network by the same run
    that prunes it. Scope is the RETIRED-TICKER half of I-30 only; the
    re-dated-duplicate half (M2SL's overlapping copies) needs the
    authoritative-backfill design and stays deferred."""
    keep = sorted({str(t["ticker"]) for t in ALLOWED_TICKERS} | set(DERIVED_SIGNALS))
    placeholders = ", ".join(f":k{i}" for i in range(len(keep)))
    params = {f"k{i}": t for i, t in enumerate(keep)}

    retired = [
        str(r["ticker"])
        for r in await db.query(
            f"SELECT ticker FROM allowed_tickers WHERE ticker NOT IN ({placeholders}) "
            "ORDER BY ticker",
            **params,
        )
    ]
    stale_rows = (
        await db.query(
            f"SELECT COUNT(*) AS n FROM market_data WHERE ticker NOT IN ({placeholders})", **params
        )
    )[0]["n"]

    async with db.transaction():
        await db.command(
            f"DELETE FROM allowed_tickers WHERE ticker NOT IN ({placeholders})", **params
        )
        await db.command(f"DELETE FROM market_data WHERE ticker NOT IN ({placeholders})", **params)
        # Derived asset benchmarks follow their ticker: 10b only rewrites the
        # ids it still considers investable, so a retired one's rows would
        # otherwise linger and keep `asset:<retired>` resolvable.
        await db.command(
            f"DELETE FROM benchmark_valuation WHERE benchmark_kind = 'asset' "
            f"AND benchmark_id NOT IN ({placeholders})",
            **params,
        )
    if retired:
        logger.warning(
            "step 1b: pruned retired tickers %s (%d market_data rows)", retired, stale_rows
        )
    return {"retired_tickers": retired, "market_data_rows_pruned": stale_rows}


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


_MATURATION_FIELDS = (
    "market_score",
    "recency_factor",
    "confirmation_count",
    "infirmation_count",
    "weight_effective",
    "status",
    "validated_at",
    "trace",
)


async def _seed_invariants(db: InvestmentDB) -> int:
    """Step 4 — status='proposed'; matured over 35y at M5 (ADR-006: belief
    does not grant integration, history does). market_score/recency_factor
    default to 1.0 pre-confrontation; weight_effective follows the pinned
    formula (CLAUDE.md 'Invariant weight model'). A RE-RUN must not clobber
    an already-matured invariant's mechanical state back to these pristine
    defaults — for a row that already exists, `_MATURATION_FIELDS` are
    re-written to their CURRENT value (a no-op update; `upsert_vertex`
    requires `trace` present even on update, so these can't just be omitted
    from `props`) rather than reset. The M5 `mature_seed_invariants()`
    idempotency guard (invariants.py `_already_matured`) depends on
    `trace`/`status` surviving a re-seed."""
    existing = {
        str(r["id"]): r
        for r in await db.query(
            "SELECT id, market_score, recency_factor, confirmation_count, infirmation_count, "
            "weight_effective, status, validated_at, trace FROM invariant"
        )
    }
    for inv in INVARIANTS:
        props = _without_id(inv)
        existing_row = existing.get(_vertex_id(inv))
        if existing_row is None:
            props["market_score"] = 1.0
            props["recency_factor"] = 1.0
            props["confirmation_count"] = 0
            props["infirmation_count"] = 0
            weight_initial = cast("float", props["weight_initial"])
            floor_weight = cast("float", props["floor_weight"])
            props["weight_effective"] = max(weight_initial, floor_weight)
        else:
            for field in _MATURATION_FIELDS:
                props[field] = existing_row[field]
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
    shortfalls: dict[str, str] = {}
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
        replaced = await _write_series_authoritatively(db, ticker, rows)
        if replaced is not None:
            shortfalls[ticker] = replaced
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
        "authoritative_write_shortfalls": shortfalls,
        "tradable_floor": tradable_floor,
        "splice_reports": splice_reports,
    }


async def _materialize_regimes(db: InvestmentDB) -> dict[str, Any]:
    """Step 10: historical Regime materialization — `market/regime.py`'s
    `detect()` is ONE code path shared by UC0 (this call, over the full 35y
    backfill step 9 just persisted), the Phase 9 replay, the Monday catch-up,
    and the on-demand UC9 prelude (docs/DATA_MODELS.md Regime entity)."""
    commits = await regime.detect(db)
    return {"regime_episodes": len(commits)}


async def _materialize_benchmark_valuation(db: InvestmentDB) -> dict[str, Any]:
    """Step 10b (M5): benchmark_valuation (asset_class + strategy rows) +
    the `real_rate` derived signal — mechanical/backtests.py `materialize_
    benchmark_valuation` (docs/USE_CASES.md UC0 step 10b), the prerequisite
    for invariant maturation ("define and value the benchmarks before
    valuing invariants").

    The window is the CONFRONTATION horizon (proposal_outcome_weeks, in
    trading days), not `rolling_window_days` — this table is read only by
    the confrontation, whose window it must therefore match
    (mechanical/backtests.py `period_series_frame`)."""
    horizon_trading_days = int(
        SYSTEM_THRESHOLDS["proposal_outcome_weeks"] * ratios.TRADING_DAYS_PER_WEEK
    )
    lookback = int(SYSTEM_THRESHOLDS["derivative_lookback_short"])
    result = await backtests.materialize_benchmark_valuation(db, horizon_trading_days, lookback)
    return dataclasses.asdict(result)


async def _run_backtests_favors(db: InvestmentDB) -> dict[str, Any]:
    """Step 11 (M5): Backtest rows + FAVORS edges — mechanical/backtests.py
    `run_backtests_and_favors` (docs/USE_CASES.md UC0 step 11)."""
    window = int(SYSTEM_THRESHOLDS["rolling_window_days"])
    result = await backtests.run_backtests_and_favors(db, window)
    return dataclasses.asdict(result)


async def _mature_seed_invariants(db: InvestmentDB) -> dict[str, Any]:
    """Step 11b (M5): birth maturation of the 6 seed invariants over the
    full 35y history (mechanical/invariants.py `mature_seed_invariants`) —
    the SAME factored, source-blind mechanism later applied to every
    post-launch birth (docs/USE_CASES.md UC0 step 11b; ADR-006)."""
    results = await invariants.mature_seed_invariants(db)
    return {"invariants": [dataclasses.asdict(r) for r in results]}


async def _warm_start_scenario_probabilities(db: InvestmentDB) -> dict[str, Any]:
    """Step 11c (M5): ScenarioProbability warm-start from 35y base rates —
    mechanical/scenarios.py `warm_start_scenario_probabilities`
    (docs/USE_CASES.md UC0 step 11c)."""
    return await scenarios.warm_start_scenario_probabilities(db)


async def _check_invariant_contradictions(db: InvestmentDB) -> dict[str, Any]:
    """Pairwise contradiction check over `status='integrated'` invariants —
    docs/ARCHITECTURE.md 'Invariant contradiction check': "Runs at seed
    (after 11b/11c)"."""
    contradictions = await invariants.check_contradictions(db)
    return {"contradictions": [dataclasses.asdict(c) for c in contradictions]}


async def _seed_portfolio_nav(db: InvestmentDB) -> dict[str, Any]:
    """Step 12 (M4): PortfolioNAV TS backfill — the ALL_WEATHER_BENCHMARK
    synthetic series FIRST (so per-portfolio vs_benchmark can read it back,
    mechanical/ratios.py `backfill_nav` docstring), then one series per
    Portfolio, from the date all constituents exist (docs/TASKS.md Task
    1ter.7 item 4)."""
    window = int(SYSTEM_THRESHOLDS["rolling_window_days"])
    results: dict[str, Any] = {
        ratios.ALL_WEATHER_ID: dataclasses.asdict(
            await ratios.backfill_nav(db, ratios.ALL_WEATHER_ID, ALL_WEATHER_BENCHMARK, window)
        )
    }
    for pf in PORTFOLIOS:
        allocation = cast("dict[str, float]", pf["allocation"])
        result = await ratios.backfill_nav(db, _vertex_id(pf), allocation, window)
        results[_vertex_id(pf)] = dataclasses.asdict(result)
    return results


async def _seed_snapshot(db: InvestmentDB) -> dict[str, Any]:
    """Step 13 (M4): UC6 valuation (updates Portfolio vertices from the
    PortfolioNAV TS just backfilled) + UC7 ranking bootstrap
    (portfolio_weekly_snapshot, one row per enabled Portfolio for the seed
    date, docs/TASKS.md Task 1ter.7 item 5)."""
    window = int(SYSTEM_THRESHOLDS["rolling_window_days"])
    valuations = await ratios.value_portfolios(db, window)
    ranked = await snapshots.build_snapshot(db, SYSTEM_THRESHOLDS["ranking_tiebreak_window"])
    return {"portfolios_valued": len(valuations), "snapshot_rows": len(ranked)}


async def _seed_corpus(db: InvestmentDB, settings: Settings) -> dict[str, Any]:
    """Step 6 (M7): ingest every supported source under `sources_path`.

    `sources_path` (config.py, `SOURCES_PATH`) is the corpus's canonical home
    and this is its first consumer. The books deliberately do NOT live in the
    repo: they are large and copyrighted, `.gitignore` excludes them, and a
    corpus kept beside the code is one `git add -A` away from being published.

    Idempotent twice over: `ingest_file` overwrites the same document/passage
    ids for the same title, and a missing or empty directory is a no-op rather
    than an error — a fresh clone with no corpus must still seed."""
    embedder = embedding.InProcessEmbedder(settings.embedding_model)
    ingester = await corpus_ingester.CorpusIngester.from_db(db, embedder)
    if not settings.sources_path.is_dir():
        logger.warning("UC0 step 6: no corpus at %s — nothing to ingest", settings.sources_path)
        return {"documents": 0, "passages": 0}
    sources = sorted(
        path
        for path in settings.sources_path.rglob("*")
        if path.is_file() and path.suffix.lower() in corpus_ingester.SUPPORTED_SUFFIXES
    )
    results: dict[str, Any] = {"documents": 0, "passages": 0, "supports": 0}
    for path in sources:
        result = await ingester.ingest_file(path, kind="book", author=_corpus_author(path))
        results["documents"] += 1
        results["passages"] += result.chunk_count
        results["supports"] += result.supports_created
    logger.info("UC0 step 6: %s", results)
    return results


def _corpus_author(path: Path) -> str | None:
    """The document's author, read from the filename.

    Deliberately crude, and it only has to be: `writeback/knowledge.py` maps
    this to an author TIER by substring, and anything unrecognised falls to
    the conservative 'other' tier (floor 0.20). Getting it wrong under-weights
    a book; it can never over-weight one."""
    stem = path.stem.lower()
    for needle, author in CORPUS_AUTHORS.items():
        if needle in stem:
            return author
    return None


async def _seed_curation(db: InvestmentDB, settings: Settings) -> dict[str, Any]:
    """Step 6b (M7): the initial curation pass over every ingested document.

    The ONE step in the seed that calls an LLM, and the reason the checkpoint
    had to exist first: `curate_document` skips passages already curated under
    the same fingerprint, so this is expensive EXACTLY ONCE. Re-running the
    seed — which the module docstring promises is safe — then costs nothing.

    A document that fails does not abort the seed: its passages stay unmarked
    and the next run retries precisely those. Same policy as the watcher and
    the curator's own batch loop, for the same reason — a long job must not
    lose hours of work to one bad response."""
    embedder = embedding.InProcessEmbedder(settings.embedding_model)
    writer = knowledge.KnowledgeWriteback(db, embedder)
    curator = curator_mod.KnowledgeCurator(
        db,
        model_name=settings.planner_model,
        api_key=settings.openrouter_api_key,
        reasoning_effort=settings.curator_reasoning_effort,
    )
    documents = await db.query("SELECT id, title FROM document ORDER BY id")
    results: dict[str, Any] = {"documents": 0, "candidates": 0, "failed": []}
    for row in documents:
        document_id = str(row["id"])
        try:
            scored = await curator.curate_document(document_id, writer)
        except Exception as exc:  # one bad document must not cost the others
            logger.warning("UC0 step 6b: %s FAILED — %s: %s", row["title"], type(exc).__name__, exc)
            results["failed"].append(document_id)
            continue
        results["documents"] += 1
        results["candidates"] += len(scored)
    # The same rule as the batch loop one level down, for the same reason: a
    # per-document `except` that never escalates turns a total outage into a
    # tidy inventory line. If nothing at all got through, the seed must say so.
    if results["failed"] and results["documents"] == 0:
        raise RuntimeError(
            f"UC0 step 6b: every document failed curation ({len(results['failed'])}) — "
            "see the warnings above; nothing was persisted"
        )
    logger.info("UC0 step 6b: %s", results)
    return results


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
        inventory["pruned"] = await _prune_retired_series(db)
        inventory["framework"] = await _seed_frameworks(db)
        inventory["regime_type"] = await _seed_regime_types(db)
        inventory["invariant"] = await _seed_invariants(db)
        inventory["strategy"] = await _seed_strategies(db)
        inventory["scenario"] = await _seed_scenarios(db)
        inventory["portfolio"] = await _seed_portfolios(db)
        inventory["market_data"] = await _seed_market_data(
            db, settings, fetch_raw=fetch_raw, yahoo_rate_limit_seconds=yahoo_rate_limit_seconds
        )
        inventory["regime"] = await _materialize_regimes(db)
        # Step 12 (PortfolioNAV backfill) runs BEFORE 10b/11: benchmark_
        # valuation's strategy rows and Backtest rows both read a Strategy's
        # primary-portfolio NAV (mechanical/backtests.py "prescribed
        # allocation" resolution) — numeric step order (10b/11 < 12) is not
        # execution order here, only step 13 (snapshot) stays last.
        inventory["portfolio_nav"] = await _seed_portfolio_nav(db)
        inventory["benchmark_valuation"] = await _materialize_benchmark_valuation(db)
        inventory["backtests_favors"] = await _run_backtests_favors(db)
        # Steps 6/6b sit HERE — after 10b, before 11b — and the position is
        # load-bearing at both ends:
        #   after 10b, because `signal_ranges` inlines the observed range of
        #     every signal into the curator prompt, and `real_rate` is only
        #     materialised by step 10b;
        #   before 11b, because MILESTONES says corpus invariants are "matured
        #     the same way" and `mature_seed_invariants` is source-blind ("on
        #     every invariant"). Running maturation first — as this did until
        #     2026-07-21 — left 34 corpus invariants with 0 confrontations and
        #     no verdict, silently exempt from the engine that exists to judge
        #     them. Nothing failed; they were simply never measured.
        inventory["corpus"] = await _seed_corpus(db, settings)
        inventory["curation"] = await _seed_curation(db, settings)
        inventory["invariant_maturation"] = await _mature_seed_invariants(db)
        inventory["scenario_warm_start"] = await _warm_start_scenario_probabilities(db)
        # After curation too: a contradiction between a seeded invariant and a
        # freshly extracted one is exactly the kind the check exists to catch.
        inventory["invariant_contradictions"] = await _check_invariant_contradictions(db)
        inventory["snapshot"] = await _seed_snapshot(db)

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
