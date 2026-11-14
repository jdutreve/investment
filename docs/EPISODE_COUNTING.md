# Counting by episode: what the October placebo could not see

Owner, 2026-11-14: should a condition active for several years count as one
occurrence rather than as many twelve-week windows? The gold study of the same
day (`docs/GOLD_AND_REAL_RATES.md`) showed why the question matters. The
corpus's "negative real rates favour gold" stands at 40/69, but the condition
came in 13 episodes only, and its interval drawn by episode is half again as
wide as the one drawn window by window.

## What is already measured, and the gap it leaves

The question was half answered on 2026-10-06 (`docs/INVARIANT_TASKS.md`, lot 4,
3.2; bench `docs/research/2026-10-05-verdict-calibration/`, second part). Each
invariant's own condition was shifted along its calendar 400 times, so its
episodes kept their shape and lost their link to what followed.

- The verdict was found to hold its guarantee despite dependence: a claim that
  knows nothing is integrated at least once over a life 0.21% of the time on
  average, 0.79% on the 46 most dependent records, and one invariant of 242
  reached 5.75%, inside the noise. Decision: the verdict is not corrected.
- The displayed range still assumes independent moments. The estimator tried
  for it (the design effect of calendar blocks) did not track the placebo.

The gap is a limitation that protocol wrote down: **a circular shift barely
moves a dense condition.** A condition ON half the time, in a few long
episodes, lands after a shift almost where it was. Its placebo is therefore a
floor, not an estimate, and it is a floor exactly for the records the owner's
question is about. "The verdict holds" was measured least well where
dependence is strongest.

This protocol fills that gap with a placebo that moves every episode, and
measures, on the same placebo, a verdict that counts episodes.

The protocol below is written and committed BEFORE any figure is computed,
and the study runs once. Its result is an entry in
`mechanical/research_findings.FINDINGS`. Research only: one read-only SQLite
transaction on the live database, nothing in production changed by the run.

## Protocol (committed 2026-11-14, before the run)

**Data and machinery.** The live database, read-only, as of the run. The
measurable invariants (status `proposed`, `integrated` or `rejected`, with a
condition and an effect that pass `validate_invariant`). Moments, outcomes,
margins, nulls and labels are read with the October bench's code, which was
tested equal to production's; at placebo zero it must reproduce each
invariant's stored birth confrontations, and the run stops if it does not.

**The placebo: run permutation.** For each invariant, its condition's daily
state over its sample is a sequence of alternating runs: ON runs and OFF runs.
A placebo life keeps the ON runs and the OFF runs, with their lengths, and
draws a new order for each set independently, keeping the alternation and
which state starts. The share of days ON, the number of episodes and every
episode's length are preserved. Every episode moves, a dense condition's
included, and its link to dates is gone. 400 lives per invariant, seed 0.
Outcomes are the real every-date labels of the invariant's protocol, read at
the placebo's moments, so the outcomes keep their own autocorrelation.

**The two verdict rules.**

- **A — in force.** One vote per decided moment; checkpoints every 10 votes in
  date order up to 320; `checkpoint_bars` and `rejection_bars` at α = 5% over
  a life, θ = 0.60, against the invariant's measured null.
- **B — by episode, fixed here.** One vote per EPISODE of the condition that
  holds at least one decided moment: confirmed if more than half its decided
  moments are confirmations, refuted if fewer than half, dropped if exactly
  half. Checkpoints every 10 episode votes in date order; the same bars, the
  same α, θ and null. B is A applied to episodes instead of windows; no new
  parameter.

**E1 — false integrations.** Per invariant, the share of placebo lives in
which the claim is integrated at least once, under A and under B. Reported
by share of days ON (under 10%, 10–30%, over 30%), by October placebo
dispersion (under 1, 1 to 1.5, over 1.5), the mean, the worst case, and the
count of invariants above 7.2% (5% plus two standard errors at 400 lives).
`inv-low-real-yields-favor-gold` and the negative-real-rate claim on
gold-commodities are reported by name.

**E2 — what B costs.** On each invariant's real episode structure (its
moments and the episode each belongs to), true claims simulated at a rate of
0.65 and 0.70: 400 lives each, every episode confirmed as a whole with that
probability and its moments taking the episode's outcome, which is the
dependence at its strongest; and again with moments drawn independently,
which is A's best case. Reported: the share integrated by the end of the
sample under A and under B, by the same groups.

**E3 — today's corpus under B.** The stored confrontations regrouped by
episode: the number of invariants integrated, proposed and rejected under A
and under B, and the list of those whose status differs.

**Decision rule.**

- **B replaces A for the verdict** iff E1 shows A breaking its guarantee where
  the October placebo could not look — a mean above 5% in the over-30% group,
  or more than 5% of that group's invariants above 7.2% — AND B holds 5% in
  every group, AND under E2's strongest dependence B integrates a true 0.70
  claim at least half as often as A. That would be an amendment to ADR-006,
  written with the figures.
- **A stays** if it holds on E1 too: the October conclusion then extends to
  dense conditions, measured this time and no longer a floor.
- **Any other outcome** (A breaks and B does not fix it, or B costs more than
  half the power) is written down as found, and the verdict stays as it is
  until a rule passes this bench.

**The display, independent of the result.** Each record shown to the Worker
and in the digest would carry its count of episodes beside its count of
moments ("40/69 over 13 episodes"). That is a fact about the record, not an
estimate, so it needs no validation; it is the owner's to accept or refuse,
and is listed here so that the result does not decide it.

## Known limitations

- A run permutation places episodes in eras they never occurred in. The handle's
  ordinary excess differs by era (measured 2026-10-10: gold −1.9% over
  1991–2002, +1.1% since), so a placebo episode can inherit an era's drift
  that the real one did not. Reported as found, not corrected.
- The outcome labels are judged against the whole-sample median, as in
  production (owner decision D1).
- B's majority vote discards the strength of the evidence inside an episode:
  9 confirmations out of 10 count as 1, like 6 out of 10.
- E2's "an episode is confirmed as a whole" is the extreme of dependence, and
  independent moments the other extreme. The truth lies between, and E2 reports
  both bounds rather than one estimate.
