# IMPROVEMENTS.md — Deferred Features

See REVISION_NOTES.md for V1 scope, core concepts, and the ranking rule.
This file is the single backlog of features deliberately excluded from V1,
referenced as `I-N` from the other docs.

Features are deferred to keep the core focused on the 5 mechanisms that define
agent performance (regime detection, portfolio ranking, adaptation timing,
weekly decision, learning loop). Each item is **fully specified** so it can be
re-added without redesign once the MVP runs and we have signal on what's worth
adding.

---

## When to revisit this file

- MVP has been running for **≥3 months** with no architectural changes needed.
- `learn_from_adaptations` (V2) — or the V1 `outcomes.py` job (ex-I-23) — has
  produced ≥10 invariant weight updates from real performance.
- At least one user observation suggests a specific limitation that maps to an
  item below.

Don't add proactively. Add when a real gap shows up.

---

## I-0 — V2 auto-adaptive execution

**Why deferred:** V1 must first prove the ranking/digest/proposal loop.
Auto-application creates operational and financial risk before signal quality
is validated.

**Trigger to add:** when the V1 paper-mode proposal history shows stable
value-add versus the defender over at least 3 months (the V2 boundary in
REVISION_NOTES.md, measured by the `outcomes.py` scoreboard — ex-I-23).

**Spec:**
- 48h auto-validation can be enabled explicitly.
- Real allocation changes require concentration, drawdown, turnover, and
  defender-gap checks.
- `performance_3m` must be measured against the counterfactual defender, not
  as absolute return.
- Learning from real adaptations updates BACKED_BY invariants
  (`learn_from_adaptations`, Adaptation vertex, MODIFIES edge,
  `adaptation_quality` document type).
- Add Proposal→Portfolio edges (replacing the scalar
  `defender_id`/`challenger_id`) when graph traversal becomes useful.

---

## I-1 — Multi-Framework support

**Why deferred:** 4 Seasons (Dalio) covers stagflation and growth/inflation
regimes well enough for Phase 1 accumulation. V1 seeds `permanent` and
`liquidity-cycle` Framework vertices as metadata (`enabled=false`).
Full multi-framework graph traversal adds edge types and arbitration complexity.

**Trigger to add:** if learning (outcomes.py / V2) consistently shows that 4 Seasons
mispredicts regimes during liquidity cycles (e.g. 2020 QE event), activate
`liquidity-cycle` as a second framework.

**Spec:**
- Framework vertex already exists; flip `enabled=true`.
- New edges: `Strategy -[INSPIRED_BY weight]-> Framework`,
  `Framework -[DEFINES]-> RegimeType`.
- `RegimeType.framework_id` already present — migration is free.
- When a second framework is active: UC2 detects regime per framework, UC6+UC7
  re-evaluate strategies under each lens, UC8 decides if any framework's
  signal warrants a proposal.
- Arbitration: weight per framework by historical `Framework.accuracy`,
  updated mechanically from confirmed/failed regime predictions. Ranking
  remains over all enabled portfolios.

**Migration:** purely additive. No schema break.

---

## I-2 — Benchmark as vertex

**Why deferred:** In Phase 1, `benchmark` is a Portfolio-level string
("60/40-USD"). A vertex adds traversal overhead with no read benefit.

**Trigger to add:** when comparing portfolios against multiple benchmarks
becomes a regular Worker question.

**Spec:**
- Vertex `Benchmark {id, name, ticker, currency, description, active, trace}`
- Edge `Portfolio -[BENCHMARKED_AGAINST primary:BOOLEAN, since:DATE]-> Benchmark`
  (benchmark is a Portfolio concern, not Strategy-level).
- Seed: SPX, AGG, NASDAQ, MSCI World USD.
- `Portfolio.benchmark` string remains for backward compatibility.

---

## I-3 — Hypothesis pre-declared predictions

**Why deferred:** The learning mechanism already closes the loop via realized
performance propagated to BACKED_BY invariants. Hypothesis adds epistemic
rigor (falsifiability before outcome known) but does not directly improve
decisions; it improves *corpus quality over time*.

**Trigger to add:** when the agent has accumulated ≥20 agent-discovery
invariants and we want to filter out post-hoc rationalizations.

**Spec:**
```sql
-- Plain table (same convention as DATA_MODELS.md):
CREATE TABLE IF NOT EXISTS hypotheses (...);
-- id STRING (PK, ULID), invariant_id STRING, prediction STRING,
-- conditions STRING, emitted_at DATE, expiry DATE,
-- outcome STRING (default 'pending'), actual_data MAP, trace STRING
```
- Worker emits `hypotheses_proposed: list[Hypothesis]` in WorkerResult
  (add alongside `innovations_proposed`; empty list until this item lands).
- Monthly job `evaluate_hypotheses()` runs at expiry, sets outcome, updates
  invariant confirmation/infirmation counts with `source='hypothesis'`.
- Add to Worker prompt: `skill-emit-hypothesis.md`.
- EventLog types: `HypothesisProposed`, `HypothesisEvaluated`.

---

## I-4 — Per-invariant floor override

**Why deferred:** Floor by `author` tier (dalio=0.40, marks=0.35, null=0.20,
system=0.05) is sufficient at MVP scale. Per-invariant override is power-user
customization.

**Trigger to add:** when the user wants to manually elevate or depress
confidence in a specific invariant via UC9.

**Spec:**
- `Invariant.floor_weight` is already persisted at creation from the author
  tier; this item adds the ability to override it per-invariant afterwards.
- UC9 command: "Set floor for invariant {id} to {value}".
- Telegram syntax: `/floor gold-stagflation 0.30`.

---

## I-5 — Two-tier recency half-life

**Why deferred:** A single half-life of 365 days works adequately for both
structural and market invariants in MVP. Differentiation matters at maturity
when the corpus has >50 invariants of mixed origin.

**Trigger to add:** when Dalio-grade invariants visibly suffer from the short
half-life (drop below their natural confidence after 1y of market silence).

**Spec:**
- Structural invariants (author dalio/marks/other): `half_life = 730 days`
- Market invariants (author system, and I-10 user tiers): `half_life = 180 days`
- Store in `invariant_author_config.half_life_days`.
- `recency_factor = 0.5 + 0.5 × exp(-days_since / half_life)` (asymptotic
  floor 0.5)

---

## I-6 — Asset-class concentration limit

**Why deferred:** The single-asset limit (`max_single_asset_pct`, 40%) catches
the most dangerous concentrations. Class-level limit (e.g. no more than 60%
equities) is a refinement.

**Trigger to add:** when a proposal passes the single-asset check but produces
uncomfortable class concentration in practice.

**Spec:**
- `Portfolio.max_class_concentration: FLOAT` (default 60).
- Asset class mapping from `allowed_tickers.asset_class`.
- Writeback blocks if `sum(class_alloc) > max_class_concentration` for any class.

---

## I-7 — Auto-disable underperforming strategies

**Why deferred:** Manual disabling via UC9 is fine for 4 strategies.
Automation matters when the library grows beyond 10.

**Trigger to add:** when the strategy library exceeds 8 strategies.

**Spec:**
- `Strategy.consecutive_low_rank: INT` (incremented each week if the
  portfolios holding it rank > 3, reset otherwise).
- Threshold: `consecutive_low_rank >= 26` → `enabled=false` + Telegram
  notification.
- User re-enables via UC9.

---

## I-8 — Monthly self-evaluation scorecard (UC10)

**Why deferred:** The weekly Telegram digest already shows regime, ranking,
invariant changes. A formal monthly scorecard adds reflection but no decision
input.

**Trigger to add:** when the user wants periodic structured reflection on
agent quality (typical signal: recurring "how is the agent doing overall?"
questions).

**Spec:**
- New UC10 — monthly cron, 1st of month, 08:00.
- Aggregates: hypothesis outcomes (I-3), proposal/adaptation performance
  results, top invariant weight changes, strategies at risk, ranking
  stability.
- Output: ScorecardEvent + Telegram message.

---

## I-9 — Auto-veille channels (RSS, YouTube, X, podcasts)

**Why deferred (general RSS moved out of V1, 2026-07):** curation of
CHOSEN sources is the essence; a feed vacuum produces news noise, not
Dalio-grade invariants. V1 ships the narrow **UC3 Event Watch** (pinned
official sources — Fed/ECB/SNB press — LLM triage, bounded enrichment)
plus user deposits. This item covers everything broader: general RSS
(reactivate with I-26 tiering), YouTube/X/podcasts (transcription
overhead on top).

**Trigger to add:** when weekly deposits alone visibly starve UC4 curation,
or the user identifies specific sources with consistent Tier-1 signal.

**Spec:**
- RSS: feedparser over a curated feed list, dedupe by URL hash → inbox
  (watcher picks up); REQUIRES I-26 tiering from day one.
- yt-dlp for YouTube transcript extraction.
- whisper-cpp or yt-dlp audio→text for podcasts.
- X via Nitter scraping or official API (rate limits).
- All produce text → same `CorpusIngester.ingest_text()` pipeline.

---

## I-10 — Additional invariant author tiers

**Why deferred:** MVP uses the author tiers dalio / marks / null (other
corpus) / system. `arbitrage-user` (learned from user decisions) and
`discussion-user` (co-built in UC9) are sophistications.

**Trigger to add:** when UC9 has been active for ≥1 month and the user has
made several thesis decisions worth capturing as invariants.

**Spec:**
- Two new `author` tiers in `invariant_author_config`:
  `arbitrage-user` (floor 0.25, weight_initial 0.60-0.70) and
  `discussion-user` (floor 0.15, weight_initial 0.40-0.60).
- UC9 detects user decisions and proposes a new Invariant.
- Same mechanical lifecycle as any invariant (ADR-006): status:proposed →
  matured 25y → status:integrated iff time-validated. The user tiers only set
  the floor/weight_initial band, not a validation gate.

---

## I-11 — Worker emits Hypothesis alongside proposals

**Why deferred:** Coupled with I-3.

**Trigger to add:** simultaneously with I-3.

**Spec:**
- Worker prompt includes `skill-emit-hypothesis.md`.
- For each proposal emitted, Worker emits ≥1 Hypothesis testing the
  invariants that motivated it.
- Validates that the agent commits to falsifiable predictions, not just
  rationalizes after the fact.

---

## I-12 — Strategy condition orthogonality check (automated)

**Why deferred:** In MVP with 4 hand-coded strategies, conditions are reviewed
manually at seed time to ensure they don't merely restate the regime
definition.

**Trigger to add:** when the agent proposes a new Strategy autonomously
(`source=agent-discovery`, see I-13).

**Spec:**
- On any new Strategy creation, parse the `conditions` string.
- Extract referenced indicators (CPI, GROWTH_COMPOSITE, VIX, yield curve, etc.).
- Compare against the regime detection thresholds (in `system_thresholds`).
- If `conditions` references ONLY indicators that the regime already defines,
  reject with "circular condition — add at least one orthogonal dimension".

---

## I-13 — Strategy auto-discovery and onboarding

**Why deferred:** MVP has 4 strategies seeded by hand. Agent-discovered
strategies are an advanced capability.

**Trigger to add:** when the corpus contains ≥5 ingested strategy descriptions
and the Worker shows pattern recognition over the current library.

**Spec:**
- Worker can propose `Strategy source=agent-discovery status=proposed`.
- Auto-enabled after mechanical probation — no user gate (ADR-006).
- New strategy enters FAVORS ranking starting the next weekly cycle.
- Requires a minimum backtest history (3+ periods) before its portfolios
  become real candidates.

---

## I-14 — Regime transition probabilities

**Why deferred:** Reactive regime detection works. Anticipating "stagflation →
falling-growth" requires reliable transition matrices, which need years of
data.

**Trigger to add:** when the Regime instance history contains ≥20 transitions.

**Spec:**
- Compute P(next_regime | current_regime) from the Regime vertices ordered
  by `start_date`.
- Surface in PlannerContext as `regime_transition_probabilities`.
- Worker may anticipate transitions, but never proposes before a regime change
  is confirmed (V1 is explicitly reactive).

---

## I-15 — FX hedging consideration

**Why deferred:** Phase 1 = accumulation. Long horizon (15+ years) dilutes FX
volatility. Hedging adds cost and complexity.

**Trigger to add:** Phase 2 (distribution/income) or when the user explicitly
wants CHF stability over USD return.

**Spec:**
- Add CHF-hedged tickers to `allowed_tickers`: IUSE (S&P 500 CHF-hedged),
  AGGH (US Aggregate Bond CHF-hedged).
- Add `Portfolio.fx_hedge_target_pct: FLOAT`.
- Worker may propose substituting unhedged → hedged tickers.

---

## I-16 — Stress testing

**Why deferred:** Out of MVP scope by design.

**Trigger to add:** when ≥3 adaptations have been auto-applied in V2 and the
user wants forward-looking risk visibility.

**Spec:**
- Monthly job `stress_test(scenarios=[2008, 2020, 2022])`.
- For each historical crisis, replay the current allocation through that
  period's MarketData.
- Output: stressed drawdown, stressed Sortino.
- Telegram alert if stressed drawdown > 1.5× `max_drawdown_rule`.

---

## I-17 — Multi-portfolio support

**Why deferred:** A single live (defender) portfolio in MVP. Multi-portfolio
adds allocation aggregation across portfolios, FX exposure consolidation, etc.

**Trigger to add:** when the user wants tax-optimized splits (e.g. taxable
account vs retirement account).

**Spec:**
- `portfolio_check` tool accepts a list of portfolio IDs.
- Proposals/adaptations can target a specific portfolio.
- Aggregate Sharpe/Sortino reported across portfolios in the weekly digest.

---

## I-18 — Markdown Skills versioning

**Why deferred:** 4 skills hand-edited in MVP. Versioning matters when skills
evolve through agent self-improvement.

**Trigger to add:** when the user wants to A/B test different skill
formulations.

**Spec:**
- Skills stored in the DB as a `Skill` entity with `version` and `active`
  flags.
- Worker loads only `active=true` versions.
- Changes traceable via EventLog.

---

## I-19 — Signal vertex (qualitative market events)

**Why deferred:** Dropped from V1 (with the IMPLIES and GENERATES edges).
MarketData is a time-series, so it can never be a graph edge source; and
qualitative events are rare enough in V1 to live as strings.

**Trigger to add:** V2, if qualitative events need graph traversal.

**Spec:**
- V1: qualitative market events ("Greenspan nomination", communiqué shifts)
  live in the `Regime.events` / `Evaluation.events` STRING[] arrays.
- V2: promote to a proper `Signal` vertex with edges (IMPLIES → Regime,
  GENERATES → Evaluation) if traversal over qualitative events becomes a
  Worker need. **Naming:** call it `Signal`, NOT `Event` — the `EventLog`
  audit vertex already exists (DATA_MODELS.md) and must not be confused
  with market signals.

---

## I-20 — PMI data source — ✅ RESOLVED

**Decision (2026-07):** the growth axis uses **GROWTH_COMPOSITE** —
z(INDPRO YoY) − z(UNRATE Δ3m), rebased to index 100 — fully free, FRED-native,
automatic and perennial (no license risk, no manual pulls). Formula in
DATA_MODELS.md; detection algorithm in ARCHITECTURE.md.

Rejected candidates: DBnomics ISM mirror (third-party availability risk),
S&P Global US PMI (manual pull). Revisit only if GROWTH_COMPOSITE visibly
lags actual growth turns vs published PMI prints.

---

## I-21 — Transaction-cost / turnover model

**Why deferred:** V1 proposals are paper-mode; no real costs are incurred.

**Trigger to add:** before the V2 boundary is evaluated — "net of costs" in
REVISION_NOTES.md is not computable without it.

**Spec:**
- Explicit cost assumptions per asset (spread + commission, bps).
- UC8 gate gains a turnover term: expected gain must exceed switching cost.
- `system_thresholds`: `cost_bps_default`, `proposal_min_net_gain`.
- Interim: the Phase 9 shadow replay already applies a flat
  `replay_cost_bps` (10 bps/side) — this item refines it per asset when
  the V2 boundary is evaluated.

---

## I-22 — Mechanical scenario-probability algorithm

**Why deferred:** The weekly 08:35 job needs a defined algorithm; scenario
triggers mix numeric conditions ("CPI < 2.5") with qualitative ones
("Fed dovish") that no mechanical job can evaluate.

**V1 behavior (interim):** the weekly job evaluates **numeric triggers only**
against MarketData TS (week-over-week shift computed on read); qualitative triggers are
interpreted exclusively by the weekly Worker cycle, which may adjust
probabilities in its WorkerResult.

**Spec (to harden):**
- Formal trigger grammar (indicator, operator, threshold) stored as
  structured data instead of free text.
- Probability update rule (e.g. logistic blend of trigger hit-rate) defined
  and backtested, or the weekly job demoted to shift detection only.

---

## I-23 — Retrospective learning job — ✅ PROMOTED TO V1

**Decision (2026-07):** promoted into V1 core as `mechanical/outcomes.py`
(weekly 08:52 — see ARCHITECTURE.md "Unified improvement cycle").
Every Proposal (switch AND reallocation) receives an `outcome.verdict`
(won/lost) at +`proposal_outcome_weeks` (12), feeding
`invariant_confrontations.source='proposal'`; accepted paper-tests are
tracked weekly; the digest renders the cumulative hit-rate scoreboard —
the live continuation of the Phase 9 replay's `hit_rate_12w`. Naturally
silent during the first 12 weeks (no aged proposals yet), but specified and
tested from day one.
**Relationship to Phase 9:** the shadow replay covers the HISTORICAL
evidence at install time; outcomes.py is the FORWARD-looking measurement.
Both feed the V2 boundary; neither replaces the other.

---

## I-24 — Severity-weighted confrontations

**Why deferred:** `invariant_confrontations.severity` is recorded in V1 but
unused in `market_score` (simple count ratio).

**Trigger to add:** when confirmations accumulate and a single severe
refutation should outweigh many mild confirmations.

**Spec:**
- `market_score = Σ(severity × confirmed) / Σ(severity)` over confrontations.
- Backfill computable from the existing `invariant_confrontations` table.

---

## I-25 — OCR / scanned-PDF pipeline

**Why deferred:** V1 assumes text-extractable PDFs.

**Trigger to add:** first scanned/image-only corpus document worth ingesting.

**Spec:** tesseract (or vision-LLM page transcription) → same
`CorpusIngester.ingest_text()` pipeline.

---

## I-26 — Veille source-quality tiers

**Why deferred:** V1 treats all RSS sources equally.

**Trigger to add:** when noisy feeds visibly pollute UC4 curation.

**Spec:**
- `rss_sources` document type with `tier` and rolling signal-quality score
  (how often a source's items end up cited in Evaluations/Invariants).
- UC3 prioritizes by tier; low-tier items summarized, not fully curated.

---

## I-27 — Schema self-extension (moved out of V1)

**Why deferred:** a proposed vertex/edge/property type is dead weight until
someone writes the code that reads and writes it — in V1 the agent cannot
ship code, so a validated schema extension could never become functional.
V1 innovations are `new_invariant` / `new_strategy` / `strategy_revision` /
`process` / `data`.

**Trigger to add:** V2, when innovations can be paired with deployable
behavior changes.

**Spec (restore what V1 removed):**
- `schema_extensions` document type (id ULID, improvement_type, name,
  spec MAP, status, proposed_at, validated_at, rationale).
- `ImprovementType` members `schema_vertex` / `schema_edge` /
  `schema_property`.
- Explicit user validation before any CREATE; CREATE executed by Writeback.

---

## I-28 — Multiple independent conditions per invariant

**Why deferred:** V1 gives each invariant ONE `condition` (a conjunction of
predicates over known signals — e.g. rising-and-decelerating inflation) and
ONE `effect`; its confrontation frequency is emergent from that condition
(event → per occurrence, persistent state → per episode, 'always' → weekly).
A single invariant could legitimately carry SEVERAL *independent* trigger
conditions with different effects (OR-semantics, not the AND-conjunction V1
already supports) — e.g. one behaviour under rising inflation AND a distinct
one on rate hikes. Modelling that means a list of (condition, effect) pairs
and a market_score aggregated across them — extra surface the curator must
synthesise reliably, for a case that is real but rare.

**Trigger to add:** when a concrete invariant is genuinely two rules wedged
into one and splitting it into two invariants is worse than modelling both
conditions on one.

**Spec:** promote `condition`/`effect` from single objects to a
`criteria : [{condition, effect}]` list; mature_invariant() iterates criteria;
market_score aggregates confirm/infirm across all of them.

---

## Implementation order recommendation

If/when adding from this list, prioritize by dependency and impact:

1. ~~I-20 (PMI source)~~ — **resolved**: GROWTH_COMPOSITE shipped in V1.
2. ~~I-23 (retrospective learning)~~ — **promoted**: shipped in V1 as
   `outcomes.py` (unified improvement cycle).
3. **I-21** (cost model) — needed before the V2 boundary can be evaluated.
4. **I-3 + I-11** (Hypothesis) — closes the epistemic loop, prevents post-hoc bias.
5. **I-5** (two-tier half-life) — small change, real fairness gain for Dalio-grade.
6. **I-8** (monthly scorecard) — visibility, builds user trust.
7. **I-7** (auto-disable) — needed when the strategy library grows.
8. **I-1** (multi-framework) — only when 4 Seasons shows clear blind spots.
9. The rest as triggered by real usage.

---

## What never goes here

- Anything that lets the agent AUTO-EXECUTE a real allocation change in V1 —
  the manual-execution boundary is the V1/V2 line (ADR-006). (Integrating an
  invariant is autonomous; moving real money is not.)
- Anything that integrates an invariant OTHER than through the mechanical
  maturation verdict (N_min/θ, not refuted) — no back-door `status=integrated`
  that skips the 25y confrontation.
- Anything that bypasses EventLog append-before-commit ordering.
- Anything that gives Worker direct DB write access.
- Anything that increases *scheduled autonomous* LLM decision-making beyond
  the weekly cycle (mechanical jobs can run as often as needed;
  user-initiated UC9 chats and their capped ad-hoc UC8 re-run — max 1/day —
  are explicitly allowed, being user-triggered; the event-driven curation
  curator is also allowed because it only extracts knowledge into candidates
  that mature mechanically — it never decides anything).
