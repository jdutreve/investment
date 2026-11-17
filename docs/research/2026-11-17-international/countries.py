"""Phase 1 of docs/INTERNATIONAL_INVARIANTS.md: one database copy per country. Run once.

Usage: countries.py WORK_DIR — reads WORK_DIR/data/*.csv (FRED OECD series), writes
WORK_DIR/country_XX.db for US, GB, DE, CA, FR. The live database is only read.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from investment import seed
from investment.db.seed_data import SIGNAL_ALIASES
from investment.db.sqlite import InvestmentDB
from investment.market import derivatives
from investment.mechanical import invariants

sys.path.insert(0, str(Path(__file__).parent.parent / "2026-11-16-positive-control"))
from control import forward_excess, planted

COUNTRIES = ["US", "GB", "DE", "CA", "FR"]
REPLACED = ["CPIAUCSL", "^IRX", "DGS10", "SPY", "TLT"]
LOOKBACK = 30


def fred(data: Path, name: str) -> pd.Series:
    df = pd.read_csv(data / f"{name}.csv", na_values=[".", ""])
    return pd.Series(
        df.iloc[:, 1].to_numpy(dtype=float), index=pd.to_datetime(df.iloc[:, 0])
    ).dropna()


def par_bond_index(yield_pct: pd.Series, maturity: float = 10.0) -> pd.Series:
    y = yield_pct / 100.0
    coupon = y.shift(1)
    discount = (1 + y / 2) ** (-2 * (maturity - 1 / 12))
    price = (coupon / y) * (1 - discount) + discount
    returns = (price - 1 + coupon / 12).dropna()
    return (1 + returns).cumprod() * 100


def published(monthly: pd.Series, day: int) -> pd.Series:
    """A month's value, dated the day it is known: `day` of the next month."""
    return pd.Series(
        monthly.to_numpy(),
        index=monthly.index + pd.offsets.MonthBegin(1) + pd.Timedelta(day - 1, "D"),
    )


def business_daily(series: pd.Series) -> pd.Series:
    days = pd.bdate_range(series.index.min(), series.index.max())
    return series.reindex(series.index.union(days)).ffill().loc[days]


def country_series(data: Path, cc: str) -> dict[str, pd.Series]:
    long = fred(data, f"IRLTLT01{cc}M156N")
    return {
        "CPIAUCSL": published(fred(data, f"CPALTT01{cc}M659N"), 15),
        "^IRX": business_daily(published(fred(data, f"IR3TIB01{cc}M156N"), 1)),
        "DGS10": business_daily(published(long, 1)),
        "SPY": business_daily(published(fred(data, f"SPASTT01{cc}M661N"), 1)),
        "TLT": business_daily(published(par_bond_index(long), 1)),
    }


def build_copy(target: Path, data: Path, cc: str) -> None:
    target.unlink(missing_ok=True)
    src = sqlite3.connect(
        "file:" + os.path.expanduser("~/data/investment/investment.db") + "?mode=ro", uri=True
    )
    con = sqlite3.connect(target)
    src.backup(con)
    src.close()
    meta = {
        t: con.execute(
            "SELECT asset_class, currency FROM market_data WHERE ticker = ? LIMIT 1", (t,)
        ).fetchone()
        for t in REPLACED
    }
    con.execute("DELETE FROM market_data")
    con.execute("DELETE FROM benchmark_valuation")
    con.execute("DELETE FROM invariant_confrontations")
    rows = []
    for ticker, level in country_series(data, cc).items():
        deriv = derivatives.compute_derivatives(level, ticker, LOOKBACK)
        asset_class, currency = meta[ticker]
        for ts, r in deriv.iterrows():
            rows.append(
                (
                    ticker,
                    asset_class,
                    currency,
                    ts.strftime("%Y-%m-%d"),
                    *(
                        None if pd.isna(v) else float(v)
                        for v in (r["level"], r["speed"], r["acceleration"])
                    ),
                )
            )
    con.executemany(
        "INSERT INTO market_data (ticker, asset_class, currency, ts, level, speed, acceleration) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


def plant_oracles(target: Path) -> None:
    """The sanity check: 20 oracles at p = 0.70, common, as in the positive control."""
    con = sqlite3.connect(target)
    excess = forward_excess(con)
    for seed_n in range(20):
        signal = planted(excess, 0.70, 1.0, 1000 * seed_n + 70)
        ticker, alias = f"PLANTED_{seed_n}", f"planted_{seed_n}"
        con.executemany(
            "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
            "VALUES (?, 'MACRO', 'USD', ?, ?)",
            [(ticker, t.strftime("%Y-%m-%d"), v) for t, v in signal.items()],
        )
        con.execute(
            "INSERT INTO invariant (id, title, description, source, status, condition, effect, "
            "weight_initial, floor_weight, trace, created_at, updated_at) VALUES "
            "(?, ?, 'oracle', 'research', 'proposed', ?, ?, 0.5, 0.05, 'planted oracle', "
            "'2026-11-17', '2026-11-17')",
            (
                f"planted-{seed_n}",
                f"planted {seed_n}",
                json.dumps([{"signal": alias, "feature": "level", "op": ">", "value": 0.5}]),
                json.dumps(
                    {
                        "handle": "asset-class:equities",
                        "metric": "return",
                        "method": "cross_class",
                        "direction": "outperform",
                    }
                ),
            ),
        )
        SIGNAL_ALIASES[alias] = ticker
    con.commit()
    con.close()


async def measure(target: Path, cc: str) -> None:
    started = time.monotonic()
    db = InvestmentDB(target)
    try:
        await seed._materialize_benchmark_valuation(db)
    finally:
        await db.close()
    if cc == "GB":
        plant_oracles(target)
    db = InvestmentDB(target)
    try:
        await invariants.mature_seed_invariants(db, remeasure_on_changed_data=True)
    finally:
        await db.close()
    print(f"{cc}: measured in {time.monotonic() - started:.0f}s", flush=True)


async def main(work: Path) -> None:
    for cc in COUNTRIES:
        target = work / f"country_{cc}.db"
        build_copy(target, work / "data", cc)
        await measure(target, cc)
    con = sqlite3.connect(work / "country_GB.db")
    oracle = con.execute(
        "SELECT SUM(confirmation_count), SUM(confirmation_count + infirmation_count) "
        "FROM invariant WHERE id LIKE 'planted-%'"
    ).fetchone()
    rate = oracle[0] / oracle[1]
    print(f"SANITY: oracle p=0.70 in GB reads {rate:.3f} -> {'PASS' if rate >= 0.65 else 'STOP'}")
    np.save(work / "sanity.npy", np.array([rate]))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
