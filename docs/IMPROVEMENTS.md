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
mechanical 'inadequate' rejection branch — rejected once a true rate of θ
becomes an implausible source of evidence this bad at
`invariant_verdict_confidence` (0.95), i.e. demonstrably unable to reach the
bar. 'proposed' now means insufficient evidence only, and empties as N grows.
(Stated with a Wilson upper bound at M5; restated as the exact binomial tail
by the M5-bis amendment, which left this branch's verdicts unchanged on the
real board.) Spec: ADR-006 amendments (docs/DECISIONS.md) + ARCHITECTURE
"Birth maturation" TIME-VALIDATION VERDICT. Nothing remains deferred here;
kept as the record of a refused alternative (do not re-propose without new
evidence).

Note the symmetry the owner's ruling eventually forced BOTH ways: M5-bis
found integration had the mirror defect — a bare point test that got easier
as evidence shrank — and closed it with the same device. Rigor to reject,
credulity to accept was never a coherent pair.

---

## I-32 — Seed invariant effect re-specification (post-M5-verdict rework)

**Why deferred:** the M5 challenge point is owner territory — the verdicts
below are *fair measurements of mis-posed questions*, and re-posing them is a
philosophy edit, not a code fix.

What the honest 35y maturation showed (baseline-relative, horizon-spaced
moments, 12w forward horizon). Numbers below are the LIVE board as of
2026-07-15, post-M5-bis (`integrated` now needs effect size AND evidence —
ADR-006):
- `inv-diversification-drawdown` (rejected, 1/20 = 0.050): its
  `cross_strategy` effect benchmarks four-seasons-rp's drawdown against
  permanent-browne (25% cash) and barbell-taleb (85% safety) — a risk-parity
  portfolio SHOULD lose a drawdown contest to those. The claim is
  "diversification beats CONCENTRATION"; the roster tests "risk parity beats
  even-safer". Re-specify the effect (e.g. vs an equities-only / 60-40
  reference, or method `absolute` vs its own concentrated sleeve) before
  reading 0.050 as history rejecting Dalio.
- `inv-inflation-persistence-tips` (proposed, 9/14 = 0.643): clears θ on the
  point estimate but not the evidence bar — a zero-edge invariant produces
  9-of-14 21% of the time. It held `integrated` under the pre-M5-bis rule.
  N is the binding constraint: the inflation-protected class floor is
  2003-12 (TIP's own ETF inception), not 2000 as this item said before.
  Whether that floor is movable is I-34; nothing to re-specify HERE.
- `inv-low-real-yields-favor-gold` (integrated, 53/82 = 0.646, null tail
  0.005) is the only invariant that has earned integration — the only one
  whose evidence excludes the no-condition null.
- The rest sit at the 0.50 null: publicly-known macro conditions show no
  12-week cross-class edge, which is the efficient-market default. Lever if
  the M7 corpus factory keeps landing candidates at 0.50: effects on RISK
  metrics (max_drawdown, volatility), where macro conditions plausibly carry
  more signal than on relative returns. Schema-light, but it changes what
  "matured" means — decide with factory-scale evidence, not on 7 data points.

**The per-invariant horizon lever is CLOSED (tested 2026-07-15, owner
request).** `effect.horizon_weeks` looked like the obvious answer to
"Dalio-scale claims live at 6-24 months, not 12 weeks". It is not, and the
reason is structural rather than fixable: moments must be spaced one horizon
apart to stay independent (ARCHITECTURE, "WHY horizon-spaced"), so a 4x
longer horizon costs 4x the sample. Measured on `inv-liquidity-easing-risk`
across the real 35y:

    horizon   moments    N   score   verdict
       12w        76     59  0.5593  proposed
       26w        40     33  0.5152  proposed
       39w        29     23  0.5652  proposed
       52w        24     20  0.6000  integrated  <-- 12/20, exactly theta

The 52w "integration" is 12 of 20 — a pass a zero-edge coin delivers 25% of
the time, with a 95% interval of [0.42, 0.76] that does not exclude the null.
No horizon structure, just a bar that gets easier as N shrinks (it is what
exposed the M5-bis defect). At the limit, 35 years hold ~35 non-overlapping
12-month windows and far fewer with any condition attached: a 12-month effect
is NOT certifiable at 95% from this history, however it is posed. 12 weeks is
not a compromise — it is the longest horizon 35 years can speak to. Longer
claims need either an explicitly lower confidence bar (owner call, not a code
change) or overlapping windows (which breaks the independence the binomial
tails assume — i.e. it fabricates evidence).

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

## I-34 — TIP/VIPSX splice: resampled validation was never tried

**Why deferred:** it re-opens an M2 owner decision, and the upside lands on
the one thing M6 does not read.

`HISTORY_PROXIES` maps `TIP -> VIPSX (2000)`, and every seed run since M2
logs `step 9: splice TIP/VIPSX rejected, ETF-only floor`. Measured on the
real 2003-12..2005-12 overlap: TIP vs VIPSX correlates **0.890 daily**
(under `MIN_RETURN_CORR` 0.94, hence rejected) but **0.9953 monthly**. That
profile is the documented GLD case exactly — VIPSX is a mutual fund with one
NAV struck at 4pm, TIP trades intraday, so the daily clock disagrees, not
the series — and it is a BETTER monthly fit than SHY/VFISX (0.963), which
`seed.RESAMPLED_VALIDATION_TICKERS` already accepts. Adding TIP there would
recover 2000-06..2003-12 for the `inflation-protected` benchmark class,
~14 quarters, on `inv-inflation-persistence-tips` (9/14) — the one seed
invariant whose verdict is gated purely by N.

**Why it is NOT filed as a bug (2026-07-15).** M2 already weighed this pair.
The defender's own trace records the investigation: *"TIPS didn't exist
before 1997, so no free proxy can extend TIP's own history past its 2003 ETF
inception (VIPSX/VAIPX/PRTNX/ACITX all tried, none clear the splice gate
cleanly) — IEF is the closest behavioral match found (corr 0.77 vs TIP...)"*.
The answer M2 chose was to fix the PORTFOLIO (TIP -> IEF), not the splice
gate, and the portfolios have run on IEF ever since. Admitting TIP now
changes an owner decision on a considered trade-off. It was tried and
reverted at M5-bis for exactly this reason.

**Scope note:** this touches NOTHING M6 consumes. Portfolios hold IEF, so
`portfolio_nav` already reaches 1986-1991 and the replay window is intact;
the only consumer of the `inflation-protected` benchmark class is invariant
confrontation, and the mechanical replay is blind to invariant weights
(ARCHITECTURE). Pure invariant-evidence upside.

**Trigger to revisit:** whenever I-32's re-specification pass is taken up —
decide the TIPS floor and the TIPS effect together, since both bear on the
same invariant. Do not take it alone.

---

## I-35 — FAVORS: the per-regime ranking is noise in 4 of 5 regimes

**Why deferred:** it is not a code defect — FAVORS computes exactly what it
claims. The finding is about the DATA's power, and M6's own walk-forward is
the right instrument to price it. Logged so M6 is read WITH this, not against
it.

**Method (2026-07-15, pre-M6).** The M5 pass built FAVORS and never tested
it; only the invariant half of M5 was challenged. Tested here the way the
invariant defects were found — against a null, not by reading code. FAVORS is
consumed as a WITHIN-regime ranking ("in regime R, prefer strategy X"), so
the null is "the regime label carries no information about which strategy
wins": permute regime labels across the 89 completed episodes (whole rows, so
each episode keeps its 4-strategy vector and cross-strategy correlation is
preserved), 20k draws, statistic = the observed within-regime spread
(max−min of strategy means), metric `sortino_rolling`.

    regime                             n   observed  null p50   p
    falling-growth-falling-inflation  17       1.23      0.49   0.034  <- real
    falling-growth-rising-inflation   17       0.18      0.50   0.942
    rising-growth-falling-inflation   15       0.40      0.53   0.684
    rising-growth-rising-inflation    13       0.60      0.56   0.457
    uncertain                         27       0.66      0.41   0.208

**Four of five regimes are indistinguishable from random labels.**
Stagflation — the regime this whole system is designed around — is the
worst: p=0.942, and its observed spread (0.18) is SMALLER than the median
random spread (0.50). The four strategies resemble each other MORE in
stagflation than four randomly chosen episodes would. And the winning margins
FAVORS acts on are 0.02–0.05 sortino: `momentum-macro` takes
rising-growth-falling-inflation from barbell by exactly **0.02**, which is
`ranking_tiebreak_window` — the project's own declared threshold for "these
are TIED". FAVORS breaks ties the ranking rule refuses to break.

What FAVORS effectively says is "hold barbell" (best unconditionally at 1.93
vs 1.67/1.68/1.56) with noise on top. The one solid result is economically
credible and worth keeping: in falling-growth-falling-inflation (deflationary
bust, p=0.034) barbell sits +0.07 above its own norm while the other three
fall −0.46..−0.78 — a barbell protecting in a deflation is precisely its
design.

**Rejected fix — do NOT make FAVORS baseline-relative.** Proposed and
withdrawn the same day. The instinct was to reuse the M5-bis invariant fix
(score the per-regime mean against the strategy's own unconditional mean),
but it answers the wrong question. An INVARIANT asserts ("if C then H
outperforms") and must be tested against "H outperforms anyway", so
baseline-relative is right. FAVORS CHOOSES ("in R, hold which?"), and the
right answer is the highest ABSOLUTE performer in R. Baseline-relative would
have picked `permanent-browne` for stagflation — the worst strategy of the
four overall (1.56) — because it least underperforms its own norm. Holding
the worst strategy for being "less bad than usual" is not a defect being
fixed, it is one being introduced. Regimes having different base rates is not
a bug; it cancels inside a within-regime ranking.

**Consequence for M6 (the reason this is logged BEFORE it, not after).** Task
9.2's grid calibrates the blend weights of `0.4×scenario + 0.6×favors`. The
0.6 leg is noise in 4 of 5 regimes. This is not a reason to delay M6 — the
walk-forward split (calibrate ~25y, validate ~10y) exists exactly to price an
input like this, and a machine finding it on held-out data beats an analyst
finding it by hand. But it flips how one result must be read: **if the
calibration returns a HIGH, STABLE favors weight on the holdout, treat it as
suspicious rather than as confirmation.** The signal is not in the data at
this episode count; a strong weight is more likely 5 knobs overfitting ~25y
than a discovery.

**Trigger to revisit:** M6's calibration output. If it drives the favors
weight toward 0, this item is answered and FAVORS is honest dead weight in
the blend (decide then whether to keep the edge at all). If it does not,
reconcile that against the table above before trusting the number.

**M6 answer (2026-07-16):** answered in the first direction, and more
sharply than posed — after the mechanical repairs (own-strategy guard,
scenario hysteresis) the final 729-point regrid shows favors=0 winning but
favors=0 and favors=1 INTERLEAVED through the top 15: the blend's composition
does not separate candidates at all. Not just "FAVORS is dead weight" — the
whole reallocation leg is noise whichever way it is blended (the only knob
that consistently helps is capping its turnover). The pre-repair grid had
instead picked favors=1.0 in-sample and collapsed to -3.2 pts/y on the
holdout — the whipsaw handles were what let it manufacture that. Whether the
blend's favors leg is kept at all is part of the M6 STOP decision
(docs/MILESTONES.md M6 Findings).

---

## I-36 — Seed re-run silently clobbers calibrated system_thresholds

**Why deferred:** the correct fix is a semantics choice (see below) the owner
should make once, not a hotfix; and nothing is corrupted until the FIRST
calibration `--apply` meets the NEXT seed re-run — a window that is knowable
and short.

**The defect.** `seed.py`'s reference-table step writes every
`SYSTEM_THRESHOLDS` key with `INSERT OR REPLACE` (src/investment/seed.py:141).
The incremental-seed plan RE-RUNS the seed at M7 (docs/MILESTONES.md
"Incremental seed"). So: M6's Task 9.2 calibration writes a user-confirmed
winning set via `apply_thresholds()` (UserDecisionEvent + UPDATE, one
transaction) → the M7 seed re-run resets every one of those keys to the
hardcoded seed values → no error, and the EventLog now asserts values the
table no longer holds. The audit trail contradicts the state, which is the
exact failure EventLog-first exists to prevent.

**Fix directions (pick one):**
- `INSERT OR IGNORE`: new keys land on re-run, existing values are never
  touched. Cost: a deliberate seed-data change to an existing key no longer
  propagates via re-run — post-calibration that is arguably the CORRECT
  default (changing a live threshold should be a decision, not a side effect
  of reseeding), but it changes what "idempotent seed" means for this table.
- Provenance column (`source: seed | calibrated | user`) and replace only
  `source='seed'` rows. Cleaner semantics, costs a schema change while
  `CREATE TABLE IF NOT EXISTS` is still the migration story.

**Trigger to revisit:** BEFORE the first real calibration `--apply`, or
before M7's seed re-run — whichever comes first. Until fixed, a re-run after
an `--apply` must re-apply the confirmed set (the UserDecisionEvent payload
carries it).

---

## I-37 — Vintage sensitivity is unreportable: no revised-series axis exists

**Why deferred:** it needs data that was deliberately never stored (ADR-003
keeps FIRST-release vintages only), so it is a fetch + storage design, not a
replay change.

**The gap.** M6's DoV says "vintage_mode=first_release; vintage sensitivity
reported". The first half is done (`replay_report.vintage_mode`). The second
half means: re-run the SAME replay on the REVISED (current) macro series and
report how far the verdict moves — it bounds what the PIT discipline actually
bought. `market_data` has one row per (ticker, ts) — the ALFRED first release
— so the revised values simply do not exist locally; there is no second
vintage axis in the schema.

**Scope if built:** fetch current-vintage FRED for the macro series only
(CPIAUCSL, M2SL, the GROWTH_COMPOSITE inputs — Yahoo prices do not revise);
store under a parallel namespace (ticker suffix `@latest` or a `vintage`
column); re-run detector → derived signals → replay on that variant; report
the verdict delta. The consumer at risk is the REGIME DETECTOR (its
level/speed/acceleration reads move with revisions); everything else is
price-based.

**Trigger to revisit:** before go-live if the M6 STOP is passed (it is a DoV
line); otherwise strike the DoV sub-item explicitly as unmeasurable-in-V1 —
an owner decision either way.

---

## I-38 — Regime axis is a lagged MACRO-publication signal; books need a MARKET signal

**Why deferred:** it re-opens the M3 detector and touches the Dalio framing in
CLAUDE.md — a re-scoping of the project's core, not a V1 tweak. Consigned so
the M6 STOP decision is made WITH it, and so it is gated behind a cheap test
(the stress-hedge, below) rather than built speculatively.

**The finding (M6, 2026-07-19).** The mechanical core's map from regime to
book is refuted for 2 of 4 quadrants (docs/MILESTONES.md M6 Findings), and the
root cause is not the books — it is that the detector labels the WRONG thing.
It runs on GROWTH_COMPOSITE + CPI (first-release macro prints: ~2 months
lagged, then 3-print hysteresis), so a "falling growth" label lands after the
market has already priced and traded the move. 12 of 17 falling-growth-
falling-inflation episodes have POSITIVE equities (they are recoveries and
benign disinflations, not crises); the defensive book designed for them misses
the rebound. The books were designed for MARKET regimes (crisis/boom); the
detector delivers macro-publication regimes. The only validated signal
(`^VIX > 25`, I-35) is the only axis measured at market speed.

**Three convergent design directions (all point the same way — coarser +
contemporary):**
1. **Benign slowdowns should not trigger a regime change at all.** If most
   detected episodes are benign (12/17 above), the response to them is "do
   nothing", and generating a costly allocation change for each is the defect.
   → the granularity is too FINE. The evidence supports ~2 states
   (stress / not-stress), not 5 quadrants — which also directly cuts the
   turnover cost M6 measured. "Detect earlier" only helps for episodes that
   DO warrant a response (real stress), and for those VIX already fires
   contemporaneously.
2. **Reframe the objective from unsupervised to supervised.** Not "what is the
   true macro regime?" (economics, unfalsifiable at 13-27 episodes) but "what
   real-time OBSERVABLE most separates the periods where a non-B allocation
   beats B?" (measurable, walk-forward-testable). Same overfitting discipline
   as the threshold grid: derive on 1991-2016, confirm on 2016-2026.
3. **Swap the indicators, keep the engine.** The detector's math (level/speed/
   accel, hysteresis, confidence) and the regime types can stand; only the
   INPUT series change — to market-observable, forward-pricing signals with
   long history: yield-curve slope, credit spreads, equity breadth,
   copper/gold, VIX term structure. These are contemporaneous AND have 35y of
   history, so they preserve the walk-forward validation that is the project's
   epistemic backbone.

**Explicitly OUT for validation: real-time alt-data (Truflation & similar).**
Truflation is genuinely contemporaneous, but it starts ~2021 — no 35y
backfill, so it CANNOT be walk-forward-validated, which breaks the discipline
every other signal in the system is held to. It could only ever be a
live-forward V2 signal, never historical go-live evidence. Market proxies
(above) are the viable class precisely because they have the history.

**Trigger to revisit:** gate it behind the stress-hedge test. FIRST measure
"hold B, switch to barbell only under confirmed stress" (cheap, existing data,
the one validated cell) — it is both a candidate V1 product AND the diagnostic
for whether this systemic path has headroom. If the stress-hedge captures most
of the extractable risk-reduction, rebuilding the regime axis to rediscover
"de-risk under stress" is gold-plating for V1. Only if measurable value
remains BEYOND stress does the indicator-swap pay. Revisit at the V2 boundary
regardless.

---

## I-39 — Equity weight vs the integrated high-inflation invariant — ✅ MEASURED, books unchanged

**Status:** opened and resolved 2026-07-20. The composition question is
CLOSED by measurement (see "RE-MEASURED" below); what remains open is a cheap
book-RENAMING, carried at the end of this item. Kept in full rather than
struck, because the negative result and the orthogonality finding are the
reasons not to re-open it.

**The finding (Faber, *Global Asset Allocation* 2015, read 2026-07-20).** Our
`inflation` book is SPY 50 / GLD 40 / IWN 10 — i.e. 60% equities, and gold as
the sole real asset. Two independent things now argue that composition is
wrong for the state it is named after:

1. **The book's Figure 48** (Credit Suisse, 1900-2014): real stock AND bond
   returns are highest below ~3% inflation and "fall off a cliff" above 5%.
   Equities are not the inflation pass-through they are assumed to be.
2. **The book's ch. 11 decade table**: through the inflationary 1973-1981, the
   two equity-heavy allocations with NO real assets were the two worst (60/40
   -4.05%/yr real, Buffett -3.48%); the only positive ones were the real-asset-
   heavy Marc Faber (+2.25%) and Permanent (+0.92%). The allocations that won
   the 1970s then lost the disinflation that followed — the effect is
   regime-conditional, which is precisely what a per-regime book is for.

Claim (1) is now seeded as `inv-high-inflation-equities`, so **the seed corpus
contains an invariant that contradicts a live book's composition.** That is the
system working as designed (belief does not grant integration — the engine
confronts it over 35y), but the contradiction should be resolved explicitly
rather than left to drift.

**⚠️ THE ENGINE ALREADY RULED — this item is NO LONGER merely deferred
(2026-07-20).** Landed in the live DB and matured over the full 35y, the
invariant came back **`integrated`**: score 0.636 on 28 confirmations / 16
infirmations (N=44), clearing θ=0.60, N_min=3 and the binomial-tail test.
`check_contradictions` over the integrated set reports none. So this is not a
book-quote awaiting evidence — **on our own 1991-2026 data, equities measurably
underperform the median asset class while CPI YoY is above 3%, and the
`inflation` book holds 50% SPY into exactly that state.**

Two things sharpen it. First, the same sweep leaves `inv-rising-growth-equities`
**`rejected`** (score 0.506) — the seeded Dalio-tier belief that growth favours
equities does not survive confrontation, while the Faber-sourced one does; the
overlap flagged in the trace resolved against the incumbent. Second, the
companion `inv-low-real-rate-nominal-bonds` came back **`proposed`** (0.542,
26c/22i) — insufficient evidence, undecided. Only the equity claim is
established; do not treat the pair as jointly confirmed.

Related gap, same book: the stack holds **no commodities, no REITs, no TIPS**,
and is **100% US** — no INTL/EM equity — while `BENCHMARK_CLASSES` already
carries `inflation-protected`, `gold-commodities`, `INTL_EQUITY` and
`EM_EQUITY`, and DJP/TIP are already priced in the DB. The candidate assets
exist; only the measurement does not.

**Scope if built:** re-measure the stack with an `inflation` book tilted toward
real assets (candidates: DJP commodities, TIP, a REIT sleeve) at the expense of
the SPY sleeve, on the SAME harness (`market_signal.run_market_signal` +
`nav_metrics`) and the SAME 1991-2026 window, then check `cap_violations`. Note
the honest limit up front: **our window starts 1991 and contains no 1970s-style
inflation** (`inflation > 5` on 5.7% of prints), so the backtest CANNOT confirm
the effect Faber measured — it can only show the cost of carrying the hedge in
a low-inflation era. That asymmetry is the reason to treat this as a
deliberate insurance decision, not a return optimisation.

**RE-MEASURED 2026-07-20 — the books stay as they are. Resolution below.**

**First correction: the premise above was mis-aimed.** The `inflation` BOOK is
selected by the MARKET signal (credit spread + slope); the invariant fires on
CPI YoY. Measured on the 418 monthly decisions, those two states are nearly
ORTHOGONAL — each book spends 28-33% of its time with CPI>3 against a 31.3%
base rate (growth 30.6%, inflation 33.0%, slowdown 28.3%). **The `inflation`
book is not an inflation state: mean CPI 2.99 vs 2.23 for `growth`.** And when
CPI>3, the stack is in `growth` 42.0% of the time — the book carrying 90%
equities — versus `inflation` 46.6%. Mean equity weight carried while CPI>3 is
**66.9%**, so fixing the `inflation` book alone would have addressed under half
the indicted exposure. The coherence problem is real but it belongs to the
STACK, not to one book.

**Second: every remedy measured either costs return or breaches a cap.** Same
harness/window/costs, control reproduces ADR-007 exactly (9.85% / -23.77%):

| variant | CAGR full | CAGR holdout | MaxDD full | caps |
|---|---|---|---|---|
| baseline (control) | 9.85% | 7.77% | -23.8% | clean |
| `inflation` book SPY→DJP | 8.52% | 8.23% | -24.2% | clean |
| CPI>3 overlay, SPY→DJP | 9.26% | 8.68% | **-28.1%** | 1 breach |
| CPI>3 overlay, SPY→GLD | 10.49% | 8.52% | **-27.0%** | 15 breaches |

- The book-level swap (the idea this item opened with) **costs 1.33 pt/yr** on
  the full window — as the orthogonality above predicts, it is aimed at the
  wrong 46%.
- Both CPI overlays breach the `-25%` drawdown cap. The SPY→GLD variant also
  breaches the single-asset cap 15 times (GLD reaches 90% in the `inflation`
  book, and GLD is NOT the exempt trend-haven). Its +0.64 pt is in any case
  suspect: "hold more gold since 1991" leans on gold's 2000s run, a
  single-episode effect this window cannot separate from the signal.

**Resolution — keep the books, record the reason (the second option this item
offered).** Not because the verdict is ignored: `inv-high-inflation-equities`
stays `integrated` and its measurement stands. But the hedge it implies cannot
be bought within the owner's own binding caps, and our 1991-2026 window has no
1970s-style inflation (`inflation > 5` on 5.7% of prints), so the window shows
the hedge's COST while being structurally unable to show its PAYOFF. Paying a
certain 0.6-1.3 pt/yr for an insurance whose benefit is unobservable here is
not a decision a backtest can justify. **No further variants should be
searched** — the holdout has been consulted repeatedly and this is exactly the
knob-hunt STRATEGY_COMPARISON's stop-optimizing rule forbids.

**What SHOULD change (cheap, unmeasured, not a composition change):** the book
named `inflation` does not track inflation, and the Worker (an LLM reading book
names as semantic context) will be misled by it. Renaming the three books after
what actually selects them — the credit/slope state, e.g. `tight-credit-flat`
— removes a real reasoning hazard at zero backtest risk. Needs an ADR-007
addendum since the names are seeded entities.

**Trigger to revisit the COMPOSITION:** realized CPI YoY sustained above 5%
(the regime this window lacks and the book's actual cliff), or forward
paper-mode showing the stack's equity sleeve bleeding through an inflation
episode. Not before — there is no new information to be had until then.

---

## I-40 — Quarterly cadence: better on average, but phase-fragile and it does NOT solve the tax constraint

**Why deferred:** the measurement is done (below) and it does not support a
cadence change on its own; what remains is an owner call tied to OPEN #2
(docs/V1_STRATEGY.md), not an implementation.

**Why it was measured.** Faber's rebalancing evidence (ch. 12: monthly vs never
differs by <0.50%/yr; "yearly or even every few years is just fine") removes
the a-priori that slower is worse, and OPEN #2 wants longer holdings for the
Swiss Circular-36 six-month private-investor safe harbour. `quarterly` was
therefore added to `replay.decision_dates` and the stack re-run.

**Measured on the live DB** (1991-2026 full / 2016-2026 holdout, 20 bps,
`market_signal.run_market_signal`):

| cadence | CAGR full | CAGR holdout | MaxDD full | median hold |
|---|---|---|---|---|
| weekly | 9.83% | 8.85% | -23.6% | 14 d |
| monthly (adopted) | 9.85% | 7.77% | -23.8% | 61 d |
| quarterly (Mar phase) | 10.19% | 8.81% | -23.8% | 179 d |

Quarterly looks like a free win at first read. **It is not — the phase test
kills that reading.** A quarterly clock samples only 4 dates/yr, so which
months it lands on matters. Re-running the three distinct phases:

| phase | CAGR full | CAGR holdout | MaxDD full |
|---|---|---|---|
| quarterly@JAN | 12.09% | 10.91% | **-25.4%** |
| quarterly@FEB | 10.86% | 10.51% | **-27.6%** |
| quarterly@MAR | 10.19% | 8.81% | -23.8% |

**The spread across phases is 1.90 pt CAGR (full) / 2.10 pt (holdout) — wider
than the entire +2.5 edge over B that justified the pivot.** So:

- **Direction is consistent**: all 3 phases beat monthly on BOTH windows (6/6),
  mean +1.2 pt full / +2.3 pt holdout. Slowing the clock does not cost return
  — Faber's finding survives contact with a signal-driven stack, which was not
  obvious a priori (his evidence covers static rebalancing only).
- **Magnitude is not knowable**: picking the 12.09% phase because it backtests
  best is phase-mining, and STRATEGY_COMPARISON's stop-optimizing rule applies
  with full force — the holdout has been consulted many times.
- **Drawdown widens**: 2 of 3 phases breach the ADR-007 `-25%` cap (-25.4%,
  -27.6%) that monthly clears at -23.8%. A cadence change here is a cap
  question, not just a return question.
- **It does NOT deliver the tax rationale it was tested for.** Median holding
  is ~94 days in 5 of the 6 phase/window cells — well short of the 182-day
  safe harbour. Only quarterly@MAR on the full window reaches 179 days, and
  even that falls to 94 on the holdout. **Quarterly does not resolve OPEN #2.**
  If the six-month threshold must genuinely be cleared, the honest candidates
  are semi-annual/annual cadence or a regime-only clock (no trend overlay
  re-evaluation) — neither measured here — or a tax wrapper.

**Conclusion for now:** keep MONTHLY as adopted. Quarterly is not rejected on
return, but it buys a wider outcome distribution and a cap breach in exchange
for a tax benefit it does not actually deliver.

**Trigger to revisit:** if the Swiss fiduciaire (OPEN #2) confirms the
quasi-professional risk is material, re-open with semi-annual and regime-only
clocks measured too, and choose the phase by a rule fixed IN ADVANCE (e.g.
first decision month after go-live), never by backtest rank.

---

## I-41 — The stack is 100% US: no international or EM equity in any book

**Why deferred:** same reason as I-39 — it changes the composition ADR-007 was
signed on. Split out from I-39 deliberately: that item is about what the
`inflation` book holds, this one is a home-bias question affecting **all three
books**, with its own evidence and its own data horizon.

**The finding (Faber, *Global Asset Allocation* 2015, read 2026-07-20).** All
five tickers the stack can hold (SPY, IWN, GLD, VCIT, IEF) are US. The book's
central recommendation is the opposite: "at a minimum, an investor should
consider moving to a global 60/40 portfolio to reflect the global market
capitalization". Its ch. 6 Global Market Portfolio and ch. 11 comparison both
rest on global exposure, and ch. 13's summary repeats it.

**The honest counterweight — do not treat this as an obvious fix.** The book's
own Figure 41 is the reason: across 8 famous allocations 1973-2013 the spread
in real return is 1.84 pt, and excluding the Permanent Portfolio **all of them
land within ONE point**. Faber's own conclusion from that table is that the
allocation choice barely matters and fees dominate — which cuts against
expecting a measurable gain from adding EFA, and cuts against churning the
books to chase one. Our stack's edge comes from the regime signal and the trend
overlay, not from the breadth of the menu. Adding international is a
diversification/robustness argument (the US outperformance of 1991-2026 is not
guaranteed to repeat), NOT a return argument.

**Data available (checked on the live DB, 2026-07-20):**
- `EFA` (INTL_EQUITY) 1991-12-30 → 2026-07-17, ~8.7k rows — covers essentially
  the whole ADR-007 window, so it is testable at full length.
- `EEM` (EM_EQUITY) only from 2003-04 — adding it TRUNCATES any backtest to
  2003+, losing the dot-com bust and half the window that earned the pivot.
  EFA-only is the change that can actually be measured against 9.85%/-24%.

**Scope if built:** measure an EFA sleeve inside the `growth` book (the
risk-on book, where an international equity tilt belongs) against the current
US-only stack on the same harness and window; report whether the -24% drawdown
and the +2.5-vs-B edge survive. Note the trend overlay currently redirects only
SPY and GLD (`TREND_SLEEVES`): an EFA sleeve would need its own 200d rule or it
silently escapes the drawdown control that is the stack's whole downside
defence — that, not the allocation, is the real implementation risk here.

**Trigger to revisit:** at the same time as I-39 (both are book-composition
changes; measuring them separately doubles the work and neither is urgent), or
if forward paper-mode shows the stack's edge concentrated in a US-specific
episode.

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
