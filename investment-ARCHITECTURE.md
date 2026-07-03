# ARCHITECTURE.md — Investment Agent MVP

See REVISION_NOTES.md for V1 scope and core concepts.

## Objective

Build capital (10-20 year horizon) via a self-improving expert investment agent.

V1 is a portfolio ranking and digest engine. V2 adds auto-adaptive execution
and full market-confronted learning.

The agent improves through:
1. **Corpus maturation** — Documents → Passages → Invariants.
2. **Framework reasoning** — frameworks interpret markets and design/refine
   strategies and portfolios.
3. **Market confrontation** — in V1 via paper-mode `Proposal` vertices and
   weekly snapshots; in V2 via real `performance_3m` propagated to invariants.

Maturation, ranking, and persistence are the core of the system.
The agent is the sole writer. Writes serialized via asyncio.

See IMPROVEMENTS.md for deferred features.

---

## DB Stack

```
SQLite single file (ADR-004)
  → in-process, ARM64 supported
  → Graph + Vector + FTS + SQL + Time-Series in one engine
  → Single Python process = sole writer = no contention

Filesystem
  → Binary sources only (PDF, Kindle CSV) at ~/data/investment/sources/
  → Referenced via Document.source_path
```

---

## Planner / Worker Asymmetry

| | Planner (Qwen3-8B, OpenRouter) | Worker (Sonnet 4.6, Anthropic) |
|--|--------------------------------|------------------------------|
| DB access | Direct Python asyncio | tool_call via bridged functions |
| `_db` | direct, in-process | never — closure only |
| LLM calls | 3 fixed (1a, 1b, 2) | variable (1-8 tool calls) |
| DB writes | forbidden | forbidden (Writeback handles) |
| Scope | full DB read | 3 bridged functions only |

```python
# Bridged functions (Worker only):
db_query(sql)                   # READ only, max 20 rows
market_fetch(tickers, period)   # ALLOWED_TICKERS only, max 30 rows
portfolio_check(portfolio_id)   # ID validation, limited fields
# _db captured by closure in PlannerPre.run() — invisible to Worker
```

---

## System Prompts

```
PLANNER system prompt:
  "You are the cognitive coach of an expert investment agent.
   You prepare the optimal context. You never reason about
   the strategies themselves."

WORKER system prompt:
  "You are a long-term investment expert, Phase 1 accumulation.
   Build capital for retirement over 15-20 years.
   Evaluate strategies, rank portfolios, compare challengers against the
   defender, propose paper-mode adjustments. You may propose adjusting the
   defender's own allocation (blend 0.4 × active-scenario target +
   0.6 × regime-favored structural anchor), citing the invariants that
   support it. V1 never auto-executes; final gates are applied outside you.
   Use the Skills provided and the data in your context.
   You are unaware of the Planner, Writeback, and internal storage.
   Three tools: db_query, market_fetch, portfolio_check.
   Sharpe/Sortino/Calmar are pre-calculated indicators in USD in the DB;
   the suffix is _rolling. Interpret them — do not recalculate.
   Rolling window is 36 months. Risk-free rate is 3M T-Bill (^IRX).
   WorkerResult must include innovations_proposed (empty list if none)
   and reallocation_proposed (null if none)."
```

---

## Agent Cognitive Cycle

```
SEED      (UC0) corpus + frameworks + regimes + invariants + strategies +
                portfolios + first snapshot (one-shot)
INGESTS   corpus → Passages → Invariants (UC4)
DETECTS   regime (4 Seasons) from MarketData TS with level/speed/acceleration
RANKS     all enabled portfolios, including the defender, by USD
          *_rolling indicators + drawdown (weekly)
PROPOSES  V1 emits paper-mode Proposal vertices when a gate is met:
          switch (mechanical gates) or reallocation (Worker-proposed,
          Writeback-validated)
V2 ADAPTS Telegram with auto-validation timeout (V2 only)
MEASURES  PortfolioNAV + weekly snapshots with rolling indicators (USD)
V2 LEARNS Adaptation × performance_3m → BACKED_BY invariant weights
```

---

## Entities — Overview

```
GRAPH VERTICES (13 in V1 — V2 adds Adaptation)
  Framework       lens for market interpretation; seeded '4seasons'
  RegimeType      static regime definition per framework
  Regime          detected macro regime instance; id <alias>-<start_date>
  Invariant       universal principle with dynamic weight, author-tier floor,
                  tags, real-source provenance
  Strategy        thesis; seeded ids four-seasons-rp / permanent-browne /
                  barbell-taleb / momentum-macro (never collide with
                  Framework ids)
  Scenario        bull/base/bear per strategy (weekly shift detection)
  Evaluation      MarketData × Strategy crossing → weekly verdict
  Backtest        Strategy × Regime instance historical performance
  Proposal        V1 paper-mode recommendation (switch | reallocation)
  Portfolio       concrete ETF allocation; ranking unit; defender=true
  Document        corpus source
  Passage         RAG unit (chunk + embedding)
  EventLog        append-only audit log (no edges) — APPEND BEFORE any
                  vertex/edge commit

GRAPH EDGES (10 in V1 — V2 adds Adaptation → MODIFIES → Portfolio)
  Evaluation → UPDATES       → Strategy
  RegimeType → FAVORS        → Strategy (strategy-level rolling indicators,
                                aggregated across n_periods historical instances)
  Strategy   → HAS_SCENARIO  → Scenario (3 per Strategy)
  Strategy   → BACKED_BY     → Invariant
  Strategy   → TESTED_IN     → Backtest
  Backtest   → IN_REGIME     → Regime instance
  Portfolio  → HOLDS         → Strategy (primary BOOLEAN, weight, since)
  Portfolio  → DESIGNED_FOR  → RegimeType (nullable)
  Document   → CONTAINS      → Passage
  Passage    → SUPPORTS      → Invariant

TIME-SERIES (3)
  MarketData          level/speed/acceleration per (ticker, asset_class).
                      Growth axis = GROWTH_COMPOSITE; global liquidity =
                      asset_class=GLOBAL_LIQUIDITY. `level` is the canonical
                      value; regime membership via date lookup on Regime.
  ScenarioProbability bull/base/bear probabilities per Strategy (weekly)
  PortfolioNAV        rolling indicators per day (USD)

DOCUMENT TYPES      user_profile, allowed_tickers, system_thresholds,
                    invariant_author_config, invariant_confrontations,
                    portfolio_weekly_snapshot,
                    scenario_calibration (weekly calibration scores),
                    replay_report (Phase 9 shadow replay / go-live gate)
                    — weight/history/performance data live on the graph
                    vertices and FAVORS edges, never duplicated in docs
```

Benchmark vertex and hypotheses document type are deferred — see IMPROVEMENTS.md.

---

## Regime Detection (4 Seasons only in V1) — formal algorithm

Thresholds loaded from `system_thresholds` — not hardcoded. Growth axis =
`GROWTH_COMPOSITE` (z(INDPRO YoY) − z(UNRATE Δ3m), rebased 100 — see
DATA_MODELS.md); inflation axis = CPI YoY (CPIAUCSL transformed).

**Axis classification (daily 06:50, on latest MarketData rows):**

```
growth_dir    = 'rising'  if GROWTH_COMPOSITE.speed > +regime_growth_noise (0.15)
              = 'falling' if GROWTH_COMPOSITE.speed < −regime_growth_noise
              = 'flat'    otherwise
inflation_dir = 'rising'  if CPI_YOY.speed > +regime_cpi_noise (0.05)
                           AND CPI_YOY.level > regime_cpi_stagflation (2.5)
              = 'rising'  if CPI_YOY.speed > +regime_cpi_noise (level ≤ 2.5 →
                           counts as rising only with accel > 0)
              = 'falling' if CPI_YOY.speed < −regime_cpi_noise
              = 'flat'    otherwise

candidate = quadrant(growth_dir, inflation_dir)   -- 'uncertain' if any axis flat
```

**Hysteresis:** a regime CHANGE is committed only after the same candidate
quadrant has been produced by `regime_confirm_prints` (2) **consecutive
monthly observations of each axis**. Both axes are monthly series
(GROWTH_COMPOSITE from INDPRO/UNRATE, CPI YoY) — a trading-day window would
be trivially satisfied between prints, so confirmation counts new prints,
not calendar days. Until confirmed, `is_current` stays on the previous
instance and the candidate is tracked in memory. On commit: previous Regime gets
`end_date`, new Regime vertex created (`<alias>-<start_date>`). Exactly one
`is_current=true` per framework — enforced in the same transaction.

**Confidence (0-100):**

```
axis_strength(a) = min(1, |speed_a| / speed_scale_a)     -- scales in thresholds
accel_bonus      = 10 if sign(accel)==sign(speed) on BOTH axes else 0
confidence       = clamp(50 + 20×axis_strength(growth)
                            + 20×axis_strength(inflation) + accel_bonus, 0, 100)
```

**Tags layered on top (instance-level):** `deflation` when CPI YoY < 0;
`liquidity-tightening` (GLOBAL_LIQUIDITY level < 100 AND speed < 0) /
`liquidity-easing` (level > 100 AND speed > 0); `market-stress` when
^VIX > regime_vix_stress (25).

A RegimeEvent is appended to EventLog only when the regime, confidence band
(±10), or tag set changes.

Strategy conditions must include ≥1 indicator orthogonal to regime
thresholds, and every referenced indicator must be computable from
MarketData TS or Regime fields.

---

## Invariant confrontation rule (mechanical, V1)

How confirmations/infirmations are generated without V2 real executions.
Runs in the weekly 08:40 step, after Backtests/FAVORS refresh, and after each
Evaluation commit.

```
FROM BACKTESTS (source='backtest'):
  Let rt = current regime type. After the weekly FAVORS refresh:
  For each Strategy s with a refreshed FAVORS edge from rt:
    median = median(favors.sortino_rolling) across all strategies for rt
    For each Invariant i in BACKED_BY(s) where
        'regime:<rt.id>' ∈ i.tags OR i.tags has no 'regime:*' tag:
      if favors(s).sortino_rolling ≥ median:
          confirmation(i, severity=1.0)
      elif favors(s).sortino_rolling < median − confrontation_margin (0.10):
          infirmation(i, severity=1.0)
      else: no-op
  Only the CURRENT regime type confronts — historical cells do not re-confront
  weekly (they already did at seed).

FROM EVALUATIONS (source='evaluation'):
  verdict='confirms'     → confirmation for each BACKED_BY invariant of the
                           evaluated strategy (severity=1.0)
  verdict='invalidates'  → infirmation (severity=1.0)
  'weakens' | 'neutral'  → no count change

FROM PROPOSALS (source='proposal') — closes the loop on emitted proposals:
  Run by evaluate_proposals() (weekly 08:52 — see "Unified improvement
  cycle" below). When a Proposal reaches proposal_outcome_weeks (12) of age:
  verdict='won'  → confirmation for each cited invariant
                   (reallocation: supporting_invariants; switch: the
                    challenger's BACKED_BY invariants), severity=1.0
  verdict='lost' → infirmation, severity=1.0

Each confrontation: append invariant_confrontations doc → update counts →
update_invariant_weights() (weight_effective formula in CLAUDE.md) →
Invariant.updated_at = today (drives recency_factor).
Severity is recorded but unused in market_score in V1 (IMPROVEMENTS I-24).
```

---

## Unified improvement cycle (ALL resources)

Every improvable resource follows the SAME lifecycle — this is the system's
core loop, and each stage is mechanical except the proposal itself:

```
measure current performance → PROPOSE → user gate (where required)
  → MATURATION (mechanical measurement over a fixed window)
  → ADOPT (validated principle — no longer a proposal) | REJECT
```

| Resource            | Proposer          | Measure (mechanical)                          | Maturation window            | Adopt / Reject                                  |
|---------------------|-------------------|-----------------------------------------------|------------------------------|--------------------------------------------------|
| Proposal (switch)   | Writeback gates   | proposed vs incumbent NAV since `date`        | proposal_outcome_weeks (12)  | outcome.verdict won/lost + confrontations        |
| Proposal (realloc)  | Worker            | proposed vs incumbent NAV since `date`        | proposal_outcome_weeks (12)  | outcome.verdict won/lost + confrontations        |
| Invariant           | Worker / curation | confrontation rule (backtest/evaluation/proposal) | continuous (recency decay) | weight_effective vs floor; realloc gate 6 eligibility |
| Strategy (new/revision) | Worker        | FAVORS refresh after activation               | strategy_probation_weeks (12) | probation verdict: keep / propose closure       |
| Scenario probabilities | daily job + Worker | calibration: dominant scenario vs realized  | scenario_calibration_weeks (4) | score feeds Worker context + Strategy conviction |
| Thresholds          | Phase 9 replay    | walk-forward calibration                      | 15y calibrate / 10y validate | user-confirmed write to system_thresholds        |

**`mechanical/outcomes.py` — weekly 08:52 (after ranking, before UC8):**

```
evaluate_proposals():
  For each Proposal with outcome.verdict='pending' and
      age >= proposal_outcome_weeks:
    proposed_return  = synthetic NAV return of the proposed allocation
                       (switch: challenger allocation; realloc:
                        proposed_allocation) since Proposal.date,
                       per the pinned NAV conventions, net of
                       replay_cost_bps × turnover
    incumbent_return = defender allocation as of Proposal.date, held
    outcome = {proposed_return, incumbent_return,
               verdict: 'won' if proposed > incumbent else 'lost'}
    → OutcomeEvent (kind=proposal) → Proposal.outcome + evaluated_at
    → invariant confrontations source='proposal' (rule above)
  Accepted paper-tests (paper_started set) are additionally tracked EVERY
  week from paper_started and rendered in the digest scoreboard.

score_scenarios():
  For each Strategy, at +scenario_calibration_weeks: was the realized
  regime/quadrant the scenario that held the dominant probability?
  → scenario_calibration doc row (strategy_id, date, brier-style score)
  → OutcomeEvent (kind=calibration, batch)

strategy_probation_check():
  For each Strategy activated (new or revision) strategy_probation_weeks
  ago: compare its FAVORS percentile in the current regime type vs the
  median → OutcomeEvent (kind=probation) verdict 'keep' | 'review'
  ('review' → Telegram: propose closure, user decides)
```

The digest renders a **scoreboard**: cumulative proposal hit-rate (the live
continuation of the Phase 9 replay's hit_rate_12w), paper-tests in progress
(proposed vs incumbent to date), strategies in probation, scenario
calibration flags. The system's week-over-week improvement is measured
here — not asserted.

---

## Strategy Library + Comparison

```
Seeded strategies (all enabled=true):
  four-seasons-rp    Dalio risk parity
  permanent-browne   Browne 25/25/25/25
  barbell-taleb      Taleb safety + convexity
  momentum-macro     dynamic rotation by regime

Mechanical (weekly):
  → Backtest per Strategy × RegimeType cell where data coverage suffices
  → Scenario bull/base/bear with weekly shift probabilities
  → RegimeType → FAVORS → Strategy (strategy-level rolling indicators,
                          aggregated across all historical instances)
Worker (weekly):
  → ranks portfolios, not strategies (it's the portfolio that is valued)
  → compares challengers against the defender
```

---

## 3 Scenarios per Strategy

Probabilities of bull/base/bear must always sum to 100.

```
Strategy "4 Seasons" — example

  Scenario bull (35%)  triggers: CPI_YOY < 2.5 AND GROWTH_COMPOSITE > 102
                                 AND "Fed dovish" (qualitative)
  Scenario base (45%)  triggers: CPI_YOY 2.5-3.5 AND "Fed pause" (qualitative)
  Scenario bear (20%)  triggers: ^VIX > 25 OR (CPI_YOY > 4 AND GROWTH_COMPOSITE < 98)

Probability mechanics in V1: the weekly Monday 08:35 mechanical job
evaluates **numeric triggers only** (e.g. "CPI<2.5", "VIX>25") against
MarketData TS and computes shift_d7 (weekly, not daily — probability values
only change via the weekly Worker cycle); qualitative triggers ("Fed
dovish") are interpreted exclusively by the Worker, which reviews and may
adjust probabilities. Formal trigger grammar deferred — see IMPROVEMENTS
I-22.
V1 uses shifts as context for proposals.
V2 may use shift thresholds for auto-adaptive execution.
```

**Proposal/Adaptation delta blending (V1 paper-mode, V2 real):**
- Scenario allocation = tactical short-term override
  (active scenario's `target_allocation` − current defender allocation).
- FAVORS-derived allocation = structural long-term anchor (the top-FAVORS
  strategy's **prescribed allocation** − current). Prescribed allocation of a
  strategy = its base-scenario `target_allocation` (structural); bull/bear
  scenario targets are tactical variants. The same prescribed allocation is
  what synthetic backtests replay.
- `delta = 0.4 × scenario_delta + 0.6 × favors_delta`, rounded to 2.5-point
  increments, then re-normalized to sum 100.
- This blend is the basis of the Worker's **reallocation proposals**
  (`WorkerResult.reallocation_proposed`, see DATA_MODELS.md); Writeback
  validates the result against the mechanical reallocation gates
  (USE_CASES.md UC8-B).
- Worker documents the blend in `reasoning` and cites supporting invariants.

---

## learn_from_adaptations — Learning Loop (V2)

V2-only mechanical job that closes the feedback loop. V1 records data but does
not run this job.

```python
async def learn_from_adaptations(db):
    """
    V2 only. Runs Monday 08:55 — processes adaptations reaching 3-month maturity.

    For each Adaptation where:
      (user_validated=true OR auto_validated=true)
      AND performance_3m IS NOT NULL
      AND learning_applied=false

    1. Fetch Strategy via MODIFIES → Portfolio → HOLDS(primary=true) → Strategy
    2. Fetch all BACKED_BY invariants
    3. For each invariant:
         severity = min(1.5, 1 + abs(performance_3m) / 0.10)
         if performance_3m > 0: confirmation_count += 1
         else:                  infirmation_count += 1
         log to invariant_confrontations
    4. Trigger update_invariant_weights() for affected invariants
    5. Set Adaptation.learning_applied = true
    """
```

V1 alternative for retrospective evaluation: `portfolio_weekly_snapshot` rows
themselves are the audit trail. A simpler V1.5 job can ask, retrospectively,
"did the top challenger 12 weeks ago outperform the defender since then?" and
update invariants based on that — without needing real executions.

---

## Decision Granularity

```
Once at install (UC0)
  Manual CLI  →  full DB bootstrap + first snapshot

Daily (mechanical only)
  02:00  inbox → CorpusIngester
  06:30  MarketData TS + level/speed/acceleration
  06:35  rolling indicators → PortfolioNAV TS
  06:50  regime detection → Regime vertex
  (scenario probabilities moved to the weekly chain — Monday 08:35)

Weekly (Monday — canonical timeline, identical in CLAUDE.md / USE_CASES.md)
  08:00  UC2 market valuation → MarketEvent
  08:10  UC3 knowledge search → inbox
  08:20  UC4 knowledge curation → KnowledgeEvent
  08:30  Backtests → FAVORS edges (RegimeType → Strategy)
  08:40  Invariant weights
  08:45  UC6 portfolio valuations → Portfolio vertices
  08:50  UC7 ranking → portfolio_weekly_snapshot
  08:55  V2 only: learn_from_adaptations
  09:00  UC8: Planner Pre → Worker → Planner Post → Writeback
  09:30  Weekly digest → Telegram
```

---

## FX

```
User is in CHF. Assets in USD.
Portfolio.fx_usd_exposure tracked daily (informational only).
Indicators calculated in USD. CHFUSD=X applied for display only.
No hedging in Phase 1. See IMPROVEMENTS.md I-15.
```

---

## System Evolution — Supervised Self-Extensible

```
Worker discovers new pattern (type=new_invariant)
  → ImprovementProposal in WorkerResult.innovations_proposed
  → EventLog append → Invariant source:agent-discovery status:proposed
  → Telegram notification in the same cycle
        ↓
  User validates → status:integrated
  User rejects   → status:rejected (reason persisted as trace)

Schema self-extension (new vertex/edge/property types): DEFERRED TO V2
(IMPROVEMENTS I-27) — a schema element without code to use it is dead
weight. V1 innovations are new_invariant / new_strategy /
strategy_revision / process / data.
```

**New Strategy (type=new_strategy)** — the Worker (or the curation runner)
may propose an entire agent-discovered strategy. The proposal `spec` must be
complete:

```
spec = {
  id             : <regimeType.alias>-<name>-<vN> for regime-specific
                   strategies (ex: 'stagflation-custom-v2'); never a
                   Framework id
  title, description, regime_type_id, framework_id
  conditions     : ≥1 orthogonal computable indicator (manual check at
                   validation in V1 — IMPROVEMENTS I-12)
  backed_by      : invariant ids (existing, integrated)
  scenarios      : the 3 bull/base/bear definitions — triggers,
                   target_allocation (sums to 100, complies with the binding
                   user caps), initial probabilities (sum to 100)
}

Flow:
  → EventLog append (InnovationEvent) → Strategy vertex
    source:agent-discovery, status:proposed, enabled:false
  → Telegram notification in the same cycle
        ↓
  User validates → in ONE Writeback transaction:
    status:active, enabled:true
    3 Scenario vertices + HAS_SCENARIO edges
    BACKED_BY edges to the cited invariants
  → the next weekly cycle picks it up mechanically: Backtests over
    historical Regime instances (coverage permitting) → FAVORS edges →
    eligible as structural anchor for reallocation deltas
  User rejects → status:closed, enabled stays false (reason as trace)

A new Strategy affects the ranking only when a Portfolio HOLDS it —
creating or modifying Portfolios remains a user action (UC9) in V1.
```

**Strategy revision (type=strategy_revision)** — the "better strategy" path:
same spec fields as new_strategy plus `supersedes: <strategy_id>`. On user
validation, in ONE transaction: new vertex id `-v(N+1)` created active (with
its 3 Scenarios and BACKED_BY edges), the superseded vertex gets
`status='closed'`, `enabled=false`, `date_revised=today`, and the new
vertex's `trace` records the lineage ("supersedes <id>: <what changed and
why>"). HOLDS edges are NOT migrated automatically — repointing a Portfolio
to the new version is a user action (UC9). Backtests/FAVORS for the new
version are computed at the next weekly cycle; the revision then enters
probation like any new strategy (see "Unified improvement cycle").

---

## Detailed Planner Steps

### CALL 1a — Query Strategist (LLM → JSON parameters)
```
Input  : raw trigger + conversation_history
LLM    : Qwen3-8B via OpenRouter, thinking=512 tokens

tool_use output — QueryStrategies:
  semantic_query     → text for embedding search
  portfolio_filter   → filter for enabled portfolios
  invariant_topics   → topics to filter Invariants
  regime_focus       → regime for comparative Backtests
  proposal_limit     → number of recent proposals to load
```

### PYTHON — DB Execution (no LLM)
```
1. embedding = await embedding_service.encode(semantic_query)
2. asyncio.gather (6 DB queries):
   ① Passages vector search
   ② Current Regime + global liquidity
   ③ Ranked enabled portfolios from portfolio_weekly_snapshot
   ④ Scenarios with d7 shift
   ⑤ Top invariants by weight_effective
   ⑥ Last 3 Proposals (status=any) for context
```

### CALL 1b — Context Builder (LLM → PlannerContext)
```
LLM filters, orders, selects, builds.
Output PlannerContext via assemble_context tool.
```

### PYTHON — Bridged closures + ToolContextWrapper
```
_db captured by closure — Worker never sees it.
ToolContextWrapper wraps each function via DI.
PlannerPre returns PlannerContext + tool_registry.
```

### WORKER
```
Receives PlannerContext + injected tool_registry.
Reasons with Markdown Skills.
Calls db_query / market_fetch / portfolio_check as needed.
Produces WorkerResult (always complete, fields possibly empty).
```

```python
class ScenarioAdjustment(BaseModel):
    strategy_id : str
    scenario    : str            # 'bull' | 'base' | 'bear'
    probability : float          # new value; the 3 must sum to 100
    rationale   : str            # qualitative-trigger interpretation

class EvaluationDraft(BaseModel):
    strategy_id      : str
    verdict          : str       # 'confirms' | 'weakens' | 'invalidates' | 'neutral'
    conviction_delta : float
    events           : list[str]
    reasoning        : str

class WorkerResult(BaseModel):
    regime_assessment     : str
    ranking_commentary    : str                       # explains, never re-ranks
    scenario_adjustments  : list[ScenarioAdjustment]  # qualitative triggers only
    evaluations           : list[EvaluationDraft]
    reallocation_proposed : Optional[ReallocationProposal]  # see DATA_MODELS.md
    innovations_proposed  : list[ImprovementProposal]       # empty list if none
    reasoning             : str   # also serves as the Proposal vertex's
                                  #   reasoning (switch commentary folded here)
```

### CALL 2 — Knowledge Extractor (async post-Worker)
```
asyncio.create_task() after WorkerResult.
Extracts: regime updates, evaluations, scenario updates,
          invariant confrontations, innovations.
Outputs PostPlannerResult via extract_knowledge tool.
Writeback commits — EventLog append first, then vertices, then edges —
and runs the MECHANICAL proposal gates (switch from snapshot ranks;
reallocation validation from WorkerResult.reallocation_proposed).
```

---

## Weekly Cycle Timeline (Monday — sequential chain, times indicative)

Steps run as ONE chain: each starts only after the previous succeeds. On
failure: ErrorEvent → EventLog + Telegram alert, chain aborts (no ranking on
stale data). Timezone Europe/Zurich.

```
06:30   Daily mechanical jobs complete (regime, ratios)

08:00   Weekly pre-processing (UC2 → UC3 → UC4, then mechanical)
          → Market valuation (MarketEvent), knowledge search + curation
          → Backtests recalculated → FAVORS edges
          → Scenario numeric triggers + shift_d7 → ScenarioProbability TS
          → Invariant weights updated (incl. mechanical confrontations)
          → Portfolio valuations + ranking → portfolio_weekly_snapshot rows
          → Proposal outcomes + scenario calibration + strategy probation
            (mechanical/outcomes.py — see "Unified improvement cycle")
          → V2 only: learn_from_adaptations

09:00   Worker cycle
  09:00:01  Call 1a    LLM ~1s
  09:00:02  Python DB  ~20ms (6 parallel queries)
  09:00:03  Call 1b    LLM ~2s  → PlannerContext
  09:00:05  Worker     LLM ~5s  → WorkerResult
  09:00:10  asyncio.create_task() → PlannerPost + Writeback
              Call 2 + commits (EventLog first)
              Mechanical gates: switch (from snapshot ranks) and
              reallocation (from WorkerResult.reallocation_proposed)
              Proposal vertex (V1) if a gate passes
              snapshot `recommendation` columns updated
              (rows themselves written by UC7 at 08:50)

09:30   Weekly Telegram digest
          → regime + ranking + defender row + challenger gap
          → cumulative returns (3m/6m/1y/3y/5y) displayed alongside indicators
          → V1 paper-mode Proposal payload if any (switch: old vs new
            portfolio; reallocation: old vs new allocation + blend reasoning)
          → scoreboard: proposal hit-rate, paper-tests in progress,
            strategies in probation, scenario calibration flags
          → proposed innovations (user validation required)
```

---

## LLM Abstraction — Model-Agnostic

Business code calls `BaseLLMClient.complete()`. Model and provider in `.env`.

```python
class LLMResponse(BaseModel):
    content: Optional[str]
    tool_calls: list[LLMToolCall]
    stop_reason: str         # "end_turn" | "tool_use"
    thinking: Optional[str]

class BaseLLMClient(ABC):
    @abstractmethod
    async def complete(
        self, messages, system=None, tools=None,
        tool_choice="auto", thinking_budget=None
    ) -> LLMResponse: ...
```

Two implementations: `OpenAICompatibleClient` (OpenRouter) and `AnthropicClient`.
Swap model: change `.env` only.

---

## What User Does

```
Seeds      → runs UC0 once at install
Feeds      → uploads PDFs, URLs (local drop or Telegram)
Challenges → questions theses via UC9 (Telegram chat)
Arbitrates → validates/rejects Proposals and innovations
Defines    → drawdown rule, single-asset concentration, strategy enabled flag
```
