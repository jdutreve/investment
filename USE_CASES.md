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

Event-driven — no nightly cron (the Mac sleeps at night)
  inbox watcher: deposit + 5-min quiet → ingestion batch → curation
  curator (LLM, only on new Documents) → Telegram candidates;
  backup after every Monday chain and every ingestion batch

Weekly (Monday 08:00 when running + DUE-ON-START at launch/wake — one
sequential chain: each step starts only after the previous one succeeds;
on failure the chain aborts, emits an ErrorEvent and sends a Telegram
alert. Times in CLAUDE.md are indicative.)
  UC1  Market Feed (CATCH-UP) → MarketData TS for all days since last
                              run + regime detector step (new prints) + NAV
                              catch-up + expiry sweep (also runs on-demand
                              as the prelude to an ad-hoc UC9 UC8 re-run)
  UC2  (absorbed — see tombstone below)
  UC3  Event Watch          → Document(kind=event) deposits (pinned
                              official sources, LLM triage + enrichment)
  UC4  Knowledge Curation   → KnowledgeEvent
  UC5  Knowledge Storage    → DB updated (transverse mechanism, see below)
  UC6  Portfolio Valuation  → ValuationEvent
  UC7  Portfolio Ranking    → RankingEvent + portfolio_weekly_snapshot
  —    Outcome evaluation   → OutcomeEvent (kind: proposal | calibration |
                              probation — mechanical/outcomes.py; see
                              ARCHITECTURE "Unified improvement cycle")
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
Exemption: pure TS writes (UC1 market feed, weekly NAV catch-up and
scenario-probability appends) —
they create no vertex/edge, so no ordering constraint applies.
The catch-up regime detector emits `RegimeEvent` (only when the regime or
its tags change) and the inbox watcher's ingestion emits `IngestionEvent`
(one per processed batch).
UC8 reads EventLog weekly to assemble its inputs.

---

## UC0 — Seed
**Trigger:** one-shot CLI `uv run python -m investment.seed`.
**LLM:** none.
**Idempotent:** UPSERT on every vertex; safe to re-run.

**What it does:**

```
1.  Reference-table bootstrap (see DATA_MODELS.md):
    - user_profile (currency, BINDING drawdown rule, BINDING concentration cap)
    - allowed_tickers (ETFs + FRED macro series + composites, with
      source and transform columns — TIP, TLT, GLD, DJP, SPY, IEF,
      CHFUSD=X, ^IRX, ^VIX, CPIAUCSL, T10Y2Y, UNRATE, INDPRO,
      GROWTH_COMPOSITE, GLOBAL_LIQUIDITY, ...)
    - system_thresholds (regime thresholds, calmar window 756d,
      recency half-life 365d, vector similarity 0.35, proposal gates for
      switch AND reallocation, proposal_expiry_days, ...)
    - invariant_author_config (dalio/marks/corpus-other/system
      floors and initial weight bands — keyed by `author` field on Invariant)

2.  Framework vertices:
    - '4seasons' enabled=true (the only active framework in V1)
    - 'permanent' + 'liquidity-cycle' enabled=false (metadata-only, seeded
      so I-1 can flip them on later — TASKS.md seed)

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

4.  Invariant vertices (status='proposed' at creation, seed minimum,
    hand-written — guaranteed baseline even when step 6b is skipped;
    created BEFORE the corpus steps so SUPPORTS/BACKED_BY targets exist).
    They are matured over 35y at step 11b and become 'integrated' ONLY if
    time-validated (N_min/θ, not refuted) — belief does not grant
    integration, history does (ADR-006):
    - inflation-persistence-tips     (dalio, weight 0.85, floor 0.40)
    - falling-growth-duration         (dalio, weight 0.80, floor 0.40)
    - rising-growth-equities          (dalio, weight 0.80, floor 0.40)
    - liquidity-tightening-risk       (marks, weight 0.75, floor 0.35)
    - liquidity-easing-risk           (marks, weight 0.75, floor 0.35)
    - diversification-drawdown        (dalio, weight 0.70, floor 0.40)

5.  Strategy vertices (4), enabled=true:
    - four-seasons-rp, permanent-browne, barbell-taleb, momentum-macro
    - Conditions include ≥1 dimension orthogonal to regime thresholds,
      and every referenced indicator is computable from MarketData/Regime
    - BACKED_BY edges to relevant invariants

6.  Corpus seed (optional, if PDFs are in ~/data/investment/sources/corpus):
    - Calls the SAME `CorpusIngester` used by the inbox watcher
      (single pipeline: parse + chunk + embed → Document + Passage vertices)
    - SUPPORTS edges built from passage-invariant matches above similarity
      floor (invariants exist — step 4 — so the matrix is non-empty)

6b. Initial curation pass (DEFAULT when a corpus is present; skip with
    `--no-curate` — the ONLY LLM step in UC0):
    - Runs the SAME curator as weekly UC4 (Task 5.3) over the whole
      corpus ingested in step 6, in batches of passages
    - Extracted invariant candidates are proposed with
      **author = Document.author tier** ('dalio' → floor 0.40,
      'marks' → 0.35, other → null/0.20) — NOT 'system'; 'system' is
      reserved for market-pattern discoveries (backtests, rankings)
    - Each candidate → BACKED_BY/SUPPORTS edges → mature_invariant() over
      35y → status='integrated' if time-validated (N_min/θ, not refuted),
      else stays 'proposed' (candidate). 100 % mechanical — no user gate
      (ADR-006).
    - This is how a deposited book yields matured invariants at install time
      instead of waiting for weekly UC4 cycles

7.  Scenario vertices (3 per Strategy = 12 total), bull/base/bear with
    initial probabilities summing to 100
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
    - 35y history for macro/FRED series (→1991); ETFs from inception, spliced
      with HISTORY_PROXIES back to ~1991 for the tradable/benchmark layer
      (proxies span to 1968-86 → margin; commodity TR source the verify-gate)
      (SPY 1993, GLD/TLT/TIP 2002-04, DJP 2006, BIL 2007, ...)
    - As-known-at-ts (ADR-003): first-release ALFRED vintages for revised
      series, every macro observation indexed at its publication date
    - Computed columns: level, speed (1st derivative), acceleration (2nd),
      per-series transforms per DATA_MODELS.md "MarketData semantics"
    - GROWTH_COMPOSITE and GLOBAL_LIQUIDITY composites computed and stored

10. Historical Regime materialization (NEW — prerequisite for backtests):
    - Run the regime detector over the FULL macro backfill (35y → 1991,
      captures the 1994 bond crash + 2000 dot-com; liquidity only from ~2002)
    - Create one Regime vertex per detected historical episode
      (is_current=false, end_date set)
    - Set is_current=true on the final (ongoing) instance

10b. Benchmark valuation materialization (prerequisite for invariant
    maturation — "define and value the benchmarks before valuing invariants"):
    - asset_class rows: group the reference universe into the 5 coarse classes
      via the pinned BENCHMARK_CLASSES mapping (TASKS seed) over
      allowed_tickers.asset_class (equities / bonds / inflation-protected /
      gold-commodities / cash); per (class, period) compute return +
      sortino_rolling + max_drawdown + volatility from constituent prices,
      SPLICED with HISTORY_PROXIES before ETF inception → tradable history to
      ~1991 (equity/bond/gold/cash proxies to 1968-86, margin; commodity TR
      source is the verify-gate — TIPS floor 2000, not in the AW benchmark)
    - strategy rows: per Strategy's prescribed allocation (synthetic NAV),
      same metrics per period, same proxy splice
    - Write benchmark_valuation rows — this IS what effect.method reads
      (cross_class → asset_class rows, cross_strategy → strategy rows)
    - Also materialize DERIVED signals used by conditions (real_rate =
      irx − inflation, composites) into the MarketData TS

11. Initial Backtests:
    - For each (Strategy, RegimeType) cell where historical coverage ≥
      min_backtest_periods regime instances
    - Backtest vertex with USD sharpe_rolling, sortino_rolling, calmar_rolling
    - TESTED_IN + IN_REGIME (→ historical Regime instance) edges
    - FAVORS edges from RegimeType to Strategy with strategy-level rolling
      indicators (synthetic backtest of prescribed allocation, aggregated
      across all historical instances — n_periods now meaningful thanks to
      the 35y backfill)
11b. Invariant birth maturation:
    - Call mature_invariant() (ARCHITECTURE "Birth maturation") on EVERY
      seed invariant — the SAME factored, source-blind mechanism later
      applied to every post-launch birth. Seed invariants are just the
      first batch; there is no special seed maturation path.
    - Confronts over the FULL available history (from ~1991 where the signals
      exist; per-signal floors liquidity 2002 / TIPS 2000) — so the system
      GOES LIVE with all seed+corpus knowledge already matured over 1991-present,
      not cold. All CONDITION-moments where i.condition held (regime is
      one signal among many; frequency emergent), evaluating i.effect by its
      METHOD, reading benchmark_valuation from step 10b (cross_class → asset-
      class rows, cross_strategy → strategy rows). The invariant-specific
      comparison is recomputed on the fly — nothing per-moment stored; only
      invariant_confrontations + weights persist. Seeds confirmation_count /
      infirmation_count → market_score from day zero; verdict per N_min (3) /
      θ (0.60).
    - Prerequisite: the signals i.condition references + the benchmarks
      i.effect.method reads must be persisted — steps 10 (regime instances),
      10b (benchmark valuations) + the market TS — before this runs.

11c. Scenario probability warm-start (35y calibration — the un-matured piece
    the invariants already have; ARCHITECTURE "Unified improvement cycle"):
    - For each Scenario (bull/base/bear × strategy), run the SAME calibration
      scoring used weekly (dominant-scenario vs realized) over the FULL 35y
      history → the historical BASE-RATE frequency of each scenario's realized
      outcome, regime-conditioned where applicable.
    - Set the seed ScenarioProbability from these base rates (not hand-set) —
      so the reallocation blend's `0.4 × active-scenario` leg is HISTORICALLY
      GROUNDED at go-live, matched to the 35y-matured FAVORS/invariants, not
      cold. The weekly job then adjusts forward from these priors.
    - Same PIT/floor rules as maturation (from ~1991 where signals exist).

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

14. SeedEvent → EventLog with full inventory (EXPLICIT EXEMPTION from the
    append-before-commit rule: UC0 is the bootstrap — the SeedEvent is a
    CLOSING summary, appended after the commits it inventories):
    payload = {
      frameworks, regimes (incl. historical count), invariants, strategies,
      scenarios, portfolios, market_data_rows, backtests, snapshot_date,
      schema_version
    }
```

**Done when:** Worker can run its first weekly cycle (catch-up→UC8) without missing
data, and the digest renders a non-empty defender row.

**User action:** None after running the command.

---

## UC1 — Market Feed
**Trigger:** Monday chain 08:00 (catch-up of all days since last run) —
also invoked on-demand as the prelude to an ad-hoc UC9 UC8 re-run.
**What it does:** Fetches prices from Yahoo Finance and macro series from FRED.
Applies per-series transforms (DATA_MODELS.md "MarketData semantics") and
computes `level`, `speed`, `acceleration` for each series. Appends to
MarketData TS. Includes ^IRX (3M T-Bill risk-free rate), the
`GROWTH_COMPOSITE` and the `GLOBAL_LIQUIDITY` composites.
**Output:** → MarketData TS — the durable `market_data` table in SQLite
(35y history; what the regime detector, NAV, Planner baseline, Worker
`market_fetch` and the Phase 9 replay all read). No EventLog row: EventLog
is the audit journal for entity/relation commits, not the storage — the TS
row itself is the durable record, so auditing it would duplicate the table.
**User action:** None.

---

## UC2 — Market Valuation (ABSORBED — no separate step, no MarketEvent)

Everything UC2 once snapshotted is computed and audited elsewhere:
- **current regime** → catch-up detector (Regime `is_current` + RegimeEvent);
- **portfolio valuations** → NAV catch-up + UC6 (ValuationEvent) + UC7;
- **macro indicators** (level/speed/acceleration) → catch-up fetch →
  MarketData TS, read directly by the Planner baseline;
- **weekly audit copy of the market context** →
  `portfolio_weekly_snapshot.market_context` (written by UC7).
A separate MarketEvent was a duplicate that nothing consumed.

---

## UC3 — Event Watch (qualitative Tier-1 events, trusted sources only)

**Trigger:** Weekly chain (Monday, after the catch-up, before UC4).
**What it does:** NOT a feed vacuum — a narrow watch over a few PINNED
official sources (static `EVENT_SOURCES` constant in code: Fed press
releases / FOMC statements, ECB press, SNB press; changing sources = edit
the constant — complexify to runtime config only if a real need appears):

1. Fetch new items since last run (dedupe by URL against existing Document source_paths) — mechanical.
2. **LLM triage** (curator, `skill-triage-events`): MAJOR event
   (nomination, doctrine shift, emergency action) vs routine — routine is
   discarded.
3. Major events → **Document(kind=event)**: summary, entities, and
   **enrichment** (e.g. Fed chair replaced → profile and likely intent of
   the successor) from the source text + model knowledge + a **bounded
   fetch** restricted to the EVENT_SOURCES domains; if still
   insufficient, the item is flagged `needs-user-input` and pushed to
   Telegram instead of being hallucinated.
4. Ingested SYNCHRONOUSLY via the same `CorpusIngester` (no watcher
   round-trip — UC4's sweep, minutes later, must see the events) →
   Passages + embeddings → Worker context and citable in
   `Evaluation.events`.

The user note channel (one-line Telegram messages → `kind=note`) remains
as a complement. Quantitative shocks stay mechanical (VIX/liquidity tags).
General auto-veille (broad RSS, YouTube/X) stays deferred — I-9/I-26.
**Output:** inbox deposits → Document(kind=event) (audited by
IngestionEvent).
**User action:** None, except answering `needs-user-input` flags.

---

## UC4 — Knowledge Curation
**Triggers (same curator, three callers):**
1. **Event-driven:** ~5 minutes after a deposit (watcher quiet period),
   whenever the ingestion batch created ≥1 new Document, the curation
   curator processes it immediately — a deposited book yields its invariant
   candidates within minutes, not the next Monday. Knowledge extraction
   only — never decisions.
2. **Weekly cron (Monday, after UC3):** sweep over anything not yet curated
   + re-curation opportunities on existing invariants.
3. **UC0 seed batch** (step 6b, default): initial pass over the whole corpus.

**What it does:** Processes Documents/Passages ingested since the last run.
Raw inbox parsing (parse + chunk + embed → Document/Passage vertices +
similarity-based SUPPORTS edges) is done by the watcher batch with no LLM;
the
curator is the LLM step that turns new passages into invariant
updates and candidates (CurationResult — see TASKS.md Task 5.3).

**Curation (autonomous):** updating confirmation counts, enriching
description/example, adding SUPPORTS edges, recalculating `weight_effective`
on existing integrated Invariants.

**Innovation (also autonomous — no user gate, ADR-006; after the mechanical
dedup gate — see TASKS.md Phase 6):** creating a new Invariant, a new or
revised Strategy (`type=new_strategy` / `strategy_revision`,
`enabled=false` — auto-enabled after mechanical probation; lifecycle in
ARCHITECTURE.md "System Evolution"), or proposing a new metric (schema
self-extension is V2 — IMPROVEMENTS I-27). A new Invariant is born
`status=proposed`, matured over 35y, and reaches `status=integrated`
mechanically iff time-validated (N_min/θ, not refuted) — the digest reports
it, never asks. New invariants extracted from corpus documents carry
`author = Document.author` tier (dalio → floor 0.40, etc.);
`author='system'` is reserved for market-pattern discoveries. `source` is
always the real free-text provenance (document+page, backtest run).

**Output:** KnowledgeEvent → EventLog.
**User action:** none — the invariant/strategy lifecycle is fully mechanical.

---

## UC5 — Knowledge Storage
**Not a use case in the Monday sequence** — a transverse mechanism invoked by
any UC that has data to persist. Planner Post decides what to persist;
Writeback executes.
**Order:** EventLog append → graph vertex → edges → FTS → vector → documents.
**Output:** DB updated.
**User action:** None.

---

## UC6 — Portfolio Valuation
**Trigger:** Weekly cron (Monday, before Worker).
**What it does:** Calculates USD `sharpe_rolling`, `sortino_rolling`,
`calmar_rolling`, `max_drawdown`, `volatility`, plus cumulative
`return_3m / 6m / 1y / 3y / 5y` for all enabled portfolios, including the
defender. Risk-free rate = 3M T-Bill. Rolling indicator window = 36M.
Updates each `Portfolio` vertex. (PortfolioNAV TS is written by the Monday
08:00 catch-up job only — UC6 reads it, it does not append.)
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
Pre-gate (anti-repetition): a challenger rejected by the user within the
last `proposal_cooldown_weeks` (4) is skipped, unless the regime type has
changed since the rejection.
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
5. every proposed ticker is in `allowed_tickers` (active, non-macro);
6. every cited invariant (`supporting_invariants`) is `status='integrated'`
   with `weight_effective` ≥ `proposal_invariant_weight_min` (0.10), AND is
   not measurably refuted: if it has ≥ `invariant_refuted_min_confrontations`
   (4) confrontations and `market_score` < `invariant_refuted_score` (0.35),
   it is ineligible REGARDLESS of its authority floor — the floor protects
   authority against forgetting, never against measured refutation. AND its
   `condition` is ACTIVE now (or 'always') — a veridical-but-dormant invariant
   does not justify acting on TODAY's market (the active/veridical split:
   integration = veridical, this clause = active).
On pass: Proposal vertex (`proposal_type='reallocation'`,
`recommendation='paper-test'`), rendered in the digest with old vs new
allocation and the Worker's argued reasoning.

Inputs:
```
MarketData TS + Regime → current regime & liquidity (direct reads)
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

**C — Outcome measurement (mechanical — closes the loop):**
Every Proposal is measured at `proposal_outcome_weeks` (12) by
`evaluate_proposals()` (weekly 08:52): synthetic NAV of the proposed
allocation vs the incumbent defender allocation since `Proposal.date`, net
of costs → `outcome.verdict` 'won'/'lost' → invariant confrontations
`source='proposal'`. Accepted paper-tests are tracked weekly from
`paper_started`. The digest scoreboard renders cumulative hit-rate —
the live continuation of the Phase 9 replay metric. Full spec in
ARCHITECTURE "Unified improvement cycle".

**Output:** ProposalEvent → EventLog + Proposal vertex, or nothing.

---

## UC9 — User interfaces (Telegram bot + `invest` CLI + local dashboard)
**Trigger:** User action on any of the three fronts — Telegram message,
`invest` CLI command, dashboard click (http://127.0.0.1:8765). All fronts
dispatch to ONE command layer (`ops/commands.py`, TASKS Phase 6ter):
action → UserDecisionEvent → Writeback — same gates, same audit trail.
Reads are direct on SQLite (WAL); the dashboard adds a read-only SQL
console and semantic search; full command list in TASKS Task 6ter.2.
**LLM policy:** UC9 uses the Worker model (Sonnet) with the same 3 bridged
read-only tools (`db_query`, `market_fetch`, `portfolio_check`) and the same
Worker system prompt plus a chat skill. It never writes directly — decisions
go through Planner Post → Writeback like any UC (UC5 path). This is
user-initiated, so it does not violate the "weekly = sole scheduled decision
cycle" rule; it may trigger at most **one ad-hoc UC8 re-run per day** —
which always starts with the UC1 catch-up prelude (fetch + regime + NAV,
mechanical, seconds) so the Worker never reasons on stale market data —
while the RANKING context stays the latest Monday snapshot (no mid-week
snapshot rewrite). `/status`
mid-week offers `/refresh` (same prelude, no UC8).
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

Proposal buttons ([ACCEPT PAPER-TEST]/[REJECT]) are handled by the same bot:
callbacks set `Proposal.user_response` via Writeback, with a
UserDecisionEvent appended first. Innovations (new invariants/strategies)
have NO button — they integrate mechanically and are only reported (ADR-006).
On [REJECT] the bot prompts for an optional one-line reason →
`Proposal.rejection_reason` (fed back into the Worker's context and the
switch cooldown rule). Pending proposals auto-expire after
`proposal_expiry_days` (14).

**Output:** UserDecisionEvent → EventLog.
**User action:** This IS the user action UC.

---

## Summary Table

| #  | UC                  | Trigger             | Output                                | Frequency        |
|----|---------------------|---------------------|---------------------------------------|------------------|
| 0  | Seed                | Manual CLI          | SeedEvent + full DB bootstrap         | Once at install  |
| 1  | Market Feed         | Monday catch-up + on-demand | MarketData TS                         | Weekly           |
| 2  | (absorbed)          | —                   | catch-up + snapshot.market_context    | —                |
| 3  | Event Watch         | Weekly chain        | Document(kind=event) via ingester     | Weekly           |
| 4  | Knowledge Curation  | Weekly cron         | KnowledgeEvent                        | Weekly           |
| 5  | Knowledge Storage   | Any event with data | —                                     | Event-driven     |
| 6  | Portfolio Valuation | Weekly cron         | ValuationEvent                        | Weekly           |
| 7  | Portfolio Ranking   | Weekly cron         | RankingEvent + snapshot               | Weekly           |
| 8  | Proposal Detection  | Weekly Worker cycle | ProposalEvent + Proposal (switch or reallocation) / — | Weekly |
| 9  | User interfaces     | Telegram / CLI / dashboard | UserDecisionEvent (one command layer) | On demand |

---

## What the Agent Never Does Without User Awareness

The V1 boundary is **real-world execution**, not a knowledge-validation gate
(ADR-006). The agent's internal cognition is fully autonomous; the weekly
digest provides awareness by REPORTING what changed — it never asks.

- **Execute an allocation change** in V1. V1 only ranks, digests, and proposes
  paper-mode switches and reallocations via Proposal vertices — application to
  the real portfolio is always the owner's manual act. THIS is the human
  boundary.
- **Change the owner's rules** (drawdown limit, concentration limit, strategy
  enabled) without UC9 — those are user preferences, never agent-overridden.
- Persist a **schema extension** (V2 — IMPROVEMENTS I-27).

(Creating AND mechanically integrating invariants/strategies is now
autonomous — matured over 35y, reported in the digest, no approval flow.)
