# CLAUDE.md — Investment Agent (MVP Core)

Local single-user agent that builds retirement capital (Phase 1: accumulation).
V1 = portfolio ranking + weekly Telegram digest + paper-mode proposals (switch
defender / reallocate). **V1 never executes a trade**; cognition is fully
autonomous (no user-validation gate — ADR-006), the owner alone places orders.
V2 adds auto-execution and learning from real performance.

> **ADR-007 pivot (accepted 2026-07-20) — read `docs/V1_STRATEGY.md`.** V1's
> ADOPTED allocation strategy is now the **market-signal monthly countercyclical
> stack** (market-priced credit-spread/slope regime → 4 concentrated books +
> trend overlay, MONTHLY cadence), not the seeded Dalio 4-quadrant
> portfolio rotation. **The non-negotiables below that describe the weekly
> chain, FAVORS and the ranking rule still hold, but as the RETAINED BRIDGE**,
> NOT the live allocation path. Where a rule below governs allocation, the
> market-signal stack path in V1_STRATEGY supersedes it; where it governs the caps,
> the caps still bind (stricter-of enforcement is unchanged).
>
> **ADR-014 (2026-08-15) — the bridge is PERMANENT, demoted and never retired.**
> The earlier wording ("not deleted until forward paper-mode earns the switch")
> promised a deletion that will not come: the three grounds for it all dissolved
> (the Worker's lever with ADR-012, the calendar dependency when the stack got
> its own clock, the benchmark role when `ms-trend-baseline` took it), and what
> keeps the bridge is a reason nobody had written down — it is the FAVORS PEER
> SET that makes `outcomes.strategy_probation_check` a measurement instead of a
> comparison with one rival. Its scenario-driven UC8 and cognitive reallocation
> blend are gone regardless (ADR-012); the 0.4/0.6 blend survives as pure
> mechanics inside `replay.py`. The `defender` flag stays on
> `4s-balanced-defender`, because `replay.load_inputs` reads that flag to build
> M6's B arm.

## Documentation map — load on demand

| File | Load it when you need |
|---|---|
| `docs/V1_STRATEGY.md` | **THE ADOPTED STRATEGY (ADR-007)** — the market-signal monthly stack, the migration plan, impact map, roadmap Step 0→7, open owner decisions. Read alongside MILESTONES. |
| `docs/MILESTONES.md` | **EXECUTION ORDER** — 13 increments, STOP points, incremental-seed map. Read before starting any work. |
| `docs/TASKS.md` | Full build spec, phase by phase — the implementation source of truth (DDL, seed data, job specs, CLI/dashboard spec). |
| `docs/DATA_MODELS.md` | Complete schema (13 entities, 11 relations, 3 TS, 13 doc tables), pinned calculation conventions, units, EventLog ordering semantics. |
| `docs/ARCHITECTURE.md` | Regime detection algorithm, Planner/Worker cycle, invariant confrontation & birth maturation, unified improvement cycle, replay harness. |
| `docs/USE_CASES.md` | UC0–UC9 step by step, proposal gates detail, Event Watch. |
| `docs/REVISION_NOTES.md` | V1 scope, core concepts, ranking rule, the 3 validations and their honest bounds. |
| `docs/DECISIONS.md` | ADRs (SQLite, local Mac, vintage discipline, ops layer, no-user-gate). Never contradict an accepted ADR silently. |
| `docs/IMPROVEMENTS.md` | Deferred features (I-N) and the triggers to revisit them. |
| `docs/EXAMPLE.md` | One full weekly cycle traced end to end (stagflation 2026). |

Read TASKS + DATA_MODELS + ARCHITECTURE sections for an area before writing
its code.

## Working principles

- **Ask when in doubt** — if an ADR/spec doesn't settle it, ask the owner
  instead of guessing. But a *found bug* during review is not a doubt: fix it
  immediately, then report what was fixed.
- **Simple by default** — smallest implementation that satisfies the current
  milestone's Definition of Verified; no speculative abstractions or knobs.
- **Comment intent and execution context, not mechanics** — which
  ADR/Task/UC a block serves, what edge case it protects; reference specs
  from code (`# ADR-004`, `# see DATA_MODELS.md 'Ordering semantics'`).
- **Human-readable first**; prefer proven/boring over clever.
- **No dead code, no speculative stubs** — deferred work goes to
  docs/IMPROVEMENTS.md, not inert code. Delete obsolete code, don't disable.
- **State assumptions explicitly** when the spec is silent (comment + commit
  message); any non-obvious judgment call gets a one-line "why" in the commit.
- **Confirm before anything hard to reverse or system-wide** (deps, dotfiles,
  launchd, data deletion).
- **WHEN A SECOND ONE ARRIVES, FIND WHAT NAMED THE FIRST.** The most productive
  review question this project has: a constant, a set or a sentence that named
  the only thing existing when it was written, and a second thing arrives
  without the rule following. Five occurrences in the week of 2026-08-08 —
  `COGNITIVE_BOOK_ID` (one book id, then two, so gate 6 recorded on one and
  refused on the other), `HAVEN_EXEMPT` (IEF, then a cash fallback the
  exemption did not follow, freezing the stack through the 2022 drawdown), a
  hand-written `describe_rule` sentence, a hand-listed "every reachable state"
  test that stopped covering them, and a digest line saying "redirected to IEF"
  when the target was cash. Grep for the singular name; ask what else is now of
  its kind.
- **A bound whose consumption is not logged cannot be re-derived.** The Worker's
  12-tool-call budget was undiagnosable for a day — refused calls raised before
  the log line, so the budget counted fifteen and the log showed seven, and
  three successive theories were built on the missing eight. When you add a
  limit, log what spends it.

## Architecture in one screen

Three cognitive roles, strictly separated (full spec: docs/ARCHITECTURE.md):

**Which MODEL runs each role is `.env`'s business alone** — `PLANNER_MODEL` and
`WORKER_MODEL` have NO default in the code (the agent refuses to start without
them) and no source file may name a model. Each role states the CAPABILITIES it
requires; a swap is a `.env` edit plus a smoke test, never a code change.

- **PLANNER** (`PLANNER_MODEL` via OpenRouter, thinking mode) — assembles Worker
  context: mechanical baseline queries + LLM-chosen margin (corpus searches,
  ≤3 whitelisted zooms — never raw SQL). Post-Worker Call 2 = guardrail +
  knowledge extraction. Direct DB access in Python, no tool_call.
- **WORKER** (`WORKER_MODEL` via OpenRouter — every role routes there, owner
  decision 2026-07-21; needs structured output AND function tools in the SAME
  run) — investment expert; interprets,
  never calculates (indicators are already in the DB). DB access via 3
  bridged tools ONLY: `db_query` (FORBIDDEN_SQL whitelist, ≤20 rows),
  `market_fetch` (ALLOWED_TICKERS, ≤30 rows), `portfolio_check`. Unaware of
  Planner/Writeback/storage — never mention them in Worker prompts.
  `WorkerResult` always includes `innovations_proposed: list`; it carries NO
  allocation — the Worker does not allocate (ADR-012).
- **MECHANICAL JOBS** (APScheduler, pure Python, no LLM) — everything else.

Scheduling (Europe/Zurich; laptop sleeps — ADR-002, so NO nightly cron):
- **Event-driven**: inbox watcher (60s poll, 5-min quiet) → ingestion batch →
  curator (LLM, knowledge extraction only); backup after every chain/batch.
- **Weekly chain, SUNDAY 08:00** (moved from Monday, owner 2026-08-12: the
  digest carries an order placed by hand, and Sunday morning is before the week
  opens. `chain.CHAIN_START_WEEKDAY` is the single anchor the cron and the
  due-check both read.) (ADR-007: the ALLOCATION DECISION runs MONTHLY, not weekly —
  the market-signal regime and market-signal books move slowly by design; the catch-up
  /NAV/regime-step/curation jobs below keep their natural weekly frequency,
  only the UC8 decision + digest gate on the monthly cadence. Indicative times,
  strictly sequential, abort +
  Telegram alert on failure; DUE-ON-START at launch/wake if the last success
  predates the most recent Sunday 08:00): 08:00 catch-up (market TS, regime
  step per new monthly print, NAV, expiry sweep) → 08:05 UC3 event watch →
  08:10 UC4 curation sweep → 08:30 backtests→FAVORS → 08:35 scenario
  probabilities → 08:40 invariant weights → 08:45 UC6 valuations → 08:50 UC7
  ranking → 08:52 outcomes (verdicts +12w, calibration, probation) → 08:55
  **market-signal monthly decision** (`market_signal_cycle.py` — ADR-007's LIVE
  allocation: PIT inputs → credit-spread/slope signal → book → trend overlay →
  binding caps → EventLog → `Proposal(proposal_type='market-signal')`; runs on
  every week but is a no-op unless the month's decision date is new, so the
  monthly cadence needs no separate schedule. It refreshes the stack's
  `portfolio_nav` on EVERY run, before that monthly check — the NAV is a weekly
  artefact feeding the ranking and the drawdown alert, only the DECISION is
  monthly) → 09:00
  UC8 decision cycle (Planner Pre → Worker → Planner Post → Writeback gates;
  the Worker READS the market-signal decision as context and nuances it, it
  never re-picks the book) → 09:30 digest. Full annotated timeline: docs/USE_CASES.md.
- UC9 (user chat) may trigger one ad-hoc UC8 re-run per day.

## Stack

Python 3.13 (uv) · SQLite WAL single file `~/data/investment/investment.db`
(ADR-004) · PydanticAI (the LLM abstraction — no homemade wrapper) ·
sentence-transformers in-process (384 dims) · APScheduler single process ·
launchd on a MacBook Pro M5 (ADR-002) · Yahoo+FRED (ALFRED first-release
vintages — ADR-003; 35y backfill →1991 via HISTORY_PROXIES splice) ·
Telegram bot + `invest` CLI + local dashboard 127.0.0.1:8765 — three fronts
of ONE command layer `ops/commands.py` (ADR-005): reads direct on SQLite,
writes only through the running agent. All indicators USD (rf = ^IRX);
USD end to end — display, NAV and performance (owner, 2026-08-08); CHFUSD=X is
tracked but unused. Full table: docs/TASKS.md Phase 0.

## Non-negotiable rules

**SQLite (ADR-004)** — agent = sole writer, ONE connection opened in
`main.py` and injected everywhere; writes serialized via one asyncio executor
path, always inside explicit transactions. `trace` mandatory on every vertex
(`ValueError` if empty); exempt: `passage`, `regime_type`, `event_log`.

**EventLog** — append-only; monotonic ULID id = THE canonical append order
(a hard guarantee, enforced in `InvestmentDB`). **Every UC side-effect is
appended to EventLog BEFORE its vertex/edge commit**, same transaction.
Exempt: UC0 seed (closing SeedEvent) and pure TS writes.

**No user gate (ADR-006)** — invariants, strategies, scenario probabilities
and thresholds all mature MECHANICALLY: measure → propose → maturation window
→ adopt/reject. Invariant verdict (three outcomes, ADR-006 amendments):
`integrated` iff N_min AND score ≥ θ AND the 0.50 null produces evidence this
good ≤ 5% of the time (exact binomial tail — θ alone is a point test that gets
EASIER at small N: at N=3 a zero-edge invariant integrated on a coin flip);
`rejected` iff refuted (score < 0.35, N ≥ 4) OR inadequate (N ≥ 4 AND a true
rate of θ produces evidence this bad ≤ 5% of the time — demonstrably cannot
reach the bar); else `proposed` = INSUFFICIENT EVIDENCE only. Effect size and
evidence are BOTH required: θ asks "worth acting on?", the tail asks "do we
know it at all?". Belief does not grant integration, history does. Nothing
stays proposed forever; nothing is adopted without measurement. The unified
improvement cycle (docs/ARCHITECTURE.md) covers Proposals (verdict at +12w),
strategies (12w probation), scenarios (calibration scoring).

**Invariant weight model** —
`weight_effective = max(weight_initial × market_score × recency_factor, floor_weight)`;
floors by `author` tier: dalio 0.40 · marks 0.35 · null/other 0.20 · system
0.05. `market_score = confirmations / (confirmations + infirmations)` (1.0
until first confrontation). `recency_factor = 0.5 + 0.5 × exp(-days_since/365)`
with `days_since` CONDITION-RELATIVE (a dormant invariant does not decay).
Weight-like fields are 0–1 fractions everywhere. Every invariant matures over
35y at birth; details: docs/ARCHITECTURE.md "Birth maturation".

**Ranking rule** — all enabled portfolios ranked together, defender included,
never privileged. Sort: `sortino_rolling` DESC, with Sortino ties GROUPED, not
compared pairwise: a row joins the current group while within 0.02 of that
group's LEADER (highest Sortino), else opens a new group and leads it. Within
a group: `calmar_rolling` DESC, then `max_drawdown` (less negative wins).
A *pairwise* "tied within 0.02" test is NOT transitive (1.00/1.015/1.03: A ties
B, B ties C, C beats A) — it admits no consistent order and the result follows
row order; grouping makes the key `(group, −calmar, −max_drawdown)`, a total
order (M4).
`calmar_rolling < 1.0` → demoted to bottom. TWO things keep a row ranked while
barring it from the defender role and proposal candidacy, and they are separate
rules with separate mechanisms: breaching the user DRAWDOWN rule (stamped on the
snapshot as `excluded_from_candidacy`, `snapshots.breaches_drawdown_rule`), and
being a BENCHMARK (`seed_data.BENCHMARK_PORTFOLIOS` = `all-weather-USD`,
`spy-USD` — refused by kind in the challenger search, and carrying no flag,
since the flag means the drawdown one). A yardstick is ranked so it is seen and
never proposed so it cannot be bought.

**Binding caps** — `user_profile.max_single_asset_pct` (**60%** since 2026-08-14
for the tight-flat book's SPY 60, where the equity dial's Sortino peaks; 50 by
the ADR-007 addendum, 40 before it. It binds the largest ASSET CLASS of an
allocation, not the largest ticker — `SPY 50 / VTI 20` is 70% of one exposure —
and `/cap <pct>` moves it) and
`max_drawdown_pct` (**-25%**, raised from -15% by ADR-007 for the
accumulation-horizon market-signal stack; it bounds the STACK's realized drawdown,
not each book standalone) bind the defender role and ALL proposal candidacy;
per-portfolio rules may only be STRICTER. Writeback enforces the stricter of
the two and blocks any violating proposal — **on the ranking/bridge paths.
ADR-009 scopes the DRAWDOWN leg out of the market-signal path**: it is measured
there on the stack's 36-month rolling drawdown and raises a digest ALERT,
because refusing a proposal cannot exit a position, only freeze one — and the
proposal blocked during a drawdown is the trend overlay's flight to safety. The
concentration cap still binds, EXCEPT on the haven chain — `HAVEN_EXEMPT` =
IEF **and the cash fallback** (ADR-007's second addendum, extended 2026-08-08
after the cap froze the stack in its stale book on four of the seven 2022 dates
of the M8b run). Same reason as the drawdown leg: a refusal cannot exit a
position, and the target being refused was the flight to safety itself.
`reallocation_gates` takes the same exemption for the bridge's mechanical
blend, which is now its only cognitive-free caller (ADR-012).

**Trading costs (ADR-010)** — ONE rate, `ratios.TRADING_COST_BPS` = **23 bps
per order** (Saxo actual; no FX leg, every portfolio is USD in a USD account),
charged to EVERY NAV — the BENCHMARKS included (both of them since 2026-08-15),
the temporary NAVs that score a proposal at +12w included (I-45), and the
monthly drift-rebalance included. `system_thresholds.replay_cost_bps` must equal
it: a replay that validates a strategy and a ranking that compares it have to
charge the same rate.

**THE WORKER DOES NOT ALLOCATE (ADR-012)** — it READS the market-signal
decision and contributes a qualitative reading, invariants, rule revisions and
process critiques. It proposes no allocation, for any book: `WorkerResult` has
no allocation field, `dispose_reallocation` and its gates are gone, and the two
M8b measurement books with them. A disagreement with the RULE goes through
`innovations_proposed` (`strategy_revision`), where ADR-006's maturation
MEASURES it instead of applying it once on conviction.

This subsumes ADR-011 rather than contradicting it: its gate 0 refused cognitive
reallocations aimed at the stack, and there are none left to refuse, so the
property holds by construction. The retained bridge's defender is now purely
mechanical (`replay.py` applies the 0.4/0.6 blend through `blend_allocation` +
`reallocation_gates`), which makes it the cleaner benchmark — two mechanical
policies compared, no LLM variance inside either. Two days of M8b runs are the
evidence, and ADR-012 records both what they show and what they do not.

**Mechanical calculations** — Sharpe/Sortino/Calmar in numpy/pandas, no LLM;
rolling window 756 trading days; cumulative `return_3m/6m/1y/3y/5y` on
calendar windows; all formulas pinned in docs/DATA_MODELS.md "Calculation
conventions" — two implementations must produce the same numbers. Indicator
(never "ratio") is the canonical generic term.

**Regimes** — 5 seeded `RegimeType`s (stagflation = alias of
falling-growth-rising-inflation); deflation is a TAG on Regime instances,
never a type. Detection on level/speed/acceleration (direction classified on a
speed smoothed over `regime_speed_smoothing_months`), with
`regime_confirm_prints`-print hysteresis (M3-calibrated: 3);
growth axis = GROWTH_COMPOSITE. FAVORS/DESIGNED_FOR point to RegimeType;
IN_REGIME to Regime instances. Algorithm: docs/ARCHITECTURE.md.

**Vintage discipline (ADR-003)** — a MarketData row's `ts` is the date the
value became KNOWABLE (publication-dated, ALFRED first-release); replay and
maturation are point-in-time by construction (`ts ≤ t`).

**Entities** — 13 vertices, 11 relations (6 M:N tables + 5 FK columns),
3 time-series, 13 document tables (10 in the spec + `curated_passage` the M7
curation checkpoint, `innovation` the recurrence ledger, `revision_measurement`
the measured-verdict ledger). These counts named what existed when they were
written and drifted three times; the schema is the authority
(`db/schema.py` section headers carry the same counts). Complete schema:
docs/DATA_MODELS.md.

## Dev standards (essentials)

- **Zen of Python governs judgment calls**: explicit, simple, flat, readable.
- `uv` + committed `uv.lock` + `.python-version` (3.13). `ruff` with
  `E,F,I,UP,B,SIM,RUF` (line 100) + `ruff format`; `mypy --strict` on
  `src/investment/` (inline `# type: ignore[code]` + reason only).
- Full type hints (PEP 604); `pydantic` models at every I/O boundary (DB
  rows, LLM outputs, HTTP), frozen for read-many value objects.
- All persisted timestamps UTC; Europe/Zurich only at the presentation edge.
- Indicators are `float` (numpy/pandas vectorized) — no `Decimal` reflex.
- Asyncio single-writer: never block the loop; CPU-bound via
  `run_in_executor`; external calls behind a per-provider `Semaphore` +
  timeout + exponential backoff; no fire-and-forget tasks without a handle.
- Every mechanical job idempotent (UPSERT, check-before-append).
- `pydantic-settings` fails at startup on missing keys; SIGTERM/SIGINT →
  finish transaction, checkpoint WAL, close. No bare `except`; unhandled
  errors surface (chain aborts + Telegram alert). Stdlib `logging` with
  `run_id` per scheduled run; `print` only in CLI output.
- Tests: `pytest` + `pytest-asyncio` under `tests/`, real throwaway SQLite
  (no mocks); one integration test per mechanical job; `hypothesis` for
  numeric invariants that must hold at the edges.
- Secrets in `.env` (never committed; `.env.example` documents keys); no
  secret in logs or EventLog payloads. Pre-commit: ruff, mypy, secret scan.
- Schema: `CREATE TABLE IF NOT EXISTS` for V1; first post-go-live change
  starts a numbered migration convention.
- Dashboard (`ops/dashboard/`): server-rendered HTML + vanilla JS, no
  bundler/CDN/SPA; escape all rendered text; semantic HTML; server-side SVG
  charts; errors surfaced, never swallowed. Spec: docs/TASKS.md Phase 6ter.

## Git

Public repo, solo dev, no PR — commit straight to `main`, one commit per
milestone (`feat: M<N> — description`), push with `gh`/https. English is the
sole language of the project: code, comments, docs, commits, identifiers.

## Definition of Done

Per-milestone "Definition of Verified" lives in docs/MILESTONES.md (the
execution checklist); the V1-wide 13-point Definition of Done lives in
docs/TASKS.md and docs/USE_CASES.md UC0 (seed inventory, weekly cycle,
maturation, replay gate, outcome scoreboard).
