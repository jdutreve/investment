"""Step-9 orchestration integration test (docs/USE_CASES.md UC0 step 9;
docs/MILESTONES.md M2). `tests/test_market.py` covers the pure functions;
this exercises `seed._seed_market_data` end to end against a real throwaway
SQLite (CLAUDE.md: real DB, no mocks) with a synthetic fetch stub — the glue
the unit tests never touch: HISTORY_PROXIES splice dispatch (standard vs
resampled), the FRED/ETF persist-window asymmetry, composite gating, the
tradable-floor / skipped inventory.

The stub returns correlated ETF/proxy FAMILIES (shared return path + tiny
idiosyncratic noise → correlation well above the splice gate) with each ETF
starting AFTER its proxy, so a real splice extends history; long monthly macro
history so the composites' trailing windows actually warm up; and FRED-shaped
FX (DEXUSEU/DEXJPUS) for the liquidity USD-conversion.
"""

import zlib
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from investment import seed
from investment.config import Settings
from investment.db.seed_data import ALLOWED_TICKERS
from investment.db.sqlite import InvestmentDB
from investment.market import splice

_FULL_END = pd.Timestamp("2026-06-30")
_BDAYS = pd.bdate_range("1980-01-01", _FULL_END)

# family -> (proxy, proxy_start, {etf: inception}) — mirrors seed_data.py
# HISTORY_PROXIES; each ETF starts well after its proxy so the splice has real
# pre-join history to prepend.
_FAMILIES: dict[str, tuple[str, str, dict[str, str]]] = {
    "equity": ("VFINX", "1980-01-02", {"SPY": "1993-01-29", "VTI": "2001-05-24"}),
    "longbond": ("VUSTX", "1986-05-19", {"TLT": "2002-07-30"}),
    "intbond": ("VFITX", "1991-10-28", {"IEF": "2002-07-30"}),
    "shortbond": ("VFISX", "1991-10-28", {"SHY": "2002-07-30"}),
    "tips": ("VIPSX", "2000-06-29", {"TIP": "2003-12-05"}),
    "gold": ("LBMA_GOLD_AM", "1980-01-02", {"GLD": "2004-11-18"}),
    "commodity": ("^BCOM", "1991-01-02", {"DBC": "2006-02-03", "DJP": "2006-10-23"}),
    "intl": ("FDIVX", "1991-12-27", {"EFA": "2001-08-14"}),
}


def _bdays_from(start: str) -> pd.DatetimeIndex:
    return _BDAYS[pd.Timestamp(start) <= _BDAYS]


def _gbm(
    base: float, drift: float, sigma: float, idx: pd.DatetimeIndex, rng: np.random.Generator
) -> pd.Series:
    """A geometric random walk on `idx` — a stand-in level series."""
    return pd.Series(base * np.cumprod(1 + rng.normal(drift, sigma, len(idx))), idx)


def _build_series() -> dict[str, pd.Series]:
    series: dict[str, pd.Series] = {}

    for fi, (proxy, proxy_start, etfs) in enumerate(_FAMILIES.values()):
        idx = _bdays_from(proxy_start)
        rng = np.random.default_rng(100 + fi)
        shared = rng.normal(0.0003, 0.01, len(idx))  # the common driver
        series[proxy] = pd.Series(
            100.0 * np.cumprod(1.0 + shared + rng.normal(0.0, 4e-4, len(idx))), index=idx
        )
        for etf, inception in etfs.items():
            etf_full = pd.Series(
                100.0 * np.cumprod(1.0 + shared + rng.normal(0.0, 4e-4, len(idx))), index=idx
            )
            series[etf] = etf_full[etf_full.index >= pd.Timestamp(inception)]

    # FRED macro — long monthly history so the 10y GROWTH_COMPOSITE window and
    # the 5y GLOBAL_LIQUIDITY window are fully warm by the 35y backfill floor.
    months = pd.date_range("1960-01-01", _FULL_END, freq="MS")
    rng = np.random.default_rng(1)
    series["INDPRO"] = _gbm(40.0, 0.0015, 0.006, months, rng)
    series["CPIAUCSL"] = _gbm(30.0, 0.0025, 0.004, months, rng)
    series["UNRATE"] = pd.Series((5.0 + rng.normal(0.0, 0.8, len(months))).clip(min=0.5), months)
    series["M2SL"] = _gbm(300.0, 0.004, 0.004, months, rng)

    # WALCL (weekly, from 2002) is the LATEST-starting component → it, not the
    # FRED FX, sets the GLOBAL_LIQUIDITY floor (the point of the DEXUSEU/DEXJPUS
    # switch: Yahoo FX would only start ~2003 and gate the composite later).
    walcl = pd.date_range("2002-12-18", _FULL_END, freq="W-WED")
    ecb = pd.date_range("1999-01-01", _FULL_END, freq="W-FRI")
    boj = pd.date_range("1998-01-01", _FULL_END, freq="MS")
    series["WALCL"] = _gbm(8e5, 0.001, 0.006, walcl, rng)
    series["ECBASSETSW"] = _gbm(7e5, 0.001, 0.006, ecb, rng)
    series["JPNASSETS"] = _gbm(9e5, 0.002, 0.006, boj, rng)

    series["DEXUSEU"] = _gbm(1.1, 0.0, 0.004, _bdays_from("1999-01-04"), rng)
    series["DEXJPUS"] = _gbm(110.0, 0.0, 0.004, _bdays_from("1980-01-02"), rng)

    # Proxy-less tickers — fetched straight, no splice, no composite role.
    for si, tk in enumerate(["QQQ", "EEM", "^IRX", "^VIX", "CHFUSD=X", "T10Y2Y"]):
        idx = _bdays_from("1999-03-10" if tk == "QQQ" else "1980-01-02")
        series[tk] = _gbm(100.0, 0.0002, 0.01, idx, np.random.default_rng(50 + si))

    return series


_SERIES = _build_series()


def _make_stub(fail: frozenset[str] = frozenset()) -> seed.FetchRawFn:
    async def _stub(ticker_row: Mapping[str, Any], api_key: str, start: date | None) -> pd.Series:
        ticker = str(ticker_row["ticker"])
        if ticker in fail:
            raise RuntimeError(f"synthetic fetch failure for {ticker}")
        if ticker in _SERIES:
            return _SERIES[ticker].copy()
        # Generic fallback keeps the test from crashing if a minor ticker is
        # added to ALLOWED_TICKERS later (assertions target named tickers).
        # crc32, not hash(): the latter is process-randomised (PYTHONHASHSEED).
        idx = _bdays_from("1980-01-02")
        rng = np.random.default_rng(zlib.crc32(ticker.encode()))
        return pd.Series(100.0 * np.cumprod(1 + rng.normal(0.0002, 0.01, len(idx))), idx)

    return _stub


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        anthropic_api_key="test",
        openrouter_api_key="test",
        fred_api_key="test",
        telegram_bot_token="test",
        telegram_chat_id="test",
        db_path=tmp_path / "seed.db",
        inbox_path=tmp_path / "inbox",
        sources_path=tmp_path / "sources",
    )  # type: ignore[call-arg]


def _fetchable() -> list[dict[str, object]]:
    return [t for t in ALLOWED_TICKERS if t["source"] in ("yahoo", "fred")]


async def _min_ts(db: InvestmentDB, ticker: str) -> str | None:
    sql = "SELECT MIN(ts) AS m FROM market_data WHERE ticker = :t"
    return (await db.query(sql, t=ticker))[0]["m"]


async def _count_nonnull_level(db: InvestmentDB, ticker: str) -> int:
    sql = "SELECT COUNT(level) AS n FROM market_data WHERE ticker = :t"
    return (await db.query(sql, t=ticker))[0]["n"]


async def test_step9_splices_composites_and_truncates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        inv = await seed._seed_market_data(
            db, settings, fetch_raw=_make_stub(), yahoo_rate_limit_seconds=0.0
        )

        assert inv["tickers_ok"] == len(_fetchable())
        assert inv["tickers_skipped"] == {}
        assert inv["market_data_rows"] > 0

        # 1. splice extended SPY back to its proxy (VFINX, 1980), far before
        #    SPY's own 1993 ETF inception.
        assert inv["tradable_floor"]["SPY"][:4] <= "1981"
        spy_min = await _min_ts(db, "SPY")
        assert spy_min is not None and spy_min < "1993"

        # 2. splice report present and clears the artifact gate; GLD/SHY take
        #    the resampled (monthly-validated) path.
        spy_rep = next(r for r in inv["splice_reports"] if r["ticker"] == "SPY")
        assert spy_rep["proxy"] == "VFINX"
        assert spy_rep["return_corr"] >= splice.MIN_RETURN_CORR
        gld_rep = next(r for r in inv["splice_reports"] if r["ticker"] == "GLD")
        assert gld_rep["periods_per_year"] == 12

        # 3. FRED macro truncated to the 35y backfill window; ETFs are NOT
        #    (they carry their full spliced history — checked in #1).
        backfill = timedelta(days=365 * settings.market_backfill_years)
        target_start = (date.today() - backfill).isoformat()
        cpi_min = await _min_ts(db, "CPIAUCSL")
        assert cpi_min is not None and cpi_min >= target_start

        # 4. GROWTH_COMPOSITE materialized with real (non-null) values.
        assert await _count_nonnull_level(db, "GROWTH_COMPOSITE") > 0

        # 5. GLOBAL_LIQUIDITY gated on ALL components (skipna=False): its first
        #    non-null value is bounded by WALCL (2002), not the FRED FX.
        gl = (
            await db.query(
                "SELECT MIN(ts) AS m, COUNT(level) AS n FROM market_data "
                "WHERE ticker='GLOBAL_LIQUIDITY' AND level IS NOT NULL"
            )
        )[0]
        assert gl["n"] > 0
        assert gl["m"] >= "2002"

        # 6. derivatives: the earliest row of a series has NULL speed (the first
        #    difference needs a prior observation).
        speed_sql = "SELECT speed FROM market_data WHERE ticker='SPY' ORDER BY ts LIMIT 1"
        assert (await db.query(speed_sql))[0]["speed"] is None
    finally:
        await db.close()


async def test_step9_failed_fetch_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A per-ticker fetch failure is recorded in `skipped` and the rest of the
    backfill still runs (no scheduler/Telegram to escalate to yet — M9)."""
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        inv = await seed._seed_market_data(
            db, settings, fetch_raw=_make_stub(frozenset({"QQQ"})), yahoo_rate_limit_seconds=0.0
        )
        assert "QQQ" in inv["tickers_skipped"]
        assert inv["tickers_ok"] == len(_fetchable()) - 1
        rows = await db.query("SELECT COUNT(*) AS n FROM market_data WHERE ticker='SPY'")
        assert rows[0]["n"] > 0
    finally:
        await db.close()
