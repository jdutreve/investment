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
arcadedb-embedded (Apache 2.0)
  → in-process, ARM64 supported
  → Graph + Vector + FTS + SQL + Time-Series in one engine
  → Single Python process = sole writer = no contention

Filesystem
  → Binary sources only (PDF, Kindle CSV) at /data/investment/sources/
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
| Scope | full ArcadeDB read | 3 bridged functions only |

```python
# Bridged functions (Worker only):
db_query(cypher_or_sql, lang)   # READ only, max 20 rows
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
   defender, propose paper-mode adjustments. V1 never auto-executes.
   Use the Skills provided and the data in your context.
   You are unaware of the Planner, Writeback, and internal storage.
   Three tools: db_query, market_fetch, portfolio_check.
   Sharpe/Sortino/Calmar are pre-calculated indicators in USD in ArcadeDB;
   the suffix is _rolling. Interpret them — do not recalculate.
   Rolling window is 36 months. Risk-free rate is 3M T-Bill (^IRX).
   WorkerResult must include innovations_proposed (empty list if none)."
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
PROPOSES  V1 emits Proposal vertices in paper-mode when gate is met
V2 ADAPTS Telegram with auto-validation timeout (V2 only)
MEASURES  PortfolioNAV + weekly snapshots with rolling indicators (USD)
V2 LEARNS Adaptation × performance_3m → BACKED_BY invariant weights
```

---

## Entities — Overview

```
GRAPH VERTICES (13)
  Framework       lens for market interpretation; seeded '4seasons'
  RegimeType      static regime definition per framework
  Regime          detected macro regime instance; id <alias>-<start_date>
  Invariant       universal principle with dynamic weight, author-tier floor,
                  tags, real-source provenance
  Strategy        thesis tied to a regime_type_id (alias-first id); description
  Scenario        bull/base/bear per strategy (weekly shift detection)
  Evaluation      MarketData × Strategy crossing → weekly verdict
  Backtest        Strategy × Regime instance historical performance
  Adaptation      V2-only allocation decision (reserved vertex)
  Proposal        V1 paper-mode recommendation persisted weekly
  Portfolio       concrete ETF allocation; ranking unit; defender=true
  Document        corpus source
  Passage         RAG unit (chunk + embedding)

GRAPH EDGES (11)
  Evaluation → UPDATES       → Strategy
  RegimeType → FAVORS        → Strategy (strategy-level rolling indicators,
                                aggregated across n_periods historical instances)
  Strategy   → HAS_SCENARIO  → Scenario (3 per Strategy)
  Strategy   → BACKED_BY     → Invariant
  Strategy   → TESTED_IN     → Backtest
  Backtest   → IN_REGIME     → Regime instance
  Adaptation → MODIFIES      → Portfolio (V2 only)
  Portfolio  → HOLDS         → Strategy (primary BOOLEAN, weight, since)
  Portfolio  → DESIGNED_FOR  → RegimeType (nullable)
  Document   → CONTAINS      → Passage
  Passage    → SUPPORTS      → Invariant

TIME-SERIES (4)
  MarketData          level/speed/acceleration per (ticker, asset_class).
                      Global liquidity is asset_class=GLOBAL_LIQUIDITY.
                      `close`, `volume`, `regime_id` removed — `level` is the
                      canonical value; regime membership reached via date lookup.
  ScenarioProbability bull/base/bear probabilities per Strategy
  PortfolioNAV        rolling indicators per day (USD)
  Event               all UC outputs — APPEND BEFORE vertex/edge commit
```

Benchmark vertex and Hypothesis SQL table are deferred — see IMPROVEMENTS.md.

---

## Regime Detection (4 Seasons only in V1)

Thresholds loaded from `system_thresholds` SQL — not hardcoded. Detection
uses `level`, `speed`, and `acceleration` from MarketData TS, not only static
thresholds: a value crossing a threshold while accelerating is a stronger
signal than the same level reached while decelerating.

```
Regime                              Indicators (illustrative)
rising-growth-falling-inflation     PMI rising, CPI YoY decelerating
rising-growth-rising-inflation      PMI rising, CPI YoY accelerating
falling-growth-rising-inflation     PMI falling, CPI YoY accelerating
  (alias: stagflation)
falling-growth-falling-inflation    PMI falling, CPI YoY decelerating
uncertain                            contradictory signals or thresholds straddled
```

Tags layered on top: `deflation` when CPI YoY < 0; `liquidity-tightening` /
`liquidity-easing` from the GLOBAL_LIQUIDITY composite; `market-stress` when
VIX > stress threshold.

Strategy conditions must include ≥1 indicator orthogonal to regime thresholds.

---

## Strategy Library + Comparison

```
Seeded strategies (all enabled=true):
  4seasons         Dalio risk parity
  permanent        Browne 25/25/25/25
  barbell          Taleb safety + convexity
  momentum-macro   dynamic rotation by regime

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

  Scenario bull (35%)  triggers: CPI<2.5% AND Fed dovish AND PMI>52
  Scenario base (45%)  triggers: CPI 2.5-3.5% AND Fed pause
  Scenario bear (20%)  triggers: VIX>25 OR (CPI>4% AND PMI<48)

Probability mechanics in V1: the daily 06:45 mechanical job evaluates
**numeric triggers only** (e.g. "CPI<2.5", "VIX>25") against MarketData TS
and computes shift_d7; qualitative triggers ("Fed dovish") are interpreted
exclusively by the weekly Worker cycle, which reviews and may adjust
probabilities. Formal trigger grammar deferred — see IMPROVEMENTS I-22.
V1 uses shifts as context for proposals.
V2 may use shift thresholds for auto-adaptive execution.
```

**Proposal/Adaptation delta blending (V1 paper-mode, V2 real):**
- Scenario allocation = tactical short-term override.
- FAVORS-derived allocation = structural long-term anchor.
- `delta = 0.4 × scenario_delta + 0.6 × favors_delta`
- Worker documents the blend in `reasoning`.

---

## learn_from_adaptations — Learning Loop (V2)

V2-only mechanical job that closes the feedback loop. V1 records data but does
not run this job.

```python
async def learn_from_adaptations(db):
    """
    V2 only. Runs Monday 08:30 — processes adaptations reaching 3-month maturity.

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
  06:45  Scenario probabilities → ScenarioProbability TS
  06:50  regime detection → Regime vertex

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
Worker discovers new pattern
  → ImprovementProposal in WorkerResult.innovations_proposed
  → Event TS append → Invariant source:agent-discovery status:proposed
  → Telegram notification in the same cycle
        ↓
  User validates → status:integrated
  User rejects   → status:rejected (reason persisted as trace)

Schema extensions (new vertex/edge type):
  → require explicit user validation before CREATE
  → stored in schema_extensions SQL table
```

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
2. asyncio.gather (6 parallel ArcadeDB queries):
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
Produces WorkerResult with innovations_proposed (always present, possibly empty).
```

### CALL 2 — Knowledge Extractor (async post-Worker)
```
asyncio.create_task() after WorkerResult.
Extracts: regime updates, evaluations, scenario updates,
          proposals (V1) / adaptations (V2), invariant confrontations,
          innovations.
Outputs PostPlannerResult via extract_knowledge tool.
Writeback commits — Event TS append first, then vertices, then edges.
```

---

## Weekly Cycle Timeline (Monday)

```
06:30   Daily mechanical jobs complete (regime, ratios, scenarios)

08:00   Weekly pre-processing (UC2 → UC3 → UC4, then mechanical)
          → Market valuation (MarketEvent), knowledge search + curation
          → Backtests recalculated → FAVORS edges
          → Invariant weights updated
          → Portfolio valuations + ranking → portfolio_weekly_snapshot rows
          → V2 only: learn_from_adaptations

09:00   Worker cycle
  09:00:01  Call 1a    LLM ~1s
  09:00:02  Python DB  ~20ms (6 parallel queries)
  09:00:03  Call 1b    LLM ~2s  → PlannerContext
  09:00:05  Worker     LLM ~5s  → WorkerResult
  09:00:10  asyncio.create_task() → PlannerPost + Writeback
              Call 2 + commits (Event TS first)
              Proposal vertex (V1) if gate met
              snapshot `recommendation` columns updated
              (rows themselves written by UC7 at 08:50)

09:30   Weekly Telegram digest
          → regime + ranking + defender row + challenger gap
          → cumulative returns (3m/6m/1y/3y/5y) displayed alongside indicators
          → V1 paper-mode Proposal payload if any
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
Feeds      → uploads PDFs, URLs (SCP or Telegram)
Challenges → questions theses via UC9 (Telegram chat)
Arbitrates → validates/rejects Proposals and innovations
Defines    → drawdown rule, single-asset concentration, strategy enabled flag
```
