# CLAUDE.md — Investment Agent (MVP Core)

See REVISION_NOTES.md for V1 scope, core concepts, ranking rule, and stagflation/deflation tagging.

Read this file before any action. Implement in the order defined in
investment-TASKS.md. Also read investment-ARCHITECTURE.md and DATA_MODELS.md
before writing any code. See IMPROVEMENTS.md for deferred features and when
to add them, and DECISIONS.md for the ADRs (engine spike gate, local macOS
target, vintage discipline) — never contradict an accepted ADR silently.

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

WORKER (Sonnet 4.6, Anthropic API)
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
           → curation runner (LLM) — ONLY when the batch created new
             Documents: invariant candidates (author = document author
             tier) → Telegram validation within minutes of the deposit.
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
           days; candidate state persisted; start_dates come from the
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
| Worker LLM      | `claude-sonnet-4-6` via Anthropic                             |
| Market data     | Yahoo Finance + FRED + GROWTH_COMPOSITE + GLOBAL_LIQUIDITY    |
| Backfill        | 25y macro (ALFRED first-release vintages, publication-dated   |
|                 | — ADR-003); ETFs limited by inception date                    |
| Risk-free rate  | 3-Month T-Bill (^IRX) via Yahoo Finance — USD                 |
| Timezone        | Europe/Zurich (APScheduler + all cron times)                  |
| Currency        | USD for all indicators; CHFUSD=X for display only             |
| Ingestion       | Telegram bot + local drop → inbox/ (watcher, ~5 min)          |
| Embeddings      | sentence-transformers in-process, 384 dims (no daemon)        |
| Veille          | UC3 Event Watch: pinned official sources (Fed/ECB/SNB press,  |
|                 | LLM triage, bounded-fetch enrichment) + user deposits/notes.  |
|                 | Quantitative shocks are mechanical (VIX/liquidity tags).      |
|                 | General auto-veille deferred — I-9/I-26                       |
| Notifications   | Telegram weekly digest (Mon 09:30) + Proposal alerts          |
| Process         | APScheduler, single Python process                            |
| Host            | Local MacBook Pro M5 (macOS ARM64), 24 GB — see DECISIONS.md  |
| Service         | launchd LaunchAgent `com.jp.investment-agent`; weekly chain   |
|                 | DUE-ON-START at launch/wake (laptop sleep — TASKS Task 0.7)   |

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
and replay. Exemption: pure TS writes (UC1 market feed, weekly NAV catch-up and scenario
jobs) append no EventLog row — they create no vertex/edge.

### Decision cycle
- **Event-driven ingestion** = mechanical, with ONE LLM exception: the
  curation runner (fires minutes after a deposit; knowledge extraction
  from newly ingested documents — its outputs are
  `status=proposed` candidates gated by user validation, never decisions).
- **Weekly (Monday 09:00)** = sole *scheduled* decision cycle. Worker +
  Planner Post. UC9 (user-initiated chat) may trigger one ad-hoc UC8 re-run
  per day — user-initiated, so it does not break the autonomy rule.
- V1 proposals (switch or reallocation) → Telegram digest + `Proposal`
  vertex only. No automatic application. V2 = auto-validation and
  auto-application.

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
  - `days_since = (today - updated_at).days`   ← updated_at = last confrontation
  - `half_life = 365 days`
  - `recency_factor = 0.5 + 0.5 × exp(-days_since / half_life)`
    (decays from 1.0 toward an asymptotic floor of 0.5 — no clamp needed)
- `market_score = confirmation_count / (confirmation_count + infirmation_count)`
  (use 1.0 until first confrontation)
- Event-driven update after each Backtest or Evaluation.
- `source=agent-discovery` → EventLog append → vertex committed with
  `status=proposed` → Telegram notification in the same cycle. Never
  `integrated` without `user_validated=True`.

### Curation vs Innovation
- **Curation** (autonomous): update weight, add confirmations, add SUPPORTS
  edges, enrich description/example on **existing integrated** Invariants.
- **Innovation** (user validation required): create a new Invariant, new
  or revised Strategy (`type=new_strategy` / `strategy_revision`,
  `enabled=false` until validated — lifecycle in ARCHITECTURE "System
  Evolution"), new metric. `status=proposed` until `user_validated=True`.
  Schema self-extension (new vertex/edge types) is V2 — IMPROVEMENTS I-27.
- **Author tier of new Invariants**: extracted from a corpus document →
  `author = Document.author` tier (dalio/marks/null — floor 0.40/0.35/0.20);
  discovered from market patterns (backtests, rankings) →
  `author='system'` (floor 0.05). `source` always records the real
  provenance in free text. The UC0 initial curation pass (USE_CASES step 4b,
  DEFAULT — skip with `--no-curate`) lets a deposited book yield validated
  invariants at install time; later deposits are curated within minutes
  (watcher → ingestion batch → curation runner).

### Worker
- Never mention Writeback/Planner/storage in Worker prompts.
- `status:integrated` only after `user_validated=True`.
- `_db` captured by closure in PlannerPre.run() — never exposed to Worker.
- Bridged functions (principle of least privilege):
  - `db_query` : FORBIDDEN_SQL whitelist, max 20 rows
  - `market_fetch` : ALLOWED_TICKERS only, max 30 rows
  - `portfolio_check` : ID format validation, limited exposed fields
- `WorkerResult` must include `innovations_proposed: list[ImprovementProposal]`
  (empty list if none) and `reallocation_proposed:
  Optional[ReallocationProposal]` (see DATA_MODELS.md).

### Unified improvement cycle — proposal → measure → adoption
- Applies to ALL improvable resources (Proposal, Invariant, Strategy,
  scenario probabilities, thresholds): measure current performance →
  propose → user gate where required → mechanical maturation window →
  adopt or reject. Nothing stays "proposed" forever and nothing is adopted
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
  (`status=integrated` AND `weight_effective ≥
  proposal_invariant_weight_min`). See USE_CASES.md UC8.

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
- All formulas pinned in DATA_MODELS.md "Calculation conventions"
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

## Entities (conceptual graph: 13 entities, 10 relations — physically 5 M:N tables + 5 FK columns — 3 time-series; V2 adds Adaptation + MODIFIES; see DATA_MODELS.md mapping)

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
              system_thresholds, invariant_confrontations,
              portfolio_weekly_snapshot, scenario_calibration, replay_report
              (plain tables, single engine — weight/history/
               performance data live on vertices and FAVORS edges, never
               duplicated in docs)
```

See DATA_MODELS.md for the complete schema and properties.

---

## Git Workflow

```bash
git add .
git commit -m "feat: Phase N — description"
git push origin main
```

Private repo, solo dev — no PR. `gh` CLI sufficient.

---

## Definition of Done

1. UC0 seed produces 13 vertex types, 10 edge types, 3 time-series, the
   document types, historical Regime instances from the 25y backfill, seed
   data, and the first `portfolio_weekly_snapshot` row.
2. `update_ratios()` (Monday 08:00 catch-up) populates PortfolioNAV TS for
   every trading day (USD).
3. `detect_regime()` creates/updates a Regime vertex with `is_current=true`
   using level/speed/acceleration, with hysteresis and a computed confidence.
4. After Dalio corpus ingestion + `--curate` batch validation: 10+ Passage
   vertices, and extracted Invariants carrying `author='dalio'` (floor 0.40)
   with weights, linked by SUPPORTS edges.
5. Full weekly cycle: MarketData/EventLog ingestion → Worker → Evaluation →
   Scenario update → Proposal (if gate passed).
6. `source=agent-discovery` Invariant is persisted as `status=proposed` and
   triggers a Telegram notification in the same cycle.
7. `weight_effective` of an agent-discovery invariant grows after mechanical
   market confirmations (ARCHITECTURE "Invariant confrontation rule").
8. `learn_from_adaptations()` (V2) propagates `performance_3m` to BACKED_BY invariants.
9. Every EventLog append precedes its corresponding entity/relation commit.
10. Writeback blocks any Proposal (switch or reallocation) whose implied
    allocation violates the binding user caps.
11. The Worker can emit a reallocation Proposal for the defender that passes
    the mechanical gates and renders in the digest with old vs new
    allocation and reasoning.
12. The Phase 9 shadow replay produces a 25y `replay_report` with zero
    point-in-time violations, and `main.py` refuses to enable the weekly
    proposal cycle when the report shows no net value-add on the validation
    window (override `--force-live`).
13. A Proposal older than `proposal_outcome_weeks` carries an
    `outcome.verdict` (won/lost), its cited invariants show a matching
    `invariant_confrontations` row with `source='proposal'`, and the digest
    renders the scoreboard (hit-rate, paper-tests, probations).
