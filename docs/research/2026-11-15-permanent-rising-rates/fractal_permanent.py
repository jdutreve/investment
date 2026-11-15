import sqlite3

import numpy as np
import pandas as pd

con = sqlite3.connect("file:/Users/jdutreve/data/investment/investment.db?mode=ro", uri=True)


def s(t):
    d = pd.read_sql(
        "SELECT ts, level FROM market_data WHERE ticker=? AND level IS NOT NULL ORDER BY ts",
        con,
        params=(t,),
    )
    return pd.Series(d.level.values, index=pd.to_datetime(d.ts))


px = pd.concat({"SPY": s("SPY"), "TLT": s("TLT"), "GLD": s("GLD")}, axis=1).dropna()
irx = s("^IRX").reindex(px.index, method="ffill")
px = px.resample("ME").last()
irx = irx.resample("ME").last()
r = px.pct_change()
r["cash"] = (irx.shift(1) / 100) / 12
r = r.dropna()
rf = r["cash"]


def mix(k):  # k replacement steps: bonds = 0.25**(k+1), others share the rest
    b = 0.25 ** (k + 1)
    o = (1 - b) / 3
    return {"SPY": o, "TLT": b, "GLD": o, "cash": o}


books = {
    "permanent 25/25/25/25": mix(0),
    "1 step (bonds 6.25%)": mix(1),
    "limit: 1/3 SPY/GLD/cash": {"SPY": 1 / 3, "TLT": 0, "GLD": 1 / 3, "cash": 1 / 3},
    "TLT alone": {"TLT": 1},
    "SPY alone": {"SPY": 1},
}


def stats(x, start=None, end=None):
    x = x[start:end]
    nav = (1 + x).cumprod()
    yrs = len(x) / 12
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    ex = x - rf.reindex(x.index)
    down = np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(12)
    dd = (nav / nav.cummax() - 1).min()
    return cagr, x.std() * np.sqrt(12), ex.mean() * 12 / down, dd, nav.iloc[-1] - 1


print(f"period {r.index[0]:%Y-%m} -> {r.index[-1]:%Y-%m}, monthly rebalance, no costs")
print(f"{'book':28s} CAGR   vol   Sortino  maxDD")
port = {}
for n, w in books.items():
    x = sum(r[k] * v for k, v in w.items())
    port[n] = x
    c, v, so, dd, _ = stats(x)
    print(f"{n:28s} {c * 100:5.2f}% {v * 100:5.1f}% {so:6.2f} {dd * 100:7.1f}%")
print("\ncumulative return in stress windows")
win = {
    "2000-02..2002-09": ("2000-03", "2002-09"),
    "2007-11..2009-02": ("2007-11", "2009-02"),
    "2020-02..2020-03": ("2020-02", "2020-03"),
    "2022": ("2022-01", "2022-12"),
    "2011-09..2015-12 (gold bust)": ("2011-09", "2015-12"),
}
for wn, (a, b) in win.items():
    print(
        wn.ljust(30),
        "  ".join(
            f"{n.split(':')[0][:12]:>12s} {stats(port[n], a, b)[4] * 100:+6.1f}%"
            for n in list(books)[:3]
        ),
    )
print(
    "\ncorrelation of monthly returns with SPY:",
    {k: round(r[k].corr(r["SPY"]), 2) for k in ["TLT", "GLD", "cash"]},
)
