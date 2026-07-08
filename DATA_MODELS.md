# DATA_MODELS.md — Investment Agent MVP

See REVISION_NOTES.md for V1 scope, core concepts, and ranking rule.

## Persistence principle

SQLite, single file (`~/data/investment/investment.db`), stdlib `sqlite3` —
see DECISIONS.md ADR-004. `journal_mode=WAL`, `synchronous=NORMAL`,
`foreign_keys=ON`, ONE connection. The agent is the sole writer — all calls
serialized via one asyncio executor path, writes **always inside explicit
transactions**. Binary sources (PDF, Kindle CSV) on filesystem, referenced
via `Document.source_path`.

**Conceptual → physical mapping (ADR-004):** the model below remains a
conceptual graph — entities ("vertices") and typed relations ("edges") —
because that is the right vocabulary for the domain. Physically, every entity is a SQLite **table**; a 1:N relation is a FK
column on the child, a M:N relation an association table
(`from_id, to_id, <properties>`) — every relation in this system is a
1-hop FK either way. Types: STRING→TEXT, FLOAT→REAL, MAP→TEXT (JSON1),
DATE/DATETIME→TEXT ISO-8601, FLOAT[dim]→BLOB (float32, 384 dims — see
TASKS Phase 1bis). Table names are
snake_case (`RegimeType` → `regime_type`).

**Mandatory rule:** any vertex with empty `trace` is rejected with `ValueError`.
Exemptions (`TRACE_EXEMPT = {"Passage", "RegimeType", "EventLog"}`):
Passage inherits provenance from its parent Document; RegimeType is static
seed data whose narrative lives in `description`; EventLog's payload IS the
trace.

**EventLog ordering rule:** every EventLog append must precede the
corresponding entity/relation commit. Pure TS writes (MarketData,
PortfolioNAV catch-up, ScenarioProbability weekly appends) are exempt — they commit no
vertex/edge.

**Units convention:**
- Performance indicator fields (`max_drawdown`, `drawdown`, `volatility`,
  `daily_return`, `return_*`, `total_return`, `vs_benchmark`, and their
  `*_delta` gap counterparts) are **decimal fractions**: `-0.062` = -6.2%.
- Fields suffixed `_pct` or `_rule`, plus `fx_usd_exposure` and `confidence`,
  are **percent points** on a 0–100 scale (`max_drawdown_rule: -15.0` = -15%).
- `allocation` MAPs are percent weights summing to 100.
- Percent formatting happens only at the Telegram display layer.

See IMPROVEMENTS.md for deferred schema elements (Benchmark, Hypothesis,
multi-tier recency, per-invariant floor override).

---

## Graph Schema — VERTEX types (13 in V1 — V2 adds Adaptation)

### Framework
*Lens used to interpret markets and design/refine strategies and portfolios.*

```
Framework {
  id          : STRING   PRIMARY KEY  -- '4seasons' | 'permanent' | 'liquidity-cycle'
  name        : STRING
  description : STRING
  enabled     : BOOLEAN
  accuracy    : FLOAT    -- updated as predictions confirmed (V2 driver)
  trace       : STRING   -- MANDATORY
  created_at  : DATETIME
}
```

Only `4seasons` enabled in V1. Other frameworks may be seeded as metadata
(`enabled=false`) for future use (see IMPROVEMENTS I-1).

---

### RegimeType
*Static definition of a possible regime within a framework. Seeded once; never mutated
by the agent. The FAVORS and DESIGNED_FOR edges live here, not on Regime instances,
because they represent multi-period aggregated knowledge about the type.
No `trace` (TRACE_EXEMPT) — the narrative lives in `description`.*

```
RegimeType {
  id           : STRING  PRIMARY KEY  -- 'falling-growth-rising-inflation' |
                                      --  'rising-growth-falling-inflation'  |
                                      --  'rising-growth-rising-inflation'   |
                                      --  'falling-growth-falling-inflation' |
                                      --  'uncertain'
  name         : STRING               -- human-readable label
  aliases      : STRING[]             -- ex: ['stagflation']
  framework_id : STRING               -- '4seasons' in V1
  description  : STRING               -- carries the regime narrative
  created_at   : DATETIME
}
```

5 RegimeTypes seeded for the `4seasons` framework. Deflation is never a RegimeType,
only a dynamic tag on Regime instances (when CPI YoY < 0).

---

### Regime
*A concrete occurrence of a RegimeType, bounded in time. Created/updated by the
regime detector's step() — one step per new monthly print, ONE code path,
four callers: UC0 35y materialization and the Phase 9 replay iterate it
over the archive; the Monday catch-up and the on-demand UC9 prelude call
it on the new prints since the last run. IN_REGIME edges point here.*

```
Regime {
  id              : STRING  PRIMARY KEY   -- ex: 'stagflation-2026-05-01'
                                          --   convention: <regimeType.alias>-<start_date>
  regime_type_id  : STRING                -- FK → RegimeType.id
  tags            : STRING[]              -- dynamic instance tags:
                                          --   'deflation' when CPI YoY < 0
                                          --   'liquidity-tightening' etc.
  start_date      : DATE
  end_date        : DATE                  -- null if currently active
  confidence      : FLOAT                 -- 0-100 (formula in ARCHITECTURE)
  is_current      : BOOLEAN               -- exactly one true at a time per framework
  events          : STRING[]              -- summarised numeric observations that
                                          --   triggered the regime (CPI level/speed/accel,
                                          --   growth composite ditto, liquidity ditto)
  trace           : STRING  -- MANDATORY
  created_at      : DATETIME
  updated_at      : DATETIME              -- a.k.a. as_of
}
```

Deflation is never a RegimeType, only a tag on Regime instances.
The static definition (name, aliases, framework_id) lives on RegimeType.

---

### Invariant
*Universal principle. The PRINCIPLE is invariant. The CONFIDENCE evolves.*

```
Invariant {
  id            : STRING  PRIMARY KEY
  title         : STRING
  description   : STRING            -- full statement (replaces former content+description)
  example       : STRING
  source        : STRING            -- free-text: real provenance (document+page, backtest,
                                    --   observation date), NOT an enum of provenance types
  author        : STRING            -- authority tier driving floor: 'dalio' | 'marks' |
                                    --   'system' (agent-discovery) | null
  status        : STRING            -- 'proposed' (maturing) | 'integrated'
                                    --   (time-validated: N_min/θ, not refuted)
                                    --   | 'rejected' (refuted). Mechanical —
                                    --   no 'validated' human step (ADR-006).
  tags          : STRING[]          -- THEMATIC / retrieval only (NOT the
                                    --   confrontation driver — that is `condition`).
                                    --   Namespaced where applicable: 'asset:GLD',
                                    --   'asset-class:fixed-income',
                                    --   'indicator:max_drawdown', 'phase:accumulation',
                                    --   'regime:<regime_type_id>';
                                    --   free thematic tags otherwise: 'economy',
                                    --   'rates', 'forex', 'credit', 'liquidity',
                                    --   'geopolitics', … (retrieval/curation)
  embedding     : FLOAT[384]        -- embedded text = title + "\n" + description
                                    --   (sentence-transformers, in-process,
                                    --    model pinned in .env); RE-ENCODED
                                    --   whenever title/description change

  condition     : JSON             -- WHEN the invariant is ACTIVE. Predicate[]
                                   --   ANDed over REGISTRY signals; empty ⇒ 'always'.
                                   --   Predicate = {signal, feature, op, value};
                                   --   signal ∈ collected MarketData series +
                                   --   DERIVED composites (real_rate = irx−inflation,
                                   --   GROWTH_COMPOSITE, GLOBAL_LIQUIDITY) + 'regime';
                                   --   feature ∈ level|speed|acceleration|type.
                                   --   Curator expresses the FUNDAMENTAL driver, not
                                   --   a surface correlate (e.g. real_rate<0, not
                                   --   regime=stagflation, for gold).
                                   --   Machine-readable so "is it active now?" is
                                   --   answerable and mature_invariant() can find
                                   --   its historical moments. Empty condition +
                                   --   no effect ⇒ reference knowledge.
  effect        : JSON             -- WHAT must hold when active — the VALUATION
                                   --   METHOD (drives veracity):
                                   --   {handle, metric, method, direction}
                                   --   handle ∈ asset:<t>|asset-class:<c>|strategy:<id>
                                   --     (INDEPENDENT of BACKED_BY);
                                   --   method ∈ cross_class|cross_strategy|absolute
                                   --     (cross_* read the MEDIAN of the OTHER
                                   --      benchmark_valuation rows; absolute vs 0);
                                   --   direction ∈ outperform|underperform.
                                   --   See ARCHITECTURE "Birth maturation".

  weight_initial   : FLOAT
  floor_weight     : FLOAT
  weight_effective : FLOAT  -- = max(weight_initial × market_score × recency_factor,
                            --        floor_weight)

  confirmation_count : INT
  infirmation_count  : INT
  market_score       : FLOAT
  recency_factor     : FLOAT  -- 0.5 + 0.5 × exp(-days_since/half_life);
                              --   CONDITION-RELATIVE, NOT wall-clock — a dormant
                              --   invariant whose condition is absent does NOT
                              --   decay. Concretely:
                              --     days_since = 0            if condition active now
                              --                = today − last_active_day  otherwise
                              --   where last_active_day = the most recent day the
                              --   condition held (end of its last episode / last
                              --   occurrence). For an 'always' condition this is
                              --   days-since-last-confrontation.

  trace           : STRING  -- MANDATORY
  created_at      : DATETIME
  validated_at    : DATETIME  -- TIME-validation timestamp (mechanical: when it
                              --   first met N_min/θ, not refuted) — not a user
                              --   click; null while still a candidate (ADR-006)
  updated_at      : DATETIME  -- last confrontation (drives recency_factor)
}
```

An Invariant with an **empty `condition` / no `effect`** (equivalently no
BACKED_BY edge) is **reference knowledge**: never confronted (market_score
stays 1.0), weight = authority × recency; it informs Worker reasoning without
backing a strategy — intended, not an accident. A weighted (maturable)
invariant MUST carry a machine-readable `condition` + `effect` over known
signals; an observation not reducible to that is a ponctual fact, not an
invariant. **A ponctual fact is NOT a new entity**: a quantitative one is a
TS-derived confrontation moment (already an `invariant_confrontations` row —
that row IS its link to the invariant it confirms/refutes); a narrative one is
an event Document/Passage that may `SUPPORTS` the invariant it illustrates.
Invariants extracted from UC3 events or user notes carry `author=null` → floor
0.20 ('other corpus' tier).

Half-life uniform in V1: 365 days (see IMPROVEMENTS I-5).
Floor by **author** tier, persisted at creation:
`author='dalio'`=0.40, `author='marks'`=0.35, `author=null` (other corpus)=0.20,
`author='system'` (agent-discovery)=0.05.

---

### Strategy
*Investment thesis or concept.*

```
Strategy {
  id             : STRING  PRIMARY KEY  -- seeded: 'four-seasons-rp' |
                                        --   'permanent-browne' | 'barbell-taleb' |
                                        --   'momentum-macro'.
                                        -- Regime-specific strategies follow
                                        --   <regimeType.alias>-<name>-<vN>
                                        --   (ex: 'stagflation-custom-v2').
                                        -- Strategy ids NEVER collide with
                                        --   Framework ids.
  title          : STRING
  description    : STRING               -- one-paragraph rationale of the thesis
  regime_type_id : STRING               -- FK → RegimeType.id; null for framework-neutral
  framework_id   : STRING               -- lens under which the strategy is evaluated
                                        --   in V1 ('4seasons'), not its origin
  conviction     : FLOAT                -- 0-100; updated after each Evaluation
  enabled        : BOOLEAN              -- false = excluded from ranking and Worker context
  conditions     : STRING               -- must include ≥1 dimension orthogonal to regime;
                                        --   every referenced indicator must be computable
                                        --   from MarketData TS or Regime fields
  source         : STRING               -- 'corpus' | 'agent-discovery' (free text accepted)
  status         : STRING               -- 'proposed' | 'active' (auto-enabled
                                        --   after mechanical probation) | 'closed'.
                                        --   No 'validated' human step (ADR-006).
  date_opened    : DATE
  date_revised   : DATE
  trace          : STRING  -- MANDATORY
  created_at     : DATETIME
  updated_at     : DATETIME
}
```

Benchmarking is a Portfolio concern, not a Strategy one.

Agent-discovered strategies enter via
`ImprovementProposal(type=new_strategy)` as `status='proposed'`,
`enabled=false`; they enter a mechanical probation window
(`strategy_probation_weeks`, 12) and **auto-enable** if it passes — no user
gate (ADR-006) — creating their 3 Scenario vertices and BACKED_BY edges in
one transaction. Full lifecycle in ARCHITECTURE.md "System Evolution".
Revisions (`type=strategy_revision`, spec + `supersedes`) close the old
version (`status='closed'`, `enabled=false`, `date_revised` set) and open
`-v(N+1)`; the owner may still repoint HOLDS edges (UC9) as a preference
override. Every activated strategy (new or revision) runs its probation
measured mechanically — see ARCHITECTURE "Unified improvement cycle".

---

### Scenario
*Probabilities of bull/base/bear must always sum to 100 per Strategy.
HAS_SCENARIO is a 1:N relation → physically `scenario.strategy_id`
(mapping rule above).*

```
Scenario {
  id                : STRING  PRIMARY KEY
  name              : STRING  -- 'bull' | 'base' | 'bear'
  probability       : FLOAT   -- 0-100 (history in scenario_probability;
                              --   week-over-week shift computed on read)
  triggers          : STRING[]
  target_allocation : MAP     -- target allocation if this scenario realizes
  currency          : STRING  -- 'USD'
  trace             : STRING  -- MANDATORY
  updated_at        : DATETIME
}
```

---

### Evaluation

```
Evaluation {
  id               : STRING  PRIMARY KEY
  date             : DATE
  verdict          : STRING  -- 'confirms' | 'weakens' | 'invalidates' | 'neutral'
  conviction_delta : FLOAT
  events           : STRING[] -- triggering observations, same convention as
                              --   Regime.events (ex: "CPI level 3.1 (speed +0.30)")
                              --   — an array, not edges: MarketData is a TS,
                              --   not a vertex, so it cannot be an edge source
  reasoning        : STRING
  trace            : STRING  -- MANDATORY
  created_at       : DATETIME
}
```

---

### Backtest

```
Backtest {
  id              : STRING  PRIMARY KEY
  period          : STRING  -- ex: "2021-2022"
  date_start      : DATE
  date_end        : DATE
  sharpe_rolling  : FLOAT   -- USD, computed over the backtest period
                            --   (may be shorter than 36M — field name kept
                            --    uniform for query symmetry), rf = ^IRX
  sortino_rolling : FLOAT
  calmar_rolling  : FLOAT   -- annualized return / |max_drawdown| over the period
  max_drawdown    : FLOAT
  total_return    : FLOAT
  currency        : STRING  -- 'USD'
  trace           : STRING  -- MANDATORY
  created_at      : DATETIME
}
```

Every Backtest row is **mechanical by construction** — the Worker proposes
strategies and invariants but never computes numbers (non-negotiable rule);
a proposed strategy gets its backtests from the mechanical pipeline at the
next cycle, so results stay reproducible by the replay. Hence no
`source`/`status` columns.

---

### Proposal
*V1 paper-mode recommendation. Persisted per weekly cycle when a gate is met.
Two kinds (`proposal_type`): **switch** (replace defender with a challenger
portfolio) and **reallocation** (adjust the defender's own allocation,
Worker-proposed, Writeback-validated).*

```
Proposal {
  id                  : STRING  PRIMARY KEY
  date                : DATE
  proposal_type       : STRING  -- 'switch' | 'reallocation'
  defender_id         : STRING  -- portfolio_id of current defender
  challenger_id       : STRING  -- portfolio_id of challenger (switch only; null
                                --   for reallocation)
  proposed_allocation : MAP     -- reallocation only: full target allocation for
                                --   the defender (percent weights, sum 100);
                                --   null for switch
  recommendation      : STRING  -- 'monitor' | 'paper-test'
                                --   ('maintain' never creates a Proposal vertex —
                                --    it exists only on snapshot rows)
  defender_rank       : INT
  challenger_rank     : INT     -- null for reallocation
  gap                 : MAP     -- {sharpe, sortino, calmar, max_drawdown,
                                --   allocation_diff}; for reallocation,
                                --   allocation_diff = proposed − current
  market_context      : MAP     -- {framework, regime, confidence, global_liquidity,
                                --   derivatives}
  reasoning           : STRING  -- Worker reasoning; for reallocation, must cite
                                --   the delta blend inputs (scenario target +
                                --   FAVORS anchor) and supporting invariants
  user_response       : STRING  -- 'pending' | 'accepted' | 'rejected' | 'expired'
                                --   auto-set to 'expired' after
                                --   proposal_expiry_days (system_thresholds, 14)
  rejection_reason    : STRING  -- optional free text captured by the bot on
                                --   [REJECT]; fed back into
                                --   PlannerContext.recent_proposals
  paper_started       : DATE    -- set when user accepts a paper-test
  outcome             : MAP     -- {proposed_return, incumbent_return,
                                --   verdict: 'won'|'lost'|'pending'} — written
                                --   by evaluate_proposals() at
                                --   proposal_outcome_weeks (12); drives
                                --   confrontations source='proposal'
                                --   (ARCHITECTURE "Unified improvement cycle")
  evaluated_at        : DATE    -- when outcome verdict was computed
  trace               : STRING  -- MANDATORY
  created_at          : DATETIME
}
```

Anti-repetition: Writeback refuses a new switch Proposal naming the same
challenger within `proposal_cooldown_weeks` (4) of a rejection, unless the
regime type changed in between.

No edge to Portfolio in V1 — `defender_id` and `challenger_id` as scalars. An
edge can be added in V2 when traversal becomes useful.

---

### Adaptation — V2 only (NOT created at UC0; documented here for V2)

Created together with the MODIFIES edge and the `adaptation_quality`
document type when V2 lands (`IF NOT EXISTS` makes the addition trivial).
V1 proposals live in `Proposal`.

```
Adaptation {
  id               : STRING  PRIMARY KEY
  date             : DATE
  delta            : MAP
  regime_id        : STRING
  drawdown_at_decision : FLOAT
  fx_usd_exposure  : FLOAT
  user_validated   : BOOLEAN
  auto_validated   : BOOLEAN -- V2 only
  learning_applied : BOOLEAN
  performance_1m   : FLOAT
  performance_3m   : FLOAT
  sharpe_delta     : FLOAT
  sortino_delta    : FLOAT
  reasoning        : STRING
  trace            : STRING  -- MANDATORY
  created_at       : DATETIME
}
```

---

### Portfolio
*Concrete ETF allocation. Ranking unit. `defender=true` marks the current defender.*

```
Portfolio {
  id                   : STRING  PRIMARY KEY
  name                 : STRING  -- descriptive; never just "defender"
  framework_id         : STRING  -- references Framework vertex; ex: '4seasons'
  defender             : BOOLEAN -- true = current defender. Exactly one in V1.
  enabled              : BOOLEAN -- false = excluded from ranking
  currency             : STRING  -- user display currency (CHF)
  benchmark            : STRING  -- benchmark ticker for this portfolio (Portfolio-level,
                                 --   not Strategy-level)
  allocation           : MAP     -- concrete ETF allocation, mandatory
  max_drawdown_rule    : FLOAT   -- may only be STRICTER than user_profile
                                 --   .max_drawdown_pct (binding rule)
  max_single_asset_pct : FLOAT   -- may only be STRICTER than user_profile
                                 --   .max_single_asset_pct (binding rule)
  phase                : STRING  -- 'accumulation'
  fx_usd_exposure      : FLOAT   -- informational

  -- Ranking indicators, calculated in USD, rolling 36M window
  sharpe_rolling       : FLOAT
  sortino_rolling      : FLOAT
  calmar_rolling       : FLOAT
  max_drawdown         : FLOAT
  volatility           : FLOAT

  -- Cumulative returns on calendar windows ending at updated_at
  return_3m            : FLOAT
  return_6m            : FLOAT
  return_1y            : FLOAT
  return_3y            : FLOAT
  return_5y            : FLOAT

  date_revised         : DATE
  trace                : STRING  -- MANDATORY
  updated_at           : DATETIME
}
```

The primary Strategy a Portfolio executes is reached via the `HOLDS` edge
(see edges). No scalar `strategy_id` on Portfolio (removed — was redundant
with the edge).

The Regime a Portfolio was designed for is reached via the `DESIGNED_FOR`
edge (nullable for framework-neutral portfolios). No scalar
`designed_regime_id` on Portfolio.

---

### Document

```
Document {
  id          : STRING  PRIMARY KEY
  title       : STRING
  author      : STRING
  kind        : STRING  -- 'book' | 'article' | 'note' (user one-liner) |
                        --   'event' (UC3 Event Watch — triaged + enriched)
  source_type : STRING  -- 'pdf' | 'kindle' | 'url' | 'text'
  source_path : STRING
  ingested_at : DATE
  chunk_count : INT
  trace       : STRING  -- MANDATORY
}
```

---

### Passage

```
Passage {
  id         : STRING  PRIMARY KEY
  content    : STRING
  page       : INT
  chunk_id   : STRING
  embedding  : FLOAT[384]
  created_at : DATETIME
  -- trace not required (TRACE_EXEMPT): inherits from parent Document
}
```

---

### EventLog
*Append-only audit spine (table `event_log`). No relations ever. No trace
(TRACE_EXEMPT) — the payload IS the trace. Indexed on (type) and
(event_date).*

```
EventLog {
  id         : STRING   PRIMARY KEY  -- app-generated MONOTONIC ULID
                                     --   (strictly increasing even within the
                                     --    same millisecond)
  ts         : DATETIME              -- wall-clock append time — INFORMATIONAL
  event_date : DATE                  -- DOMAIN date the event refers to
                                     --   (indexed — sortable/filterable
                                     --    independently of id and ts).
                                     --   = today for live events; = the
                                     --   historical date for backfilled or
                                     --   retrospective events (e.g. a seed
                                     --   backtest over 2021-2022 carries its
                                     --   period start)
  type       : STRING   -- SeedEvent |
                        --  KnowledgeEvent | ValuationEvent | RankingEvent |
                        --  ProposalEvent | InnovationEvent | UserDecisionEvent |
                        --  RegimeEvent (catch-up detector, on change only) |
                        --  IngestionEvent (inbox watcher, per batch) |
                        --  ErrorEvent (failed job in the Monday chain) |
                        --  ReplayEvent (Phase 9 shadow replay run) |
                        --  OutcomeEvent (weekly outcomes.py — payload.kind:
                        --    'proposal' | 'calibration' | 'probation')
  source_uc  : STRING   -- 'UC0'..'UC9' | 'catch-up' | 'inbox-watcher' | 'system'
  source_id  : STRING   -- id of the entity or run that produced the event
  payload    : STRING   -- JSON (may reference older DOMAIN dates — that is
                        --   normal and does not affect ordering)
}
```

**Ordering semantics — three independent time axes:**
- `id` (monotonic ULID) = **append order, the canonical order** for replay,
  audit and the ordering invariant. The agent is the sole writer and all
  writes are serialized in one asyncio path, so append order is total.
- `ts` = wall-clock append time, informational only (can regress on NTP
  step-back without consequence).
- `event_date` = **domain time** ("when did it happen in the world") —
  indexed, so queries can sort/filter by business date independently of
  insertion order (e.g. `SELECT FROM EventLog WHERE event_date BETWEEN ...
  ORDER BY event_date` for a market-history view).

An event appended after another but carrying an older `event_date` is
therefore **normal** (backfills, retrospective jobs). The
append-before-commit invariant is causal and enforced by code sequence (the
append and its vertex/edge commit happen in the same serialized write path),
never by timestamp comparison.

---

## Graph Schema — RELATION types (10 conceptual in V1 — V2 adds MODIFIES)

**Physical mapping rule (composition):** the five 1:N relations below are
**compositions** — the child cannot exist without its parent (a Scenario
without its Strategy, a Passage without its Document…), so the relation is
constitutive of the child's identity: FK column **and relation properties
on the child row** (each child participates in exactly ONE instance of the
relation, so ownership is unambiguous; the spec still documents each
property under its relation). Only true M:N relations get an association
table (`from_id, to_id, properties`). Guiding principle: **complexify when
needed** — if a 1:N ever becomes M:N, creating the table then is a trivial
SQLite migration.

**1:N — FK columns on the child (5):**

```
Evaluation -[UPDATES]-> Strategy       → evaluation.strategy_id
  (conviction_delta and date already live on Evaluation itself)
Strategy -[HAS_SCENARIO]-> Scenario    → scenario.strategy_id
  (always 3 per active Strategy — bull/base/bear, probabilities sum 100)
Strategy -[TESTED_IN]-> Backtest       → backtest.strategy_id
  + backtest.is_primary : BOOLEAN
Backtest -[IN_REGIME]-> Regime         → backtest.regime_id
  + backtest.overlap_pct : FLOAT  -- percent points, 0-100
Document -[CONTAINS]-> Passage         → passage.document_id
  + passage.position : INT, passage.page : INT
```

**M:N — association tables (5):**

```
RegimeType -[FAVORS]-> Strategy
  -- Multi-period aggregated favorability across ALL historical Regime instances
  --   of this type. Updated after each weekly backtest cycle.
  sharpe_rolling  : FLOAT
  sortino_rolling : FLOAT
  calmar_rolling  : FLOAT
  max_drawdown    : FLOAT
  n_periods       : INT   -- total historical periods of this regime type
  last_updated    : DATE

Strategy -[BACKED_BY]-> Invariant
  strength : FLOAT
  added_at : DATE
  excerpt  : STRING  -- <100 chars

Portfolio -[HOLDS]-> Strategy
  primary : BOOLEAN  -- true for the main strategy a portfolio executes
  weight  : FLOAT
  since   : DATE

Portfolio -[DESIGNED_FOR]-> RegimeType
  -- Points to the type, not an instance; a portfolio may target several
  --   quadrants (see seed). Framework-neutral portfolios have no row.
  rationale : STRING

Passage -[SUPPORTS]-> Invariant
  strength : FLOAT
  excerpt  : STRING
```

**V2:** `Adaptation -[MODIFIES]-> Portfolio` (delta MAP, drawdown_before,
validated_at) — created with Adaptation when V2 lands.

---

## Time-Series types (3)

Plain SQLite tables, full daily granularity, composite PK on
(series id, ts). Ranges are read into pandas; ALL window math happens in
numpy — the engine only stores and serves rows.

```sql
CREATE TABLE IF NOT EXISTS market_data (
  ticker TEXT NOT NULL, asset_class TEXT NOT NULL, currency TEXT NOT NULL,
  ts TEXT NOT NULL,                -- as-known-at date (ADR-003)
  level REAL, speed REAL, acceleration REAL,
  PRIMARY KEY (ticker, ts));

CREATE TABLE IF NOT EXISTS scenario_probability (
  strategy_id TEXT NOT NULL, scenario TEXT NOT NULL, ts TEXT NOT NULL,
  probability REAL,
  PRIMARY KEY (strategy_id, scenario, ts));
-- Week-over-week shift = LAG on read; no stored derivative column.
-- Appended WEEKLY: 08:35 mechanical (numeric triggers) and possibly again
-- by Writeback after Worker adjustments — the POST-WORKER row is the
-- canonical weekly value; every read takes MAX(ts) within the week.

CREATE TABLE IF NOT EXISTS portfolio_nav (
  portfolio_id TEXT NOT NULL, currency TEXT NOT NULL, ts TEXT NOT NULL,
  nav REAL, daily_return REAL, sharpe_rolling REAL, sortino_rolling REAL,
  calmar_rolling REAL, drawdown REAL, vs_benchmark REAL,
  PRIMARY KEY (portfolio_id, ts));
```

**No downsampling policies** — the 756-trading-day rolling windows and the
35y Phase 9 replay require full daily granularity end to end; total volume
(~30 series × 35y daily) is trivial for the embedded engine.

`level` carries the canonical numeric reading (no separate close/volume
columns — volume is not used in any rule); the regime an observation belongs
to is reached via date lookup on Regime (start_date/end_date), never stored
per row.

The `level`/`speed`/`acceleration` columns are how the regime detector spots
early shifts: a value crossing a threshold *and* accelerating is a stronger
signal than the same level reached while decelerating.

### As-known-at-ts rule (ADR-003)

A MarketData row's `ts` is **the date the value became knowable**, and its
`level` is **the value as first known**: macro observations are indexed at
their publication date (ALFRED `realtime_start`; fallback `reference_date +
availability_lag_days`), and the 35y backfill stores ALFRED **first-release**
values for revised series (INDPRO first; CPIAUCSL, UNRATE second).
Composites and z-scores are computed from these as-known rows. The live
fetcher (Monday catch-up / on-demand) appends whatever is current at fetch
time — identical semantics; post-append revisions are ignored (the 2-print hysteresis absorbs
revision noise). This makes `materialize_history` and the Phase 9 replay
point-in-time by construction: they simply read `ts ≤ t`.

### MarketData semantics — what `level` contains per series

| ticker              | asset_class      | level =                                | speed / acceleration lookback |
|---------------------|------------------|----------------------------------------|-------------------------------|
| ETFs (TIP, TLT, …)  | per ticker       | adjusted close (USD)                   | `derivative_lookback_short` (30d) |
| ^IRX                | RISK_FREE        | annualized yield, percent points       | 30d                           |
| ^VIX                | VOLATILITY       | index close (sole VIX source)          | 30d                           |
| CHFUSD=X            | FX               | spot rate                              | 30d                           |
| CPIAUCSL            | MACRO            | **CPI YoY %** (computed from the index in derivatives.py) | 1 observation (monthly series): speed = Δ1m of YoY, accel = Δ of speed |
| T10Y2Y              | MACRO            | raw spread, percent points             | 30d                           |
| UNRATE              | MACRO            | raw rate, percent points               | 1 obs (monthly)               |
| INDPRO              | MACRO            | **YoY %** of the index                 | 1 obs (monthly)               |
| GROWTH_COMPOSITE    | MACRO            | composite index (see below)            | 1 obs (monthly)               |
| GLOBAL_LIQUIDITY    | GLOBAL_LIQUIDITY | composite index (see below)            | 7d (weekly components)        |

### Composite series (computed in Python, stored as MarketData rows)

**GROWTH_COMPOSITE** — the 4 Seasons growth axis (replaces ISM PMI, which has
no free perennial source — decision recorded in IMPROVEMENTS I-20):

```
z_indpro  = z-score of INDPRO YoY   over trailing 10y
z_unrate  = z-score of Δ3m(UNRATE)  over trailing 10y
raw       = (z_indpro − z_unrate) / 2        -- unemployment rising = growth falling
level     = 100 + 10 × raw                   -- >100 expansion, <100 contraction
```

**GLOBAL_LIQUIDITY** — composite of M2SL, WALCL, ECBASSETSW, JPNASSETS:

```
z_i    = z-score of component i (USD-converted) over trailing 5y
level  = 100 + 10 × mean(z_i)                -- >100 easing, <100 tightening
```

Tags derived for Regime instances: `liquidity-tightening` when
`level < 100 AND speed < 0`; `liquidity-easing` when `level > 100 AND speed > 0`.

---

## Calculation conventions (pinned — implementations must match to the digit)

- **Calendar**: NYSE trading calendar. Annualization factor **252**.
  Scheduling timezone Europe/Zurich; TS timestamps are UTC dates.
- **Daily risk-free**: `rf_daily = (1 + IRX_level/100)^(1/252) − 1`
  (latest available ^IRX).
- **NAV synthesis** (seed backfill and daily update): constant target weights,
  **rebalanced monthly** on the first trading day of each month. The `cash`
  sleeve accrues daily at `rf_daily`. `NAV(t0) = 100`. Prices = MarketData
  `level` (adjusted close, USD).
- **daily_return**: `NAV(t)/NAV(t−1) − 1`.
- **Rolling window**: 756 trading days (36M) for all `*_rolling` indicators.
  If history < 756d, use all available history and flag
  `window_incomplete=true` in the snapshot trace.
- **sharpe_rolling**: `mean(r − rf_daily) / std(r, ddof=1) × √252` over the window.
- **sortino_rolling**: `mean(r − rf_daily) / downside_dev × √252`, with
  `downside_dev = sqrt(mean(min(0, r − rf_daily)²))` (MAR = rf).
- **max_drawdown**: `min(NAV/cummax(NAV) − 1)` within the window (decimal fraction).
- **calmar_rolling**: `((NAV_end/NAV_start)^(252/window_days) − 1) / |max_drawdown|`.
- **volatility**: `std(r, ddof=1) × √252` over the window.
- **return_3m/6m/1y/3y/5y**: `NAV(t)/NAV(t − Nd) − 1` on calendar windows of
  91/182/365/1095/1826 days (nearest previous trading day).
- **Missing data**: forward-fill up to 5 trading days; longer gaps abort the
  affected computation and emit an ErrorEvent.

---

## Document types

Plain tables. Ids are generated in Python as ULIDs; structured values are
JSON columns (SQLite JSON1).

**Table criterion (guiding principle):** a table exists only for data that
**grows or changes at runtime** (confrontations, snapshots, calibration,
user-editable rules/thresholds). Bounded static config lives in code
constants (e.g. `EVENT_SOURCES`, `TRACE_EXEMPT`, `FORBIDDEN_SQL`) — promote
to a table only when it actually starts growing or needs runtime editing.

### Static

```sql
CREATE TABLE IF NOT EXISTS user_profile (...);
-- user_id STRING (PK, unique index), currency STRING, benchmark STRING,
-- max_drawdown_pct FLOAT (BINDING for defender role + proposal candidacy),
-- max_single_asset_pct FLOAT (BINDING),
-- phase STRING, horizon_years INTEGER, risk_tolerance STRING,
-- auto_validation_hours INTEGER (default 48 — V2 auto-validation timer),
-- telegram_chat_id STRING, created_at DATE, updated_at DATE

CREATE TABLE IF NOT EXISTS invariant_author_config (...);
-- author STRING (PK: 'dalio'|'marks'|'other'|'system'; 'other' is the sentinel
--   for Invariant.author = null), floor_weight FLOAT,
-- initial_weight_min FLOAT, initial_weight_max FLOAT, description STRING

CREATE TABLE IF NOT EXISTS allowed_tickers (...);
-- ticker STRING (PK), asset_class STRING, currency STRING,
-- source STRING ('yahoo'|'fred'|'composite'), transform STRING
--   ('none'|'yoy_pct'|'composite'),
-- availability_lag_days INTEGER (0 for market prices; publication lag for
--   monthly macro series — fallback dating when ALFRED realtime_start is
--   unavailable, see ADR-003),
-- description STRING, active BOOLEAN
-- Includes macro series and composites so market_fetch can expose them
--   to the Worker.

CREATE TABLE IF NOT EXISTS system_thresholds (...);

CREATE TABLE IF NOT EXISTS detector_state (...);
-- Single row — the regime detector's persisted hysteresis state (it must
--   survive restarts: due-on-start). candidate_type STRING,
--   consecutive_prints INTEGER, last_print_ts_growth TEXT,
--   last_print_ts_inflation TEXT, updated_at TEXT.
-- Runtime-changing state → table (criterion above).
-- key STRING (PK), value FLOAT, description STRING, updated_at DATE
-- Seed includes regime thresholds, rolling window (756d), recency half-life,
-- vector similarity floor, proposal gate thresholds (switch AND reallocation),
-- proposal_expiry_days,
-- invariant_min_confrontations (N_min, 3) and invariant_time_validation_score
--   (θ, 0.60) — the time-validation verdict gate (ARCHITECTURE "Birth
--   maturation"); both calibrated by the Phase 9 replay.
-- (See TASKS.md seed.)

```

Removed as redundant duplicates (single-engine rule — the graph vertex IS
the record): `invariant_weights` (all weight fields live on `Invariant`),
`regime_history` (all fields live on / are derivable from `Regime`
instances), `schema_extensions` (schema self-extension deferred to V2 —
IMPROVEMENTS I-27). (Per-period STRATEGY valuations are NOT redundant — they
live in `benchmark_valuation.strategy` as the `cross_strategy` benchmark;
`Backtest` is regime-type-aggregated, `FAVORS` too, so neither holds the
per-period series.)

### Analytical

```sql
CREATE TABLE IF NOT EXISTS invariant_confrontations (...);
-- id STRING (PK, ULID), invariant_id STRING, moment_context STRING, date DATE,
--   -- moment_context: the regime type if the moment is regime-keyed, else a
--   --   compact descriptor of the condition that held (e.g. 'inflation:rising')
--   --   — a moment is any condition-occurrence, not only a regime
-- verdict STRING ('confirmed'|'refuted'), severity FLOAT,
-- source STRING ('backtest'|'evaluation'|'proposal'|'adaptation' (V2)),
-- source_id STRING

CREATE TABLE IF NOT EXISTS benchmark_valuation (...);
-- The pre-materialised BENCHMARK that effect.method reads at confrontation:
-- 'cross_class' → the asset-class rows; 'cross_strategy' → the strategy rows
-- (USE_CASES step 10b; "define and value the benchmarks before valuing
--  invariants"). Grows per period → a table (criterion).
-- benchmark_kind STRING ('asset_class'|'strategy'), benchmark_id STRING,
-- date DATE (unique index on (benchmark_kind, benchmark_id, date)),
-- return FLOAT, sortino_rolling FLOAT, max_drawdown FLOAT, volatility FLOAT
--   -- asset_class rows: per reference class (equities / bonds / inflation-
--   --   protected / gold-commodities / cash) from constituent ETF prices;
--   --   class membership = the pinned BENCHMARK_CLASSES mapping (TASKS seed)
--   --   over allowed_tickers.asset_class (fine → coarse).
--   -- strategy rows: per Strategy's prescribed allocation (synthetic NAV).
--   -- Rebuilt over 35y at seed, extended weekly.

CREATE TABLE IF NOT EXISTS portfolio_weekly_snapshot (...);
-- date DATE, portfolio_id STRING (unique index on (date, portfolio_id)),
-- defender BOOLEAN, framework_id STRING,
-- designed_regime_type_id STRING (denormalized from DESIGNED_FOR),
-- primary_strategy_id STRING (denormalized from HOLDS(primary=true)),
-- allocation MAP, rank INTEGER,
-- sharpe_rolling FLOAT, sortino_rolling FLOAT, calmar_rolling FLOAT,
-- max_drawdown FLOAT, volatility FLOAT,
-- return_3m FLOAT, return_6m FLOAT, return_1y FLOAT, return_3y FLOAT,
-- return_5y FLOAT,
-- gap_to_defender MAP, market_context MAP,
-- recommendation STRING,  -- 'maintain'/'monitor' written by UC7 (mechanical);
--                         --   upgraded to 'paper-test' by Writeback after the
--                         --   UC8 cycle when a proposal gate is met
-- trace STRING

CREATE TABLE IF NOT EXISTS scenario_calibration (...);
-- id STRING (PK, ULID), strategy_id STRING, date DATE,
-- dominant_scenario STRING, realized STRING ('bull'|'base'|'bear' mapped
--   from the realized regime/quadrant), score FLOAT (Brier-style)
-- Written weekly by score_scenarios() at +scenario_calibration_weeks;
-- summarized into the Worker context and the digest scoreboard.

CREATE TABLE IF NOT EXISTS replay_report (...);
-- id STRING (PK, ULID), run_at DATETIME, window_start DATE, window_end DATE,
-- kind STRING ('mechanical' | 'agentic')  -- 'mechanical' = Task 9.1 (go-live
--   evidence); 'agentic' = Task 9.4 (Planner+Worker, semi-PIT, NOT go-live)
-- thresholds MAP (the set replayed), acceptance_policy STRING,
-- nav_agent_follow MAP, nav_hold_defender MAP
--   (each: cagr, sortino, calmar, max_drawdown — decimal fractions),
-- n_switches INTEGER, avg_turnover FLOAT, hit_rate_12w FLOAT,
-- false_signal_rate FLOAT, cost_bps FLOAT, pit_assertions_passed BOOLEAN,
-- vintage_mode STRING ('first_release' expected — a go-live verdict obtained
--   on revised data is not valid evidence, ADR-003),
-- delta_vs_mechanical REAL   -- kind='agentic' only: A' − A (Worker's marginal
--   contribution over the mechanical core); labelled non-PIT
-- behavioral_log JSON        -- kind='agentic' only: per-date Worker decision +
--   reasoning + gate outcome, for owner inspection at crisis points
-- notes STRING
-- Written by Phase 9: kind='mechanical' (Task 9.1) is read by the AUTOMATED
--   main.py go-live gate; kind='agentic' (Task 9.4) is never the automated
--   gate but IS the necessary MANUAL pre-go-live STOP (M8b — best-case screen).
--   (nav_benchmark dropped with the 60/40 benchmark; vs_benchmark vs
--    ALL_WEATHER_BENCHMARK lives on the snapshot, not here.)
```

Ranking rule (applies to snapshot rows):
1. primary key = `sortino_rolling` DESC
2. tie-break (within 0.02) = `calmar_rolling` DESC
3. final tie-break = `max_drawdown` (less negative wins)

Snapshots with `calmar_rolling < 1.0` are demoted to the bottom regardless of
Sortino (Invariant#calmar-accumulation gate). A `max_drawdown` breaching the
**user** rule (-15%) keeps the row in the ranking but excludes the portfolio
from the defender role and from proposal candidacy.

The `Strategy` vertex is the single source of truth for strategies — no
separate library document.

---

## Worker output models

```python
class ImprovementType(str, Enum):
    new_invariant   = "new_invariant"   # the canonical V1 innovation (EXAMPLE Step 6)
    new_strategy    = "new_strategy"    # complete strategy spec — see ARCHITECTURE
                                        #   "System Evolution" for the required
                                        #   spec fields and validation lifecycle
    strategy_revision = "strategy_revision"  # "better strategy": new_strategy spec
                                        #   + supersedes:<strategy_id>; on
                                        #   validation the old version is closed
                                        #   (date_revised set) — ARCHITECTURE
    process         = "process"
    data            = "data"            # new metric / threshold proposals
    # schema self-extension (new vertex/edge/property types) deferred to V2
    # — IMPROVEMENTS I-27: a schema element without code to use it is dead
    # weight.

class ImprovementProposal(BaseModel):
    type           : ImprovementType
    title          : str
    rationale      : str
    spec           : dict             # new_invariant: InvariantCandidate fields;
                                      #   new_strategy: full strategy spec incl.
                                      #   the 3 scenario definitions (ARCHITECTURE)
    source         : str = "agent-discovery"
    author         : str = "system"   # drives the floor tier, like Invariant.author
    status         : str = "proposed"
    weight_initial : float            # new_invariant only; ignored otherwise
    floor_weight   : float            # new_invariant only; ignored otherwise
    trace          : str

class ReallocationProposal(BaseModel):
    """Worker-proposed adjustment of the DEFENDER's allocation (paper-mode).
    Writeback validates mechanically before persisting a Proposal vertex."""
    proposed_allocation : dict[str, float]   # percent weights, must sum to 100
    scenario_delta      : dict[str, float]   # tactical input (active scenario target − current)
    favors_delta        : dict[str, float]   # structural input (FAVORS-derived − current)
    blend_note          : str                # how 0.4/0.6 blend was applied
    supporting_invariants : list[str]        # invariant ids cited
    reasoning           : str

# WorkerResult must always include:
#   innovations_proposed  : list[ImprovementProposal]      (empty list if none)
#   reallocation_proposed : Optional[ReallocationProposal] (None if none)
# Full WorkerResult schema in ARCHITECTURE.md.
```

---

## Persistence Routing

*Planner Post decides what to persist. Writeback is a pure executor. EventLog
append always precedes vertex/edge commit.*

```
Invariant    → EventLog → vertex (all weight fields live here) → edges
              (SUPPORTS from passages; BACKED_BY from suggested_backed_by
              at birth) → mature_invariant() (35y) → FTS + vector
Evaluation   → EventLog → vertex (events[] filled) → UPDATES → FTS
Scenario     → EventLog → vertex update → ScenarioProbability TS (weekly)
Proposal V1  → mechanical gates (switch or reallocation) → EventLog
              → Proposal vertex → snapshot `recommendation` upgrade → Telegram
Adaptation V2 → concentration check → EventLog → vertex → Telegram timer
Portfolio    → vertex → HOLDS + DESIGNED_FOR edges → PortfolioNAV TS
Backtest     → EventLog → vertex → TESTED_IN + IN_REGIME → FAVORS
Regime       → EventLog (RegimeEvent, on change) → vertex (is_current updated)
Framework    → EventLog → vertex (seed only in V1)
ImprovementProposal → EventLog → vertex (status:proposed) → Telegram
Document/Passage (watcher) → EventLog (IngestionEvent, per batch)
              → vertices → CONTAINS + SUPPORTS
```

---

## Storage

```
One SQLite file: ~/data/investment/investment.db (WAL sidecar files
alongside). Dataset ≈ 100 MB — lives in the OS page cache after first read,
so reads are effectively in-memory.

Embeddings: float32×384 BLOBs on invariant/passage rows, loaded at startup
into an in-RAM numpy matrix (~15 MB at 10k passages) and APPENDED
INCREMENTALLY at runtime — the BLOB row is COMMITTED first, then the matrix
row appended (the matrix is a cache of committed state, rebuildable at any
restart); similarity =
brute-force cosine (<10 ms at this scale). No vector index, no FTS in V1
(FTS5 available natively if ever needed).

Backup after every chain/ingestion batch — sqlite3 .backup (WAL-safe); see TASKS.md Phase 7
```

---

## Regime detection thresholds

Loaded from `system_thresholds` — not hardcoded. Detection uses `level`,
`speed`, and `acceleration` from MarketData TS, not only static thresholds.
The formal detection algorithm (axis classification, confidence formula,
hysteresis) is specified in ARCHITECTURE.md.

Strategy `conditions` must reference at least one indicator NOT in the regime
threshold set, to avoid tautological self-confirmation, and every referenced
indicator must be mechanically computable from MarketData TS or Regime fields.
Manual check at seed time in V1 — see IMPROVEMENTS I-12 for automated check.
