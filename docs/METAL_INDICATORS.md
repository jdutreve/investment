# The metal indicators, measured — gold, silver, copper and their comparators

Owner, 2026-11-08: "measure and compare". The digest prints a gold switch, a
silver and a copper switch, and the gold/silver and copper/gold ratios against
their averages (`mechanical/metals.py`), each with its customary reading. This
document asks whether those readings hold on the history this system has, and
which indicator, if any, carries something.

The protocol below was written and committed BEFORE any figure was computed,
and the study runs once. Its result is an entry in
`mechanical/research_findings.FINDINGS`, so the Worker reads it.

## Protocol (committed 2026-11-08, before the run)

**Data.** The live database, read-only, as of 2026-11-08: GLD (total-return
index, LBMA-spliced), GC=F, SI=F, HG=F (front-month futures, Yahoo), DFII5
(5-year TIPS yield, FRED, dated at its knowable date with a 5-day lag), SPY and
IEF (total-return), DGS10, ^IRX, and the two ratios as materialised
(`gold_silver_ratio` = GC=F/SI=F, `copper_gold_ratio` = 1000 × HG=F/GC=F). The
calendar is SPY's trading days; every series is forward-filled onto it from its
own knowable dates (ADR-003), never interpolated.

**Signals**, the state on day t, exactly as the digest computes them:

| id | ON when |
|---|---|
| GOLD_REAL | DFII5(t) < its mean over (t − 365 days, t] |
| GOLD_TREND | GLD(t) > its mean over its last 200 rows |
| GOLD_SWITCH | GOLD_REAL or GOLD_TREND |
| SILVER | SI=F(t) > its mean over its last 200 rows |
| COPPER | HG=F(t) > its mean over its last 200 rows |
| GS_UP | gold/silver(t) > its mean over (t − 365 days, t] |
| CG_UP | copper/gold(t) > its mean over (t − 365 days, t] |

**Window.** 2004-01-02 (the first date every signal can be read: DFII5 starts
2003-01-02 and needs a year) to the last date with a complete 12-week outcome.
Every hypothesis is tested on this one window, so they compare.

**Outcome.** Twelve weeks, the project's invariant horizon
(`proposal_outcome_weeks`): entry at the close of the trading day AFTER t (the
signal is read on t's close, so it cannot also trade on it), exit at the first
close at least 84 calendar days later.

**Sampling.** Non-overlapping: one observation every 12 weeks from the window's
start, so no two outcomes share a day (overlapping windows would multiply the
sample by 60 and its apparent significance with it). Robustness: the same grid
started 3, 6 and 9 weeks later.

**Hypotheses** — the customary readings, each with its claimed direction:

| # | signal | outcome (12 weeks) | claim |
|---|---|---|---|
| H1 | GOLD_SWITCH | GLD return | higher when ON |
| H2 | GOLD_REAL | GLD return | higher when ON |
| H3 | GOLD_TREND | GLD return | higher when ON |
| H4 | SILVER | SI=F return | higher when ON |
| H5 | COPPER | HG=F return | higher when ON |
| H6 | CG_UP | SPY return − IEF return | higher when ON (growth) |
| H7 | CG_UP | DGS10 change, points | higher when ON (yields rise) |
| H8 | GS_UP | SPY return − IEF return | lower when ON (defensive) |

**Statistic.** The difference of means, ON minus OFF, on the offset-0 grid; a
90% interval from 10,000 bootstrap resamples within each group (seed 0); the
one-sided bootstrap p in the claimed direction; the difference on each half
(2004-2014 and 2015-end); the difference on each of the four offsets; the share
of observations ON.

**Verdict, per hypothesis.**

- SUPPORTED iff the difference has the claimed sign, its 90% interval excludes
  zero, and the sign holds on both halves AND on all four offsets.
- CONTRADICTED iff the 90% interval lies entirely on the other side of zero.
- NOT SUPPORTED otherwise.

Eight hypotheses are tested, so one SUPPORTED by chance is to be expected
about one time in three at this bar; a one-sided p under 0.05/8 = 0.006 is
reported as the stricter, corrected reading.

**The comparison that matters for a holder: timing gold.** Daily, from the same
start: hold GLD on day t+1 when the signal was ON at t's close, else cash at
^IRX; 23 bp per change of position (ADR-010). For GOLD_SWITCH, GOLD_REAL and
GOLD_TREND against buy-and-hold GLD: CAGR, annualised volatility, Sortino
(daily, against ^IRX), maximum drawdown, number of changes. No verdict rule:
this is the price list of each choice, read beside H1-H3.

## Result

(to be written after the single run)
