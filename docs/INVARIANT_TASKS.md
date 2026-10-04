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
- [x] **6.1 residue.** `invariant_merge_threshold` is removed from the seed,
  and the seed now deletes any threshold it no longer names
  (`_seed_reference_tables`; INSERT OR REPLACE alone never removed a key). The
  live row leaves at the next seed run.
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

DONE and deployed 2026-10-04. The live database is the backup
`investment.db.bak-pre-evidence-dates-20261004` migrated, with the invariant
steps of the seed re-run on it (see "Deployed" below for why not the full seed).

- [x] **1.1 Two dates.** `invariant_confrontations.date` → `signal_date`, plus
  `available_at` (`RENAMED_COLUMNS` / `ADDED_COLUMNS` with a backfill:
  `backtest` = signal + horizon; `evaluation` and `proposal` = the day
  written). `as_of_snapshot` bounds on `available_at`. Guarantee tested: a
  confrontation whose signal predates t and whose window closes after it does
  not reach the replay (`test_agentic_replay_semipit`).
- [x] **1.1 follow-up — what the outcome-date bound changes, measured
  2026-10-04 on the re-swept live database.** At 2008-10-01, 144 verdict rows
  had a signal before t and an outcome after it; removing them touches 144
  invariants and changes 16 verdicts (9 `rejected` → `proposed`, 4 `proposed`
  → `rejected`, 2 `integrated` → `proposed`, 1 `proposed` → `integrated`;
  integrated as-of t 9 → 8). At 2022-12-31: 51 rows, 51 invariants, 4 verdicts
  (integrated 11 → 10). The audit had reported 142 / 12 and 52 / 6 on the
  records before the re-sweep.
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
- [x] **Deployed.** Live database: 9 integrated, 178 proposed, 68 rejected,
  820 reference; 16,699 confrontations (5,351 confirmed, 5,038 refuted, 5,491
  neutral, 808 no data, 11 evaluations), none with a NULL `available_at` or
  `definition`; every invariant's stored count equals its rows; no floor other
  than 0.05; `invariant_merge_threshold` gone. Identical to the core sample.
- [x] **Incident during deployment — the full seed corrupted GLD, found by the
  anti-drift tests, repaired the same day.** LBMA answered 403, the GLD splice
  was rejected, and the seed's "ETF-only floor" fallback wrote ETF-scale prices
  over the tail of the stored splice (proxy scale): 1263.50 on 2004-11-17,
  44.38 the day after. The stack's control arm then showed a -39% drawdown.
  Repair: database restored from the backup, then ONLY the invariant steps of
  the seed re-run (reference tables, seed invariants, 35y re-sweep,
  contradiction check; a SeedEvent records that it was partial). Fix: a
  rejected splice now leaves a stored longer history untouched and reports the
  ticker as skipped (`seed._seed_market_data`), tested by
  `test_a_rejected_splice_does_not_overwrite_the_spliced_history`. The damaged
  file is kept as `investment.db.damaged-gld-splice-20261004`.
- [x] **Span guard counts values, not rows.** GLOBAL_LIQUIDITY carried 406
  NULL warm-up rows over 1991-2003, which passed for twelve years of history
  held, so every seed abandoned the delete and wrote the series additively.
  `replace_ts_series` now measures both spans on rows that carry a value
  (`schema.TS_VALUE_COLUMN`); the 406 rows are deleted from the live database.
  Tested by `test_rows_without_a_value_are_not_history_held`. (The ten FRED
  rows the full seed dropped were the 35-year window sliding — not a defect.)
- [x] **The spliced histories are archived.** LBMA (403, browser user-agent
  included) and Yahoo `^BCOM` (no data) no longer answer, so gold before 2004
  and commodities before 2006 could not be rebuilt on an empty database.
  `market/spliced_history_archive.py`: one CSV per spliced ticker in
  `~/data/investment/spliced_history/` (beside the database, NOT in the public
  repository — licensed data). The seed archives every splice that succeeds,
  and on a rejected splice with nothing longer stored it rebuilds from the
  archive and chains the ETF's returns onto it. An archive is refused when the
  series mixes two scales or reaches less far back than the one kept. Exported
  2026-10-04 for 11 of the 12 spliced tickers.
- [x] **DJP was damaged in the live database from 2026-09-21 to 2026-10-04 —
  found by the archive's refusal, repaired.** 169.79 on 2006-10-27, 48.55 on
  2006-10-30: a phantom -71% day. Same failure as GLD, two weeks earlier — the
  seed of 2026-09-21 17:43 met a dead `^BCOM` and wrote ETF-scale prices over
  the splice. Holders: `4s-balanced-defender` (7.5%), `momentum-macro-rotation`
  (10%), `all-weather-USD` (7.5%) and three disabled portfolios; the
  market-signal stack holds none. Repair (owner, 2026-10-04): the spliced
  history from `investment.db.bak-pre-seed-20260921`, the returns since
  2026-09-18 chained onto it, then NAV, benchmark valuations, backtests and
  FAVORS, the invariant sweep (`mature_seed_invariants(
  remeasure_on_changed_data=True)`, new) and the snapshot recomputed; a
  SeedEvent records it; DJP is now archived like the others. Effect: ranking
  unchanged (the 36-month window never saw 2006), CAGR +0.2pp on the three
  enabled holders, 31 invariants moved by a count or two, 2 `rejected` back to
  `proposed`, integrated still 9. Backup before the repair:
  `investment.db.bak-pre-djp-repair-20261004`. The digests of 2026-09-27 and
  2026-10-04 were issued on the damaged series and stand as issued.
- [x] **Live database, final state 2026-10-04:** 9 integrated, 180 proposed,
  66 rejected, 820 reference.

## Lot 2 — Keep measuring after birth (P2, 5.1, 5.2)

- [x] **2.1 Weekly forward sweep.** `invariants.confront_completed_moments`,
  step `invariant-forward` before `invariant-weights`. It takes up the moments
  the birth sweep left open (stored `no_data`, window incomplete) once their
  window completes, then resumes `sample_moments` one horizon after the last
  stored moment. A moment is written once, when its window completes; its
  baseline is bounded at `signal_date + horizon` (D1), so a catch-up after
  weeks asleep writes what the weekly runs would have. Source `forward`;
  `ux_confrontation_mechanical_moment` is unique on (invariant, source,
  signal_date, definition) for the two mechanical sources. Guarantees tested
  (`tests/test_invariant_forward.py`): measurement continues after birth, a
  second run writes nothing, nothing is written before a window completes,
  one catch-up equals thirty weekly runs, a re-sweep does not count a moment
  twice.
- [x] **2.1 — the cause found on the way: the series stood still.**
  `benchmark_valuation` and the derived signals (real rates, broad money,
  equity trend, gold deviation) were written by the seed ONLY. No window could
  complete between two seeds — that, not the skipped re-sweep, is why
  mechanical confrontations stopped at birth — and `active_invariant_ids`
  answered "is this condition active today?" on the derived signals of the
  last seed. New step `benchmark-valuations`, last of the refresh block, same
  call as the seed's step 10b.
- [x] **2.1 — a re-sweep replaces the forward rows too.** It runs again
  because the definition, the rule or the data changed, and each makes a
  forward row as stale as a birth row; it then covers those dates itself. The
  forward baseline (as known that day) is lost for those moments and replaced
  by the whole-sample one — consistent with D1, stated here because it is a
  choice.
- [x] **Core sample on a copy of the live database (2026-10-04).** As it
  stands: `benchmark-valuations` 3.9 s and changes no value; forward sweep
  0.4 s, 254 invariants swept, nothing due, 100 moments waiting. With the
  records rewound to 2026-04-03: 232 moments confronted over 138 invariants
  in 65 s (60 confirmed, 77 refuted, 95 neutral) — the same dates and the same
  verdict as the birth sweep on every one of them, statuses unchanged, second
  run empty. The cost is the as-of baseline, about 0.3 s per moment; a normal
  week has some twenty.
- [x] **5.1 Only measurement moves a standing.** `restate_invariant` counts
  `backtest` + `forward` only; `evaluation` rows are still written, as
  readings. `_commit_confrontations` no longer restates, and
  `commit_knowledge` no longer takes thresholds it had no other use for.
  `skill-evaluate-strategy.md` no longer tells the Worker its verdicts move
  the weights. Guarantee tested: a reading changes no count, score, weight or
  verdict, at the commit or at the next restatement
  (`test_a_reading_is_recorded_and_moves_no_standing`). On a copy of the live
  database: 6 invariants lose one to three counts, no verdict changes.
- [x] **5.2 Proposal source — unreachable, deleted.** The only proposal
  written on the live path is `market-signal`, which cites nothing; no code
  creates a `switch` or `reallocation` Proposal outside the replay's shadow
  ones. `_confront_cited` and `_cited_invariants` are gone with their two
  tests. LEFT FOR THE OWNER: the `proposal_cites` table now has no reader and
  no writer (the as-of prune aside) — dropping it changes the relation count
  in CLAUDE.md and DATA_MODELS, so it is a decision, not a cleanup.
- [x] **2.2 Encoded effect versus the text — demoted (D3).** Of the
  invariants with N = 0, six are absolute claims on cash's `return`: claims
  about REAL return encoded on a nominal series that never leaves the margin
  (7 to 87 moments, every one neutral). `validate_invariant` now refuses that
  shape — `asset-class:cash` + `absolute` + `return` — so the next sweep
  demotes the six to reference knowledge and a new one is demoted at birth.
  Cash AGAINST the other classes is measurable and stays. Verified on a copy
  of the live database: 6 demoted, nothing else moves.
  NOT TOUCHED, and why: six invariants whose condition never fired in 35
  years (nothing to measure yet, they wait), and one equities claim with two
  moments, both neutral — too little evidence, not an inexpressible claim.
  The live database changes at the next sweep (the next `commit_innovations`
  or seed), and a demotion ERASES the condition and the effect.

## Lot 3 — What the weight says and shows (P4)

- [ ] **4.2 Magnitude.** `severity` is written as 1.0 everywhere: store the
  excess over the baseline, then report mean excess, worst outcomes and
  stability by half-period.
- [ ] **4.1 Display.** N, interval, effect size and coverage beside the weight
  (dashboard, digest, Worker context). Sensitivity of `PRIOR_CONFRONTATIONS`
  at 2 / 4 / 8.
- [x] **4.3 A reference note carries no weight (D4).** `weight_effective` is
  NULL for `status='reference'` (`_force_uncertified`); the retrieval pool
  orders measured invariants by weight and places the notes after them, in the
  order retrieval found them; the Planner's pool and the Worker's context
  render a note as "reference note, not measured" through one function
  (`context.standing_label`), and the Worker's system prompt says so in half a
  sentence. Guarantees tested: a note has no weight after a sweep, a note
  never outranks a measured invariant whatever its author, a note is never
  shown with a number. The live rows lose their weight at the next sweep.

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
- [x] **D2 — yes (owner, 2026-10-04), and it was not a question:** decisions
  rest on measurements only, a principle the project already states.
- [x] **D3 — demote (owner, 2026-10-04):** too few claims and too much work
  for what a real-return metric would add.
- [x] **D4 — out of the ordering, and no weight at all (owner, 2026-10-04):**
  a weight on what is not measured does not respect the project's motto. They
  are treated as what they are, unmeasured notes.
- [x] **D5 — fixed checkpoints (owner, 2026-10-04).** And if few or none of
  the integrated invariants survive the calibrated rule, the Worker reads the
  best candidates, labelled with their real standing: it decides nothing, its
  role is to stir. This needs 4.1's display (N, interval, coverage) in the
  Worker context, so 4.1 comes before 3.1 lands.
- [x] **D6** — rename `date` → `signal_date`: yes (2026-10-04).
