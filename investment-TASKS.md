# TASKS.md — Investment Agent MVP

See REVISION_NOTES.md for V1 scope and core concepts.

## Objective

Build capital (10-20 year horizon) via a self-improving expert investment agent.
Phase 1: accumulation only.

V1 delivers a weekly portfolio ranking and digest engine. V2 adds auto-adaptive
execution and automatic learning from real allocation changes.

V1 mechanisms:
1. Market context — 4 Seasons regime + global liquidity, with
   level/speed/acceleration on MarketData TS.
2. Knowledge seed — Documents/notes → Passages → Invariants.
3. Portfolio universe seed — Strategies as theses; Portfolios as concrete
   ETF allocations.
4. Ranking — all enabled portfolios, including the live defender, using USD
   `sharpe_rolling`, `sortino_rolling`, `calmar_rolling`, `max_drawdown`,
   `volatility`, `total_return`.
5. Digest/proposal — Telegram weekly digest + optional Proposal vertex.

See IMPROVEMENTS.md for deferred V2 features.

---

## Scope MVP

| Component       | Detail                                                        |
|-----------------|---------------------------------------------------------------|
| DB              | ArcadeDB embedded in-process                                  |
| Graph           | 13 vertex types, 13 edge types                                |
| Time-Series     | MarketData + ScenarioProbability + PortfolioNAV + Event       |
| LLM Framework   | PydanticAI (V1, model-agnostic)                               |
| Planner         | Qwen3-8B via OpenRouter, thinking=512/1024                    |
| Worker          | Sonnet 4 via Anthropic                                        |
| Corpus          | PDF parser direct → Passages → Invariants                     |
| Veille          | RSS feeds + user deposits                                     |
| Market data     | Yahoo Finance prices + FRED macro + GLOBAL_LIQUIDITY composite |
| Risk-free rate  | 3-Month T-Bill (^IRX) — USD                                   |
| Currency        | USD for all ratios; CHFUSD=X for display only                 |
| Ingestion       | Telegram bot + SCP → inbox/ (nightly job 02:00)               |
| Notification    | Telegram weekly digest (Mon 09:30) + Proposal alerts          |
| Deployment      | systemd service on Hetzner CAX21 ARM                          |

**Out of scope (see IMPROVEMENTS.md):** I-1, I-2, I-3 through I-18.

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

# Claude Code (native ARM64)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.bashrc
nvm install --lts
npm install -g @anthropic-ai/claude-code
claude --version
claude --no-browser

# GitHub CLI
apt install gh -y
gh auth login

git config --global user.email "jp@..."
git config --global user.name "JP"
git clone https://github.com/jp/investment-agent.git /opt/investment-agent
```

**Done when:** python3.12, uv, ollama (with nomic-embed-text), claude, gh, tmux OK.

---

### Task 0.2 — Project directories

```bash
mkdir -p /opt/investment-agent
mkdir -p /data/investment/{inbox,sources/corpus,sources/kindle,arcade_db,logs}
chown -R ubuntu:ubuntu /opt/investment-agent /data/investment
```

---

### Task 0.3 — Environment variables

```bash
cat >> /opt/investment-agent/.env << 'EOF'
# LLMs
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
PLANNER_MODEL=qwen/qwen3-8b
PLANNER_THINKING_BUDGET_PRE=512
PLANNER_THINKING_BUDGET_POST=1024
WORKER_MODEL=claude-sonnet-4-20250514
OLLAMA_BASE_URL=http://localhost:11434

# ArcadeDB
ARCADE_DB_PATH=/data/investment/arcade_db/investment.db

# Ingestion
INBOX_PATH=/data/investment/inbox
SOURCES_PATH=/data/investment/sources/corpus

# Market data
YAHOO_FINANCE_TICKERS=TIP,TLT,GLD,DJP,SPY,IEF,CHFUSD=X,^IRX,^VIX
FRED_SERIES=CPIAUCSL,T10Y2Y,VIXCLS,UMCSENT,UNRATE
GLOBAL_LIQUIDITY_COMPONENTS=M2SL,WALCL,ECBASSETSW,BOJ_ASSETS

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Veille
RSS_FEEDS_TIER1=https://feeds.bloomberg.com/markets/news.rss,...

# User profile defaults
USER_CURRENCY=CHF
USER_MAX_DRAWDOWN_PCT=-15
USER_MAX_SINGLE_ASSET_PCT=40
USER_BENCHMARK=60/40-USD
USER_PHASE=accumulation
USER_AUTO_VALIDATION_HOURS=48
EOF
```

---

### Task 0.4 — Python setup

```bash
cd /opt/investment-agent
uv init investment-agent
cd investment-agent

uv add arcadedb-embedded pydantic-ai anthropic openai \
       apscheduler pydantic pydantic-settings python-dotenv \
       python-telegram-bot

uv add yfinance pandas-datareader pandas numpy scipy \
       pypdf2 aiofiles aiohttp feedparser

uv add --dev pytest pytest-asyncio httpx

python3 -c "import arcadedb_embedded; print('ArcadeDB OK')"
```

---

### Task 0.5 — Project structure

```
/opt/investment-agent/
├── .env
├── pyproject.toml
├── main.py                       ← APScheduler entry (UC1 onwards)
├── seed.py                       ← UC0 CLI entry (one-shot bootstrap)
├── config.py
├── src/
│   ├── metis/
│   │   └── base/
│   │       ├── embedding.py
│   │       ├── llm.py
│   │       ├── llm_clients.py
│   │       ├── llm_factory.py
│   │       └── tool_wrapper.py
│   └── investment/
│       ├── models/
│       │   ├── entities.py       ← Pydantic: Framework, Signal, Regime,
│       │   │                       Invariant, Strategy, Scenario, Evaluation,
│       │   │                       Backtest, Adaptation, Proposal, Portfolio,
│       │   │                       Document, Passage
│       │   ├── command.py
│       │   └── result.py
│       ├── db/
│       │   ├── arcade.py
│       │   ├── schema.py
│       │   ├── seed.py           ← UC0 implementation
│       │   └── queries.py
│       ├── planner/
│       ├── worker/
│       │   ├── worker.py
│       │   ├── tools.py
│       │   └── skills/
│       │       ├── skill-evaluate-strategy.md
│       │       ├── skill-rank-portfolios.md
│       │       ├── skill-compare-vs-defender.md
│       │       └── skill-interpret-invariants.md
│       ├── writeback/
│       ├── corpus/
│       ├── market/
│       │   ├── fetcher.py
│       │   ├── derivatives.py    ← level/speed/acceleration
│       │   ├── liquidity.py      ← GLOBAL_LIQUIDITY composite
│       │   └── regime.py
│       ├── mechanical/
│       │   ├── ratios.py
│       │   ├── scenarios.py
│       │   ├── invariants.py
│       │   ├── backtests.py
│       │   ├── snapshots.py      ← portfolio_weekly_snapshot writer
│       │   └── learning.py       ← V2 only
│       ├── veille/
│       └── telegram/
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
ExecStart=/opt/investment-agent/investment-agent/.venv/bin/python main.py
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
-- VERTEX TYPES (13)
CREATE VERTEX TYPE Framework  IF NOT EXISTS;
CREATE VERTEX TYPE Signal     IF NOT EXISTS;
CREATE VERTEX TYPE Regime     IF NOT EXISTS;
CREATE VERTEX TYPE Invariant  IF NOT EXISTS;
CREATE VERTEX TYPE Strategy   IF NOT EXISTS;
CREATE VERTEX TYPE Scenario   IF NOT EXISTS;
CREATE VERTEX TYPE Evaluation IF NOT EXISTS;
CREATE VERTEX TYPE Backtest   IF NOT EXISTS;
CREATE VERTEX TYPE Adaptation IF NOT EXISTS;   -- V2 only, reserved
CREATE VERTEX TYPE Proposal   IF NOT EXISTS;   -- V1 paper-mode
CREATE VERTEX TYPE Portfolio  IF NOT EXISTS;
CREATE VERTEX TYPE Document   IF NOT EXISTS;
CREATE VERTEX TYPE Passage    IF NOT EXISTS;

-- EDGE TYPES (13)
CREATE EDGE TYPE IMPLIES       IF NOT EXISTS;
CREATE EDGE TYPE GENERATES     IF NOT EXISTS;
CREATE EDGE TYPE UPDATES       IF NOT EXISTS;
CREATE EDGE TYPE FAVORS        IF NOT EXISTS;  -- Regime → Strategy
CREATE EDGE TYPE HAS_SCENARIO  IF NOT EXISTS;
CREATE EDGE TYPE BACKED_BY     IF NOT EXISTS;
CREATE EDGE TYPE TESTED_IN     IF NOT EXISTS;
CREATE EDGE TYPE IN_REGIME     IF NOT EXISTS;
CREATE EDGE TYPE MODIFIES      IF NOT EXISTS;  -- V2 only
CREATE EDGE TYPE HOLDS         IF NOT EXISTS;  -- Portfolio → Strategy (primary BOOLEAN)
CREATE EDGE TYPE DESIGNED_FOR  IF NOT EXISTS;  -- Portfolio → Regime (nullable)
CREATE EDGE TYPE CONTAINS      IF NOT EXISTS;
CREATE EDGE TYPE SUPPORTS      IF NOT EXISTS;

-- INDEXES
CREATE INDEX ON Framework (enabled)           IF NOT EXISTS;
CREATE INDEX ON Signal (date)                 IF NOT EXISTS;
CREATE INDEX ON Signal (tier)                 IF NOT EXISTS;
CREATE INDEX ON Regime (is_current)           IF NOT EXISTS;
CREATE INDEX ON Invariant (status)            IF NOT EXISTS;
CREATE INDEX ON Strategy (status)             IF NOT EXISTS;
CREATE INDEX ON Strategy (enabled)            IF NOT EXISTS;
CREATE INDEX ON Portfolio (live)              IF NOT EXISTS;
CREATE INDEX ON Portfolio (enabled)           IF NOT EXISTS;
CREATE INDEX ON Proposal (date)               IF NOT EXISTS;
CREATE INDEX ON Proposal (user_response)      IF NOT EXISTS;
CREATE INDEX ON Adaptation (user_validated)   IF NOT EXISTS;
CREATE INDEX ON Adaptation (learning_applied) IF NOT EXISTS;

-- VECTOR INDEXES
CREATE VECTOR INDEX ON Passage   (embedding) LSM TYPE COSINE IF NOT EXISTS;
CREATE VECTOR INDEX ON Invariant (embedding) LSM TYPE COSINE IF NOT EXISTS;
CREATE VECTOR INDEX ON Signal    (embedding) LSM TYPE COSINE IF NOT EXISTS;

-- TIME-SERIES (4)
CREATE TIME SERIES TYPE MarketData IF NOT EXISTS (
  ticker       STRING, asset_class STRING, currency STRING,
  close        FLOAT,
  level        FLOAT, speed FLOAT, acceleration FLOAT,
  volume       LONG,
  regime_id    STRING
);
CREATE TIME SERIES TYPE ScenarioProbability IF NOT EXISTS (
  strategy_id  STRING, scenario STRING,
  probability  FLOAT,  shift_d7 FLOAT
);
CREATE TIME SERIES TYPE PortfolioNAV IF NOT EXISTS (
  portfolio_id    STRING, currency STRING,
  nav             FLOAT,  daily_return FLOAT,
  sharpe_rolling  FLOAT,  sortino_rolling FLOAT,
  calmar_rolling  FLOAT,  drawdown FLOAT,
  vs_benchmark    FLOAT
);
CREATE TIME SERIES TYPE Event IF NOT EXISTS (
  type STRING, source_uc STRING, source_id STRING, payload STRING
);

-- DOWNSAMPLING POLICIES
ALTER TIMESERIES TYPE MarketData ADD DOWNSAMPLING POLICY
  AFTER 30 DAYS GRANULARITY 1 DAY
  AFTER 365 DAYS GRANULARITY 1 WEEK;
ALTER TIMESERIES TYPE ScenarioProbability ADD DOWNSAMPLING POLICY
  AFTER 7 DAYS  GRANULARITY 1 DAY
  AFTER 30 DAYS GRANULARITY 1 WEEK;
ALTER TIMESERIES TYPE PortfolioNAV ADD DOWNSAMPLING POLICY
  AFTER 90 DAYS GRANULARITY 1 WEEK;
"""
```

**Done when:** schema created without error; all 13 vertex + 13 edge + 4 TS types present.

---

### Task 1.2 — ArcadeDB client wrapper

**`src/investment/db/arcade.py`**

```python
import arcadedb_embedded as arcadedb

class InvestmentDB:
    """ArcadeDB wrapper — agent sole writer, asyncio sequential."""

    def __init__(self, db_path: str):
        self._db = arcadedb.create_database(db_path)
        self._db.__enter__()

    async def query(self, lang: str, sql: str, *params) -> list[dict]: ...
    async def command(self, lang: str, sql: str, *params) -> None: ...
    async def create_vertex(self, type: str, props: dict) -> str:
        if not props.get("trace"):
            raise ValueError(f"trace mandatory for {type}")
        ...
    async def create_edge(self, type: str, from_id: str, to_id: str,
                          props: dict = {}) -> None: ...
    async def upsert_vertex(self, type: str, id: str, props: dict) -> str: ...
    async def append_ts(self, type: str, ts: datetime, props: dict) -> None: ...
    async def query_ts(self, type: str, where: str, limit: int) -> list[dict]: ...

    def close(self):
        self._db.__exit__(None, None, None)
```

---

### Task 1.3 — Seed reference data (SQL only)

**`src/investment/db/seed.py` — SQL portion**

```python
SYSTEM_THRESHOLDS = {
    "scenario_shift_trigger": 10.0,
    "min_backtest_periods": 3.0,
    "vector_similarity_min": 0.35,
    "auto_validation_hours": 48.0,
    "calmar_window_days": 756.0,
    "recency_half_life_days": 365.0,
    "regime_cpi_stagflation": 2.5,
    "regime_pmi_contraction": 48.0,
    "regime_pmi_expansion": 52.0,
    "regime_vix_stress": 25.0,
    "regime_cpi_deflation": 0.0,
    "regime_consecutive_months": 2.0,
    "derivative_lookback_short": 30.0,    # days for speed/acceleration short window
    "derivative_lookback_long": 90.0,     # days for speed/acceleration long window
}

INVARIANT_SOURCE_CONFIG = [
    {"source": "corpus", "author_weight": "dalio", "floor_weight": 0.40,
     "initial_weight_min": 0.80, "initial_weight_max": 0.90},
    {"source": "corpus", "author_weight": "marks", "floor_weight": 0.35,
     "initial_weight_min": 0.75, "initial_weight_max": 0.85},
    {"source": "corpus", "author_weight": None, "floor_weight": 0.20,
     "initial_weight_min": 0.40, "initial_weight_max": 0.70},
    {"source": "agent-discovery", "author_weight": None, "floor_weight": 0.05,
     "initial_weight_min": 0.15, "initial_weight_max": 0.25},
]

ALLOWED_TICKERS = [
    {"ticker": "TIP",      "asset_class": "US_TIPS",          "currency": "USD"},
    {"ticker": "TLT",      "asset_class": "US_LONG_TREASURY", "currency": "USD"},
    {"ticker": "IEF",      "asset_class": "US_TREASURY_7_10", "currency": "USD"},
    {"ticker": "GLD",      "asset_class": "GOLD",             "currency": "USD"},
    {"ticker": "DJP",      "asset_class": "COMMODITIES",      "currency": "USD"},
    {"ticker": "SPY",      "asset_class": "US_EQUITY",        "currency": "USD"},
    {"ticker": "^IRX",     "asset_class": "RISK_FREE",        "currency": "USD"},
    {"ticker": "^VIX",     "asset_class": "VOLATILITY",       "currency": "USD"},
    {"ticker": "CHFUSD=X", "asset_class": "FX",               "currency": "USD"},
]
```

---

## Phase 1bis — LLM Abstraction
*Estimated: 0.5 day*

(unchanged — see prior version of investment-TASKS.md / investment-ARCHITECTURE.md)

---

## Phase 1ter — UC0 Seed (graph + corpus + first snapshot)
*Estimated: 1 day*

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

### Task 1ter.2 — Regime seed (5 regimes, no deflation as primary)

```python
REGIMES = [
    {"id": "rg-risingG-fallingI", "name": "rising-growth-falling-inflation",
     "framework_id": "4seasons", "aliases": [], "tags": [],
     "is_current": False, "confidence": 0,
     "trace": "Goldilocks regime."},
    {"id": "rg-risingG-risingI",  "name": "rising-growth-rising-inflation",
     "framework_id": "4seasons", "aliases": ["overheating"], "tags": [],
     "is_current": False, "confidence": 0,
     "trace": "Late-cycle overheating."},
    {"id": "rg-fallingG-risingI", "name": "falling-growth-rising-inflation",
     "framework_id": "4seasons", "aliases": ["stagflation"], "tags": [],
     "is_current": False, "confidence": 0,
     "trace": "Stagflation. Alias preserved for legacy refs."},
    {"id": "rg-fallingG-fallingI","name": "falling-growth-falling-inflation",
     "framework_id": "4seasons", "aliases": [], "tags": [],
     "is_current": False, "confidence": 0,
     "trace": "Disinflation/recession; deflation can layer as tag."},
    {"id": "rg-uncertain",        "name": "uncertain",
     "framework_id": "4seasons", "aliases": [], "tags": [],
     "is_current": False, "confidence": 0,
     "trace": "Contradictory or straddled indicators."},
]
```

### Task 1ter.3 — Invariant seed (6 minimum, status=integrated)

```python
INVARIANTS = [
    {"id": "inv-inflation-persistence-tips",
     "title": "Persistent inflation favors TIPS, commodities, and gold",
     "source": "corpus", "author_weight": "dalio", "status": "integrated",
     "weight_initial": 0.85, "floor_weight": 0.40,
     "trace": "Dalio Principles; chapter on inflation hedges."},
    {"id": "inv-falling-growth-duration",
     "title": "Falling growth favors duration and cash-like defense",
     "source": "corpus", "author_weight": "dalio", "status": "integrated",
     "weight_initial": 0.80, "floor_weight": 0.40,
     "trace": "Dalio Principles; recession playbook."},
    {"id": "inv-rising-growth-equities",
     "title": "Rising growth favors equity exposure",
     "source": "corpus", "author_weight": "dalio", "status": "integrated",
     "weight_initial": 0.80, "floor_weight": 0.40,
     "trace": "Standard cycle finance."},
    {"id": "inv-liquidity-tightening-risk",
     "title": "Tightening global liquidity pressures risk assets",
     "source": "corpus", "author_weight": "marks", "status": "integrated",
     "weight_initial": 0.75, "floor_weight": 0.35,
     "trace": "Howard Marks memos on cycles and liquidity."},
    {"id": "inv-liquidity-easing-risk",
     "title": "Easing global liquidity supports risk assets",
     "source": "corpus", "author_weight": "marks", "status": "integrated",
     "weight_initial": 0.75, "floor_weight": 0.35,
     "trace": "Howard Marks memos on cycles and liquidity."},
    {"id": "inv-diversification-drawdown",
     "title": "Diversification lowers drawdown but dilutes upside",
     "source": "corpus", "author_weight": "dalio", "status": "integrated",
     "weight_initial": 0.70, "floor_weight": 0.40,
     "trace": "Dalio Principles; All Weather chapter."},
]
```

### Task 1ter.4 — Strategy seed (4 strategies, all enabled)

```python
STRATEGIES = [
    {"id": "4seasons",
     "title": "4 Seasons Dalio",
     "base_strategy": "4seasons",
     "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 65,
     "conditions": "applicable to all regimes AND VIX < 30",
     "benchmark": "^GSPC",
     "trace": "Risk parity baseline."},
    {"id": "permanent",
     "title": "Permanent Portfolio Browne",
     "base_strategy": "permanent",
     "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 55,
     "conditions": "25/25/25/25 AND uncertainty_score > 0.6",
     "benchmark": "^GSPC",
     "trace": "Simplicity baseline; low historical drawdown."},
    {"id": "barbell",
     "title": "Barbell Taleb",
     "base_strategy": "barbell",
     "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 45,
     "conditions": "tail risk elevated AND VIX > 25",
     "benchmark": "^GSPC",
     "trace": "85% safety + 15% convexity."},
    {"id": "momentum-macro",
     "title": "Momentum Macro",
     "base_strategy": "momentum-macro",
     "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 50,
     "conditions": "clear stable regime AND momentum_signal > 0",
     "benchmark": "^GSPC",
     "trace": "Dynamic rotation by detected regime."},
]

# BACKED_BY edges (minimum):
BACKED_BY_EDGES = [
    ("4seasons",       "inv-diversification-drawdown"),
    ("4seasons",       "inv-inflation-persistence-tips"),
    ("permanent",      "inv-diversification-drawdown"),
    ("barbell",        "inv-falling-growth-duration"),
    ("momentum-macro", "inv-rising-growth-equities"),
    ("momentum-macro", "inv-liquidity-easing-risk"),
]
```

### Task 1ter.5 — Scenario seed (3 per Strategy)

```python
SCENARIOS = [
    # 4seasons
    {"id": "sc-4s-bull", "strategy_id": "4seasons", "name": "bull",
     "probability": 35, "probability_d7": 35,
     "triggers": ["CPI<2.5", "Fed dovish", "PMI>52"],
     "target_allocation": {"SPY": 35, "TLT": 25, "GLD": 15, "TIP": 15, "DJP": 5, "cash": 5},
     "currency": "USD", "trace": "Goldilocks scenario for 4 Seasons."},
    {"id": "sc-4s-base", "strategy_id": "4seasons", "name": "base",
     "probability": 45, "probability_d7": 45,
     "triggers": ["CPI 2.5-3.5", "Fed pause"],
     "target_allocation": {"SPY": 30, "TLT": 30, "GLD": 10, "TIP": 20, "DJP": 7.5, "cash": 2.5},
     "currency": "USD", "trace": "Base case for 4 Seasons."},
    {"id": "sc-4s-bear", "strategy_id": "4seasons", "name": "bear",
     "probability": 20, "probability_d7": 20,
     "triggers": ["VIX>25", "CPI>4 AND PMI<48"],
     "target_allocation": {"TIP": 30, "GLD": 25, "DJP": 15, "SPY": 10, "TLT": 10, "cash": 10},
     "currency": "USD", "trace": "Stagflation/stress scenario."},
    # ... 9 more for permanent, barbell, momentum-macro
]
```

### Task 1ter.6 — Portfolio seed (6-10 portfolios, exactly one live=true)

```python
PORTFOLIOS = [
    {"id": "4s-balanced-defender",
     "name": "4 Seasons Balanced Defender",
     "framework_id": "4seasons",
     "live": True, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"TIP": 20, "TLT": 30, "GLD": 10, "DJP": 7.5, "SPY": 30, "cash": 2.5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 97.5,
     "trace": "Initial defender — standard 4 Seasons balanced."},
    {"id": "4s-stagflation-defensive",
     "name": "4 Seasons Stagflation Defensive",
     "framework_id": "4seasons",
     "live": False, "enabled": True,
     "currency": "CHF", "benchmark": "60/40-USD",
     "allocation": {"TIP": 30, "GLD": 25, "DJP": 15, "SPY": 10, "TLT": 10, "cash": 10},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 97.5,
     "trace": "Designed for falling-growth-rising-inflation."},
    {"id": "4s-rising-growth-equities",
     "name": "4 Seasons Rising-Growth Equity Tilt",
     "framework_id": "4seasons",
     "live": False, "enabled": True,
     "allocation": {"SPY": 50, "TLT": 15, "GLD": 10, "TIP": 15, "DJP": 5, "cash": 5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 50.0,
     "phase": "accumulation", "fx_usd_exposure": 100.0,
     "trace": "Designed for rising-growth quadrants."},
    {"id": "4s-falling-growth-defensive",
     "name": "4 Seasons Falling-Growth Defensive",
     "framework_id": "4seasons",
     "live": False, "enabled": True,
     "allocation": {"TLT": 40, "IEF": 20, "GLD": 15, "SPY": 15, "cash": 10},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 95.0,
     "trace": "Designed for falling-growth-falling-inflation."},
    {"id": "permanent-balanced",
     "name": "Permanent Portfolio Balanced",
     "framework_id": "permanent",
     "live": False, "enabled": True,
     "allocation": {"SPY": 25, "TLT": 25, "GLD": 25, "cash": 25},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 30.0,
     "phase": "accumulation", "fx_usd_exposure": 75.0,
     "trace": "Browne 25/25/25/25; framework-neutral."},
    {"id": "barbell-defensive",
     "name": "Barbell Taleb Defensive",
     "framework_id": "4seasons",
     "live": False, "enabled": True,
     "allocation": {"IEF": 70, "TLT": 15, "SPY": 15},
     "max_drawdown_rule": -10.0, "max_single_asset_pct": 70.0,
     "phase": "accumulation", "fx_usd_exposure": 100.0,
     "trace": "85% safe + 15% convex."},
    {"id": "momentum-macro-rotation",
     "name": "Momentum Macro Rotation",
     "framework_id": "4seasons",
     "live": False, "enabled": True,
     "allocation": {"SPY": 40, "TLT": 30, "GLD": 15, "DJP": 10, "cash": 5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 50.0,
     "phase": "accumulation", "fx_usd_exposure": 100.0,
     "trace": "Dynamic; current allocation reflects last regime."},
]

# HOLDS edges (Portfolio → Strategy, primary=true):
HOLDS_EDGES = [
    ("4s-balanced-defender",      "4seasons",        True),
    ("4s-stagflation-defensive",  "4seasons",        True),
    ("4s-rising-growth-equities", "4seasons",        True),
    ("4s-falling-growth-defensive","4seasons",       True),
    ("permanent-balanced",        "permanent",       True),
    ("barbell-defensive",         "barbell",         True),
    ("momentum-macro-rotation",   "momentum-macro",  True),
]

# DESIGNED_FOR edges (Portfolio → Regime, only when applicable):
DESIGNED_FOR_EDGES = [
    ("4s-stagflation-defensive",   "rg-fallingG-risingI",  "Designed for stagflation regime."),
    ("4s-rising-growth-equities",  "rg-risingG-risingI",   "Designed for rising-growth quadrants."),
    ("4s-rising-growth-equities",  "rg-risingG-fallingI",  "Designed for rising-growth quadrants."),
    ("4s-falling-growth-defensive","rg-fallingG-fallingI", "Designed for disinflation/recession."),
    # 4s-balanced-defender, permanent-balanced, barbell-defensive,
    # momentum-macro-rotation: no DESIGNED_FOR edge (framework-neutral or dynamic).
]
```

### Task 1ter.7 — Time-series and snapshot bootstrap

```python
# 1. MarketData TS: 5y backfill for all ALLOWED_TICKERS.
#    For each ticker, after backfill:
#      compute level (= close, optionally smoothed),
#      speed = first derivative over derivative_lookback_short days,
#      acceleration = second derivative over derivative_lookback_short days.
#    GLOBAL_LIQUIDITY is the composite of M2 + major CB balance sheets
#    (formula in src/investment/market/liquidity.py).
#
# 2. First regime detection: run RegimeDetector against most recent 12M.
#    Set is_current=true on the matching Regime vertex.
#
# 3. Backtests: for each (Strategy × Regime) cell where coverage >= min_backtest_periods,
#    create Backtest vertex + TESTED_IN + IN_REGIME edges,
#    populate Regime -[FAVORS]-> Strategy with rolling ratios.
#
# 4. PortfolioNAV TS: for each Portfolio, synthesize NAV from
#    NAV(t) = Σ_asset allocation[asset] × close[asset](t) / close[asset](0).
#    Compute daily_return, sharpe_rolling (252d), sortino_rolling (252d),
#    calmar_rolling (756d), drawdown.
#
# 5. portfolio_weekly_snapshot: one row per enabled Portfolio for the seed
#    date; rank by sortino_rolling DESC tiebroken by calmar_rolling DESC;
#    fill market_context, gap_to_defender (null for live row), recommendation='maintain'.
#
# 6. Emit SeedEvent → Event TS with full inventory and schema_version.
```

Run: `uv run python -m investment.seed`
Idempotent (UPSERT on all vertices and edges).

**Done when:** schema populated; first snapshot row visible; SeedEvent in Event TS.

---

## Phase 2 — Market Data (daily)
*Estimated: 1 day*

### Task 2.1 — Market fetcher

**`src/investment/market/fetcher.py`** — Yahoo Finance + FRED.

```python
class MarketFetcher:
    TICKERS = {
        "TIP": "US TIPS", "TLT": "Long US Treasuries", "GLD": "Gold",
        "DJP": "Commodities", "SPY": "S&P 500", "IEF": "US Treasuries 7-10y",
        "CHFUSD=X": "CHF/USD FX (display only)",
        "^IRX": "3M T-Bill (risk-free)", "^VIX": "VIX market stress",
    }
    FRED_SERIES = {
        "CPIAUCSL": "CPI US YoY",
        "T10Y2Y": "Yield curve 10y-2y",
        "VIXCLS": "VIX",
        "UMCSENT": "Consumer sentiment",
        "UNRATE": "Unemployment rate",
    }
    LIQUIDITY_COMPONENTS = {
        "M2SL": "US M2 money supply",
        "WALCL": "Fed balance sheet",
        # ECB and BOJ via separate fetcher or manual pull
    }

    async def fetch_and_store(self, db: InvestmentDB, start: date = None) -> int:
        """5y backfill + daily incremental.
           time.sleep(0.5) between tickers to respect YF rate limits."""
```

### Task 2.2 — Derivatives + GLOBAL_LIQUIDITY composite

**`src/investment/market/derivatives.py`**
```python
def compute_derivatives(close_series: pd.Series, lookback: int) -> dict:
    """Returns level, speed, acceleration for the latest point."""
```

**`src/investment/market/liquidity.py`**
```python
def compute_global_liquidity(components: dict[str, pd.Series]) -> pd.Series:
    """Z-score per component, equal-weighted sum, persisted as MarketData
       with ticker='GLOBAL_LIQUIDITY' and asset_class='GLOBAL_LIQUIDITY'."""
```

### Task 2.3 — Regime detector

**`src/investment/market/regime.py`** — uses level/speed/acceleration + global
liquidity tags. Emits MarketEvent → Event TS BEFORE updating Regime vertex.

**Done when:** `detector.detect(db)` creates/updates a Regime vertex with
`is_current=true` and produces a global_liquidity tag where appropriate.

---

## Phase 3 — Corpus Parser
*Estimated: 1 day*

(unchanged structurally — see prior version; ensure SUPPORTS edges connect to
the seeded invariants when similarity floor is met.)

---

## Phase 4 — Planner
*Estimated: 2 days*

(unchanged structurally — see prior version. Key Cypher to fix:)

```cypher
-- FAVORS Cypher (was Regime → Portfolio; now Regime → Strategy):
MATCH (r:Regime {is_current:true})-[f:FAVORS]->(s:Strategy)
WHERE s.enabled=true
RETURN s, f.sortino_rolling, f.sharpe_rolling, f.calmar_rolling
ORDER BY f.sortino_rolling DESC

-- Defender query:
MATCH (p:Portfolio {live:true, enabled:true})
OPTIONAL MATCH (p)-[h:HOLDS {primary:true}]->(s:Strategy)
OPTIONAL MATCH (p)-[:DESIGNED_FOR]->(r:Regime)
RETURN p, s.id AS primary_strategy, r.id AS designed_regime

-- Ranking query (from portfolio_weekly_snapshot):
SELECT * FROM portfolio_weekly_snapshot
WHERE date = $today
ORDER BY rank ASC
```

---

## Phase 5 — Worker
*Estimated: 1 day*

(unchanged structurally — see prior version. Skills renamed:)

```
skill-evaluate-strategy.md
skill-rank-portfolios.md
skill-compare-vs-defender.md       ← replaces skill-propose-adaptation.md
skill-interpret-invariants.md
```

The Worker uses `sharpe_rolling`/`sortino_rolling`/`calmar_rolling` from
context. Never recalculates. V1 emits paper-mode Proposals via the
`compare-vs-defender` skill.

---

## Phase 5bis — Mechanical Jobs
*Estimated: 1 day*

(unchanged structurally. Notes:)
- `ratios.py` writes `sharpe_rolling` / `sortino_rolling` / `calmar_rolling` everywhere.
- `snapshots.py` is the new module writing `portfolio_weekly_snapshot` rows.
- `learning.py` is V2-only; stubbed in V1.

---

## Phase 6 — Writeback
*Estimated: 1 day*

(unchanged structurally. Key V1 path:)

```python
# V1 proposals — Proposal vertex (not Adaptation)
for proposal in post_result.proposals:
    # Concentration check on the implied challenger allocation
    challenger = await db.query("cypher",
        f"MATCH (p:Portfolio {{id:'{proposal.challenger_id}'}}) RETURN p")
    p = challenger[0]["p"]
    defender = await db.query("cypher",
        f"MATCH (p:Portfolio {{live:true}}) RETURN p")
    if self._violates_concentration(p["allocation"], p["max_single_asset_pct"]):
        await self.telegram.send(f"⛔ Proposal blocked: concentration violated\n{proposal.reasoning}")
        continue
    await db.append_ts("Event", now(), {"type": "ProposalEmitted", ...})
    pid = await db.create_vertex("Proposal", {
        **proposal.dict(), "user_response": "pending"
    })
    await self.telegram.send_proposal(proposal, proposal_id=pid)
```

V2 adaptation flow (auto-validation timer) remains as documented previously
but is not active in V1.

---

## Phase 7 — Main process + APScheduler
*Estimated: 0.5 day*

```python
# main.py — UC1 onwards. UC0 is run separately via `python -m investment.seed`.

async def main():
    db = InvestmentDB(settings.arcade_db_path)
    await db.init_schema()
    # Refuse to start if seed has not run:
    if not await db.query("cypher", "MATCH (f:Framework {id:'4seasons'}) RETURN f"):
        raise RuntimeError("Seed not run. Execute `uv run python -m investment.seed` first.")

    # ... scheduler setup as before, no UC0 here
```

---

## Phase 8 — Integration tests
*Estimated: 0.5 day*

```python
async def test_uc0_seed_idempotent():
    # Run seed twice → no duplicate vertex; SeedEvent appears twice

async def test_schema_complete():
    # 13 vertex + 13 edge + 4 TS types created

async def test_corpus_ingestion():
    # PDF → Document + Passages with embeddings; vector search works

async def test_regime_detection_with_derivatives():
    # MarketData has level/speed/acceleration; detector uses acceleration
    # to flag early shift

async def test_portfolio_ranking():
    # All enabled portfolios ranked, including live defender
    # gap_to_defender is null for live, non-null for others

async def test_favors_targets_strategy():
    # Regime -[FAVORS]-> Strategy (not Portfolio) for all FAVORS rows

async def test_holds_primary():
    # Each Portfolio has exactly one HOLDS with primary=true

async def test_proposal_gate():
    # Challenger beats defender on Sortino+Calmar → Proposal vertex created
    # Concentration check passes
    # Telegram notification sent

async def test_concentration_block():
    # Proposed challenger violates max_single_asset_pct → blocked, no vertex

async def test_agent_innovation():
    # Worker emits Invariant source=agent-discovery
    # status=proposed, Telegram notification BEFORE commit

async def test_event_ts_precedes_commit():
    # For every Signal/Proposal/Invariant/Regime change, Event TS row exists
    # with timestamp <= vertex commit timestamp
```

---

## Notes for Claude Code

1. **arcadedb-embedded ARM64** — wheel since 26.1.1.post3.
2. **Single process** — `main.py` only. `seed.py` is the UC0 one-shot.
3. **Sequential writes** — all ArcadeDB writes in the same asyncio thread.
4. **Mandatory trace** — every `create_vertex` with empty `trace` raises `ValueError`.
5. **Event TS first** — every Event TS append must precede vertex/edge commit.
6. **Risk-free rate** — fetch ^IRX daily; use in Sharpe/Sortino.
7. **Calmar window** — 756 trading days (36M). From `system_thresholds`.
8. **Currency** — USD for ratios. CHFUSD=X for user display only.
9. **Rolling suffix** — `sharpe_rolling`/`sortino_rolling`/`calmar_rolling`
   everywhere (Portfolio, Backtest, FAVORS, PortfolioNAV).
10. **Recency formula** — `max(0.5, 0.5 + 0.5 * exp(-days_since / 365))` in V1.
11. **Floor on Invariant vertex** — set at creation from source/author defaults.
12. **Shift threshold** — `abs(prob - prob_d7) > 10` → context, not auto-action in V1.
13. **V2 Auto-validation** — deferred; V1 never auto-applies.
14. **Innovation status** — never `status:integrated` without `user_validated=True`.
15. **Concentration check** — Writeback blocks if `max_single_asset_pct` violated.
16. **YFinance rate limit** — `time.sleep(0.5)` between tickers.
17. **Ollama already installed** — verify `ollama list`, do not reinstall.
18. **FAVORS direction** — Regime → Strategy. Not Portfolio.
19. **DESIGNED_FOR** — Portfolio → Regime, nullable.
20. **HOLDS** — Portfolio → Strategy with `primary BOOLEAN`. No `strategy_id`
    scalar on Portfolio.

**Total estimated effort:** ~8 days for MVP (UC0 = ~1 day, rest unchanged).
