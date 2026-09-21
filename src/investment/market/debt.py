"""DEBT_TO_GDP and CREDIT_GROWTH composites (ADR-003 vintage discipline).

WHY THESE TWO SERIES EXIST AT ALL. The corpus's largest document is Dalio's
*Principles for Navigating Big Debt Crises*, and a recall study of its
archetypal-cycle chapter (2026-09-20) found the curator's extraction complete
but its yield capped by the VOCABULARY, not by the reading: three of the eight
reference notes that batch produced name the same missing thing — "every
condition here requires debt aggregates (debt/GDP, debt service/income, debt
growth vs income growth) that do not exist in the signal vocabulary". A book
about debt cycles was being read by an agent that measured no debt. These two
composites plus DRSFRMACBS (raw) are that gap closed.

WHAT EACH ONE ANSWERS, in the book's own terms:
  - DEBT_TO_GDP — "total debt-to-income levels averaged around 300 percent of
    GDP" at the top, and "the typical bubble sees leveraging up at an average
    rate of 20 to 25 percent of GDP over three years". The LEVEL carries the
    first claim, the quarter-on-quarter `speed` the second.
  - CREDIT_GROWTH — "debt growing faster than incomes", and, on the way down,
    "risky lending slows". Weekly H.8 bank credit, so this one moves at the
    agent's own cadence rather than the Z.1's quarterly crawl.

TWO SOURCES, TWO DATING PATHS, and that asymmetry is deliberate. GDP is
revised heavily and its ALFRED vintages reach back to 1991-12-04 — the whole
backfill window — so it takes the first-release path (`fetcher.REVISED_SERIES`).
TCMDODNS is revised too, but ALFRED holds only 57 vintages starting 2010-06-10:
the first-release path would silently truncate the series to 2010 and cost 19
years of history. It therefore takes the current-vintage path with an
`availability_lag_days` that encodes the Z.1's real publication delay. Each leg
is still dated by when it became KNOWABLE, which is what ADR-003 requires; they
simply learn it by different means.

THE INDICES DO NOT ALIGN, for that same reason: a first-release date and an
observation-plus-lag date are different calendars. The ratio is therefore taken
on the NUMERATOR's calendar — one point per debt print, against the latest GDP
known at that date — and NOT on the union of the two.

THE UNION WAS THE FIRST ATTEMPT AND IT WAS WRONG (fixed 2026-09-20, same day it
shipped). Both legs grow every quarter but print on different dates, so on the
union the ratio sawtoothed by about ±3 points: a GDP print lifted the
denominator alone and the ratio fell, the following debt print lifted the
numerator alone and it jumped back. `speed` then measured WHICH LEG HAD JUST
PUBLISHED, not whether debt was rising against income — and `debt_to_gdp.speed
> 0` would have fired every other print, deterministically, on a series the
curator was about to be handed.

THE ONE CASE THE NUMERATOR'S CALENDAR DOES NOT FIX is a late denominator. GDP
for quarter Q first prints about 115 days after Q starts and the Z.1 debt
figure about 165, so the debt print normally finds its OWN quarter's GDP
waiting — which is why the pairing works at all. When a release slips it does
not: Q3 2025 GDP appeared on 2025-12-23, ten days AFTER that quarter's debt
figure, so the naive pairing divided Q3 debt by Q2 GDP and invented a 5.8-point
jump followed by a 5.5-point fall. A GDP print older than `GDP_STALE_DAYS`
means the two legs are not the same quarter, and that point is skipped rather
than guessed: measured over 1991-2026 this rejects exactly ONE quarter of 140,
and the quarterly move drops to a 1.07-point median (the 47-point move that
remains is 2020 Q2, where it is the economy and not the calendar).
"""

import pandas as pd

from investment.market.derivatives import asof_lag

# Z.1 domestic nonfinancial debt (millions USD) over GDP (billions USD, SAAR).
DEBT_COMPONENTS: tuple[str, str] = ("TCMDODNS", "GDP")

# H.8 bank credit, all commercial banks (billions USD, weekly).
CREDIT_COMPONENT = "TOTBKCR"

# CREDIT_GROWTH is a year-on-year percent change, and it is computed here
# rather than by `apply_transform`'s `yoy_pct` because that transform is a
# 12-OBSERVATION shift: on a monthly series that is a year, on this weekly one
# it would be twelve weeks wearing a year's name. A calendar window says what
# it means whatever the series' cadence.
CREDIT_GROWTH_WINDOW_DAYS = 365

_MILLIONS_PER_BILLION = 1000.0

# How stale the GDP print may be, relative to the debt print it is divided
# into. Normal spacing is ~50 days (115 vs 165 after the quarter starts); a
# full quarter means a release slipped and the legs are a quarter apart.
GDP_STALE_DAYS = 90


def compute_debt_to_gdp(debt_millions: pd.Series, gdp_billions: pd.Series) -> pd.Series:
    """Domestic nonfinancial debt as a PERCENT of GDP.

    Empty in, empty out — the seed and the catch-up both guard on their inputs,
    but a composite that raises on a missing component would abort a whole
    backfill for one unavailable series."""
    if debt_millions.empty or gdp_billions.empty:
        return pd.Series(dtype=float)
    debt = debt_millions.sort_index() / _MILLIONS_PER_BILLION
    gdp = gdp_billions.sort_index()
    both = debt.index.union(gdp.index)
    # ffill, NOT interpolate: between two prints the latest KNOWN value is what
    # was knowable, and interpolating would leak the next print backwards.
    gdp_value = gdp.reindex(both).ffill().reindex(debt.index)
    # The DATE of that print, carried alongside its value, so staleness can be
    # judged — a value alone cannot say how old it is.
    gdp_published = pd.Series(gdp.index, index=gdp.index).reindex(both).ffill().reindex(debt.index)
    same_quarter = (debt.index - gdp_published) <= pd.Timedelta(days=GDP_STALE_DAYS)
    ratio = 100.0 * debt / gdp_value
    return ratio[same_quarter].dropna()


def compute_credit_growth(bank_credit: pd.Series) -> pd.Series:
    """Year-on-year percent change in bank credit, on a CALENDAR window."""
    if bank_credit.empty:
        return pd.Series(dtype=float)
    level = bank_credit.sort_index()
    year_ago = asof_lag(level, CREDIT_GROWTH_WINDOW_DAYS)
    growth = 100.0 * (level / year_ago - 1.0)
    return growth.dropna()
