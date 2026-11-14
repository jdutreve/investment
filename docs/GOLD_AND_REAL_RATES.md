# Gold and real rates: the level or the direction, measured

Owner, 2026-11-14. The corpus's claim "when real interest rates are negative,
gold outperforms the median asset class" (Faber, Dalio) stands at 40
confirmations against 29 refutations, for a null of 0.51. Half the refutations
come from one episode: gold lost 45% from September 2011 to December 2015
while the short real rate stayed negative throughout. In 2013 that rate was
negative but RISING, and gold fell 28%. The reading this suggests is that gold
answers the DIRECTION of real rates, not their level. The corpus holds no
claim on the direction. The owner: "yes, do it".

The owner also asked whether a condition active for several years should
count as one occurrence rather than as many twelve-week windows. Both
questions are legitimate and they are different ones: "while it holds, does
gold do better?" counts every window; "when it switches on, does gold do
better?" counts each episode once. A long episode must not pass for many
independent proofs, so the protocol below measures uncertainty BY EPISODE.

The protocol below was written and committed BEFORE any figure was computed,
and the study runs once. Its result is an entry in
`mechanical/research_findings.FINDINGS`, so the Worker reads it.

## Protocol (committed 2026-11-14, before the run)

**Data.** The live database, read-only, as of 2026-11-14: GLD, SPY and IEF
(total return, spliced back with their HISTORY_PROXIES), ^IRX, and the series
the tape already carries, each stored at its knowable date (ADR-003):
`real_yield_10y` (DGS10 − CPI year on year), `real_rate` (^IRX − CPI year on
year) and DFII5 (the 5-year TIPS yield, the market's own real rate). The
calendar is SPY's trading days; every series is forward-filled onto it from
its own knowable dates, never interpolated.

**Signals**, the state on day t:

| id | ON when |
|---|---|
| FALLING_RY | real_yield_10y(t) < real_yield_10y as known at t − 91 days |
| NEG_SHORT | real_rate(t) < 0 |
| FALLING_TIPS | DFII5(t) < DFII5 as known at t − 91 days |

A quarter's change for the direction, as in the dollar study's liquidity
signal; zero for the level, as the corpus's claim states it.

**Outcomes**, twelve weeks: entry at the close of the trading day AFTER t,
exit at the first close at least 84 calendar days later (the project's
invariant horizon). GLD's total return; and GLD's return minus the MEDIAN of
SPY's, IEF's and cash's returns over the same window, cash compounding ^IRX
daily. That second outcome is the corpus claim's own comparison, on gold
alone instead of the gold-and-commodities blend.

**Hypotheses**:

| # | signal | outcome (12 weeks) | claim |
|---|---|---|---|
| H1 | FALLING_RY | GLD return | higher when ON |
| H2 | FALLING_RY | GLD − median | higher when ON |
| H3 | NEG_SHORT | GLD − median | higher when ON (the corpus claim, gold alone) |
| H4 | NEG_SHORT | GLD return | higher when ON |
| H5 | FALLING_RY, only on dates where NEG_SHORT is ON | GLD − median | higher when ON (the 2013 reading) |
| H6 | FALLING_TIPS | GLD return | higher when ON |

**Windows.** H1–H5 from the first trading day on which `real_yield_10y` as
known 91 days earlier exists (its first stored row is 1991-12-13); H6 from the
same point for DFII5 (first row 2003-01-07). All to the last date with a
complete outcome. Each window is cut in two halves at its middle date.

**Sampling.** Non-overlapping: one observation every 60 trading days from the
window's start. Robustness: the same grid started 3, 6 and 9 weeks later.

**Episodes.** An episode is a maximal run of consecutive trading days in one
state (ON or OFF) of the hypothesis's signal; for H5, a run of FALLING_RY's
state within NEG_SHORT, a NEG_SHORT gap ending the run. Each observation
belongs to the episode containing its date t.

**Statistic.**

- The difference of means, ON minus OFF, on the offset-0 grid.
- Its 90% interval from 10,000 resamples (seed 0) BY EPISODE: ON episodes are
  drawn with replacement among the ON episodes that hold an observation, OFF
  episodes likewise, and each drawn episode brings all its observations. The
  one-sided p in the claimed direction is read from the same resamples.
- For comparison with the dollar study, the same interval with observations
  drawn one by one (how much wider the episode interval is, is itself the
  answer to the owner's second question).
- The EPISODE-WEIGHTED difference: the mean over ON episodes of each episode's
  own mean, minus the same over OFF episodes, so that every episode counts
  once whatever its length.
- The difference on each half and on each of the four offsets; the share of
  observations ON; the number of ON and OFF episodes.

**Verdict, per hypothesis.**

- SUPPORTED iff the difference has the claimed sign, its 90% episode interval
  excludes zero, and the sign holds on both halves, on all four offsets AND on
  the episode-weighted difference.
- CONTRADICTED iff the 90% episode interval lies entirely on the other side of
  zero.
- NOT SUPPORTED otherwise.

Six hypotheses are tested; a one-sided p under 0.05/6 = 0.008 is reported as
the stricter, corrected reading.

**Onsets, reported without a verdict.** For each signal of H1, H3 and H6, every
episode lasting at least 20 trading days: its first day s, and the outcome
GLD − median from the close after s. The mean over ON onsets minus the mean
over OFF onsets, and their counts. Onsets can overlap in time; this counts
each switch once and is what "count it as one occurrence" measures.

**The price list for a holder.** Daily, from each window's start to the last
SPY close: hold GLD on day t+1 when the signal was ON at t's close, else cash
at ^IRX; a position is held over (close t+1, close t+2] on the signal read at
close t; 23 bp per change of position (ADR-010).

- GLD while FALLING_RY, else cash — against GLD held.
- GLD while NEG_SHORT, else cash — against GLD held.
- GLD while FALLING_TIPS, else cash — against GLD held (from H6's start).

CAGR, annualised volatility, Sortino (daily, against ^IRX, annualised), maximum
drawdown, number of changes. No verdict rule: this is what each choice would
have cost or paid, read beside H1–H6.

## Result (run once, 2026-11-14)

No deviation from the protocol. 145 non-overlapping 12-week outcomes for
H1–H4 (1992-03-13 to 2026-07-10), 76 for H5 (the dates where the short real
rate is negative), 98 for H6 (2003-04-08 to 2026-05-27), offset 0. Intervals
are by episode; the observation-by-observation interval is shown beside it.

| # | signal → outcome | ON − OFF | 90% by episode | 90% by window | p | episode-weighted | halves | offsets 0/3/6/9 wk | ON | episodes ON/OFF | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | FALLING_RY → GLD | +2.54% | [+0.59, +4.59] | [+0.53, +4.58] | 0.015 | +2.70 | +1.90 / +3.11 | +2.54 −1.34 −0.79 −0.22 | 50% | 51 / 53 | not supported |
| H2 | FALLING_RY → GLD − median | +2.59% | [+0.58, +4.70] | [+0.56, +4.64] | 0.016 | +2.77 | +1.65 / +3.41 | +2.59 −1.36 −0.55 +0.36 | 50% | 51 / 53 | not supported |
| H3 | NEG_SHORT → GLD − median | +0.54% | [−3.22, +2.95] | [−1.56, +2.59] | 0.43 | +0.68 | +2.98 / −3.96 | +0.54 +1.17 −0.10 +0.35 | 52% | 13 / 10 | not supported |
| H4 | NEG_SHORT → GLD | −0.38% | [−3.78, +1.83] | [−2.46, +1.64] | 0.60 | +0.28 | +2.28 / −4.77 | −0.38 +0.47 −1.05 −0.62 | 52% | 13 / 10 | not supported |
| H5 | FALLING_RY within NEG_SHORT → GLD − median | +2.89% | [+0.41, +5.39] | [+0.24, +5.55] | 0.028 | +3.02 | +6.32 / +1.37 | +2.89 −1.59 −0.90 +1.09 | 54% | 30 / 29 | not supported |
| H6 | FALLING_TIPS → GLD | −1.93% | [−4.52, +0.38] | [−4.49, +0.60] | 0.91 | −3.23 | −3.59 / −0.44 | −1.93 +0.62 +2.44 +0.41 | 53% | 40 / 37 | not supported |

**Onsets** (episodes of at least 20 trading days, GLD − median, no verdict):
FALLING_RY 52 ON onsets at +0.60% against 53 OFF at +1.27% (−0.67);
NEG_SHORT 13 against 13, +3.49% against −0.41% (+3.90); FALLING_TIPS 32
against 38, +4.16% against +2.29% (+1.88).

**The price list**, daily to 2026-11-13, 23 bp a change:

| from | rule | CAGR | vol | Sortino | max drawdown | changes |
|---|---|---|---|---|---|---|
| 1992-03-13 | GLD held | +6.95% | 16.7% | 0.49 | −45.6% | 0 |
| 1992-03-13 | GLD while FALLING_RY, else cash | +2.02% | 12.0% | 0.03 | −40.8% | 339 |
| 1992-03-13 | GLD while NEG_SHORT, else cash | +4.43% | 12.2% | 0.30 | −43.4% | 50 |
| 2003-04-08 | GLD held | +10.72% | 18.1% | 0.79 | −45.6% | 0 |
| 2003-04-08 | GLD while FALLING_TIPS, else cash | +4.00% | 12.7% | 0.33 | −46.8% | 282 |

**Today** (as knowable on 2026-11-14): `real_yield_10y` 1.38 against 1.33 a
quarter earlier — FALLING_RY is OFF; `real_rate` +0.40 — NEG_SHORT is OFF;
DFII5 2.35 against 2.13 — FALLING_TIPS is OFF.

**What it says.**

- **The direction does not time gold either.** The first grid showed +2.5
  points with an interval clear of zero; the same grid started three, six and
  nine weeks later showed the opposite sign three times out of three. That is
  where the grid happened to fall, not a property of gold, and the protocol's
  offsets exist to catch exactly this. The onsets agree (−0.67), and holding
  gold only while the real yield fell earned 2.02% a year against 6.95% held.
  The market's own real yield (TIPS, 2003–2026) leaned the wrong way. The
  2011–2015 episode suggested a rule; 34 years do not contain it.
- **The level does not either, and counting by episode shows why its record
  looked better than it is.** Negative short real rates came in 13 episodes;
  the windows inside them are not 13 × several independent proofs. The episode
  interval is half again as wide as the window one ([−3.22, +2.95] against
  [−1.56, +2.59]), the halves disagree in sign (+2.98 then −3.96). The corpus's
  40/69 counts many windows of a few long episodes. For a signal that changes
  state often (FALLING_RY, about 100 episodes for 145 windows) the two
  intervals coincide, which is the expected behaviour: episode counting costs
  nothing where windows are already independent.
- **The one reading worth keeping is the switch of the level, not its state**:
  the 13 times the short real rate turned negative, gold beat the median by
  3.9 points more than the 13 times it turned positive. Thirteen against
  thirteen, with no interval and no verdict; it is a lead, not a finding.
- **Consequence for the Worker:** neither the level nor the recent direction
  of real rates is a reason for more or less gold over a quarter
  (`research_findings.FINDINGS`, 2026-11-14). The common reading, that gold
  moves inversely to real yields, is about moves in the SAME period; whether a
  past move predicts the next quarter is what this measured, and it does not.
