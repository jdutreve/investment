# Invariant audit brief — valuation and lifecycle

A self-contained brief for an external auditor (human or model) of how
invariants and reference notes are born, valued, judged and consumed.

**Scope.** Repository `~/projects/investment`, SQLite database
`~/data/investment/investment.db` (read-only for an auditor: the agent is the
sole writer, ADR-004). Core code: `src/investment/mechanical/invariants.py`.
Figures were read from the live database on 2026-10-03.

## 1. Two objects in one table

The `invariant` table holds two things, told apart by `status`:

- **Measurable invariant** — a `condition` (a list of ANDed predicates on market
  signals: `signal`, `feature` among level/speed/acceleration, `op`, `value`)
  and an `effect` (`handle`, `metric`, `method`, `direction`). Statuses
  `proposed`, `integrated`, `rejected`. 250 rows.
- **Reference note** — no condition and no effect. Status `reference`,
  terminal. 1,396 rows.

An EMPTY condition WITH an effect is not a note: it is an unconditional claim
("always"), measured absolutely (`is_absolute_claim`).

## 2. Birth

Three paths create rows:

- **Seed** (`seed.py`) — the initial invariants.
- **Curator** (`writeback/knowledge.py`) — an LLM extracts candidates and notes
  from a document's passages.
- **Worker** (`writeback/writeback.py`, `_commit_invariant_innovation`) —
  innovations proposed during the decision cycle.

Rules common to every birth:

- **Starting weight.** The model proposes `weight_initial`; the author tier's
  band binds it (`AuthorBand.bind`). Bands live in `invariant_author_config`:
  dalio 0.80-0.90, marks 0.75-0.85, other 0.40-0.70, system 0.15-0.25. Floor
  0.05 for every tier. A reference note is born at the bottom of its band.
- **Dedup gate** (`find_duplicate`). Exact structural identity first, with no
  similarity threshold; otherwise cosine >= 0.80 AND same effect AND conditions
  not provably disjoint. A duplicate merges INTO the incumbent, which keeps its
  history. A reference note is never a merge target.
- **Validation** (`validate_invariant`). A malformed condition or effect
  demotes the row to a reference note; it never raises.
- **The author's declared status is ignored.** `_force_uncertified` resets
  status, counts and score to their defaults for anything the engine did not
  measure.

## 3. Birth maturation (`_mature_one`)

Each measurable invariant is tested once over roughly 35 years:

1. The condition is evaluated day by day, point-in-time (ADR-003).
2. `sample_moments` takes an active day, skips one 12-week horizon, repeats —
   so the outcome windows are disjoint.
3. At each moment the handle's EXCESS is measured over the following 12 weeks:
   against the median of the other classes or strategies, or against 0 when
   `method = absolute`.
4. That excess is compared to the **baseline**: the median of the same excess
   over ALL dates, condition ignored. A confirmation means "better than usual",
   not "the effect occurred". The null score is therefore 0.50 for every
   handle.
5. A per-metric margin defines a no-op band: neither confirmation nor
   infirmation.
6. Confrontations are written to `invariant_confrontations` with
   `source = 'backtest'`.

A fingerprint of (condition, effect, verdict rule) is stamped into `trace`. If
the definition or a threshold changes, the invariant is re-matured and its
`backtest` confrontations are replaced.

## 4. Valuation

```
market_score     = confirmations / (confirmations + infirmations)   # 1.0 before any confrontation
weight_effective = max((weight_initial × 4 + confirmations) / (4 + N), floor_weight)   # N = confirmations + infirmations
```

`weight_initial` is a PRIOR worth 4 confrontations
(`invariants.PRIOR_CONFRONTATIONS`): unmeasured, the weight is `weight_initial`;
measured, it converges on the record, above or below where it started. Until
2026-10-03 the formula was `weight_initial × market_score`, which made the
starting weight a ceiling.

No date enters the weight since the same day: `recency_factor` was removed
(owner decision, "an invariant is timeless").

`invariants.restate_invariant` is the single writer of an invariant's standing
after birth: it writes score, weight, verdict and `validated_at` together.

## 5. Verdict (`time_validation_verdict`)

Recomputed from the counts, stateless, checked in this order:

| Verdict | Condition |
|---|---|
| `rejected` (refuted) | N >= 4 and score < 0.35 |
| `integrated` | N >= 3 and score >= 0.60 and P(X >= confirmations under 0.50) <= 5% |
| `rejected` (inadequate) | N >= 4 and P(X <= confirmations under 0.60) <= 5% |
| `proposed` | otherwise — insufficient evidence |

Exact binomial tails. No human validation (ADR-006).

## 6. Life after birth

- **Weekly confrontation** (`_commit_confrontations`, source `evaluation`). The
  post-Worker Planner (an LLM, behind a guardrail) emits verdicts; only
  invariants whose condition is active TODAY are confronted, and reference
  notes are excluded by status.
- **Proposal verdict at +12 weeks** (`outcomes._confront_cited`, source
  `proposal`). Won confirms the cited invariants, lost infirms them.
- **Weekly restatement** (`as_of_cycle.reweigh_invariants_asof`, the
  "invariant-weights" step of the Sunday chain). Every non-reference invariant
  is restated from its `invariant_confrontations` rows.

All three go through `restate_invariant`, so the verdict moves with the counts.
- **Contradictions** (`check_contradictions`). Integrated pairs with the same
  handle and metric, opposite direction, and conditions that can co-occur.
  Flagged, never auto-resolved; the only call site found is in `seed.py`.

Live volumes: 10,052 `backtest` confrontations, 10 `evaluation`, 0 `proposal`.

## 7. What consumes the weight

- **Worker context** (`planner/baseline.py`) — `integrated` only, ordered by
  weight, 8 per bucket, 20 at most.
- **Retrieval** (`planner/retrieval.py`) — everything except `rejected`,
  ordered by weight. Reference notes are included.
- **Digest** — the highest-weighted integrated invariants.

The weight enters no allocation decision: the Worker does not allocate
(ADR-012) and gate 6 is gone.

## 8. State of the population

| Status | Rows | Mean weight | Never confronted |
|---|---|---|---|
| integrated | 12 | 0.562 | 0 |
| proposed | 165 | 0.424 | 13 |
| rejected | 73 | 0.312 | 0 |
| reference | 1,396 | 0.656 | 1,396 |

## 9. Points to audit

1. **Verdict lagging the score — FIXED 2026-10-03.** The `evaluation` and
   `proposal` paths updated the score without recomputing the verdict, so
   `status` stayed as the last weekly restatement wrote it, for up to a week.
   Now covered by `test_a_confrontation_restates_the_verdict_not_only_the_score`.
   To verify: no UPDATE of `confirmation_count` outside `restate_invariant` and
   `_persist_maturation`.
2. **Reference notes outweigh measured invariants.** A note keeps its starting
   weight forever (0.80 for a Dalio book) with no measurement at all, and
   retrieval orders by weight. The 2026-10-03 formula stops measurement from
   PENALISING an invariant; it does not touch the notes. Section 8's means
   predate that formula and move at the next weekly restatement.
3. **Independence of moments.** The 12-week spacing makes windows disjoint, not
   episodes independent. Example: `01M323X79G8326RD8X61QZXTS8` is integrated on
   8 confirmations drawn from two episodes (2008 and 2021-2023).
4. **Full-sample baseline.** The code concedes it ("a weight prior, not
   out-of-sample proof"): the birth score is in-sample.
5. **No weighting by the age of the evidence.** A relation that stopped working
   is diluted by its old confirmations. Measured: of 113 invariants with at
   least 15 confrontations on each side of 2010, 3 fell from >= 0.60 to < 0.50
   and 9 did the reverse — compatible with noise, not tested, and the 2010 cut
   is arbitrary.
6. **`evaluation` confrontations come from an LLM.** Only 10 so far, but each
   weighs as much as a mechanical one.
7. **Documentation lag.** The `AuthorBand` docstring quotes the old floors
   (0.40 / 0.35 / 0.20 / 0.05); `docs/ARCHITECTURE.md` still mentions gate 6
   eligibility.
8. **Point anomalies in the database.** One dalio invariant at `weight_initial`
   0.70, outside its band; one non-reference invariant without a maturation
   marker; 13 rows with no cited passage (`supports.cited = 1`).
