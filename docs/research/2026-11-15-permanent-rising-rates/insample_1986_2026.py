import sqlite3
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
con = sqlite3.connect("file:/Users/jdutreve/data/investment/investment.db?mode=ro", uri=True)


def s(t):
    d = pd.read_sql(
        "SELECT ts, level FROM market_data WHERE ticker=? AND level IS NOT NULL ORDER BY ts",
        con,
        params=(t,),
    )
    return pd.Series(d.level.values, index=pd.to_datetime(d.ts))


px = pd.concat({"SPY": s("SPY"), "TLT": s("TLT"), "GLD": s("GLD")}, axis=1, sort=True).dropna()
irx = s("^IRX").reindex(px.index, method="ffill")
m = px.resample("ME").last()
irx = irx.resample("ME").last()
r = m.pct_change()
r["cash"] = (irx.shift(1) / 100) / 12
dgs10 = s("DGS10").resample("ME").last().reindex(m.index)
P = {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "cash": 0.25}
N = {"SPY": 1 / 3, "TLT": 0, "GLD": 1 / 3, "cash": 1 / 3}


def ret(w):
    return sum(r[k] * v for k, v in w.items())


perm, nob = ret(P), ret(N)
# Signals known at the END of month t-1, applied to month t (point in time).
sig = pd.DataFrame(
    {
        "10y yield up over 12m": (dgs10 - dgs10.shift(12)) > 0,
        "TLT 12m return < 0": (m.TLT / m.TLT.shift(12) - 1) < 0,
        "TLT below 10m average": m.TLT.rolling(10).mean() > m.TLT,
    }
).shift(1)
valid = r.dropna().index
rf = r["cash"]


def stats(x):
    x = x.dropna()
    nav = (1 + x).cumprod()
    yrs = len(x) / 12
    ex = x - rf.reindex(x.index)
    down = np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(12)
    return (
        (nav.iloc[-1] ** (1 / yrs) - 1) * 100,
        ex.mean() * 12 / down,
        (nav / nav.cummax() - 1).min() * 100,
    )


print("A. Monthly return, permanent minus no-bond, by rate regime (annualised points)")
for n in sig:
    sg = sig[n].reindex(valid)
    ok = sg.notna() & dgs10.reindex(valid).notna() if "10y" in n else sg.notna()
    for flag in (True, False):
        idx = valid[ok & (sg == flag)]
        d = (perm - nob).reindex(idx)
        tstat = d.mean() / d.std() * np.sqrt(len(d))
        print(
            f"  {n:24s} {'YES' if flag else 'no ':3s} n={len(d):3d}  "
            f"perm-nobond {d.mean() * 1200:+6.2f} pts/yr  t={tstat:+.2f}"
        )
print("\nB. Switching rule: permanent, but no bonds while the signal is on (vs permanent)")
for n in sig:
    sg = sig[n].reindex(valid)
    start = sg.dropna().index[0] if "10y" not in n else dgs10.dropna().index[13]
    idx = valid[valid >= start]
    sw = pd.Series(
        np.where(sg.reindex(idx).astype(bool), nob.reindex(idx), perm.reindex(idx)), index=idx
    )
    a, b = stats(perm.reindex(idx)), stats(sw)
    on = sg.reindex(idx).mean() * 100
    print(f"  {n:24s} from {start:%Y-%m} (signal on {on:4.0f}% of months)")
    print(f"      permanent   CAGR {a[0]:5.2f}%  Sortino {a[1]:.2f}  maxDD {a[2]:6.1f}%")
    print(f"      switching   CAGR {b[0]:5.2f}%  Sortino {b[1]:.2f}  maxDD {b[2]:6.1f}%")
print("\nC. Where the gain comes from (TLT below 10m average rule), by period, cumulative points")
sg = sig["TLT below 10m average"].reindex(valid).astype(bool)
sw = pd.Series(np.where(sg, nob.reindex(valid), perm.reindex(valid)), index=valid)
for a, b in [
    ("1986", "1993"),
    ("1994", "1999"),
    ("2000", "2007"),
    ("2008", "2015"),
    ("2016", "2020"),
    ("2021", "2023"),
    ("2024", "2026"),
]:
    print(
        f"  {a}-{b}: switching {((1 + sw[a:b]).prod() - 1) * 100:+6.1f}%  "
        f"permanent {((1 + perm.reindex(valid)[a:b]).prod() - 1) * 100:+6.1f}%"
    )
for n, x in (("permanent", perm.reindex(valid)), ("switching", sw)):
    nav = (1 + x).cumprod()
    dd = nav / nav.cummax() - 1
    print(f"  {n} worst drawdown trough {dd.idxmin():%Y-%m}")
