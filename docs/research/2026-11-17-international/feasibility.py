"""Short cross-country feasibility test (docs/INTERNATIONAL_FEASIBILITY.md). Run once.

Usage: feasibility.py WORK_DIR — reads WORK_DIR/data/*.csv (FRED OECD series).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest

COUNTRIES = ["GB", "DE", "JP", "CA", "FR"]
REFERENCE = "US"
THRESHOLD = 3.0
MIN_QUARTERS = 20


def series(data: Path, name: str) -> pd.Series | None:
    path = data / f"{name}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, na_values=[".", ""])
    s = pd.Series(df.iloc[:, 1].to_numpy(dtype=float), index=pd.to_datetime(df.iloc[:, 0]))
    return s.dropna()


def par_bond_monthly(yield_pct: pd.Series, maturity: float = 10.0) -> pd.Series:
    """Monthly return of a par bond bought at y(t-1) and revalued at y(t)."""
    y = yield_pct / 100.0
    coupon = y.shift(1)
    periods = 2 * (maturity - 1 / 12)
    discount = (1 + y / 2) ** (-periods)
    price = (coupon / y) * (1 - discount) + discount
    return (price - 1 + coupon / 12).dropna()


def country(data: Path, cc: str) -> pd.DataFrame | None:
    long = series(data, f"IRLTLT01{cc}M156N")
    cpi = series(data, f"CPALTT01{cc}M659N")
    short = series(data, f"IR3TIB01{cc}M156N")
    shares = series(data, f"SPASTT01{cc}M661N")
    if long is None or cpi is None or short is None or shares is None:
        return None
    bond_m = par_bond_monthly(long)
    frame = pd.concat(
        {
            "real_yield": long - cpi.shift(1),  # the CPI of the previous month is the one known
            "bond_3m": (1 + bond_m).rolling(3).apply(np.prod, raw=True).shift(-3) - 1,
            "cash_3m": short / 100 / 4,
            "equity_3m": shares.shift(-3) / shares - 1,
        },
        axis=1,
    )
    grid = frame[frame.index.month.isin([1, 4, 7, 10])].dropna()
    grid["a"] = grid["bond_3m"] - grid["cash_3m"]
    grid["b"] = grid["bond_3m"] - grid["equity_3m"]
    for outcome in ("a", "b"):
        grid[f"{outcome}_ok"] = grid[outcome] > grid[outcome].median()
    grid["on"] = grid["real_yield"] > THRESHOLD
    return grid


def main(work: Path) -> None:
    data = work / "data"
    pooled = {"a": [0, 0], "b": [0, 0]}
    qualified = 0
    print(
        f"{'country':8s} {'span':17s} {'quarters':>8s} {'on':>4s}  "
        "outcome A (bond-cash)   outcome B (bond-equity)"
    )
    for cc in [*COUNTRIES, REFERENCE]:
        grid = country(data, cc)
        if grid is None or grid.empty:
            print(f"{cc:8s} missing data")
            continue
        on = grid[grid["on"]]
        line = f"{cc:8s} {grid.index[0]:%Y-%m}..{grid.index[-1]:%Y-%m} {len(grid):8d} {len(on):4d}"
        for outcome in ("a", "b"):
            k, n = int(on[f"{outcome}_ok"].sum()), len(on)
            p = binomtest(k, n, 0.5, alternative="greater").pvalue if n else float("nan")
            line += f"  {k:3d}/{n:<3d} = {k / n if n else float('nan'):.2f} p {p:.3f}"
            if cc != REFERENCE and len(on) >= MIN_QUARTERS:
                pooled[outcome][0] += k
                pooled[outcome][1] += n
        if cc != REFERENCE and len(on) >= MIN_QUARTERS:
            qualified += 1
        print(line)
    print(
        f"\nFEASIBLE (>= 4 of 5 with {MIN_QUARTERS}+ quarters on): {qualified} -> "
        f"{'YES' if qualified >= 4 else 'NO'}"
    )
    for outcome, label in (("a", "A bond-cash"), ("b", "B bond-equity")):
        k, n = pooled[outcome]
        p = binomtest(k, n, 0.5, alternative="greater").pvalue if n else float("nan")
        verdict = (
            ("HOLDS INTERNATIONALLY" if p < 0.05 else "DOES NOT HOLD")
            if outcome == "a"
            else "reported"
        )
        print(f"POOLED non-US, outcome {label}: {k}/{n} = {k / n:.3f}, p {p:.4f} -> {verdict}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
