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
- [x] **6.4 Legacy floors — live database.** Re-seeded 2026-10-04: no row
  with `floor_weight != 0.05`.
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

Code done and DEPLOYED 2026-10-04 (agent stopped, backup
`investment.db.bak-pre-evidence-dates-20261004`, seed, agent restarted).

- [x] **1.1 Two dates.** `invariant_confrontations.date` → `signal_date`, plus
  `available_at` (`RENAMED_COLUMNS` / `ADDED_COLUMNS` with a backfill:
  `backtest` = signal + horizon; `evaluation` and `proposal` = the day
  written). `as_of_snapshot` bounds on `available_at`. Guarantee tested: a
  confrontation whose signal predates t and whose window closes after it does
  not reach the replay (`test_agentic_replay_semipit`).
- [ ] **1.1 follow-up.** Re-run the as-of replay at 2008-10-01 and 2022-12-31
  and count the verdicts that move (the audit reported 12 and 6).
- [x] **1.2 Definition fingerprint on each confrontation.** `definition` =
  `definition_fingerprint(condition, effect)`, apart from the maturation
  fingerprint, which also hashes the verdict thresholds. `restate_invariant`
  takes an id and counts the rows of the current definition itself; the three
  callers no longer hand it counters. Guarantee tested: revising a condition
  zeroes the standing and keeps the rows
  (`test_a_revised_condition_does_not_inherit_the_old_ones_evidence`).
- [x] **1.3 Neutral is not missing.** `confront_moment` returns one of four
  outcomes and all four are stored; only `confirmed`/`refuted` count. The
  maturation fingerprint includes what the sweep records, so every definition
  is re-swept once.
- [x] **Core sample on a copy of the live database (2026-10-04).** Migration
  clean (0 NULL dates). Re-sweep of 254 measurable invariants in 77 s: 5,351
  confirmed, 5,038 refuted, 5,491 neutral, 808 no data — a third of all
  moments sit inside the margin. Old code and new code give IDENTICAL counts
  and verdicts on the same data, so the change of code moves nothing.
- [x] **What the re-sweep itself moves — seen and accepted by the owner
  ("needed to realign on the intention").** The stored records dated from each
  invariant's birth and the data had moved since (composite repair 2026-09-21,
  debt leg, 12 more weeks). On the core sample 71 invariants changed counts and
  9 changed verdict: integrated 12 → 9 (`inv-high-inflation-equities` 30/16 →
  28/18, `inv-gold-ratio-trend-tilt` 28/16 → 27/18,
  `01KZG80DPFB0Z72RMVTAB32BM9` 5/0 → 4/0, all to `proposed`), and 6 `rejected`
  returned to `proposed`. This is plan lesson 8 measured: a birth record goes
  stale.
- [x] **Deployed.** Live database after the seed: 9 integrated, 177 proposed,
  69 rejected, 820 reference; 16,607 confrontations (5,320 confirmed, 5,032
  refuted, 5,457 neutral, 787 no data, 11 evaluations), none with a NULL
  `available_at` or `definition`; every invariant's stored count equals its
  rows. The figures differ slightly from the core sample because the seed
  refetched market data.
- [ ] **Seen during the seed, not this plan's:** Yahoo returns nothing for
  `^BCOM` (three attempts, six minutes), so the DBC splice is rejected and DBC
  keeps its ETF-only history from 2006 — unchanged from before the seed.
- [ ] **6.1 residue** — drop `system_thresholds.invariant_merge_threshold`.

## Lot 2 — Keep measuring after birth (P2, 5.1, 5.2)

- [ ] **2.1 Weekly forward sweep.** A step `invariant-forward` before
  `invariant-weights`: resume `sample_moments` from the last stored moment
  (which is why 1.3 comes first), confront only windows that have completed,
  against the baseline as it stands that day (D1). Own source `forward`; unique
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
- [ ] **2.2 Encoded effect versus the text.** The cause of N = 0 is now
  measured (core sample, 12 invariants): 5 have a condition that was never
  active in 35 years (0 moments), 7 are neutral at every moment (2 to 86
  moments, all inside the margin). Then add a `real_return` metric or demote.
  Owner decision D3.

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
- [x] **D1 Birth baseline — NO (owner, 2026-10-04).** The birth sweep keeps
  its whole-sample baseline: looking back with everything known today is an
  advantage to use, and it makes an invariant's record more pertinent. What
  must not leak is the outcome's DATE, which 1.1 settles. Consequence for 2.1:
  a forward moment is judged against the baseline as it stands when its window
  completes.
- [ ] **D2** — LLM evaluations stop moving the score once 2.1 runs.
  Recommended: yes.
- [ ] **D3** — the N = 0 invariants: a real-return metric, or demotion.
- [ ] **D4** — reference notes: out of the ordering, or a weight that does not
  claim measurement.
- [ ] **D5** — P3: fixed checkpoints or confidence sequences; and what the
  Worker reads if none of the 12 integrated invariants survives.
- [x] **D6** — rename `date` → `signal_date`: yes (2026-10-04).
