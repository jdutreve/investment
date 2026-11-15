# The permanent portfolio in a decade of rising rates — protocol (written before any figure)

Date: 2026-11-15. Run once; the figures that come out are the answer.

## Questions
Q1. In the rising-rate era 1968-01 .. 1981-09 (10-year yield ~5.5% -> 15.8%), does the
    Browne permanent (SPY/TLT/GLD/cash 25 each) beat its no-bond limit (1/3 SPY, GLD, cash)?
Q2. Does the switching rule found in-sample on 1986-2026 on 2026-11-15 — permanent, but the
    bond quarter spread over the other three while TLT is below its 10-month average
    (signal at month-end t-1, applied to month t) — hold OUT OF SAMPLE on 1968-01 .. 1986-05?

## Data (no parameter fitted)
- Equities: Ken French Mkt-RF + RF (CRSP total market, dividends included), daily -> monthly.
- Long bond (TLT proxy): a 20-year par Treasury priced EXACTLY each month-end from FRED DGS20
  (the last daily yield of each month): bought at par with coupon y(t-1), sold one month later
  as a 19y11m bond at y(t). Duration follows the yield level by construction, nothing fitted.
  Validated on the overlap with the live TLT series (1993-10 ..): monthly correlation and
  annual return gap reported; the proxy is NOT rescaled.
- Gold: the live GLD series (LBMA AM fix, 1968-). Caveat stated in advance: US citizens could
  not hold gold until 1975-01 and the dollar was tied to gold until 1971-08, so 1968-74 gold
  is a liberalisation windfall, not a repeatable return. Q1/Q2 are therefore ALSO reported
  from 1975-01.
- Cash: Ken French RF (1-month T-bill).
- Monthly rebalancing, no trading costs (the same for every book; costs only hurt the switcher).

## Metrics and decision
Sortino (excess over RF, downside deviation), CAGR, maximum drawdown, per window.
Sortino difference with a 90% interval from a circular block bootstrap (12-month blocks,
2000 resamples, seed 7).
- Q2 HOLDS out of sample iff, on 1968-01..1986-05 AND on 1975-01..1986-05, the switcher's
  Sortino minus the permanent's has a 90% interval whose lower bound is above 0
  and its maximum drawdown is not worse. Anything else: REFUSED out of sample.
- Q1 is descriptive (which book did better in the rising-rate decade, by how much, and where).

## Amendment before any figure (2026-11-15, same session)
The Ken French library is unreachable from this machine's sandbox, and Yahoo's VFINX starts
1980-01. Data sources replaced, the rest of the protocol unchanged:
- Equities: S&P 500 month-end close (Yahoo ^GSPC) + Shiller's monthly dividend (D/12 of the
  month, ie_data.xls) as the income return. Validated on its overlap with VFINX (1980..2023):
  monthly correlation and annual return gap reported, not rescaled.
- Cash: the 3-month T-bill (^IRX, live database), y(t-1)/12 — as in the 1986-2026 calculation
  the switching rule came from.
