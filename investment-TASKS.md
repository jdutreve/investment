# TASKS.md — Investment Agent MVP

See REVISION_NOTES.md for V1 scope and core concepts.

## Objective

Build capital (10-20 year horizon) via a self-improving expert investment agent.
Phase 1: accumulation only.

V1 delivers a weekly portfolio ranking and digest engine. V2 adds auto-adaptive
execution and automatic learning from real allocation changes.

V1 mechanisms:
1. Market context — 4 Seasons regime + global liquidity, with
   level/speed/acceleration on MarketData TS. Growth axis = GROWTH_COMPOSITE.
2. Knowledge seed — Documents/notes → Passages → Invariants.
3. Portfolio universe seed — Strategies as theses; Portfolios as concrete
   ETF allocations.
4. Ranking — all enabled portfolios, including the defender, using USD
   `sharpe_rolling`, `sortino_rolling`, `calmar_rolling`, `max_drawdown`,
   `volatility`, plus cumulative `return_3m / 6m / 1y / 3y / 5y`.
5. Digest/proposal — Telegram weekly digest + optional Proposal vertex
   (switch or reallocation, both paper-mode).

See IMPROVEMENTS.md for deferred V2 features.

---

## Scope MVP

| Component       | Detail                                                        |
|-----------------|---------------------------------------------------------------|
| DB              | ArcadeDB embedded in-process (single engine: graph + vector + FTS + TS + documents) |
| Graph           | 14 vertex types (incl. EventLog), 11 edge types               |
| Time-Series     | MarketData + ScenarioProbability + PortfolioNAV               |
| LLM Framework   | PydanticAI (model-agnostic)                                   |
| Planner         | Qwen3-8B via OpenRouter, thinking=512/1024                    |
| Worker          | Sonnet 4.6 via Anthropic                                      |
| Corpus          | PDF parser direct → Passages → Invariants                     |
| Veille          | RSS feeds + user deposits                                     |
| Market data     | Yahoo Finance prices + FRED macro + GROWTH_COMPOSITE + GLOBAL_LIQUIDITY |
| Backfill        | 25y macro; ETFs from inception                                |
| Risk-free rate  | 3-Month T-Bill (^IRX) — USD                                   |
| Currency        | USD for all indicators; CHFUSD=X for display only             |
| Ingestion       | Telegram bot + SCP → inbox/ (nightly job 02:00)               |
| Notification    | Telegram weekly digest (Mon 09:30) + Proposal alerts          |
| Timezone        | Europe/Zurich (APScheduler)                                   |
| Deployment      | systemd service on Hetzner CAX21 ARM                          |

**Out of scope (see IMPROVEMENTS.md):** I-0 through I-26.

---

## Phase 0 — VM Installation (Hetzner CAX21 ARM)
*Estimated: 0.5 day*

### Task 0.1 — System prerequisites

```bash
ssh root@<hetzner-ip>
apt update && apt upgrade -y
apt install -y \
  python3.12 python3.12-venv python3.12-dev \
  build-essential git curl wget tmux

curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

ollama list
ollama pull nomic-embed-text  # if missing

# GitHub CLI
apt install gh -y
gh auth login

git config --global user.email "jp@..."
git config --global user.name "JP"
git clone https://github.com/jp/investment-agent.git /opt/investment-agent
```

**Done when:** python3.12, uv, ollama (with nomic-embed-text), gh, tmux OK.

---

### Task 0.2 — Project directories

```bash
mkdir -p /data/investment/{inbox,sources/corpus,sources/kindle,arcade_db,backups,logs}
chown -R ubuntu:ubuntu /opt/investment-agent /data/investment
```

---

### Task 0.3 — Environment variables

```bash
cat > /opt/investment-agent/.env << 'EOF'
# LLMs
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
PLANNER_MODEL=qwen/qwen3-8b
PLANNER_THINKING_BUDGET_PRE=512
PLANNER_THINKING_BUDGET_POST=1024
WORKER_MODEL=claude-sonnet-4-6
OLLAMA_BASE_URL=http://localhost:11434

# ArcadeDB
ARCADE_DB_PATH=/data/investment/arcade_db/investment.db

# Scheduling
TZ=Europe/Zurich

# Ingestion
INBOX_PATH=/data/investment/inbox
SOURCES_PATH=/data/investment/sources/corpus

# Market data
MARKET_BACKFILL_YEARS=25
YAHOO_FINANCE_TICKERS=TIP,TLT,GLD,DJP,SPY,VTI,QQQ,EFA,EEM,IEF,SHY,BIL,DBC,CHFUSD=X,^IRX,^VIX
FRED_SERIES=CPIAUCSL,T10Y2Y,UMCSENT,UNRATE,INDPRO
GROWTH_COMPOSITE_COMPONENTS=INDPRO,UNRATE
GLOBAL_LIQUIDITY_COMPONENTS=M2SL,WALCL,ECBASSETSW,JPNASSETS

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Veille
RSS_FEEDS_TIER1=https://feeds.bloomberg.com/markets/news.rss,...

# User profile defaults (BINDING rules — see REVISION_NOTES.md)
USER_CURRENCY=CHF
USER_MAX_DRAWDOWN_PCT=-15
USER_MAX_SINGLE_ASSET_PCT=40
USER_BENCHMARK=60/40-USD
USER_PHASE=accumulation
USER_AUTO_VALIDATION_HOURS=48
EOF
chmod 600 /opt/investment-agent/.env
```

Notes: VIX comes from Yahoo `^VIX` only (VIXCLS dropped — single source).
`ECBASSETSW` and `JPNASSETS` are the FRED ids for ECB and BoJ total assets.

---

### Task 0.4 — Python setup

```bash
cd /opt/investment-agent
uv init --package investment-agent   # src layout
cd investment-agent

uv add arcadedb-embedded pydantic-ai anthropic openai \
       apscheduler pydantic pydantic-settings python-dotenv \
       python-telegram-bot python-ulid

uv add yfinance pandas-datareader pandas numpy scipy \
       pypdf aiofiles aiohttp feedparser

uv add --dev pytest pytest-asyncio httpx

uv run python -c "import arcadedb_embedded; print('ArcadeDB OK')"
```

---

### Task 0.5 — Project structure

Everything lives under `src/` (uv package layout). Entry points are modules,
not root scripts — no path ambiguity.

```
/opt/investment-agent/investment-agent/
├── pyproject.toml
├── src/
│   ├── metis/
│   │   └── base/
│   │       ├── embedding.py
│   │       ├── llm.py
│   │       ├── llm_clients.py
│   │       ├── llm_factory.py
│   │       └── tool_wrapper.py
│   └── investment/
│       ├── main.py               ← APScheduler entry: python -m investment.main
│       ├── seed.py               ← UC0 CLI entry:     python -m investment.seed
│       ├── config.py             ← pydantic-settings, ALL env vars typed here
│       ├── models/
│       │   ├── entities.py       ← Pydantic: Framework, RegimeType, Regime,
│       │   │                       Invariant, Strategy, Scenario, Evaluation,
│       │   │                       Backtest, Adaptation, Proposal, Portfolio,
│       │   │                       Document, Passage, EventLog
│       │   ├── command.py        ← PlannerContext, QueryStrategies
│       │   └── result.py         ← WorkerResult, ReallocationProposal,
│       │                           ImprovementProposal, PostPlannerResult
│       ├── db/
│       │   ├── arcade.py
│       │   ├── schema.py
│       │   ├── seed_data.py      ← seed constants (this file's Phase 1ter)
│       │   └── queries.py
│       ├── planner/
│       │   ├── pre.py
│       │   └── post.py
│       ├── worker/
│       │   ├── worker.py
│       │   ├── tools.py
│       │   └── skills/
│       │       ├── skill-evaluate-strategy.md
│       │       ├── skill-rank-portfolios.md
│       │       ├── skill-compare-vs-defender.md
│       │       ├── skill-propose-reallocation.md
│       │       └── skill-interpret-invariants.md
│       ├── writeback/
│       │   └── writeback.py      ← gates + persistence executor
│       ├── corpus/
│       │   └── ingester.py       ← single pipeline (nightly job AND UC0 seed)
│       ├── market/
│       │   ├── fetcher.py
│       │   ├── derivatives.py    ← level/speed/acceleration + transforms
│       │   ├── growth.py         ← GROWTH_COMPOSITE
│       │   ├── liquidity.py      ← GLOBAL_LIQUIDITY composite
│       │   └── regime.py
│       ├── mechanical/
│       │   ├── ratios.py
│       │   ├── scenarios.py
│       │   ├── invariants.py     ← weights + confrontation rule
│       │   ├── backtests.py
│       │   ├── snapshots.py      ← portfolio_weekly_snapshot writer
│       │   ├── replay.py         ← Phase 9 shadow replay (go-live gate)
│       │   └── learning.py       ← V2 only (stub)
│       ├── veille/
│       │   └── rss.py            ← UC3
│       └── telegram/
│           ├── digest.py         ← weekly digest renderer
│           └── bot.py            ← UC9 chat + proposal/innovation callbacks
tests/
```

---

### Task 0.6 — systemd service

```bash
cat > /etc/systemd/system/investment-agent.service << 'EOF'
[Unit]
Description=Investment Agent
After=network.target ollama.service
Requires=ollama.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/investment-agent/investment-agent
EnvironmentFile=/opt/investment-agent/.env
ExecStart=/opt/investment-agent/investment-agent/.venv/bin/python -m investment.main
Restart=on-failure
RestartSec=10
StandardOutput=append:/data/investment/logs/agent.log
StandardError=append:/data/investment/logs/agent.error.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable investment-agent
# Start after UC0 seed completes
```

---

### Task 0.7 — Laptop SCP aliases

```bash
VPS_IP="<hetzner-ip>"
VPS_USER="ubuntu"
VPS_KEY="~/.ssh/hetzner"
VPS_INBOX="/data/investment/inbox"

alias feed-pdf='f() { scp -i $VPS_KEY "$1" $VPS_USER@$VPS_IP:$VPS_INBOX/; }; f'
alias feed-url='f() { ssh -i $VPS_KEY $VPS_USER@$VPS_IP "echo $1 > $VPS_INBOX/$(date +%s).url"; }; f'
```

---

## Phase 1 — ArcadeDB Schema
*Estimated: 1 day*

### Task 1.1 — Schema creation

**`src/investment/db/schema.py`**

```python
SCHEMA_SQL = """
-- VERTEX TYPES (14)
CREATE VERTEX TYPE Framework  IF NOT EXISTS;
CREATE VERTEX TYPE RegimeType IF NOT EXISTS;
CREATE VERTEX TYPE Regime     IF NOT EXISTS;
CREATE VERTEX TYPE Invariant  IF NOT EXISTS;
CREATE VERTEX TYPE Strategy   IF NOT EXISTS;
CREATE VERTEX TYPE Scenario   IF NOT EXISTS;
CREATE VERTEX TYPE Evaluation IF NOT EXISTS;
CREATE VERTEX TYPE Backtest   IF NOT EXISTS;
CREATE VERTEX TYPE Adaptation IF NOT EXISTS;   -- V2 only, reserved
CREATE VERTEX TYPE Proposal   IF NOT EXISTS;   -- V1 paper-mode (switch|reallocation)
CREATE VERTEX TYPE Portfolio  IF NOT EXISTS;
CREATE VERTEX TYPE Document   IF NOT EXISTS;
CREATE VERTEX TYPE Passage    IF NOT EXISTS;
CREATE VERTEX TYPE EventLog   IF NOT EXISTS;   -- append-only audit log, no edges

-- EDGE TYPES (11)
CREATE EDGE TYPE UPDATES       IF NOT EXISTS;
CREATE EDGE TYPE FAVORS        IF NOT EXISTS;  -- RegimeType → Strategy
CREATE EDGE TYPE HAS_SCENARIO  IF NOT EXISTS;
CREATE EDGE TYPE BACKED_BY     IF NOT EXISTS;
CREATE EDGE TYPE TESTED_IN     IF NOT EXISTS;
CREATE EDGE TYPE IN_REGIME     IF NOT EXISTS;  -- Backtest → Regime instance
CREATE EDGE TYPE MODIFIES      IF NOT EXISTS;  -- V2 only
CREATE EDGE TYPE HOLDS         IF NOT EXISTS;  -- Portfolio → Strategy (primary BOOLEAN)
CREATE EDGE TYPE DESIGNED_FOR  IF NOT EXISTS;  -- Portfolio → RegimeType (nullable)
CREATE EDGE TYPE CONTAINS      IF NOT EXISTS;
CREATE EDGE TYPE SUPPORTS      IF NOT EXISTS;

-- DOCUMENT TYPES (former "SQL tables" — see DATA_MODELS.md for columns)
CREATE DOCUMENT TYPE user_profile             IF NOT EXISTS;
CREATE DOCUMENT TYPE invariant_author_config  IF NOT EXISTS;
CREATE DOCUMENT TYPE allowed_tickers          IF NOT EXISTS;
CREATE DOCUMENT TYPE system_thresholds        IF NOT EXISTS;
CREATE DOCUMENT TYPE schema_extensions        IF NOT EXISTS;
CREATE DOCUMENT TYPE strategy_performance     IF NOT EXISTS;
CREATE DOCUMENT TYPE invariant_weights        IF NOT EXISTS;
CREATE DOCUMENT TYPE regime_history           IF NOT EXISTS;
CREATE DOCUMENT TYPE invariant_confrontations IF NOT EXISTS;
CREATE DOCUMENT TYPE portfolio_weekly_snapshot IF NOT EXISTS;
CREATE DOCUMENT TYPE scenario_calibration     IF NOT EXISTS;  -- outcomes.py
CREATE DOCUMENT TYPE replay_report            IF NOT EXISTS;  -- Phase 9

-- INDEXES
CREATE INDEX ON Framework (enabled)           IF NOT EXISTS;
CREATE INDEX ON Regime (is_current)           IF NOT EXISTS;
CREATE INDEX ON Invariant (status)            IF NOT EXISTS;
CREATE INDEX ON Strategy (status)             IF NOT EXISTS;
CREATE INDEX ON Strategy (enabled)            IF NOT EXISTS;
CREATE INDEX ON Portfolio (defender)          IF NOT EXISTS;
CREATE INDEX ON Portfolio (enabled)           IF NOT EXISTS;
CREATE INDEX ON Proposal (date)               IF NOT EXISTS;
CREATE INDEX ON Proposal (user_response)      IF NOT EXISTS;
CREATE INDEX ON EventLog (type)               IF NOT EXISTS;
CREATE INDEX ON EventLog (ts)                 IF NOT EXISTS;
CREATE INDEX ON EventLog (event_date)         IF NOT EXISTS;
CREATE INDEX ON portfolio_weekly_snapshot (date) IF NOT EXISTS;

-- TIME-SERIES (3) — correct ArcadeDB syntax: TIMESTAMP + TAGS + FIELDS
CREATE TIMESERIES TYPE MarketData IF NOT EXISTS
  TIMESTAMP ts
  TAGS   (ticker STRING, asset_class STRING, currency STRING)
  FIELDS (level DOUBLE, speed DOUBLE, acceleration DOUBLE);
CREATE TIMESERIES TYPE ScenarioProbability IF NOT EXISTS
  TIMESTAMP ts
  TAGS   (strategy_id STRING, scenario STRING)
  FIELDS (probability DOUBLE, shift_d7 DOUBLE);
CREATE TIMESERIES TYPE PortfolioNAV IF NOT EXISTS
  TIMESTAMP ts
  TAGS   (portfolio_id STRING, currency STRING)
  FIELDS (nav DOUBLE, daily_return DOUBLE,
          sharpe_rolling DOUBLE, sortino_rolling DOUBLE,
          calmar_rolling DOUBLE, drawdown DOUBLE, vs_benchmark DOUBLE);

-- DOWNSAMPLING POLICIES
ALTER TIMESERIES TYPE MarketData ADD DOWNSAMPLING POLICY
  AFTER 30 DAYS GRANULARITY 1 DAYS
  AFTER 365 DAYS GRANULARITY 1 WEEKS;
ALTER TIMESERIES TYPE ScenarioProbability ADD DOWNSAMPLING POLICY
  AFTER 7 DAYS  GRANULARITY 1 DAYS
  AFTER 30 DAYS GRANULARITY 1 WEEKS;
ALTER TIMESERIES TYPE PortfolioNAV ADD DOWNSAMPLING POLICY
  AFTER 90 DAYS GRANULARITY 1 WEEKS;
"""

# VECTOR INDEXES — the audit log is a vertex (EventLog), NOT a TS, because
# TS FIELDS are numeric only.
# ⚠️ Do not guess vector-index SQL. Create the two HNSW indexes (Passage.embedding,
# Invariant.embedding, 768 dims, cosine) via the API exposed by the installed
# arcadedb-embedded version (Java: buildTypeIndex(...).withLSMVectorType()
# .withDimensions(768); the Python bindings expose the equivalent — check
# bindings/python examples 03_vector_search.py). Queries then use:
#   SELECT expand(`vector.neighbors`('Passage[embedding]', :vec, 20))

# FULL-TEXT INDEXES (persistence routing "FTS" step):
#   Passage.content, Invariant (title + description).
# ⚠️ Same caution as vector indexes: verify the FTS index DDL/API against the
# installed arcadedb-embedded version before use; do not guess syntax.
```

**Done when:** schema created without error; 14 vertex + 11 edge + 3 TS +
10 document types present; both vector indexes queryable via `vector.neighbors`.

---

### Task 1.2 — ArcadeDB client wrapper

**`src/investment/db/arcade.py`**

```python
import arcadedb_embedded as arcadedb
from ulid import ULID

TRACE_EXEMPT = {"Passage", "RegimeType", "EventLog"}

class InvestmentDB:
    """ArcadeDB wrapper — agent sole writer, asyncio sequential.
    ALL writes run inside explicit transactions (db.transaction())."""

    def __init__(self, db_path: str):
        self._db = arcadedb.create_database(db_path)  # or open_database if exists
        self._db.__enter__()

    async def query(self, lang: str, stmt: str, **params) -> list[dict]: ...
    async def command(self, lang: str, stmt: str, **params) -> None:
        # wraps in transaction
        ...
    async def create_vertex(self, type: str, props: dict) -> str:
        if type not in TRACE_EXEMPT and not props.get("trace"):
            raise ValueError(f"trace mandatory for {type}")
        ...
    async def create_edge(self, type: str, from_id: str, to_id: str,
                          props: dict = {}) -> None: ...
    async def upsert_vertex(self, type: str, id: str, props: dict) -> str: ...
    async def append_event(self, type: str, source_uc: str,
                           source_id: str, payload: dict,
                           event_date: date | None = None) -> str:
        """EventLog append — MUST be called before the related vertex/edge
        commit, in the same serialized write path. id = MONOTONIC ULID
        (ulid.monotonic) — the canonical order key; ts is informational;
        event_date = domain date (defaults to today; pass the historical
        date for backfilled/retrospective events). See DATA_MODELS.md
        'Ordering semantics'."""
    async def append_ts(self, type: str, ts: datetime, tags: dict,
                        fields: dict) -> None: ...
    async def query_ts(self, type: str, where: str, limit: int) -> list[dict]: ...

    def close(self):
        self._db.__exit__(None, None, None)
```

---

### Task 1.3 — Seed reference data (document types)

**`src/investment/db/seed_data.py` — reference portion**

```python
SYSTEM_THRESHOLDS = {
    # ranking + proposal gates
    "rolling_window_days": 756.0,        # 36M window for ALL *_rolling indicators
    "ranking_tiebreak_window": 0.02,
    "proposal_sortino_gap_min": 0.02,    # switch gate
    "proposal_calmar_min": 1.5,          # switch gate (absolute threshold)
    "proposal_min_allocation_change_pts": 5.0,   # switch gate 5 + realloc gate 3
    "proposal_max_turnover_pct": 30.0,   # realloc gate 4: Σ|delta|/2
    "proposal_expiry_days": 14.0,        # pending → expired
    "proposal_outcome_weeks": 12.0,      # maturation before outcome verdict
    "proposal_cooldown_weeks": 4.0,      # anti-repetition after user rejection
    "proposal_invariant_weight_min": 0.10,  # realloc gate 6: cited-invariant floor
    "strategy_probation_weeks": 12.0,    # new/revised strategy probation window
    "scenario_calibration_weeks": 4.0,   # scenario probability scoring horizon
    # invariants
    "recency_half_life_days": 365.0,
    "confrontation_margin": 0.10,        # FAVORS-vs-median infirmation margin
    "vector_similarity_min": 0.35,
    # regime detection (see ARCHITECTURE formal algorithm)
    "regime_cpi_stagflation": 2.5,
    "regime_cpi_noise": 0.05,
    "regime_cpi_deflation": 0.0,
    "regime_cpi_speed_scale": 0.3,
    "regime_growth_noise": 0.15,
    "regime_growth_speed_scale": 1.0,
    "regime_vix_stress": 25.0,
    "regime_confirm_prints": 2.0,        # hysteresis: consecutive monthly
                                         #   observations per axis (both axes
                                         #   are monthly series — days would
                                         #   be trivially satisfied)
    # scenarios / misc
    "scenario_shift_trigger": 10.0,
    "min_backtest_periods": 3.0,
    "auto_validation_hours": 48.0,
    "derivative_lookback_short": 30.0,
    # shadow replay (Phase 9 — go-live gate)
    "replay_cost_bps": 10.0,             # per side, applied to turnover
    "replay_confirmation_weeks": 2.0,    # acceptance policy in the replay
}

INVARIANT_AUTHOR_CONFIG = [
    {"author": "dalio",  "floor_weight": 0.40,
     "initial_weight_min": 0.80, "initial_weight_max": 0.90},
    {"author": "marks",  "floor_weight": 0.35,
     "initial_weight_min": 0.75, "initial_weight_max": 0.85},
    {"author": "other",  "floor_weight": 0.20,                 # sentinel for
     "initial_weight_min": 0.40, "initial_weight_max": 0.70},  # Invariant.author=null
    {"author": "system", "floor_weight": 0.05,
     "initial_weight_min": 0.15, "initial_weight_max": 0.25},  # agent-discovery
]

ALLOWED_TICKERS = [
    # Yahoo ETFs / indices (transform 'none' = adjusted close in `level`)
    {"ticker": "TIP",  "asset_class": "US_TIPS",          "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "TLT",  "asset_class": "US_LONG_TREASURY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "IEF",  "asset_class": "US_TREASURY_7_10", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "GLD",  "asset_class": "GOLD",             "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "DJP",  "asset_class": "COMMODITIES",      "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "SPY",  "asset_class": "US_EQUITY",        "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "VTI",  "asset_class": "US_EQUITY",        "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "QQQ",  "asset_class": "US_EQUITY",        "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "EFA",  "asset_class": "INTL_EQUITY",      "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "EEM",  "asset_class": "EM_EQUITY",        "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "SHY",  "asset_class": "US_TREASURY_1_3",  "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "BIL",  "asset_class": "US_TBILL",         "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "DBC",  "asset_class": "COMMODITIES",      "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "^IRX", "asset_class": "RISK_FREE",        "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "^VIX", "asset_class": "VOLATILITY",       "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "CHFUSD=X", "asset_class": "FX",           "currency": "USD", "source": "yahoo", "transform": "none"},
    # FRED macro (transforms per DATA_MODELS.md "MarketData semantics")
    {"ticker": "CPIAUCSL", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "yoy_pct"},
    {"ticker": "T10Y2Y",   "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none"},
    {"ticker": "UNRATE",   "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none"},
    {"ticker": "INDPRO",   "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "yoy_pct"},
    {"ticker": "UMCSENT",  "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none"},
    # Composites (computed in Python — see market/growth.py, market/liquidity.py)
    {"ticker": "GROWTH_COMPOSITE", "asset_class": "MACRO",            "currency": "USD", "source": "composite", "transform": "composite"},
    {"ticker": "GLOBAL_LIQUIDITY", "asset_class": "GLOBAL_LIQUIDITY", "currency": "USD", "source": "composite", "transform": "composite"},
]
# Macro/composite tickers are exposed to the Worker's market_fetch but are
# NEVER valid allocation assets (Writeback realloc gate 5 checks asset_class).
```

---

## Phase 1bis — LLM Abstraction
*Estimated: 0.5 day*

### Task 1bis.1 — `src/metis/base/llm.py`

```python
class LLMToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

class LLMResponse(BaseModel):
    content: Optional[str]
    tool_calls: list[LLMToolCall]
    stop_reason: str         # "end_turn" | "tool_use"
    thinking: Optional[str]

class BaseLLMClient(ABC):
    @abstractmethod
    async def complete(self, messages, system=None, tools=None,
                       tool_choice="auto", thinking_budget=None) -> LLMResponse: ...
```

### Task 1bis.2 — `llm_clients.py`

- `AnthropicClient` — Anthropic SDK, native tool use, maps to `LLMResponse`.
- `OpenAICompatibleClient` — OpenRouter chat completions; thinking via the
  provider's reasoning parameter; tool_choice forced for Calls 1a/1b/2.
- **Robustness (small-model reality):** every structured output is validated
  with Pydantic; on validation error retry once with the error message
  appended; on second failure raise `PlannerOutputError` (chain aborts with
  ErrorEvent — never silently continue).

### Task 1bis.3 — `llm_factory.py` + `embedding.py`

- Factory reads `.env` (`PLANNER_MODEL`, `WORKER_MODEL`); business code only
  sees `BaseLLMClient`.
- `OllamaEmbeddingService.encode(text) -> list[float]` — nomic-embed-text,
  768 dims, HTTP to `OLLAMA_BASE_URL`. Invariant embedding input =
  `title + "\n" + description`.

**Done when:** a round-trip tool_use test passes against both providers
(recorded fixtures for CI), and a 768-dim embedding is returned.

---

## Phase 1ter — UC0 Seed (graph + corpus + first snapshot)
*Estimated: 1.5 days*

Run: `uv run python -m investment.seed [--no-curate]` — idempotent (UPSERT on
all vertices and edges). Full step list in USE_CASES.md UC0 (14 steps,
including **historical Regime materialization** — step 10 — and the
**initial curation pass** — step 4b, DEFAULT when a corpus is present, the
only LLM step in UC0: extracts author-tiered invariant candidates from the
deposited books/articles for batch validation).

### Task 1ter.1 — Framework seed

```python
FRAMEWORKS = [
    {"id": "4seasons", "name": "Ray Dalio 4 Seasons",
     "description": "Growth × inflation matrix",
     "enabled": True, "accuracy": None,
     "trace": "Primary framework for V1 — see Dalio Principles."},
    # Optional metadata-only (enabled=false):
    {"id": "permanent",        "name": "Browne Permanent",
     "enabled": False, "accuracy": None,
     "trace": "Reference framework; not yet active in V1."},
    {"id": "liquidity-cycle",  "name": "Global Liquidity Cycle",
     "enabled": False, "accuracy": None,
     "trace": "Reference framework; not yet active in V1."},
]
```

### Task 1ter.2 — RegimeType seed (5 types, seeded once, never mutated)

```python
# `description` carries the narrative (RegimeType is TRACE_EXEMPT).
# Growth axis = GROWTH_COMPOSITE (not PMI — see IMPROVEMENTS I-20 resolution).
REGIME_TYPES = [
    {"id": "rising-growth-falling-inflation", "name": "Goldilocks",
     "framework_id": "4seasons", "aliases": [],
     "description": "Growth composite rising and CPI YoY decelerating — goldilocks."},
    {"id": "rising-growth-rising-inflation",  "name": "Overheating",
     "framework_id": "4seasons", "aliases": ["overheating"],
     "description": "Growth composite rising with CPI YoY accelerating — late cycle."},
    {"id": "falling-growth-rising-inflation", "name": "Stagflation",
     "framework_id": "4seasons", "aliases": ["stagflation"],
     "description": "Growth composite falling with CPI YoY > 2.5 and accelerating."},
    {"id": "falling-growth-falling-inflation","name": "Disinflation/Recession",
     "framework_id": "4seasons", "aliases": [],
     "description": "Growth composite falling and CPI YoY decelerating; deflation may layer as tag."},
    {"id": "uncertain",                        "name": "Uncertain",
     "framework_id": "4seasons", "aliases": [],
     "description": "Contradictory or straddled indicators (any flat axis)."},
]
```

### Task 1ter.3 — Invariant seed (6 minimum, status=integrated)

`regime:*` tags use RegimeType ids — they drive the mechanical confrontation
rule (ARCHITECTURE).

```python
INVARIANTS = [
    {"id": "inv-inflation-persistence-tips",
     "title": "Persistent inflation favors TIPS, commodities, and gold",
     "description": "When CPI YoY > 2.5% and speed > 0, real yields fall and "
                    "TIPS/gold/commodities outperform nominal bonds.",
     "example": "2021-2022: TIP +2.3% while TLT -26%.",
     "source": "Dalio — Principles for Navigating Big Debt Crises, ch. inflation",
     "author": "dalio", "status": "integrated",
     "topic": ["tips", "inflation", "gold"],
     "tags": ["asset:TIP", "asset:GLD", "indicator:real-yield",
              "regime:falling-growth-rising-inflation",
              "regime:rising-growth-rising-inflation"],
     "weight_initial": 0.85, "floor_weight": 0.40,
     "trace": "Dalio Principles; chapter on inflation hedges."},
    {"id": "inv-falling-growth-duration",
     "title": "Falling growth favors duration and cash-like defense",
     "description": "Contracting growth with rate-cut expectations supports long "
                    "duration (TLT) and cash equivalents (BIL).",
     "example": "2008 H2, 2019 H2: TLT strongly positive as growth rolled over.",
     "source": "Dalio — Principles for Navigating Big Debt Crises, ch. recession",
     "author": "dalio", "status": "integrated",
     "topic": ["duration", "recession"],
     "tags": ["asset:TLT", "asset:BIL",
              "regime:falling-growth-falling-inflation"],
     "weight_initial": 0.80, "floor_weight": 0.40,
     "trace": "Dalio Principles; recession playbook."},
    {"id": "inv-rising-growth-equities",
     "title": "Rising growth favors equity exposure",
     "description": "Expanding growth with positive earnings revisions supports "
                    "broad equity beta (SPY/VTI).",
     "example": "2016-2018, 2023-2024 expansions.",
     "source": "Standard cycle finance; multi-decade empirical regularity",
     "author": "dalio", "status": "integrated",
     "topic": ["equities", "growth"],
     "tags": ["asset:SPY", "asset:VTI",
              "regime:rising-growth-falling-inflation",
              "regime:rising-growth-rising-inflation"],
     "weight_initial": 0.80, "floor_weight": 0.40,
     "trace": "Standard cycle finance."},
    {"id": "inv-liquidity-tightening-risk",
     "title": "Tightening global liquidity pressures risk assets",
     "description": "GLOBAL_LIQUIDITY level < 100 with speed < 0 historically "
                    "compresses risk-asset multiples.",
     "example": "2018 QT, 2022 tightening.",
     "source": "Howard Marks — memos on cycles and liquidity (multiple, 2008-2023)",
     "author": "marks", "status": "integrated",
     "topic": ["liquidity", "risk"],
     "tags": ["indicator:global-liquidity"],
     "weight_initial": 0.75, "floor_weight": 0.35,
     "trace": "Howard Marks memos on cycles and liquidity."},
    {"id": "inv-liquidity-easing-risk",
     "title": "Easing global liquidity supports risk assets",
     "description": "GLOBAL_LIQUIDITY speed > 0 historically expands risk-asset "
                    "multiples.",
     "example": "2020-2021 QE.",
     "source": "Howard Marks — memos on cycles and liquidity (multiple, 2008-2023)",
     "author": "marks", "status": "integrated",
     "topic": ["liquidity", "risk"],
     "tags": ["indicator:global-liquidity"],
     "weight_initial": 0.75, "floor_weight": 0.35,
     "trace": "Howard Marks memos on cycles and liquidity."},
    {"id": "inv-diversification-drawdown",
     "title": "Diversification lowers drawdown but dilutes upside",
     "description": "Cross-asset diversification reduces max_drawdown at the "
                    "cost of upside capture in single-regime bull runs.",
     "example": "2008: 60/40 -30% vs All Weather ~-12%.",
     "source": "Dalio — All Weather framework documentation",
     "author": "dalio", "status": "integrated",
     "topic": ["diversification", "drawdown"],
     "tags": ["indicator:max_drawdown", "phase:accumulation"],
     "weight_initial": 0.70, "floor_weight": 0.40,
     "trace": "Dalio Principles; All Weather chapter."},
]
```

### Task 1ter.4 — Strategy seed (4 strategies, all enabled)

Strategy ids never collide with Framework ids. `framework_id` = evaluation
lens (single active framework in V1), not intellectual origin. Every
`conditions` indicator is computable from MarketData/Regime, with ≥1
orthogonal to the regime definition.

```python
STRATEGIES = [
    {"id": "four-seasons-rp",
     "title": "4 Seasons Dalio Risk Parity",
     "description": "Risk-parity baseline allocating across stocks, long bonds, "
                    "TIPS, gold and commodities to perform in every quadrant.",
     "regime_type_id": None, "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 65,
     "conditions": "applicable to all regimes; orthogonal: ^VIX level < 30",
     "trace": "Risk parity baseline."},
    {"id": "permanent-browne",
     "title": "Permanent Portfolio Browne",
     "description": "Browne 25/25/25/25 across stocks, long bonds, gold and cash; "
                    "simplicity baseline with low historical drawdown.",
     "regime_type_id": None, "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 55,
     "conditions": "regime = uncertain OR regime confidence < 60; "
                   "orthogonal: ^VIX level > 20",
     "trace": "Simplicity baseline; low historical drawdown."},
    {"id": "barbell-taleb",
     "title": "Barbell Taleb",
     "description": "~85% safety (short/intermediate Treasuries, split across "
                    "SHY/BIL/IEF to respect the 40% single-asset cap) + ~15% "
                    "convexity (equity sleeve) to capture upside while bounding downside.",
     "regime_type_id": None, "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 45,
     "conditions": "orthogonal: ^VIX level > 25 (tail risk elevated)",
     "trace": "85% safety + 15% convexity."},
    {"id": "momentum-macro",
     "title": "Momentum Macro",
     "description": "Dynamic rotation by detected regime; tilts toward the "
                    "asset class with strongest current macro momentum.",
     "regime_type_id": None, "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 50,
     "conditions": "regime stable >= 60 days; orthogonal: SPY 90d return > 0",
     "trace": "Dynamic rotation by detected regime."},
]

BACKED_BY_EDGES = [
    ("four-seasons-rp",  "inv-diversification-drawdown"),
    ("four-seasons-rp",  "inv-inflation-persistence-tips"),
    ("permanent-browne", "inv-diversification-drawdown"),
    ("barbell-taleb",    "inv-falling-growth-duration"),
    ("momentum-macro",   "inv-rising-growth-equities"),
    ("momentum-macro",   "inv-liquidity-easing-risk"),
]
```

### Task 1ter.5 — Scenario seed (3 per Strategy = 12)

```python
SCENARIOS = [
    # four-seasons-rp (strategy_id used only to build the HAS_SCENARIO edge)
    {"id": "sc-4s-bull", "strategy_id": "four-seasons-rp", "name": "bull",
     "probability": 35, "probability_d7": 35,
     "triggers": ["CPI_YOY < 2.5", "GROWTH_COMPOSITE > 102", "Fed dovish"],
     "target_allocation": {"SPY": 35, "TLT": 25, "GLD": 15, "TIP": 15, "DJP": 5, "cash": 5},
     "currency": "USD", "trace": "Goldilocks scenario for 4 Seasons."},
    {"id": "sc-4s-base", "strategy_id": "four-seasons-rp", "name": "base",
     "probability": 45, "probability_d7": 45,
     "triggers": ["CPI_YOY 2.5-3.5", "Fed pause"],
     "target_allocation": {"SPY": 30, "TLT": 30, "GLD": 10, "TIP": 20, "DJP": 7.5, "cash": 2.5},
     "currency": "USD", "trace": "Base case for 4 Seasons."},
    {"id": "sc-4s-bear", "strategy_id": "four-seasons-rp", "name": "bear",
     "probability": 20, "probability_d7": 20,
     "triggers": ["^VIX > 25", "CPI_YOY > 4 AND GROWTH_COMPOSITE < 98"],
     "target_allocation": {"TIP": 30, "GLD": 25, "DJP": 15, "SPY": 10, "TLT": 10, "cash": 10},
     "currency": "USD", "trace": "Stagflation/stress scenario."},
    # ... 9 more for permanent-browne, barbell-taleb, momentum-macro
]
# Numeric triggers follow the grammar "<TICKER|ALIAS> <op> <number>" and are
# evaluated by the daily 06:45 job; free-text triggers ("Fed dovish") are
# Worker-interpreted weekly (IMPROVEMENTS I-22).
```

### Task 1ter.6 — Portfolio seed (7 portfolios, exactly one defender=true)

**All allocations comply with the BINDING user caps (max single asset 40%,
max drawdown -15%). Per-portfolio rules may only be stricter.**

```python
PORTFOLIOS = [
    {"id": "4s-balanced-defender",
     "name": "4 Seasons Balanced Defender",
     "framework_id": "4seasons", "defender": True, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"TIP": 20, "TLT": 30, "GLD": 10, "DJP": 7.5, "SPY": 30, "cash": 2.5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 97.5,
     "trace": "Initial defender — standard 4 Seasons balanced."},
    {"id": "4s-stagflation-defensive",
     "name": "4 Seasons Stagflation Defensive",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"TIP": 30, "GLD": 25, "DJP": 15, "SPY": 10, "TLT": 10, "cash": 10},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 97.5,
     "trace": "Designed for falling-growth-rising-inflation."},
    {"id": "4s-rising-growth-equities",
     "name": "4 Seasons Rising-Growth Equity Tilt",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"SPY": 40, "EFA": 10, "TLT": 15, "GLD": 10, "TIP": 15, "DJP": 5, "cash": 5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 95.0,
     "trace": "Designed for rising-growth quadrants. SPY capped at the "
              "binding 40% user rule; EFA adds intl diversification."},
    {"id": "4s-falling-growth-defensive",
     "name": "4 Seasons Falling-Growth Defensive",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"TLT": 40, "IEF": 20, "GLD": 15, "SPY": 15, "cash": 10},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 95.0,
     "trace": "Designed for falling-growth-falling-inflation."},
    {"id": "permanent-balanced",
     "name": "Permanent Portfolio Balanced",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"SPY": 25, "TLT": 25, "GLD": 25, "cash": 25},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 30.0,   # stricter — OK
     "phase": "accumulation", "fx_usd_exposure": 75.0,
     "trace": "Browne 25/25/25/25; framework-neutral."},
    {"id": "barbell-defensive",
     "name": "Barbell Taleb Defensive",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"SHY": 35, "BIL": 30, "IEF": 20, "SPY": 15},
     "max_drawdown_rule": -10.0,          # stricter than user rule — OK
     "max_single_asset_pct": 40.0,        # was 70 — now complies with binding cap
     "phase": "accumulation", "fx_usd_exposure": 100.0,
     "trace": "85% safety split across SHY/BIL/IEF (binding 40% cap) + 15% convex."},
    {"id": "momentum-macro-rotation",
     "name": "Momentum Macro Rotation",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"SPY": 40, "TLT": 30, "GLD": 15, "DJP": 10, "cash": 5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 100.0,
     "trace": "Dynamic; current allocation reflects last regime."},
]

HOLDS_EDGES = [
    ("4s-balanced-defender",       "four-seasons-rp",  True),
    ("4s-stagflation-defensive",   "four-seasons-rp",  True),
    ("4s-rising-growth-equities",  "four-seasons-rp",  True),
    ("4s-falling-growth-defensive","four-seasons-rp",  True),
    ("permanent-balanced",         "permanent-browne", True),
    ("barbell-defensive",          "barbell-taleb",    True),
    ("momentum-macro-rotation",    "momentum-macro",   True),
]

DESIGNED_FOR_EDGES = [
    ("4s-stagflation-defensive",   "falling-growth-rising-inflation",  "Designed for stagflation regime."),
    ("4s-rising-growth-equities",  "rising-growth-rising-inflation",   "Designed for rising-growth quadrants."),
    ("4s-rising-growth-equities",  "rising-growth-falling-inflation",  "Designed for rising-growth quadrants."),
    ("4s-falling-growth-defensive","falling-growth-falling-inflation", "Designed for disinflation/recession."),
    # 4s-balanced-defender, permanent-balanced, barbell-defensive,
    # momentum-macro-rotation: no DESIGNED_FOR edge (framework-neutral or dynamic).
]
```

### Task 1ter.7 — Time-series, historical regimes, and snapshot bootstrap

```python
# 1. MarketData TS backfill: 25y for FRED/composites; ETFs from inception
#    (SPY 1993, GLD/TLT/TIP 2002-04, DJP 2006, BIL 2007). Apply per-series
#    transforms (DATA_MODELS.md), compute level/speed/acceleration.
#    GROWTH_COMPOSITE (market/growth.py) and GLOBAL_LIQUIDITY
#    (market/liquidity.py) computed over the full history.
#
# 2. HISTORICAL REGIME MATERIALIZATION: run RegimeDetector over the full
#    25y macro backfill. One Regime vertex per episode (is_current=false,
#    end_date set, confidence from the formula, events filled from the
#    triggering rows). Fill regime_history (incl. followed_by). Set
#    is_current=true on the ongoing final instance.
#    → This is what makes IN_REGIME edges and FAVORS n_periods meaningful.
#
# 3. Backtests: for each (Strategy × RegimeType) cell with >=
#    min_backtest_periods historical instances: synthetic backtest of the
#    strategy's prescribed allocation over each instance (NAV conventions of
#    DATA_MODELS.md) → Backtest vertex + TESTED_IN + IN_REGIME edges;
#    aggregate into RegimeType -[FAVORS]-> Strategy (mean of per-instance
#    indicators, n_periods = instance count) + strategy_performance docs.
#
# 4. PortfolioNAV TS: synthetic NAV per portfolio from the date all
#    constituents exist — constant weights, MONTHLY rebalancing, cash accrues
#    at ^IRX (pinned conventions). daily_return, sharpe/sortino/calmar_rolling
#    (756d), drawdown, vs_benchmark.
#
# 5. portfolio_weekly_snapshot: one row per enabled Portfolio for the seed
#    date; rank by sortino_rolling DESC, tie-break calmar_rolling DESC then
#    max_drawdown; calmar < 1.0 demoted; market_context, returns,
#    gap_to_defender (null for defender), recommendation='maintain'.
#
# 6. SeedEvent → EventLog with full inventory and schema_version.
```

**Done when:** schema populated; historical Regime instances exist; FAVORS
edges have n_periods ≥ 1; first snapshot row visible; SeedEvent in EventLog.

---

## Phase 2 — Market Data (daily)
*Estimated: 1 day*

### Task 2.1 — Market fetcher

**`src/investment/market/fetcher.py`** — Yahoo Finance + FRED, driven by the
`allowed_tickers` documents (source + transform columns). 25y backfill + daily
incremental. `time.sleep(0.5)` between Yahoo tickers (rate limit). Retry 3×
with exponential backoff (60s base); on final failure → ErrorEvent + Telegram
alert, and the affected series keeps its last value (forward-fill ≤ 5 trading
days per the missing-data convention).

### Task 2.2 — Derivatives + composites

**`derivatives.py`**
```python
def compute_derivatives(series: pd.Series, lookback_days: int) -> pd.DataFrame:
    """level, speed (1st diff over lookback), acceleration (diff of speed).
    Monthly series (CPI, UNRATE, INDPRO, composites): lookback = 1 observation.
    Daily series: lookback = derivative_lookback_short (30d)."""

def apply_transform(series: pd.Series, transform: str) -> pd.Series:
    """'none' | 'yoy_pct' (12m percent change) | 'composite' (passthrough)."""
```

**`growth.py`** — GROWTH_COMPOSITE (the 4 Seasons growth axis; replaces ISM
PMI — automatic, free, perennial):
```python
def compute_growth_composite(indpro: pd.Series, unrate: pd.Series) -> pd.Series:
    """z(INDPRO YoY, 10y trailing) − z(Δ3m UNRATE, 10y trailing), halved,
    rebased: level = 100 + 10 × raw. >100 expansion, <100 contraction."""
```

**`liquidity.py`** — GLOBAL_LIQUIDITY:
```python
def compute_global_liquidity(components: dict[str, pd.Series]) -> pd.Series:
    """Per component: USD-convert, z-score over 5y trailing. level = 100 +
    10 × mean(z). Persisted as MarketData ticker='GLOBAL_LIQUIDITY'."""
```

### Task 2.3 — Regime detector

**`regime.py`** — implements the formal algorithm in
investment-ARCHITECTURE.md (axis classification, hysteresis
`regime_confirm_prints`, confidence formula, tag derivation, is_current
uniqueness in one transaction). Emits **RegimeEvent → EventLog BEFORE**
touching the Regime vertex, and only when regime/confidence-band/tags change.
Also exposes `materialize_history(db, start, end)` used by UC0 step 2.

**Done when:** `detector.detect(db)` creates/updates a Regime vertex with
`is_current=true`, hysteresis verified on synthetic flip-flop data, and
`materialize_history` yields ≥ 10 episodes on the 25y backfill.

---

## Phase 3 — Corpus Parser + Veille
*Estimated: 1 day*

### Task 3.1 — `corpus/ingester.py` (single pipeline: nightly job AND UC0 seed)

```python
class CorpusIngester:
    async def ingest_file(self, path: Path) -> Document:
        """Dispatch by extension: .pdf (pypdf), .txt/.md, .url (fetch + strip
        boilerplate), kindle .csv. Chunking: ~1000 chars, 150 overlap,
        page-tracked. Embeddings via OllamaEmbeddingService (768).
        Order per batch: IngestionEvent → Document vertex → Passage vertices
        (+ CONTAINS) → SUPPORTS edges."""

    async def link_supports(self, passage) -> None:
        """vector.neighbors on Invariant embeddings; similarity >=
        vector_similarity_min (0.35) → SUPPORTS edge (strength=similarity,
        excerpt=first 100 chars)."""
```

Nightly 02:00 job: scan `INBOX_PATH`, ingest, move processed files to
`SOURCES_PATH`. Failures move the file to `inbox/failed/` + ErrorEvent
(never crash the loop).

### Task 3.2 — `veille/rss.py` (UC3, weekly)

feedparser over `RSS_FEEDS_TIER1`; items from the last 7 days; dedupe by URL
hash against already-ingested Documents; write raw items to `inbox/` (they are
picked up by the nightly ingester). KnowledgeSearchEvent → EventLog with
counts.

**Done when:** a Dalio PDF produces Document + Passages with embeddings, and
≥1 SUPPORTS edge lands on a seeded invariant; an RSS run deposits items in
inbox and logs the event.

---

## Phase 4 — Planner
*Estimated: 2 days*

### Task 4.1 — `planner/pre.py`

Implements the 4 steps of ARCHITECTURE "Detailed Planner Steps":

```python
class PlannerPre:
    async def run(self, trigger: str, history: list) -> tuple[PlannerContext, dict]:
        # CALL 1a — Qwen3-8B, forced tool_use "QueryStrategies":
        #   semantic_query, portfolio_filter, invariant_topics,
        #   regime_focus, proposal_limit
        # PYTHON — embedding + asyncio.gather (6 queries):
        #   ① Passages vector search (vector.neighbors)
        #   ② Current Regime + global liquidity latest row
        #   ③ Ranked snapshot rows (today)
        #   ④ Scenarios + shift_d7 (ScenarioProbability TS)
        #   ⑤ Top invariants by weight_effective (integrated only)
        #   ⑥ Last 3 Proposals (any status)
        # CALL 1b — assemble_context tool → PlannerContext
        # Bridged tool closures built HERE (_db captured — never given to Worker);
        # returns (PlannerContext, tool_registry)
```

```python
class PlannerContext(BaseModel):
    regime: dict                  # type, aliases, confidence, tags, events
    global_liquidity: dict        # level, speed, state
    ranking: list[dict]           # snapshot rows incl. allocations
    scenarios: list[dict]         # per strategy, with shift_d7
    top_invariants: list[dict]    # id, title, weight_effective, tags, author
    recent_proposals: list[dict]  # incl. outcome verdicts and
                              #   rejection_reason — the Worker sees how its
                              #   past proposals fared and why rejections
                              #   happened
    passages: list[dict]          # id, excerpt, similarity
    notes: str                    # Call 1b free-text framing
```

Key queries (fixed):
```cypher
-- FAVORS for the current regime:
MATCH (r:Regime {is_current:true})
MATCH (rt:RegimeType {id: r.regime_type_id})-[f:FAVORS]->(s:Strategy)
WHERE s.enabled=true
RETURN s, f.sortino_rolling, f.sharpe_rolling, f.calmar_rolling
ORDER BY f.sortino_rolling DESC

-- Defender:
MATCH (p:Portfolio {defender:true, enabled:true})
OPTIONAL MATCH (p)-[h:HOLDS {primary:true}]->(s:Strategy)
OPTIONAL MATCH (p)-[:DESIGNED_FOR]->(rt:RegimeType)
RETURN p, s.id AS primary_strategy, rt.id AS designed_regime_type

-- Ranking (document type):
SELECT FROM portfolio_weekly_snapshot WHERE date = :today ORDER BY rank ASC
```

### Task 4.2 — `planner/post.py`

CALL 2 (async, thinking=1024): input = WorkerResult + PlannerContext; forced
tool_use `extract_knowledge` → `PostPlannerResult {evaluations,
scenario_updates, confrontations, innovations, regime_notes}`. Rejects any
vertex payload with missing `trace` (ValueError). Pydantic validation with
1 retry (Phase 1bis policy).

**Done when:** on the seeded DB with a mocked LLM, PlannerContext builds in
<5s and PostPlannerResult round-trips.

---

## Phase 5 — Worker
*Estimated: 1.5 days*

### Task 5.1 — `worker/tools.py` (principle of least privilege)

```python
SQL_KEYWORD_BLACKLIST = {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP",
                         "ALTER", "TRUNCATE", "GRANT"}  # reject if present
PORTFOLIO_ID_RE = r"^[a-z0-9][a-z0-9-]{0,49}$"
PORTFOLIO_EXPOSED_FIELDS = [
    "id", "name", "defender", "enabled", "allocation", "benchmark",
    "max_drawdown_rule", "max_single_asset_pct",
    "sharpe_rolling", "sortino_rolling", "calmar_rolling",
    "max_drawdown", "volatility",
    "return_3m", "return_6m", "return_1y", "return_3y", "return_5y",
]

async def db_query(stmt: str, lang: str) -> list[dict]:
    """lang in {'sql','cypher'}; READ only (keyword blacklist); LIMIT
    enforced/injected at 20 rows."""

async def market_fetch(tickers: list[str], period: str) -> list[dict]:
    """tickers ⊆ allowed_tickers(active=true) — macro & composites included;
    max 30 rows total; returns (ts, ticker, level, speed, acceleration)."""

async def portfolio_check(portfolio_id: str) -> dict:
    """id regex-validated; returns PORTFOLIO_EXPOSED_FIELDS only."""
```

### Task 5.2 — `worker/worker.py` + skills

PydanticAI agent, model `WORKER_MODEL`, system prompt from ARCHITECTURE,
skills (markdown files) concatenated into the system context, output type
`WorkerResult` (schema in ARCHITECTURE). 1-8 tool calls budget.

Skill files (each: purpose, inputs, method, output contract):
- `skill-evaluate-strategy.md` — verdict per enabled strategy from regime,
  FAVORS, scenario shifts, invariants → EvaluationDrafts.
- `skill-rank-portfolios.md` — EXPLAIN the mechanical ranking (never re-rank);
  flag calmar demotions and drawdown-rule exclusions.
- `skill-compare-vs-defender.md` — challenger gaps, downside-profile flags,
  switch_commentary for the Proposal reasoning.
- `skill-propose-reallocation.md` — WHEN active-scenario probability shifted
  > scenario_shift_trigger OR allocation drift vs blend target > 5pts:
  build proposed_allocation = current + 0.4×scenario_delta + 0.6×favors_delta,
  rounded to 2.5, renormalized to 100; cite ≥1 supporting invariant; explain
  the blend in reasoning. Otherwise return null.
- `skill-interpret-invariants.md` — weight semantics (ceiling, floor, decay),
  authority tiers, how to cite invariants in reasoning.

### Task 5.3 — UC4 knowledge curation runner (LLM)

`worker/curation.py` — same Worker model, NO bridged tools (input assembled
mechanically): new Passages since last run + their SUPPORTS-linked invariants
+ top invariants by weight. Skill: `skill-curate-knowledge.md`.

**Three callers, one runner:**
1. nightly 02:15, event-driven — only when the 02:00 ingester created new
   Documents (a deposited book yields candidates the next morning);
2. weekly Monday 08:20 — sweep + re-curation of existing invariants;
3. UC0 seed batch (step 4b, default) — whole corpus, interactive CLI
   validation.

Output:

```python
class InvariantCandidate(BaseModel):
    title: str
    description: str
    example: str
    source: str               # real provenance: "document#id, passage#id, p.N"
    author: Optional[str]     # = Document.author tier (dalio/marks/None) for
                              #   corpus extraction; 'system' ONLY for
                              #   market-pattern discoveries
    tags: list[str]           # incl. regime:<regime_type_id> when applicable
    supporting_passages: list[str]
    suggested_backed_by: list[str]  # Strategy ids this invariant plausibly
                              #   backs; on user validation Writeback creates
                              #   the BACKED_BY edges (without this, new
                              #   invariants would never enter the
                              #   confrontation loop)

class CurationResult(BaseModel):
    curations: list[dict]     # AUTONOMOUS: description/example enrichment,
                              #   new SUPPORTS links on existing INTEGRATED
                              #   invariants (never weights directly — weights
                              #   are mechanical)
    invariant_candidates: list[InvariantCandidate]  # → status=proposed;
                              #   weight_initial/floor from
                              #   invariant_author_config[author]
    innovations_proposed: list[ImprovementProposal] # new_invariant /
                              #   new_strategy / schema / metric proposals
```

Persisted via Writeback (KnowledgeEvent → EventLog first). Curation vs
Innovation boundary and author-tier rule per CLAUDE.md. The same runner
serves two callers: weekly UC4 (validation via Telegram) and the UC0
`--curate` seed pass (batch validation interactively in the CLI — see
USE_CASES.md step 4b).

**Done when:** on the seeded DB, the Worker produces a complete WorkerResult
(with reallocation_proposed populated when the bear-scenario fixture shifts
+35pts) using only the 3 bridged tools; the curation runner enriches an
existing invariant from a new fixture passage without touching its weight.

---

## Phase 5bis — Mechanical Jobs
*Estimated: 1.5 days*

- `ratios.py` (daily 06:35) — NAV update per pinned conventions (constant
  weights, monthly rebalance, cash at ^IRX) + all `*_rolling` indicators →
  PortfolioNAV TS. Weekly (UC6): update Portfolio vertices + ValuationEvent.
- `scenarios.py` (daily 06:45) — evaluate NUMERIC triggers only (grammar
  `<TICKER|ALIAS> <op> <number>`; unparseable → skipped, Worker-only);
  append current probabilities + `shift_d7` to ScenarioProbability TS.
  Probability VALUES change only via Worker `scenario_adjustments` (weekly).
- `backtests.py` (weekly 08:30) — synthetic backtests per (Strategy ×
  RegimeType) over historical Regime instances; refresh FAVORS aggregates +
  strategy_performance docs.
- `invariants.py` (weekly 08:40 + event-driven) — implements the mechanical
  confrontation rule (ARCHITECTURE): FAVORS-vs-median for the current regime
  type + Evaluation verdict propagation → invariant_confrontations →
  update_invariant_weights() → Invariant.updated_at.
- `snapshots.py` (weekly 08:50) — ranking per REVISION_NOTES rule
  (sortino DESC, calmar tie-break 0.02, max_drawdown final; calmar<1.0
  demoted; user-drawdown breach = defender/proposal exclusion flag) →
  snapshot rows + RankingEvent.
- `outcomes.py` (weekly 08:52) — the unified improvement cycle's measuring
  arm (full spec in ARCHITECTURE): `evaluate_proposals()` (outcome verdicts
  at +proposal_outcome_weeks, net of replay_cost_bps, → ProposalOutcomeEvent
  → Proposal.outcome + confrontations source='proposal'; weekly paper-test
  tracking from paper_started), `score_scenarios()` (calibration at
  +scenario_calibration_weeks → scenario_calibration docs +
  CalibrationEvent), `strategy_probation_check()` (FAVORS percentile at
  +strategy_probation_weeks → ProbationEvent 'keep'|'review'; 'review' →
  Telegram closure proposal).
- `learning.py` — V2 stub raising NotImplementedError.

**Done when:** the full Monday pre-processing chain (08:00→08:55 steps) runs
on the seeded DB and produces a fresh ranked snapshot.

---

## Phase 6 — Writeback
*Estimated: 1 day*

**`writeback/writeback.py`** — pure executor + mechanical gates. EventLog
append ALWAYS precedes vertex/edge commits.

```python
def effective_caps(user_profile, portfolio) -> tuple[float, float]:
    """Binding rule: stricter of user_profile and portfolio caps."""
    return (min(user.max_single_asset_pct, p.max_single_asset_pct),
            max(user.max_drawdown_pct, p.max_drawdown_rule))  # both negative

# A — switch gate (from snapshot rows, after Worker cycle):
#   PRE-GATE: challenger not user-rejected within proposal_cooldown_weeks
#     (unless regime type changed since the rejection)
#   challenger rank < defender rank
#   AND sortino gap >= proposal_sortino_gap_min
#   AND challenger calmar_rolling >= proposal_calmar_min
#   AND challenger max_drawdown within binding user drawdown rule
#   AND max(challenger allocation) <= binding single-asset cap
#   AND max per-asset |challenger − defender| >= proposal_min_allocation_change_pts
#   → Proposal(proposal_type='switch', recommendation='paper-test'|'monitor',
#              reasoning=WorkerResult.switch_commentary)

# B — reallocation gate (from WorkerResult.reallocation_proposed):
#   sum(proposed_allocation) == 100 ± 0.1
#   AND every ticker in allowed_tickers(active, non-MACRO asset_class) or 'cash'
#   AND max(proposed_allocation) <= binding single-asset cap
#   AND max per-asset |delta| >= proposal_min_allocation_change_pts
#   AND Σ|delta|/2 <= proposal_max_turnover_pct
#   AND every supporting_invariant is status='integrated' with
#       weight_effective >= proposal_invariant_weight_min  (gate 6)
#   → Proposal(proposal_type='reallocation', recommendation='paper-test',
#              proposed_allocation=..., reasoning=ReallocationProposal.reasoning)

# On pass: ProposalEvent → Proposal vertex → snapshot recommendation upgrade
#          → telegram.send_proposal(...)
# On block: ⛔ Telegram note with the failed gate + Worker reasoning; no vertex.
# Innovations: InnovationEvent → vertex(status=proposed) → Telegram [YES][NO].
#   type=new_invariant → Invariant vertex.
#   type=new_strategy  → Strategy vertex (enabled=false); on user YES, ONE
#     transaction creates status=active + 3 Scenarios + HAS_SCENARIO +
#     BACKED_BY edges (spec fields in ARCHITECTURE "System Evolution");
#     Backtests/FAVORS follow mechanically at the next weekly cycle.
#   type=strategy_revision → same as new_strategy + in the SAME transaction
#     the superseded vertex gets status='closed', enabled=false,
#     date_revised=today; HOLDS repointing stays a user action (UC9).
#   Every activated strategy (new or revision) enters probation
#     (strategy_probation_weeks — outcomes.py).
# Expiry: daily sweep sets user_response='expired' after proposal_expiry_days.
```

V2 adaptation flow (auto-validation timer) remains documented but inactive.

**Done when:** both gates covered by tests (pass + block paths), EventLog
ordering asserted.

---

## Phase 6bis — Telegram (digest + UC9 bot)
*Estimated: 1 day*

### Task 6bis.1 — `telegram/digest.py`

Renders the Monday 09:30 digest from snapshot rows + Proposal + innovations
(templates in EXAMPLE.md Steps 8A/8B): regime header, ranked table with
Sortino/Calmar, defender star, key invariants with weights, proposal block
(switch: both portfolios + gaps; reallocation: old vs new allocation table +
blend reasoning), **scoreboard block** (cumulative proposal hit-rate,
paper-tests in progress with proposed-vs-incumbent to date, strategies in
probation, scenario calibration flags), cumulative returns line. Percent
formatting happens HERE only (decimal fractions everywhere else).

### Task 6bis.2 — `telegram/bot.py` (UC9)

python-telegram-bot application:
- Callbacks: `[ACCEPT PAPER-TEST]/[REJECT]` → UserDecisionEvent →
  Proposal.user_response (+ paper_started on accept; on reject, prompt for
  an optional one-line rejection_reason); `[YES]/[NO]` →
  Invariant status integrated/rejected (+ validated_at).
- Chat handler: Worker model + same 3 bridged tools + chat skill; decisions
  persist via Planner Post → Writeback; max ONE ad-hoc UC8 re-run per day.
- Commands: `/status`, `/ranking`, `/disable <strategy_id>`,
  `/enable <strategy_id>`, `/drawdown <pct>` (updates user_profile — binding).
- Document/URL messages are saved to `INBOX_PATH` (feeds the nightly ingester).

**Done when:** digest renders from a seeded snapshot; buttons mutate state
via Writeback with EventLog-first ordering; `/drawdown -10` updates
user_profile and is reflected in the next gate evaluation.

---

## Phase 7 — Main process + APScheduler
*Estimated: 0.5 day*

```python
# src/investment/main.py — UC1 onwards. UC0 runs via `python -m investment.seed`.

async def main():
    db = InvestmentDB(settings.arcade_db_path)
    await db.init_schema()
    if not await db.query("cypher", "MATCH (f:Framework {id:'4seasons'}) RETURN f"):
        raise RuntimeError("Seed not run. Execute `uv run python -m investment.seed` first.")

    scheduler = AsyncIOScheduler(timezone="Europe/Zurich")
    # Daily: 02:00 ingest, 06:30 market, 06:35 ratios, 06:45 scenarios,
    #        06:50 regime, 03:00 backup, hourly proposal-expiry sweep.
    # Weekly: ONE job Monday 08:00 = monday_chain() — runs UC2 → UC3 → UC4 →
    #   backtests → invariant weights → UC6 → UC7 → outcomes.py →
    #   (V2 learning) → UC8 → digest SEQUENTIALLY. Each step awaited; on exception: ErrorEvent →
    #   Telegram alert → abort remaining steps (never rank on stale data).
    # Retries: fetchers 3× exponential backoff; LLM calls per Phase 1bis policy.
```

Backup (daily 03:00): ArcadeDB backup (or cold file copy of
`/data/investment/arcade_db/` after a checkpoint) →
`/data/investment/backups/`, keep 14 days.

---

## Phase 8 — Integration tests
*Estimated: 1 day*

```python
async def test_uc0_seed_idempotent():        # run twice → no duplicates; 2 SeedEvents
async def test_schema_complete():            # 14 vertex + 11 edge + 3 TS + 12 doc types
async def test_seed_respects_binding_caps(): # every seed allocation ≤ 40% single asset
async def test_historical_regimes_seeded():  # ≥10 Regime instances; exactly 1 is_current
async def test_nav_conventions_golden():     # NAV/sharpe/sortino/calmar on a fixed
                                             # 3-asset fixture == pinned golden numbers
async def test_corpus_ingestion():           # PDF → Document + Passages; vector search works
async def test_regime_detection_hysteresis():# flip-flop input does not switch before
                                             # regime_confirm_prints consecutive
                                             # concordant monthly prints per axis
async def test_portfolio_ranking():          # all enabled ranked; calmar<1 demoted;
                                             # gap_to_defender null only for defender
async def test_favors_targets_strategy():    # RegimeType -[FAVORS]-> Strategy only
async def test_holds_primary():              # exactly one HOLDS primary=true per Portfolio
async def test_switch_gate():                # challenger passes 5 gates → Proposal(switch)
async def test_switch_gate_blocked_caps():   # binding user cap violation → blocked
async def test_reallocation_gate():          # valid ReallocationProposal → Proposal
                                             # vertex with proposed_allocation
async def test_reallocation_gate_turnover(): # Σ|delta|/2 > 30 → blocked
async def test_invariant_confrontation():    # FAVORS above median → confirmation row +
                                             # weight_effective recomputed
async def test_agent_innovation():           # status=proposed + Telegram in same cycle
async def test_new_strategy_innovation():    # validated new_strategy → Strategy(active)
                                             # + 3 Scenarios + BACKED_BY in one tx;
                                             # rejected → status=closed, enabled=false;
                                             # next weekly cycle produces its FAVORS
async def test_strategy_revision():          # validated revision → -v(N+1) active AND
                                             # superseded closed + date_revised in one
                                             # tx; HOLDS untouched; probation starts
async def test_proposal_outcome():           # Proposal aged 12w → outcome.verdict set,
                                             # confrontation rows source='proposal' for
                                             # cited invariants; younger → still pending
async def test_realloc_gate_invariant_weight(): # cited invariant weight_effective <
                                             # threshold (or not integrated) → blocked
async def test_proposal_cooldown():          # same challenger re-gated within 4 weeks
                                             # of rejection → skipped, unless regime
                                             # type changed
async def test_scenario_calibration():       # dominant scenario vs realized quadrant
                                             # → scenario_calibration row + score
async def test_strategy_probation():         # strategy below median FAVORS at +12w →
                                             # ProbationEvent 'review' + Telegram
async def test_nightly_curation_trigger():   # new Document at 02:00 → curation runs
                                             # at 02:15 → InvariantCandidate with
                                             # author = document author tier +
                                             # suggested_backed_by; no new Document
                                             # → runner not invoked
async def test_eventlog_event_date_query():  # backfilled event sortable by event_date
                                             # independently of append order
async def test_eventlog_precedes_commit():   # for every audited change, the EventLog
                                             # append happens before the vertex commit
                                             # IN APPEND ORDER (monotonic ULID id) —
                                             # never compare wall-clock ts
async def test_eventlog_id_monotonic():      # two appends in the same millisecond →
                                             # strictly increasing ids; a backdated
                                             # payload date does not disturb ordering
async def test_monday_chain_aborts():        # UC6 failure → no UC7 snapshot, ErrorEvent
```

---

## Phase 9 — Shadow Replay (meta-backtest of the agent — GO-LIVE GATE)
*Estimated: 1.5 days*

**Principle:** the decision pipeline is fully mechanical (regime detection,
ranking, switch and reallocation gates — the LLM only adds reasoning, never
decisions). It can therefore be **replayed week by week over the whole 25y
backfill** to measure whether the agent's recommendations would actually have
beaten holding the defender, net of costs — BEFORE the service goes live.
This is also what turns every hand-picked threshold (Sortino gap 0.02,
Calmar 1.5, blend 0.4/0.6, turnover 30, 36M window) from an opinion into a
calibrated value.

### Task 9.1 — `mechanical/replay.py`

```python
async def shadow_replay(db, start: date, end: date,
                        thresholds: dict | None = None) -> ReplayReport:
    """Replay the mechanical Monday pipeline for every Monday in [start, end].

    POINT-IN-TIME DISCIPLINE (non-negotiable — a leak invalidates everything):
      - MarketData/derivatives: only rows with ts <= t.
      - Regime state as-of t: the historical instances are PIT by construction
        (materialize_history runs the detector forward chronologically with
        hysteresis); assert no instance with start_date > t is visible.
      - FAVORS as-of t: aggregate ONLY over regime instances with end_date < t
        (recomputed incrementally, never read from the live seeded edges).
      - Portfolio indicators: rolling windows ending at t.

    Per simulated Monday t:
      1. rank enabled portfolios (same snapshots.py code path, shadow output)
      2. run the SWITCH gates → hypothetical Proposal; acceptance policy =
         'accept-after-2-weeks-confirmation' (configurable) → the shadow
         defender switches
      3. run the REALLOCATION path mechanically: numeric scenario triggers
         only (the Worker's qualitative judgment is NOT simulated — flag as
         a conservative approximation in the report)
      4. apply switching costs: turnover × replay_cost_bps (both sides)
      5. record the shadow book NAV

    Outputs ReplayReport (persisted as replay_report doc + ReplayEvent →
    EventLog with event_date = t range) comparing three NAVs over [start,end]:
      A. agent-follow (accept every gated proposal)
      B. hold-initial-defender (never switch)
      C. 60/40 benchmark
    Metrics: CAGR, sortino, calmar, max_drawdown, n_switches, avg turnover,
    proposal hit-rate at +12 weeks, false-signal rate (proposal whose
    challenger underperforms the defender over the following 12 weeks).
    """
```

### Task 9.2 — Threshold calibration (walk-forward)

Grid search over `proposal_sortino_gap_min`, `proposal_calmar_min`,
`ranking_tiebreak_window`, blend weights, `proposal_max_turnover_pct`.
**Walk-forward split: calibrate on the first 15y, validate on the last 10y**
(never calibrate and judge on the same window). Winning set written to
`system_thresholds` only after user confirmation via Telegram.

### Task 9.3 — Go-live gate in `main.py`

At startup, `main.py` refuses to enable the weekly proposal cycle unless the
latest `replay_report` shows **agent-follow ≥ hold-initial-defender on the
validation window, net of costs** (override: `--force-live`). Telegram
summary on every replay run:
"25y replay: agent-follow +X.X%/y vs defender +Y.Y%/y | Sortino A vs B |
N switches, hit-rate Z% at +12w, false signals W%".

Add to `system_thresholds`: `replay_cost_bps: 10.0`,
`replay_confirmation_weeks: 2.0`. Add `ReplayEvent` to the EventLog type enum
and `replay_report` to the document types.

**Done when:** `uv run python -m investment.mechanical.replay` produces a
full 25y report with zero point-in-time assertions failed; go-live gate
blocks on a fixture where the ruleset destroys value; calibration writes
nothing without user confirmation.

Tests:
```python
async def test_replay_point_in_time():   # injecting a future-dated row must not
                                         # change any decision before its date
async def test_replay_go_live_gate():    # value-destroying fixture → main.py
                                         # refuses weekly cycle without --force-live
```

---

## Notes for Claude Code

1. **arcadedb-embedded ARM64** — wheel since 26.1.1.post3. Writes require
   explicit transactions.
2. **Entry points** — `python -m investment.main` (service) and
   `python -m investment.seed` (UC0). No root-level scripts.
3. **Sequential writes** — all ArcadeDB writes in the same asyncio thread.
4. **Mandatory trace** — `create_vertex` raises ValueError on empty trace,
   EXCEPT `TRACE_EXEMPT = {Passage, RegimeType, EventLog}`.
5. **EventLog first** — every EventLog append precedes vertex/edge commit.
   EventLog is a VERTEX (append-only), not a TS.
6. **TimeSeries syntax** — `CREATE TIMESERIES TYPE x TIMESTAMP ts TAGS (...)
   FIELDS (...)`; FIELDS are numeric only.
7. **Vector indexes** — do NOT guess SQL DDL; use the bindings' API
   (LSM vector, 768 dims, cosine) and `vector.neighbors()` for queries.
8. **Risk-free rate** — fetch ^IRX daily; use in Sharpe/Sortino
   (`rf_daily = (1+IRX/100)^(1/252)−1`).
9. **Rolling window** — 756 trading days (36M) for ALL `*_rolling`
   indicators, from `system_thresholds.rolling_window_days`. All other
   formulas pinned in DATA_MODELS.md "Calculation conventions".
10. **Currency** — USD for indicators. CHFUSD=X for user display only.
11. **Recency formula** — `0.5 + 0.5 * exp(-days_since / 365)` (asymptotic
    floor 0.5; no clamp).
12. **Floor on Invariant vertex** — set at creation from `author` tier
    (dalio=0.40, marks=0.35, null=0.20, system=0.05). `source` is real provenance.
13. **Growth axis** — GROWTH_COMPOSITE (INDPRO YoY, UNRATE Δ3m), never PMI.
    VIX from Yahoo ^VIX only.
14. **Binding caps** — user_profile rules bind; per-portfolio rules only
    stricter; Writeback uses `effective_caps()`.
15. **Worker proposes, Writeback disposes** — all proposal gates (switch AND
    reallocation) are mechanical, in Writeback.
16. **Innovation status** — never `status:integrated` without `user_validated=True`.
17. **Timezone** — Europe/Zurich everywhere (APScheduler + cron semantics).
18. **Monday = one chain** — sequential, abort on failure, ErrorEvent + alert.
19. **YFinance rate limit** — `time.sleep(0.5)` between tickers.
20. **FAVORS direction** — RegimeType → Strategy. Not Portfolio, not Regime instance.
21. **DESIGNED_FOR** — Portfolio → RegimeType, nullable. **HOLDS** —
    Portfolio → Strategy with `primary BOOLEAN`; no `strategy_id` scalar.
22. **Ids** — ULIDs for generated ids (EventLog, confrontations, proposals).

**Total estimated effort:** ~12.5 days for MVP (incl. Phase 9 shadow replay).
