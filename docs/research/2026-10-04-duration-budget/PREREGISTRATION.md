# Independent duration gate — historical A/B protocol

Written on 2026-10-04 before calculating candidate performance. Research only;
no live allocation rule, threshold, proposal or database row is changed.

## Question and primary candidate

Does a separate duration brake improve the adopted spread/curve stack without
changing its equity or gold targets?

At each existing monthly decision, use the existing DGS10 speed: the latest
knowable 10-year Treasury yield minus its as-of value 30 calendar days earlier.
If the speed is **strictly greater than 0.20 percentage points (20 bp)**, redirect
**all final IEF and VCIT target weight to interest-bearing cash**. Apply this
AFTER the existing trend overlay, including IEF received as its haven. Otherwise
use the original target. Reassess from the original target next month, not from
the previously gated target. No new book classification or hysteresis is added.

This is a deliberately simple, full-exit prototype of the independent-duration
idea, not a complete risk-budget optimizer. A 20 bp threshold is an ex-ante round
stress scale, not fitted to this candidate's performance. A rate increase does
not necessarily mean bear steepening; no curve-direction condition is claimed.

## Fixed comparison

- One SQLite read-only transaction; identical input frames for both arms.
- Production book selection, medians, hysteresis, monthly cadence, price lag,
  price proxies, risk-free cash return and NAV engine remain unchanged.
- Charge the existing 23 bp/order cost, monthly drift rebalances included.
- Walk continuously from the existing priceable start (1993-11-01) to the
  latest common trading date. Slice NAVs for subperiods; do not reset the book
  or hysteresis at a boundary or price past a window's stated end.
- Primary window: the strategy's existing pinned window, ending 2026-07-01.
- Chronological robustness: 1993-2008 and 2009 to that same pinned end.
- Diagnostic windows fixed before results: calendar years 1994, 2008, 2020,
  2022; the full history through latest data; and the post-pinned tail.
- Sensitivity candidates: 10 bp and 30 bp, the same full exit. They are not
  alternatives from which to select the best performer after seeing results.
- Report CAGR, Sortino (MAR = risk-free), Calmar, daily maximum drawdown,
  realized calendar-year returns, target cash exposure and target-change
  turnover (the existing engine's turnover excludes drift-only rebalances,
  although those rebalances are charged).

## Evidence and decision criteria

Use the project's existing paired moving-block bootstrap: 63 trading-day
blocks, 2,000 draws and its fixed seed. Report its 90% interval for the Sortino
delta and the fraction of resamples with delta <= 0. The project's adoption
criterion is Pareto improvement in CAGR/Sortino/Calmar/drawdown, with a one-sided
5% bootstrap evidence gate. Report its verdict, including trade-off or
insufficient evidence, whatever it says. Also check 21- and 126-day blocks on
the primary window; these diagnose dependence sensitivity and do not retune
the candidate.

A research recommendation additionally requires that the primary candidate
does not lose its direction of benefit in either chronological half. These
halves are **not virgin out-of-sample tests**: the baseline and prior research
have already used them. This protocol prevents tuning during this comparison,
but does not undo that historical selection. No rule will be deployed from
this experiment. If historical results warrant it, the next independent test
is a frozen prospective shadow comparison, subject to owner authorization.

## Known limitations

Historical ETF prices use the project's mutual-fund/gold proxies before ETF
inception. Publication dating and the one-day price lag are inherited from the
production harness, not independently re-audited intraday in this experiment.
Cash earns the same modeled ^IRX return as production, not a newly verified
broker cash rate. Taxes, market impact, actual execution spreads, FX and actual
holdings are outside the existing cost model. A binary rate signal can miss
bond carry or a quick yield reversal; the existing price-trend overlay may
already cover most of the losses. Subperiod selection and bootstrap cannot
prove future profitability or causal identification of a macro regime.

## Warm-up clarification (before any candidate performance was calculated)

The loader derives speeds on the common priceable calendar, which begins on
1993-11-01, rather than on the older raw DGS10 history. Its first two monthly
decisions (November 1 and December 1) consequently have unavailable 30-day
speed. Preserve the baseline target for unavailable speed within the initial
30-calendar-day warm-up plus a 7-day publication/calendar grace, and record
these dates explicitly. Any later unavailable speed is an error, never a
silent no-op. A missing DGS10 series still refuses the entire comparison.
