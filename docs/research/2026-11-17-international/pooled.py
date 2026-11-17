"""Phase 1 analysis of docs/INTERNATIONAL_INVARIANTS.md: the pooled, date-clustered test.

Usage: pooled.py WORK_DIR — reads WORK_DIR/country_XX.db. Read-only. Run once.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp

COUNTRIES = ["US", "GB", "DE", "CA", "FR"]
SIGNALS = {"inflation", "irx", "real_rate", "real_yield", "equity_trend"}
HANDLES = {"asset-class:equities", "asset-class:bonds", "asset-class:cash"}
FDR = 0.10


def translatable(con: sqlite3.Connection) -> pd.DataFrame:
    rows = []
    for iid, title, cond, eff in con.execute(
        "SELECT id, title, condition, effect FROM invariant "
        "WHERE status IN ('proposed', 'integrated', 'rejected')"
    ):
        condition = json.loads(cond or "[]")
        effect = json.loads(eff) if eff else None
        if (
            effect
            and condition
            and {p["signal"] for p in condition} <= SIGNALS
            and effect["handle"] in HANDLES
            and effect["method"] == "cross_class"
        ):
            rows.append((iid, title))
    return pd.DataFrame(rows, columns=["id", "title"]).set_index("id")


def benjamini_hochberg(p: pd.Series, q: float) -> pd.Series:
    order = p.sort_values()
    n = len(order)
    passed = order.to_numpy() <= q * np.arange(1, n + 1) / n
    k = passed.nonzero()[0].max() + 1 if passed.any() else 0
    return pd.Series(np.arange(n) < k, index=order.index).reindex(p.index)


def main(work: Path) -> None:
    live = sqlite3.connect(
        "file:" + os.path.expanduser("~/data/investment/investment.db") + "?mode=ro", uri=True
    )
    claims = translatable(live)
    moments, nulls = [], []
    for cc in COUNTRIES:
        con = sqlite3.connect(f"file:{work / f'country_{cc}.db'}?mode=ro", uri=True)
        conf = pd.read_sql(
            "SELECT invariant_id, signal_date, verdict FROM invariant_confrontations "
            "WHERE source = 'backtest' AND verdict IN ('confirmed', 'refuted')",
            con,
        )
        conf = conf[conf["invariant_id"].isin(claims.index)]
        conf["country"] = cc
        moments.append(conf)
        n = pd.read_sql("SELECT id, null_confirmation_rate AS null_rate FROM invariant", con)
        n["country"] = cc
        nulls.append(n[n["id"].isin(claims.index)])
    moments = pd.concat(moments)
    moments["ok"] = (moments["verdict"] == "confirmed").astype(float)
    nulls = pd.concat(nulls).set_index(["id", "country"])["null_rate"]

    rows = []
    for iid, group in moments.groupby("invariant_id"):
        per_country = group.groupby("country")["ok"].agg(["sum", "count"])
        weights = per_country["count"]
        null = float(sum(nulls.get((iid, c), 0.5) * w for c, w in weights.items()) / weights.sum())
        by_date = group.groupby("signal_date")["ok"].mean()
        if len(by_date) < 3:
            continue
        test = ttest_1samp(by_date - null, 0.0, alternative="greater")
        us = per_country.loc["US"] if "US" in per_country.index else None
        rows.append(
            {
                "id": iid,
                "dates": len(by_date),
                "moments": int(weights.sum()),
                "rate": group["ok"].mean(),
                "null": null,
                "p": float(test.pvalue),
                "countries": " ".join(
                    f"{c} {int(r['sum'])}/{int(r['count'])}" for c, r in per_country.iterrows()
                ),
                "us_only": f"{int(us['sum'])}/{int(us['count'])}" if us is not None else "-",
                "title": str(claims.loc[iid, "title"])[:80],
            }
        )
    table = pd.DataFrame(rows).set_index("id")
    table["pass"] = benjamini_hochberg(table["p"], FDR)
    table = table.sort_values("p")
    print(f"translatable invariants measured: {len(table)} (of {len(claims)})")
    print(f"pooled p < 0.05: {(table['p'] < 0.05).sum()} (by chance: {0.05 * len(table):.1f})")
    print(f"pass at FDR {FDR:.0%}: {int(table['pass'].sum())}")
    verdict = "WORTH BUILDING" if table["pass"].sum() >= 3 else "STOPS HERE"
    print(f"DECISION (3 or more pass): {verdict}\n")
    for _iid, r in table.head(15).iterrows():
        print(
            f"{'PASS' if r['pass'] else '    '} p {r['p']:.4f} rate {r['rate']:.3f} null "
            f"{r['null']:.3f} dates {r['dates']:3d}  US alone {r['us_only']:>7s}  {r['title']}"
        )
        print(f"      {r['countries']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
