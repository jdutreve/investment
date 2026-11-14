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

## Before the run: the guard stopped it, and what that found

The first attempt stopped at the guard: at placebo zero the bench did not
reproduce the stored records of 142 of the 272 measurable invariants (by 1 to
8 confirmations each). The bench's own tests still passed, so its machinery
was still production's. The backups dated the break: no mismatch on
2026-10-06, 5 on 2026-10-11, 104 on 2026-10-18. Between those dates, commit
2865843 (2026-10-17) repaired how year-on-year changes are read: CPI and
industrial production had been misread. The seed repaired the series, but
the maturation fingerprint knows what was asked, not what it was measured
on. The records of every invariant reading inflation (49 of 63), growth
(19 of 20), the short real rate (29 of 39) or the 10-year real yield (10 of
12) therefore stayed measured on the
misread series for four weeks.

With the owner's agreement, every invariant was re-measured on the current
data (`mature_seed_invariants(remeasure_on_changed_data=True)`, agent
stopped, backup `investment.db.bak-pre-remeasure-20261114`). The records of
142 invariants changed, their weights by 0.013 on average and 0.12 at most,
and nine statuses moved. One invariant is integrated, the first since
October: Dalio, "a central bank cutting while the economy is already
accelerating fuels an equity rally", 46/69 against a null of 0.48. The guard
then passed (0 mismatches) and the study ran once, unchanged.

## Result (run once, 2026-11-14)

No deviation from the protocol. 272 measurable invariants, 271 in E1 (one is
unconditional), 264 in E2 (with at least one decided moment at shift zero).

**E1 — false integrations on the run-permutation placebo** (share of 400
lives in which a claim that knows nothing is integrated at least once):

| group | n | mean A | mean B | worst A | worst B | above 7.2% A / B |
|---|---|---|---|---|---|---|
| ON < 10% of days | 115 | 0.03% | 0.00% | 1.25% | 0.25% | 0 / 0 |
| ON 10–30% | 129 | 0.21% | 0.12% | 2.75% | 1.75% | 0 / 0 |
| ON > 30% | 27 | 0.41% | 0.10% | 7.25% | 2.00% | 1 / 0 |
| October dispersion < 1 | 140 | 0.05% | 0.03% | 2.50% | 2.00% | 0 / 0 |
| October dispersion 1–1.5 | 34 | 0.17% | 0.08% | 2.00% | 1.50% | 0 / 0 |
| October dispersion > 1.5 | 46 | 0.53% | 0.18% | 7.25% | 1.50% | 1 / 0 |
| all | 271 | 0.15% | 0.07% | 7.25% | 2.00% | 1 / 0 |

The one record above 7.2% under A is `inv-low-real-yields-favor-gold` (10-year
real yield under 2.5 → GLD; ON 36% of days in 31 episodes, October dispersion
2.93): 7.25% under A, 0.50% under B. The negative-real-rate claim on
gold-commodities: 2.75% under A, 0.00% under B.

**E2 — what B costs** (share of lives ending integrated, true claims):

| group | n | 0.65 strong A / B | 0.65 independent A / B | 0.70 strong A / B | 0.70 independent A / B |
|---|---|---|---|---|---|
| ON < 10% | 108 | 4.0% / 1.2% | 1.6% / 1.2% | 6.4% / 2.8% | 3.4% / 2.7% |
| ON 10–30% | 129 | 39.0% / 19.5% | 30.5% / 20.9% | 58.9% / 36.6% | 55.2% / 39.1% |
| ON > 30% | 27 | 62.0% / 24.6% | 64.0% / 30.1% | 76.8% / 37.6% | 88.5% / 45.2% |
| all | 264 | 27.1% / 12.6% | 22.1% / 13.8% | 39.3% / 22.9% | 37.4% / 24.8% |

B keeps 0.58 of A's power on a true 0.70 claim under the strongest dependence
(0.49 on the dense group).

**E3 — today's corpus under B.** A reproduces every stored status (272 of
272). B: integrated 1 (the same), proposed 264, rejected 7, against A's 1, 261
and 10. Seven statuses differ, all at the rejection bar: five rejected under
A are proposed under B (among them "nominal growth above nominal rates in a
deleveraging → equities", 39/84 over 10 episodes, and "accelerating
delinquencies → equities underperform", 42/91 over 44), two proposed under A
are rejected under B. The median record has 1.3 decided moments per episode:
for most invariants a window already is an episode.

**Decision, by the rule fixed in advance: A stays.** B holds 5% in every group
and keeps more than half the power, but A does not break where the October
placebo could not look. Its mean on the dense conditions is 0.41%, and one of
27 records sits above 7.2%, which is 3.7% of the group against the 5% the rule
required. The October conclusion now extends to dense conditions, measured
rather than floored: the checkpoint bars are conservative enough to absorb
the dependence of long episodes.

**What it says.**

- **The owner's intuition is right about the evidence and the verdict already
  allows for it.** Dependence is real and lives where expected: the dense, highly
  dispersed records carry ten times the false-integration rate of the rare ones,
  and counting by episode removes most of it. But that rate starts from
  0.4%, an order of magnitude under the 5% the rule promises, because the bars
  were set for a whole life of looks. Episode counting would buy a guarantee the
  verdict already keeps, at the price of about half its power to recognise a
  true claim.
- **One record is the exception to watch:** the 10-year real-yield gold claim,
  7.25% under A. It is `proposed` (54/83); were it ever integrated, its record
  would be the one most likely to be a run of a few long episodes.
- **The re-measurement mattered more than the rule.** For four weeks half the
  corpus was judged on a misread inflation series, and nothing said so. The
  fingerprint that decides "already measured" ignores the data, so any repair of
  a series leaves stale records silently. It happened on 2026-10-04 (DJP) and
  2026-10-17 (year-on-year).
- **Still the owner's, and not decided by this result:** showing each record
  with its count of episodes ("54/83 over 16 episodes").
