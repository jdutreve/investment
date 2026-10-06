# Private-credit cycle overlay — frozen research protocol

Specified on 2026-10-05 before downloading credit observation values or
calculating candidate performance. Research only: do not edit existing source,
configuration, strategy documents, data tables, proposals or allocation rules.
New files in this research directory are the only intended writes.

## Hypothesis and data

The earlier research recommendation was to add slow private-credit accumulation
to the existing price-based credit/slope stack, not another rates exit. Test
whether a limited equity reduction after credit booms adds value beyond the
existing monthly trend and spread-speed protections.

Source: BIS/FRED **QUSPAM770A**, total credit to the US private nonfinancial
sector, adjusted for breaks, percent of GDP. Government debt is excluded;
do not substitute the existing domestic-nonfinancial `DEBT_TO_GDP` composite.
FRED's vintage-date inventory has 40 dates, from 2016-06-06 to 2026-06-15.
As-of ceiling: 2026-10-04, the original recommendation date. Freeze all downloads
locally, hash them, and never print the API key.

For the latest reference quarter q available in a particular vintage:

    L(q) = mean(C(q-j) - C(q-j-12), j = 0, 1, 2, 3)

`C` is private-credit/GDP, and L is measured in **percentage points of GDP**.
Quarter labels, not publication-row offsets, identify the three-year difference.
The four-quarter smoothing and three-year change follow Davis and Taylor's
leverage-factor design. Using the last four *published* quarters adapts the lag
to actual availability; this is not an exact replication of their portfolios.

## One primary candidate, fixed before measurement

- Credit boom: **L > 10 pp**, strictly greater.
- At each original monthly decision, reduce the final, post-overlay **SPY and
  IWN** target weights by **20% of their weight** when a boom is active.
- Transfer only the released amount to that decision's existing haven:
  **IEF**, or **cash** if IEF is below both of its moving averages. Reuse the
  original decision's haven property; do not introduce another trend rule.
- Leave GLD and VCIT untouched; never remove existing IEF because rates rose.
- Keep book selection, hysteresis, dates, price lag, trend reads and spread
  gates identical. Recompute changed-target dates, including re-entry.
- The credit reading changes only on publication; hold it between releases.
  Make no new daily or weekly allocation decisions.
- If the credit signal is unavailable, keep the baseline target and explicitly
  mark the decision unavailable. Do not silently treat missing data as calm.
- The 10 pp threshold and 20% haircut are **research design choices**, not
  estimated coefficients or cutoffs proven by the paper. No optimizer.

## Two data tracks, with different evidential status

1. **Long-history diagnostic:** latest credit vintage, dated at reference
   quarter-end + **180 calendar days**. Preserve all input warm-up history.
   This deliberately conservative assumed release lag does NOT remove historical
   revisions and is NOT a strict point-in-time backtest.
2. **Vintage-aware replay:** at each ALFRED release, compute L from the full
   historical snapshot known at that release. First availability is the day
   after the recorded release, to avoid assuming an intraday publication before
   execution. Start reporting at the first original monthly decision with
   available vintage data, expected July 2016. No invented pre-2016 vintages.
   Also replay the latest vintage on the **same actual release dates and latest
   reference quarters** as a matched revision diagnostic. This holds the calendar
   and information horizon constant while changing only revisions.

For the vintage track, anchor the NAV window at the close immediately before
the first eligible monthly decision, so that decision's first-day return and
transaction costs are not discarded. Record the activation date separately.

For the long diagnostic, repeat at **90 and 270 days** of assumed publication
lag, primary threshold unchanged. For the primary 180-day lag and vintage-aware
track, repeat at **5 and 15 pp** threshold, haircut unchanged. These are labelled
sensitivity checks, not a menu from which to select a winning rule. Keep and
report unsuccessful results. Do not add further thresholds after inspection.

## Pricing, comparisons and evidence

Reuse the unchanged production decision walk and NAV engine on the frozen market
input export from the prior duration experiment. Verify that repricing original
decisions reproduces the baseline daily NAV. Reuse **23 bps/order**, cash ^IRX,
monthly drift rebalance and adjusted-price/proxy conventions. Never initialize
the writing InvestmentDB wrapper on the live database.

Report CAGR, Sortino (MAR=rf), Calmar, maximum drawdown, annual volatility,
turnover, modified decisions, mean target equity/cash and annual returns.
Primary window: **1993-11-01 to 2026-07-01**. Also show 1993-2008, 2009-2026-07-01,
through latest frozen prices (2026-10-02), calendar years 2000, 2001, 2002, 2008,
2020 and 2022, plus the vintage-covered common window.

Compare against (a) the adopted stack and (b) a **constant equity haircut** with
the same mean daily standing-target equity exposure over each track's main
measurement window. Its haircut is derived once from the mean exposure removed
by the candidate, not by maximizing returns. It is an **ex-post attribution
control, not an implementable strategy recommendation**, and matches target
equity exposure, not volatility, beta, or daily drifted holdings.

Use the project's 2,000-draw paired **63-trading-day** bootstrap for continuity
with the earlier test. Add **252- and 756-trading-day** paired circular blocks on
the primary comparisons: a slow credit signal has few independent cycles, so
short blocks can overstate the evidence. Use the same fixed seed. Report the
90% intervals and resampling fractions, not a probability of future success.
Compare candidate against both baseline and exposure-matched control.

Candidate merits further work only if it improves net Sortino without degrading
CAGR, Calmar or maximum drawdown materially, the chronological halves do not
contradict it, and improvement is not explained solely by the exposure control.
No automatic adoption even on passing estimates: the revised long history,
small vintage sample, historical selection and untested live execution remain
limitations. Chronological splits are robustness checks, not untouched OOS data.

## Sources

- Davis and Taylor, *The Leverage Factor: Credit Cycles and Asset Returns*,
  Management Science (2022):
  <https://pubsonline.informs.org/doi/abs/10.1287/mnsc.2022.4508?journalCode=mnsc>.
- Authors' methods/results presentation:
  <https://cepr.org/voxeu/columns/credit-cycles-and-asset-returns>.
- BIS/FRED series definition:
  <https://fred.stlouisfed.org/series/QUSPAM770A>.
- ALFRED series:
  <https://alfred.stlouisfed.org/series?seid=QUSPAM770A>.
