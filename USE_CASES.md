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
  —    Shadow replay        → ReplayEvent + replay_report (Phase 9 —
                              meta-backtest of the mechanical pipeline;
                              gates go-live, re-runnable after threshold
                              changes)

Daily (mechanical only — no LLM)
  UC1  Market Feed          → MarketData TS (+ level/speed/acceleration)

Weekly (Monday — one sequential chain: each step starts only after the
previous one succeeds; on failure the chain aborts, emits an ErrorEvent and
sends a Telegram alert. Times in CLAUDE.md are indicative.)
  UC2  Market Valuation     → MarketEvent
  UC3  Knowledge Search     → KnowledgeSearchEvent
  UC4  Knowledge Curation   → KnowledgeEvent
  UC5  Knowledge Storage    → DB updated (transverse mechanism, see below)
  UC6  Portfolio Valuation  → ValuationEvent
  UC7  Portfolio Ranking    → RankingEvent + portfolio_weekly_snapshot
  UC8  Proposal Detection   → ProposalEvent + Proposal vertex
                              (switch or reallocation), or nothing
  —    Weekly digest        → Telegram (09:30 — renders UC7/UC8 output; always sent)

On demand
  UC9  Chatbot              → UserDecisionEvent → may re-trigger UC8
                              (max 1 ad-hoc re-run per day)
```

---

## EventLog (append-only audit vertex — see DATA_MODELS.md)

Every UC that commits a vertex or edge appends to `EventLog` FIRST.
**Every EventLog append must precede the corresponding vertex/edge commit.**
Exemption: pure TS writes (UC1 market feed, daily NAV and scenario jobs) —
they create no vertex/edge, so no ordering constraint applies.
Daily jobs that DO touch vertices emit events: the 06:50 regime detector
emits `RegimeEvent` (only when the regime or its tags change) and the 02:00
inbox parser emits `IngestionEvent` (one per processed batch).
UC8 reads EventLog weekly to assemble its inputs.

---

## UC0 — Seed
**Trigger:** one-shot CLI `uv run python -m investment.seed`.
**LLM:** none.
**Idempotent:** UPSERT on every vertex; safe to re-run.

**What it does:**

```
1.  Document-type bootstrap (ArcadeDB document types, see DATA_MODELS.md):
    - user_profile (currency, BINDING drawdown rule, BINDING concentration cap)
    - allowed_tickers (ETFs + FRED macro series + composites, with
      source and transform columns — TIP, TLT, GLD, DJP, SPY, IEF,
      CHFUSD=X, ^IRX, ^VIX, CPIAUCSL, T10Y2Y, UNRATE, INDPRO, UMCSENT,
      GROWTH_COMPOSITE, GLOBAL_LIQUIDITY, ...)
    - system_thresholds (regime thresholds, calmar window 756d,
      recency half-life 365d, vector similarity 0.35, proposal gates for
      switch AND reallocation, proposal_expiry_days, ...)
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
    - Calls the SAME `CorpusIngester` used by the nightly 02:00 job
      (single pipeline: parse + chunk + embed → Document + Passage vertices)
    - SUPPORTS edges built from passage-invariant matches above similarity floor

4b. Initial curation pass (DEFAULT when a corpus is present; skip with
    `--no-curate` — the ONLY LLM step in UC0):
    - Runs the SAME curation runner as weekly UC4 (Task 5.3) over the whole
      corpus ingested in step 4, in batches of passages
    - Extracted invariant candidates are proposed with
      **author = Document.author tier** ('dalio' → floor 0.40,
      'marks' → 0.35, other → null/0.20) — NOT 'system'; 'system' is
      reserved for market-pattern discoveries (backtests, rankings)
    - status='proposed' → batch validation, interactively in the CLI at
      seed time (fallback: Telegram [YES]/[NO] per candidate)
    - Validated candidates → status='integrated', BACKED_BY/SUPPORTS edges
    - This is how a deposited book yields stable invariants at install time
      instead of waiting for weekly UC4 cycles

5.  Invariant vertices (status='integrated', seed minimum, hand-written —
    guaranteed baseline even when step 4b is skipped):
    - inflation-persistence-tips     (dalio, weight 0.85, floor 0.40)
    - falling-growth-duration         (dalio, weight 0.80, floor 0.40)
    - rising-growth-equities          (dalio, weight 0.80, floor 0.40)
    - liquidity-tightening-risk       (marks, weight 0.75, floor 0.35)
    - liquidity-easing-risk           (marks, weight 0.75, floor 0.35)
    - diversification-drawdown        (dalio, weight 0.70, floor 0.40)

6.  Strategy vertices (4), enabled=true:
    - four-seasons-rp, permanent-browne, barbell-taleb, momentum-macro
    - Conditions include ≥1 dimension orthogonal to regime thresholds,
      and every referenced indicator is computable from MarketData/Regime
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
    All seed allocations comply with the BINDING user caps
    (max_single_asset_pct 40, max_drawdown_pct -15); per-portfolio rules
    may only be stricter.
    + HOLDS edges (primary=true to main strategy)
    + DESIGNED_FOR edges to RegimeType (nullable for framework-neutral portfolios)

9.  MarketData TS backfill:
    - 25y history for macro/FRED series; ETFs from inception
      (SPY 1993, GLD/TLT/TIP 2002-04, DJP 2006, BIL 2007, ...)
    - Computed columns: level, speed (1st derivative), acceleration (2nd),
      per-series transforms per DATA_MODELS.md "MarketData semantics"
    - GROWTH_COMPOSITE and GLOBAL_LIQUIDITY composites computed and stored

10. Historical Regime materialization (NEW — prerequisite for backtests):
    - Run the regime detector over the FULL macro backfill (25y)
    - Create one Regime vertex per detected historical episode
      (is_current=false, end_date set), fill regime_history documents
    - Set is_current=true on the final (ongoing) instance

11. Initial Backtests:
    - For each (Strategy, RegimeType) cell where historical coverage ≥
      min_backtest_periods regime instances
    - Backtest vertex with USD sharpe_rolling, sortino_rolling, calmar_rolling
    - TESTED_IN + IN_REGIME (→ historical Regime instance) edges
    - FAVORS edges from RegimeType to Strategy with strategy-level rolling
      indicators (synthetic backtest of prescribed allocation, aggregated
      across all historical instances — n_periods now meaningful thanks to
      the 25y backfill)

12. PortfolioNAV TS synthetic backfill:
    - NAV per DATA_MODELS.md calculation conventions (constant weights,
      monthly rebalancing, cash accruing at ^IRX), from the date all
      constituents exist
    - daily_return, sharpe_rolling, sortino_rolling, calmar_rolling,
      drawdown, vs_benchmark

13. First portfolio_weekly_snapshot:
    - Rank all enabled Portfolios including the defender
    - market_context filled from current regime + global liquidity state
    - gap_to_defender computed for each non-defender entry
    - recommendation = 'maintain' on day zero

14. SeedEvent → EventLog with full inventory:
    payload = {
      frameworks, regimes (incl. historical count), invariants, strategies,
      scenarios, portfolios, market_data_rows, backtests, snapshot_date,
      schema_version
    }
```

**Done when:** Worker can run its first weekly cycle (UC2→UC8) without missing
data, and the digest renders a non-empty defender row.

**User action:** None after running the command.

---

## UC1 — Market Feed
**Trigger:** Daily cron (06:30).
**What it does:** Fetches prices from Yahoo Finance and macro series from FRED.
Applies per-series transforms (DATA_MODELS.md "MarketData semantics") and
computes `level`, `speed`, `acceleration` for each series. Appends to
MarketData TS. Includes ^IRX (3M T-Bill risk-free rate), the
`GROWTH_COMPOSITE` and the `GLOBAL_LIQUIDITY` composites.
**Output:** → MarketData TS. No EventLog row (pure TS write — no vertex/edge
committed, so the ordering invariant does not apply).
**User action:** None.

---

## UC2 — Market Valuation
**Trigger:** Weekly cron (Monday 08:00).
**What it does:** Reads MarketData TS. Produces structured market snapshot:
current regime (4 Seasons), benchmark performance, macro indicators with
level/speed/acceleration, global liquidity state. The Regime vertex itself
(`is_current`) is owned and maintained by the daily 06:50 `detect_regime()`
job — UC2 reads it, it does not update it.
**Output:** MarketEvent → EventLog.

```json
{
  "framework": "4seasons",
  "regime": "falling-growth-rising-inflation",
  "regime_aliases": ["stagflation"],
  "confidence": 78,
  "changed": false,
  "derivatives": {
    "CPI_YOY":          {"level": 3.1,  "speed": 0.3,  "acceleration": 0.2},
    "GROWTH_COMPOSITE": {"level": 97.2, "speed": -1.1, "acceleration": -0.4}
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
**Output:** KnowledgeSearchEvent → EventLog.

In V1: RSS + user deposits only. YouTube/X/podcasts deferred (IMPROVEMENTS I-9).
**User action:** None. User can deposit documents anytime.

---

## UC4 — Knowledge Curation
**Triggers (same runner, three callers):**
1. **Nightly, event-driven (02:15):** whenever the 02:00 inbox parser
   ingested ≥1 new Document, the curation runner processes it immediately —
   a deposited book yields its invariant candidates the next morning, not
   the next Monday. Knowledge extraction only — never decisions.
2. **Weekly cron (Monday, after UC3):** sweep over anything not yet curated
   + re-curation opportunities on existing invariants.
3. **UC0 seed batch** (step 4b, default): initial pass over the whole corpus.

**What it does:** Processes Documents/Passages ingested since the last run.
Raw inbox parsing (parse + chunk + embed → Document/Passage vertices +
similarity-based SUPPORTS edges) runs nightly at 02:00 with no LLM; the
curation runner is the LLM step that turns new passages into invariant
updates and candidates (CurationResult — see investment-TASKS.md Task 5.3).

**Curation (autonomous):** updating confirmation counts, enriching
description/example, adding SUPPORTS edges, recalculating `weight_effective`
on existing integrated Invariants. No user validation required.

**Innovation (requires user validation):** creating a new Invariant
(`status=proposed`), proposing a new metric or schema element. Persisted as
`status=proposed`, with a Telegram notification in the same cycle; never
`integrated` without user validation. New invariants extracted from corpus
documents carry `author = Document.author` tier (dalio → floor 0.40, etc.);
`author='system'` is reserved for market-pattern discoveries. `source` is
always the real free-text provenance (document+page, backtest run).

**Output:** KnowledgeEvent → EventLog.
**User action:** Validation required for proposed innovations.

---

## UC5 — Knowledge Storage
**Not a use case in the Monday sequence** — a transverse mechanism invoked by
any UC that has data to persist. Planner Post decides what to persist;
Writeback executes.
**Order:** EventLog append → graph vertex → edges → FTS → vector → documents.
**Output:** ArcadeDB updated.
**User action:** None.

---

## UC6 — Portfolio Valuation
**Trigger:** Weekly cron (Monday, before Worker).
**What it does:** Calculates USD `sharpe_rolling`, `sortino_rolling`,
`calmar_rolling`, `max_drawdown`, `volatility`, plus cumulative
`return_3m / 6m / 1y / 3y / 5y` for all enabled portfolios, including the
defender. Risk-free rate = 3M T-Bill. Rolling indicator window = 36M.
Updates each `Portfolio` vertex. (PortfolioNAV TS is written by the daily
06:35 job only — UC6 reads it, it does not append.)
**Output:** ValuationEvent → EventLog.

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
**Output:** RankingEvent → EventLog + one row per portfolio in
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
**Principle: the Worker proposes, Writeback disposes.** All gates are
deterministic and run mechanically in Writeback; the Worker contributes
judgment (reasoning, qualitative trigger interpretation, Evaluations,
innovations, reallocation proposals). V1 never auto-applies.

**A — Switch proposal (mechanical gates, Writeback):**
1. challenger outranks the defender in `portfolio_weekly_snapshot`;
2. `sortino_rolling` gap ≥ `proposal_sortino_gap_min` (system_thresholds, 0.02);
3. challenger `calmar_rolling` ≥ 1.5 (absolute Calmar threshold — compared to
   the threshold, not to the defender's Calmar);
4. binding concentration constraints pass (user caps; per-portfolio caps if
   stricter);
5. meaningful allocation change vs the defender: at least one asset differs
   by ≥ `proposal_min_allocation_change_pts` (5.0 percent points).
A challenger may pass with a worse Calmar or drawdown than the defender as
long as it stays above the absolute Calmar threshold — the digest must then
flag the weaker downside profile (see EXAMPLE.md Step 8B).
The Worker annotates the mechanical outcome with `reasoning` (invariants,
regime context, liquidity state) — it does not decide the gate.

**B — Reallocation proposal (Worker-proposed, Writeback-validated):**
The Worker may propose adjusting the DEFENDER's own allocation via
`WorkerResult.reallocation_proposed` (see DATA_MODELS.md), built from the
delta blend `0.4 × scenario_delta + 0.6 × favors_delta` (tactical scenario
target vs structural FAVORS anchor), citing supporting invariants.
Writeback validates mechanically:
1. `proposed_allocation` sums to 100 (±0.1);
2. binding concentration caps pass on the proposed allocation;
3. max per-asset change ≥ `proposal_min_allocation_change_pts` (5.0);
4. turnover `Σ|delta|/2` ≤ `proposal_max_turnover_pct` (30.0);
5. every proposed ticker is in `allowed_tickers` (active, non-macro).
On pass: Proposal vertex (`proposal_type='reallocation'`,
`recommendation='paper-test'`), rendered in the digest with old vs new
allocation and the Worker's argued reasoning.

Inputs:
```
MarketEvent       → current regime + global liquidity context
KnowledgeEvent    → invariant changes
ValuationEvent    → portfolio metrics
RankingEvent      → defender rank and challenger gap
UserDecisionEvent → user amendments
```

If a Proposal is warranted (either kind):
- EventLog append (ProposalEvent) → `Proposal` vertex
  (`recommendation` = 'paper-test' or 'monitor') → snapshot
  `recommendation` upgraded → Telegram digest payload.
- No automatic allocation change in V1.

If not warranted:
- Snapshot recommendation stays 'maintain'; no Proposal vertex created.

**Output:** ProposalEvent → EventLog + Proposal vertex, or nothing.

---

## UC9 — Chatbot
**Trigger:** User message (Telegram).
**LLM policy:** UC9 uses the Worker model (Sonnet) with the same 3 bridged
read-only tools (`db_query`, `market_fetch`, `portfolio_check`) and the same
Worker system prompt plus a chat skill. It never writes directly — decisions
go through Planner Post → Writeback like any UC (UC5 path). This is
user-initiated, so it does not violate the "weekly = sole scheduled decision
cycle" rule; it may trigger at most **one ad-hoc UC8 re-run per day**.
**What it does:** Conversational interface. Any decision stored via UC5.
Rule changes ("reduce max drawdown") update `user_profile` (binding rules);
strategy toggles update `Strategy.enabled`. Can trigger a new UC8 cycle if
the decision impacts the current state.

```
Examples:
  "Does your TIPS thesis hold if the Fed pivots in Q3?"
  "Reduce max drawdown to -10% from now"        → user_profile.max_drawdown_pct
  "Disable momentum-macro strategy"             → Strategy.enabled=false
  "How has the defender ranked over the last 8 weeks?"
```

Proposal buttons ([ACCEPT PAPER-TEST]/[REJECT], [YES]/[NO] for innovations)
are handled by the same bot: callbacks set `Proposal.user_response` /
`Invariant.status` via Writeback, with a UserDecisionEvent appended first.
Pending proposals auto-expire after `proposal_expiry_days` (14).

**Output:** UserDecisionEvent → EventLog.
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
| 8  | Proposal Detection  | Weekly Worker cycle | ProposalEvent + Proposal (switch or reallocation) / — | Weekly |
| 9  | Chatbot             | User message        | UserDecisionEvent                     | On demand        |

---

## What the Agent Never Does Without User Awareness

- Execute an allocation change in V1. V1 only ranks, digests, and proposes
  paper-mode switches and reallocations via Proposal vertices — application
  is always manual.
- Create a new Invariant `source=agent-discovery` without prior Telegram
  notification.
- Change user rules (drawdown limit, concentration limit, strategy enabled)
  without UC9.
- Persist a schema extension without explicit user validation.
