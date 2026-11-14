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
