"""Positive control of the invariant engine (docs/INSTRUMENT_POSITIVE_CONTROL.md). Run once.

Usage: control.py WORK_DIR — copies the live database to WORK_DIR/control.db, plants
oracle signals of known accuracy, matures them with production's own engine, and
reports the three criteria. The live database is only read.
"""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from investment.db.seed_data import SIGNAL_ALIASES
from investment.db.sqlite import InvestmentDB
from investment.mechanical import invariants as engine

HORIZON = pd.Timedelta(weeks=12)
ACCURACIES = (0.50, 0.55, 0.60, 0.70)
FREQUENCIES = {"common": 1.0, "rare": 0.2}
SEEDS = 20
EFFECT = {
    "handle": "asset-class:equities",
    "metric": "return",
    "method": "cross_class",
    "direction": "outperform",
}


def copy_live(target: Path) -> None:
    target.unlink(missing_ok=True)
    src = sqlite3.connect(
        "file:" + os.path.expanduser("~/data/investment/investment.db") + "?mode=ro", uri=True
    )
    dst = sqlite3.connect(target)
    src.backup(dst)
    dst.close()
    src.close()


def forward_excess(con: sqlite3.Connection) -> pd.Series:
    """E(t): equities' 12-week return minus the other classes' median, over the
    12 weeks FOLLOWING t — this script's own reading of `benchmark_valuation`,
    whose rows carry the TRAILING 12-week return dated at its end."""
    rows = pd.read_sql(
        "SELECT benchmark_id, date, return FROM benchmark_valuation "
        "WHERE benchmark_kind = 'asset_class' AND return IS NOT NULL",
        con,
    )
    rows["date"] = pd.to_datetime(rows["date"])
    series = {
        b: g.set_index("date")["return"].sort_index() for b, g in rows.groupby("benchmark_id")
    }
    equities = series.pop("equities")
    out = {}
    for t in equities.index:
        target = t + HORIZON
        if target > equities.index.max():
            break
        own = equities.loc[:target].iloc[-1]
        others = [s.loc[:target].iloc[-1] for s in series.values() if s.index.min() <= target]
        if others:
            out[t] = own - float(np.median(others))
    return pd.Series(out)


def planted(excess: pd.Series, accuracy: float, frequency: float, seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    above = excess > excess.median()
    block = np.arange(len(excess)) // 12
    block_on = rng.random(block.max() + 1) < frequency
    draw = rng.random(len(excess))
    on = np.where(above, draw < accuracy, draw < 1 - accuracy) & block_on[block]
    return pd.Series(on.astype(float), index=excess.index)


async def main(work: Path) -> None:
    target = work / "control.db"
    copy_live(target)
    con = sqlite3.connect(target)
    excess = forward_excess(con)
    above = excess > excess.median()
    cells = []
    for accuracy in ACCURACIES:
        for name, frequency in FREQUENCIES.items():
            for seed in range(SEEDS):
                tag = f"p{int(accuracy * 100)}_{name}_{seed}"
                signal = planted(excess, accuracy, frequency, 1000 * seed + int(accuracy * 100))
                realised = float(above[signal == 1].mean())
                ticker, alias = f"PLANTED_{tag}", f"planted_{tag}"
                con.executemany(
                    "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
                    "VALUES (?, 'MACRO', 'USD', ?, ?)",
                    [(ticker, t.strftime("%Y-%m-%d"), v) for t, v in signal.items()],
                )
                con.execute(
                    "INSERT INTO invariant (id, title, description, source, status, condition, "
                    "effect, weight_initial, floor_weight, trace, created_at, updated_at) VALUES "
                    "(?, ?, 'positive control', 'research', 'proposed', ?, ?, 0.5, 0.05, "
                    "'planted oracle', '2026-11-16', '2026-11-16')",
                    (
                        f"planted-{tag}",
                        f"planted {tag}",
                        json.dumps(
                            [{"signal": alias, "feature": "level", "op": ">", "value": 0.5}]
                        ),
                        json.dumps(EFFECT),
                    ),
                )
                SIGNAL_ALIASES[alias] = ticker
                cells.append((accuracy, name, seed, f"planted-{tag}", realised))
    con.commit()
    con.close()

    db = InvestmentDB(target)
    rows = await db.query("SELECT * FROM invariant WHERE id LIKE 'planted-%' ORDER BY id")
    inputs = await engine._load_measurement_inputs(db, rows)
    thresholds = {
        str(r["key"]): float(r["value"])
        for r in await db.query("SELECT key, value FROM system_thresholds")
    }
    for row in rows:
        await engine._mature_one(db, row, inputs, True)
        await engine.restate_invariant(db, str(row["id"]), thresholds, date(2026, 11, 16))
    result = {
        str(r["id"]): r
        for r in await db.query(
            "SELECT id, status, confirmation_count AS c, infirmation_count AS r "
            "FROM invariant WHERE id LIKE 'planted-%'"
        )
    }
    await db.close()

    table = pd.DataFrame(
        [
            {
                "p": accuracy,
                "frequency": name,
                "realised": realised,
                "decided": result[iid]["c"] + result[iid]["r"],
                "rate": result[iid]["c"] / max(result[iid]["c"] + result[iid]["r"], 1),
                "integrated": result[iid]["status"] == "integrated",
                "rejected": result[iid]["status"] == "rejected",
            }
            for accuracy, name, _seed, iid, realised in cells
        ]
    )
    summary = table.groupby(["p", "frequency"]).agg(
        planted_realised=("realised", "mean"),
        measured_rate=("rate", "mean"),
        decided_moments=("decided", "mean"),
        integrated=("integrated", "sum"),
        rejected=("rejected", "sum"),
    )
    print(summary.round(3).to_string())
    c1 = all(abs(rate - p) <= 0.05 for (p, _f), rate in summary["measured_rate"].items())
    c2 = int(table[table["p"] == 0.50]["integrated"].sum()) <= 2
    c3 = (
        int(table[(table["p"] == 0.70) & (table["frequency"] == "common")]["integrated"].sum())
        >= 16
    )
    print(f"\nC1 rate recovery within 0.05 in every cell: {'PASS' if c1 else 'FAIL'}")
    print(f"C2 at most 2 of 40 uninformative integrated: {'PASS' if c2 else 'FAIL'}")
    print(f"C3 at least 16 of 20 strong common integrated: {'PASS' if c3 else 'FAIL'}")
    print(f"INSTRUMENT {'PASSES' if c1 and c2 and c3 else 'FAILS'}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
