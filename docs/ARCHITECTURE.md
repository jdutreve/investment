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

| | Planner (Qwen3-8B, OpenRouter) | Worker (Sonnet 5, Anthropic) |
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
  "You are the CAPTAIN of this ship — a long-term investment expert, Phase 1
   accumulation. Your DESTINATION is fixed: build retirement capital over
   15-20 years. Rule #1: don't lose. Rule #2: don't forget rule #1.
   You read the WEATHER — the market: the current regime, global liquidity,
   volatility, and the level/speed/acceleration of every series (speed and
   acceleration tell you whether a storm is building or easing, so you
   ANTICIPATE, not merely react).
   You steer by LIGHTHOUSES — the invariants in your context orient your
   reasoning, they do not give orders (see skill-interpret-invariants).
   You carry 35 YEARS of a sailor's experience — every indicator, backtest,
   FAVORS edge and invariant weight you read was already confronted over
   1991-present (1994, 2000, 2008, 2020, 2022).
   You chart the course; the owner's hand is on the wheel — V1 never
   auto-executes, and final safety gates are applied outside you.
   Evaluate strategies, rank portfolios, compare challengers against the
   defender, propose paper-mode adjustments. You may propose adjusting the
   defender's own allocation (blend 0.4 × active-scenario target +
   0.6 × regime-favored structural anchor), citing the invariants that
   support it.
   Use the Skills provided and the data in your context.
   You are unaware of the Planner, Writeback, and internal storage.
   Three tools: db_query, market_fetch, portfolio_check.
   Sharpe/Sortino/Calmar are pre-calculated indicators in USD in the DB;
   the suffix is _rolling. Interpret them — do not recalculate.
   Rolling window is 36 months. Risk-free rate is 3M T-Bill (^IRX).
   WorkerResult must include innovations_proposed (empty list if none)
   and reallocation_proposed (null if none)."

CURATOR: no persona system prompt by design. It runs on WORKER_MODEL but
  is a single-shot, tool-less transformation whose behavioral spec lives
  entirely in its skill (skill-curate-knowledge.md). Omitting the Worker
  persona is deliberate — it keeps the curator on the
  knowledge-extraction-never-decisions side of the guardrail (it must never
  evaluate strategies, rank, or propose adjustments).
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

GRAPH RELATIONS (10 conceptual — physically 5 M:N tables + 5 FK
             columns on the child; V2 adds Adaptation → MODIFIES → Portfolio)
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
                    invariant_author_config, detector_state (hysteresis +
                    chain last-success marker, 1 row), invariant_confrontations,
                    benchmark_valuation (cross_class/cross_strategy benchmark,
                    per period), portfolio_weekly_snapshot,
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

**Axis classification (ONE state-machine step per new monthly print,
candidate state persisted; four callers: UC0 materialization and the
replay iterate step() over the archive; the Monday catch-up and the
on-demand UC9 prelude call it on the prints since the last run):**

```
-- Direction is classified on a SMOOTHED speed, never on a single print:
speed_g       = trailing mean of GROWTH_COMPOSITE.speed over the last
                regime_speed_smoothing_months (4) observations
speed_i       = trailing mean of CPI_YOY.speed over the same window
                -- level and acceleration stay the LATEST RAW print

growth_dir    = 'rising'  if speed_g > +regime_growth_noise (0.3)
              = 'falling' if speed_g < −regime_growth_noise
              = 'flat'    otherwise
inflation_dir = 'rising'  if speed_i > +regime_cpi_noise (0.04)
                           AND CPI_YOY.level > regime_cpi_stagflation (2.5)
              = 'rising'  if speed_i > +regime_cpi_noise (level ≤ 2.5 →
                           counts as rising only with accel > 0)
              = 'falling' if speed_i < −regime_cpi_noise
              = 'flat'    otherwise

candidate = quadrant(growth_dir, inflation_dir)   -- 'uncertain' if any axis flat
```

**Why the speed is smoothed (M3):** a bare 1-month diff of a z-score-amplified
composite is dominated by single-month noise — a lone +6.3 print inside the
2008 collapse reads as "rising growth" at ANY noise threshold, because it is a
genuine month-over-month bounce, not a calibration artifact. The smoothing is
LOCAL TO THE DETECTOR's read of the series: the persisted `market_data`
level/speed/acceleration stay exactly as TASKS.md Task 2.2 pins them, so every
other consumer (invariant conditions, the Worker) still sees the raw series.
Only the direction-classifying speed is smoothed; the CPI stagflation LEVEL
gate and the `events` narrative read the raw print.

**All values above are M3-calibrated, not chosen** — a grid search over the
real 35y history scored every (noise × confirm_prints × smoothing) combination
against 7 episodes the detector must register; the winner takes LOW noise
thresholds with the chop suppressed by smoothing + a 3-print confirmation
(7/7 events, 90 episodes, 4% whipsaw). A wide-noise variant instead goes blind
for years. The full rationale lives at the values themselves, in
`db/seed_data.py` `SYSTEM_THRESHOLDS` — which is authoritative; the numbers
quoted here are illustrative and must not be read as the source of truth.

**Hysteresis:** a regime CHANGE is committed only after the same candidate
quadrant has been produced by `regime_confirm_prints` (3) **consecutive
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
                   -- speed_a = the SMOOTHED speed above: *_speed_scale is
                   -- calibrated to the p90 of the smoothed distribution
accel_bonus      = 10 if sign(accel)==sign(speed) on BOTH axes else 0
                   -- accel = the freshest RAW acceleration: an intentional
                   -- short/long-horizon pairing — "is the latest print still
                   -- pushing in the sustained direction?"
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
Runs in the weekly 08:40 step, after Backtests/FAVORS and benchmark_valuation
refresh, and after each Evaluation commit.

```
FROM BACKTESTS (source='backtest') — forward confrontation on the invariant's
CONDITION-moments. Per-moment metrics are recomputed from the TS (NOT the
running FAVORS aggregate, which serves Worker reasoning and the reallocation
blend, not confrontation):

  A MOMENT of invariant i = a day i.condition HOLDS (knowable that day,
  ADR-003: predicates evaluate on as-known-at-ts data), read from the
  market-data TS / regime instances (condition model in "Birth maturation"
  below), SAMPLED at one-horizon spacing across the condition's active time:
  walk the active days forward, take one, skip a horizon, repeat. Forward, a
  moment is SCORED once its outcome window has elapsed — never truncated.

  WHY horizon-spaced. (a) NON-OVERLAPPING ⇒ the verdict's tail tests are
  sound: outcome windows are [d, d+horizon], so horizon spacing makes them
  disjoint and the moments quasi-independent — the binomial assumes exactly
  that, and sampling active time weekly would overlap every 12w window
  12-fold, inflating N against evidence that is not there. (b) CONTINUOUS in
  condition frequency ⇒ no cliff between a persistent state and 'always'.
  ONE-MOMENT-PER-EPISODE (the rejected alternative) had an indefensible
  discontinuity: a condition true 100% of the time sampled ~1800 times, one
  true 88% of the time in a single block sampled ONCE. Measured on the real
  data, `real_rate < 2.5` holds 88% of 35y but chatters into 36 episodes —
  one of 7050 days (2001-2020, the entire low-real-rate era) plus 35 six-day
  blips at the threshold in the high-rate 1990s; per-episode scoring gave
  that era a single data point and let the blips carry the verdict ('low
  real yields favour gold' read 0.158/REFUTED on N=19, vs 0.542/undecided on
  N=107 sampled honestly). A SHORT episode still contributes its start (the
  decision moment realloc gate 6 simulates — "the condition is active NOW,
  tilt?"), since the next active day inside a horizon is skipped; a LONG one
  is sampled throughout. 'always' is not special-cased: it is simply a
  condition active on every date.
  For the moment M, evaluate i.effect by its method (see "Birth
  maturation"): benchmark_M from the pre-materialised valuations per
  i.effect.method (cross_class / cross_strategy / absolute).

  The metric is read over the horizon FOLLOWING M (proposal_outcome_weeks) —
  the invariant claims the condition BRINGS THE EFFECT ABOUT, so the window
  measured is the one the condition could have acted on, starting when the
  condition fires. The two rejected windows, both measured on the real 35y
  data (M5): a TRAILING window at M mostly predates the condition (episodes
  run 1..244 days against a multi-week metric window) and reproduces the
  unconditional base rate; a window anchored at the episode CLOSE measures
  the aftermath of the condition ENDING — cycle mean-reversion — and
  manufactures anti-signal ('rising growth favours equities' scored 0.397
  close-anchored vs 0.512 start-anchored).

  The comparison is BASELINE-RELATIVE:
    excess_M   = i.handle's metric − benchmark_M            (over that horizon)
    baseline_i = median excess over ALL dates, i.condition IGNORED
                 (= 0 when i.condition is empty — see below)
    excess_M vs baseline_i in i.direction ± confrontation_margin[metric]:
      held         → confirmation(i)
      contradicted → infirmation(i)
      within band  → no-op

  WHY baseline-relative. A confirmation must mean "the effect happened MORE
  than it usually does", not merely "the effect happened". Measured
  absolutely, any invariant whose effect points along a strong base rate
  self-certifies: equities beat the median of the other classes ~70% of any
  12w window on the risk premium alone, so on the real 35y data "rising growth
  favours equities" scored 0.65 and INTEGRATED while performing worse than
  ignoring growth entirely, and every 'equities underperform' claim was
  rejected before the evidence was read. Subtracting the invariant's own null
  is what makes market_score (formula unchanged — ../CLAUDE.md) a SKILL
  frequency, and what anchors its null at 0.50 for EVERY handle — the anchor
  invariant_time_validation_score (0.60) is written against. Without it θ is a
  scalar compared against a per-handle base rate (0.70 equities, 0.40 TIPS)
  and no single value can be right for both (M5).

  An EMPTY condition ('always') keeps baseline_i = 0, i.e. an ABSOLUTE
  measure: it makes no conditional claim, so its lift is zero by construction
  and a lift measure would pin it at 0.50 forever; its claim genuinely is
  absolute ("this handle's drawdown is lower, period").

  confrontation_margin is PER-METRIC (confrontation_margin_<metric>, falling
  back to confrontation_margin): one absolute band cannot serve metrics on
  incommensurable scales — 0.10 on max_drawdown, whose cross-strategy
  dispersion is ±0.04, admitted 0 of 1812 moments and made the invariant
  permanently unmaturable (M5).

  POINT-IN-TIME (ADR-003): baseline_i must be computed as-known-at-t on the
  forward weekly path. At BIRTH the full-sample baseline carries the same
  in-sample bias the birth sweep already concedes ("a weight prior, not
  out-of-sample proof" — see below), and no more.
  Already-elapsed historical moments are NOT re-confronted: they were swept
  once, at i's BIRTH, by mature_invariant() (see below). Seed invariants are
  the first batch of births; no special seed path.
  "Once" means ONCE PER DEFINITION, keyed on a fingerprint of
  (i.condition, i.effect): both are mutable — the seed rewrites them on every
  run and M7's consolidation revises them — and a verdict belongs to the
  definition it was earned under. An EDIT re-sweeps (replacing its own prior
  source='backtest' rows; evaluation/proposal rows are forward evidence and
  survive); an unchanged definition still skips. Keyed on "was ever matured"
  instead, an edited invariant keeps a score measured against its old
  condition: rewriting the gold invariant's condition to one that can never
  fire preserved 0.646/INTEGRATED, citable by gate 6 (M5).

FROM EVALUATIONS (source='evaluation'):
  CONDITION GATE — confront ONLY invariants whose `condition` was ACTIVE at
  the evaluation's as-of date (an 'always' condition always qualifies).
  Confronting an invariant whose condition was absent would credit/blame it
  for a market it does not claim to describe.
    verdict='confirms'     → confirmation for each qualifying BACKED_BY
                             invariant of the evaluated strategy (severity=1.0)
    verdict='invalidates'  → infirmation (severity=1.0)
    'weakens' | 'neutral'  → no count change

FROM PROPOSALS (source='proposal') — closes the loop on emitted proposals:
  Run by evaluate_proposals() (weekly 08:52 — see "Unified improvement
  cycle" below). When a Proposal reaches proposal_outcome_weeks (12) of age:
  CONDITION GATE — of the cited invariants (reallocation: supporting_invariants;
  switch: the challenger's BACKED_BY invariants), confront ONLY those whose
  `condition` was active during the outcome window [Proposal.date, +12w]
  ('always' always qualifies).
    verdict='won'  → confirmation for each qualifying cited invariant (severity=1.0)
    verdict='lost' → infirmation, severity=1.0

Each confrontation: append invariant_confrontations doc → update counts →
update_invariant_weights() (weight_effective formula in ../CLAUDE.md) →
Invariant.updated_at = today (drives recency_factor).
Severity is recorded but unused in market_score in V1 (IMPROVEMENTS I-24).
```

### Birth maturation — `mature_invariant()` (factored, source-blind)

Maturation is **orthogonal to provenance and creation type.** ONE mechanism,
identical for EVERY invariant at creation — seed, corpus ingestion,
agent-discovery, user note, UC3 event. Provenance affects only metadata
(author identity, floor); it never changes the maturation path.

**Two distinct things** the condition/effect split keeps separate:
- **ACTIVE** — `i.condition` holds NOW → `i` applies to the current market
  (Worker context, digest "what it depends on"). *Applicability, present tense.*
- **VERIDICAL** — over the moments where `i.condition` HELD (35y + forward),
  did `i.effect` materialise? → `market_score`. *Truth / track record.*

The 2×2: active+veridical = reliable & applicable now; **inactive+veridical =
dormant but trustworthy** (its condition simply is not present — must NOT
decay, see recency); active+unproven; inactive+refuted.

```
mature_invariant(i)  — Writeback, at every birth (after dedup, before/at commit):
  Requires a machine-readable CONDITION + EFFECT over KNOWN signals:
    condition : Predicate[]  (ANDed; empty ⇒ 'always')  — WHEN active
      Predicate = { signal, feature, op, value }
      signal  ∈ the SIGNAL REGISTRY (db/seed_data.py SIGNAL_ALIASES): any
                collected MarketData series (from allowed_tickers), any DERIVED
                composite (GROWTH_COMPOSITE, GLOBAL_LIQUIDITY,
                real_rate = irx − inflation, real_yield = DGS10 − inflation),
                or 'regime'
      feature = 'level' | 'speed' | 'acceleration' for a series signal;
                'type' for 'regime' ONLY (they are not interchangeable — a
                series carries no 'type' column and vice versa)
      CURATOR RULE 1: express the FUNDAMENTAL causal driver, not a surface
      correlate — "gold when real yields are low", NOT "gold in stagflation"
      (more causal, and captures more moments → larger N).
      CURATOR RULE 2: pick the signal by ECONOMIC MEANING, never by name
      similarity. `real_rate` and `real_yield` differ only in the maturity of
      their nominal leg and read almost identically, but they are NOT
      interchangeable and NOT a refinement of one another:
        real_rate  = ^IRX − CPI YoY  → the POLICY STANCE (what the Fed sets).
                     Use for accommodation / financial-repression claims.
        real_yield = DGS10 − CPI YoY → the market-priced REAL COST OF CAPITAL.
                     Use for OPPORTUNITY-COST claims (a non-yielding asset vs
                     a yielding alternative) and long-horizon discounting.
      Measured over 1991-2026 they disagree on a `< 2.5` test for 22% of days
      (the short real rate is below 2.5 for 88% of the sample — near
      degenerate — the long yield for 68%), and that 22% decides verdicts:
      the owner's "low real yields favour gold" scored 0.542/undecided on
      real_rate and 0.646/INTEGRATED on real_yield. Gold's claim is
      opportunity cost, hence the LONG yield (M5).
      e.g. low real yields       → [{real_yield, level, <, 2.5}]
           negative policy rate  → [{real_rate, level, <, 0}]
           rising & decelerating → [{inflation, speed, >, 0}, {inflation, acceleration, <, 0}]
           short-rate rising     → [{irx, speed, >, 0}]
    effect : { handle, metric, method, direction }  — the VALUATION METHOD
      handle   = 'asset:<ticker>' | 'asset-class:<class>' | 'strategy:<id>'
      metric   = return | max_drawdown | sortino_rolling | volatility
                 (EXACTLY the benchmark_valuation columns — mechanical/
                  backtests.py BENCHMARK_METRICS; the gate rejects anything
                  else, since the confrontation reads it as a column. NOTE
                  'relative_return' is NOT one: relativity is the METHOD's
                  job (cross_class), not the metric's — a real author
                  submitted it twice.)
      method   = 'cross_class'    (handle vs the MEDIAN of the other asset classes)
               | 'cross_strategy' (handle vs the MEDIAN of the other strategies)
               | 'absolute'       (handle metric vs 0 — sign of the metric)
      direction= 'outperform' | 'underperform'  (absolute: outperform ≡ metric > 0)
      e.g. gold: {asset-class:gold-commodities, return, cross_class, outperform}
      (effect.handle is INDEPENDENT of BACKED_BY: BACKED_BY = what the invariant
       supports for reallocation; effect = how its veracity is measured.)

  VALIDATION GATE (mechanical, Writeback, before maturation):
    every predicate's `signal` ∈ the registry, `feature` valid for it,
    `op`/`value` type-consistent; `effect.handle` an existing asset / asset
    class (a BENCHMARK_CLASSES key) / enabled Strategy; `metric` a computed
    indicator; `method` in the enum AND consistent with the handle kind
    (cross_class ⇒ asset/class handle; cross_strategy ⇒ strategy handle;
    absolute ⇒ any handle); `direction` valid. FAIL on any → the
    candidate is DEMOTED to reference knowledge (empty condition/effect,
    market_score frozen 1.0, reason in `trace`) — a malformed condition/effect
    never silently breaks maturation.

  An observation not reducible to a VALID condition+effect over registry
  signals is NOT a weighted invariant: a ponctual fact — not a new entity
  (a confrontation moment or an event Passage; DATA_MODELS) — not matured.

  PREREQUISITE (materialised at seed, upstream of any maturation):
    - regime instances (USE_CASES step 10) — for regime-signal conditions;
    - the market-data TS incl. DERIVED signals (real_rate, composites);
    - the BENCHMARK VALUATIONS (USE_CASES step 10b) — each reference asset
      class AND each strategy valued per period over 35y; this IS what
      `cross_class` / `cross_strategy` read. "Define and value the benchmarks
      before valuing invariants."
    Maturation cannot run before these exist.

  MOMENTS = all historical periods/occurrences where i.condition held, read
  from the TS / regime instances, over the FULL available history — from the
  earliest date i's signals exist (~1991 for macro/price signals; per-signal
  data floors: liquidity 2002 WALCL, TIPS-effect 2000). Nothing artificially
  truncates the window: every invariant is confronted as far back as its data
  allows, so at go-live the whole seed+corpus knowledge is already matured over
  1991-present, not cold. (frequency EMERGENT from the condition: the active
  days are SAMPLED at one-horizon spacing — a rare event contributes its
  occurrence, a persistent state is sampled throughout, 'always' is just
  active-every-day. Rationale — independence + continuity — in "Invariant
  confrontation rule" above.)
  For each moment M, evaluate i.effect by its METHOD, over the horizon
  FOLLOWING M, BASELINE-RELATIVE (full rule + rationale in "Invariant
  confrontation rule" above — this is the same rule, swept once over history):
    benchmark_M per method — cross_class: MEDIAN of the other asset classes'
      metric; cross_strategy: MEDIAN of the other strategies'; absolute: 0
      — READ from the pre-materialised benchmark_valuations, not recomputed
      ad hoc
    excess_M = i.handle's metric − benchmark_M
    baseline_i = median excess over ALL dates, i.condition IGNORED (0 if
      i.condition is empty — an 'always' claim is scored absolutely)
    excess_M vs baseline_i in i.direction ± confrontation_margin[metric]
      → confirmation(i) | infirmation(i) | no-op (within band)
  → seeds confirmation_count / infirmation_count from all N moments at once
  → market_score REAL on day 1 (no infinite forward wait)
  → append invariant_confrontations rows → update_invariant_weights()
     → updated_at = today.

  Per-moment metrics are NOT stored — recomputed on the fly from the persisted
  signals. Maturation persists only its OUTCOMES (invariant_confrontations +
  weights).

  TIME-VALIDATION VERDICT — the number. Three outcomes, checked in order
  (ADR-006 + its M5/M5-bis amendments; every threshold from
  system_thresholds; α = 1 − invariant_verdict_confidence = 0.05):
    REFUTED    → rejected:   confrontations ≥ 4 AND market_score < 0.35
                             (the effect actively fails when cited — the
                              point test arms fast for clearly harmful i)
    INTEGRATED:              confrontations ≥ invariant_min_confrontations
                             (N_min, 3) AND market_score ≥
                             invariant_time_validation_score (θ, 0.60)
                             AND P(X ≥ confirmations | N, invariant_null_
                             score) ≤ α — i.e. the 0.50 null (the
                             no-condition rate of a baseline-relative
                             score) is an implausible source of evidence
                             this good. EFFECT SIZE **and** EVIDENCE.
    INADEQUATE → rejected:   confrontations ≥ 4 AND P(X ≤ confirmations |
                             N, θ) ≤ α — given ample evidence, a true rate
                             of θ is an implausible source of evidence this
                             bad, so i demonstrably CANNOT reach the bar.
                             Cannot race INTEGRATED: score ≥ θ puts the
                             count at or above θ's own median, so its lower
                             tail is ≈0.5, never ≤ α.
    otherwise  → proposed:   INSUFFICIENT EVIDENCE — the ONLY meaning of
                             'proposed'. It empties mechanically as
                             confrontations accrue (above θ the null tail
                             collapses and i integrates; below θ the θ tail
                             collapses and i is rejected), honoring
                             "Nothing stays proposed forever" (ADR-006);
                             only genuine data scarcity (a rare condition,
                             a late data floor) keeps an invariant here,
                             weight held near floor.
  WHY the null tail on INTEGRATED (M5-bis): θ alone is a POINT test, and a
  point test gets EASIER the less evidence there is. P(score ≥ 0.60 | the
  invariant has NO edge) is 50% at N=3, 21% at N=14, 25% at N=20, 3% at
  N=82 — so at the old rule `inv-inflation-persistence-tips` held an
  'integrated' stamp on 9/14 (a 21% coin), and gate 6 would have cited it in
  a live proposal. The incentive also ran backwards: a NARROWER condition
  yields fewer moments and so passed MORE easily — the engine mechanically
  rewarded the over-fitted invariants it exists to catch, with no user gate
  downstream to notice (ADR-006). The bar stays reachable: a true-0.65
  invariant qualifies on ~30 moments (~7y of active condition at a 12w
  horizon) and the real gold invariant clears it at 53/82 (tail 0.005).
  WHY EXACT tails, not the Wilson interval this rule first used: Wilson is
  liberal at extreme rates with small N — exactly where the defect lives.
  wilson_lower(3,3) = 0.526 ≥ 0.50 would still have integrated a 3-for-3
  invariant that a coin reproduces 12.5% of the time. The exact tail puts
  the smallest perfect record at 5/5 (0.031) and leaves every rejection on
  the real board unchanged.
  WHY the INADEQUATE branch: without it the 0.35..θ band is an absorbing
  dead middle — on the real 35y maturation 4 of 6 seed invariants landed
  there (e.g. 0.545 on N=354, upper bound 0.588) and would have stayed
  'proposed' at ANY N, unqualifiable, while gate 6 cites integrated
  invariants only. The engine's purpose is to VALIDATE knowledge: verdicts
  must converge. Baseline-relative scoring (above) is what makes the test
  sound — the null is 0.50 for every handle.
  The verdict is STATELESS — recomputed from current counts at every
  confrontation — so a rejection is as reversible as the evidence behind it.
  weight_effective stays continuous via market_score; the verdict is the
  discrete gate for integration eligibility and money-moving citation.
  N_min/θ/confidence are owner-set (NOT in the Phase 9 calibration grid —
  the mechanical replay is blind to invariant weights; see DATA_MODELS
  system_thresholds note).

  RECENCY is CONDITION-RELATIVE. recency_factor must NOT decay i for its
  condition being ABSENT — a dormant-but-veridical invariant is not stale, it
  waits for its condition. days_since counts moment-time (since the condition
  was last PRESENT), not wall-clock. (Formula pinned in DATA_MODELS.)

  NOT reducible to condition+effect over known signals (axiomatic — "keep costs
  low"; hard caps like concentration/drawdown): NOT a weighted invariant.
  Either reference knowledge (empty condition, market_score frozen 1.0) or a
  binding user_profile rule — never enters mature_invariant().
```

**Point-in-time honesty (ADR-003).** mature_invariant() recomputes effects from
the point-in-time market-data TS (ALFRED vintages) — NO new look-ahead. But for
a corpus invariant part of the 35y is in-sample for its author, and an
**agent-discovery invariant is FULLY in-sample** (discovered from the same
history it is then scored on): the resulting market_score is a **weight prior**,
not out-of-sample proof. Uniform 35y maturation for all births — agent-discovery
included — is a deliberate choice; V2 accrues real forward track record.

### Invariant contradiction check (mechanical — seed + every birth)

After maturation, Writeback flags pairs of INTEGRATED invariants that
**contradict**: their conditions can be simultaneously ACTIVE (the predicate
sets overlap — e.g. both fire on rising real rates) AND their effects oppose
on the SAME handle (same asset/class/strategy, same metric, opposite
direction). A flagged pair is surfaced for owner review (digest) — it does not
auto-resolve; two high-weight invariants pulling opposite ways on the same
lever is a knowledge defect the market-score alone will not catch (each may be
individually well-confirmed). Cheap (pairwise over the integrated set, ~50
invariants). Runs at seed (after 11b/11c) and on each new integrated birth.

---

## Unified improvement cycle (ALL resources)

Every improvable resource follows the SAME lifecycle — this is the system's
core loop, and each stage is mechanical except the proposal itself:

```
measure current performance → PROPOSE
  → MATURATION (mechanical measurement over a fixed window)
  → ADOPT (validated principle — no longer a proposal) | REJECT
```
No user gate anywhere in this loop (ADR-006); the digest reports adoptions
and rejections.

| Resource            | Proposer          | Measure (mechanical)                          | Maturation window            | Adopt / Reject                                  |
|---------------------|-------------------|-----------------------------------------------|------------------------------|--------------------------------------------------|
| Proposal (switch)   | Writeback gates   | proposed vs incumbent NAV since `date`        | proposal_outcome_weeks (12)  | outcome.verdict won/lost + confrontations        |
| Proposal (realloc)  | Worker            | proposed vs incumbent NAV since `date`        | proposal_outcome_weeks (12)  | outcome.verdict won/lost + confrontations        |
| Invariant           | Worker / curation | confrontation rule (backtest/evaluation/proposal) | continuous (recency decay) | weight_effective vs floor; realloc gate 6 eligibility |
| Strategy (new/revision) | Worker        | FAVORS refresh after activation               | strategy_probation_weeks (12) | probation verdict: keep / propose closure       |
| Scenario probabilities | seed WARM-START (35y base rates, UC0 step 11c) + weekly job + Worker | calibration: dominant scenario vs realized | scenario_calibration_weeks (4) | score feeds Worker context + Strategy conviction |
| Thresholds          | Phase 9 replay    | walk-forward calibration                      | ~25y calibrate / ~10y validate | user-confirmed write to system_thresholds        |

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
  For each Strategy activated VIA INNOVATION (new or revision — anchored
  on its InnovationEvent date; the 4 SEEDED strategies are the baseline
  and never enter probation) strategy_probation_weeks ago:
  compare its FAVORS percentile in the current regime type vs the
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
MarketData TS (week-over-week shift computed on read — probability values
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

Event-driven (no nightly cron — the Mac sleeps, ADR-002)
  inbox watcher (60s poll, 5-min quiet) → CorpusIngester batch
    → curator (LLM — only when the batch created new Documents)
  backup after every Monday chain and every ingestion batch
  (market fetch, regime detection, NAV, scenario probabilities: all in
   the Monday chain — decision cadence is weekly)

Weekly (Monday — canonical timeline, identical in ../CLAUDE.md / USE_CASES.md)
  (UC2 absorbed — catch-up + snapshot.market_context)
  08:05  UC3 event watch → Document(kind=event) via ingester
  08:10  UC4 knowledge curation → KnowledgeEvent
  08:30  Backtests → FAVORS edges (RegimeType → Strategy)
  08:35  Scenario numeric triggers → ScenarioProbability TS
  08:40  Invariant weights
  08:45  UC6 portfolio valuations → Portfolio vertices
  08:50  UC7 ranking → portfolio_weekly_snapshot
  08:52  Outcome evaluation (outcomes.py) → OutcomeEvent
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

## System Evolution — Autonomous, mechanically self-validating (ADR-006)

```
Worker/curator discovers new pattern (type=new_invariant)
  → ImprovementProposal in WorkerResult.innovations_proposed
  → EventLog append → Invariant source:agent-discovery status:proposed
  → mature_invariant() (35y confrontation, like any birth)
        ↓
  time-validated (N_min/θ, not refuted) → status:integrated   [mechanical]
  refuted (≥4 confrontations, market_score < 0.35)            → status:rejected
  otherwise                                                    → stays proposed
                                                                  (candidate, low weight)
  → the weekly digest reports what changed; no user gate, no approval flow.

Schema self-extension (new vertex/edge/property types): DEFERRED TO V2
(IMPROVEMENTS I-27) — a schema element without code to use it is dead
weight. V1 innovations are new_invariant / new_strategy /
strategy_revision / process / data.
```

**New Strategy (type=new_strategy)** — the Worker (or the curator)
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

Flow (fully mechanical — no user gate, ADR-006):
  → EventLog append (InnovationEvent) → Strategy vertex
    source:agent-discovery, status:proposed, enabled:false
  → mechanical PROBATION (strategy_probation_weeks); the digest reports it
        ↓
  probation PASSES → in ONE Writeback transaction:
    status:active, enabled:true
    3 Scenario vertices + HAS_SCENARIO edges
    BACKED_BY edges to the cited invariants
  → the next weekly cycle picks it up mechanically: Backtests over
    historical Regime instances (coverage permitting) → FAVORS edges →
    eligible as structural anchor for reallocation deltas
  probation FAILS → status:closed, enabled stays false (reason as trace)

A new Strategy affects the ranking only when a Portfolio HOLDS it —
creating or modifying Portfolios remains a user preference (UC9) in V1.
```

**Strategy revision (type=strategy_revision)** — the "better strategy" path:
same spec fields as new_strategy plus `supersedes: <strategy_id>`.
Mechanically (no user gate — ADR-006): `-v(N+1)` is born `status=proposed`,
`enabled=false` and enters probation like any new strategy. On probation
PASS, in ONE transaction: `-v(N+1)` becomes active (with its 3 Scenarios and
BACKED_BY edges), the superseded vertex gets `status='closed'`,
`enabled=false`, `date_revised=today`, and the new vertex's `trace` records
the lineage ("supersedes <id>: <what changed and why>"). On probation FAIL,
`-v(N+1)` closes and the superseded stays active. HOLDS edges are NOT
migrated automatically — repointing a Portfolio to the new version is a user
action (UC9). Backtests/FAVORS for the new version are computed at the next
weekly cycle (see "Unified improvement cycle").

---

## Detailed Planner Steps

### PYTHON — Baseline (mechanical, no LLM)
```
asyncio.gather (5 fixed queries — no judgment involved, so no LLM):
  ① Current Regime + global liquidity
  ② Ranked enabled portfolios from portfolio_weekly_snapshot
  ③ Scenarios (+ week-over-week shift, computed on read)
  ④ Invariants in 3 relevance buckets (K=8 each, ≤20 after dedup):
     regime:<current> tag | assets held by defender+challengers | global
     top by weight_effective
  ⑤ Last 3 Proposals (incl. outcome verdicts, rejection reasons)
```

### CALL 1a — Query Strategist (LLM → the VARIABLE margin only)
```
Input  : raw trigger + baseline SUMMARY
LLM    : Qwen3-8B via OpenRouter, thinking=512 tokens

tool_use output — QueryStrategies (bounded; never raw SQL):
  corpus_queries : list[str] (≤3) — what to search in the corpus THIS
                   week (regime shift? refuted invariant? rejected
                   proposal?) — the genuinely variable judgment
  zooms          : list[Zoom] (≤3, whitelisted enum):
                   strategy_history(id) | invariant_confrontations(id) |
                   regime_history(window) | proposal_thread(id)
```

### PYTHON — Variable execution (no LLM)
```
embed corpus_queries → cosine over BOTH matrices:
  passages (top-k, + their SUPPORTS-linked invariants)
  invariants directly (top-k — reaches agent-discovery / reference
  invariants that have no supporting passage)
execute whitelisted zooms.
```

### CALL 1b — Context Builder (LLM → PlannerContext)
```
LLM filters, orders, selects, builds.
Output PlannerContext via assemble_context tool.
```

### PYTHON — Bridged tools (PydanticAI deps injection)
```
_db lives in the agent deps — Worker never sees it.
The 3 bridged functions are PydanticAI tools.
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
asyncio.create_task() after WorkerResult — but the Monday chain AWAITS
this task before the digest (the digest renders the Proposal it creates);
create_task only buys parallelism within the UC8 step.
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
(event-driven: inbox watcher → ingestion + curation; backup after
         chain/batches; weekly chain is DUE-ON-START at launch/wake)

08:00   Weekly pre-processing (catch-up → UC3 → UC4, then mechanical)
          → CATCH-UP: market fetch (all days since last run) → regime
            detector step per new print → NAV/ratios → proposal-expiry sweep
          → event watch (pinned sources, LLM triage) → curation sweep
            (user deposits are ingested event-driven, before the chain)
          → Backtests recalculated → FAVORS edges
          → Scenario numeric triggers → ScenarioProbability TS
          → Invariant weights updated (incl. mechanical confrontations)
          → Portfolio valuations + ranking → portfolio_weekly_snapshot rows
          → Proposal outcomes + scenario calibration + strategy probation
            (mechanical/outcomes.py — see "Unified improvement cycle")
          → V2 only: learn_from_adaptations

09:00   Worker cycle
  09:00:00  Python     ~20ms — BASELINE (5 fixed queries, no LLM)
  09:00:01  Call 1a    LLM ~1s — variable margin (corpus_queries + zooms)
  09:00:02  Python     ~50ms — embed queries, cosine, whitelisted zooms
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
          → newly integrated / rejected invariants + strategies (mechanical,
            reported — not for approval; ADR-006)
```

---

## LLM Runtime — PydanticAI, no homemade abstraction

PydanticAI IS the model-agnostic layer (TASKS Phase 1bis): two Agent
instances (Planner via OpenRouter provider, Worker via Anthropic), models
in `.env`, structured outputs Pydantic-validated with the one-retry policy.
Swap model: change `.env` only. No `BaseLLMClient`, no factory, no wrapper.

---

## What User Does

```
Seeds      → runs UC0 once at install
Feeds      → PDFs, URLs, one-line notes (Telegram, `invest feed/note`, drop)
Challenges → questions theses via UC9 chat (Telegram or `invest chat`)
Reads      → weekly digest (push) | dashboard http://127.0.0.1:8765 —
             ranking, invariants + confrontation timelines, proposals &
             scoreboard, EventLog, semantic search, read-only SQL console
Arbitrates → accepts/rejects paper-mode Proposals only (buttons, CLI,
             dashboard — one command layer, ADR-005); invariant/strategy
             integration is mechanical, reported not arbitrated (ADR-006)
Defines    → drawdown rule, concentration, strategy enabled flag
```
