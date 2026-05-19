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
3. Rank all enabled `Portfolio` rows, including the live defender, using USD
   `sharpe_rolling`, `sortino_rolling`, `calmar_rolling`, `max_drawdown`,
   `volatility`, `total_return`.
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

WORKER (Sonnet 4, Anthropic API)
  System prompt : investment expert Phase 1 accumulation
  Markdown Skills : strategy evaluation, ranking, ratio interpretation,
                    defender comparison
  DB access     : via tool_call ONLY (ToolContextWrapper, DI)
  3 tools       : db_query | market_fetch | portfolio_check
  Ratios        : already in ArcadeDB — Worker interprets, does NOT calculate
  Unaware of    : Planner, Writeback, internal structure

MECHANICAL JOBS (APScheduler, pure Python, no LLM)
  One-time
    UC0    seed → DB bootstrap (CLI command, not cron)
  Daily
    02:00  inbox processor → CorpusIngester
    06:30  fetch market data + level/speed/acceleration → MarketData TS
    06:35  Sharpe/Sortino/Calmar (rolling) → PortfolioNAV TS
    06:45  Scenario probabilities + 7-day shifts → ScenarioProbability TS
    06:50  regime detection (4 Seasons) → Regime vertex (is_current)
  Weekly (Monday)
    08:00  Portfolio valuations recalculated → portfolio_weekly_snapshot
    08:15  Invariant weights recalculated
    08:30  V2 only: learn_from_adaptations
    09:00  Worker decision cycle (Planner Pre → Worker → Planner Post)
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
| Worker LLM      | `claude-sonnet-4-20250514` via Anthropic                      |
| Market data     | Yahoo Finance + FRED + GLOBAL_LIQUIDITY composite             |
| Risk-free rate  | 3-Month T-Bill (^IRX) via Yahoo Finance — USD                 |
| Currency        | USD for all ratios; CHFUSD=X for display only                 |
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
- Sources in V1: `corpus` and `agent-discovery`.
- `weight_effective = max(weight_initial × market_score × recency_factor, floor_weight)`
- `floor_weight` by source category, persisted at creation:
  - corpus (dalio): 0.40
  - corpus (marks): 0.35
  - corpus (other): 0.20
  - agent-discovery: 0.05
- `recency_factor` (single half-life in V1):
  - `days_since = (today - last_confronted).days`
  - `half_life = 365 days`
  - `recency_factor = max(0.5, 0.5 + 0.5 × exp(-days_since / half_life))`
- `market_score = confirmation_count / (confirmation_count + infirmation_count)`
  (use 1.0 until first confrontation)
- Event-driven update after each Backtest or Evaluation.
- `source=agent-discovery` + `status=proposed` → Telegram BEFORE commit.

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
- Calmar window: rolling 36 months (756 trading days).
- All ratios in USD; `*_rolling` suffix everywhere on Portfolio/Backtest/FAVORS.
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
- `Regime` vertex = one concrete occurrence (start/end date, confidence,
  signals_count, dynamic tags). Created/updated by `detect_regime()`.
- Deflation = dynamic tag on Regime instances, never a RegimeType.
- Detection uses `level`, `speed`, `acceleration` on MarketData TS.
- FAVORS and DESIGNED_FOR point to RegimeType (type-level, multi-period).
- IMPLIES and IN_REGIME point to Regime (instance-level).

### FX
- `Portfolio.fx_usd_exposure` tracked, informational only.
- No hedging in Phase 1.

---

## ArcadeDB Entities (14 vertices, 13 edges, 4 time-series)

```
VERTEX : Framework, RegimeType, Signal, Regime, Invariant, Strategy, Scenario,
         Evaluation, Backtest, Adaptation (V2-only), Proposal,
         Portfolio, Document, Passage

EDGES  : IMPLIES (Signal → Regime instance), GENERATES, UPDATES,
         FAVORS (RegimeType → Strategy, multi-period aggregated),
         HAS_SCENARIO, BACKED_BY, TESTED_IN,
         IN_REGIME (Backtest → Regime instance),
         MODIFIES (V2), HOLDS (Portfolio → Strategy, primary BOOLEAN),
         DESIGNED_FOR (Portfolio → RegimeType), CONTAINS, SUPPORTS

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

1. UC0 seed produces 13 vertex types, 13 edge types, 4 time-series, seed data,
   and the first `portfolio_weekly_snapshot` row.
2. `update_ratios_daily()` populates PortfolioNAV TS daily (USD).
3. `detect_regime()` creates/updates a Regime vertex with `is_current=true`
   using level/speed/acceleration.
4. After Dalio corpus ingestion: 10+ Passage + Invariant vertices with weights.
5. Full weekly cycle: Signal accumulation → Worker → Evaluation → Scenario
   update → Proposal (if gate passed).
6. `source=agent-discovery` Invariant triggers Telegram notification before commit.
7. `weight_effective` of an agent-discovery invariant grows after market confirmations.
8. `learn_from_adaptations()` (V2) propagates `performance_3m` to BACKED_BY invariants.
9. Every Event TS append precedes its corresponding ArcadeDB vertex/edge commit.
10. Concentration limit blocks a Proposal whose challenger allocation would
    violate `max_single_asset_pct`.
