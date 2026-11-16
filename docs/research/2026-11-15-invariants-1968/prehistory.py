"""The synthetic pre-history of `docs/INVARIANTS_1968.md`, and the fetcher that serves it.

Nothing here is fitted. Each construction is chained onto the first day of the
real series it extends, so the real history is untouched and only the days
before it are added:

- VFINX before 1980-01: S&P 500 daily close + Shiller's monthly dividend, spread
  evenly over the month's trading days.
- VUSTX / VFITX / VFISX before their inception: a par Treasury priced exactly
  every day from the constant-maturity yield (coupon y(t-1), revalued at y(t)
  one day shorter), at 20, 8.5 and 2 years from DGS20, DGS10 and DGS2.
- BAA10Y before its daily series: monthly Moody's Baa minus GS10, dated the
  first day of the next month (a monthly average is knowable then — ADR-003).

`python prehistory.py WORK_DIR` runs the protocol's guard: each synthetic
series against its real proxy over their overlap, monthly return correlation
>= 0.95, or the run stops.
"""

import asyncio
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from investment.market import fetcher

MIN_MONTHLY_CORR = 0.95
BOND_PROXIES = {"VUSTX": ("DGS20", 20.0), "VFITX": ("DGS10", 8.5), "VFISX": ("DGS2", 2.0)}


def _fred_csv(path: Path) -> pd.Series:
    df = pd.read_csv(path, na_values=[".", ""])
    return pd.Series(
        df.iloc[:, 1].to_numpy(dtype=float), index=pd.to_datetime(df.iloc[:, 0])
    ).dropna()


def sp500_total_return(data: Path) -> pd.Series:
    """Daily total-return index of the S&P 500 (price + Shiller dividend)."""
    gspc = pd.read_csv(data / "GSPC.csv", index_col=0)
    gspc.index = pd.to_datetime(gspc.index, utc=True).tz_localize(None).normalize()
    close = gspc.iloc[:, 0].astype(float)
    shiller = pd.read_excel(data / "ie_data.xls", sheet_name="Data", header=None, skiprows=8)
    shiller = shiller[[0, 1, 2]].dropna()
    shiller = shiller[pd.to_numeric(shiller[0], errors="coerce").notna()]
    year = shiller[0].astype(float).astype(int)
    month = ((shiller[0].astype(float) - year) * 100).round().astype(int)
    period = pd.PeriodIndex(pd.to_datetime(dict(year=year, month=month, day=1)), freq="M")
    monthly_yield = pd.Series(
        (shiller[2].astype(float) / shiller[1].astype(float)).to_numpy() / 12, index=period
    )
    days = close.index.to_period("M")
    per_day = monthly_yield.reindex(days).to_numpy() / days.value_counts().reindex(days).to_numpy()
    returns = close.pct_change() + pd.Series(per_day, index=close.index)
    returns = returns.dropna()
    return (1 + returns).cumprod()


def par_bond_index(yield_pct: pd.Series, maturity_years: float) -> pd.Series:
    """Daily index of a constant-maturity par Treasury, priced exactly."""
    y = yield_pct.sort_index() / 100.0
    dt = y.index.to_series().diff().dt.days / 365.25
    coupon = y.shift(1)
    periods = 2 * (maturity_years - dt)
    discount = (1 + y / 2) ** (-periods)
    price = (coupon / y) * (1 - discount) + discount
    returns = (price - 1 + coupon * dt).dropna()
    return (1 + returns).cumprod()


def baa10y_monthly(data: Path) -> pd.Series:
    spread = (_fred_csv(data / "BAA.csv") - _fred_csv(data / "GS10.csv")).dropna()
    return pd.Series(spread.to_numpy(), index=spread.index + pd.offsets.MonthBegin(1))


def chain_before(real: pd.Series, synthetic: pd.Series) -> pd.Series:
    """`synthetic`'s days before `real` begins, rescaled to meet it, then `real`."""
    real = real.sort_index()
    first = real.index[0]
    anchor = synthetic.loc[:first]
    if anchor.empty:
        return real
    scaled = synthetic / anchor.iloc[-1] * real.iloc[0]
    return pd.concat([scaled.loc[scaled.index < first], real])


def synthetic_for(proxy: str, data: Path) -> pd.Series:
    if proxy == "VFINX":
        return sp500_total_return(data)
    source, maturity = BOND_PROXIES[proxy]
    return par_bond_index(_fred_csv(data / f"{source}.csv"), maturity)


class ExtendedFetcher:
    """`fetcher.fetch_raw_series` with the pre-history added to five series."""

    def __init__(self, data: Path) -> None:
        self._data = data

    async def __call__(
        self, ticker_row: Mapping[str, Any], api_key: str, start: date | None
    ) -> pd.Series:
        raw = await fetcher.fetch_raw_series(ticker_row, api_key, start)
        ticker = str(ticker_row["ticker"])
        if ticker in ("VFINX", *BOND_PROXIES):
            return chain_before(raw, synthetic_for(ticker, self._data))
        if ticker == "BAA10Y":
            monthly = baa10y_monthly(self._data)
            return pd.concat([monthly.loc[monthly.index < raw.index[0]], raw.sort_index()])
        return raw


async def guard(data: Path) -> bool:
    ok = True
    for proxy in ("VFINX", *BOND_PROXIES):
        real = await fetcher.fetch_raw_series({"ticker": proxy, "source": "yahoo"}, "", None)
        synthetic = synthetic_for(proxy, data)
        monthly_real = real.resample("ME").last().pct_change().dropna()
        monthly_syn = synthetic.resample("ME").last().pct_change().dropna()
        overlap = monthly_real.index.intersection(monthly_syn.index)
        corr = monthly_real[overlap].corr(monthly_syn[overlap])
        gap = (monthly_syn[overlap].mean() - monthly_real[overlap].mean()) * 1200
        added = synthetic.loc[synthetic.index < real.index.min()]
        passed = corr >= MIN_MONTHLY_CORR
        ok &= passed
        print(
            f"{proxy}: overlap {overlap[0]:%Y-%m}..{overlap[-1]:%Y-%m} ({len(overlap)} months), "
            f"monthly corr {corr:.3f}, synthetic minus real {gap:+.2f} pt/yr, "
            f"adds {added.index.min():%Y-%m-%d}..{added.index.max():%Y-%m-%d} "
            f"-> {'PASS' if passed else 'STOP'}"
        )
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(guard(Path(sys.argv[1]) / "data")) else 1)
