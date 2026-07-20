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

## ADR-007 — Adopt the Verdad market-signal monthly stack as V1's operating strategy

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
1. The V1 operating strategy is the **Verdad market-signal monthly stack**
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
Surfaced while wiring M6-bis: the Verdad books deliberately hold 50% single
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
cap-books-to-40 (would drift the backtest) and exempt-the-whole-Verdad-stack
(would weaken "binding caps bind ALL candidacy" too broadly).

**Second addendum (2026-07-20, owner sign-off) — trend-haven sleeve exempt from
the single-asset cap.** Surfaced by the M6-bis cap confrontation: at ~10 of the
119 decision dates (risk-off — 2008-09, 2020, 2022...) BOTH SPY and GLD are
below their 200d MA, so the trend overlay redirects both into IEF, concentrating
the HAVEN to ~90% — breaching even the raised 50% cap. That concentration IS the
drawdown control (the deliberate flight to safety), and the validated 9.85%
includes it. Decision: the **trend-haven sleeve (IEF) is EXEMPT from the
single-asset cap** in the Verdad path (`gates.concentration_ok(..., exempt=
{IEF})`), chosen over splitting the excess into SHY/cash. Rationale: SIMPLICITY
— the haven is structurally a safety redirect, not a conviction bet, so the
"single-asset" cap's intent (bound concentrated BETS) does not apply to it;
exempting preserves the validated numbers exactly with no re-validation. Scope
is NARROW and explicit: only IEF, only via the documented `exempt` argument; the
cap still binds every other sleeve and every seeded-portfolio proposal (the
`exempt` default is empty). This is why it does not reopen the "binding caps bind
ALL candidacy" principle — it is a named exception, not a hole.
