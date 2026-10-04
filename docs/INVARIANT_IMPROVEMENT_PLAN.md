# Invariant improvement plan — lessons and actions

What two reviews of the invariant mechanism found, merged into one ordered list
of actions. Written 2026-10-04 against commit `5d88b62`.

**Sources.** (1) The internal intention-versus-code review of 2026-10-03 (seven
inconsistencies, `docs/INVARIANT_AUDIT_BRIEF.md` section 9). (2) An external
audit of that brief, itself a second reading of an earlier external audit.

**How to read the evidence column.** *Verified* = re-measured here, read-only,
on the live database or by running the project's own functions. *Reported* =
stated by the external audit, consistent with the code as read, not re-run.

**Already done (2026-10-03).** The weight is a prior the evidence replaces,
`(weight_initial × 4 + confirmations) / (4 + N)`; no date enters it; score,
weight and verdict are written together by `restate_invariant`. The external
audit confirms both as real improvements and finds no inconsistent status in the
database.

**Done 2026-10-04 — a re-curation replaces its own notes.** A reference note is
never a merge target, so every fingerprint change (prompt version, or a model
swap in `.env`) re-read the corpus and stacked a new generation of notes beside
the old. 647 of 1,467 curator notes were an older reading of passages read
again since; they were removed (backup
`investment.db.bak-pre-note-dedup-20261004`), leaving 823. The curator now
replaces, per batch, the notes whose cited passages all lie inside that batch
(`KnowledgeWriteback._superseded_notes`). By provenance, not similarity: the
same fact restated by two models measured anywhere from cosine 0.60 to 0.93,
and distinct facts from one passage reached 0.70. This also shrinks action 4.3
— 410 notes weigh 0.80 or more, down from 895 — without settling it.

## Lessons

1. **A piece of evidence has two dates.** When the signal fired, and when its
   outcome became knowable. ADR-003 enforces this for market data and nobody
   applied it to confrontations: `invariant_confrontations.date` named the only
   date there was. This is "when a second one arrives, find what named the
   first" again.
2. **One counter, three kinds of event.** A backtest measures a defined effect,
   an LLM evaluation expresses a reading, a proposal outcome measures a
   portfolio. They feed the same `confirmation_count`. Same shape as `supports`
   before `cited`.
3. **A threshold per look is not a threshold per lifetime, nor per corpus.** 5%
   at every confrontation is not 5% over an invariant's life, and 5% per
   invariant is not 5% over 250 of them.
4. **A null must be measured, not asserted.** "0.50 for every handle" follows
   from a median baseline only if the no-op margin removes both sides equally.
5. **Frequency is not magnitude.** A 60% hit rate says nothing about the size of
   the wins and losses.
6. **"Can co-occur" is not "is the same".** The dedup gate merges on the
   contradiction detector's overlap test, which answers a different question.
7. **Evidence belongs to the definition it tested.** A revised condition keeps
   the forward confrontations earned by the old one.
8. **Measurement must outlive birth.** "35y + forward" is the stated intention;
   only the 35y is mechanical.
9. **Check the reviewer too.** The first external audit claimed the inflation
   invariant was tested against zero (it is tested against its own historical
   median) and concluded "no drift" from a selected 3-versus-9 split. Our own
   brief called the status "frozen after birth" when it lagged by a week. Every
   figure below marked *verified* was re-measured before being written down.

## Actions, in priority order

### P1 — Define and date a piece of evidence

| # | Action | Gap and evidence | Kind |
|---|---|---|---|
| 1.1 | Store `signal_date` and `available_at` (= signal + horizon) on each confrontation; the as-of replay filters on `available_at`. | The replay bounds evidence at the signal date (`db/as_of_snapshot.py`, `invariant_confrontations` on `date`), so outcomes not yet complete are visible. *Verified:* 142 such rows at 2008-10-01, 52 at 2022-12-31. *Reported:* 12 and 6 verdicts change once availability is deferred. | Defect (ADR-003) |
| 1.2 | Stamp each confrontation with the maturation fingerprint of the definition it tested; a restatement counts only the current fingerprint. | `_persist_maturation` replaces `backtest` rows on re-maturation and keeps `evaluation` and `proposal` rows earned under the old definition. *Verified by reading.* | Defect |
| 1.3 | Record neutral results and missing data separately instead of one "no-op". | Both return `None` from `confront_moment` and neither is stored, so coverage cannot be reported. *Verified by reading.* | Defect |

### P2 — Keep measuring mechanically after birth

| # | Action | Gap and evidence | Kind |
|---|---|---|---|
| 2.1 | Weekly step: confront every moment whose 12-week window has newly completed, once, with a baseline known at that date. | A matured definition is skipped (`_already_matured`); the weekly "invariant-weights" step only recounts existing rows. *Verified:* `backtest` confrontations stop at 2026-07-03. | Design gap against stated intention |
| 2.2 | Check that the encoded effect tests what the text claims; add a real-return metric or demote what cannot be expressed. | Claims about REAL returns are encoded on nominal `return`. *Verified:* 13 `proposed` invariants are birth-matured with N = 0 and keep their full starting weight, against "nothing stays proposed forever". The cause for the cash invariants (every moment neutral) is inferred, not measured. | Design gap |

### P3 — Calibrate the verdict

| # | Action | Gap and evidence | Kind |
|---|---|---|---|
| 3.1 | Replace the per-confrontation test with fixed validation checkpoints or an anytime-valid procedure (confidence sequences). | *Verified with the project's own `time_validation_verdict`:* a fair coin is integrated at least once with probability 9.9% within 20 looks, 15.8% within 50, 19.6% within 100. | Owner decision |
| 3.2 | Count evidence in a way that respects dependence between moments of one episode. | Disjoint windows are not independent episodes. *Verified:* `01M323X79G8326RD8X61QZXTS8` is integrated on 8 of 8 from two episodes. A fixed "three episodes" minimum would be arbitrary; prefer a dependence-aware measure. | Owner decision |
| 3.3 | Define hypothesis families and apply a multiplicity correction suited to their dependence (Romano–Wolf is a candidate). | *Verified:* none of the 250 tests passes Benjamini–Hochberg at 5%; smallest adjusted p-value 0.651. This is a fragility diagnostic, not proof the 12 integrated invariants are false — and it is premature until P1 and 3.1 fix what a p-value means here. | Owner decision |
| 3.4 | Measure the null confirmation rate per protocol instead of assuming 0.50. | The margin can remove more results on one side of the median. *Reported:* an example built with the project's functions yields 1/3. Some benchmarks do sit near 0.50. | Design gap |
| 3.5 | Calibrate the "refuted" branch, or name it as the fast heuristic it is. | *Verified by arithmetic:* at N = 4 it rejects 0/4 and 1/4, which a fair coin produces 31.25% of the time. | Owner decision |

### P4 — What the weight says and shows

| # | Action | Gap and evidence | Kind |
|---|---|---|---|
| 4.1 | Keep the prior formula; show N, uncertainty, effect size and coverage beside the weight. Test the choice of 4. | The weight reads as a beta-prior mean only if observations are comparable; 4 is a convention. | Design |
| 4.2 | Measure magnitude alongside frequency: mean excess, worst outcomes, stability. | The margin sets a minimum size per retained observation; nothing measures the average effect or the tail. | Design gap |
| 4.3 | Take reference notes out of weight ordering, or give them a weight that does not claim measurement. | A note keeps its starting weight forever. *Verified:* 895 of 1,396 notes weigh 0.80 or more; 6 of the 12 integrated invariants weigh less than 0.80 even under the new formula. Retrieval orders by weight. | Owner decision |

### P5 — Separate the sources of evidence

| # | Action | Gap and evidence | Kind |
|---|---|---|---|
| 5.1 | One counter per source. LLM evaluations stop moving the mechanical score. | Evaluations are weekly, with no completed window, baseline or margin. *Verified on the 10 existing rows:* two invariants confirmed on consecutive Sundays (overlapping windows, which `sample_moments` exists to prevent), one confirmed and refuted on the same day. | Owner decision |
| 5.2 | A proposal outcome counts for an invariant only when the proposal actually tests it. | A won portfolio does not demonstrate each invariant it cited. No such rows exist yet (0 `proposal` confrontations). | Design gap |

### P6 — Hygiene

| # | Action | Gap and evidence | Kind |
|---|---|---|---|
| 6.1 | Merge duplicates only on EQUIVALENT conditions; use similarity to group ideas, not to delete definitions. | `_same_invariant` accepts conditions that merely can co-occur. *Reported:* "inflation > 3" and "inflation > 5" with the same effect merge. | Defect |
| 6.2 | Stop marking the whole batch as cited when the model cites nothing usable. | `_cited` falls back to the batch. *Verified:* 932 of 1,633 cited invariants carry exactly 20 cited passages, the batch size. Existing provenance needs a re-curation to repair. | Defect |
| 6.3 | Run the contradiction check on every integrated birth, as the module docstring says. | Only `seed.py` calls it. *Verified:* 0 contradictions among today's 12 integrated. | Defect |
| 6.4 | Bring the legacy floors to the universal 0.05. | *Verified:* 10 rows — 4 at 0.20, 2 at 0.35, 4 at 0.40. | Defect |
| 6.5 | Rewrite justifications that are no longer true. | `planner/baseline.py` and `mechanical/gates.py` still explain a filter by "weight dominated by the author-tier floor"; the `AuthorBand` docstring and `docs/EXAMPLE.md` quote the old floors. | Defect |
| 6.6 | Correct the audit brief: 13 rows without a `supports.cited` link are not without provenance — several carry an explicit source. | *Reported*, consistent with the seed and owner-note rows seen in the database. | Defect (doc) |

## Suggested order

1. **P1 first.** Until a confrontation is dated and tied to its definition, every
   later calibration is computed on leaked or mixed evidence.
2. **P2 and 5.1 together.** Adding the mechanical forward sweep is what makes it
   safe to stop counting LLM evaluations.
3. **P6** can proceed in parallel at any time; none of it waits on a decision.
4. **P3 last**, once the evidence it would calibrate is sound. Its consequence
   should be decided beforehand: under a strict correction, few or none of the
   current integrated invariants may survive, and the Worker reads integrated
   invariants only.

Each action needs a test on the guarantee it restores (for example: "a
confrontation dated after t changes no verdict before t"), not only on the
function it changes. The external audit ran 141 targeted tests, all passing;
none of them covers these guarantees.
