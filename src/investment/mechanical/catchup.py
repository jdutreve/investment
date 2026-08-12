"""UC1 — the Monday 08:00 catch-up (docs/USE_CASES.md UC1 "Market Feed";
CLAUDE.md "Scheduling": "08:00 catch-up (market TS, regime step per new monthly
print, NAV)").

Three things, in this order, because each feeds the next: refresh the market
series, step the regime detector over whatever new prints arrived, rebuild the
portfolio NAVs on the new prices. Everything downstream in the chain — FAVORS,
the ranking, the monthly allocation decision — reads what this leaves behind, so
a chain without it ranks last week's world.

WHY THIS IS NOT A RE-SEED, and why it had to be written rather than reused. The
seed's step 9 (`seed._seed_market_data`) fetches each series from ITS BEGINNING
— 35 years, every ticker, every run — splices the history proxies and rewrites
the series authoritatively. That is right for a backfill and wrong for a weekly run:
it re-downloads ~35 years to learn five new closes, and it pays Yahoo's rate
limit on every one of seventeen tickers.

SO THE HISTORY COMES FROM THE DATABASE AND ONLY THE TAIL COMES FROM THE NETWORK.
Per series: read the stored level, fetch a bounded recent window, merge with the
new values winning, recompute the derivatives over the WHOLE merged series, and
write back only the refreshed window. The derivatives are therefore computed on
exactly the input the seed computes them on, which is what keeps the two paths
from producing different numbers for the same day (CLAUDE.md: "two
implementations must produce the same numbers").

THE TAIL IS RATIO-CHAINED ONTO THE STORED SCALE, NEVER PASTED ONTO IT, and this
is the part that took a live check to get right. A spliced ticker's stored level
is not its price: `splice._construct_spliced_level` ratio-chains the proxy's
returns onto the ETF's and re-bases the result, so SPY reads ~16150 in
`market_data` while Yahoo quotes ~773 — a factor of 21. Merging the raw quote in
would have written a -95% single-day return into the series every week.

That exact defect has already cost this project an investigation: the same
docstring records a raw ETF price surviving in a one-row hole of a spliced
series, "a ~-91% return followed by a ~+1000% one, straight into every NAV built
on them", found at M4 by the All Weather external check. So the fetched window
contributes its RETURNS, cumulated from the last stored value they overlap —
which is `splice.cumulate_returns`, the same function the splice itself uses.

CHAINED ONLY WHERE THE SCALE IS SYNTHETIC (`HISTORY_PROXIES`), and never for a
macro series. Chaining assumes a strictly positive price index; `T10Y2Y` goes
NEGATIVE at every inversion and a percentage change across zero is meaningless,
while a `yoy_pct` level is already an absolute percentage that needs no
re-basing. Macro values are therefore taken as fetched — which is also what a
re-seed writes for them.

WHAT THE MERGE DELIBERATELY DOES NOT DO: re-splice. `HISTORY_PROXIES` exists to
extend a tradable ETF backwards into a proxy's history, and the stored level
already carries that splice. New data is never in the proxy era, so the splice
has nothing to say about it — and re-running it weekly would re-litigate a
35-year decision on every week.

VINTAGE DISCIPLINE HOLDS (ADR-003). The FRED path fetches ALFRED first releases,
so re-reading an overlapping window returns the same values it returned before.
Yahoo adjusted closes DO get restated (splits, dividends) — a split rescales the
whole series and leaves every RETURN untouched, so a chained tail is immune to
it by construction, which is the other reason to chain rather than paste.

NO EXPIRY SWEEP, and the omission is deliberate. CLAUDE.md's timeline lists one,
and `system_thresholds.proposal_expiry_days` (14) carries the note "(UNWIRED)".
It would mark a pending Proposal `user_response='expired'` — a state that
described the owner failing to answer, in a design where the owner was asked.
ADR-006 removed the asking: a proposal that passes its gates IS the paper-test,
and what closes it is the +12w verdict (`outcomes.evaluate_proposals`). Wiring
"expired" now would stamp a failure-to-respond on proposals nobody was ever
asked to respond to. It stays unwired until something gives it a meaning.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, timedelta
from typing import Any, cast

import pandas as pd

from investment.config import Settings
from investment.db.seed_data import (
    ALL_WEATHER_BENCHMARK,
    ALLOWED_TICKERS,
    HISTORY_PROXIES,
    PORTFOLIOS,
    TIME_VARYING_PORTFOLIOS,
)
from investment.db.sqlite import InvestmentDB
from investment.market import derivatives, fetcher, growth, liquidity, regime, splice
from investment.mechanical import ratios

logger = logging.getLogger(__name__)

FetchRawFn = Callable[[Mapping[str, Any], str, date | None], Awaitable[pd.Series]]

# How far BACK of the stored tail the network window starts, and the number is
# a maximum of three separate requirements rather than a round guess:
#
#   1. `yoy_pct` (CPIAUCSL, INDPRO) is `pct_change(periods=12)` over MONTHLY
#      observations, so the newest point needs 13 observations behind it —
#      ~400 calendar days. Nothing else in the transform vocabulary looks back
#      at all ('none' and 'composite' are passthroughs).
#   2. `compute_derivatives` reads the level as of (t - lookback) for speed and
#      again for acceleration, so a correct tail needs 2 x the longest lookback
#      (`WEEKLY_LOOKBACK_DAYS_TICKERS` tops out at 182 for gold_10y_dev).
#   3. Restatements: Yahoo revises adjusted closes for a split or a dividend
#      some days after the fact, and only the window that is re-read gets the
#      correction.
#
# 500 days clears all three with room. The cost of being generous is one wider
# HTTP response per ticker per week; the cost of being tight is a silently wrong
# speed on the newest print, which is the reading the regime detector acts on.
FETCH_MARGIN_DAYS = 500

# The composites and what they are built from (docs/TASKS.md Task 2.2). Their
# inputs are read back from `market_data` rather than kept in memory as the seed
# does: by the time they are computed the components' rows have just been
# refreshed, so the database IS the full as-known history, and reading it costs
# a query instead of a second network pass.
GROWTH_INPUTS = ("INDPRO", "UNRATE")
LIQUIDITY_COMPONENTS = ("M2SL", "WALCL", "ECBASSETSW", "JPNASSETS")
LIQUIDITY_FX = ("DEXUSEU", "DEXJPUS")


@dataclasses.dataclass(frozen=True)
class CatchupReport:
    """What the catch-up did, for the chain log and `invest status`.

    `skipped` is a map rather than a count: a ticker that stopped updating is
    the failure this whole milestone is built against (mechanical/alerts.py),
    and "3 skipped" would not name it."""

    tickers_refreshed: int
    market_rows: int
    composites: list[str]
    regime_episodes: int
    navs_rebuilt: int
    skipped: dict[str, str]


async def stored_level(db: InvestmentDB, ticker: str) -> pd.Series:
    """The series' stored `level`, ascending by ts — already transformed and,
    for a tradable, already spliced (that is what step 9 persisted)."""
    rows = await db.query(
        "SELECT ts, level FROM market_data WHERE ticker = :t AND level IS NOT NULL ORDER BY ts",
        t=ticker,
    )
    if not rows:
        return pd.Series(dtype=float)
    index = pd.to_datetime([str(r["ts"]) for r in rows])
    return pd.Series([float(r["level"]) for r in rows], index=index)


def rebase_onto(fetched: pd.Series, stored: pd.Series, *, chain: bool) -> pd.Series:
    """The fetched values expressed on the STORED series' scale.

    `chain=False` — the two are already the same quantity (a rate, a YoY
    percentage, an unspliced adjusted close), so the fetch is returned as it
    came and its values replace the stored ones over the overlap.

    `chain=True` — the stored series is a ratio-chained INDEX whose scale is an
    artefact of where the splice re-based it, so the fetch shares its shape and
    not its level. Only the returns after the last common date are kept, and
    they are cumulated from the stored value there: the join carries a real
    return and no cross-instrument jump is invented, which is the same rule
    `splice._construct_spliced_level` follows — and the same function,
    `splice.cumulate_returns`, does the cumulating.

    NO OVERLAP MEANS NO ANCHOR, and that is a refusal rather than a guess: a
    stored series that stopped before the fetch window began cannot be chained
    onto without inventing the return that spans the gap. The caller records it
    and the freshness alert says so on Monday."""
    fetched = fetched.sort_index()
    if not chain or fetched.empty or stored.empty:
        return fetched
    common = stored.index.intersection(fetched.index)
    if common.empty:
        raise LookupError(
            "no overlap between the stored series and the fetched window — "
            "the gap is too wide to chain across; re-seed instead"
        )
    anchor = common.max()
    returns = fetched.pct_change()
    after = returns.loc[returns.index > anchor].dropna()
    if after.empty:
        return pd.Series(dtype=float)
    return splice.cumulate_returns(after, base=float(stored.loc[anchor]))


async def refresh_series(
    db: InvestmentDB,
    ticker_row: Mapping[str, Any],
    api_key: str,
    *,
    fetch_raw: FetchRawFn,
    lookback: int,
) -> int:
    """Refresh ONE series' tail. Returns the number of rows written.

    A series with NO stored history is SKIPPED rather than backfilled, and the
    caller reports it: a catch-up's bounded window would give a newly added
    ticker a truncated history that looks complete, and every rolling window
    computed on it afterwards would be quietly wrong. Backfilling is the seed's
    job, and re-seeding is routine (docs/MILESTONES.md "Incremental seed")."""
    ticker = str(ticker_row["ticker"])
    stored = await stored_level(db, ticker)
    if stored.empty:
        raise LookupError(f"{ticker} has no stored history — run the seed, not the catch-up")

    since = stored.index.max().date() - timedelta(days=FETCH_MARGIN_DAYS)
    raw = await fetch_raw(ticker_row, api_key, since)
    if raw.empty:
        return 0

    fetched = derivatives.apply_transform(
        fetcher.forward_fill_gaps(raw), str(ticker_row["transform"])
    )
    tail = rebase_onto(fetched, stored, chain=ticker in HISTORY_PROXIES)
    if tail.empty:
        return 0
    # `combine_first` fills from the argument only where the caller is missing,
    # so the tail wins wherever it has a value. For a chained series the tail
    # starts strictly after the anchor and no stored row is touched; for a macro
    # series it covers the fetched window, and a re-read first release is the
    # same value it replaces.
    merged = tail.combine_first(stored).sort_index()
    deriv = derivatives.compute_derivatives(merged, ticker, lookback)
    # Only what actually moved: the tail's own first date, not the whole fetch
    # window. A chained tail starts after the anchor, so the 35 years before it
    # are never rewritten — the derivatives above needed the history, the write
    # does not.
    rows = derivatives.market_data_rows(
        ticker,
        str(ticker_row["asset_class"]),
        str(ticker_row["currency"]),
        deriv,
        tail.index.min().date(),
    )
    await db.append_ts_batch("market_data", rows)
    return len(rows)


async def refresh_market_data(
    db: InvestmentDB,
    settings: Settings,
    *,
    fetch_raw: FetchRawFn = fetcher.fetch_raw_series,
    yahoo_rate_limit_seconds: float = fetcher.YAHOO_RATE_LIMIT_SECONDS,
    lookback: int,
) -> tuple[int, int, dict[str, str]]:
    """Every fetchable series, then the composites. Returns
    `(tickers_refreshed, rows_written, skipped)`.

    A PER-TICKER FAILURE IS RECORDED AND THE LOOP CONTINUES, exactly as step 9
    does: one dead FRED endpoint must not cost the week's ranking, and the two
    freshness alerts (mechanical/alerts.py) are what turn a persistent skip into
    something the owner reads on Monday. What it must NOT do is fail silently —
    hence the map, the log line and the digest alert behind them."""
    api_key = settings.fred_api_key
    refreshed = rows = 0
    skipped: dict[str, str] = {}
    yahoo_calls = 0

    for ticker_row in ALLOWED_TICKERS:
        if ticker_row["source"] not in ("yahoo", "fred"):
            continue  # composites are computed below, never fetched
        ticker = str(ticker_row["ticker"])
        if ticker_row["source"] == "yahoo":
            if yahoo_calls:
                await asyncio.sleep(yahoo_rate_limit_seconds)
            yahoo_calls += 1
        try:
            written = await refresh_series(
                db, ticker_row, api_key, fetch_raw=fetch_raw, lookback=lookback
            )
        except Exception as exc:
            logger.warning("catch-up: %s skipped — %s", ticker, exc)
            skipped[ticker] = f"{type(exc).__name__}: {exc}"
            continue
        refreshed += 1
        rows += written

    return refreshed, rows, skipped


async def refresh_composites(db: InvestmentDB, lookback: int) -> tuple[list[str], int]:
    """GROWTH_COMPOSITE and GLOBAL_LIQUIDITY from their refreshed components.

    Recomputed over the WHOLE stored history, not over a window, because both
    are z-scores against a trailing window — 10 years for growth, 5 for
    liquidity. A composite computed on a short window would be scored against a
    different distribution than the one the seed used and would drift from it on
    the same date, which is precisely the class of defect this project keeps
    finding. Reading the components back from SQLite costs milliseconds."""
    written: list[str] = []
    rows_written = 0

    inputs = {t: await stored_level(db, t) for t in GROWTH_INPUTS}
    if all(not s.empty for s in inputs.values()):
        composite = growth.compute_growth_composite(inputs["INDPRO"], inputs["UNRATE"])
        deriv = derivatives.compute_derivatives(composite, "GROWTH_COMPOSITE", lookback)
        rows = derivatives.market_data_rows("GROWTH_COMPOSITE", "MACRO", "USD", deriv, None)
        await db.append_ts_batch("market_data", rows)
        written.append("GROWTH_COMPOSITE")
        rows_written += len(rows)
    else:
        logger.warning("catch-up: GROWTH_COMPOSITE skipped, missing INDPRO/UNRATE")

    components = {t: await stored_level(db, t) for t in (*LIQUIDITY_COMPONENTS, *LIQUIDITY_FX)}
    if all(not s.empty for s in components.values()):
        eurusd, usdjpy = components["DEXUSEU"], components["DEXJPUS"]
        usd = {
            t: liquidity.usd_convert(t, components[t], eurusd, usdjpy) for t in LIQUIDITY_COMPONENTS
        }
        composite = liquidity.compute_global_liquidity(usd)
        deriv = derivatives.compute_derivatives(composite, "GLOBAL_LIQUIDITY", lookback)
        rows = derivatives.market_data_rows(
            "GLOBAL_LIQUIDITY", "GLOBAL_LIQUIDITY", "USD", deriv, None
        )
        await db.append_ts_batch("market_data", rows)
        written.append("GLOBAL_LIQUIDITY")
        rows_written += len(rows)
    else:
        logger.warning("catch-up: GLOBAL_LIQUIDITY skipped, missing components")

    return written, rows_written


async def refresh_nav(db: InvestmentDB, window: int) -> int:
    """Rebuild the STATIC portfolios' NAV on the refreshed prices. Returns how
    many series were written.

    The benchmark FIRST, because every other portfolio's `vs_benchmark` reads it
    back (ratios.backfill_nav) — the same ordering constraint `seed
    ._seed_portfolio_nav` states, and the reason this is a loop here rather than
    a comprehension.

    THE MARKET-SIGNAL STACK IS NOT HERE. Its NAV comes from a change-point walk
    rather than from constant weights, and `market_signal_cycle` already rebuilds
    it on EVERY run — at 08:55, after this job and on the ~3 weekly runs a month
    that decide nothing (the NAV is weekly, only the decision is monthly).
    Rebuilding it here as well would be a second producer of one series."""
    cost = ratios.TRADING_COST_BPS  # ADR-010: every NAV pays the same rate
    await ratios.backfill_nav(db, ratios.ALL_WEATHER_ID, ALL_WEATHER_BENCHMARK, window, cost)
    written = 1
    for portfolio in PORTFOLIOS:
        portfolio_id = str(portfolio["id"])
        if portfolio_id in TIME_VARYING_PORTFOLIOS:
            continue
        allocation = cast("dict[str, float]", portfolio["allocation"])
        await ratios.backfill_nav(db, portfolio_id, allocation, window, cost)
        written += 1
    return written


async def run_catchup(
    db: InvestmentDB,
    settings: Settings,
    *,
    fetch_raw: FetchRawFn = fetcher.fetch_raw_series,
    yahoo_rate_limit_seconds: float = fetcher.YAHOO_RATE_LIMIT_SECONDS,
) -> CatchupReport:
    """The whole 08:00 slot. Idempotent: every write is an UPSERT keyed on
    (series, ts), so running it twice in a morning changes nothing the second
    time — which is what lets the cron and the heartbeat both reach it."""
    # THE THRESHOLDS COME FROM THE DATABASE, not from `seed_data.SYSTEM_THRESHOLDS`.
    # The seed writes those constants once; Task 9.2 calibrates the table
    # afterwards, and a live job reading the constant would compute on a value
    # the system no longer uses. Same source as `regime.detect` and
    # `as_of_cycle`, which is the convention for everything on the live path.
    thresholds = {
        str(r["key"]): float(r["value"])
        for r in await db.query("SELECT key, value FROM system_thresholds")
    }
    lookback = int(thresholds["derivative_lookback_short"])

    refreshed, rows, skipped = await refresh_market_data(
        db,
        settings,
        fetch_raw=fetch_raw,
        yahoo_rate_limit_seconds=yahoo_rate_limit_seconds,
        lookback=lookback,
    )
    composites, composite_rows = await refresh_composites(db, lookback)
    # AFTER the market data and BEFORE the NAV. `regime.detect` is ONE code path
    # shared by UC0's 35-year materialization, the Phase 9 replay, this job and
    # the UC9 prelude (seed `_materialize_regimes`), and it steps per new print:
    # with no new print it commits nothing, which is the normal weekly outcome.
    episodes = await regime.detect(db)
    navs = await refresh_nav(db, int(thresholds["rolling_window_days"]))

    report = CatchupReport(
        tickers_refreshed=refreshed,
        market_rows=rows + composite_rows,
        composites=composites,
        regime_episodes=len(episodes),
        navs_rebuilt=navs,
        skipped=skipped,
    )
    logger.info("catch-up: %s", report)
    return report
