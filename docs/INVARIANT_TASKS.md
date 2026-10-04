# Invariant improvement — tasks

The execution checklist for `docs/INVARIANT_IMPROVEMENT_PLAN.md` (the plan says
WHY and carries the evidence; this file says WHAT, in what order, and where it
stands). Action numbers are the plan's. Started 2026-10-04.

Each task names the GUARANTEE its test must restore, not the function it
changes (plan, last paragraph).

## Lot 0 — Hygiene (P6)

- [x] **6.1 Dedup merges equivalent definitions only.** `knowledge.find_duplicate`
  merges on identical structure (same predicates, same effect) and nothing
  looser. The cosine pass, `DEDUP_COSINE_THRESHOLD`, `_same_invariant` and the
  embedding matrix of `InvariantCorpus` are deleted — once the structural test
  is equivalence, that pass was unreachable. Owner decision 2026-10-04.
  Guarantee tested: "inflation > 3" and "inflation > 5" with one effect stay
  two invariants (`test_a_different_threshold_is_a_different_definition`).
- [ ] **6.1 residue.** `system_thresholds.invariant_merge_threshold` is seeded
  and read by nothing. Removing a seeded threshold touches the live table —
  do it with the next schema-level change (lot 1), not alone.
- [ ] **6.2 Stop marking the whole batch as cited.** WAITING on the next
  curator reading (owner, 2026-10-04). `_cited` now logs what the model wrote
  when it cited nothing usable; the fallback itself is unchanged. Known so far:
  the fallback fired for 100% / 39% / 15% of claims under the three successive
  curator models, and removing it makes those notes un-replaceable
  (`_superseded_notes` needs at least one citation). Decide between a
  normalisation of the cited ids and a separate "read in this batch"
  provenance once the log shows the shape of an unusable citation.
- [x] **6.3 Contradiction check outside the seed.**
  `alerts.invariant_contradiction_alert`, in `collect_alerts` — a read of the
  current integrated set, so it covers a birth, a restatement and a
  re-maturation. Guarantee tested: two contradicting integrated invariants are
  told without `seed.py` running.
- [x] **6.4 Legacy floors to 0.05 — code.** The 10 seed invariants use
  `VISIBILITY_FLOOR`.
- [ ] **6.4 Legacy floors — live database.** The 10 rows keep 0.20/0.35/0.40
  until the seed is re-run; their weights follow at the next weekly
  `invariant-weights` step. Check afterwards:
  `SELECT COUNT(*) FROM invariant WHERE floor_weight != 0.05` → 0.
- [x] **6.5 Justifications that are no longer true.** `planner/baseline.py`,
  `mechanical/gates.py` (re-measured figures), `AuthorBand` and `AUTHOR_TIERS`
  in `writeback/knowledge.py`, `docs/EXAMPLE.md`, `docs/TASKS.md`,
  `docs/ARCHITECTURE.md`.
- [x] **6.5 Worker prompt.** `skill-interpret-invariants.md` is DELETED (owner,
  2026-10-04) rather than corrected: of its six paragraphs, two were false
  (authority floor, tiers), two unreachable (reference notes, how to cite) and
  one duplicated the system prompt. Its one live instruction — a `dormant`
  invariant is not evidence about today — is now a sentence of
  `WORKER_SYSTEM_PROMPT`.
- [x] **6.6 Audit brief corrected.** No cited passage is not no provenance: 14
  rows, 10 seeds with an explicit `source` and 4 `agent-discovery` notes.

## Lot 1 — Define and date a piece of evidence (P1)

Blocks every later lot. Two owner decisions first: D1 and D6 below.

- [ ] **1.1 Two dates.** `invariant_confrontations.date` → `signal_date`, plus
  `available_at`. Backfill: `backtest` = signal + horizon; `evaluation` and
  `proposal` = the day written. `as_of_snapshot._WORLD_OBSERVATIONS` bounds on
  `available_at`. Guarantee: a confrontation whose window completes after t
  changes no verdict at t. Re-measure the 142 rows at 2008-10-01 and the 52 at
  2022-12-31.
- [ ] **1.2 Definition fingerprint on each confrontation.** A `definition`
  column = fingerprint of (condition, effect) ALONE — the existing maturation
  fingerprint also hashes the verdict thresholds, so a threshold change would
  orphan the evidence. `restate_invariant` counts the rows of the current
  definition itself instead of being handed counters (`writeback.py` and
  `outcomes.py` do `counter + 1`; `_persist_maturation` overwrites the counter
  with `backtest` rows only). Guarantee: revising a condition zeroes the
  forward evidence the old one earned.
- [ ] **1.3 Neutral is not missing.** `confront_moment` tells them apart and
  both are stored (`verdict` = `neutral` / `no_data`, excluded from N), so
  coverage can be reported. Needs a 35y re-sweep of the ~255 measurable
  invariants: core sample of 10 first.

## Lot 2 — Keep measuring after birth (P2, 5.1, 5.2)

- [ ] **2.1 Weekly forward sweep.** A step `invariant-forward` before
  `invariant-weights`: resume `sample_moments` from the last stored moment
  (which is why 1.3 comes first), confront only windows that have completed,
  against a baseline known at the moment's date. Own source `forward`; unique
  on (invariant, source, signal_date, definition). Guarantee: two runs write
  the same rows; mechanical confrontations no longer stop at 2026-07-03.
- [ ] **5.1 One counter per source.** The mechanical score counts `backtest` +
  `forward` only; `evaluation` rows stay visible as readings.
  `_commit_confrontations` stops calling `restate_invariant`.
  `skill-evaluate-strategy.md` tells the Worker its confrontations move the
  weights — prompt edit. Owner decision D2.
- [ ] **5.2 Proposal source.** Check whether `outcomes._confront_cited` is
  still reachable since ADR-012 (0 rows, nothing writes `proposal_cites` on
  the live path); delete it if not.
- [ ] **2.2 Encoded effect versus the text.** Measure first why 13 `proposed`
  invariants are matured with N = 0 (moments / neutral / no data — readable
  after 1.3), then add a `real_return` metric or demote. Owner decision D3.

## Lot 3 — What the weight says and shows (P4)

- [ ] **4.2 Magnitude.** `severity` is written as 1.0 everywhere: store the
  excess over the baseline, then report mean excess, worst outcomes and
  stability by half-period.
- [ ] **4.1 Display.** N, interval, effect size and coverage beside the weight
  (dashboard, digest, Worker context). Sensitivity of `PRIOR_CONFRONTATIONS`
  at 2 / 4 / 8.
- [ ] **4.3 Reference notes out of weight ordering.** Owner decision D4.

## Lot 4 — Calibrate the verdict (P3) — last

- [ ] **3.4 then 3.1: a simulation bench first.** Measured null rate per
  protocol, and the false-integration rate over N looks. It is the guarantee
  test of whatever rule is chosen.
- [ ] **3.2 Dependence between moments of one episode.** After D5.
- [ ] **3.3 Hypothesis families and multiplicity.** After D5; this is where
  similarity may GROUP ideas (see 6.1).
- [ ] **3.5 The "refuted" branch.** Calibrate it or name it a fast heuristic.

## Owner decisions

- [x] **6.1** — delete the cosine pass (2026-10-04).
- [x] **6.2** — wait for the next reading (2026-10-04).
- [x] **Worker skill** — delete `skill-interpret-invariants.md` (2026-10-04).
- [ ] **D1 Birth baseline.** The 35y sweep judges each moment against a median
  taken over the whole sample, future included; the plan fixes it for the
  forward sweep only. Making it "known at the date" everywhere fits ADR-003
  and moves every verdict. Recommended: yes, core sample first.
- [ ] **D2** — LLM evaluations stop moving the score once 2.1 runs.
  Recommended: yes.
- [ ] **D3** — the N = 0 invariants: a real-return metric, or demotion.
- [ ] **D4** — reference notes: out of the ordering, or a weight that does not
  claim measurement.
- [ ] **D5** — P3: fixed checkpoints or confidence sequences; and what the
  Worker reads if none of the 12 integrated invariants survives.
- [ ] **D6** — rename `date` → `signal_date` (an `ALTER TABLE` on the live
  database, backup first). Recommended: yes — it is lesson 1.
