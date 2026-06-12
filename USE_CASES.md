# USE_CASES.md — Investment Agent MVP

See REVISION_NOTES.md for V1 scope and core concepts.

The agent is a black box. These are the observable processes. The agent runs
autonomously. User receives notifications and can amend via UC9.

See IMPROVEMENTS.md for deferred UCs (UC10 monthly scorecard).

---

## Flow Overview

```
One-time (manual)
  UC0  Seed                 → SeedEvent

Daily (mechanical only — no LLM)
  UC1  Market Feed          → MarketData TS (+ level/speed/acceleration)

Weekly (Monday — run in sequence)
  UC2  Market Valuation     → MarketEvent
  UC3  Knowledge Search     → KnowledgeSearchEvent
  UC4  Knowledge Curation   → KnowledgeEvent
  UC5  Knowledge Storage    → DB updated
  UC6  Portfolio Valuation  → ValuationEvent
  UC7  Portfolio Ranking    → RankingEvent + portfolio_weekly_snapshot
  UC8  Proposal Detection   → ProposalEvent + Proposal vertex (or nothing)
  —    Weekly digest        → Telegram (09:30 — renders UC7/UC8 output; always sent)

On demand
  UC9  Chatbot              → UserDecisionEvent → may re-trigger UC8
```

---

## Event Time-Series

All UCs except UC1 (pure TS write, no Event) append Events to the Event TS.
**Every Event TS append must precede the corresponding vertex/edge commit.**
UC8 reads this TS weekly to detect proposals.

---

## UC0 — Seed
**Trigger:** one-shot CLI `uv run python -m investment.seed`.
**LLM:** none.
**Idempotent:** UPSERT on every vertex; safe to re-run.

**What it does:**

```
1.  SQL bootstrap:
    - user_profile (currency, drawdown rule, concentration cap)
    - allowed_tickers (TIP, TLT, GLD, DJP, SPY, IEF, CHFUSD=X, ^IRX, ^VIX, ...)
    - system_thresholds (regime thresholds, calmar window 756d,
      recency half-life 365d, vector similarity 0.35, ...)
    - invariant_author_config (dalio/marks/corpus-other/system
      floors and initial weight bands — keyed by `author` field on Invariant)

2.  Framework vertex:
    - '4seasons' enabled=true

3.  RegimeType vertices (5), seeded once and never mutated:
    - rising-growth-falling-inflation
    - rising-growth-rising-inflation
    - falling-growth-rising-inflation  (aliases: ['stagflation'])
    - falling-growth-falling-inflation
    - uncertain
    Concrete Regime instances are created dynamically by `detect_regime()`
    (id convention `<regimeType.alias>-<start_date>`, e.g.
    `stagflation-2026-05-01`).
    Tags reserved on instances: 'deflation', 'liquidity-tightening',
                   'liquidity-easing', 'market-stress'

4.  Corpus seed (optional, if PDFs are in /data/investment/sources/corpus):
    - Document + Passage vertices with embeddings
    - SUPPORTS edges built from passage-invariant matches above similarity floor

5.  Invariant vertices (status='integrated', seed minimum):
    - inflation-persistence-tips     (dalio, weight 0.85, floor 0.40)
    - falling-growth-duration         (dalio, weight 0.80, floor 0.40)
    - rising-growth-equities          (dalio, weight 0.80, floor 0.40)
    - liquidity-tightening-risk       (marks, weight 0.75, floor 0.35)
    - liquidity-easing-risk           (marks, weight 0.75, floor 0.35)
    - diversification-drawdown        (dalio, weight 0.70, floor 0.40)

6.  Strategy vertices (4), enabled=true:
    - 4seasons, permanent, barbell, momentum-macro
    - Conditions include ≥1 dimension orthogonal to regime thresholds
    - BACKED_BY edges to relevant invariants

7.  Scenario vertices (3 per Strategy = 12 total), bull/base/bear with
    initial probabilities summing to 100; probability_d7 = probability
    + HAS_SCENARIO edges

8.  Portfolio vertices (6-10), exactly one defender=true:
    - 4s-balanced-defender              (defender=true)
    - 4s-stagflation-defensive
    - 4s-rising-growth-equities
    - 4s-falling-growth-defensive
    - permanent-balanced
    - barbell-defensive
    - momentum-macro-rotation
    + HOLDS edges (primary=true to main strategy)
    + DESIGNED_FOR edges to RegimeType (nullable for framework-neutral portfolios)

9.  MarketData TS backfill:
    - 5y history for all allowed_tickers from Yahoo Finance + FRED
    - Computed columns: level, speed (1st derivative), acceleration (2nd)
    - GLOBAL_LIQUIDITY composite from M2 + major CB balance sheets

10. First regime detection:
    - Apply detector to most recent 12 months of data
    - Set is_current=true on the matching Regime vertex

11. Initial Backtests:
    - For each (Strategy, RegimeType) cell where data coverage ≥ min_backtest_periods
    - Backtest vertex with USD sharpe_rolling, sortino_rolling, calmar_rolling
    - TESTED_IN + IN_REGIME (→ historical Regime instance) edges
    - FAVORS edges from RegimeType to Strategy with strategy-level rolling
      indicators (synthetic backtest of prescribed allocation)

12. PortfolioNAV TS synthetic backfill:
    - For each Portfolio: NAV(t) = Σ allocation[asset] × close[asset](t) / close[asset](0)
    - daily_return, sharpe_rolling, sortino_rolling, calmar_rolling,
      drawdown, vs_benchmark

13. First portfolio_weekly_snapshot:
    - Rank all enabled Portfolios including the defender
    - market_context filled from current regime + global liquidity state
    - gap_to_defender computed for each non-defender entry
    - recommendation = 'maintain' on day zero

14. SeedEvent → Event TS with full inventory:
    payload = {
      frameworks, regimes, invariants, strategies, scenarios, portfolios,
      market_data_rows, backtests, snapshot_date, schema_version
    }
```

**Done when:** Worker can run its first weekly cycle (UC2→UC8) without missing
data, and the digest renders a non-empty defender row.

**User action:** None after running the command.

---

## UC1 — Market Feed
**Trigger:** Daily cron (06:30).
**What it does:** Fetches OHLCV from Yahoo Finance and macro series from FRED.
Computes `level`, `speed`, `acceleration` for each series. Appends to
MarketData TS. Includes ^IRX (3M T-Bill risk-free rate) and the
`GLOBAL_LIQUIDITY` composite.
**Output:** → MarketData TS. No Event emitted.
**User action:** None.

---

## UC2 — Market Valuation
**Trigger:** Weekly cron (Monday 08:00).
**What it does:** Reads MarketData TS. Produces structured market snapshot:
current regime (4 Seasons), benchmark performance, macro indicators with
level/speed/acceleration, global liquidity state. The Regime vertex itself
(`is_current`) is owned and maintained by the daily 06:50 `detect_regime()`
job — UC2 reads it, it does not update it.
**Output:** MarketEvent → Event TS.

```json
{
  "framework": "4seasons",
  "regime": "falling-growth-rising-inflation",
  "regime_aliases": ["stagflation"],
  "confidence": 78,
  "changed": false,
  "derivatives": {
    "CPI_YOY": {"level": 3.1, "speed_3m": 0.3, "acceleration_3m": 0.2},
    "PMI":     {"level": 47.2, "speed_3m": -1.1, "acceleration_3m": -0.4}
  },
  "global_liquidity": {"state": "tight", "trend": "tightening"},
  "benchmarks": {"SPX_1w": "-1.2%", "BOND_1w": "+0.3%"},
  "tags_active": ["liquidity-tightening"]
}
```

**User action:** None.

---

## UC3 — Knowledge Search
**Trigger:** Weekly cron (Monday, after UC2).
**What it does:** Reads RSS feeds + user deposits from the previous 7 days.
Deposits raw content in `inbox/`.
**Output:** KnowledgeSearchEvent → Event TS.

In V1: RSS + user deposits only. YouTube/X/podcasts deferred (IMPROVEMENTS I-9).
**User action:** None. User can deposit documents anytime.

---

## UC4 — Knowledge Curation
**Trigger:** Weekly cron (Monday, after UC3).
**What it does:** Processes Documents/Passages ingested since the last cycle.
Raw inbox parsing (parse + chunk + embed → Document/Passage vertices +
similarity-based SUPPORTS edges) runs nightly at 02:00 with no LLM; UC4 is
the weekly LLM step that turns new passages into invariant updates.

**Curation (autonomous):** updating confirmation counts, enriching
description/example, adding SUPPORTS edges, recalculating `weight_effective`
on existing integrated Invariants. No user validation required.

**Innovation (requires user validation):** creating a new Invariant
(`source=agent-discovery`, `status=proposed`), proposing a new metric or
schema element. Persisted as `status=proposed`, with a Telegram notification
in the same cycle; never `integrated` without user validation.

**Output:** KnowledgeEvent → Event TS.
**User action:** Validation required for proposed innovations.

---

## UC5 — Knowledge Storage
**Trigger:** Event-driven — any event carrying data to persist.
**What it does:** Planner Post structures and persists into ArcadeDB.
**Order:** Event TS append → graph vertex → edges → FTS → vector → SQL.
**Output:** ArcadeDB updated.
**User action:** None.

---

## UC6 — Portfolio Valuation
**Trigger:** Weekly cron (Monday, before Worker).
**What it does:** Calculates USD `sharpe_rolling`, `sortino_rolling`,
`calmar_rolling`, `max_drawdown`, `volatility`, plus cumulative
`return_3m / 6m / 1y / 3y / 5y` for all enabled portfolios, including the
defender. Risk-free rate = 3M T-Bill. Rolling indicator window = 36M.
Updates each `Portfolio` vertex and appends to PortfolioNAV TS.
**Output:** ValuationEvent → Event TS.

```json
{
  "portfolios": [
    {
      "id": "4s-stagflation-defensive",
      "defender": false,
      "allocation": {"TIP": 30, "GLD": 25, "DJP": 15, "SPY": 10, "TLT": 10, "cash": 10},
      "sharpe_rolling": 0.71, "sortino_rolling": 1.18, "calmar_rolling": 1.9,
      "max_drawdown": -0.062, "volatility": 0.084,
      "return_3m": 0.038, "return_6m": 0.072, "return_1y": 0.143,
      "return_3y": 0.321, "return_5y": 0.486
    }
  ]
}
```

It is the **portfolio** that is valued, not the strategy. Strategies are
contextualized via FAVORS edges (updated weekly), not directly valued.

---

## UC7 — Portfolio Ranking
**Trigger:** Weekly cron (Monday, after UC6).
**What it does:** Ranks all `Portfolio(enabled=true)`, including the live
defender. The current regime does not filter the ranking universe — it is
context. Every ranked portfolio includes its concrete allocation.
**Output:** RankingEvent → Event TS + one row per portfolio in
`portfolio_weekly_snapshot`.

```json
{
  "market_context": {
    "framework": "4seasons",
    "regime": "falling-growth-rising-inflation",
    "aliases": ["stagflation"],
    "confidence": 72,
    "global_liquidity": "tightening",
    "derivatives": {"inflation_speed": "+", "growth_acceleration": "-"}
  },
  "ranking": [
    {
      "rank": 1,
      "portfolio_id": "4s-stagflation-defensive",
      "defender": false,
      "allocation": {"TIP": 30, "GLD": 25, "DJP": 15, "SPY": 10, "TLT": 10, "cash": 10},
      "sharpe_rolling": 0.71, "sortino_rolling": 1.18,
      "calmar_rolling": 1.90, "max_drawdown": -0.062
    }
  ]
}
```

---

## UC8 — Proposal Detection
**Trigger:** Weekly Worker cycle (Monday 09:00).
**What it does:** Compares the defender with top challengers. V1 never
auto-applies. It may emit a Proposal vertex when a challenger meets the gate:
1. challenger outranks the defender in `portfolio_weekly_snapshot`;
2. `sortino_rolling` gap ≥ `proposal_sortino_gap_min` (system_thresholds, 0.02);
3. challenger `calmar_rolling` ≥ 1.5 (Calmar selection threshold);
4. concentration constraints pass (`max_single_asset_pct`);
5. meaningful allocation change vs the defender.
A challenger may pass with a worse Calmar or drawdown than the defender as
long as it stays above the absolute Calmar threshold — the digest must then
flag the weaker downside profile (see EXAMPLE.md Step 8B).

Inputs:
```
MarketEvent       → current regime + global liquidity context
KnowledgeEvent    → invariant changes
ValuationEvent    → portfolio metrics
RankingEvent      → defender rank and challenger gap
UserDecisionEvent → user amendments
```

If a Proposal is warranted:
- Compute defender-vs-challenger gap.
- Verify metrics, concentration, turnover.
- Create `Proposal` vertex (`recommendation` = 'paper-test' or 'monitor').
- Send Telegram digest with the proposal payload.
- No automatic allocation change in V1.

If not warranted:
- Recommendation = 'maintain'; no Proposal vertex created.
- Snapshot row still written.

**Output:** ProposalEvent → Event TS + Proposal vertex, or nothing.

---

## UC9 — Chatbot
**Trigger:** User message (Telegram).
**What it does:** Conversational interface. Any decision stored via UC5.
Can trigger a new UC8 cycle if the decision impacts the current state.
User can disable/re-enable strategies, change drawdown limits, challenge
theses, query past snapshots.

```
Examples:
  "Does your TIPS thesis hold if the Fed pivots in Q3?"
  "Reduce max drawdown to -10% from now"
  "Disable momentum-macro strategy"
  "How has the defender ranked over the last 8 weeks?"
```

**Output:** UserDecisionEvent → Event TS.
**User action:** This IS the user action UC.

---

## Summary Table

| #  | UC                  | Trigger             | Output                                | Frequency        |
|----|---------------------|---------------------|---------------------------------------|------------------|
| 0  | Seed                | Manual CLI          | SeedEvent + full DB bootstrap         | Once at install  |
| 1  | Market Feed         | Daily cron          | MarketData TS                         | Daily            |
| 2  | Market Valuation    | Weekly cron         | MarketEvent                           | Weekly           |
| 3  | Knowledge Search    | Weekly cron         | KnowledgeSearchEvent                  | Weekly           |
| 4  | Knowledge Curation  | Weekly cron         | KnowledgeEvent                        | Weekly           |
| 5  | Knowledge Storage   | Any event with data | —                                     | Event-driven     |
| 6  | Portfolio Valuation | Weekly cron         | ValuationEvent                        | Weekly           |
| 7  | Portfolio Ranking   | Weekly cron         | RankingEvent + snapshot               | Weekly           |
| 8  | Proposal Detection  | Weekly Worker cycle | ProposalEvent + Proposal vertex / —   | Weekly           |
| 9  | Chatbot             | User message        | UserDecisionEvent                     | On demand        |

---

## What the Agent Never Does Without User Awareness

- Execute an allocation change in V1. V1 only ranks, digests, and proposes
  paper-mode comparisons via Proposal vertices.
- Create a new Invariant `source=agent-discovery` without prior Telegram
  notification.
- Change user rules (drawdown limit, concentration limit, strategy enabled)
  without UC9.
- Persist a schema extension without explicit user validation.
