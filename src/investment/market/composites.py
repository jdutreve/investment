"""THE COMPOSITES, declared once and built by one function.

A composite is a MarketData series computed from other MarketData series
(docs/DATA_MODELS.md "Composite series"). There are four, and until 2026-09-21
each of them carried a hand-written block in `seed.py` AND another in
`mechanical/catchup.py` — eight blocks that had to agree on the ticker, the
asset class, the inputs, the guard when an input is missing and the shape of
the rows. They drifted the first chance they got: `refresh_composites` still
announced "GROWTH_COMPOSITE and GLOBAL_LIQUIDITY" after the debt leg made it
four.

That is CLAUDE.md's "when a second one arrives" at its fourth arrival, so the
answer here is not a third copy but a declaration: adding a fifth composite is
a row in `COMPOSITES` and a compute function, nothing else.

WHAT LIVES WHERE, deliberately. The FORMULAS stay in the modules that own them
— `growth.py`, `liquidity.py`, `debt.py` — each next to the reasoning that
justifies it, and so do the input names (`liquidity.COMPONENTS` is read by the
digest's freshness line too). This registry owns only what the two PRODUCERS
need: which composites exist, what each needs to be computed, and how its rows
are stamped.

WHAT THE PRODUCERS STILL DECIDE, because they genuinely differ: where the input
series come from (the seed holds them in memory at full history, the catch-up
reads them back from SQLite), how far back rows are kept, how a write is
persisted, and how a missing input is reported.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from investment.market import debt, derivatives, growth, liquidity


@dataclass(frozen=True)
class Composite:
    """One computed series: its identity, its ingredients, and its recipe."""

    ticker: str
    asset_class: str
    inputs: tuple[str, ...]
    compute: Callable[[Mapping[str, pd.Series]], pd.Series]
    currency: str = "USD"


def _growth(series: Mapping[str, pd.Series]) -> pd.Series:
    return growth.compute_growth_composite(series["INDPRO"], series["UNRATE"])


def _global_liquidity(series: Mapping[str, pd.Series]) -> pd.Series:
    eurusd, usdjpy = series["DEXUSEU"], series["DEXJPUS"]
    usd = {t: liquidity.usd_convert(t, series[t], eurusd, usdjpy) for t in liquidity.COMPONENTS}
    return liquidity.compute_global_liquidity(usd)


def _debt_to_gdp(series: Mapping[str, pd.Series]) -> pd.Series:
    numerator, denominator = debt.DEBT_COMPONENTS
    return debt.compute_debt_to_gdp(series[numerator], series[denominator])


def _credit_growth(series: Mapping[str, pd.Series]) -> pd.Series:
    return debt.compute_credit_growth(series[debt.CREDIT_COMPONENT])


COMPOSITES: tuple[Composite, ...] = (
    Composite("GROWTH_COMPOSITE", "MACRO", ("INDPRO", "UNRATE"), _growth),
    Composite(
        "GLOBAL_LIQUIDITY",
        "GLOBAL_LIQUIDITY",
        (*liquidity.COMPONENTS, *liquidity.FX_TICKERS),
        _global_liquidity,
    ),
    Composite("DEBT_TO_GDP", "MACRO", debt.DEBT_COMPONENTS, _debt_to_gdp),
    Composite("CREDIT_GROWTH", "MACRO", (debt.CREDIT_COMPONENT,), _credit_growth),
)


def missing_inputs(composite: Composite, available: Mapping[str, pd.Series]) -> list[str]:
    """The inputs this composite needs and does not have. An input present but
    EMPTY counts as missing — the catch-up reads its inputs back from SQLite
    and gets an empty series for one never fetched."""
    return [t for t in composite.inputs if available.get(t) is None or available[t].empty]


def rows_for(
    composite: Composite,
    available: Mapping[str, pd.Series],
    lookback: int,
    start: date | None,
) -> list[dict[str, Any]]:
    """The composite's `market_data` rows, computed and stamped.

    Callers check `missing_inputs` first; this one assumes its ingredients are
    there. `start` truncates for storage AFTER the computation, so a composite
    with a trailing window still sees the whole history it was given.

    THE WARM-UP IS NOT AN OBSERVATION, and dropping it is load-bearing rather
    than tidy. A z-score over a trailing window is NaN until the window fills —
    ten years for growth, five for liquidity — and `market_data_rows` turns NaN
    into SQL NULL, so those rows exist, carry no value, and still carry a `ts`.
    That made them the FIRST date of the series, which is what
    `replace_ts_series(keep_earlier_rows=True)` bounds its delete by: the
    catch-up would have deleted the seed's real early values and written its own
    NULLs over them, exactly the loss the mode was added to prevent. Measured on
    the live database before this: GROWTH_COMPOSITE held 122 NULL levels over
    1991-2001 and GLOBAL_LIQUIDITY 406 over 1991-2003, where the seed — which
    computes from the full fetch and truncates afterwards — had values.

    LEADING only, via `first_valid_index`, not `dropna()`: an interior gap is a
    real hole in the inputs and must stay one, or the 1-observation lookback
    would difference two readings that are not adjacent."""
    series = composite.compute(available)
    first = series.first_valid_index()
    if first is not None:
        series = series.loc[first:]
    deriv = derivatives.compute_derivatives(series, composite.ticker, lookback)
    return derivatives.market_data_rows(
        composite.ticker, composite.asset_class, composite.currency, deriv, start
    )
