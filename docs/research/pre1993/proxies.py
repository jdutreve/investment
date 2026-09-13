"""Build the pre-1993 proxies and calibrate each on its overlap with the live series.

Writes proxies.pkl (the raw proxy series and the calibration haircuts); nothing is
inserted anywhere. Usage: proxies.py WORK_DIR/pre1993 WORK_DIR/measure.db
"""

import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

P = Path(sys.argv[1])
con = sqlite3.connect(sys.argv[2])


def ff_section(path, header_start):
    """The first table of a Ken French CSV, the one whose header starts `header_start`."""
    lines = path.read_text().splitlines()
    i = next(k for k, line in enumerate(lines) if line.startswith(header_start))
    j = next(k for k in range(i + 1, len(lines)) if not lines[k].strip())
    rows = [line.split(",") for line in lines[i + 1 : j]]
    cols = [c.strip() for c in lines[i].split(",")[1:]]
    df = pd.DataFrame(
        [[float(x) for x in r[1:]] for r in rows],
        columns=cols,
        index=pd.to_datetime([r[0].strip() for r in rows], format="%Y%m%d"),
    )
    return df.replace([-99.99, -999.0], np.nan) / 100.0


def fred(name):
    df = pd.read_csv(P / f"{name}.csv", na_values=[".", ""])
    values = df.iloc[:, 1].to_numpy(dtype=float)
    return pd.Series(values, index=pd.to_datetime(df.iloc[:, 0])).dropna()


def live(ticker):
    df = pd.read_sql(
        "SELECT ts, level FROM market_data WHERE ticker=? AND level IS NOT NULL ORDER BY ts",
        con,
        params=(ticker,),
    )
    return pd.Series(df["level"].to_numpy(), index=pd.to_datetime(df["ts"]))


def bond_returns(yield_pct, duration):
    """Carry + duration + convexity from a yield series. The convexity is the plain-bullet
    approximation; neither parameter is fitted."""
    y = yield_pct / 100.0
    dt = y.index.to_series().diff().dt.days / 365.25
    dy = y.diff()
    convexity = duration**2 + duration
    return (y.shift(1) * dt - duration * dy + 0.5 * convexity * dy**2).dropna()


def next_month_first(s):
    """A monthly average of month m is knowable at the start of m + 1 (ADR-003)."""
    return pd.Series(s.to_numpy(), index=s.index + pd.offsets.MonthBegin(1))


ff3 = ff_section(P / "F-F_Research_Data_Factors_daily.csv", ",Mkt-RF")
ff6 = ff_section(P / "6_Portfolios_2x3_Daily.csv", ",SMALL LoBM")
proxy_ret = {
    "SPY": ff3["Mkt-RF"] + ff3["RF"],
    "IWN": ff6["SMALL HiBM"],
    "IEF": bond_returns(fred("DGS10"), 7.5),
}
daily_ig = ((fred("DAAA") + fred("DBAA")) / 2).dropna()
monthly_ig = next_month_first((fred("AAA") + fred("BAA")) / 2)
ig = pd.concat([monthly_ig[monthly_ig.index < daily_ig.index[0]], daily_ig])
proxy_ret["VCIT"] = bond_returns(ig, 6.3)

print("sleeve overlap                  corr d   corr m  proxy/y   live/y    gap/y  vol ratio")
haircut = {}
for ticker, r in proxy_ret.items():
    lv = live(ticker).pct_change().dropna()
    idx = r.index.intersection(lv.index)
    a, b = r.loc[idx], lv.loc[idx]
    ma, mb = (1 + a).resample("ME").prod() - 1, (1 + b).resample("ME").prod() - 1
    haircut[ticker] = a.mean() - b.mean()
    overlap = f"{idx[0].date()}..{idx[-1].date()}"
    yearly = [x * 252 * 100 for x in (a.mean(), b.mean(), haircut[ticker])]
    print(
        f"{ticker:<7}{overlap:<24}{a.corr(b):>7.3f}{ma.corr(mb):>9.3f}"
        + "".join(f"{x:>8.2f}%" for x in yearly)
        + f"{a.std() / b.std():>11.2f}"
    )

# The signals must reproduce the live rows on their overlap, and they do with a one-day
# shift: the live `availability_lag_days = 1` for these FRED dailies.
spread_daily = (fred("DBAA") - fred("DGS10")).dropna()
spread_monthly = next_month_first(fred("BAA") - fred("GS10")).dropna()
for ticker, series in (
    ("BAA10Y", spread_daily),
    ("T10Y2Y", fred("T10Y2Y")),
    ("DGS10", fred("DGS10")),
):
    lv = live(ticker)
    for label, shifted in (
        ("same day", series),
        ("+1 day", series.set_axis(series.index + pd.Timedelta(days=1))),
    ):
        idx = shifted.index.intersection(lv.index)
        exact = ((shifted.loc[idx] - lv.loc[idx]).abs() < 0.005).mean()
        print(f"{ticker:<7}{label:<9} share of the overlap reproduced exactly: {exact:.1%}")

raw_monthly = (fred("BAA") - fred("GS10")).dropna()
monthly_of_daily = spread_daily.resample("MS").mean()
idx = monthly_of_daily.index.intersection(raw_monthly.index)
corr = raw_monthly.loc[idx].corr(monthly_of_daily.loc[idx])
bias = (raw_monthly.loc[idx] - monthly_of_daily.loc[idx]).mean()
print(
    f"monthly BAA-GS10 vs the monthly mean of daily DBAA-DGS10: corr {corr:.3f}, bias {bias:+.3f}"
)

with open(P / "proxies.pkl", "wb") as fh:
    pickle.dump(
        {
            "ret": proxy_ret,
            "haircut": haircut,
            "spread_daily": spread_daily,
            "spread_monthly": spread_monthly,
            "T10Y2Y": fred("T10Y2Y"),
            "DGS10": fred("DGS10"),
        },
        fh,
    )
