"""Step-12/13 orchestration integration test (docs/MILESTONES.md M4;
docs/TASKS.md Task 1ter.7 items 4-5). `tests/test_ratios.py` and
`tests/test_snapshots.py` cover the pure functions; this exercises
`seed._seed_portfolio_nav`/`seed._seed_snapshot` end to end against a real
throwaway SQLite (CLAUDE.md: real DB, no mocks) with directly-inserted
synthetic market_data — step 9 (the live fetch/splice pipeline) is out of
scope here, already covered by tests/test_seed_market.py.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from investment import seed
from investment.config import Settings
from investment.db.seed_data import PORTFOLIOS
from investment.db.sqlite import InvestmentDB

# Every ticker referenced by PORTFOLIOS.allocation or ALL_WEATHER_BENCHMARK
# (excluding the synthetic 'cash' asset), plus the risk-free rate. IWN/VCIT are
# the market-signal books' sleeves (ADR-007).
_TICKERS = ("SPY", "TLT", "IEF", "GLD", "DJP", "EFA", "SHY", "VTI", "IWN", "VCIT", "^IRX")


def _synthetic_price_series(idx: pd.DatetimeIndex, seed_no: int) -> pd.Series:
    rng = np.random.default_rng(seed_no)
    returns = rng.normal(0.0003, 0.01, len(idx))
    return pd.Series(100.0 * np.cumprod(1.0 + returns), index=idx)


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


async def _seed_static_graph_and_market_data(db: InvestmentDB, settings: Settings) -> None:
    await seed._seed_reference_tables(db, settings)
    await seed._seed_frameworks(db)
    await seed._seed_regime_types(db)
    await seed._seed_invariants(db)
    await seed._seed_strategies(db)
    await seed._seed_scenarios(db)
    await seed._seed_portfolios(db)

    idx = pd.bdate_range("2021-01-04", periods=900)
    rows = []
    for i, ticker in enumerate(_TICKERS):
        series = pd.Series(1.5, index=idx) if ticker == "^IRX" else _synthetic_price_series(idx, i)
        for ts, level in series.items():
            rows.append(
                {
                    "ticker": ticker,
                    "asset_class": "TEST",
                    "currency": "USD",
                    "ts": ts.date().isoformat(),
                    "level": float(level),
                    "speed": None,
                    "acceleration": None,
                }
            )
    await db.append_ts_batch("market_data", rows)


async def test_seed_steps_12_13_populate_nav_and_snapshot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = InvestmentDB(settings.db_path)
    try:
        await _seed_static_graph_and_market_data(db, settings)

        nav_inventory = await seed._seed_portfolio_nav(db)
        snapshot_inventory = await seed._seed_snapshot(db)

        assert nav_inventory["all-weather-USD"]["rows_written"] > 0
        for pf in PORTFOLIOS:
            assert nav_inventory[pf["id"]]["rows_written"] > 0

        assert snapshot_inventory["portfolios_valued"] == len(PORTFOLIOS)
        assert snapshot_inventory["snapshot_rows"] == len(PORTFOLIOS)

        snap_rows = await db.query(
            "SELECT portfolio_id, defender, rank, gap_to_defender, market_context "
            "FROM portfolio_weekly_snapshot"
        )
        assert len(snap_rows) == len(PORTFOLIOS)
        assert sorted(r["rank"] for r in snap_rows) == list(range(1, len(PORTFOLIOS) + 1))

        defender_rows = [r for r in snap_rows if r["defender"]]
        assert len(defender_rows) == 1
        assert defender_rows[0]["gap_to_defender"] is None
        challenger_rows = [r for r in snap_rows if not r["defender"]]
        assert all(r["gap_to_defender"] is not None for r in challenger_rows)

        # No RegimeEvent was ever appended in this fixture (no regime.detect()
        # call) -> market_context degrades gracefully to a null regime rather
        # than raising.
        context = json.loads(snap_rows[0]["market_context"])
        assert context["regime"] is None

        events = await db.query(
            "SELECT type FROM event_log WHERE type IN ('ValuationEvent', 'RankingEvent')"
        )
        assert {e["type"] for e in events} == {"ValuationEvent", "RankingEvent"}

        vs_benchmark_rows = await db.query(
            "SELECT vs_benchmark FROM portfolio_nav WHERE portfolio_id = :pid "
            "AND vs_benchmark IS NOT NULL",
            pid=PORTFOLIOS[0]["id"],
        )
        assert vs_benchmark_rows  # the benchmark backfills first, so this must be populated
    finally:
        await db.close()
