# Independent duration gate — result: do not adopt this prototype

Measured on 2026-10-04, one common data vintage, no live allocation changes.
The pre-registered **20 bp / 30 calendar-day full exit from final IEF and VCIT
targets into cash** loses return and Sortino without improving the whole-history
maximum drawdown. Its two neighboring thresholds do not rescue the finding.
This rejects the simple full-exit implementation, not every possible separate
duration budget or rates hedge.

## Primary comparison: 1993-11-01 to 2026-07-01

| Indicator | Adopted stack | Duration gate | Difference |
|---|---:|---:|---:|
| CAGR, net of modeled fees | 11.5129% | 10.8135% | -0.6994 pp/year |
| Sortino, MAR = risk-free | 1.2959 | 1.2102 | -0.0857 |
| Calmar | 0.6977 | 0.6553 | -0.0424 |
| Daily maximum drawdown | -16.5004% | -16.5004% | Unchanged |
| Mean target cash allocation | 5.13% | 9.52% | +4.39 pp |

The gate changes **29 of 393 monthly targets** in the primary window. Equity and
gold targets and every signalled/held book remain identical by construction.
The identical worst drawdown occurs in March 2020, when the gate changes no target.

The project's paired 63-day block bootstrap, 2,000 draws, gives a **90% interval
for delta Sortino of [-0.1665, -0.0230]**. Only 0.95% of resamples are nonnegative;
99.05% are negative. This is resampling evidence about this history, **not a
99.05% probability of future underperformance**. Its verdict is `reject`.
With 21- and 126-day blocks the intervals remain entirely negative, respectively
[-0.1530, -0.0228] and [-0.1629, -0.0211].

## Chronological and threshold checks

| Window | Baseline CAGR | Gate CAGR | Baseline Sortino | Gate Sortino |
|---|---:|---:|---:|---:|
| 1993-2008 | 12.5800% | 11.1077% | 1.2948 | 1.1038 |
| 2009-2026-07-01 | 10.6980% | 10.6534% | 1.3110 | 1.3099 |
| Through 2026-10-02 | 11.3432% | 10.6503% | 1.2763 | 1.1909 |

The early-half degradation has a negative bootstrap interval; the late-half
difference is essentially flat and its interval crosses zero. These are
chronological robustness checks, **not untouched out-of-sample tests**. The
project's `reject` verdict for nearly flat comparisons can mean "no material
benefit above the existing numerical floor," not statistically proven harm.

| Threshold sensitivity, full primary window | CAGR | Sortino | Max drawdown | Project verdict |
|---|---:|---:|---:|---|
| 10 bp | 10.2355% | 1.1341 | -16.5004% | reject |
| **20 bp, primary** | **10.8135%** | **1.2102** | **-16.5004%** | **reject** |
| 30 bp | 11.1230% | 1.2490 | -16.5004% | insufficient |

The 30 bp candidate is still worse in full-history point estimates and worse in
the early half; its full-window evidence is inconclusive. It is not a winner
selected retrospectively. The post-pinned July-October tail has no primary-gate
target differences and is too short for bootstrap evidence.

## Why the mechanical argument did not earn an allocation change

Calendar-year returns below include the first day's return from the previous
year's closing NAV. The within-year metric slices in metrics.json start at the
first close of that year, so their returns/CAGR intentionally differ slightly.

| Calendar year | Adopted stack | Gate | Monthly targets modified |
|---|---:|---:|---:|
| 1994 | -4.14% | -4.14% | 0 |
| 2008 | +13.23% | +0.87% | 4 |
| 2020 | +18.89% | +18.89% | 0 |
| 2022 | -10.87% | -10.87% | 0 |

- On 2008-11-03, the standing overlay target is **IEF 100%**. The prior 30-day
  DGS10 speed is +0.34 pp, so the gate sets cash 100% for November. The baseline
  subsequently returns +7.73% that month while the variant returns -0.45% net
  of costs. A recent rate rise is not a forecast that rates will keep rising.
  Exiting the intermediate-Treasury haven can remove the crisis rebound.
- In 2022 the gate removes nothing: whenever the yield trigger fires, the
  original post-overlay target already contains no IEF or VCIT. This particular
  rate brake supplies no extra protection against that year's losses.
- Costs amplify the damage. In the **zero-cost diagnostic**, CAGR is 12.5884%
  versus 12.2753%, a -0.3131 pp gap. At the actual modeled 23 bp/order it is
  -0.6994 pp. This gross diagnostic is not statistically conclusive on its own.
- Through latest data, target-change turnover rises **67.13 to 91.87** (+37%)
  and target-change dates rise 167 to 187. These turnover sums are lifetime
  one-way-equivalent fractions, not annual percentages. Drift-only rebalances
  are omitted from that turnover counter but their fees are included in NAVs.

Removing VCIT also removes credit carry, not just rate exposure. Accordingly
this is not a clean experiment holding every economic risk constant. A true
duration hedge or a graduated duration budget would need a new specification
and a new independent test; this failed cash gate is not evidence for deploying
one. Do not search more thresholds on the same history until one looks good.

## Scope and recommendation

Keep the adopted allocation rule unchanged. Separating rate-risk explanations
from macro regime labels remains useful, but a useful explanation is not an
empirically validated trading edge. The tested candidate fails the existing
Pareto/evidence bar and the pre-registered chronological robustness requirement.

All performance is modeled in USD on the project's adjusted-price history and
pre-inception proxies. Cash earns modeled ^IRX, not a verified Saxo deposit
rate. The existing publication dating and one-day decision-price lag are
preserved, not independently re-audited intraday here. Historical selection,
proxy fidelity, taxes and actual execution remain limitations.

## Reproduce and inspect

From the repository root:

```sh
.venv/bin/python docs/research/2026-10-04-duration-budget/comparison.py \
  --inputs docs/research/2026-10-04-duration-budget/results/inputs.csv \
  --output /private/tmp/duration-comparison
.venv/bin/pytest -q docs/research/2026-10-04-duration-budget/test_comparison.py
```

For a new data vintage, replace `--inputs ...` with the database path. The script
opens a read-only transaction and never initializes or migrates the live schema.
It exports all inputs before calculations. The two unavailable initial speed
readings are recorded as warm-up, not interpreted as calm rates.

Artifacts:

- `PREREGISTRATION.md`: frozen primary rule, thresholds, periods and criteria.
- `comparison.py`: runnable research harness; no live allocation code changes.
- `results/metrics.json`: all window metrics, bootstrap results, source hashes,
  Git state, source dates, parameters and input SHA-256.
- `results/inputs.csv`: complete input vintage, including pre-start warm-up history.
  This raw market-data export is local and Git-ignored; it is not published.
- `results/nav.csv`, `decisions.csv`, `calendar_returns.csv`: inspectable traces.
- `test_comparison.py`: 14 passing safety tests, including unchanged equity/gold,
  re-entry, warm-up, state at subwindow boundaries and write refusal.

A second run from the exported inputs reproduced both primary daily NAVs exactly
and the primary bootstrap results exactly. Tiny roundoff in a sensitivity
bootstrap can change a zero-delta resample's sign, not its economic conclusion.
