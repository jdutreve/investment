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
`~/data/investment/...` and `~/projets/investment-agent/`.

**Consequences.**
- **Laptop sleep is the structural trade-off**: cron times become *earliest*
  times. Binding policy (TASKS Task 0.7 / Phase 7): every APScheduler job
  runs with `coalesce=True` and `misfire_grace_time` (6h daily / 24h weekly);
  on wake, missed jobs fire once, in order; the Monday chain stays strictly
  sequential. Correctness must never depend on the lid being open.
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
- The 25y **backfill stores first-release values** (ALFRED vintages) for the
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
