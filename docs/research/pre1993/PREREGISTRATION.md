# Pre-registration — market-signal stack on data it never saw (1977-07 → 1993-10)

Written 2026-09-13, BEFORE any test-window result was computed.

## Question
Does the market-signal stack, with its rule frozen as committed at 7f0fe24, beat the
BEST of its four books held frozen (same trend overlay, same monthly clock, same
23 bps cost) on a period that played no part in building it?
Owner's bar (2026-09-13): the stack must beat the best frozen book, not a blend.

## Window
Monthly decisions from 1977-07-01 to 1993-10-29 (the in-sample walk starts
1993-11-01). T10Y2Y starts 1976-06-01 and a median needs 252 observations, so no
earlier start uses the rule's own signal.

## Criteria (fixed now)
1. PRIMARY: Sortino(stack) > max over the 4 frozen books of their Sortino, same window.
2. Secondary: paired moving-block bootstrap (63 trading days, B = 2000) of the Sortino
   delta against that best book — 90% CI and P(delta <= 0); CAGR; max drawdown (daily);
   the same against the S&P proxy.
3. Reported whatever it says. A loss is recorded as "the in-sample edge over the best
   frozen book is not confirmed out of sample".

## Data (proxies spliced onto the live series, levels chained at the first live date)
- SPY before 1980-01-03: Fama-French daily market (Mkt-RF + RF), CRSP value-weighted.
- IWN before 1993-03-01: Fama-French daily SMALL HiBM (value-weighted small value).
- IEF before 1991-10-29: synthetic 7-10y Treasury total return from FRED DGS10
  (carry + duration + convexity, duration fixed at 7.5).
- VCIT before 1993-11-01: synthetic IG corporate total return from (Aaa + Baa)/2,
  daily DAAA/DBAA from 1986-01-02, monthly AAA/BAA before (placed on the first
  trading day of the following month), duration fixed at 6.3.
- BAA10Y before 1991-09-04: DBAA - DGS10 daily from 1986-01-02; monthly BAA - GS10
  before, dated the first day of the following month (ADR-003 knowability).
- T10Y2Y, DGS10 before 1991-09-04: FRED daily, as published.
- GLD (LBMA from 1968) and ^IRX (from 1960) already in the database.

## Calibration rule (the only tuning allowed)
Each return proxy is compared with the live series on their overlap. If its mean
return differs, that mean daily gap is subtracted from the proxy over the whole
pre-splice period — one number per sleeve, measured on the overlap only, never on
the test window. Duration and convexity are not fitted.

## Fidelity check (not a criterion)
Verdad's original three-portfolio rule, gross of costs, run on the same data: its
1980s CAGR is compared with the paper's 24.7% to judge whether the reconstructed
data can reproduce the source at all.

## Known limits, stated before the result
Monthly (not daily) credit spread before 1986, so its 30-day speed is a monthly step;
Fama-French small value is an academic, cost-free portfolio, not IWN; bond sleeves
are synthetic; the slope median runs on less than 10 years of history until 1986,
exactly as it would have live at the time; 1973-74 is outside the window.

## Amendment (2026-09-13, still before any test-window result)
- FRED daily signals (BAA10Y from DBAA - DGS10, T10Y2Y, DGS10) are dated observation
  date + 1 calendar day: the live database's `availability_lag_days = 1`, verified to
  reproduce the stored rows exactly (100% of the overlap).
- VCIT before 1986: the monthly (Aaa + Baa)/2 value is forward-filled across trading
  days until the next one, so the sleeve is priced every trading day (a monthly-only
  price would reduce the all-sleeves calendar to one date a month).
- Calibration haircuts measured on the overlaps, per year: SPY +0.62%, IWN +1.95%,
  IEF +0.12%, VCIT +1.02% (proxy minus live), subtracted from the proxies.
  Overlap correlations (daily / monthly): SPY 0.973/0.983, IWN 0.975/0.978,
  IEF 0.933/0.965, VCIT 0.768/0.875 — VCIT is the weak sleeve.
