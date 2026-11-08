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

## Result (run once, 2026-11-08)

**One deviation from the protocol, made before any figure was seen.** The
window starts 2004-01-07, not 2004-01-02: DFII5's first row is dated
2003-01-07 (its 5-day lag), so GOLD_REAL cannot be read for a full year before
the 7th, and the protocol's "first date every signal can be read" was a wrong
statement of that date. The timing comparison holds a position over
(close t+1, close t+2] on the signal read at close t — the stricter reading of
"hold on day t+1", and the same one-day delay as the outcome's entry.

99 non-overlapping 12-week outcomes, 2004-01-07 to 2026-07-22 (offset 0).

| # | signal → outcome | ON − OFF [90%] | p | halves | offsets 0/3/6/9 wk | ON | verdict |
|---|---|---|---|---|---|---|---|
| H1 | GOLD_SWITCH → GLD | −0.64% [−3.80, +2.28] | 0.63 | −0.59 / −0.70 | −0.64 +0.31 +0.89 +0.13 | 77% | not supported |
| H2 | GOLD_REAL → GLD | −0.59% [−3.29, +2.18] | 0.65 | −2.45 / +1.15 | −0.59 −0.61 −1.51 +0.76 | 51% | not supported |
| H3 | GOLD_TREND → GLD | +0.86% [−2.05, +3.66] | 0.31 | +1.42 / +0.36 | +0.86 +2.21 +1.49 +0.39 | 69% | not supported |
| H4 | SILVER → SI=F | +1.08% [−3.96, +6.10] | 0.36 | +5.16 / −2.76 | +1.08 +3.52 −4.04 +0.09 | 59% | not supported |
| H5 | COPPER → HG=F | +0.02% [−4.30, +4.39] | 0.50 | +1.40 / −1.33 | +0.02 −1.05 −0.13 −0.36 | 56% | not supported |
| H6 | CG_UP → SPY − IEF | +2.07% [−0.28, +4.50] | 0.07 | +3.65 / +2.07 | +2.07 −0.52 +1.53 +2.36 | 40% | not supported |
| H7 | CG_UP → DGS10 change | +0.057 pt [−0.076, +0.190] | 0.24 | +0.081 / +0.113 | +0.057 +0.018 +0.043 +0.181 | 40% | not supported |
| H8 | GS_UP → SPY − IEF (claim lower) | −1.49% [−4.08, +0.97] | 0.16 | −0.39 / −3.22 | −1.49 −2.33 −1.05 −0.84 | 55% | not supported |

**Timing gold**, daily, 2004-01-07 to 2026-11-06, cash at ^IRX, 23 bp a change:

| | CAGR | vol | Sortino | max drawdown | changes |
|---|---|---|---|---|---|
| buy-and-hold GLD | +9.96% | 18.2% | 0.73 | −45.6% | 0 |
| GOLD_SWITCH | +4.84% | 15.6% | 0.37 | −45.4% | 158 |
| GOLD_REAL | +5.82% | 13.2% | 0.50 | −35.8% | 122 |
| GOLD_TREND | +4.92% | 15.0% | 0.38 | −43.2% | 183 |

**What it says.**

- **No reading holds.** None of the eight clears the protocol's bar; none
  approaches the corrected one (p < 0.006). None is contradicted either: the
  metals' states say nothing measurable about the next twelve weeks, in either
  direction.
- **The gold switch is worse than useless as a timing rule.** Holding GLD only
  while it is ON halved the return (4.84% against 9.96% a year) and left the
  drawdown where it was (−45.4% against −45.6%): it is ON 77% of the time, so it
  is out mostly in the rallies that follow a dip below the line. The real-yield
  leg alone is the least bad — it cut the drawdown to −35.8% — at the cost of
  four points of return a year.
- **The one lead is copper/gold on equities over bonds**: +2.07% per twelve
  weeks when the ratio sits above its 12-month average, same sign on both
  halves, p = 0.07, one offset of four the other way. It is not a claim; it is
  the only question here worth asking sharper — for instance as an invariant on
  `copper_gold` against a protocol of its own, since the confrontation engine
  samples condition-active time rather than a fixed grid.
- **Consequence for the digest and the Worker:** the block stays, as a
  description of the present, and says it is measured and is not a forecast
  (`research_findings.FINDINGS`, 2026-11-08).

## Second study: copper/gold, sharper (protocol committed 2026-11-08, before the run)

Owner, the same day: "dig — I hold gold today; should I sell it for copper?"
Searching the 2004-2026 data again for a version of H6 that clears the bar
would manufacture one. So the lead is put to data the first study never saw,
and the owner's own question is put as a hypothesis of its own.

**Out-of-sample data, 1992-2003.** Copper: FRED `PCOPPUSDM` (IMF, monthly
average, USD per tonne, from 1992-01). Its first releases come a median 43 days
after the month starts and sometimes months later, so a month's value is taken
as knowable on the first day of the month THREE months later (a January average
is used from April 1st) — conservative, and cheap for a 12-month average.
Gold: GLD's daily series (LBMA-spliced) averaged over the same calendar month.
The ratio copper/gold is the month's copper average over the month's gold
average; its scale does not matter, since it is read against its own average.
CG_UP_M: the ratio above its mean over the 12 months up to it, read from its
knowable date and held until the next one.

**Hypotheses.**

| # | window | signal | outcome (12 weeks, entry the next trading day) | claim |
|---|---|---|---|---|
| Q1 | 1993-2003, signal dates | CG_UP_M | SPY return − IEF return | higher when ON (H6 replicated out of sample) |
| Q2 | 2004-2026 (the first study's window and grid) | CG_UP | HG=F return − GC=F return | higher when ON (the ratio's momentum: copper keeps beating gold) |
| Q3 | 1993-2003 | CG_UP_M | copper return − gold return, monthly averages: the average of the month 3 months after entry over the average of the entry month | higher when ON |

Non-overlapping 12-week grids from each window's first readable date, as in the
first study; four offsets; halves (1993-1998 / 1999-2003 for Q1 and Q3, the
first study's halves for Q2); the same bootstrap and the same verdict rule.
Q1 is the test of the lead: SUPPORTED on 1993-2003 would make it worth an
invariant; anything else closes it. Q2 and Q3 answer the owner's question.

**Context, no verdict:** copper against gold held outright over 2004-2026 and
since 2020 (futures, CAGR and drawdown of each).

### Result of the second study (run once, 2026-11-08)

| # | window | outcome | ON − OFF [90%] | p | halves | offsets 0/3/6/9 wk | n, ON | verdict |
|---|---|---|---|---|---|---|---|---|
| Q1 | 1993-2003 | SPY − IEF | +1.75% [−2.13, +5.48] | 0.22 | +1.41 / +0.75 | +1.75 +0.43 −1.12 +1.28 | 48, 38% | not supported |
| Q2 | 2004-2026 | HG=F − GC=F | +2.57% [−1.62, +6.83] | 0.16 | +1.29 / +4.08 | +2.57 −4.42 −1.38 +2.62 | 99, 40% | not supported |
| Q3 | 1993-2003 | copper − gold | −4.60% [−9.97, +0.71] | 0.92 | −0.84 / −9.03 | −4.60 −2.34 −3.00 −1.76 | 48, 38% | not supported |

Context, held outright (front-month futures): from 2004-01-07 gold +10.44% a
year with a −44.4% worst drawdown, copper +8.06% with −69.4%; from 2020-01-02
gold +15.45% (−26.7%), copper +12.22% (−34.9%).

**What it says.**

- **The lead is closed.** Out of sample, copper/gold above its average points
  the same way for equities over bonds (+1.75%) but at a third of the
  conviction, its interval wide across zero and one offset of four reversed:
  the 2004-2026 figure was not a property the earlier decade shares. As the
  protocol set, it does not become an invariant.
- **A rising copper/gold ratio does not say copper will keep beating gold.**
  2004-2026: +2.57%, two offsets of four the other way. 1993-2003: the opposite
  sign on every offset (−4.60%), short of significance — if anything the ratio
  reverted.
- **For a holder choosing between the two metals**, neither period gives copper
  the edge: gold returned more, with a drawdown a third shallower over 2004-2026.
  Copper is in none of the stack's books and the system measures nothing that
  would put it there.

## Third study: is the copper/gold cycle tradable read without hindsight? (protocol committed 2026-11-08, before the run)

Owner, the same day, with a chart of the ratio: "yet the ratio looks cyclical".
It does: a secular decline inside a falling channel, with swings of a few
years. The first two studies tested MOMENTUM (above its 12-month average, does
it continue?) over 12 weeks; a cycle implies the opposite, REVERSION from the
extremes, over months. The channel on a chart is drawn through points that
were not known at the time, so the extreme is defined here from the past alone.

**Data, one monthly series for the whole span.** Copper: FRED `PCOPPUSDM` (IMF
monthly average, 1992-01 to its last print), knowable on the first day of the
month three months later, as in the second study. Gold: GLD's daily series
averaged over the calendar month. Ratio r(M) = copper(M) / gold(M).

**Signal.** z(M) = (ln r(M) − mean of ln r over the 60 months up to M) / the
standard deviation over the same 60 months (a full 60 required). Read at a
decision date D (the first of each month) as the latest z knowable at D.
CHEAP: z < −1 (copper cheap against gold, the bottom of the channel). RICH:
z > +1. The rest: neither.

**Outcome.** Copper minus gold over h months from D: copper(month of D + h) /
copper(month of D) − 1, minus the same for gold, on monthly averages. h = 12
and h = 6.

**Sampling.** Non-overlapping: one decision every h months from the first
readable date, while the outcome's last month has a print. Robustness: the same
grid started 1, 2 and 3 months later. Halves: decisions before 2012-01-01 and
from it.

**Hypotheses.**

| # | group | outcome | claim |
|---|---|---|---|
| K1 | CHEAP vs the rest | copper − gold, 12 months | higher (copper rebounds) |
| K2 | CHEAP vs the rest | copper − gold, 6 months | higher |
| K3 | RICH vs the rest | copper − gold, 12 months | lower (copper gives back) |
| K4 | RICH vs the rest | copper − gold, 6 months | lower |
| K5 | z, all decisions | rank correlation with copper − gold, 12 months | negative |

**Statistic and verdict.** As in the first two studies: difference of means
(K5: Spearman correlation), 90% bootstrap interval (10,000 resamples, seed 0),
one-sided p, halves, four offsets; SUPPORTED iff the claimed sign, an interval
excluding zero, and the same sign on both halves and all four offsets;
CONTRADICTED iff the interval lies entirely on the other side; NOT SUPPORTED
otherwise. A group with fewer than 5 observations on the main grid gives
TOO FEW, not a verdict: about thirty years hold five or six cycles, and the
tails are where they are thin.

**Context, no verdict:** z today, from the latest knowable copper print, and
from the daily futures (GC=F, HG=F) on the same 60-month definition without the
publication lag — where the ratio sits now on this scale.
