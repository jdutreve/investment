# DECISIONS.md — Architecture Decision Records

One entry per structural decision. Status: `accepted` | `validated by spike`
| `superseded by ADR-N`. Newer ADRs never silently contradict older ones —
they supersede explicitly.

---

## ADR-001 — Single embedded engine: arcadedb-embedded, gated by a spike

**Status:** SUPERSEDED by ADR-004.
**Date:** 2026-07.

**Context.** The whole persistence design (graph + documents + time-series +
vector + FTS) bets on one library, `arcadedb-embedded` (in-process JVM via
Python bindings). Four capabilities are assumed simultaneously and none is
verified for the installed version on the target (macOS ARM64): TIMESERIES
type DDL, HNSW vector index on FLOAT[768] exposed through the Python
bindings, FTS indexes, and stable asyncio single-writer access to an
in-process JVM. Any one missing would otherwise be discovered mid-build,
with a blast radius covering every spec file.

**Decision.** Keep the single-engine design (one database, one transaction
scope, EventLog ordering invariant enforceable in one write path), but make
it conditional on **Task 0.5 — a one-day GO/NO-GO spike** run before any
production code, with a **fallback ladder decided in advance**:

- F1 (no TS types) → plain document types `(ts, tags…, fields…)` + index;
  no functional loss (no downsampling is used).
- F2 (no vector index) → `embedding FLOAT[]` property + brute-force numpy
  cosine (milliseconds at this corpus size).
- F2-bis (no FTS) → LIKE / in-Python token match.
- F3 (embedded engine unusable) → DuckDB + flattened graph — the only
  redesign path; the spike exists to surface it on day one.

**Consequences.** The specs' "verify, do not guess" notes are resolved by
the spike, which replaces them with verified syntax or the chosen fallback.
The key insight recorded here: at this project's scale (thousands of
vertices, ~200k TS rows), every advanced engine feature has a trivial
degraded mode — so the engine bet is survivable by construction.

---

## ADR-002 — Deployment target: local MacBook Pro M5 (24 GB), not a VPS

**Status:** accepted.
**Date:** 2026-07 (supersedes the earlier Hetzner CAX21 ARM target).

**Context.** The system is single-user, single-process, weekly-cadence. A
dedicated VPS added cost, SCP ingestion plumbing, and systemd ops for no
functional gain.

**Decision.** Run everything locally on the user's MacBook Pro M5 (macOS
ARM64, 24 GB RAM): launchd LaunchAgent instead of systemd, local `cp` into
`~/data/investment/inbox` instead of SCP. Paths move to
`~/data/investment/...` and `~/projets/investment/`.

**Consequences.**
- **Laptop sleep is the structural trade-off** — resolved (2026-07 rev.) by
  removing clock-based jobs entirely: NO nightly cron. Ingestion is
  event-driven (inbox watcher, 60s poll, 5-min quiet period → batch →
  curation); the weekly chain is DUE-ON-START (run at launch/wake/Monday
  cron if the last success predates the most recent Monday 08:00, exactly
  once); backup follows every chain and ingestion batch. Correctness never
  depends on the lid being open or the Mac being on at any given time.
- Backups stay local (`~/data/investment/backups`) — an off-machine copy
  (iCloud/rsync) is recommended but not part of V1 scope.
- If 24/7 autonomy is ever needed (V2 auto-execution), revisit toward an
  always-on host; that would supersede this ADR.

---

## ADR-003 — Market data is "as known at ts"; replay runs on first-release vintages

**Status:** accepted.
**Date:** 2026-07.

**Context.** FRED serves the *latest revised* values of macro series. INDPRO
is heavily revised (annual benchmark revisions), CPIAUCSL/UNRATE lightly
(seasonal factors). Two lookaheads threatened the Phase 9 replay's
point-in-time claim: (1) revised values "know" the future at historical
dates; (2) observations indexed at their reference month are visible weeks
before their real publication. The go-live gate (DoD 12) and the threshold
calibration (Task 9.2) would inherit that optimism.

**Decision.** One rule, everywhere: **a MarketData row's `ts` is the date
the value became knowable, and its `level` is the value as first known.**
Concretely:
- Every macro observation is indexed at its **publication date** — from
  ALFRED `realtime_start` when available, else `reference_date +
  allowed_tickers.availability_lag_days` (CPI ≈ 13d, INDPRO ≈ 16d,
  UNRATE ≈ 7d, UMCSENT ≈ 3d).
- The 35y **backfill stores first-release values** (ALFRED vintages) for the
  revised series (INDPRO first; CPIAUCSL, UNRATE second); composites and
  z-scores are computed from those as-known rows.
- The **live daily fetcher** appends whatever is current at fetch time —
  which at publication *is* the first release — so live and backfill rows
  have identical semantics. Post-append revisions are ignored; the
  2-consecutive-prints hysteresis absorbs revision noise.
- `replay_report.vintage_mode` records what the replay ran on
  (`first_release` expected); a go-live verdict obtained on revised data is
  not valid evidence.

**Consequences.** `materialize_history` and `shadow_replay` become PIT by
construction — they simply read MarketData `ts ≤ t`, no special casing.
Non-revised series (ETF prices, ^VIX, ^IRX, T10Y2Y, WALCL & liquidity
components) are unaffected. Cost: one extra fetch path
(`fetch_alfred_first_release`) used only by the backfill.

---

## ADR-004 — SQLite as the single engine (supersedes ADR-001)

**Status:** accepted.
**Date:** 2026-07.

**Context.** Auditing the actual workload dissolved the multi-modal premise:
**no query in the entire system traverses more than one hop** (every edge is
a FK with properties — FAVORS, BACKED_BY, HOLDS… are association tables in
disguise, two of them already denormalized into the snapshot); the
time-series total ~200k rows and all window math happens in pandas after a
one-shot load; the embedding corpus is a few thousand rows (30 MB matrix,
brute-force cosine < 10 ms); FTS would search ~50 invariants. ADR-001's
fallback ladder (F1 TS→tables, F2 vector→numpy, F2-bis FTS→LIKE) turned out
to describe the *right-sized* design, not degraded modes — leaving the JVM
in-process and unverified Python bindings as pure risk with no residual
benefit.

**Decision.** SQLite (stdlib `sqlite3`), one file
`~/data/investment/investment.db`, `journal_mode=WAL`,
`synchronous=NORMAL`, `foreign_keys=ON`, ONE connection serialized through
asyncio — which is literally the spec's own write model ("agent = sole
writer"). Mapping: entity → table, relation → association table
(`from_id, to_id, properties`), MAP → JSON1 TEXT, embeddings → float32
BLOB loaded once into an in-RAM numpy matrix, EventLog → append-only table
with monotonic ULID PK (append order = PK order), FTS5 native if ever
needed. Backup = `sqlite3 .backup` (online, WAL-safe).

**Alternatives rejected.**
- **DuckDB**: its columnar strength targets in-engine analytical scans we
  don't do (pandas does the math after a one-shot load), while its weakness
  — frequent small transactional writes — falls exactly on our spine (the
  append-only EventLog); storage format historically version-breaking,
  unacceptable for a file that must live 15 years. Possible later as an
  optional *reader* for the replay if profiling ever justifies it.
- **In-memory stores (Redis, LMDB, `:memory:`)**: solve latency at scale —
  a problem a weekly-cadence, 100 MB system does not have — by sacrificing
  the durability that is its raison d'être. At this size the SQLite file
  lives in the OS page cache anyway: in-memory speed comes free, WITH
  durability. KV stores additionally lose SQL, the Worker `db_query` tool's
  native language.

**Consequences.** The conceptual model (entities/relations vocabulary,
invariants, EventLog ordering, calculation conventions, replay) is
unchanged; only the DDL dialect and the DB wrapper change. Task 0.5 shrinks
from a 1-day GO/NO-GO spike to a ~1-hour smoke test; the ADR-001 risk is
not mitigated but **removed**. The SQLite file format is stable for 20+
years (archival-grade) — aligned with the retirement horizon. Revisit only
if a real multi-hop traversal need or a >1M-row table appears (V2+);
that decision would supersede this ADR.

---

## ADR-005 — Local exploitation: three fronts, one command layer

**Status:** accepted.
**Date:** 2026-07.

**Context.** Simple, relevant daily exploitation is vital. Telegram alone
is a narrow pipe (20-row tool cap, no tables, no charts) and the raw
SQLite file, while open, is not an interface. Meanwhile the single-writer
rule must survive any new write path.

**Decision.** One command layer, three fronts:
- `ops/commands.py` — every user action (accept/reject proposals, feed,
  note, enable/disable, drawdown, manual runs) = validate →
  UserDecisionEvent → Writeback. The Telegram bot, the `invest` CLI and
  the dashboard are thin clients of this layer.
- **Reads direct, writes through the agent**: SQLite WAL gives concurrent
  readers for free, so CLI/dashboard read the live file; writes go only
  through the running agent's serialized asyncio path via a localhost-only
  aiohttp API (127.0.0.1:LOCAL_API_PORT). Agent down → read-only mode.
- Dashboard: server-rendered HTML + vanilla fetch + inline SVG — no build
  step, no CDN, no new framework (aiohttp is already a dependency).
- Power-user escape hatch: read-only SQL console (keyword blacklist,
  LIMIT 5000 sanity cap — the Worker's 20-row cap is a guardrail for the
  LLM, not for the human owner).

**Consequences.** Every mutation, from any front, carries the same audit
trail and passes the same gates — no side-channel around the command layer.
Adding a future front (e.g. iOS shortcut) = one more thin client.
Hardening (2026-07 pass): `X-Ops-Token` header (file-based, chmod 600) on
every API call — localhost binding alone does not stop browser CSRF;
command layer idempotent across fronts; single-flight run-lock over
{catchup, chain, uc8, replay}; long ops are async jobs; `feed`/`note`/
`backup` stay available agent-down (filesystem/read-only operations).

---

## ADR-006 — Fully autonomous V1 cognition: no user-validation gate

**Status:** accepted.
**Date:** 2026-07 (supersedes the "Innovation requires user validation" and
"Never integrated without `user_validated=True`" rules stated across
../CLAUDE.md / USE_CASES / TASKS).

**Context.** The original design gated every new invariant, strategy and
metric behind an explicit user validation (`status=proposed` → Telegram/CLI
yes/no → `integrated`). Two later decisions hollowed that gate out entirely:
(1) the maturation redesign made VERACITY a **mechanical** verdict — an
invariant "survived the test of time" iff `confrontations ≥ N_min (3) AND
market_score ≥ θ (0.60) AND not refuted`, computed over 35y at birth (see
ARCHITECTURE "Birth maturation"); (2) dedup and well-formedness are already
mechanical. Nothing substantive was left for the human to judge — the owner
is explicitly not positioned to adjudicate market theses, and being asked to
click "validate" on pre-vetted, already-scored candidates is friction with no
information added.

**Decision.** V1 agent cognition is **fully autonomous — the agent is never
solicited for validation.** The invariant/strategy lifecycle is 100 %
mechanical:
- `status`: `proposed` (maturing) → `integrated` (time-validated: N_min/θ,
  not refuted) → `rejected` (refuted: ≥4 confrontations, market_score < 0.35).
  **No `validated` step, no `user_validated` field, no Telegram/CLI approval
  flow.** Same path for every provenance — corpus, agent-discovery, user
  note, UC3 event (agent-discovery is scored identically; its heavier
  in-sample bias is a self-correcting prior, ARCHITECTURE point-in-time note).

**Amendment (M5, 2026-07-15) — verdict convergence: the dead middle rejects
on confidence.** As originally stated, the verdict had an absorbing middle:
rejection required `market_score < 0.35` ("actively harmful") and integration
`≥ θ (0.60)`, so an invariant measuring 0.35–0.60 stayed `proposed` FOREVER —
at any N. On the real 35y maturation, 4 of 6 seed invariants landed there
(e.g. 0.545 on N=354, upper 95% bound 0.588: demonstrably unable to ever
reach θ, yet never qualified). That violates this ADR's own doctrine
("Nothing stays proposed forever") and, since realloc gate 6 cites
`integrated` invariants only, starves the citation loop. The owner's ruling:
do NOT relax V1 constraints (gate 6 stays integrated-only) — make the engine
QUALIFY instead. A second mechanical rejection branch is added:
- `rejected` (inadequate) iff `confrontations ≥ 4` AND a true rate of θ would
  produce evidence this bad at most `1 − invariant_verdict_confidence` (0.05)
  of the time — "given ample evidence, this invariant demonstrably cannot
  reach the bar". Baseline-relative scoring (ARCHITECTURE "Invariant
  confrontation rule") is what makes this test sound: the null is 0.50 for
  every handle. (Stated with a Wilson upper bound at M5; restated as the
  exact binomial tail by the M5-bis amendment below, which leaves this
  branch's verdicts on the real board unchanged.)
`proposed` now means exactly one thing — INSUFFICIENT EVIDENCE — and empties
mechanically as confrontations accrue. The verdict stays stateless
(recomputed from current counts), so a rejection is as reversible as the
evidence that produced it. Formula: ARCHITECTURE "Birth maturation"
TIME-VALIDATION VERDICT.

**Amendment (M5, 2026-07-15) — an author-claimed status is never honoured.**
This ADR says the engine decides `status`, but nothing enforced it: every
maturation path that cannot produce a verdict (reference knowledge, gate
demotion, no benchmark) returns before the verdict is persisted, so a
`status` supplied at birth silently stood. Authors DO supply it — the
owner-submitted gold invariant arrived `status='integrated'` with
`validated_at` set and a hand-authored `market_score: 0.78` (itself
inconsistent with its own 4/2 counts) — and gate 6 cites `integrated`
invariants, so an unmeasurable claim could have moved money on its author's
say-so. That is the precise failure this ADR exists to prevent. Every
uncertifiable path now forces `status='proposed'` and clears `validated_at`
(`mechanical/invariants.py::_force_uncertified`); supplied evidence is kept
as provenance in `source`/`trace`, never as engine state. Belief does not
grant integration — including the author's belief about their own invariant.

**Amendment (M7, 2026-07-21) — reference knowledge is the one thing that DOES
stay `proposed` forever, and that is correct.** This ADR's "nothing stays
proposed forever" governs claims the engine can JUDGE. Reference knowledge —
an Invariant with empty `condition` and no `effect` (DATA_MODELS: "a ponctual
fact is NOT a new entity") — carries nothing to confront, so no verdict is
reachable, ever. It is not stuck awaiting one; it is outside the verdict
machinery by construction. `_force_uncertified` already routes it there with
the reason `reference knowledge: no effect to measure`, keeping
`market_score = 1.0` and `validated_at` NULL.

The alternative — a fourth `status` such as `reference` — was considered and
rejected: it buys a slightly more honest column at the cost of a schema
change, a migration, and a new value every consumer must learn, when
`condition = '[]'` already identifies these rows exactly. The rule stands with
one stated exception rather than a new mechanism.

Scale, so this is not a corner case: the M7 full-corpus run persisted 209
reference notes against 34 weighted invariants. Most of what a book yields is
knowledge the engine cannot measure — that is the normal outcome, not a
degradation.

**Amendment (M5-bis, 2026-07-15) — integration requires EVIDENCE, not just a
score above θ.** The M5 amendment above put a confidence test on the
REJECTION branch but left INTEGRATION a bare point test (`N ≥ N_min AND
score ≥ θ`). That is not a test at all at small N: it gets EASIER the less
evidence there is. P(score ≥ 0.60 | the invariant has NO edge whatsoever) is
**50% at N=3** (2 of 3 confirmations is a coin flip), 21% at N=14, 25% at
N=20, and only 3% at N=82. So the engine was certifying luck, and had already
done it: `inv-inflation-persistence-tips` sat `integrated` on 9/14 — a 21%
coin, its interval straddling the null — and realloc gate 6 cites `integrated`
invariants, so it was one Monday away from a live money proposal on evidence
indistinguishable from noise. Worse, the incentive ran BACKWARDS: a narrower
condition yields fewer moments and so passed more easily, meaning the engine
mechanically **rewarded over-fitting** — the exact pathology it exists to
catch — with no user gate downstream to intercept it (this ADR). Integration
now requires both clauses:
- `integrated` iff `confrontations ≥ N_min` AND `market_score ≥ θ` AND
  `P(X ≥ confirmations | N, invariant_null_score)` ≤ `1 −
  invariant_verdict_confidence` (0.05), X binomial — "the 0.50 null is an
  implausible source of evidence this good". θ asks *is it worth acting on*;
  the tail asks *do we know it at all*. Both, always.
Discovered while testing a 12-month horizon for the liquidity invariant: it
"integrated" at 12/20 = exactly θ, a pass a coin delivers 25% of the time —
the verdict was tracking N, not skill.

The tails are EXACT (binomial), not the normal-approximation interval the M5
amendment named: Wilson is liberal at extreme rates with small N, precisely
where the defect lives — `wilson_lower(3,3) = 0.526 ≥ 0.50` would still have
integrated a 3-for-3 invariant that a coin reproduces 12.5% of the time. The
exact tail sets the smallest perfect record at 5/5 (0.031) and leaves every
rejection on the real board unchanged. Both branches are stated as exact
tails for one device, not two.

The bar stays REACHABLE — this is not a de-facto ban: a true-0.65 invariant
qualifies on ~30 moments (~7y of active condition at a 12w horizon), and the
real gold invariant clears it today at 53/82 (tail 0.005). It is also not an
absorbing state: as N grows the null tail collapses above θ (integrating) and
the θ tail collapses below it (rejecting), so "Nothing stays proposed
forever" still holds — only the measure-zero true rate exactly AT θ stalls.
Cost, accepted: the board drops from 2 integrated invariants to 1. An
`integrated` stamp that is 21% noise is worth less than no stamp.

Corollary (`mechanical/invariants.py::maturation_fingerprint`): a verdict
belongs to the RULE it was earned under, exactly as it belongs to its
definition. The M5 fingerprint keyed on `(condition, effect)` only, so this
amendment would have left every already-matured invariant sitting on the
verdict the OLD bar gave it — including TIPS, the one it exists to catch.
The fingerprint now digests the verdict rule (horizon, margin, bars,
confidence, null) too: change a rule, everything re-matures.
- New strategies auto-enable after mechanical probation
  (`strategy_probation_weeks`); no human gate.

  *Amendment 2026-08-02 — the probation was unreachable as built.* The verdict
  reads the strategy's FAVORS standing; FAVORS come from Backtests, which need a
  NAV, which needs a Portfolio — and an innovation-born strategy had none, while
  the Backtest sweep only saw `enabled = 1`. Both ends were closed, so the
  no-evidence branch wrote no OutcomeEvent and the strategy returned to the due
  list every week, forever: the exact state this ADR forbids. The fix is to make
  it MEASURABLE rather than to time it out — a CANDIDATE Portfolio is created at
  birth from the spec's base scenario (disabled, so it measures without holding)
  and the sweep now reads `enabled = 1 OR status = 'proposed'`. Timing out is the
  BACKSTOP only, for a candidate that genuinely cannot be measured (no usable
  base allocation, no prices): after 2 × `strategy_probation_weeks` with no
  FAVORS at all it is closed as unmeasurable. A timeout alone would have
  satisfied the letter of "nothing stays proposed forever" by killing every
  innovation, which is not what the rule is for. Not amended: "no current
  regime" still waits without bound — that is a system-wide outage one detection
  run repairs, not a defect of the strategy.
- The **weekly digest reports** what changed; it never asks. It is a passive
  report the owner reads, not a gate that blocks.

**The V1/V2 boundary no longer runs through a validation gate — it runs
through real-world execution.** In V1 the agent is autonomous *internally* and
emits paper-mode `Proposal` vertices only; **the owner is the sole hand that
places real orders**, at will, on reading the digest. That manual-execution
step is the human boundary. V2 = auto-execution, which would supersede this.

**Consequences.**
- The former "Curation vs Innovation" rule (once a CLAUDE.md section, now
  folded into its "No user gate" rule) collapses: the curation/innovation
  distinction no longer implies a user gate — both are mechanical; only the
  author-tier floor and the dedup gate differ.
- The command layer (ADR-005) stays, but its user actions are **preferences
  and overrides** (enable/disable a strategy, set drawdown, feed a document,
  trigger a run) — never "validate the agent's knowledge."
- Residual risk (accepted): an over-fit agent-discovery invariant integrates
  without a human filter and can color a **paper** digest recommendation.
  Bounded because nothing auto-executes, forward confrontation refutes it,
  and its weight stays continuous. The stricter lever (discover on 15y /
  validate on the 10y held-out split Phase 9 already uses) is available if a
  concrete failure ever justifies it — not needed for V1.
- DoD item 6 changes: an agent-discovery invariant is persisted and matured
  mechanically; the digest surfaces it — no `status=proposed`-awaiting-user,
  no validation notification.

---

## ADR-007 — Adopt the market-signal monthly stack as V1's operating strategy

**Status:** accepted (owner sign-off, 2026-07-20). Authorizes the CLAUDE.md/
docs revisions and the M6-bis wiring below.
**Date:** 2026-07-20.
**Supersedes (for ALLOCATION only):** the seeded Dalio 4-quadrant
portfolio-rotation as the thing that decides the allocation. Full spec + impact
map + roadmap: docs/V1_STRATEGY.md. Evidence: docs/STRATEGY_COMPARISON.md.

**Context.** The M6 mechanical premise gate found the seeded 4-quadrant
rotation does not beat B (risk-parity All Weather) on return; the whole
post-M6 exploration then measured, on the live 35y DB, that a countercyclical
market-signal stack (Verdad/Rasmussen) DOES: credit-spread + yield-slope regime
selecting concentrated books (small-cap value + IG credit, added to the menu
this pass) with a 200-day trend-following overlay, evaluated MONTHLY, returns
9.85%/yr vs B 7.27% (+2.5, robust in AND out of sample), Sortino ≈ B, max
drawdown -24% (daily), ~3.4 changes/yr. Monthly beats weekly on every practical
axis (fees, Swiss tax holding period, manual execution) at ~no return cost. The
lagged CPI/GDP detector that drove the old allocation was diagnosed as the root
cause of the failure (I-38): it labels macro-publication regimes, not the market
regimes the books are designed for. NB: this is BACKTEST evidence on a window
consulted heavily; the only validation that counts is forward paper-mode.

**Decision.**
1. The V1 operating strategy is the **market-signal monthly stack**
   (defined in docs/V1_STRATEGY.md): credit-spread(BAA10Y)/slope(T10Y2Y) regime
   → 3 concentrated books → 200d trend-following overlay, MONTHLY decision, no
   VIX overlay.
2. The old cognitive core (macro detector M3, FAVORS M5, UC7 ranking, UC8
   switch/reallocation blend, scenarios, the 7 Dalio books, weekly decision
   cadence) is **DEMOTED, not deleted** — kept wired as fallback + benchmark
   until forward paper-mode earns the full switch. Passing the crossroads is
   not burning the bridge.
3. The infrastructure (data pipeline M2, NAV M4, SQLite/EventLog M1, the
   binding-cap gates, the replay/calibration harness, the corpus/invariant
   factory M7, the Planner/Worker M8) is unchanged. The knowledge factory is
   framework-agnostic; only what it ORIENTS changes.
4. **Drawdown binding cap raised from -15% to -25%** (user_profile.
   max_drawdown_pct), and REINTERPRETED for this stack: the cap applies to the
   STACK's realized drawdown, not each component book's standalone drawdown.
   Rationale: a 10-15y ACCUMULATION horizon tolerates a deep-but-recovering
   trough (drawdowns are buying opportunities with recovery time); the stack's
   -24% then complies. OWNER CAVEAT ON RECORD: -25% leaves ~1pt margin over the
   backtested -24%; real crises exceed backtested minima, so a worse-than-
   history tail will likely breach it — -30% was offered for buffer and
   declined in favour of -25%. [If the owner prefers -30%, change here before
   accepting.]
5. Forward **paper-mode (M9)** is the go-live gate for this strategy, exactly
   as M6 was the backtest gate. Auto-execution (V2) only after forward
   validation.

**Consequences.**
- ADR-002/003/004/005/006 are all unaffected.
- The weekly-Monday-chain rule (CLAUDE.md, ARCHITECTURE) changes for the
  DECISION step only (→ monthly); catch-up/NAV/regime-step jobs keep their
  natural frequency. This is the one existing-doctrine change and is scoped
  here.
- CLAUDE.md sections (ranking rule, FAVORS, regimes, UC8, binding caps),
  ARCHITECTURE, USE_CASES (UC7/UC8), DATA_MODELS, MILESTONES get revised UNDER
  this ADR's authority once accepted — they are not silent contradictions,
  they execute this decision.
- Open owner items still to resolve before M9 go-live: the Swiss quasi-
  professional tax status (median 61-day holding still < the 6-month safe
  harbor — confirm with a fiduciaire) and the monthly-compatible drawdown
  brake IF -25% ever needs defending in a live tail.

**Addendum (2026-07-20, owner sign-off) — single-asset cap 40% → 50%.**
Surfaced while wiring M6-bis: the market-signal books deliberately hold 50% single
sleeves (growth & inflation SPY 50, slowdown VCIT 50), which breach the binding
`user_profile.max_single_asset_pct = 40`. That 40% cap was calibrated for the
DIVERSIFIED Dalio portfolios; the pivot's whole thesis is CONCENTRATED
countercyclical books, and that concentration is the measured source of the
+2.5-vs-B edge. Decision: **raise `max_single_asset_pct` to 50%** (config.py
default, .env, live `user_profile` — the three canonical places, same as the
-25% drawdown). The cap still BINDS (Writeback blocks any sleeve > 50, and
per-portfolio rules may still be stricter); it is only re-levelled for the
concentrated books. The old diversified books are unaffected (their largest
sleeve was 40). This preserves the validated 9.85%/+2.5 numbers exactly rather
than re-capping the books and drifting them. Alternatives weighed and declined:
cap-books-to-40 (would drift the backtest) and exempt-the-whole-market-signal stack
(would weaken "binding caps bind ALL candidacy" too broadly).

**Second addendum (2026-07-20, owner sign-off) — trend-haven sleeve exempt from
the single-asset cap.** Surfaced by the M6-bis cap confrontation: at ~10 of the
119 decision dates (risk-off — 2008-09, 2020, 2022...) BOTH SPY and GLD are
below their 200d MA, so the trend overlay redirects both into IEF, concentrating
the HAVEN to ~90% — breaching even the raised 50% cap. That concentration IS the
drawdown control (the deliberate flight to safety), and the validated 9.85%
includes it. Decision: the **trend-haven sleeve (IEF) is EXEMPT from the
single-asset cap** in the market-signal path (`gates.concentration_ok(..., exempt=
{IEF})`), chosen over splitting the excess into SHY/cash. Rationale: SIMPLICITY
— the haven is structurally a safety redirect, not a conviction bet, so the
"single-asset" cap's intent (bound concentrated BETS) does not apply to it;
exempting preserves the validated numbers exactly with no re-validation. Scope
is NARROW and explicit: only IEF, only via the documented `exempt` argument; the
cap still binds every other sleeve and every seeded-portfolio proposal (the
`exempt` default is empty). This is why it does not reopen the "binding caps bind
ALL candidacy" principle — it is a named exception, not a hole.

**Second addendum, extended (2026-08-08, owner sign-off) — the exemption covers
the whole haven CHAIN, cash included.** The addendum above says "only IEF", and
that WAS the whole chain when it was written. On 2026-08-07 the haven itself
became trend-checked with a cash fallback (when IEF is below its own 200d, the
redirect goes to cash, which cannot fall) — and the exemption did not follow the
new destination. Measured on the M8b agentic run of 2026-08-08: at four of the
seven inflation-shock dates (2022-03-01, -05-02, -06-01, -07-01) all four
checked instruments were below trend, the overlay produced the 100%-cash target,
and `max_single_asset_pct` refused it. The stack held its stale book through the
2022 drawdown.

A SECOND block sat behind it, found only by widening the test that was supposed
to prove no reachable state is refused: `cash` is not in `allowed_tickers`
(those are instruments with a price series), so the gate refused the fallback as
an unknown sleeve at ANY weight, not just at 100%. The log named only the cap
because that gate runs first.

Decision: `HAVEN_EXEMPT = {IEF, cash}`, defined once in `market_signal.py` and
used by both enforcement sites, and the cash fallback is accepted by the
tradable-ticker check. Rationale: ADR-009's own argument, transferred from the
drawdown leg to the concentration leg — refusing a proposal cannot exit a
position, only FREEZE one, and the proposal being refused here is precisely the
overlay's flight to safety. Cash at 100% is not a conviction bet; it is the
absence of one, and the only destination left when every checked instrument is
falling. Scope stays narrow and named: the cap binds every other sleeve and
every seeded-portfolio proposal, the `exempt` default is still empty.

**Second addendum, extended again (2026-08-09, owner sign-off) — the haven
exemption also binds the COGNITIVE path.** Measured at 2008-10-01 of the
on-stack M8b run: the incumbent was the stack's own IEF 100 (legal here, exempt
since the addendum above), and the Worker proposed IEF 72.5 / TLT 12.5 / GLD 10
/ cash 5 — a DE-concentration. `reallocation_gates` refused it on
`max_single_asset_pct`, and the 100% stayed. The gate could only freeze the
breach it would not let the Worker reduce.

ADR-009's argument, arriving in a third place. Decision: `reallocation_gates`
takes an `exempt` set and Writeback passes `HAVEN_EXEMPT`, the same chain the
mechanical path uses. Rationale: the exemption is a property of the SLEEVE, not
of the path — a haven concentration is a safety redirect, not a conviction bet,
whoever proposes it.

SCOPE, stated plainly because it is wider than the case that prompted it: this
loosens a binding cap on EVERY cognitive reallocation, the bridge defender's
included. A Worker proposal may now hold more than `max_single_asset_pct` in
IEF or in cash. Every other sleeve is still bound, the `exempt` default is
still empty, and a conviction bet on equities is refused exactly as before.

**Third addendum (2026-07-20, owner sign-off) — the 3 books are renamed after
the SIGNAL STATE, not after a macro regime.** The books were seeded as
`growth` / `inflation` / `slowdown`, names that assert a macro reading they do
not carry. Measured over the 418 monthly decisions (docs/IMPROVEMENTS.md I-39):
the market signal is essentially ORTHOGONAL to CPI — each book spends 28-33% of
its time with CPI YoY above 3% against a 31.3% base rate, and the book called
"inflation" averages CPI 2.99 versus 2.23 for the one called "growth". The names
were therefore false in the one dimension they claimed. Since the Worker is an
LLM that reads book names and decision keys as semantic context, this is a
reasoning hazard, not cosmetics.

Renamed: `growth` → **`credit-spread-wide`** (credit spread WIDE vs its 10y median —
stress is priced, so the countercyclical response is to buy risk), `inflation` →
**`credit-spread-tight-yield-curve-flat`** (spread TIGHT, slope FLAT), `slowdown` → **`credit-spread-tight-yield-curve-steep`**
(spread TIGHT, slope STEEP). This touches the `BOOKS` keys and
`classify_regime`'s return in `mechanical/market_signal.py`, and the `name` /
`trace` of the three seeded portfolios.

**Entity IDs are deliberately NOT renamed** (`ms-growth-book`,
`ms-inflation-book`, `ms-slowdown-book` are frozen). Those ids already appear in
committed EventLog payloads (ValuationEvent, RankingEvent), and EventLog is
append-only — rewriting them is forbidden, while leaving the log pointing at ids
the tables no longer hold would be precisely the audit-trail/state divergence
EventLog-first exists to prevent. The id/name mismatch is the deliberate cost of
that guarantee and is commented at the seed site. Zero allocation change: the
weights are untouched and the anti-drift replay still reproduces 9.85% CAGR /
-23.8% maxDD.

**Fourth addendum (2026-08-01, owner sign-off) — the book switch waits for
3 confirming decisions; the pinned pair becomes 11.26% / -23.8%.**
`classify_regime` compares each signal to a trailing median and nothing else, so
a signal hovering at its own median flips the book on an arbitrarily small
difference. Measured over the 409 monthly decisions: of 36 book changes, **25%
were decided by a margin under 2% and 14 reversed within 3 months** — while a
flip between the wide and steep books is a ~90% round trip (they share only
IWN). The 4-quadrant detector this pivot DEMOTED from allocation has both a
noise dead-band and 3-print hysteresis; the path PROMOTED to allocation had
neither.

`market_signal.CONFIRM_DECISIONS = 3`: the stack holds its book until a
different one has been signalled 3 monthly decisions running. Measured on the
same window, same NAV engine, same 20bps costs: **CAGR 9.85% → 11.26%, Sortino
0.94 → 1.11, max drawdown UNCHANGED at -23.8%**, turnover 53.9x → 42.0x.

Why this is not a curve-fit: the improvement holds in **both halves of the
history split independently** (10.76%/1.00 then 10.43%/1.07 at confirm 2,
rising through confirm 4); the sweep **degrades past ~4 and collapses at a 50%
dead-band** (-34.8% drawdown), so there is a real interior optimum rather than a
monotone "trade less" gradient; and buy-and-hold — the zero-turnover control —
scores a higher CAGR (10.32%) but Sortino 0.74 and **-52% drawdown**, so the
edge is not the absence of turnover. The literal "hold both candidate books
while undetermined" variant measured WORSE than simply waiting, and raised
turnover; it was rejected.

Cost: up to 3 months late on a genuine turn. The drawdown does not degrade
because the 200d trend overlay is deliberately NOT damped — it re-reads on every
decision, so the drawdown control keeps reacting while the book waits.

3 is `regime_confirm_prints`' value, reused as a convention, but kept as a
SEPARATE constant: recalibrating the macro detector must never silently move the
allocation. Timing note: the live monthly decision path was not yet wired
(M6-bis "Remaining"), so this cost no migration. **The pinned anti-drift pair is
now 11.26% / -23.8%**; 9.85% is history, not a target.

---

## ADR-008 — `proposal` accommodates the market-signal path by NULL, not by convention

**Status:** accepted 2026-07-21 (owner decision).

**Context.** The `proposal` table was designed for the ranking-based V1: a
ranked defender meets a ranked challenger and the `gap` between them justifies
the switch. ADR-007 replaced that with the market-signal monthly stack, whose
decision has no rank, no challenger and no gap — it has a signal state and a
book. Two columns block persistence: `defender_rank INTEGER NOT NULL` and
`gap TEXT NOT NULL`.

**Options considered.**
(a) Make the ranking columns nullable and add `proposal_type='market-signal'`.
(b) Fill them by a documented convention (defender = live book, rank 1,
    gap = signal state).

**Decision: (a).**

The argument is not cost — though cost points the same way: `proposal` is
EMPTY (0 rows, verified 2026-07-21) and we are pre-go-live, so
`CREATE TABLE IF NOT EXISTS` absorbs the change for free, while after go-live
it would open the numbered-migration convention.

The argument is meaning. Under (b), `gap` holds a ranking gap for one
`proposal_type` and a signal state for another. Every later reader — digest,
CLI, dashboard, `outcomes.py` at +12w — would have to know the type before it
could interpret the column. That is implicit coupling of the kind this project
spends its comments avoiding, and the Zen of Python it adopts says the
opposite. NULL states plainly what is true: these fields do not apply to this
kind of proposal.

Note what does NOT need special handling: a market-signal proposal has a
natural `defender_id` (the currently live book) and a natural
`proposed_allocation`. Only rank and gap are artefacts of the duel ADR-007
removed, and they are exactly the two made nullable.

**Consequence.** Readers must treat `defender_rank`/`gap` as optional and
branch on `proposal_type`. Any gate or renderer that assumes a rank must say
so. The ranking path (RETAINED BRIDGE, ADR-007) keeps writing both columns
unchanged, so nothing about the fallback/benchmark path is weakened.

---

## ADR-009 — The market-signal drawdown rule ALERTS; it never blocks

**Status:** accepted 2026-08-02 (owner decision, after measurement).

**Context.** ADR-007 raised the binding drawdown cap to **-25%** and scoped it
to "the STACK's realized drawdown, not each book standalone". When the live
monthly path was first wired, that cap was implemented as a Writeback *gate*:
a decision whose drawdown breached it would be refused and no Proposal written.
Two problems surfaced on inspection, both measured rather than argued.

**1. The gate could never fire, and could not have protected anything if it
had.** It was fed the drawdown of the 35-year BACKTESTED stack NAV (-23.8%), a
historical constant, because no realized stack NAV existed — the 3 book
Portfolios carried NAV series for their *static* allocations, and the stack,
the only object anyone actually holds, had none. Enumerated over all 12
reachable book x overlay states, no gate in the market-signal set can refuse a
decision produced by the MARKET; only a config or code change can trip one.

**2. Blocking is the wrong instrument, and would have sold the bottom.** A
refused gate writes no Proposal, so no order reaches the owner and the stack
stays exactly where it is. Blocking can FREEZE a position; it can never exit
one. Worse, during a drawdown the proposal being blocked is precisely the 200d
overlay's flight into IEF — the mechanism that carries the stack's -23.8%
instead of -50%. Measured over the only three episodes in 35 years:

| peak | trough | depth | holding at the trough | recovery |
|---|---|---|---|---|
| 2008-09-22 | 2009-03-09 | -23.2% | 50% IEF (overlay had fled) | 332d |
| 2020-02-24 | 2020-03-20 | -23.8% | SPY 50 / GLD 40 / IWN 10 (**overlay had not moved**) | 185d |
| 2021-12-29 | 2022-10-20 | -22.4% | 90% IEF (overlay had fled) | 831d |

The stack never once reached -25% (54 days total below -20%). In the one
episode where the overlay was too slow — 2020, a 25-day fall against a monthly
decision clock — a -25% trigger would have fired on **2020-03-20, the exact
bottom**, selling into the trough and forfeiting the 185-day recovery. A
drawdown trigger on a monthly cadence arrives mechanically after the hole.

**Decision.**
- The stack gets a real `portfolio_nav` series (`ms-stack`), built by
  `shadow_book_nav` from the change-point map rather than by `synthesize_nav`,
  whose constant weights cannot express a rotating strategy. Its indicators come
  from the same pinned `rolling_*` formulas as every portfolio it is ranked
  against.
- The drawdown rule is measured on that series over the **36-month rolling
  window** (756 trading days) — the project's existing window for every
  `*_rolling` indicator, so no new convention is introduced and the stack stays
  comparable to the ranking rows. A since-inception maximum was rejected: it is
  monotone, so a single bad year would pin it forever and it would stop
  describing the present. The rolling window subsumes it anyway, since the
  pinned convention grows the window until 756 days exist.
- Breaching -25% raises a **critical alert** in the digest
  (`mechanical/alerts.py`) and changes nothing the system does. The protection
  remains the 200d overlay.
- `market_signal_gates` keeps only sum-to-100, the single-asset cap (with the
  haven-chain exemption: IEF **and the cash fallback** — ADR-007's second
  addendum as extended 2026-08-08) and allowed-tickers (which the cash fallback
  is exempt from too, cash having no price series), documented for what they
  are: **regression guards** against a config or code change, not a safety
  control.

**Also recorded here, previously only in code comments.** The market-signal path
is exempt from the 4-week anti-repetition cooldown, from UC8-B gate 6
(cited-invariant eligibility), from `max_turnover_pct` and from
`min_allocation_change_pts`. Reasons: the book is chosen by a market-priced
signal validated over 35 years, not argued from a cited lighthouse, so gate 6
has no input (and only 2 invariants are citable today — MILESTONES M8, so it
would block nearly every month); the cooldown would suppress the overlay's
re-entry, which IS the drawdown control; a book switch is a ~90-100% turnover
move by construction against a 30% ceiling, so that cap would block every switch
the strategy exists to make. The min-change floor survives in a stricter form —
a proposal is emitted only when the target differs from what is actually HELD.
CLAUDE.md's "plus a 4-week anti-repetition cooldown" is hereby scoped to the
switch/reallocation paths of the retained bridge.

**Consequence.** Nothing in the live allocation path can refuse a decision on
market grounds. That is deliberate and now explicit: V1 never auto-executes, the
owner places every order, and the system's job at -25% is to tell them clearly —
not to withhold the instruction that would have moved them to safety.

**Addendum, 2026-08-02 (coherence pass on the implementing commit).** Three
things this ADR asserted turned out to be imprecise or unguarded once the code
was read back. None reverses the decision; all three change what it is safe to
believe about it.

1. **"36-month rolling drawdown" is the DEEPEST drawdown inside the trailing
   756 days, not today's distance from a 36-month high.**
   `ratios.rolling_max_drawdown` is `min(NAV/cummax(NAV) - 1)` WITHIN the
   window. A stack at an all-time high still reports -30% if it fell that far
   two years ago. Two consequences the ADR did not state: the alert's message
   must not read "is X% below" (it did, and was corrected), and once breached
   the alert re-fires **every Monday until the episode ages out of the window —
   up to three years, including after a full recovery.** That is accepted for
   now: the alternative measures (current drawdown from the trailing peak) would
   introduce a second drawdown convention for one alert, and the historical
   stack never reached -25% at all. Revisit if it ever fires.
2. **The series it is measured on is PAPER, not realized.** ADR-009 says the
   stack "gets a real `portfolio_nav` series"; what it gets is
   `shadow_book_nav` over the decision walk, which assumes every monthly target
   filled at the close of its anchor date — no slippage, no delay between the
   digest and the owner's order, no partial fill. That is the honest instrument
   available while V1 executes nothing (ADR-006), and it is comparable to the
   ranking rows because they are priced the same way. It is not a statement
   about the owner's account, and `alerts.py`, `persist_stack_nav` and the
   digest now all say so in those words. Closing the gap is forward paper-mode
   (V1_STRATEGY Step 6).
3. **Making the digest the only safety organ makes the digest safety-critical.**
   If nothing can refuse a decision, then everything rests on the owner being
   TOLD — and two defects in that path were found in the same pass: the market
   signal decision competed with the bridge's UC8 reallocation for one digest
   proposal slot (losing it on exactly the months it moved money), and
   `mechanical/alerts.py` shipped with no tests at all. Both fixed
   (`tests/test_alerts.py`; the decision now renders from its journal, which has
   one row per decision date whether or not money moved). The standing rule this
   sets: **anything ADR-009 delegates to the alert path carries the test burden
   a gate would have carried.**

---

## ADR-010 — Every NAV is charged the same trading cost, at one rate

**Status:** accepted 2026-08-02 (owner decision: "tout le monde au même régime,
comparaison fair").

**Context.** ADR-009 put the market-signal stack into the UC7 ranking as a real
`portfolio_nav` row. That immediately exposed an unfair comparison: the stack's
NAV was net of trading costs, while every static book's NAV was **gross** —
`ratios.synthesize_nav` rebalances monthly to target and charged nothing for it.
The one rotating strategy paid for its trades; the six books it was ranked
against traded for free.

Measuring it surfaced a second problem — the system held **two different cost
rates for the same thing**:

- `system_thresholds.replay_cost_bps = 10` (per side), used by the replay arms
  and by `outcomes.evaluate_proposals`;
- `market_signal.COST_BPS = 20` (per side), used for the stack.

Both feed the same `sum(|delta weight|) x bps` formula, so the stack was paying
**40 bps a rotation** — double the "net 20 bps/rotation" that
docs/V1_STRATEGY.md says its pinned numbers are net of, and double what the
replay charged the arms it was compared against.

**Decision.**
1. **One rate: `ratios.TRADING_COST_BPS = 23.0` bps per order** — Saxo's actual
   commission on the owner's account (owner-supplied, 2026-08-02). NOT the
   documented 10: the documented figure was an estimate, and an estimate that
   disagreed with itself in two places. `system_thresholds.replay_cost_bps` is
   moved to 23 alongside it and MUST stay equal — a replay that validates a
   strategy and a ranking that compares it have to charge the same rate.
   There is no FX leg: every portfolio here is USD, held in a USD account, so a
   rebalance triggers no CHF conversion.
2. **Every NAV pays it** — static books, the All-Weather benchmark, and the
   stack. A benchmark charged nothing is an alternative nobody can actually buy.
   *(This read "every PERSISTED NAV" between 2026-08-02 and 2026-08-14, because
   the temporary NAVs `outcomes._window_return` builds to score a proposal at
   +12w charged the switch but not the drift-rebalances inside the window. I-45
   is now CLOSED by charging them rather than by narrowing the sentence, so the
   literal wording holds again: `_window_return` takes `cost_bps` and both its
   callers pass `system_thresholds.replay_cost_bps`. Measured at 0.66 bp over 12
   weeks at 10 bps — ~1.5 bp at the live 23 — on BOTH legs, so no verdict moved,
   which is why the choice was free to be made on consistency alone.)*
3. **The monthly drift-rebalance is billed too**, in BOTH engines. It was free
   in `shadow_book_nav` on the reasoning that "both arms pay it equally, so
   charging it would only add noise to A - B" — true inside the replay, false
   once portfolios are ranked against each other. A rebalance is a real order
   either way.

**Measured impact.** Small, and it does not rescue or damage any conclusion.

| | CAGR | Sortino | maxDD | turnover |
|---|---|---|---|---|
| stack, gross | 11.80% | 1.17 | -23.8% | 42.0 |
| stack, old (20/side, drift free) | 11.22% | 1.11 | -23.8% | 42.0 |
| stack, interim estimate (10/side) | 11.51% | 1.14 | -23.8% | 42.0 |
| **stack, ADR-010 (23 bps/order)** | **11.14%** | **1.09** | **-23.8%** | 42.0 |

Static books lose **0.01-0.04 pt/yr** each: their monthly drift is only ~0.26
sum|dW| a year against the stack's 1.21, so the stack carries roughly 10x their
fee drag. That asymmetry is real, and it is the point of charging everyone —
before ADR-010 it was hidden by the books paying nothing. The ranking ORDER is
unchanged. B goes 7.41% gross -> 7.34% net.

**The pinned pair therefore moves: 11.26% / -23.8% becomes 11.14% / -23.8%**,
Sortino 1.11 -> 1.09, and the edge over B is **+3.80 pt/yr with both sides net**
(V1_STRATEGY quoted +4.0 with B gross). Max drawdown and turnover are unchanged.
This is a restatement of the same strategy at its real cost, not a new result —
the anti-drift check now targets 11.14%.

**What this closes, and what it does not.** It closes the cost question: 23 bps
is measured, not assumed, and the stack's edge survives it with room. It does
NOT close execution risk — the backtest assumes every monthly order fills at the
close on the decision date, and the stack's concentration means a single order
can be a large fraction of the book. Slippage is not modelled anywhere, and
forward paper-mode (V1_STRATEGY Step 6) is where it will show up, if it does.

---

## ADR-011 — The mechanical allocation is sovereign; the Worker reads it

**Status:** accepted 2026-08-02 (owner decision).

**Context.** ADR-007 makes the book a MECHANICAL readout: a market-priced
credit-spread/slope signal, hysteresis, a 200d overlay, all validated over 35
years. docs/V1_STRATEGY.md Step 4 says the Worker "nuances the monthly
regime/book decision". Those two sentences were never reconciled, and "nuance"
was never defined. Three questions had no written answer: may the Worker
CANCEL a mechanical decision, DELAY it, or ADJUST its weights?

Reading the code back gave two of the three answers already, and exposed why
the third was only accidentally right.

- **Cancel and delay: structurally impossible, and this is worth keeping.**
  `market_signal_cycle` runs mechanically and reads no `WorkerResult`; there is
  no data path from UC8 back into it. The decision is journalled before the
  cognitive chain even starts.
- **Adjust: prevented by a data flag, not by a rule.** The Worker's only
  allocation lever is `reallocation_proposed`, and `decision_cycle` applies it
  to whichever portfolio carries the `defender` flag. `ms-stack` is seeded
  `defender: False`, so today the lever lands on the retained bridge. But no
  gate looked at WHICH portfolio — caps, turnover and citations all inspect the
  allocation and never its owner. One flag flip and the 0.4/0.6 blend would
  have overwritten the adopted allocation, persisted as a
  `Proposal(reallocation)` with every gate green. **That flip is scheduled**:
  retiring the bridge (V1_STRATEGY Step 6) is precisely when someone makes the
  stack the defender.

**Decision.**

1. **A portfolio whose allocation is produced by a mechanical decision path
   does not accept cognitive reallocations.** Enforced as **gate 0** of
   `writeback.dispose_reallocation`, refusing `mechanical_allocation` when the
   target is in `TIME_VARYING_PORTFOLIOS`. It runs FIRST because it is about
   jurisdiction, not merit: such a proposal must be refused for that reason,
   not for whichever cap it also happened to breach. In Writeback rather than
   in `decision_cycle` so every caller is covered — "Worker proposes, Writeback
   disposes" (CLAUDE.md).
2. **`TIME_VARYING_PORTFOLIOS` is the scope**, not `framework_id`. The frozenset
   already means "allocation driven by another mechanism", the 3 static books
   are unreachable by the Worker anyway (disabled, never defender), and the
   Step-6 case is covered because the stack stays time-varying whatever its
   defender flag becomes.
3. **"Nuance" is defined**: the Worker READS the mechanical decision and
   contributes a qualitative reading — where it looks wrong, what the signal
   cannot see, which invariants argue against it. That reading is journalled
   and rendered. It is not an allocation.

   *Implemented 2026-08-02, after the ADR shipped without it.* The reading has
   its own required field, `WorkerResult.market_signal_assessment`: the prompt
   asked for it ("that reading is your contribution") while the schema had
   nowhere to put it, so it dissolved into `regime_assessment` or `reasoning` —
   neither of which had a reader anywhere. `decision_cycle.journal_worker_reading`
   appends a `WorkerReadingEvent` on EVERY cycle carrying the whole prose surface
   plus the `decision_date` it judges (a reading that cannot be joined to its
   month is not auditable), and the digest renders it as "Worker challenge"
   INSIDE the market-signal block, flagged when it belongs to an older decision.
   The same event closes a second hole it exposed: UC8 had no cycle-level event,
   so a week that proposed and confronted nothing left no trace at all.
4. **Disagreement has one audited channel: `innovations_proposed`
   (`ImprovementType.strategy_revision`).** A Worker that believes the
   INSTRUMENT is wrong — not this month's reading, but the rule — says so
   there, where ADR-006's maturation measures the claim over a window before
   anything is adopted. No fourth `proposal_type` was created: a "derogation"
   proposal would be a parallel cognitive allocation path, which is the thing
   this ADR closes. The system prompt states the distinction, and
   ARCHITECTURE.md's verbatim copy is kept in sync.

**Consequence, stated plainly.** The Worker cannot change what is held. Its
entire influence on the adopted strategy is prose plus a measured innovation
channel, and that is the intended trade: ADR-007 bought a 35-year-validated
signal, and an LLM that could edit its output on a monthly impression would
give back exactly the guarantee that was bought. The cost is real — if the
signal is blind to something the Worker sees, the system will hold the wrong
book for at least one month and possibly a full maturation window. That cost
is accepted because the alternative is unmeasurable: an override applied once
on conviction leaves no evidence either way, while an innovation leaves a
verdict.

**What this does NOT decide.** Whether the bridge's own defender should
eventually become mechanical too. Today it is the retained cognitive path and
gate 0 leaves it untouched, by design — it is the benchmark the adopted
strategy is measured against, and a benchmark the Worker cannot influence is a
different experiment from the one V1 is running.

**Consequence carried to Step 6, stated here so it is not discovered there.**
The Worker's lever is legal only against the bridge defender. Retiring the
bridge (V1_STRATEGY Step 6) makes the stack the defender, and then the lever
has no legal target at all: UC8-B in full becomes unreachable. Gate 0 is
correct in that state — it simply leaves half of UC8 with nothing to do. The
three futures (delete UC8-B / demote rather than retire the bridge / give the
Worker a capped satellite sleeve) are specified in docs/IMPROVEMENTS.md I-46,
which must be settled INSIDE the Step 6 plan. Not decided now on purpose: the
answer depends on whether forward paper-mode retires the bridge at all, and if
the stack fails, the question dissolves.

---

## ADR-012 — The Worker does not allocate. It reads, and it proposes knowledge.

**Status:** accepted 2026-08-09 (owner decision).

**Context.** ADR-011 made the market-signal allocation sovereign and left the
Worker two things it could still move: its own measurement book (`worker-book`,
2026-08-08) and the retained bridge's defender. M8b's agentic replay existed to
answer whether either was worth having — "does the cognitive half add anything
to the capital?" — through an A' curve following the reallocations the Worker
proposed and the gates accepted.

Two days of runs answered it, and the answer is not mainly about performance.

**What the runs measured.**

*Cost and complexity, unambiguous.* Four restarts in one day, and a systematic
audit that found seven defects — six of them in the cognitive ALLOCATION path
and nowhere else: the two measurement books diverging (one ranked, one not; gate
6 recording on one and refusing on the other), the priced walk never carrying
the rule it claimed to tilt, the concentration cap freezing the de-concentration
it refused, the turnover cap never decided for a monthly rotating incumbent, the
cooldown never reconciled with a monthly cadence. The knowledge path produced
one defect in the same period.

*Performance, weaker and honestly so.* On the standalone arm A' - A came to
-10.49% (GFC), -2.32% (covid, an identity — zero reallocations accepted) and
+1.41% (inflation shock). On the on-stack arm, zero accepted reallocations over
16 dates, then one proposal in seven. Seven-month windows: noise as much as
signal, which is what M8b always said it was.

AND THE MEASUREMENT WAS COMPROMISED, which this ADR records rather than hides:
until 2026-08-09 the Worker was never told it held a book at all. `target_book`
reached Writeback and stopped there, and the only reallocation avenue the prompt
offered was a blend formula for a portfolio that had stopped being the target.
So the runs do not show "the Worker allocates badly". They show that this
harness never asked it properly, at considerable cost.

*Where the value actually appeared.* The behavioural channel produced specific,
verifiable, code-checkable critiques of the mechanical rule — including the one
that found `max_single_asset_pct` freezing the stack in a stale book through the
entire 2022 drawdown, a real defect the mechanical half could not see about
itself. That is a knowledge factory that works, bolted to an allocator that does
not convince.

**Decision.** The Worker does not allocate. Anything. `WorkerResult` loses
`reallocation_proposed`; `ReallocationProposal`, `dispose_reallocation` and the
gates that existed only to judge a cognitive allocation are deleted; the two
measurement books are removed; the agentic replay keeps its readings and
innovations and loses its A' curve and its arms.

Its contribution is the market-signal reading, the innovations
(`strategy_revision`, `new_invariant`, `process`), the invariant corpus and the
rule revisions ADR-006 matures — everything that gets MEASURED over time rather
than applied once on conviction.

The bridge defender becomes purely mechanical: `replay.py` still applies the
0.4/0.6 blend through `blend_allocation` and `reallocation_gates`, which stay
for that reason and are untouched by this ADR.

**Consequences.**

- M8b stops being a NAV screen. Its Definition of Verified keeps the channel
  MILESTONES already weighted equally — read the behavioural log — and drops
  "A' beats B". The mechanical premise gate (M6) is unaffected: it never
  involved the Worker.
- ADR-011 is subsumed, not contradicted. Its gate 0 refused cognitive
  reallocations aimed at `ms-stack`; there are now no cognitive reallocations at
  all, so the property it protected holds by construction rather than by a
  check. The reasoning is kept here because it is the reasoning that led here.
- The retained bridge keeps a defender and loses its cognitive half. CLAUDE.md
  called that defender "deliberately cognitive, it is the benchmark"; it is now
  deliberately mechanical, and the benchmark is the cleaner for it — two
  mechanical policies compared, with no LLM variance inside either.
- **Reversible, and the way back is written down.** Reopening means giving the
  Worker a book and TELLING it so — the thing never done. docs/IMPROVEMENTS.md
  I-46 (the capped satellite sleeve) is where that returns if it returns, and
  the runs archived under `~/data/investment/agentic-replay/` are the evidence
  it would start from.

**What this does not touch.** The market-signal stack and every gate on it
(ADR-007, ADR-009), the invariant maturation and its verdicts (ADR-006), the
curator, the corpus, the digest's reading of the monthly decision.

---

## ADR-013 — The Task 9.3 go-live gate no longer gates UC8; forward paper-mode is the gate

**Status:** accepted 2026-08-12 (owner decision, at the start of M9).

**Context.** Task 9.3 gives `main.py` a startup gate: refuse to enable "the
weekly proposal cycle" unless the latest `replay_report` with kind='mechanical'
shows agent-follow ≥ hold-initial-defender over the validation window, net of
costs (override `--force-live`). Gate closed did not mean idle — the chain still
ran and only UC8 was skipped, with the digest saying "proposal cycle disabled
(replay gate)".

The gate's PREDICATE is "do the allocations this agent proposes destroy value
over 35 years?". When it was written that question described UC8 exactly: UC8-A
proposed a defender switch, UC8-B proposed a reallocation of the defender's
book, and both moved money.

ADR-012 removed that object. UC8 allocates nothing: `WorkerResult` carries no
allocation, `dispose_reallocation` is gone, and what the cycle produces is a
journalled reading of the mechanical decision plus knowledge — confrontations,
conviction nudges, scenario probabilities, innovations, and the rule revisions
ADR-006 matures. Meanwhile the path that actually moves money, the market-signal
monthly stack, was never covered by this gate at all: it runs at 08:55, before
UC8, and would decide whether the gate were open or shut.

So the gate as specified would, on the first real Monday, switch off the
knowledge factory while leaving the allocator untouched. On the live database
the point is not hypothetical: `replay_report` holds ZERO rows (verified
2026-08-12), so the gate is CLOSED by default and would stay closed until a
35-year mechanical replay were run into that database — a run whose verdict
concerns a decision path that no longer exists.

**Decision.** The Task 9.3 gate is retired as a runtime switch. `main.py` runs
the full Monday chain including UC8, and neither reads `replay_report` at
startup nor accepts `--force-live`.

**What actually guards go-live**, and it is not weaker — it is aimed at the
thing that moves:

- **ADR-007 §5 already says so**: "forward paper-mode (M9) is the go-live gate
  for this strategy". The gate is a period of real operation where the agent
  proposes and the owner places the orders by hand, not a boolean read at
  startup.
- **The caps** bind every market-signal decision (`writeback.market_signal_gates`),
  and M6-bis's DoV asserted zero violations over the whole 35-year backtest.
- **ADR-009's alerts** report the drawdown, the two freshness failures and a
  stuck cycle, and never block — because a refusal cannot exit a position.
- **The anti-drift check** (2026-08-12) confronts the wired stack against its
  pinned pair every Monday and puts a divergence in the digest. That is the
  guarantee the mechanical replay was standing in for, measured continuously
  instead of once.

**Consequences.**

- The mechanical replay keeps its full value as EVIDENCE and keeps its STOP
  point (M6): it is what the pivot was argued on and what a future retirement of
  the bridge would have to re-run. It stops being a runtime switch.
- The digest line "proposal cycle disabled (replay gate)" never renders; nothing
  else in the digest changes.
- Task 9.3's remaining halves are unaffected: `replay_cost_bps` (now 23.0 per
  ADR-010), the `ReplayEvent` type and the `replay_report` document table all
  stay.
- **What is deliberately NOT replaced.** Nothing now blocks a Monday because the
  KNOWLEDGE the Worker produces is poor, and nothing should: ADR-006 judges
  knowledge by maturation — measure, propose, window, adopt or reject — and a
  boolean at startup was never that mechanism.
- Reversible: reopening means giving UC8 an allocation again, at which point the
  gate's predicate would describe something once more. ADR-012 records the way
  back.

**What this does not touch.** ADR-006's maturation, ADR-007's stack and its
addenda, ADR-009's alert-never-block scope, ADR-010's single cost rate,
ADR-011/012's mechanical sovereignty, and the M6 mechanical premise gate as a
STOP point in MILESTONES.

---

## ADR-014 — The retained bridge is PERMANENT: demoted, never retired

**Status:** accepted 2026-08-15 (owner decision, Step 6 item 3).

**Context.** ADR-007 kept the Dalio 4-quadrant design alive as a "retained
bridge" — fallback, benchmark, and framework-agnostic knowledge factory — with
an explicit end date: "the bridge is not deleted until forward paper-mode earns
the switch" (CLAUDE.md), and docs/V1_STRATEGY.md Step 6 said the bridge "can be
retired, if the forward evidence holds". Step 6 reached that decision point with
two of its three inputs already settled and the third turning out to be
something else entirely.

**What was already settled when the question came up.**

- The Worker's side (I-46) dissolved on 2026-08-14: ADR-012 had deleted the
  cognitive allocation path, so "retiring the bridge leaves the Worker with no
  legal target" describes a lever that no longer exists.
- The stack's dependency on the bridge ended on 2026-08-15: it walks its own
  calendar (`market_signal.stack_calendar`) and runs on a database where no
  bridge row exists, which a test pins by deleting them all.
- The benchmark role passed to `ms-trend-baseline` on 2026-08-13. The
  attribution measured the credit/slope signal's marginal worth at **+0.24pp of
  CAGR and +0.20 of Sortino over the trend overlay alone**, so Step 6 must judge
  the stack against the trend-only control arm, not against the Dalio books. On
  the 2026-08-14 ranking that control arm sits FIRST, ahead of the stack
  (Sortino 1.841 vs 1.664 over 36 months) — a more demanding benchmark than the
  one it replaces.

So on the three grounds ADR-007 named — lever, dependency, benchmark — the
bridge had already stopped being load-bearing.

**The reason it stays anyway, and it was written nowhere.**
`outcomes.strategy_probation_check` judges an innovation-born strategy on its
FAVORS standing **against the median of its peers** in the current regime. Five
strategies exist and four of them are the bridge's (`four-seasons-rp`,
`permanent-browne`, `barbell-taleb`, `momentum-macro`). Retiring the bridge
leaves the peer median equal to the candidate's only rival — the stack itself —
so ADR-006's promise that a new strategy is MEASURED rather than believed would
degrade to a comparison with one thing. The knowledge factory, which ADR-007
called framework-agnostic and expected to survive the pivot untouched, turns out
to depend on a POPULATION, and the bridge is where the population lives.

**Decision.** The retained bridge is permanent. It is DEMOTED — it is not an
allocation path, not a fallback anyone will switch back to, and not the
benchmark — and it is not deleted. What it is, from here:

1. **The FAVORS peer set** that makes strategy probation a measurement.
2. **Seven ranked comparators** that keep the stack's weekly claim falsifiable
   at the cost of some CPU.
3. **The M6 A/B harness** (`replay.py`), an offline CLI and not a chain step.

**The defender flag does not move.** Making the stack the defender was the
obvious half of "demote" and it is refused, for a mechanical reason:
`replay.load_inputs` picks the defender BY THAT FLAG and makes it the B arm, so
flipping it would turn M6's A/B into stack-versus-stack and empty the harness of
meaning. If the flag is ever wanted on the stack, the harness needs an explicit
"bridge defender" parameter first, in the same commit.

**What this changes in the documents.** "Until forward paper-mode earns the
switch" (CLAUDE.md) and "this is when the old-design bridge can be retired"
(V1_STRATEGY Step 6) are both now wrong as written and are corrected. Step 6's
build work is complete; what remains of it is time.

**What this does not touch.** ADR-006's maturation, ADR-007's stack, ADR-009's
alert-never-block scope, ADR-010's single rate, ADR-011/012's mechanical
sovereignty. Nothing here revives the cognitive allocation path: the bridge's
defender is mechanical (`replay.py` applies the 0.4/0.6 blend through
`blend_allocation` + `reallocation_gates`), which is what makes it a clean
comparator — two mechanical policies, no LLM variance inside either.

**The condition under which this is revisited**, stated so it is not a
permanent article of faith: the day strategy probation has another way to find
peers — a population of innovation-born strategies large enough to judge each
other, or a probation rule that does not need a median — the cost/benefit
changes and deletion becomes discussable again. Not before.
