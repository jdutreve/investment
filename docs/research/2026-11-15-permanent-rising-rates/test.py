"""The permanent portfolio in a decade of rising rates — see PROTOCOL.md. Run once."""

import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
D = Path(sys.argv[1])
con = sqlite3.connect("file:/Users/jdutreve/data/investment/investment.db?mode=ro", uri=True)


def live(t):
    d = pd.read_sql(
        "SELECT ts, level FROM market_data WHERE ticker=? AND level IS NOT NULL ORDER BY ts",
        con,
        params=(t,),
    )
    return pd.Series(d.level.values, index=pd.to_datetime(d.ts))


def month_end(s):
    return s.resample("ME").last()


# -- equities: ^GSPC month-end close + Shiller dividend D/12 over Shiller's monthly price
gspc = pd.read_csv(D / "GSPC.csv", index_col=0)
gspc.index = pd.to_datetime(gspc.index, utc=True).tz_localize(None)
gspc = month_end(gspc.iloc[:, 0])
sh = pd.read_excel(D / "ie_data.xls", sheet_name="Data", header=None, skiprows=8)
sh = sh[[0, 1, 2]].dropna()
sh = sh[pd.to_numeric(sh[0], errors="coerce").notna()]
year = sh[0].astype(float).astype(int)
month = ((sh[0].astype(float) - year) * 100).round().astype(int)
idx = pd.to_datetime(dict(year=year, month=month, day=1)) + pd.offsets.MonthEnd(0)
div_yield = pd.Series((sh[2].astype(float) / sh[1].astype(float)).values, index=idx)
eq = (gspc.pct_change() + div_yield.reindex(gspc.index) / 12).dropna()

# -- long bond: 20-year par Treasury priced exactly from DGS20 month-ends
y20 = pd.read_csv(D / "DGS20.csv", index_col=0, na_values=[".", ""]).iloc[:, 0].dropna()
y20.index = pd.to_datetime(y20.index)
y20 = month_end(y20).dropna() / 100.0  # the 1987-1993 gap months drop out here


def par_bond_return(y_prev, y_now, years=20.0):
    c, n = y_prev, 2 * (years - 1 / 12)
    v = (1 + y_now / 2) ** (-n)
    price = (c / y_now) * (1 - v) + v
    return price - 1 + c / 12


bond = pd.Series(
    [par_bond_return(a, b) for a, b in zip(y20.shift(1), y20, strict=True)], index=y20.index
).dropna()
# A month whose previous month-end is missing (the 1987-1993 gap) is not a return.
gap = y20.index.to_series().diff().dt.days > 40
bond = bond[~gap.reindex(bond.index).fillna(False)]

gold = month_end(live("GLD")).pct_change().dropna()
cash = (month_end(live("^IRX")).shift(1) / 100 / 12).dropna()

# -- validation on the overlaps, not rescaled
vfinx = pd.read_csv(D / "VFINX.csv", index_col=0)
vfinx.index = pd.to_datetime(vfinx.index, utc=True).tz_localize(None)
vf = month_end(vfinx.iloc[:, 0]).pct_change().dropna()
tlt = month_end(live("TLT")).pct_change().dropna()
print("VALIDATION (monthly)          overlap            corr   proxy/y  live/y")
for name, p, lv in (("equity  vs VFINX", eq, vf), ("bond20y vs TLT/VUSTX", bond, tlt)):
    i = p.index.intersection(lv.index)
    print(
        f"  {name:26s} {i[0]:%Y-%m}..{i[-1]:%Y-%m}  {p[i].corr(lv[i]):.3f}  "
        f"{p[i].mean() * 1200:6.2f}%  {lv[i].mean() * 1200:6.2f}%"
    )

r = pd.concat({"SPY": eq, "TLT": bond, "GLD": gold, "cash": cash}, axis=1, sort=True)
r = r.loc["1968-01":"1986-05"].dropna()
tlt_index = (1 + r["TLT"]).cumprod()
signal = (tlt_index < tlt_index.rolling(10).mean()).shift(1)  # known at t-1

P = {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "cash": 0.25}
N = {"SPY": 1 / 3, "TLT": 0.0, "GLD": 1 / 3, "cash": 1 / 3}
perm = sum(r[k] * w for k, w in P.items())
nob = sum(r[k] * w for k, w in N.items())
sw = pd.Series(np.where(signal.fillna(False).astype(bool), nob, perm), index=r.index)
# The switcher needs 10 months of TLT before its first signal: every book is scored from
# that month, so all three are compared on the same months.
start = signal.dropna().index[0]
books = {"permanent": perm, "no-bond (1/3)": nob, "switcher": sw}
rf = r["cash"]


def sortino(x):
    ex = x - rf.reindex(x.index)
    down = np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(12)
    return ex.mean() * 12 / down


def stats(x):
    nav = (1 + x).cumprod()
    return (
        ((nav.iloc[-1]) ** (12 / len(x)) - 1) * 100,
        sortino(x),
        (nav / nav.cummax() - 1).min() * 100,
    )


def boot(a, b, n=2000, block=12, seed=7):
    rng = np.random.default_rng(seed)
    T = len(a)
    out = []
    for _ in range(n):
        starts = rng.integers(0, T, size=T // block + 1)
        ix = np.concatenate([(s + np.arange(block)) % T for s in starts])[:T]
        out.append(
            sortino(a.iloc[ix].set_axis(a.index[ix])) - sortino(b.iloc[ix].set_axis(b.index[ix]))
        )
    return np.percentile(out, [5, 95])


s0 = f"{start:%Y-%m}"
windows = {
    f"{s0}..1981-09 rising rates": (start, "1981-09"),
    "1975-01..1981-09 rising rates": ("1975-01", "1981-09"),
    "1981-10..1986-05 falling": ("1981-10", "1986-05"),
    f"W1 {s0}..1986-05": (start, "1986-05"),
    "W2 1975-01..1986-05": ("1975-01", "1986-05"),
}
print(
    f"\nBOOKS (monthly rebalance, no costs; switcher signal on "
    f"{signal.loc[start:].mean() * 100:.0f}% of months)"
)
for wn, (a, b) in windows.items():
    print(f"  {wn}")
    for n, x in books.items():
        c, so, dd = stats(x.loc[a:b])
        print(f"      {n:14s} CAGR {c:6.2f}%  Sortino {so:5.2f}  maxDD {dd:6.1f}%")

print("\nQ2 DECISION — switcher minus permanent, Sortino, 90% block-bootstrap interval")
for wn in (f"W1 {s0}..1986-05", "W2 1975-01..1986-05"):
    a, b = windows[wn]
    x, y = sw.loc[a:b], perm.loc[a:b]
    lo, hi = boot(x, y)
    ddx, ddy = stats(x)[2], stats(y)[2]
    print(
        f"  {wn:22s} diff {sortino(x) - sortino(y):+.3f}  [{lo:+.3f}, {hi:+.3f}]  "
        f"maxDD switcher {ddx:.1f}% vs permanent {ddy:.1f}%"
    )

print("\nSTRESS WINDOWS, cumulative")
for wn, (a, b) in {
    "1973-01..1974-09 bear": ("1973-01", "1974-09"),
    "1977-01..1980-01 inflation": ("1977-01", "1980-01"),
    "1980-02..1981-09 Volcker": ("1980-02", "1981-09"),
    "1981-10..1982-12 bond rally": ("1981-10", "1982-12"),
}.items():
    print(
        f"  {wn:28s}"
        + "".join(
            f"  {n.split()[0]:>9s} {((1 + x.loc[a:b]).prod() - 1) * 100:+6.1f}%"
            for n, x in books.items()
        )
    )
print("\nASSETS, annual return by window")
for wn, (a, b) in windows.items():
    print(
        f"  {wn:32s}"
        + "".join(
            f" {k} {((1 + r[k].loc[a:b]).prod() ** (12 / len(r[k].loc[a:b])) - 1) * 100:5.1f}%"
            for k in r
        )
    )
