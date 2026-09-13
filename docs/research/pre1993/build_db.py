"""Copy the measurement database and splice the pre-live history into the copy.

Usage: build_db.py WORK_DIR — reads WORK_DIR/measure.db and WORK_DIR/pre1993/proxies.pkl,
writes WORK_DIR/pre1993/pre1993.db. The live database is never opened.
"""

import pickle
import sqlite3
import sys
from pathlib import Path

import pandas as pd

WORK = Path(sys.argv[1])
P = WORK / "pre1993"
dst_path = P / "pre1993.db"
dst_path.unlink(missing_ok=True)
src = sqlite3.connect(WORK / "measure.db")
dst = sqlite3.connect(dst_path)
src.backup(dst)
src.close()
with open(P / "proxies.pkl", "rb") as fh:
    d = pickle.load(fh)


def fred(name):
    df = pd.read_csv(P / f"{name}.csv", na_values=[".", ""])
    values = df.iloc[:, 1].to_numpy(dtype=float)
    return pd.Series(values, index=pd.to_datetime(df.iloc[:, 0])).dropna()


def live(ticker):
    df = pd.read_sql(
        "SELECT ts, level FROM market_data WHERE ticker=? AND level IS NOT NULL ORDER BY ts",
        dst,
        params=(ticker,),
    )
    return pd.Series(df["level"].to_numpy(), index=pd.to_datetime(df["ts"]))


def meta(ticker):
    query = "SELECT asset_class, currency FROM market_data WHERE ticker=? LIMIT 1"
    return dst.execute(query, (ticker,)).fetchone()


def bond_returns(yield_pct, duration):
    y = yield_pct / 100.0
    dt = y.index.to_series().diff().dt.days / 365.25
    dy = y.diff()
    convexity = duration**2 + duration
    return (y.shift(1) * dt - duration * dy + 0.5 * convexity * dy**2).dropna()


def next_month_first(s):
    return pd.Series(s.to_numpy(), index=s.index + pd.offsets.MonthBegin(1))


def published(series):
    """FRED daily values dated observation + 1 day, the live `availability_lag_days`."""
    return series.set_axis(series.index + pd.Timedelta(days=1))


def rows_for(ticker, series):
    asset_class, currency = meta(ticker)
    return [
        (ticker, asset_class, currency, ts.strftime("%Y-%m-%d"), float(v))
        for ts, v in series.items()
    ]


# VCIT is priced on every trading day: before the daily Aaa/Baa series the monthly value is
# forward-filled across trading days, over the same span as the monthly credit spread.
trading_days = d["ret"]["SPY"].index  # Fama-French (NYSE) trading days
daily_ig = ((fred("DAAA") + fred("DBAA")) / 2).dropna()
monthly_ig = next_month_first((fred("AAA") + fred("BAA")) / 2)
monthly_ig = monthly_ig.loc[monthly_ig.index >= d["spread_monthly"].index[0]]
early_days = trading_days[
    (trading_days >= monthly_ig.index[0]) & (trading_days < daily_ig.index[0])
]
ig_early = monthly_ig.reindex(monthly_ig.index.union(early_days)).ffill().loc[early_days]
returns = {
    "SPY": d["ret"]["SPY"],
    "IWN": d["ret"]["IWN"],
    "IEF": d["ret"]["IEF"],
    "VCIT": bond_returns(pd.concat([ig_early, daily_ig]).sort_index(), 6.3),
}

rows, report = [], []
for ticker, r in returns.items():
    lv = live(ticker)
    first = lv.index[0]
    # Chained so the proxy's level on the first live day equals the live level there.
    r = (r.loc[:first] - d["haircut"][ticker]).dropna()
    if first not in r.index:
        raise SystemExit(f"{ticker}: the proxy has no {first.date()} to chain on")
    growth = (1 + r).cumprod()
    level = growth / growth.loc[first] * lv.iloc[0]
    level = level.loc[level.index < first]
    rows += rows_for(ticker, level)
    report.append(
        f"{ticker}: {len(level)} rows {level.index[0].date()}..{level.index[-1].date()}, "
        f"last proxy {level.iloc[-1]:.3f} -> live {lv.iloc[0]:.3f} on {first.date()}"
    )

spread_daily = published(d["spread_daily"])
monthly = d["spread_monthly"]
signals = {
    "BAA10Y": pd.concat([monthly.loc[monthly.index < spread_daily.index[0]], spread_daily]),
    "T10Y2Y": published(d["T10Y2Y"]),
    "DGS10": published(d["DGS10"]),
}
for ticker, series in signals.items():
    first = live(ticker).index[0]
    before = series.loc[series.index < first].dropna()
    rows += rows_for(ticker, before)
    report.append(
        f"{ticker}: {len(before)} rows {before.index[0].date()}..{before.index[-1].date()} "
        f"-> live {first.date()}"
    )

with dst:
    dst.executemany(
        "INSERT INTO market_data (ticker, asset_class, currency, ts, level) VALUES (?,?,?,?,?)",
        rows,
    )
print("\n".join(report))
print(f"inserted {len(rows)} rows into {dst_path}")
dst.close()
