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
("all-weather-USD"). A vertex adds traversal overhead with no read benefit.

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

## I-9 — Auto-watch channels (RSS, YouTube, X, podcasts)

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
  matured 35y → status:integrated iff time-validated. The user tiers only set
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

**Settled at M5-bis (2026-07-15) — list = OR, string = AND.** The interim
free-text form still needed ONE combination rule, and M5 chose "the whole
list is jointly necessary" (a documented judgment call). That contradicts
this ADR-level example in ARCHITECTURE ("Strategy '4 Seasons'"): `bull:
CPI_YOY < 2.5 AND GROWTH_COMPOSITE > 102` but `bear: ^VIX > 25 OR (CPI_YOY
> 4 AND GROWTH_COMPOSITE < 98)` — bull ANDs, bear ORs, and a bare JSON array
carries no operator to tell them apart. Refuted on the seed's own data: every
bear list contains `^VIX > 25` (16.73% of weeks raw), yet four-seasons-rp's
bear — `^VIX > 25` OR stagflation — warm-started at 1.37%. A superset cannot
be 12x rarer than its own subset. Now: each STRING is a conjunction (which is
what `parse_trigger_conjunction` was always for), the LIST is a disjunction,
and the seeded bulls were merged into single AND-strings to keep their
meaning. Live effect: 4s bear 1.37% -> 18.16%, permanent-browne bear 7.68%
-> 32.99%, bounded correctly by max(parts)=16.73% and sum(parts)=20.30%.

**Spec (to harden):**
- Formal trigger grammar (indicator, operator, threshold) stored as
  structured data instead of free text — the list/string convention above is
  a working interim, not a grammar: it cannot express precedence, negation,
  or an OR nested inside a string.
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
(the active days are sampled at one-horizon spacing — ARCHITECTURE
"Invariant confrontation rule").
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

## I-29 — Env-configurable fetch universe

**Why deferred:** the fetch universe (which tickers, sources, transforms,
availability lags) and the composite/derived-signal definitions are pinned in
`db/seed_data.py` (`ALLOWED_TICKERS`, `HISTORY_PROXIES`, `DERIVED_SIGNALS`) —
Task 2.1 makes the fetcher "driven by the allowed_tickers documents". An
earlier design also exposed `YAHOO_FINANCE_TICKERS`/`FRED_SERIES`/
`*_COMPONENTS` as `.env` vars, but nothing ever read them (the seed drives off
`ALLOWED_TICKERS`); required-but-unused, they only invited silent drift (BIL
lingered in the env list after being retired everywhere else). Removed at M2
rather than left as inert config (CLAUDE.md: no dead config).

**Trigger to add:** when the universe must change per-deployment WITHOUT a code
edit + re-seed — e.g. a second user with a different investable set, or A/B
running two universes. Until then, editing `ALLOWED_TICKERS` and re-seeding is
the single, authoritative path.

**Spec:** a startup reconciler that reads an optional `.env` override, diffs it
against `allowed_tickers`, and applies adds/deactivations through the running
agent (never a raw table write) — with a divergence check so the env and the
table cannot silently disagree.

---

## I-30 — Authoritative market_data backfill (stale-row pruning)

**PARTIALLY RESOLVED at M5 (2026-07-15) — the RETIRED-TICKER half is fixed.**
The M4 deferral rested on "it does not affect any verified result". That
stopped being true at M5: `mechanical/backtests.py investable_tickers` reads
`allowed_tickers`, so the ghost BIL row made `asset:BIL` a VALID invariant
handle, maturable against a series frozen at its M2 retirement — and the same
table gates the Worker's `market_fetch` (M8). Owner approved the prune once
shown it was reachable. `seed._prune_retired_series` (step 1b) now deletes,
on every run, the `allowed_tickers` / `market_data` / `benchmark_valuation`
rows for tickers outside the authoritative universe (`ALLOWED_TICKERS` ∪
`DERIVED_SIGNALS` — the union is load-bearing: `real_rate`/`real_yield_10y`
exist only in the latter, so a keep-set built from ALLOWED_TICKERS alone
deletes the gold invariant's own signal). Pruned live: BIL 4810 rows,
EURUSD=X 5867, JPY=X 7701 (18378 total). Regression:
`test_m5_prune_removes_ghosts_but_spares_derived_signals`.

**STILL DEFERRED — the RE-DATING half** (the second bullet below): a change
to `availability_lag_days`/source/frequency re-dates a series and writes NEW
rows beside the orphaned old ones, INSIDE a still-allowed ticker (M2SL: 1768
rows where 35y monthly ≈ 420). Pruning by ticker cannot see that — it needs
the authoritative-backfill design at the end of this item. Not urgent, for
the original reason: `GLOBAL_LIQUIDITY` is rebuilt in-memory from fresh
fetches, so no consumer reads the stale M2SL copies today.

**Why it was deferred (M4):** found while verifying the NAV engine against
external sources; owner call to log rather than fix, since it did not affect
any verified result then (every series M4/M3 actually read is clean — see
below) and the fix deletes rows from the live DB.

`append_ts_batch` is `INSERT OR REPLACE` keyed on `(ticker, ts)` and nothing
ever deletes, so `market_data` accumulates a layer of rows from every code
version ever run. Two ways it bites:
- **Retired tickers keep their whole series.** Measured on the live DB at M4:
  `BIL` 4810 rows, `EURUSD=X` 5867, `JPY=X` 7701 — all three retired at M2
  (BIL → synthetic `cash`; the Yahoo FX pair → FRED `DEXUSEU`/`DEXJPUS`), all
  three still present and still reaching 2026-07-13.
- **Re-dating a series duplicates it instead of moving it.** Any change to
  `availability_lag_days` (or to the source/frequency) re-dates every
  observation, writing NEW rows and orphaning the old ones. `M2SL`: 1768 rows
  where 35y monthly = ~420, as several overlapping copies (a 1294-row weekly
  block, plus ~2 monthly copies/year in 2024-25, plus a 1990-era value of
  3332.6 dated 2012-04-26).

**Not affected (verified at M4, not assumed):** the ALFRED first-release series
are dated by `realtime_start`, which is stable across code versions, so they
never duplicated — `CPIAUCSL`/`INDPRO`/`UNRATE`/`GROWTH_COMPOSITE` all hold
418-419 rows (11.7/y = exactly 35y monthly), and the daily tradables hold
~246 rows/y (exactly the trading calendar). So the regime detector, the NAV
engine and the ranking all read clean series today. `GLOBAL_LIQUIDITY` is
rebuilt in-memory from freshly-fetched components each run rather than from
its stored component rows, so the composite is clean despite `M2SL` not being
— but that also means the stale `M2SL` rows are a trap for any future consumer
that reads the component from the DB.

**Trigger to add:** before go-live, OR as soon as anything reads a macro
component series back from `market_data` (the Worker's `market_fetch` already
can, and an invariant `condition` on `M2SL` would today read the garbage), OR
the next time a ticker is retired or an `availability_lag_days` is changed.

**Spec:** make a full backfill authoritative rather than additive, without
letting the sliding 35y window eat genuine history:
- per ticker in step 9, after computing the fresh series, delete rows for that
  ticker WITHIN the fresh series' own date range that are not in it (rows
  outside the range are older history, deliberately kept);
- once after the ticker loop, prune tickers absent from `allowed_tickers`;
- leave `append_ts_batch` itself additive — the Monday catch-up path is
  genuinely incremental and must never delete.
Not a blanket `DELETE FROM market_data WHERE ticker=?`: `target_start` is
`today − 35y` and moves forward every run, so delete-then-insert would discard
the oldest year of real history on each seed.

**Required guard (the reason this is not a 10-minute job).** An authoritative
delete is only safe if the fresh series is TRUSTWORTHY, and step 9 cannot
currently tell a complete fetch from a truncated one. Its per-ticker
`except → continue` catches a fetch that FAILS; it does nothing about a fetch
that SUCCEEDS with partial data (Yahoo does return short/partial histories on
a bad day). Fresh-but-truncated + authoritative-delete = permanent loss of
genuine rows, and the pruning path makes it worse, since a ticker that
momentarily looks absent would have its whole series dropped. So the fix must
carry a coverage sanity check BEFORE any delete — e.g. the fresh series must
cover a plausible fraction of the expected span/row-count for its frequency,
and any shortfall must abort that ticker's delete (keep the additive write)
and report, never delete on a hunch. Take a `sqlite3 .backup` first regardless
(CLAUDE.md: confirm before data deletion).

---

## I-31 — ~~Realloc gate 6: citation relaxation~~ — REFUSED (owner, 2026-07-15)

**Resolution:** the owner refused any V1 constraint relaxation — gate 6
stays integrated-only. The problem this item described (the honest
confrontation left 4 of 6 seed invariants `proposed` forever in the
0.35–0.60 dead band, starving the citation loop) was solved on the OTHER
side, as the owner directed: make the engine QUALIFY. The verdict gained a
mechanical 'inadequate' rejection branch — rejected iff the Wilson upper
bound of market_score at `invariant_verdict_confidence` (0.95) is < θ, i.e.
demonstrably unable to reach the bar. 'proposed' now means insufficient
evidence only, and empties as N grows. Spec: ADR-006 amendment
(docs/DECISIONS.md) + ARCHITECTURE "Birth maturation" TIME-VALIDATION
VERDICT. Nothing remains deferred here; kept as the record of a refused
alternative (do not re-propose without new evidence).

---

## I-32 — Seed invariant effect re-specification (post-M5-verdict rework)

**Why deferred:** the M5 challenge point is owner territory — the verdicts
below are *fair measurements of mis-posed questions*, and re-posing them is a
philosophy edit, not a code fix.

What the honest 35y maturation showed (baseline-relative, start-anchored,
12w horizon):
- `inv-diversification-drawdown` (rejected, 0.101): its `cross_strategy`
  effect benchmarks four-seasons-rp's drawdown against permanent-browne (25%
  cash) and barbell-taleb (85% safety) — a risk-parity portfolio SHOULD lose
  a drawdown contest to those. The claim is "diversification beats
  CONCENTRATION"; the roster tests "risk parity beats even-safer". Re-specify
  the effect (e.g. vs an equities-only / 60-40 reference, or method
  `absolute` vs its own concentrated sleeve) before reading 0.101 as history
  rejecting Dalio.
- `inv-inflation-persistence-tips` (N=8): TIPS data floor is 2000
  (VIPSX) — N grows by ~1 every 3 years. Nothing to fix; certification is
  just slow for this one. Its 0.75 on N=8 is the only seed invariant showing
  skill.
- The rest sit at the 0.50 null: publicly-known macro conditions show no
  12-week cross-class edge, which is the efficient-market default. Two
  levers if the M7 corpus factory keeps landing candidates at 0.50: (a)
  effects on RISK metrics (max_drawdown, volatility) where macro conditions
  plausibly carry more signal than on relative returns; (b) per-invariant
  horizon (`effect.horizon_weeks`) — Dalio-scale claims may live at 6-24
  months, not 12 weeks. Both are schema-light but change what "matured"
  means; decide only with factory-scale evidence, not on 6 data points.

**Trigger to revisit:** the M7 STOP point (candidates/principles ratio), or
M8b if the Worker's cited-invariant pool looks too thin to reason with.

---

## I-33 — Contradiction check is blind to handle CONTAINMENT

**Why deferred:** found by the M5 quality audit; narrow today (one asset
handle exists), but it widens with every `asset:<ticker>` invariant M7's
factory produces.

`find_contradictions` compares `effect.handle` as an exact STRING, so it
flags `asset-class:equities` outperform vs `asset-class:equities`
underperform, but never `asset:GLD` outperform vs
`asset-class:gold-commodities` underperform — even though that class
CONTAINS GLD and the two claims genuinely oppose on the same lever. Same
blind spot for two assets in one class (`asset:SPY` vs `asset:VTI`, both
US_EQUITY). The check exists precisely to catch "a knowledge defect the
market-score alone will not catch" (ARCHITECTURE), and each invariant can be
individually well-confirmed while the pair is incoherent — so string
equality under-delivers on its own purpose.

**Spec:** treat handles as overlapping when one's constituent set intersects
the other's, using the mapping `investable_tickers` already builds
(ticker → coarse class): `asset:X` overlaps `asset-class:C` iff
`class_of(X) == C`; `asset:X` overlaps `asset:Y` iff `X == Y` (NOT if merely
same-class — SPY and VTI are near-identical, but SHY and TLT are both
'bonds' and legitimately oppose). Strategy handles are unaffected. Cheap:
the pairwise scan already runs over the integrated set only.

**Trigger to add:** the first M7 batch containing an `asset:<ticker>`
invariant whose class also carries a class-level invariant — or simply when
the integrated set first holds both handle kinds on one class.

---

## Implementation order recommendation

If/when adding from this list, prioritize by dependency and impact:

1. ~~I-20 (PMI source)~~ — **resolved**: GROWTH_COMPOSITE shipped in V1.
2. ~~I-23 (retrospective learning)~~ — **promoted**: shipped in V1 as
   `outcomes.py` (unified improvement cycle).
3. **I-30** (authoritative backfill) — a correctness fix on real stored data,
   not a feature; do it before go-live or at the next ticker/lag change.
4. **I-21** (cost model) — needed before the V2 boundary can be evaluated.
5. **I-3 + I-11** (Hypothesis) — closes the epistemic loop, prevents post-hoc bias.
6. **I-5** (two-tier half-life) — small change, real fairness gain for Dalio-grade.
7. **I-8** (monthly scorecard) — visibility, builds user trust.
8. **I-7** (auto-disable) — needed when the strategy library grows.
9. **I-1** (multi-framework) — only when 4 Seasons shows clear blind spots.
10. The rest as triggered by real usage.

---

## What never goes here

- Anything that lets the agent AUTO-EXECUTE a real allocation change in V1 —
  the manual-execution boundary is the V1/V2 line (ADR-006). (Integrating an
  invariant is autonomous; moving real money is not.)
- Anything that integrates an invariant OTHER than through the mechanical
  maturation verdict (N_min/θ, not refuted) — no back-door `status=integrated`
  that skips the 35y confrontation.
- Anything that bypasses EventLog append-before-commit ordering.
- Anything that gives Worker direct DB write access.
- Anything that increases *scheduled autonomous* LLM decision-making beyond
  the weekly cycle (mechanical jobs can run as often as needed;
  user-initiated UC9 chats and their capped ad-hoc UC8 re-run — max 1/day —
  are explicitly allowed, being user-triggered; the event-driven curation
  curator is also allowed because it only extracts knowledge into candidates
  that mature mechanically — it never decides anything).
