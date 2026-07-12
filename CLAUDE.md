# CLAUDE.md — Investment Agent (MVP Core)

See docs/REVISION_NOTES.md for V1 scope, core concepts, ranking rule, and stagflation/deflation tagging.

Read this file before any action. Implement in the order defined in
docs/MILESTONES.md (incremental, owner-verifiable slices of docs/TASKS.md). Also read docs/ARCHITECTURE.md and docs/DATA_MODELS.md
before writing any code. See docs/IMPROVEMENTS.md for deferred features and when
to add them, and docs/DECISIONS.md for the ADRs (engine spike gate, local macOS
target, vintage discipline) — never contradict an accepted ADR silently.

---

## Working Principles

These govern HOW to work on this repo, on top of WHAT to build (the rest
of this file and docs/).

- **Ask when in doubt.** If a requirement, scope boundary, or design
  choice isn't settled by an accepted ADR or spec, stop and ask the
  owner instead of guessing. A silent wrong guess on a 13-entity,
  10-relation, multi-LLM-stage system is expensive to unwind later —
  cheaper to ask up front.
- **Simple by default, no over-engineering.** Ship the smallest
  implementation that satisfies the current milestone's Definition of
  Verified. No speculative abstractions, no config knobs for
  hypothetical future needs, no framework beyond what's already listed
  in Stack. Three similar lines beat a premature helper.
- **Comment intent and execution context, not mechanics.** This is a
  solo project the owner must be able to audit alone, months later:
  comment the WHY — which ADR/Task/use-case a piece of logic serves,
  what time slot it runs in the Monday chain, what edge case or
  constraint it protects against — not what the code already says by
  being read.
- **Readable and understandable by a human, first.** When two
  implementations are otherwise equivalent, pick the one the owner can
  verify by eye without tracing execution (PEP 20 already says this for
  Python syntax in the Dev Standards below — this is the same principle
  applied to the whole codebase, not just style).
- **Reference the specs from the code.** Docstrings/comments should
  point back to the source of truth (e.g. `# ADR-004`, `# see
  ARCHITECTURE.md "Invariant confrontation rule"`, `# Task 1.1`, `#
  UC8`) so a reader can jump from a line of code to the paragraph that
  mandated it, and spec drift is visible at review time.
- **No dead code, no speculative stubs.** A milestone slice is either
  fully done or not present — no `TODO: implement later`, no code
  commented out "just in case". If something is deferred, it belongs in
  docs/IMPROVEMENTS.md (with its trigger), not as an inert stub in `src/`.
- **Prefer solid and proven over clever.** Within the already-approved
  dependencies (Stack), pick the standard, well-trodden way to use a
  library over an exotic or bleeding-edge pattern, even when the clever
  version is shorter. Boring code is what a solo owner can still debug
  a year from now.
- **Delete, don't disable.** Obsolete code (a throwaway spike, a
  milestone superseded by a later one) is removed outright, never left
  commented out or gated behind a flag — the repo should always reflect
  what is actually true, not what used to be true.
- **State assumptions explicitly when the spec is silent.** This should
  be rare — most calls are covered by "ask when in doubt" — but if a
  decision must be made to keep moving and no ADR/spec settles it, write
  the assumption down (a comment plus a line in the commit message)
  instead of picking silently.
- **Confirm before anything hard to reverse or system-wide.** Installing
  dependencies, editing dotfiles or launchd, deleting data — describe
  the blast radius and wait for a go-ahead, even when the owner has
  approved similar actions before in this session.
- **Traceability of non-obvious choices.** Any judgment call worth
  discussing gets a one-line "why" in the commit message, not only in
  the chat — the commit history has to carry the reasoning on its own,
  independent of any particular conversation.

---

## Objective

Build capital for retirement (Phase 1: accumulation only).

V1 delivers a portfolio ranking and digest engine. It does not auto-apply
allocation changes.

V1 mechanisms:
1. Detect the current 4 Seasons regime from market/macro data, using level,
   speed, and acceleration to anticipate regime shifts. Growth axis =
   `GROWTH_COMPOSITE` (FRED-native, automatic — see ARCHITECTURE).
2. Include global liquidity as a first-class MarketData family
   (`asset_class=GLOBAL_LIQUIDITY`) and combine it with 4 Seasons interpretation.
3. Rank all enabled `Portfolio` rows, including the defender, using USD
   `sharpe_rolling`, `sortino_rolling`, `calmar_rolling`, `max_drawdown`,
   `volatility`, plus cumulative `return_3m / 6m / 1y / 3y / 5y`. Indicator
   (never "ratio") is the canonical generic term.
4. Explain the ranking through frameworks, regimes, strategies, invariants,
   and market context.
5. Produce a weekly Telegram digest and optional paper-mode `Proposal` —
   either a **switch** (defender → challenger portfolio) or a
   **reallocation** (Worker-proposed adjustment of the defender's own
   allocation, mechanically validated by Writeback).

V2 adds auto-application, 48h auto-validation, and automatic learning from
real `performance_3m`.

---

## Strict Planner / Worker Separation

```
PLANNER (Qwen3-8B, OpenRouter, thinking mode)
  System prompt : meta-cognitive strategy
  DB access     : direct Python asyncio — NO tool_call
  Baseline      : fixed queries run MECHANICALLY (no LLM)
  Call 1a       : LLM chooses only the VARIABLE margin — corpus search
                  queries + ≤3 whitelisted zooms (never raw SQL)
  Call 1b       : LLM receives baseline + zoom results, returns PlannerContext
  Call 2        : async, post-Worker, knowledge extraction (guardrail)

WORKER (Sonnet 5, Anthropic API)
  System prompt : investment expert Phase 1 accumulation
  Markdown Skills : strategy evaluation, ranking, indicator interpretation,
                    defender comparison
  DB access     : via tool_call ONLY (PydanticAI tools + deps injection)
  3 tools       : db_query | market_fetch | portfolio_check
  Indicators    : already in the DB — Worker interprets, does NOT calculate
  Unaware of    : Planner, Writeback, internal structure

MECHANICAL JOBS (APScheduler, pure Python, no LLM)
  Timezone: Europe/Zurich for all cron times.
  One-time
    UC0    seed → DB bootstrap (CLI command, not cron)
  Event-driven — NO nightly cron (the Mac sleeps at night — ADR-002)
    inbox watcher (60s poll on inbox/): new file(s) + 5-min quiet period
           → CorpusIngester batch (parse + chunk + embed, no LLM)
           → IngestionEvent per batch
           → curator (LLM) — ONLY when the batch created new
             Documents: invariant candidates (author = document author
             tier) matured mechanically over 35y within minutes of the
             deposit (no user gate — ADR-006); the digest surfaces them.
             Knowledge extraction, never decisions.
    backup (sqlite3 .backup, keep 14d) after every Monday chain and
           every ingestion batch — no clock-based backup
  Weekly (Monday 08:00 Europe/Zurich when the process is running, plus
          DUE-ON-START: at every app launch and wake, if the last
          successful chain predates the most recent Monday 08:00, the
          chain runs immediately. One sequential chain; times are
          indicative, each step starts only after the previous one
          succeeds; on failure the chain aborts and a Telegram error
          alert is sent — see Phase 7)
    08:00  CATCH-UP (mechanical, also the prelude to any ad-hoc UC9 UC8
           re-run): market fetch for all days since last run → MarketData
           TS; regime detector stepped ONCE PER NEW MONTHLY PRINT since
           the last run (usually 0-1 — the axes only change on print
           days; candidate state persisted in `detector_state`;
           start_dates come from the
           data, never the run date; same step() as UC0 materialization
           and the replay) → Regime vertex + RegimeEvent (on change only);
           NAV/ratios catch-up → PortfolioNAV TS; proposal-expiry sweep;
           inbox-drained check
    (UC2 is absorbed: regime/valuations/macro live in the catch-up +
     snapshot.market_context — see USE_CASES tombstone)
    08:05  UC3 event watch: pinned official sources (EVENT_SOURCES
           constant — Fed/ECB/SNB press) → LLM triage (major events only) →
           Document(kind=event) with bounded-fetch enrichment, ingested
           synchronously (see USE_CASES UC3)
    08:10  UC4 knowledge curation sweep (LLM) → KnowledgeEvent
    08:30  Backtests recalculated → FAVORS edges (RegimeType → Strategy)
    08:35  Scenario probabilities → ScenarioProbability TS (shift vs
           previous week computed on read — no stored shift column)
           (numeric triggers only, weekly — probability values only change
            via the Worker; qualitative triggers Worker-interpreted, I-22)
    08:40  Invariant weights recalculated (incl. mechanical confrontations —
           see ARCHITECTURE "Invariant confrontation rule")
    08:45  UC6 portfolio valuations → Portfolio vertices + ValuationEvent
    08:50  UC7 ranking → portfolio_weekly_snapshot rows
    08:52  Outcome evaluation (mechanical/outcomes.py): proposal verdicts
           at +12w → confrontations source='proposal'; scenario
           calibration; strategy probation — see ARCHITECTURE
           "Unified improvement cycle"
    08:55  V2 only: learn_from_adaptations
    09:00  UC8 Worker decision cycle (Planner Pre → Worker → Planner Post →
           Writeback runs the mechanical proposal gates)
    09:30  Weekly digest → Telegram user
  Event-driven
    Invariant weights after each Backtest or Evaluation
```

---

## Stack

| Component       | Value                                                         |
|-----------------|---------------------------------------------------------------|
| DB              | SQLite (stdlib), WAL, single file — ADR-004                  |
| DB path         | `~/data/investment/investment.db`                             |
| LLM Framework   | PydanticAI V1 (model-agnostic)                                |
| Planner LLM     | `qwen/qwen3-8b` via OpenRouter, thinking mode                 |
| Worker LLM      | `claude-sonnet-5` via Anthropic                               |
| Market data     | Yahoo Finance + FRED + GROWTH_COMPOSITE + GLOBAL_LIQUIDITY    |
| Backfill        | MACRO/regime 35y (→1991, ALFRED first-release vintages,       |
|                 | publication-dated — ADR-003). TRADABLE/benchmark ~1991 too    |
|                 | via HISTORY_PROXIES (equity/bond/gold/cash proxies to         |
|                 | 1968-86, margin; commodity TR the verify-gate); TIPS floor    |
|                 | 2000, liquidity 2002 (WALCL). 1994 bond crash + dot-com.      |
| Risk-free rate  | 3-Month T-Bill (^IRX) via Yahoo Finance — USD                 |
| Timezone        | Europe/Zurich (APScheduler + all cron times)                  |
| Currency        | USD for all indicators; CHFUSD=X for display only             |
| Ingestion       | Telegram bot + local drop → inbox/ (watcher, ~5 min)          |
| Embeddings      | sentence-transformers in-process, 384 dims (no daemon)        |
| Event Watch     | UC3 Event Watch: pinned official sources (Fed/ECB/SNB press,  |
|                 | LLM triage, bounded-fetch enrichment) + user deposits/notes.  |
|                 | Quantitative shocks are mechanical (VIX/liquidity tags).      |
|                 | General auto-watch deferred — I-9/I-26                        |
| Notifications   | Telegram weekly digest (Mon 09:30) + Proposal alerts          |
| Local ops       | `invest` CLI + dashboard http://127.0.0.1:8765 (aiohttp) —    |
|                 | reads direct (SQLite WAL), writes via the agent's command     |
|                 | layer (ADR-005)                                               |
| Process         | APScheduler, single Python process                            |
| Host            | Local MacBook Pro M5 (macOS ARM64), 24 GB — see docs/DECISIONS.md |
| Service         | launchd LaunchAgent `com.jp.investment-agent`; weekly chain   |
|                 | DUE-ON-START at launch/wake (laptop sleep — TASKS Task 0.7)   |

---

## Python & Local Dashboard Dev Standards

State-of-the-art but scoped to this project's stack — no framework not
already listed above.

### Python (3.13, `uv`-managed)
- **Zen of Python (PEP 20) governs judgment calls**: explicit over
  implicit (typed signatures, named `pydantic` fields over positional
  dicts), simple over clever (no metaclass/decorator magic to save a few
  lines), flat over nested (early `return`/`raise` over deep `if` nesting),
  readability counts, and "there should be one obvious way to do it" — one
  helper per concern instead of parallel ad-hoc variants (e.g. a single
  `db.transaction()` pattern everywhere, not several). When two approaches
  are otherwise equal, pick the one a reader understands without tracing
  execution. `import this` is the tiebreaker, not a slogan.
- **Tooling**: `uv` for env/deps/lockfile (`uv.lock` committed, plus a
  committed `.python-version` pinned to `3.13` so `uv` resolves it without
  ambiguity). `ruff` for lint + format (replaces black/isort/flake8) —
  rule set explicitly chosen, not the bare default: at minimum
  `E,F,I,UP,B,SIM,RUF` (`UP`=pyupgrade enforces 3.13 syntax, `B`=bugbear
  catches common traps, `SIM`=simplify, `I`=import sorting). `ruff check`
  and `ruff format` in CI/pre-commit. `mypy --strict` on
  `src/investment/` (loosen only with an inline `# type: ignore[code]`
  and a reason).
- **Typing**: full type hints on every function signature (PEP 604 `X | Y`,
  no `Optional`/`Union` imports). `pydantic` models (already the framework
  for PlannerContext/WorkerResult/etc.) at every I/O boundary — DB rows,
  LLM outputs, HTTP payloads — never bare `dict`. Value objects that are
  loaded once and read many times (thresholds, PlannerContext,
  WorkerResult) are frozen (`model_config = ConfigDict(frozen=True)`) —
  makes accidental mutation a type error, not a debugging session.
- **Time**: all persisted timestamps are UTC (`datetime.now(UTC)`);
  Europe/Zurich conversion happens only at the presentation edge (digest
  text, dashboard, CLI output) — never store or compare local time,
  since the cron/DUE-ON-START logic already reasons in UTC internally.
- **Numeric precision**: indicators (Sharpe/Sortino/Calmar, NAV, weights)
  are `float` by design — this is scientific computation over
  numpy/pandas, not ledger accounting; don't introduce `Decimal` by a
  "money = Decimal" reflex, it breaks vectorized calculation and buys
  nothing here.
- **Async discipline**: the whole process is single-writer asyncio
  (ADR-004) — never block the event loop; CPU-bound work (backtests,
  embeddings, replay) goes through `loop.run_in_executor`, matching the
  "long operations are async jobs" rule already used for `/api/cmd`.
  No `time.sleep` in async code; no fire-and-forget tasks without a
  stored handle (leaks are invisible in a long-lived process). External
  calls (Anthropic, OpenRouter, Yahoo, FRED) go through a bounded
  `asyncio.Semaphore` per provider and an explicit per-call timeout with
  exponential-backoff retry — no unbounded fan-out, no indefinite hang.
- **Structure**: `src/` layout (already in the tree), one module = one
  responsibility, dependency injection over globals (DB handle injected
  from `main.py`, per "DB opened once ... injected everywhere").
- **Idempotency**: every mechanical job (catch-up, chain steps, UC8) must
  be safe to run twice with the same inputs and produce no duplicate
  effect — not just a consequence of DUE-ON-START/run-lock, but a design
  constraint on how each job is written (UPSERT over INSERT, check-before-
  append on EventLog-adjacent writes).
- **Startup validation**: `pydantic-settings` must raise at import time if
  a required key is missing (Anthropic/OpenRouter/Telegram) — fail before
  the scheduler starts, not mid-way through the Monday 09:00 chain.
- **Graceful shutdown**: handle `SIGTERM`/`SIGINT` (launchd stop/restart)
  by letting the in-flight transaction finish and checkpointing the WAL
  before closing the SQLite connection — never kill the process mid-write.
- **Schema migrations**: V1 bootstrap uses `CREATE TABLE IF NOT EXISTS`
  only; the first schema change after go-live needs a numbered, idempotent
  migration convention (e.g. `migrations/0001_*.sql` + a `schema_version`
  marker) — decide the convention before the first migration is needed,
  not while writing it under pressure.
- **Errors**: no bare `except:`; catch the narrowest exception; the
  Monday chain's "abort + Telegram alert on failure" rule means unhandled
  exceptions must surface, not be swallowed.
- **Logging**: stdlib `logging`, structured (module logger per file,
  `logger = logging.getLogger(__name__)`), never `print` outside `invest`
  CLI output. Every log line inside a scheduled run (catch-up, chain,
  UC8) carries a `run_id`/`job_id` so interleaved output from concurrent
  asyncio tasks (watcher, scheduler, API) stays traceable.
- **Tests**: `pytest` + `pytest-asyncio` (already a dep, Task 0.1). Unit
  tests colocated under `tests/`, mirroring `src/investment/`; one
  integration test per mechanical job (catch-up, ranking, outcomes) with
  a throwaway SQLite file, not mocks — this codebase's correctness lives
  in real calculations (Sharpe/Sortino/Calmar) and real schema
  constraints (`trace` NOT NULL, FK edges), which mocks would hide.
  Numeric invariants that must hold across arbitrary inputs (e.g.
  `weight_effective` always within `[floor, ...]`) get a property-based
  test (`hypothesis`) in addition to fixed golden-value regression tests
  — golden values alone won't catch a formula that's wrong only at the
  edges.
- **Config/secrets**: `pydantic-settings` reading `.env` (never committed;
  `.env.example` documents required keys — Anthropic/OpenRouter API keys,
  Telegram token). No secret ever logged or included in an EventLog
  payload.
- **Commits/CI**: `pre-commit` running `ruff check --fix`, `ruff format`,
  `mypy`, and a secret scanner (`detect-secrets` or `gitleaks`) — a
  filet against accidentally committing a real `.env` key even in a solo
  private repo. A GitHub Action (or local `uv run pytest` pre-push hook,
  given solo dev / private repo) blocking on lint + type + test failures.

### Local web dashboard (`ops/dashboard/`, aiohttp, no build step)
Per Task 6ter.3: server-rendered HTML + vanilla JS `fetch`, no bundler,
no CDN, no SPA framework — the standards below fit that constraint, not
a generic frontend stack. Single local user, `127.0.0.1`-only process:
no auth/ACL/authorization layer beyond what Task 6ter.1 already defines
(`X-Ops-Token`) — not a dev standard to layer further.
- **Escape rendered text**: HTML interpolation auto-escaped (Jinja2
  autoescape or manual `html.escape`) — the SQL console and knowledge
  browser render user/LLM-sourced text, which breaks page layout if
  unescaped even without an attacker in the picture.
- **No inline event handlers**: vanilla JS attaches listeners via
  `addEventListener` from a `<script>` block, not `onclick="..."` in
  markup — cleaner separation of markup and behavior.
- **Progressive, not reactive**: server renders the full page on load;
  JS only does polling refresh (status, job progress) and POST actions
  (accept/reject/run) — no client-side state management, no virtual DOM.
  Matches "no new framework" and keeps the dashboard debuggable with
  view-source.
- **Accessibility**: semantic HTML (`<table>` for the ranking/invariants
  grids, `<button>` not `<div onclick>`), visible focus states, labels on
  every form control (drawdown %, SQL textarea) — cheap to get right at
  server-render time, expensive to retrofit.
- **SVG charts**: generated server-side (Task 6ter.3 NAV/weight charts) —
  deterministic, testable with plain assertions on the SVG string, no
  client charting library dependency.
- **Errors surfaced, not swallowed**: a failed `/api/cmd` call renders
  the actual message (e.g. "already running: catchup") in the UI, per the
  idempotency/run-lock rules in Task 6ter.1 — never a silent no-op.

---

## Non-negotiable Rules

### SQLite (ADR-004)
- Agent = sole writer. Writes serialized via asyncio (single process),
  always inside explicit transactions (`db.transaction()`).
- `trace` mandatory on every vertex — `ValueError` if empty.
  Exemptions (`TRACE_EXEMPT`): `Passage` (inherits from Document),
  `RegimeType` (static seed, narrative in `description`), `EventLog`
  (the payload IS the trace).
- DB opened once in `main.py`, injected everywhere.

### EventLog — source of truth for UC8
`EventLog` is an **append-only table** (entity with no relations; monotonic
ULID id = canonical append order). **Every UC side-effect must be appended to EventLog BEFORE being
committed elsewhere in the DB.** Architectural invariant for auditability
and replay. Exemptions: UC0 bootstrap (SeedEvent is a closing summary) and
pure TS writes (UC1 market feed, weekly NAV catch-up and scenario
jobs) append no EventLog row — they create no vertex/edge.

### Decision cycle
- **Event-driven ingestion** = mechanical, with ONE LLM exception: the
  curator (fires minutes after a deposit; knowledge extraction
  from newly ingested documents — its outputs are invariant candidates that
  mature MECHANICALLY (35y confrontation), never decisions; no user gate —
  ADR-006).
- **Weekly (Monday 09:00)** = sole *scheduled* decision cycle. Worker +
  Planner Post. UC9 (user-initiated chat) may trigger one ad-hoc UC8 re-run
  per day — user-initiated, so it does not break the autonomy rule.
- V1 proposals (switch or reallocation) → Telegram digest + `Proposal`
  vertex only. No automatic application. V1 cognition is fully autonomous —
  no user-validation gate (ADR-006); the owner's only hand is placing real
  orders on reading the digest. V2 = auto-execution.

### Invariants — weight model
- `source` is now a free-text real provenance (document+page, backtest run,
  observation date). `author` carries the authority tier and drives the floor.
- `weight_effective = max(weight_initial × market_score × recency_factor, floor_weight)`
- `floor_weight` by `author` tier, persisted at creation:
  - dalio: 0.40
  - marks: 0.35
  - null (other corpus): 0.20
  - system (agent-discovery): 0.05
- `recency_factor` (single half-life in V1):
  - `days_since` = CONDITION-RELATIVE — time since the invariant's `condition`
    was last PRESENT (moment-time), NOT wall-clock; a dormant invariant whose
    condition is absent does not decay. For an `always` condition this reduces
    to days-since-last-confrontation.
  - `half_life = 365 days`
  - `recency_factor = 0.5 + 0.5 × exp(-days_since / half_life)`
    (decays from 1.0 toward an asymptotic floor of 0.5 — no clamp needed)
- `market_score = confirmation_count / (confirmation_count + infirmation_count)`
  (use 1.0 until first confrontation)
- Every invariant matures MECHANICALLY at birth over 35y (ARCHITECTURE "Birth
  maturation"); weight updates event-driven after each Backtest or Evaluation.
- `source=agent-discovery` → EventLog append → vertex committed and matured
  mechanically like any other (same 35y confrontation); the digest surfaces
  it. No `status=proposed`-awaiting-user, no validation notification (ADR-006).

### Curation vs Innovation (both mechanical — ADR-006)
- **Curation** (autonomous): update weight, add confirmations, add SUPPORTS
  edges, enrich description/example on **existing integrated** Invariants.
- **Innovation** (also autonomous — NO user gate; AFTER the mechanical dedup
  gate — cosine ≥ `invariant_merge_threshold` vs an existing invariant
  converts the candidate into a curation, never a duplicate): create a new
  Invariant, new or revised Strategy (`type=new_strategy` /
  `strategy_revision`, `enabled=false` until mechanical probation passes —
  lifecycle in ARCHITECTURE "System Evolution"), new metric. `status`:
  `proposed` (maturing) → `integrated` (time-validated: N_min/θ, not refuted)
  → `rejected` (refuted) — 100 % mechanical. Schema self-extension (new
  vertex/edge types) is V2 — IMPROVEMENTS I-27.
- **Author tier of new Invariants**: extracted from a corpus document →
  `author = Document.author` tier (dalio/marks/null — floor 0.40/0.35/0.20);
  discovered from market patterns (backtests, rankings) →
  `author='system'` (floor 0.05). `source` always records the real
  provenance in free text. The UC0 initial curation pass (USE_CASES step 6b,
  DEFAULT — skip with `--no-curate`) lets a deposited book yield matured
  invariants at install time; later deposits are curated within minutes
  (watcher → ingestion batch → curator), matured the same way.

### User interfaces — one command layer
- Telegram bot, `invest` CLI and the local dashboard are THREE FRONTS of
  ONE command layer (`ops/commands.py`): every user action, whatever the
  front, becomes a UserDecisionEvent and goes through Writeback — same
  gates, same audit. Reads are direct on SQLite (WAL concurrent readers);
  writes only through the running agent (single-writer preserved).

### Worker
- Never mention Writeback/Planner/storage in Worker prompts.
- `status:integrated` = time-validated mechanically (N_min/θ, not refuted);
  no user gate (ADR-006).
- `_db` captured by closure in PlannerPre.run() — never exposed to Worker.
- Bridged functions (principle of least privilege):
  - `db_query` : FORBIDDEN_SQL whitelist, max 20 rows
  - `market_fetch` : ALLOWED_TICKERS only, max 30 rows
  - `portfolio_check` : ID format validation, limited exposed fields
- `WorkerResult` must include `innovations_proposed: list[ImprovementProposal]`
  (empty list if none) and `reallocation_proposed:
  Optional[ReallocationProposal]` (see docs/DATA_MODELS.md).

### Unified improvement cycle — proposal → measure → adoption
- Applies to ALL improvable resources (Proposal, Invariant, Strategy,
  scenario probabilities, thresholds): measure current performance →
  propose → mechanical maturation window → adopt or reject. No user gate
  (ADR-006). Nothing stays "proposed" forever and nothing is adopted
  without measurement. Table + job spec in ARCHITECTURE.
- Weekly 08:52 `outcomes.py`: every Proposal gets an `outcome.verdict`
  (won/lost) at +`proposal_outcome_weeks` (12), feeding invariant
  confrontations `source='proposal'`; accepted paper-tests tracked weekly;
  activated strategies run `strategy_probation_weeks` (12); scenario
  probabilities are calibration-scored. Digest renders the scoreboard.

### UC8 — Worker proposes, Writeback disposes
- The 5 **switch gates** (rank, Sortino gap, Calmar floor, concentration,
  meaningful change) are deterministic and run **mechanically in Writeback**
  — plus an anti-repetition pre-gate (`proposal_cooldown_weeks`, 4).
- The Worker contributes: `reasoning`, interpretation of qualitative scenario
  triggers, Evaluations, innovations, and optionally a **reallocation
  proposal** for the defender (delta blend 0.4 × scenario + 0.6 × FAVORS).
- Reallocation gates (also mechanical, in Writeback): user caps on the
  proposed allocation, min meaningful change
  (`proposal_min_allocation_change_pts`), turnover cap
  (`proposal_max_turnover_pct`), and cited-invariant eligibility
  (`status=integrated`, `weight_effective ≥ proposal_invariant_weight_min`,
  not measurably refuted — ≥4 confrontations with market_score < 0.35
  disqualifies, floor or not — AND `condition` ACTIVE now: a dormant
  invariant does not justify acting on today's market). See docs/USE_CASES.md UC8.

### Mechanical calculations
- Sharpe/Sortino/Calmar: pure Python (numpy/pandas), no LLM.
- Risk-free rate: 3-Month T-Bill (^IRX), fetched daily.
- Rolling window: 36 months (756 trading days) for all `*_rolling` indicators.
- Cumulative returns: `return_3m / 6m / 1y / 3y / 5y` on calendar windows
  ending at the snapshot date.
- All indicators in USD; `*_rolling` suffix everywhere on
  Portfolio/Backtest/FAVORS.
- CHFUSD=X applied only for user-facing display.
- `portfolio_weekly_snapshot` updated after each weekly valuation.
- All formulas pinned in docs/DATA_MODELS.md "Calculation conventions"
  (annualization 252d, Sortino MAR = rf, NAV monthly rebalancing, cash
  accruing at ^IRX). Two implementations must produce the same numbers.

### Concentration & drawdown limits
- `user_profile.max_single_asset_pct` (default 40%) and
  `user_profile.max_drawdown_pct` (-15%) are **binding** for the defender
  role and all proposal candidacy (switch and reallocation).
- Per-portfolio `max_single_asset_pct` / `max_drawdown_rule` may only be
  stricter, never looser. Writeback enforces the stricter of the two.
- Proposal blocked by Writeback if the implied allocation violates.

### Strategies
- 4 strategies seeded: four-seasons-rp, permanent-browne, barbell-taleb,
  momentum-macro (ids distinct from Framework ids — no name collision).
- `Strategy.enabled` (BOOLEAN, default true). User can disable via UC9.
- Conditions must include ≥1 indicator orthogonal to regime definition
  (manual check at seed time in V1 — see IMPROVEMENTS I-12).

### Regimes
- 5 `RegimeType` vertices seeded for `4seasons`. Stagflation = alias of
  `falling-growth-rising-inflation` on RegimeType.
- `Regime` vertex = one concrete occurrence (`start_date` / `end_date`,
  confidence, `events` summary array, dynamic tags, `updated_at`).
  Id convention: `<regimeType.alias>-<start_date>` (e.g. `stagflation-2026-05-01`).
  Created/updated by `detect_regime()`.
- Deflation = dynamic tag on Regime instances, never a RegimeType.
- Detection uses `level`, `speed`, `acceleration` on MarketData TS.
- FAVORS and DESIGNED_FOR point to RegimeType (type-level, multi-period).
- IN_REGIME points to Regime (instance-level).

### FX
- `Portfolio.fx_usd_exposure` tracked, informational only.
- No hedging in Phase 1.

---

## Entities (conceptual graph: 13 entities, 10 relations — physically 5 M:N tables + 5 FK columns — 3 time-series; V2 adds Adaptation + MODIFIES; see docs/DATA_MODELS.md mapping)

```
VERTEX : Framework, RegimeType, Regime, Invariant, Strategy, Scenario,
         Evaluation, Backtest, Proposal,
         Portfolio, Document, Passage,
         EventLog (append-only audit log, no edges)
         (Signal vertex dropped from V1 — see IMPROVEMENTS I-19;
          Adaptation is V2-only and NOT created at UC0)

EDGES  : UPDATES,
         FAVORS (RegimeType → Strategy, multi-period aggregated, strategy-level),
         HAS_SCENARIO, BACKED_BY, TESTED_IN,
         IN_REGIME (Backtest → Regime instance),
         HOLDS (Portfolio → Strategy, primary BOOLEAN),
         DESIGNED_FOR (Portfolio → RegimeType), CONTAINS, SUPPORTS
         (MODIFIES is V2-only, created with Adaptation)
         (IMPLIES and GENERATES dropped from V1 — see IMPROVEMENTS I-19;
          Evaluation records its triggering observations in `events`)

TIME-SERIES : MarketData (level/speed/acceleration), ScenarioProbability,
              PortfolioNAV
DOCUMENT    : user_profile, invariant_author_config, allowed_tickers,
              system_thresholds, detector_state, invariant_confrontations,
              benchmark_valuation, portfolio_weekly_snapshot,
              scenario_calibration, replay_report
              (plain tables, single engine — weight/history/
               performance data live on vertices and FAVORS edges, never
               duplicated in docs)
```

See docs/DATA_MODELS.md for the complete schema and properties.

---

## Git Workflow

```bash
git add .
git commit -m "feat: Phase N — description"
git push origin main
```

Public repo, solo dev — no PR. `gh` CLI sufficient.

---

## Definition of Done

1. UC0 seed produces the 13 entity tables, 5 M:N relation tables (the
   other 5 relations are FK columns), 3 TS tables and 10 document tables
   (incl. `benchmark_valuation`, the cross_class/cross_strategy benchmark);
   historical Regime instances from the 35y backfill; seed data; invariants
   MATURED over 35y and scenario probabilities WARM-STARTED over 35y (go-live
   with matured knowledge, not cold); a clean invariant-contradiction check;
   and the first `portfolio_weekly_snapshot` row.
2. `update_ratios()` (Monday 08:00 catch-up) populates PortfolioNAV TS for
   every trading day (USD).
3. `detect_regime()` creates/updates a Regime vertex with `is_current=true`
   using level/speed/acceleration, with hysteresis and a computed confidence.
4. After Dalio corpus ingestion + the default seed curation pass (skip
   with `--no-curate`): 10+ Passage vertices, and extracted Invariants
   carrying `author='dalio'` (floor 0.40), each with a machine-readable
   `condition`+`effect`, matured over 35y (market_score set), linked by
   SUPPORTS edges — no user validation (ADR-006).
5. Full weekly cycle: MarketData/EventLog ingestion → Worker → Evaluation →
   Scenario update → Proposal (if gate passed).
6. `source=agent-discovery` Invariant is persisted, matured mechanically over
   35y like any other, and surfaced in the digest — no user-validation gate
   (ADR-006).
7. `weight_effective` of an agent-discovery invariant grows after mechanical
   market confirmations (ARCHITECTURE "Invariant confrontation rule").
8. `learn_from_adaptations()` (V2) propagates `performance_3m` to BACKED_BY invariants.
9. Every EventLog append precedes its corresponding entity/relation commit.
10. Writeback blocks any Proposal (switch or reallocation) whose implied
    allocation violates the binding user caps.
11. The Worker can emit a reallocation Proposal for the defender that passes
    the mechanical gates and renders in the digest with old vs new
    allocation and reasoning.
12. The Phase 9 shadow replay produces a 35y `replay_report` with zero
    point-in-time violations, and `main.py` refuses to enable the weekly
    proposal cycle when the report shows no net value-add on the validation
    window (override `--force-live`).
13. A Proposal older than `proposal_outcome_weeks` carries an
    `outcome.verdict` (won/lost), its cited invariants show a matching
    `invariant_confrontations` row with `source='proposal'`, and the digest
    renders the scoreboard (hit-rate, paper-tests, probations).
