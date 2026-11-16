"""The three questions of docs/INVARIANTS_1968.md, on WORK_DIR/extended.db. Read-only.

Usage: analysis.py WORK_DIR
"""

import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from scipy.stats import binomtest, spearmanr

CUTOFF = "1991-11-01"
MIN_MOMENTS = 10
CANDIDATES = [
    "01M326ZB100SYNVEKGD1433EWT",
    "01KY2N410K3N39TB6T6C1721MW",
    "01M57TH7559BKNZWSAF54HDP3M",
    "01M3275YRF15WP4BNKJYBWJ7GF",
    "inv-low-real-yields-favor-gold",
    "01M324DYM7BVVGGBV7THEZNR79",
    "inv-01M6X4HMTNFF00FRB1FJ6NMNR1",
    "01M57T843TMWGJ5VWN780XYVME",
    "01M325DETPS3AMVQMD9WGV75EP",
    "inv-gold-ratio-trend-tilt",
]
INTEGRATED = CANDIDATES[0]


def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


work = Path(sys.argv[1])
ext = ro(work / "extended.db")
live = ro(Path(os.path.expanduser("~/data/investment/investment.db")))

conf = pd.read_sql(
    "SELECT invariant_id, signal_date, verdict FROM invariant_confrontations "
    "WHERE source = 'backtest' AND verdict IN ('confirmed', 'refuted')",
    ext,
)
conf["pre"] = conf["signal_date"] < CUTOFF
conf["ok"] = conf["verdict"] == "confirmed"
by = conf.groupby(["invariant_id", "pre"])["ok"].agg(["sum", "count"]).unstack(fill_value=0)
inv = pd.read_sql(
    "SELECT id, status, null_confirmation_rate AS null_rate, title FROM invariant", ext
).set_index("id")
live_inv = pd.read_sql("SELECT id, status FROM invariant", live).set_index("id")


def record(iid: str, pre: bool) -> tuple[int, int]:
    if iid not in by.index:
        return 0, 0
    return int(by.loc[iid, ("sum", pre)]), int(by.loc[iid, ("count", pre)])


print("GUARD — the integrated claim from 1991-11-01 on, against production's 46/69")
k, n = record(INTEGRATED, False)
print(f"  copy: {k}/{n}  -> {'PASS' if abs(n - 69) <= 3 and abs(k - 46) <= 3 else 'STOP'}")

print("\nQ1 — the candidates before 1991-11-01 (one-sided binomial against each null)")
pooled_k = pooled_n = 0
pooled_null = 0.0
for iid in CANDIDATES:
    k, n = record(iid, True)
    k2, n2 = record(iid, False)
    null = inv.loc[iid, "null_rate"] if iid in inv.index else None
    title = str(inv.loc[iid, "title"])[:70] if iid in inv.index else "?"
    if n < MIN_MOMENTS or null is None or pd.isna(null):
        print(f"  OUT  {iid:32s} pre {k}/{n}  post {k2}/{n2}  {title}")
        continue
    p = binomtest(k, n, float(null), alternative="greater").pvalue
    pooled_k, pooled_n, pooled_null = pooled_k + k, pooled_n + n, pooled_null + float(null) * n
    print(
        f"  IN   {iid:32s} pre {k}/{n} = {k / n:.2f} (null {null:.2f}, p {p:.3f})  "
        f"post {k2}/{n2}  {title}"
    )
if pooled_n:
    null_w = pooled_null / pooled_n
    p = binomtest(pooled_k, pooled_n, null_w, alternative="greater").pvalue
    verdict = "CONFIRMED out of sample" if p < 0.05 else "NOT CONFIRMED"
    print(
        f"  POOLED {pooled_k}/{pooled_n} = {pooled_k / pooled_n:.3f} against null "
        f"{null_w:.3f}: p {p:.4f} -> {verdict}"
    )

print("\nQ2 — rate minus null before vs after 1991-11-01, invariants with 10+ moments on both")
rows = []
for iid in by.index:
    k1, n1 = record(iid, True)
    k2, n2 = record(iid, False)
    null = inv.loc[iid, "null_rate"] if iid in inv.index else None
    if n1 >= MIN_MOMENTS and n2 >= MIN_MOMENTS and null is not None and not pd.isna(null):
        rows.append((iid, k1 / n1 - null, k2 / n2 - null, n1, n2))
pairs = pd.DataFrame(rows, columns=["id", "pre", "post", "n_pre", "n_post"])
if len(pairs) >= 3:
    rho, p_two = spearmanr(pairs["pre"], pairs["post"])
    p_one = p_two / 2 if rho > 0 else 1 - p_two / 2
    verdict = "PERSISTS" if rho > 0 and p_one < 0.05 else "DOES NOT PERSIST"
    print(
        f"  {len(pairs)} invariants: Spearman rho {rho:+.3f}, one-sided p {p_one:.4f} -> {verdict}"
    )
    print(
        f"  mean rate minus null: before {pairs['pre'].mean():+.3f}, "
        f"after {pairs['post'].mean():+.3f}"
    )

print("\nQ3 — verdicts over 1968-2026 against production")
measured = inv[inv["status"].isin(["proposed", "integrated", "rejected"])]
print("  copy:      ", measured["status"].value_counts().to_dict())
live_measured = live_inv[live_inv["status"].isin(["proposed", "integrated", "rejected"])]
print("  production:", live_measured["status"].value_counts().to_dict())
joined = measured.join(live_inv, rsuffix="_live", how="inner")
changed = joined[joined["status"] != joined["status_live"]]
for iid, row in changed.iterrows():
    k1, n1 = record(iid, True)
    k2, n2 = record(iid, False)
    print(
        f"  {row['status_live']:>10s} -> {row['status']:<10s} pre {k1}/{n1} post {k2}/{n2} "
        f"null {row['null_rate']:.2f}  {str(row['title'])[:80]}"
    )
