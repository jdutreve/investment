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

### Result of the third study (run once, 2026-11-08)

| # | test | statistic [90%] | p | halves | offsets 0/1/2/3 mo | group n | verdict |
|---|---|---|---|---|---|---|---|
| K1 | CHEAP vs rest, copper − gold, 12 mo | +25.4% [+1.2, +50.1] | 0.043 | +47.3 / −2.9 | +25.4 +28.3 +27.3 +34.3 | 7 | not supported (second half) |
| K2 | CHEAP vs rest, copper − gold, 6 mo | +17.1% [+6.7, +28.0] | 0.002 | +21.8 / +11.7 | +17.1 +13.8 +6.3 +10.5 | 17 | **SUPPORTED** |
| K3 | RICH vs rest, 12 mo (claim lower) | −3.4% [−19.1, +13.0] | 0.36 | −6.0 / −7.8 | −3.4 +14.9 +34.2 +12.6 | 5 | not supported |
| K4 | RICH vs rest, 6 mo (claim lower) | −1.5% [−12.0, +9.5] | 0.40 | −2.1 / −4.3 | −1.5 +3.6 +11.3 +3.0 | 11 | not supported |
| K5 | Spearman(z, copper − gold 12 mo) | −0.25 [−0.55, +0.11] | 0.13 | −0.38 / −0.12 | −0.25 −0.22 −0.35 −0.34 | 29 | not supported |

Main grid 1997-03 to 2025-03 (12 months, n = 29) and 1997-03 to 2026-03 (6
months, n = 59).

**What it says.**

- **The cycle holds on one side, and it is the cheap side.** When copper sits
  more than one standard deviation below its 5-year relation to gold, it beat
  gold by 17 points over the next six months, on both halves and every offset,
  p = 0.002 — under the correction for the five tests here (0.01) and for all
  sixteen of the three studies (0.003). Over twelve months the gain is larger
  but rests on the first half alone.
- **The rich side says nothing.** Copper expensive against gold did not give
  back measurably, at six or twelve months.
- **Its evidence is about seven episodes, not seventeen observations.** The
  cheap state came in runs — 1998-99, 2001-03, 2008-09, 2011-12, 2015-16,
  2019-20, 2024-26 — and the gain is carried by the rebounds out of 2003, 2009,
  2016 and 2020, against failures in 2002, 2015, 2019 and 2025. A real
  regularity, thinly evidenced; a reason to watch, not a rule to trade.
- **Where it stands today:** not cheap. The last episode ran from 2024-08 to
  2026-05 (IMF months); z is −0.71 on the latest knowable print (2026-08) and
  −0.45 on the futures for November. The rebound the owner's chart shows is the
  one the episode foretold, and it has happened.
- **Copper is not a handle this system can hold or confront** (no copper
  sleeve, no asset-class benchmark), so this stays a finding and a line of the
  digest, not an invariant.

## Fourth study: the owner's channel rule — trade the band touches, nothing between (protocol committed 2026-11-08, before the run)

Owner, the same day, with a monthly chart of copper/gold since 2002 and its
falling channel: "I only play the touches of the channel's bands, nothing in
between." The rule: hold gold; when copper/gold touches the LOWER band, switch
to copper; when it touches the UPPER band, switch back to gold.

**Data.** Daily R = 1000 × HG=F / GC=F (front-month futures, 2000-08-30 to
2026-11-06), on the days both print; returns are the futures' daily changes
(a roll shows as a small step in each).

**Two channels.**

- **A, as drawn (hindsight, an upper bound).** An ordinary least-squares line
  through ln R over the whole span; the bands are that line shifted to the
  lowest and the highest residual. Every band touch in A is known only at the
  end, so A says what the drawn channel would have paid at best, nothing more.
- **B, without hindsight (what the rule could have done).** On the first
  trading day of each month, the same fit over the ten years before it (the
  first channel: 2010-09); bands at that window's lowest and highest residual,
  extended through the month along the fitted slope.

**Touch.** ln R(t) within 0.02 (about 2%) of a band, or beyond it, at day t's
close. **Trade:** the switch is made at day t+1's close; 23 bp per order, two
orders a switch (ADR-010). Start in gold on each channel's first day.

**Reported, no significance claimed** (a handful of round trips): every
completed trade's copper-minus-gold return and its dates; for each channel,
CAGR, worst drawdown and switches, against gold held and copper held over the
same span; where R sits today against B's bands.

**Reading, fixed now.** The rule is CONSISTENT with the history if, on B, most
completed copper trades beat gold AND the strategy's CAGR beats gold held;
otherwise NOT SHOWN. A alone can never make it consistent.

### Result of the fourth study (run once, 2026-11-08)

| channel | copper trades (copper vs gold) | rule CAGR | worst drawdown | switches | gold held | copper held |
|---|---|---|---|---|---|---|
| A, as drawn (2000-2026, hindsight) | 2009-02 → still open: +6.1% | +11.09% | −58.0% | 1 | +10.86% (−44.4%) | +7.74% (−69.4%) |
| B, rebuilt monthly from the past 10 years (2010-09 to 2026-11) | 2020-03-24 → 2021-02-25: **+81.5%**; 2025-09-08 → still open: **+25.4%** | **+13.11%** | −44.4% | 3 | +7.60% (−44.4%) | +3.69% (−58.0%) |

Today on B: R = 1.531, lower band 0.976, upper band 2.134 — 58% of the way up.

**What it says.**

- **On B, the rule reads CONSISTENT with the history**, by the reading fixed
  before the run: its one completed copper trade beat gold (+81.5%, 2020-21), the
  open one is ahead (+25.4% since 2025-09), and it compounded 13.1% a year
  against 7.6% for gold held, with the same worst drawdown.
- **On one completed trade and one open.** Consistent is all it can be: two
  entries in sixteen years cannot separate a skill from a pair of good draws,
  and the rebuilt channel did not fire at the 2016 low the owner's chart marks.
- **A is not the owner's drawn channel.** A least-squares fit over 2000-2026
  sets its bands at the 2006-08 boom and the 2008 crash, which the owner's lines
  leave outside; it touched once (2009) and never reached its upper band again.
  It is reported as specified, and says little about the owner's lines.
- **Where the rule stands today on B: in copper since 2025-09, exit at the upper
  band, about 2.13** — +39% from today's ratio, the owner's own target. For a
  holder of gold today, buying copper now is NOT the rule: the ratio sits
  between the bands, where it does nothing.

## Fifth study: follow the copper/gold momentum — what CAGR? (protocol committed 2026-11-08, before the run)

Owner: "no — follow the momentum. What CAGR does it give?" Hold copper while
the ratio's momentum is up, gold otherwise. Four variants fixed now, ALL
reported, none chosen after the figures:

| id | copper while | 
|---|---|
| M1 | R above its 200-session average |
| M2 | R above its 252-session (12-month) average |
| M3 | R's 126-session (6-month) change above zero |
| M4 | entered when the cycle reads cheap (z < −1, third study, futures monthly averages), held while R stays above its 200-session average; gold otherwise |

Daily R = 1000 × HG=F / GC=F, 2000-08-30 to 2026-11-06; signal at day t's
close, switch at t+1's close, 2 × 23 bp a switch; futures daily returns. For
each: CAGR, worst drawdown, switches, over the whole span and over 2001-2013
and 2014-2026 (each variant starts once its signal can be read), against gold
held and copper held over the same span. **Reading:** a variant is
CONSISTENT if its CAGR beats gold held on the whole span AND on both halves;
NOT SHOWN otherwise.
