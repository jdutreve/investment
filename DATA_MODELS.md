# DATA_MODELS.md — Investment Agent MVP

See REVISION_NOTES.md for V1 scope, core concepts, and ranking rule.

## Persistence principle

ArcadeDB embedded in-process (`arcadedb-embedded`, Apache 2.0). The agent is the
sole writer — writes serialized via asyncio. Binary sources (PDF, Kindle CSV)
on filesystem, referenced via `Document.source_path`.

**Mandatory rule:** any vertex with empty `trace` is rejected by Planner Post
with `ValueError`.

**Event TS ordering rule:** every Event TS append must precede the
corresponding vertex/edge commit in ArcadeDB.

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

## Graph Schema — VERTEX types (13)

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
because they represent multi-period aggregated knowledge about the type.*

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
daily mechanical job (06:50). IN_REGIME edges point here.*

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
  confidence      : FLOAT                 -- 0-100
  is_current      : BOOLEAN               -- exactly one true at a time per framework
  events          : STRING[]              -- summarised numeric observations that
                                          --   triggered the regime (CPI level/speed/accel,
                                          --   PMI ditto, global liquidity ditto, ...)
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
  status        : STRING            -- 'proposed' | 'validated' | 'integrated' | 'rejected'
  topic         : STRING[]          -- semantic topics
  tags          : STRING[]          -- ex: 'asset:GLD', 'indicator:max_drawdown',
                                    --   'asset-class:fixed-income', 'phase:accumulation'
  embedding     : FLOAT[768]

  weight_initial   : FLOAT
  floor_weight     : FLOAT
  weight_effective : FLOAT  -- = max(weight_initial × market_score × recency_factor,
                            --        floor_weight)

  confirmation_count : INT
  infirmation_count  : INT
  market_score       : FLOAT
  recency_factor     : FLOAT  -- recomputed from updated_at; no separate last_confronted

  trace           : STRING  -- MANDATORY
  created_at      : DATETIME
  validated_at    : DATETIME
  updated_at      : DATETIME  -- last confrontation (drives recency_factor)
}
```

Half-life uniform in V1: 365 days (see IMPROVEMENTS I-5).
Floor by **author** tier, persisted at creation:
`author='dalio'`=0.40, `author='marks'`=0.35, `author=null` (other corpus)=0.20,
`author='system'` (agent-discovery)=0.05.

---

### Strategy
*Investment thesis or concept. (Renamed from `Strategie`.)*

```
Strategy {
  id             : STRING  PRIMARY KEY  -- convention: <regimeType.alias>-<name>-<vN>
                                        --   for regime-specific strategies (ex:
                                        --   'stagflation-custom-v2'); framework-neutral
                                        --   strategies keep canonical names
                                        --   ('4seasons', 'permanent', ...)
  title          : STRING
  description    : STRING               -- one-paragraph rationale of the thesis
  regime_type_id : STRING               -- FK → RegimeType.id; null for framework-neutral
  framework_id   : STRING               -- '4seasons' in V1
  conviction     : FLOAT                -- 0-100; updated after each Evaluation
  enabled        : BOOLEAN              -- false = excluded from ranking and Worker context
  conditions     : STRING               -- must include ≥1 dimension orthogonal to regime
  source         : STRING               -- 'corpus' | 'agent-discovery' (free text accepted)
  status         : STRING               -- 'proposed' | 'validated' | 'active' | 'closed'
  date_opened    : DATE
  date_revised   : DATE
  trace          : STRING  -- MANDATORY
  created_at     : DATETIME
  updated_at     : DATETIME
}
```

`horizon`, `base_strategy`, `revision_if` and `benchmark` have been removed: a
Strategy is implicitly bounded by the regime it serves, the alias-first ID makes
the base lineage obvious, the revision condition is the inverse of `conditions`,
and benchmarking is a Portfolio concern.

---

### Scenario
*Probabilities of bull/base/bear must always sum to 100 per Strategy.
The owning Strategy is reached via the HAS_SCENARIO edge — no `strategy_id`
scalar on the vertex (the seed dicts carry one only to build the edge).*

```
Scenario {
  id                : STRING  PRIMARY KEY
  name              : STRING  -- 'bull' | 'base' | 'bear'
  probability       : FLOAT   -- 0-100
  probability_d7    : FLOAT   -- probability 7 days ago
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
                              --   replaces the former GENERATES edge: MarketData
                              --   is a TS, not a vertex, so it cannot be an edge
                              --   source
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
  sharpe_rolling  : FLOAT   -- USD, rolling 36M (756 trading days), rf = ^IRX
  sortino_rolling : FLOAT
  calmar_rolling  : FLOAT   -- annualized return / |max_drawdown|; window = 36M
  max_drawdown    : FLOAT
  total_return    : FLOAT
  currency        : STRING  -- 'USD'
  source          : STRING  -- 'agent-discovery' | 'mechanical'
  status          : STRING  -- 'proposed' | 'validated' | 'integrated'
  trace           : STRING  -- MANDATORY
  created_at      : DATETIME
}
```

---

### Proposal
*V1 paper-mode recommendation. Persisted per weekly cycle when a challenger meets the gate.*

```
Proposal {
  id              : STRING  PRIMARY KEY
  date            : DATE
  defender_id     : STRING  -- portfolio_id of current live
  challenger_id   : STRING  -- portfolio_id of challenger
  recommendation  : STRING  -- 'maintain' | 'monitor' | 'paper-test'
  defender_rank   : INT
  challenger_rank : INT
  gap             : MAP     -- {sharpe, sortino, calmar, max_drawdown, allocation_diff}
  market_context  : MAP     -- {framework, regime, confidence, global_liquidity, derivatives}
  reasoning       : STRING
  user_response   : STRING  -- 'pending' | 'accepted' | 'rejected' | 'expired'
  paper_started   : DATE    -- when paper-test recommendation was issued
  trace           : STRING  -- MANDATORY
  created_at      : DATETIME
}
```

No edge to Portfolio in V1 — `defender_id` and `challenger_id` as scalars. An
edge can be added in V2 when traversal becomes useful.

---

### Adaptation — V2 only

In V1, this vertex is reserved but unused. V1 proposals live in `Proposal`.

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
                                 --   (replaces the former `live` boolean.)
  enabled              : BOOLEAN -- false = excluded from ranking
  currency             : STRING  -- user display currency (CHF)
  benchmark            : STRING  -- benchmark ticker for this portfolio (Portfolio-level,
                                 --   not Strategy-level)
  allocation           : MAP     -- concrete ETF allocation, mandatory
  max_drawdown_rule    : FLOAT   -- user-defined floor (e.g. -15.0)
  max_single_asset_pct : FLOAT   -- concentration cap (e.g. 40.0)
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
  embedding  : FLOAT[768]
  created_at : DATETIME
  -- trace not required: inherits from parent Document
}
```

---

## Graph Schema — EDGE types (11)

Creation order: vertices first, edges second.

```
Evaluation -[UPDATES]-> Strategy
  conviction_delta : FLOAT
  date             : DATE

RegimeType -[FAVORS]-> Strategy
  -- Multi-period aggregated favorability across ALL historical Regime instances
  --   of this type. Updated after each weekly backtest cycle.
  -- Query: MATCH (rt:RegimeType)<-[:regime_type_id]-(r:Regime {is_current:true}),
  --               (rt)-[f:FAVORS]->(s:Strategy)
  sharpe_rolling  : FLOAT
  sortino_rolling : FLOAT
  calmar_rolling  : FLOAT
  max_drawdown    : FLOAT
  n_periods       : INT   -- total historical periods of this regime type
  last_updated    : DATE

Strategy -[HAS_SCENARIO]-> Scenario
  -- Always 3 per active Strategy (bull, base, bear); probabilities sum to 100
  active : BOOLEAN

Strategy -[BACKED_BY]-> Invariant
  strength : FLOAT
  added_at : DATE
  excerpt  : STRING  -- <100 chars

Strategy -[TESTED_IN]-> Backtest
  is_primary : BOOLEAN

Backtest -[IN_REGIME]-> Regime
  overlap_pct : FLOAT  -- percent points, 0-100 (units convention)

Adaptation -[MODIFIES]-> Portfolio
  -- V2 only; not used in V1
  delta            : MAP
  drawdown_before  : FLOAT
  validated_at     : DATE

Portfolio -[HOLDS]-> Strategy
  -- Replaces the former Portfolio.strategy_id scalar
  primary : BOOLEAN  -- true for the main strategy a portfolio executes
  weight  : FLOAT
  since   : DATE

Portfolio -[DESIGNED_FOR]-> RegimeType
  -- Points to the type, not an instance: the portfolio is designed for
  --   stagflation in general, not for the May 2026 occurrence.
  -- Nullable: framework-neutral portfolios (e.g. permanent) have no edge.
  rationale : STRING

Document -[CONTAINS]-> Passage
  position : INT
  page     : INT

Passage -[SUPPORTS]-> Invariant
  strength : FLOAT
  excerpt  : STRING
```

---

## Time-Series types (4)

```sql
-- OHLCV market data + macro + FX + risk-free rate + global liquidity
CREATE TIME SERIES TYPE MarketData IF NOT EXISTS (
  ticker       STRING,    -- 'TIP' | 'TLT' | 'GLD' | 'CHFUSD=X' | '^IRX' | ...
  asset_class  STRING,    -- 'US_TIPS' | 'GOLD' | 'MACRO' | 'FX' |
                          --  'RISK_FREE' | 'GLOBAL_LIQUIDITY'
  currency     STRING,    -- 'USD'
  level        FLOAT,     -- current value (smoothed if applicable)
  speed        FLOAT,     -- first derivative over configured lookback
  acceleration FLOAT      -- second derivative — flags regime shifts early
);
-- `close`, `volume`, `regime_id` were removed: `level` already carries the
-- canonical numeric reading; volume is not used in any regime/indicator rule;
-- the regime an observation belongs to is reached via date lookup on Regime
-- (start_date/end_date), not stored redundantly on each TS row.
ALTER TIMESERIES TYPE MarketData ADD DOWNSAMPLING POLICY
  AFTER 30 DAYS  GRANULARITY 1 DAY
  AFTER 365 DAYS GRANULARITY 1 WEEK;

CREATE TIME SERIES TYPE ScenarioProbability IF NOT EXISTS (
  strategy_id  STRING,
  scenario     STRING,
  probability  FLOAT,
  shift_d7     FLOAT
);
ALTER TIMESERIES TYPE ScenarioProbability ADD DOWNSAMPLING POLICY
  AFTER 7 DAYS  GRANULARITY 1 DAY
  AFTER 30 DAYS GRANULARITY 1 WEEK;

CREATE TIME SERIES TYPE PortfolioNAV IF NOT EXISTS (
  portfolio_id    STRING,
  currency        STRING,    -- 'USD'; CHF computed on read via CHFUSD=X
  nav             FLOAT,
  daily_return    FLOAT,
  sharpe_rolling  FLOAT,     -- rolling 36M (756 trading days), rf = ^IRX
  sortino_rolling FLOAT,
  calmar_rolling  FLOAT,     -- rolling 36M (756 trading days)
  drawdown        FLOAT,
  vs_benchmark    FLOAT
);
ALTER TIMESERIES TYPE PortfolioNAV ADD DOWNSAMPLING POLICY
  AFTER 90 DAYS GRANULARITY 1 WEEK;

CREATE TIME SERIES TYPE Event IF NOT EXISTS (
  type      STRING,   -- SeedEvent | MarketEvent | KnowledgeSearchEvent |
                      --  KnowledgeEvent | ValuationEvent | RankingEvent |
                      --  ProposalEvent | InnovationEvent | UserDecisionEvent
  source_uc STRING,
  source_id STRING,
  payload   STRING    -- JSON
);
```

The `level`/`speed`/`acceleration` columns on MarketData are how the regime
detector spots early shifts: a value crossing a threshold *and* accelerating
is a stronger signal than the same level reached while decelerating.

---

## SQL Tables

### Static

```sql
CREATE TABLE user_profile (
  user_id                 VARCHAR(50) PRIMARY KEY,
  currency                VARCHAR(3),
  benchmark               VARCHAR(100),
  max_drawdown_pct        FLOAT,
  max_single_asset_pct    FLOAT,
  phase                   VARCHAR(20),
  horizon_years           INT,
  risk_tolerance          VARCHAR(20),
  rebalance_threshold     FLOAT,
  auto_validation_hours   INT DEFAULT 48,
  telegram_chat_id        VARCHAR(50),
  created_at              DATE,
  updated_at              DATE
);

CREATE TABLE invariant_author_config (
  author              VARCHAR(50) PRIMARY KEY,  -- 'dalio' | 'marks' | 'other' | 'system'
                                                -- ('other' is the sentinel for
                                                --  Invariant.author = null; a SQL
                                                --  PK cannot be NULL)
  floor_weight        FLOAT,
  initial_weight_min  FLOAT,
  initial_weight_max  FLOAT,
  description         TEXT
);

CREATE TABLE allowed_tickers (
  ticker      VARCHAR(20) PRIMARY KEY,
  asset_class VARCHAR(50),
  currency    VARCHAR(3),
  description TEXT,
  active      BOOLEAN DEFAULT true
);

CREATE TABLE system_thresholds (
  key         VARCHAR(50) PRIMARY KEY,
  value       FLOAT,
  description TEXT,
  updated_at  DATE
);
-- Seed includes regime thresholds, rolling window (756d, all *_rolling
-- indicators), recency half-life, vector similarity floor, proposal gate
-- thresholds, etc. (See investment-TASKS.md seed.)

CREATE TABLE schema_extensions (
  id               UUID DEFAULT gen_random_uuid(),
  improvement_type VARCHAR(50),
  name             TEXT,
  spec             JSONB,
  status           VARCHAR(20),
  proposed_at      DATE,
  validated_at     DATE,
  rationale        TEXT
);
```

The previous `strategies_library` SQL table is removed: the `Strategy` vertex
is the single source of truth.

### Analytical

```sql
CREATE TABLE strategy_performance (
  strategy_id    VARCHAR(50),
  regime_type_id VARCHAR(50),  -- references RegimeType.id
  currency       VARCHAR(3),
  period_start  DATE,
  period_end    DATE,
  sharpe_rolling  FLOAT,
  sortino_rolling FLOAT,
  calmar_rolling  FLOAT,
  max_drawdown    FLOAT,
  total_return    FLOAT,
  n_periods       INT,
  PRIMARY KEY (strategy_id, regime_type_id, period_start, currency)
);

CREATE TABLE invariant_weights (
  invariant_id       VARCHAR(50) PRIMARY KEY,
  weight_initial     FLOAT,
  floor_weight       FLOAT,
  weight_effective   FLOAT,
  market_score       FLOAT,
  recency_factor     FLOAT,
  confirmation_count INT,
  infirmation_count  INT,
  updated_at         DATE  -- last confrontation; drives recency_factor
);

CREATE TABLE regime_history (
  regime_id      VARCHAR(50) PRIMARY KEY,
  regime_type_id VARCHAR(50),  -- FK → RegimeType.id; denormalized for fast analytics
  start_date     DATE,
  end_date       DATE,
  confidence     FLOAT,
  duration_days  INT,
  followed_by    VARCHAR(50)   -- regime_id of the next instance
);

CREATE TABLE invariant_confrontations (
  id           UUID DEFAULT gen_random_uuid(),
  invariant_id VARCHAR(50),
  regime       VARCHAR(50),
  date         DATE,
  verdict      VARCHAR(20),  -- 'confirmed' | 'refuted'
  severity     FLOAT,
  source       VARCHAR(50),  -- 'backtest' | 'proposal' (V1) | 'adaptation' (V2)
  source_id    VARCHAR(50)
);

CREATE TABLE portfolio_weekly_snapshot (
  date              DATE,
  portfolio_id      VARCHAR(50),
  defender          BOOLEAN,                -- renamed from `live`
  framework_id      VARCHAR(50),
  designed_regime_type_id VARCHAR(50),  -- denormalized from DESIGNED_FOR (Portfolio→RegimeType)
  primary_strategy_id VARCHAR(50), -- denormalized from HOLDS(primary=true) edge
  allocation        JSONB,
  rank              INT,                    -- see Ranking rule below
  sharpe_rolling    FLOAT,                  -- USD, rolling 36M (756 trading days)
  sortino_rolling   FLOAT,
  calmar_rolling    FLOAT,
  max_drawdown      FLOAT,
  volatility        FLOAT,
  return_3m         FLOAT,                  -- cumulative return on calendar window
  return_6m         FLOAT,
  return_1y         FLOAT,
  return_3y         FLOAT,
  return_5y         FLOAT,
  gap_to_defender   JSONB,
  market_context    JSONB,
  recommendation    TEXT,   -- written as 'maintain'/'monitor' by UC7 (mechanical);
                            --   upgraded to 'paper-test' by Writeback after the
                            --   UC8 Worker cycle when the proposal gate is met
  trace             TEXT,
  PRIMARY KEY (date, portfolio_id)
);
-- Ranking rule:
--   1. primary key  = sortino_rolling DESC
--   2. tie-break (within 0.02) = calmar_rolling DESC
--   3. final tie-break          = max_drawdown (less negative wins)
-- Snapshots with calmar_rolling < 1.0 are demoted to the bottom regardless of
-- Sortino (Invariant#calmar-accumulation gate). The Phase 1 max-drawdown rule
-- (-15%) is a hard exclusion for the defender role.
```

`adaptation_quality` removed from V1 (V2-only). Re-added with V2.

---

## ImprovementProposal

```python
class ImprovementType(str, Enum):
    schema_vertex   = "schema_vertex"
    schema_edge     = "schema_edge"
    schema_property = "schema_property"
    process         = "process"
    data            = "data"

class ImprovementProposal(BaseModel):
    type           : ImprovementType
    title          : str
    rationale      : str
    spec           : dict
    source         : str = "agent-discovery"
    author         : str = "system"   # drives the floor tier, like Invariant.author
    status         : str = "proposed"
    weight_initial : float
    floor_weight   : float
    trace          : str

# WorkerResult must always include:
#   innovations_proposed: list[ImprovementProposal]  (empty list if none)
```

---

## Persistence Routing

*Planner Post decides what to persist. Writeback is a pure executor. Event TS append always precedes vertex/edge commit.*

```
Invariant    → Event TS → vertex → edges → FTS + vector → invariant_weights SQL
Evaluation   → Event TS → vertex (events[] filled) → UPDATES → FTS
Scenario     → Event TS → vertex update → ScenarioProbability TS
Proposal V1  → concentration check → Event TS → Proposal vertex
              → portfolio_weekly_snapshot row → Telegram
Adaptation V2 → concentration check → Event TS → vertex → Telegram timer
Portfolio    → vertex → HOLDS + DESIGNED_FOR edges → PortfolioNAV TS
Backtest     → Event TS → vertex → TESTED_IN + IN_REGIME → FAVORS
              → strategy_performance SQL
Regime       → Event TS → vertex (is_current updated) → regime_history SQL
Framework    → Event TS → vertex (seed only in V1)
ImprovementProposal → Event TS → vertex (status:proposed) → Telegram
```

---

## Storage

```
Graph + Vector (LSMVectorIndex — HNSW + LSM Tree)
  /data/investment/arcade_db/
  LRU page cache: arcadedb.maxPageRAM=512m (CAX21 4GB RAM)

Time-Series — native automatic tiering via DOWNSAMPLING POLICY
```

---

## Regime detection thresholds

Loaded from `system_thresholds` SQL — not hardcoded. Detection uses `level`,
`speed`, and `acceleration` from MarketData TS, not only static thresholds.

Strategy `conditions` must reference at least one indicator NOT in the regime
threshold set, to avoid tautological self-confirmation. Manual check at seed
time in V1 — see IMPROVEMENTS I-12 for automated check.
