"""The dollar and the Fed as indicators (db/seed_data.py, 2026-11-13): the two
derived series on synthetic inputs, and their materialisation on a real
throwaway SQLite."""

from pathlib import Path

import pandas as pd
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical.backtests import _materialize_fed_signals, fed_path, net_liquidity


def _series(points: dict[str, float]) -> pd.Series:
    return pd.Series(list(points.values()), index=pd.DatetimeIndex(list(points)), dtype=float)


def test_the_fed_path_reads_the_policy_rate_last_known_on_the_two_year_dates() -> None:
    two_year = _series({"2026-11-06": 4.0, "2026-11-09": 3.5})
    # The fed funds rate prints on the weekend; Monday reads Sunday's print.
    funds = _series({"2026-11-06": 3.75, "2026-11-08": 3.9})
    path = fed_path(two_year, funds)
    assert path.round(4).to_dict() == {
        pd.Timestamp("2026-11-06"): 0.25,
        pd.Timestamp("2026-11-09"): -0.4,
    }


def test_net_liquidity_counts_no_reverse_repo_where_none_was_made() -> None:
    """2008's last operation must not drain the balance sheet in 2010."""
    assets = _series({"2008-12-31": 2_000_000.0, "2010-06-30": 2_300_000.0})
    treasury = _series({"2008-12-31": 100_000.0, "2010-06-30": 200_000.0})
    reverse_repo = _series({"2008-12-30": 26.0})
    liquidity = net_liquidity(assets, treasury, reverse_repo)
    assert liquidity.round(1).to_dict() == {
        pd.Timestamp("2008-12-31"): 1874.0,
        pd.Timestamp("2010-06-30"): 2100.0,
    }


def test_net_liquidity_carries_a_friday_operation_over_the_weekend() -> None:
    assets = _series({"2026-11-09": 6_700_000.0})
    treasury = _series({"2026-11-04": 900_000.0})
    reverse_repo = _series({"2026-11-06": 10.0})
    assert net_liquidity(assets, treasury, reverse_repo).iloc[0] == pytest.approx(5790.0)


def test_net_liquidity_is_not_made_without_one_of_its_inputs() -> None:
    assets = _series({"2026-11-09": 6_700_000.0})
    treasury = _series({"2026-11-04": 900_000.0})
    assert net_liquidity(assets, treasury, pd.Series(dtype=float)).empty


async def _row(db: InvestmentDB, ticker: str, ts: str, level: float) -> None:
    await db.command(
        "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
        "VALUES (:t, 'MACRO', 'USD', :ts, :lvl)",
        t=ticker,
        ts=ts,
        lvl=level,
    )


async def test_both_fed_signals_are_written_as_indicators(tmp_path: Path) -> None:
    db = InvestmentDB(tmp_path / "fed.db")
    try:
        await _row(db, "DGS2", "2026-11-06", 4.0)
        await _row(db, "DFF", "2026-11-06", 3.75)
        await _row(db, "WALCL", "2026-11-06", 6_700_000.0)
        await _row(db, "WTREGEN", "2026-11-06", 900_000.0)
        await _row(db, "RRPONTSYD", "2026-11-06", 10.0)
        written = await _materialize_fed_signals(db, 30)
        rows = await db.query(
            "SELECT ticker, level FROM market_data "
            "WHERE ticker IN ('fed_path_2y', 'net_liquidity') ORDER BY ticker"
        )
    finally:
        await db.close()
    assert written == {"fed_path_2y": 1, "net_liquidity": 1}
    assert [(r["ticker"], round(r["level"], 4)) for r in rows] == [
        ("fed_path_2y", 0.25),
        ("net_liquidity", 5790.0),
    ]
