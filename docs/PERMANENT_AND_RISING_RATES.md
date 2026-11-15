# The permanent portfolio's bonds when rates rise

Owner, 2026-11-15. Two questions about the Browne permanent portfolio
(`permanent-balanced`: SPY / TLT / GLD / cash, 25 each).

1. Since the permanent beats a bond on its own, could the bond quarter be
   replaced by a sub-permanent, and again, until no bond is left? The
   recursion converges on 1/3 SPY, 1/3 GLD, 1/3 cash. It is just the
   permanent without bonds.
2. Are the bonds worth holding only while rates fall? And would a rule that
   drops them while rates rise do better?

Scripts and raw outputs: `docs/research/2026-11-15-permanent-rising-rates/`.
That folder is force-added under the ignored `docs/research`.

## In sample, 1986-06 .. 2026-11 (exploratory, no protocol)

Monthly rebalance, no costs. Before 2002, TLT is the VUSTX splice.

| | CAGR | Sortino | Max drawdown |
|---|---|---|---|
| Permanent | 6.90% | 0.96 | -16.1% |
| No bonds (1/3 each) | 7.13% | 0.90 | -17.3% |

Without bonds, the losses in equity crises doubled: -8.0% against +2.9% in
2000-02, -14.4% against -6.3% in 2007-09, -6.9% against -2.0% in early 2020.
The one exception was 2022 (-5.5% against -12.6%).

Split by the direction of rates, the bonds cost the permanent about
0.9 point a year in months after a rise. The t-statistic is only -1.3. In
months after a fall they cost nothing.

A switching rule beat the permanent in sample. The rule: hold the permanent,
but spread the bond quarter over the other three while TLT is below its
10-month average. Sortino rose from 0.96 to 1.02 and the maximum drawdown went
from -16.1% to -13.6%. Almost all of the gain came from 2021-2023. The rule
lost in 1986-93, in 2000-07 and in 2016-20. And 1986-2020 is a single secular
bond bull market. Hence the test below.

## Out of sample, 1968 .. 1986: the rising-rate decade

`PROTOCOL.md` was written before any figure was computed, amended once before
any figure (the data sources), and run once. It was not committed before the
run; it was written in the same session as the run.

**Data.**
- **Equities:** S&P 500 month-end close plus Shiller's monthly dividend.
  Correlation with VFINX over 1980-2023: 0.993.
- **Long bond:** a 20-year par Treasury priced exactly from FRED DGS20.
  Correlation with VUSTX/TLT over 1986-2026: 0.983, with the same annual
  return (5.20% against 5.16%).
- **Gold:** LBMA.
- **Cash:** the 3-month bill.

Nothing was fitted or rescaled.

| Window | Book | CAGR | Sortino | Max DD |
|---|---|---|---|---|
| 1968-03..1981-09, rates rising | permanent | 9.54% | 0.43 | -11.9% |
| | no bonds | 12.16% | 0.76 | -14.6% |
| | switcher | 9.77% | 0.43 | -15.0% |
| 1975-01..1981-09, rates rising | permanent | 9.79% | 0.24 | -11.6% |
| | no bonds | 12.87% | 0.61 | -12.0% |
| | switcher | 10.71% | 0.35 | -12.0% |
| 1981-10..1986-05, rates falling | permanent | 14.01% | 0.93 | -7.6% |
| | no bonds | 9.43% | 0.07 | -9.9% |
| | switcher | 12.31% | 0.59 | -7.6% |
| 1975-01..1986-05, whole | permanent | 11.49% | 0.46 | -12.2% |
| | no bonds | 11.45% | 0.42 | -18.3% |
| | switcher | 11.36% | 0.42 | -16.2% |

The long bond returned 0.3% a year over 1975-81, against 8.5% for cash.

**Q1 — in the rising-rate decade, the bonds were dead weight.** Without them
the permanent earned about 3 points a year more, with a better Sortino. The
gold windfall of 1968-74 makes the gap larger, but the gap also holds from
1975, after US citizens could hold gold again. Then rates turned in 1981, and
the bonds earned it all back: over 1975-86 the two books are equal on return,
and the permanent's drawdown is 6 points shallower.

**Q2 — the switching rule is REFUSED out of sample.** Its Sortino minus the
permanent's, with a 90% block-bootstrap interval:
- 1968-03..1986-05: -0.077 [-0.211, +0.051];
- 1975-01..1986-05: -0.043 [-0.200, +0.108].

Its maximum drawdown is worse in both windows: -16.2% against -12.2%. The
rule whipsawed in the Volcker years (-6.2% against -4.2% for the permanent)
and missed part of the 1982 bond rally (+23.5% against +28.8%).

A note on the run: before its first full 10-month average, the switcher's
signal read "off" instead of "unknown". So for the first nine months, 1968-03
to 1968-11, the switcher was identical to the permanent. This changes no
difference in returns.

## Reading

Holding no bonds through a decade of rising rates paid. But only someone who
knew that the decade had begun could have done it. The one real-time signal
tried, the bond's own trend, did not find it out of sample. This matches the
two refusals already recorded for the stack: a fast-rising 10-year yield
(2026-10-04), and the bear steepener (2026-11-01). A rates signal laid on top
of a bond's own price adds nothing.
