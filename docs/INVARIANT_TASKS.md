# Invariant improvement — tasks

The execution checklist for `docs/INVARIANT_IMPROVEMENT_PLAN.md` (the plan says
WHY and carries the evidence; this file says WHAT, in what order, and where it
stands). Action numbers are the plan's. Started 2026-10-04.

Each task names the GUARANTEE its test must restore, not the function it
changes (plan, last paragraph).

## Where it stands — CLOSED (owner, 2026-10-10)

The chapter is closed. Lots 0 to 4 are done, deployed and committed; the
question that followed — is there information in the corpus at all — was put
to five measurements and answered (section "After lot 4"). Nothing below is
waiting to be worked on: what is left is PARKED, each item with what would
reopen it.

**What the chapter leaves in place.**

- A piece of evidence has two dates and belongs to the definition it tested;
  only measurement moves a standing; measurement continues after birth.
- The verdict is judged every 10 decided moments against the measured null of
  each protocol, with an integration bar and a rejection bar that each hold 5%
  over a record's whole life.
- A reference note carries no weight; a measured moment carries its lift; a
  record is shown with its range, its coverage and its stability.
- The Worker and the digest read the established invariants, then the
  candidates a checkpoint has judged, each labelled "not established".
- The corpus is material for the Worker's proposals, not a set of signals
  (owner, 2026-10-10). No further signal experiment is planned on it.

**Live database, 2026-10-10:** 0 integrated, 237 proposed, 12 rejected, 826
reference. Backups: `investment.db.bak-pre-checkpoint-verdict-20261006` (before
lots 2 to 4) and `investment.db.bak-pre-rejection-bar-20261006`.

**The chain of 2026-10-11 is the first on this code.** Its thirteen mechanical
steps were rehearsed on a copy of the live database on 2026-10-10 and all
pass, the two that had never run live included (`benchmark-valuations`,
`invariant-forward`: ten moments confronted, no status changed). Not
rehearsed: the three steps that call a model (`event-watch`, `curation`,
`uc8`) and the sending of the digest.

**Parked — and what would reopen each:**

- [ ] **6.2, the batch marked as cited** — the next curator reading, whose log
  now shows what an unusable citation looks like.
- [ ] **The `proposal_cites` table**, without reader or writer since 5.2 — an
  owner decision, because dropping it changes the relation count in CLAUDE.md
  and DATA_MODELS.
- [ ] **`docs/EXAMPLE.md` and the test list of `docs/TASKS.md` Phase 6** still
  describe the proposal-sourced confrontation removed by 5.2 — the next time
  either file is edited.
- [ ] **A demotion leaves the old confrontation rows** (208 neutral rows of the
  six cash claims, counted nowhere) — if a demoted claim is ever re-promoted,
  or the rows are found to mislead a reader.
- [ ] **One equities claim** (`01M327ZEK75QTGAS5R4YVH16CX`), two moments, both
  neutral — more moments, which the forward sweep now brings.
- [ ] **One definition held twice** (`inv-low-real-rate-nominal-bonds` and
  `01KY2N2MX6G7BZD10380KYBZAF`, both 26/48) — a decision to merge after the
  fact; the gate merges at writing only.
- [ ] **The dashboard's invariant columns** were rebuilt on 2026-10-06 and have
  not been looked at in a browser — the next time the dashboard is opened.
- [ ] **A dependence-aware range for the displayed record** — an instrument
  other than calendar blocks, which failed its criterion (3.2).
- [ ] **A record's rate inside its era, shown beside the whole-sample one** —
  not built; D1 stands and the era effect is written down instead.

**Decided against, so that it is not proposed again:**

- Tracing which invariants each proposal drew on (owner, 2026-10-10): it adds
  nothing to the efficiency of the system.
- Lowering theta, or any change whose purpose is to have more invariants
  integrated: the useful question was whether the corpus carries exploitable
  information, and it was answered.

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
- [ ] **6.2 Stop marking the whole batch as cited.** PARKED, WAITING on the next
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

DONE in code 2026-10-04, tested; deployed 2026-10-06.

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

DONE in code 2026-10-05 (4.3 on 2026-10-04), tested; deployed 2026-10-06.

- [x] **4.2 Magnitude.** Each measured moment stores its `lift` — the
  handle's value beyond its baseline, positive when in favour of the claim
  (`invariants.moment_lift`). `severity`, written as the constant 1.0 and read
  by nothing, is dropped rather than renamed so that no row keeps a 1.0 that
  would read as a measured effect; a Worker reading carries no lift. The
  maturation fingerprint records the change, so the next sweep re-measures
  every definition once and fills it. The verdict still reads the label alone
  (a magnitude-weighted score stays I-24).
- [x] **4.1 Display.** `invariants.evidence_summaries` reads the rows
  `restate_invariant` counts and reports: confirmed / decided, the rate's 95%
  Wilson range, neutral and unmeasurable moments, mean and worst lift, and the
  rate over the earlier and the later half of the decided moments. ONE
  renderer (`describe_evidence`) for the Worker context (beside the weight),
  the text digest and — as columns — the Gmail table and the dashboard's
  invariant table. The range is the one the record would carry IF its moments
  were independent, and says so: the truth is wider (3.2).
- [x] **Core sample on a copy of the live database (2026-10-05).** Re-sweep of
  248 definitions in 73 s; no verdict moves (9 integrated, 174 proposed, 66
  rejected, 826 reference); every confirmed, refuted and neutral moment of a
  measurable invariant carries a lift. Read on the 9 integrated: none has a
  negative mean lift; five rest on 5 to 8 decided moments, with a range whose
  lower bound sits between 0.53 and 0.68; the largest record is 54 of 83
  (0.54-0.74). Three are weaker in their later half (1.00 → 0.75, 1.00 → 0.71,
  0.77 → 0.62).
- [x] **Sensitivity of `PRIOR_CONFRONTATIONS`, same copy, 242 measured
  invariants.** At 2 instead of 4: mean weight change 0.018, largest 0.133, 23
  move by more than 0.05, 18 of the top 20 unchanged. At 8: mean 0.023,
  largest 0.107, 26 move by more than 0.05, 19 of the top 20 unchanged. The
  choice of 4 is a convention the ordering barely depends on; it stays.
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

DONE 2026-10-06 and deployed. The bench came first and every rule was chosen
on it; its two protocols were written before their figures
(`docs/research/2026-10-05-verdict-calibration/`).

- [x] **3.4 A simulation bench first.**
  `docs/research/2026-10-05-verdict-calibration/` — protocol written before
  the figures, `bench.py` read-only on the live database, results and reading
  in its README. A rule is a status per record plus the counts at which it
  looks; the bench reads it exactly on independent moments and on a PLACEBO
  (each invariant's own condition shifted along its calendar, 400 times, so
  the episodes keep their shape and lose their link to what followed). Its
  moments, excess and labels are tested equal to production's, and shift zero
  reproduces the measured confrontations of all 248 invariants.
- [x] **3.4 — what it measured (2026-10-05).**
  NULL: 0.485 to 0.515 on the 19 asset and asset-class protocols; 0.41 to 0.68
  on four of the five strategy protocols, where 86% to 92% of dates are
  neutral (five invariants judged against a null that is not theirs).
  DEPENDENCE: about none for conditions holding under 10% of the time (median
  dispersion 0.93); above 1.5 for 28% of the records of 20 moments or more,
  2.92 for `inv-low-real-yields-favor-gold`. For dense conditions the placebo
  gives a floor, not an estimate.
  RULE IN FORCE: a claim that knows nothing is integrated at least once in
  9.0% of placebo lives (20.0% for a fair coin within 160 looks, the plan's
  figures reproduced), 2.5% at a single look at the end of the sample; the
  gold invariant's shifted condition passes 26% of the time at a single look.
- [x] **3.4 — the two candidates of 3.1 on the bench.** Lifetime level 5%
  spent equally, bars exact, never below theta. DOUBLING (10, 20, 40, 80, 160,
  320 — nothing can pass at 5): 3.7% for a fair coin, 1.0% on the placebo, 11
  of 242 invariants above 5%; keeps 2 of the 9 integrated. EVERY 10: 2.4%,
  0.3%, none above 5%; keeps 1. Same power past 50 decided moments (a true
  0.65 claim: 75% within 100, 94% within 160), doubling ahead before it. A
  record between two checkpoints is judged on the moments up to the last one:
  up to half the record waits under doubling, at most 9 moments under every-10.
- [x] **3.1 Fixed checkpoints (D5) — every 10, against the measured null
  (owner, 2026-10-05).** `invariants.checkpoint_verdict` replaces
  `time_validation_verdict`: a record is read every 10 decided moments up to
  320, on its first moments in date order, and nowhere between;
  `checkpoint_bars` computes the bars that hold 5% over the life, never below
  theta. `restate_invariant` reads the record in date order; the counts, score
  and weight stay the whole record's. The null is
  `invariant.null_confirmation_rate` (new column), measured by the sweep from
  the every-date excess it already computed; 0.50 remains for an unconditional
  claim. `invariant_min_confrontations` and
  `invariant_refuted_min_confrontations` are retired. Guarantees tested: a
  claim confirming at its protocol's null is integrated at least once over a
  life at most 5% of the time — simulated, not by the calculation that sets
  the bars, at nulls 0.41 / 0.50 / 0.68
  (`test_a_claim_that_knows_nothing_is_rarely_integrated_over_a_whole_life`);
  a fair-coin bar on a skewed protocol does not hold it; the moments since the
  last checkpoint wait for the next; a record is judged against its own null.
  ADR-006 carries the amendment; CLAUDE.md, ARCHITECTURE and DATA_MODELS the
  rule.
- [x] **3.1 — the consequence, measured before deployment: no invariant is
  integrated.** The table the choice was made on said every-10 kept one; that
  was on a 0.50 null. `inv-low-real-yields-favor-gold` has 52 of its first 80
  moments, the bar is 52 at 0.50 and 53 at GLD's measured 0.507. Seven of the
  other eight rested on 5 to 13 moments. The adopted rule on the placebo:
  0.21% over a life, one invariant of 242 at 5.25% (bench README, section 7).
- [x] **3.1 — what the Worker reads (D5).** `planner/baseline.py` shows the
  established invariants first, then the CANDIDATES: `proposed` invariants
  whose record has passed a checkpoint (judged, neither integrated nor
  rejected), by weight. One with no record stays out — its weight is belief
  alone. `context.standing_label` writes "candidate, not established" before
  the weight and the record, for the Planner's pool and the Worker's context
  alike, and the Worker's system prompt says what the label means. On the
  copy: 133 judged candidates, 16 shown. Guarantees tested: a judged candidate
  follows every established invariant although it outweighs them; a candidate
  short of a checkpoint or rejected is not shown; a candidate is never
  rendered without its label.
- [x] **3.2 Dependence between moments of one episode — measured, not
  corrected (2026-10-06).** Bench, second part (`PREREGISTRATION_2.md`,
  README section 8). The VERDICT needs no correction: dependence does raise
  false integrations (rank correlation 0.44 with the placebo dispersion) and
  the adopted rule still holds — 0.79% over a life on the 46 most dependent
  records, one invariant of 242 at 5.75%, inside the noise. The DISPLAYED
  range does not get one either, for want of an instrument: the design effect
  of calendar blocks tracked the placebo at 0.29 at best where 0.5 was
  required, and put the gold invariant at 1.5 where the placebo says 2.9. The
  range goes on saying it assumes independent moments. What follows is the
  note written before that measurement.
- **3.2, as it stood.** A dependence-aware
  count of the evidence, not an arbitrary minimum of episodes. Also what the
  displayed range needs to stop being optimistic. What the bench gives and
  does not: the placebo's dispersion is usable for a condition that holds
  rarely and is a FLOOR for a dense one, so an effective size taken from it
  would be most generous exactly where dependence is strongest. Needs another
  instrument (a block bootstrap over episodes is the candidate).
- [x] **3.3 Hypothesis families and multiplicity — measured, and the level
  stays per invariant (owner, 2026-10-06).** Joint placebo, one shift for all
  247 conditions, families = one effect (24 of them): at the adopted 5% per
  invariant, 0.2 invariant is integrated by chance at any one time, at least
  one 16% of the time, at least one at some point 40% of the time. At 1% per
  invariant: 2% and 8%, and a true-0.70 claim integrated within 100 moments
  87% of the time instead of 96%. No level met both conditions fixed in
  advance. The figure is written in ARCHITECTURE and CLAUDE.md, to be read
  beside any count of integrated invariants. Grouping ideas by similarity was
  not needed: the families that share an outcome series are given by the
  effect.
- [x] **3.5 The rejected branches — calibrated (owner, 2026-10-06).**
  `invariants.rejection_bars`: the mirror of the integration bar, so that a
  claim exactly at theta is rejected at least once over its life at most 5% of
  the time. `refuted` and `inadequate` are gone, with
  `invariant_refuted_score` and the binomial tails; `checkpoint_verdict` takes
  a count, a checkpoint and two bars. Measured before the choice: the two
  branches rejected a true-theta claim in 21.6% of lives, and `refuted` kept
  beside the bar still gave 9.6%. Cost: a fair coin leaves `proposed` within
  160 moments 65% of the time instead of 88%. Guarantees tested, by
  simulation: a claim at theta is rarely rejected over a life; nothing stays
  proposed forever; the two bars cannot meet. On the live database: rejected
  48 → 12.

## After lot 4 — is there information in the corpus at all? (research, 2026-10-07 to 10)

Asked by the owner once no invariant was integrated: is the mechanism too
restrictive, when the proposals come from experts? Four experiments, each with
a protocol written before its figures, all read-only, none changing production.
Their folders are under `docs/research/`, which the repository ignores; what
they found is recorded here.

- [x] **The corpus against noise (2026-10-06).** The corpus confirms at 0.517
  where its own conditions, shifted to dates where they mean nothing, confirm
  at 0.501 — reached by 1% of joint shifts. But noise alone gives 20 records
  at 0.60 or above, and there are 22: the best ones cannot be told from luck.
- [x] **One family on the size of its effect — gold (2026-10-07,
  `2026-10-07-gold-family`).** 26 members, 25 definitions, one vote each,
  periods counted once, against gold held the same way all the time, net of
  cost: −0.25 point per 12 weeks over 2002-2026, half the placebo shifts as
  good, the two halves disagreeing. Not shown.
- [x] **The era effect (2026-10-10, `2026-10-10-era-effect`).** A record is
  confirmed against the WHOLE-SAMPLE median, and a handle's ordinary excess
  differs by era (gold: −1.9% over 1991-2002, +1.1% since). Judged against the
  ten years around each moment, the corpus's gap falls from +1.56 to +0.87
  point and is no longer told from its placebo (8.3% of shifts); 44% of the gap
  was eras (61% at 5 years, 25% at 20). Gold carries it: its family goes from
  0.560 to 0.499, `inv-low-real-yields-favor-gold` from 54/83 to 43/85. The
  short strong candidates keep their rate. A baseline known at the time does
  not help (9% of the gap). D1 stands; this is what it lets through.
- [x] **Every idea on the size of its effect (2026-10-10, `2026-10-10-ideas`).**
  247 invariants are 178 ideas (same signals and operators, same handle, same
  direction; 109 invariants are a neighbouring threshold of another). 126
  judged: +0.05 point gross per 12 weeks over the era's ordinary excess, −0.14
  net of cost, one joint shift in five as good. Six ideas look significant
  alone and 126 draws of noise give 6.3.
- [x] **Twelve months, and drawdown instead of return (2026-10-10,
  `2026-10-10-horizon-and-drawdown`).** The same ideas: 12-month return −0.03
  point net (19.7% of shifts as good), 12-week drawdown +0.01 point (50.7%),
  12-month drawdown −0.08 point (84.7%). None claimed.
- **What it comes to.** As mechanical conditions on US macro signals, at 12
  weeks or 12 months, on return or on drawdown, the corpus does not separate
  the periods that follow from ordinary ones. That is a statement about a
  curator's one-threshold encoding over 1991-2026, not about the books, and not
  about what a reader does with an idea.
- One pair of invariants carries the same definition twice
  (`inv-low-real-rate-nominal-bonds` and `01KY2N2MX6G7BZD10380KYBZAF`), written
  on 2026-07-21 within minutes of the commit that introduced the structural
  merge. Today's gate would merge it; nothing merges after the fact. Parked
  (top of this file).
- **What the corpus is for (owner, 2026-10-10).** Not allocation signals:
  material for the Worker's PROPOSALS — strategy, tactics, remarks,
  predictions. The experiments above therefore answer a question the corpus
  was never asked to pass. What they are good for is the label on what the
  Worker reads.

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
  role is to stir. This needed 4.1's display (N, range, coverage) in the
  Worker context, which is in place.
- [x] **D6** — rename `date` → `signal_date`: yes (2026-10-04).
- [x] **3.1** — checkpoints every 10, against the measured null of each
  protocol (2026-10-05).
- [x] **3.5** — the calibrated rejection bar, alone (2026-10-06).
- [x] **3.3** — 5% per invariant, the corpus figure written down (2026-10-06).
- [x] **Digest** — the judged candidates, labelled (2026-10-06).
- [x] **The corpus's purpose** — material for the Worker's proposals, not
  signals; no tracing of which invariants a proposal drew on (2026-10-10).
- [x] **The chapter is closed** (2026-10-10).
