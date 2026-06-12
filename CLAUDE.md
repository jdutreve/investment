# CLAUDE.md — Investment Agent (MVP Core)

See REVISION_NOTES.md for V1 scope, core concepts, ranking rule, and stagflation/deflation tagging.

Read this file before any action. Implement in the order defined in
investment-TASKS.md. Also read investment-ARCHITECTURE.md and DATA_MODELS.md
before writing any code. See IMPROVEMENTS.md for deferred features and when
to add them.

---

## Objective

Build capital for retirement (Phase 1: accumulation only).

V1 delivers a portfolio ranking and digest engine. It does not auto-apply
allocation changes.

V1 mechanisms:
1. Detect the current 4 Seasons regime from market/macro data, using level,
   speed, and acceleration to anticipate regime shifts.
2. Include global liquidity as a first-class MarketData family
   (`asset_class=GLOBAL_LIQUIDITY`) and combine it with 4 Seasons interpretation.
3. Rank all enabled `Portfolio` rows, including the defender, using USD
   `sharpe_rolling`, `sortino_rolling`, `calmar_rolling`, `max_drawdown`,
   `volatility`, plus cumulative `return_3m / 6m / 1y / 3y / 5y`. Indicator
   (never "ratio") is the canonical generic term.
4. Explain the ranking through frameworks, regimes, strategies, invariants,
   and market context.
5. Produce a weekly Telegram digest and optional paper-mode `Proposal`.

V2 adds auto-application, 48h auto-validation, and automatic learning from
real `performance_3m`.

---

## Strict Planner / Worker Separation

```
PLANNER (Qwen3-8B, OpenRouter, thinking mode)
  System prompt : meta-cognitive strategy
  DB access     : direct Python asyncio — NO tool_call
  Call 1a       : LLM returns JSON parameters (tool_use)
                  PYTHON CODE executes DB queries with those parameters
  Call 1b       : LLM receives raw DB results, returns PlannerContext
  Call 2        : async, post-Worker, knowledge extraction

WORKER (Sonnet 4.6, Anthropic API)
  System prompt : investment expert Phase 1 accumulation
  Markdown Skills : strategy evaluation, ranking, indicator interpretation,
                    defender comparison
  DB access     : via tool_call ONLY (ToolContextWrapper, DI)
  3 tools       : db_query | market_fetch | portfolio_check
  Indicators    : already in ArcadeDB — Worker interprets, does NOT calculate
  Unaware of    : Planner, Writeback, internal structure

MECHANICAL JOBS (APScheduler, pure Python, no LLM)
  One-time
    UC0    seed → DB bootstrap (CLI command, not cron)
  Daily
    02:00  inbox parser → Document/Passage vertices + SUPPORTS edges
           (parse + chunk + embed only — invariant curation is weekly UC4)
    06:30  fetch market data + level/speed/acceleration → MarketData TS
    06:35  Sharpe/Sortino/Calmar (rolling) → PortfolioNAV TS
    06:45  Scenario probabilities + 7-day shifts → ScenarioProbability TS
           (numeric triggers only; qualitative triggers are Worker-interpreted
            weekly — see IMPROVEMENTS I-22)
    06:50  regime detection (4 Seasons) → Regime vertex (is_current)
  Weekly (Monday — canonical timeline, see USE_CASES.md)
    08:00  UC2 market valuation → MarketEvent
    08:10  UC3 knowledge search → inbox
    08:20  UC4 knowledge curation (LLM) → KnowledgeEvent
    08:30  Backtests recalculated → FAVORS edges (RegimeType → Strategy)
    08:40  Invariant weights recalculated
    08:45  UC6 portfolio valuations → Portfolio vertices + ValuationEvent
    08:50  UC7 ranking → portfolio_weekly_snapshot rows
    08:55  V2 only: learn_from_adaptations
    09:00  UC8 Worker decision cycle (Planner Pre → Worker → Planner Post)
    09:30  Weekly digest → Telegram user
  Event-driven
    Invariant weights after each Backtest or Evaluation
```

---

## Stack

| Component       | Value                                                         |
|-----------------|---------------------------------------------------------------|
| DB              | `arcadedb-embedded` (Apache 2.0) in-process, ARM64           |
| DB path         | `/data/investment/arcade_db/investment.db`                    |
| LLM Framework   | PydanticAI V1 (model-agnostic)                                |
| Planner LLM     | `qwen/qwen3-8b` via OpenRouter, thinking mode                 |
| Worker LLM      | `claude-sonnet-4-6` via Anthropic                             |
| Market data     | Yahoo Finance + FRED + GLOBAL_LIQUIDITY composite             |
| Risk-free rate  | 3-Month T-Bill (^IRX) via Yahoo Finance — USD                 |
| Currency        | USD for all indicators; CHFUSD=X for display only             |
| Ingestion       | Telegram bot + SCP → inbox/ (nightly job 02:00)               |
| Veille          | RSS feeds + user deposits                                     |
| Notifications   | Telegram weekly digest (Mon 09:30) + Proposal alerts          |
| Process         | APScheduler, single Python process                            |
| Service         | systemd `investment-agent.service`                            |

---

## Non-negotiable Rules

### ArcadeDB
- Agent = sole writer. Writes serialized via asyncio (single process).
- `trace` mandatory on every vertex — `ValueError` if empty.
- DB opened once in `main.py`, injected everywhere.

### Event Time-Series — source of truth for UC8
**Every UC side-effect must be appended to the Event TS BEFORE being committed
elsewhere in ArcadeDB.** Architectural invariant for auditability and replay.

### Decision cycle
- **Daily** = mechanical only. No LLM.
- **Weekly (Monday 09:00)** = sole decision cycle. Worker + Planner Post.
- V1 proposals → Telegram digest + `Proposal` vertex only.
  No automatic application. V2 = auto-validation and auto-application.

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
  - `recency_factor = max(0.5, 0.5 + 0.5 × exp(-days_since / half_life))`
- `market_score = confirmation_count / (confirmation_count + infirmation_count)`
  (use 1.0 until first confrontation)
- Event-driven update after each Backtest or Evaluation.
- `source=agent-discovery` → Event TS append → vertex committed with
  `status=proposed` → Telegram notification in the same cycle. Never
  `integrated` without `user_validated=True`.

### Curation vs Innovation
- **Curation** (autonomous): update weight, add confirmations, add SUPPORTS
  edges, enrich description/example on **existing integrated** Invariants.
- **Innovation** (user validation required): create a new Invariant
  (`source=agent-discovery`), new vertex/edge type, new metric. `status=proposed`
  until `user_validated=True`.

### Worker
- Never mention Writeback/Planner/storage in Worker prompts.
- `status:integrated` only after `user_validated=True`.
- `_db` captured by closure in PlannerPre.run() — never exposed to Worker.
- Bridged functions (principle of least privilege):
  - `db_query` : FORBIDDEN_SQL whitelist, max 20 rows
  - `market_fetch` : ALLOWED_TICKERS only, max 30 rows
  - `portfolio_check` : ID format validation, limited exposed fields
- `WorkerResult` must include `innovations_proposed: list[ImprovementProposal]`
  (empty list if none).

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

### Concentration limit
- `Portfolio.max_single_asset_pct` (default 40%): no single asset above this.
- Proposal blocked by Writeback if the implied challenger allocation violates.

### Strategies
- 4 strategies seeded: 4seasons, permanent, barbell, momentum-macro.
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

## ArcadeDB Entities (13 vertices, 11 edges, 4 time-series — see DATA_MODELS.md)

```
VERTEX : Framework, RegimeType, Regime, Invariant, Strategy, Scenario,
         Evaluation, Backtest, Adaptation (V2-only), Proposal,
         Portfolio, Document, Passage
         (Signal vertex dropped from V1 — see IMPROVEMENTS I-19)

EDGES  : UPDATES,
         FAVORS (RegimeType → Strategy, multi-period aggregated, strategy-level),
         HAS_SCENARIO, BACKED_BY, TESTED_IN,
         IN_REGIME (Backtest → Regime instance),
         MODIFIES (V2), HOLDS (Portfolio → Strategy, primary BOOLEAN),
         DESIGNED_FOR (Portfolio → RegimeType), CONTAINS, SUPPORTS
         (IMPLIES and GENERATES dropped from V1 — see IMPROVEMENTS I-19;
          Evaluation records its triggering observations in `events`)

TIME-SERIES : MarketData (level/speed/acceleration), ScenarioProbability,
              PortfolioNAV, Event
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

1. UC0 seed produces 13 vertex types, 11 edge types, 4 time-series, seed data,
   and the first `portfolio_weekly_snapshot` row.
2. `update_ratios_daily()` populates PortfolioNAV TS daily (USD).
3. `detect_regime()` creates/updates a Regime vertex with `is_current=true`
   using level/speed/acceleration.
4. After Dalio corpus ingestion: 10+ Passage + Invariant vertices with weights.
5. Full weekly cycle: MarketData/Event ingestion → Worker → Evaluation →
   Scenario update → Proposal (if gate passed).
6. `source=agent-discovery` Invariant is persisted as `status=proposed` and
   triggers a Telegram notification in the same cycle.
7. `weight_effective` of an agent-discovery invariant grows after market confirmations.
8. `learn_from_adaptations()` (V2) propagates `performance_3m` to BACKED_BY invariants.
9. Every Event TS append precedes its corresponding ArcadeDB vertex/edge commit.
10. Concentration limit blocks a Proposal whose challenger allocation would
    violate `max_single_asset_pct`.
