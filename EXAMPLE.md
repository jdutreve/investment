# EXAMPLE.md — Full cycle: Stagflation May 2026

Full trace of a real V1 cycle: events → regime → portfolio ranking
→ Worker cycle → innovation → proposals to user.
Each entity is instantiated with its actual properties.
**Every entity below exists in the UC0 seed** (investment-TASKS.md Phase 1ter)
— this example is V1-conformant end to end.

**Two complete scenarios are shown:**
- **Scenario A (Steps 1–8A):** defender ranks first → no switch, but the
  Worker emits a **reallocation Proposal** for the defender (paper-mode).
- **Scenario B (Step 8B):** challenger beats defender → **switch Proposal**,
  paper-test.

**Vocabulary convention:** financial / economic terms throughout. Performance
numbers are *indicators* (Sharpe, Sortino, Calmar, max_drawdown, total_return,
volatility), never "ratios" as a generic field name.

**Reference asset universe** (Phase 1 accumulation, USD-quoted): equities
`SPY VTI QQQ EFA EEM`; rates `TLT IEF SHY BIL`; inflation-protected `TIP`;
gold & commodities `GLD DJP DBC`; cash `cash` (accrues at ^IRX). Currency
overlay quotes back to CHF for display only.

---

## Step 1 — Tier 1 events of 11 May 2026

Numeric indicators (CPI, growth composite, VIX, prices) flow into the
**MarketData TS** (Step 2). There is no Signal vertex in the V1 schema: purely
qualitative events with no direct TS analogue (e.g. *"central-bank communiqué
shift"*) are captured in V1 as entries in `Regime.events` /
`Evaluation.events`; a dedicated Signal vertex is deferred to V2
(IMPROVEMENTS I-19 — named Signal, not Event: `EventLog` is the audit log).

For 11 May 2026, all incoming Tier 1 information is numeric and is captured
directly in the MarketData TS rows below.

---

## Step 2 — Regime detection

Regime detection is **mechanical** (Monday 08:00 catch-up — the detector
runs day-by-day over the fetched days, formal algorithm in
investment-ARCHITECTURE.md; start_dates come from the data, not the run
date). It reads `level`, `speed`, and `acceleration` from the MarketData TS.

### 2a — MarketData TS rows (Monday catch-up fetch)

```
MarketData { ticker:"CPIAUCSL", asset_class:"MACRO", currency:"USD"
  ts:2026-05-11
  level:3.1          ← CPI YoY % (transform yoy_pct); rising-inflation
                       threshold = level > 2.5 with speed > 0
  speed:+0.30        ← Δ1m of YoY CPI; positive → accelerating inflation
  acceleration:+0.15 ← speed itself accelerating → early regime-shift warning
}

MarketData { ticker:"GROWTH_COMPOSITE", asset_class:"MACRO", currency:"USD"
  ts:2026-05-11
  level:97.2         ← < 100 = growth below trailing baseline
  speed:-0.90        ← contraction deepening (|speed| > regime_growth_noise 0.15)
  acceleration:-0.25 ← contraction worsening
}

MarketData { ticker:"GLOBAL_LIQUIDITY", asset_class:"GLOBAL_LIQUIDITY"
  currency:"USD"
  ts:2026-05-11
  level:98.4         ← < 100 = tighter than trailing baseline
  speed:-0.80        ← negative → tightening
  acceleration:-0.40 ← tightening accelerating
}
```

`global_liquidity: "tightening"` in Proposal.market_context is derived from
this row: `level < 100 AND speed < 0`.

### 2b — RegimeType (seeded once at UC0) and Regime instance (catch-up detector)

`RegimeType` is seeded at UC0 and never mutated (TRACE_EXEMPT — narrative in
description). `Regime` is a concrete occurrence created/updated by
`detect_regime()`. FAVORS and DESIGNED_FOR point to the type; IN_REGIME
points to the instance.

```
RegimeType {
  id: "falling-growth-rising-inflation"
  name: "Stagflation"
  aliases: ["stagflation"]
  framework_id: "4seasons"
  description: "Growth composite falling with CPI YoY > 2.5 and accelerating.
                Maps to the 4 Seasons quadrant 'growth falling + inflation
                rising'. Seeded at UC0."
  created_at: 2026-01-01
}
```

Detection: CPI level 3.1 > 2.5 with speed +0.30 > noise → inflation rising;
GROWTH_COMPOSITE speed −0.90 < −0.15 → growth falling → candidate =
`falling-growth-rising-inflation`. Candidate produced by 2 consecutive
monthly prints of both axes — the March and April observations (hysteresis
`regime_confirm_prints` = 2; both axes are monthly series, so confirmation
counts prints, not days) → committed 2026-05-01.
Confidence = 50 + 20×min(1, 0.30/0.3) + 20×min(1, 0.90/1.0) + 10 (accel
aligned on both axes) = 50 + 20 + 18 + 10 = **78**.

EventLog append precedes the vertex commit:

```
EventLog { id:"01JX...", ts:2026-05-04T08:00, type:"RegimeEvent",
           source_uc:"catch-up", source_id:"stagflation-2026-05-01",
           -- committed Monday May 4; start_date 2026-05-01 (Friday) comes
           -- from the DATA, not the run date
           payload:'{"from":"rising-growth-rising-inflation",
                     "to":"falling-growth-rising-inflation","confidence":78}' }
```

```
Regime {
  id: "stagflation-2026-05-01"
  regime_type_id: "falling-growth-rising-inflation"
  tags: ["stagflation", "inflation-rising", "growth-falling",
         "liquidity-tightening"]
  start_date: 2026-05-01, end_date: null   ← from the data (Friday)
  is_current: true, confidence: 78
  events: [
    "CPI YoY 3.1 (speed +0.30, accel +0.15)",
    "GROWTH_COMPOSITE 97.2 (speed -0.90, accel -0.25)",
    "global liquidity tightening (level 98.4, speed -0.80, accel -0.40)"
  ]
  trace: "Acceleration on both axes confirms regime, not a transient blip.
          Committed after 2 consecutive confirming monthly prints."
  created_at: 2026-05-04                   ← commit date (Monday catch-up)
  updated_at: 2026-05-11    ← as_of
}
```

---

## Step 3 — Strategy and its Invariants (all from the seed + validated discoveries)

The defender `4s-balanced-defender` executes the seeded strategy
`four-seasons-rp` (HOLDS primary=true). Its thesis is backed by a mix of
corpus invariants (seeded at UC0) and **user-validated agent discoveries** —
the V1-legal path for `source=agent-discovery` (proposed → Telegram →
user YES → integrated).

```
Strategy#four-seasons-rp -[BACKED_BY strength:0.8 added_at:2026-01-15
  excerpt:"TIPS/gold/commodities outperform nominal bonds in inflation"]->
  Invariant#inv-inflation-persistence-tips          (dalio, seeded)

Strategy#four-seasons-rp -[BACKED_BY strength:0.7 added_at:2026-01-15
  excerpt:"diversification reduces max_drawdown"]->
  Invariant#inv-diversification-drawdown            (dalio, seeded)

Strategy#four-seasons-rp -[BACKED_BY strength:0.9 added_at:2026-03-02
  excerpt:"GLD +18% vs 4S in stagflation 2021-2022"]->
  Invariant#gold-stagflation-hedge                  (system, user-validated)
```

### Invariant weight mechanics

**Rule:** `weight_effective = max(weight_initial × market_score × recency_factor, floor_weight)`

`weight_initial` is the **ceiling** — weight can only decay below it (via
refutations or time) or be floored. Confirmations **preserve** weight against
decay; they do not push it above `weight_initial`. Authority gradient is
enforced at creation via initial weight and floor (`author:"dalio"` → floor
0.40, `author:"system"` → floor 0.05).
`recency_factor = 0.5 + 0.5 × exp(−days_since/365)` — decays from 1.0 toward
an asymptotic floor of 0.5.

---

**Invariant#gold-stagflation-hedge** — agent discovery, user-validated,
3/3 confirmations

```
Invariant {
  id: "gold-stagflation-hedge"
  title: "GLD outperforms in stagflation vs standard 4 Seasons weighting"
  description: "In stagflation, gold benefits from dual tailwinds: real yields
                fall (inflation up, growth down) and risk aversion rises.
                Standard 4 Seasons holds ~10% GLD; this invariant supports a
                15-25% tilt."
  example: "2021-2022: GLD +18% vs 4 Seasons GLD sleeve +11% while the growth
            composite stayed below 100 for 8 consecutive months."
  topic: ["gold","stagflation","inflation","allocation"]
  tags: ["asset:GLD", "asset-class:commodities",
         "regime:falling-growth-rising-inflation"]
  source: "Backtest stagflation 2021-2022 — GLD +18% vs 4S GLD sleeve +11%
           (computed 2026-03-01 from Yahoo daily closes)"
  author: "system"
  status: "integrated"            ← proposed 2026-03-01, user validated 2026-03-02
  floor_weight: 0.05              ← system (agent-discovery) floor
  weight_initial: 0.25            ← ceiling for this invariant
  confirmation_count: 3
  infirmation_count: 0
  market_score: 1.0               ← 3/(3+0)
  recency_factor: 0.992           ← 0.5+0.5×exp(-6/365); 6d since last confrontation
  weight_effective: 0.248         ← max(0.25 × 1.0 × 0.992, 0.05)
  embedding: [384 floats]         ← encode(title + "\n" + description)
  trace: "Discovered analyzing stagflation 2021-2022. Confirmed mechanically:
          Oct 2024, Mar 2026, May 2026 (FAVORS-vs-median rule)."
  created_at: 2026-03-01
  validated_at: 2026-03-02        ← user clicked [YES]
  updated_at: 2026-05-11
}
```

**Invariant#calmar-accumulation** — agent discovery, user-validated,
2 confirmations / 1 refutation

```
Invariant {
  id: "calmar-accumulation"
  title: "Calmar indicator > Sharpe as primary selection metric in accumulation"
  description: "Sharpe penalizes all volatility equally, including upside.
                Calmar (annualized return / |max_drawdown|) directly measures
                recovery potential. In Phase 1 accumulation, Calmar is the
                primary selection indicator."
  example: "2008: best-Sharpe strategies had max_drawdown -35% to -50%;
            best-Calmar strategies had -8% to -15%, recovering 3× faster."
  topic: ["calmar","sharpe","drawdown","accumulation","risk"]
  tags: ["indicator:calmar", "indicator:max_drawdown", "phase:accumulation"]
  source: "Backtests 2008 / 2020 / 2022 cross-validation (computed 2026-03-01)"
  author: "system"
  status: "integrated"
  floor_weight: 0.05
  weight_initial: 0.20
  confirmation_count: 2
  infirmation_count: 1
  market_score: 0.667             ← 2/(2+1)
  recency_factor: 0.920           ← 0.5+0.5×exp(-63/365); 63d since last confrontation
  weight_effective: 0.123         ← max(0.20 × 0.667 × 0.920, 0.05)
  embedding: [384 floats]
  trace: "One refutation: 2023 bull run where high-Sharpe strategies
          outperformed. This invariant motivates the calmar<1.0 ranking
          demotion and the 1.5 proposal gate."
  created_at: 2026-03-01
  validated_at: 2026-03-02
  updated_at: 2026-03-15
}
```

**Invariant#inv-inflation-persistence-tips** — Dalio corpus (UC0 seed),
8 confirmations / 1 refutation

```
Invariant {
  id: "inv-inflation-persistence-tips"
  title: "Persistent inflation favors TIPS, commodities, and gold"
  description: "When CPI YoY > 2.5% and speed > 0, real yields fall and
                TIPS/gold/commodities outperform nominal bonds."
  example: "2021-2022: TIP +2.3% while TLT -26%."
  topic: ["tips","inflation","bonds","real-yield"]
  tags: ["asset:TIP", "asset:GLD", "indicator:real-yield",
         "regime:falling-growth-rising-inflation",
         "regime:rising-growth-rising-inflation"]
  source: "Dalio — Principles for Navigating Big Debt Crises (2018), p.142
           (document#dalio-big-debt-crises-2018, passage#pass-dalio-tips-142)"
  author: "dalio"
  status: "integrated"
  floor_weight: 0.40              ← dalio floor — never falls below this
  weight_initial: 0.85
  confirmation_count: 8
  infirmation_count: 1
  market_score: 0.889             ← 8/(8+1)
  recency_factor: 0.992
  weight_effective: 0.750         ← max(0.85 × 0.889 × 0.992, 0.40)
  embedding: [384 floats]
  trace: "Dalio All Weather principle. Only refutation: 2020 deflation
          (extreme case out of scope)."
  created_at: 2026-01-15
  validated_at: 2026-01-15        ← integrated at corpus ingestion time
  updated_at: 2026-05-11
}
```

---

## Step 4 — FAVORS edges and Portfolio ranking

### 4a — FAVORS edges (RegimeType → Strategy)

Updated after each weekly backtest cycle. Edge indicators come from
**synthetic backtests of each strategy's prescribed allocation** (= its base
scenario `target_allocation`) replayed over every historical Regime instance
of this type — instances materialized at UC0 from the 25y backfill.
They are *strategy-level* numbers and must not be confused with
portfolio-level rolling indicators (Step 4b). `n_periods=4` = 4 distinct
stagflation episodes in the 25y history.

```
RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.58, sortino_rolling:0.91,
    calmar_rolling:1.3, max_drawdown:-0.088,
    n_periods:4, last_updated:2026-05-11]-> Strategy#four-seasons-rp

RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.45, sortino_rolling:0.69,
    calmar_rolling:1.0, max_drawdown:-0.101,
    n_periods:4, last_updated:2026-05-11]-> Strategy#permanent-browne

RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.41, sortino_rolling:0.60,
    calmar_rolling:1.1, max_drawdown:-0.054,
    n_periods:4, last_updated:2026-05-11]-> Strategy#barbell-taleb

RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.20, sortino_rolling:0.28,
    calmar_rolling:0.5, max_drawdown:-0.194,
    n_periods:4, last_updated:2026-05-11]-> Strategy#momentum-macro
```

Mechanical invariant confrontation (weekly 08:40, ARCHITECTURE rule): median
FAVORS sortino for this regime = 0.645. `four-seasons-rp` (0.91 ≥ median) →
confirmation for its BACKED_BY invariants tagged
`regime:falling-growth-rising-inflation` (gold-stagflation-hedge,
inv-inflation-persistence-tips) and untagged ones. `momentum-macro`
(0.28 < 0.645 − 0.10) → infirmation for inv-rising-growth-equities? No —
that invariant carries only rising-growth regime tags, so it is NOT
confronted by a stagflation cell. Tags gate the confrontation.

### 4b — Portfolio ranking (portfolio_weekly_snapshot, Monday 08:50)

All 7 enabled portfolios ranked by USD rolling indicators (36M / 756 trading
days; cumulative returns on calendar windows). Units: decimal fractions;
percent formatting only in Telegram. 4 rows shown, 3 omitted for brevity.

**Rank rule:** `sortino_rolling` DESC; tie-break (within 0.02)
`calmar_rolling` DESC; final tie-break `max_drawdown`. `calmar_rolling < 1.0`
demoted to the bottom. `max_drawdown` breaching the **user** rule (-15%)
keeps the row but excludes defender role + proposal candidacy.

```
portfolio_weekly_snapshot {
  date: 2026-05-12, defender: true, framework_id: "4seasons"
  portfolio_id: "4s-balanced-defender"
  designed_regime_type_id: null              ← framework-neutral, no DESIGNED_FOR
  primary_strategy_id: "four-seasons-rp"
  allocation: {TIP:20, TLT:30, GLD:10, DJP:7.5, SPY:30, cash:2.5}
  rank: 1
  sharpe_rolling: 0.71, sortino_rolling: 1.18, calmar_rolling: 1.9
  max_drawdown: -0.041, volatility: 0.08
  return_3m: 0.038, return_6m: 0.072, return_1y: 0.143,
  return_3y: 0.321, return_5y: 0.486
  gap_to_defender: null
  recommendation: "maintain"                 ← upgraded by Writeback in Step 8A
  market_context: {framework:"4seasons",
                   regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Defender holds rank 1 — 36M window still dominated by pre-shift data"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, defender: false
  portfolio_id: "4s-stagflation-defensive"
  designed_regime_type_id: "falling-growth-rising-inflation"
  primary_strategy_id: "four-seasons-rp"
  allocation: {TIP:30, GLD:25, DJP:15, SPY:10, TLT:10, cash:10}
  rank: 2
  sharpe_rolling: 0.66, sortino_rolling: 1.10, calmar_rolling: 1.7
  max_drawdown: -0.052, volatility: 0.07
  return_3m: 0.042, return_6m: 0.066, return_1y: 0.121,
  return_3y: 0.264, return_5y: 0.401
  gap_to_defender: {sharpe_delta:-0.05, sortino_delta:-0.08, calmar_delta:-0.2,
                    max_drawdown_delta:-0.011}
  recommendation: "monitor"
  trace: "Stagflation-designed challenger closing in — best 3m return"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, defender: false
  portfolio_id: "permanent-balanced"
  designed_regime_type_id: null
  primary_strategy_id: "permanent-browne"
  allocation: {SPY:25, TLT:25, GLD:25, cash:25}
  rank: 3
  sharpe_rolling: 0.48, sortino_rolling: 0.72, calmar_rolling: 1.1
  max_drawdown: -0.094, volatility: 0.09
  gap_to_defender: {sharpe_delta:-0.23, sortino_delta:-0.46, calmar_delta:-0.8,
                    max_drawdown_delta:-0.053}
  recommendation: "monitor"
  trace: "Defensive but Calmar below the 1.5 proposal threshold"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, defender: false
  portfolio_id: "momentum-macro-rotation"
  designed_regime_type_id: null
  primary_strategy_id: "momentum-macro"
  allocation: {SPY:40, TLT:30, GLD:15, DJP:10, cash:5}
  rank: 7                                     ← calmar 0.6 < 1.0 → demoted to bottom
  sharpe_rolling: 0.22, sortino_rolling: 0.31, calmar_rolling: 0.6
  max_drawdown: -0.182, volatility: 0.16
  gap_to_defender: {sharpe_delta:-0.49, sortino_delta:-0.87, calmar_delta:-1.3,
                    max_drawdown_delta:-0.141}
  recommendation: "monitor"
  trace: "Calmar-demoted; drawdown -18.2% breaches the -15% user rule →
          excluded from defender role and proposal candidacy"
}
```

Edges for the defender portfolio:

```
Portfolio#4s-balanced-defender -[HOLDS primary:true weight:1.0 since:2026-01-15]->
  Strategy#four-seasons-rp
-- No DESIGNED_FOR edge: the balanced defender is framework-neutral.
```

---

## Step 5 — Scenarios and Evaluation

Weekly 08:35 job: numeric triggers only (`^VIX > 25` hit on 2026-05-08) +
`shift_d7` recorded in ScenarioProbability TS. Probability VALUES are changed
by the Worker (weekly), which also interprets qualitative triggers.

```
Scenario#sc-4s-bear {
  id: "sc-4s-bear", name: "bear"
  probability: 55               ← Worker adjustment this cycle; was 20
  probability_d7: 20
  triggers: ["^VIX > 25", "CPI_YOY > 4 AND GROWTH_COMPOSITE < 98"]
  target_allocation: {TIP:30, GLD:25, DJP:15, SPY:10, TLT:10, cash:10}
  currency: "USD"
  trace: "Massive shift +35pts — VIX trigger hit mechanically; CPI
          acceleration interpreted by Worker"
  updated_at: 2026-05-11
}
Scenario#sc-4s-base { probability: 35, probability_d7: 55, ... }
Scenario#sc-4s-bull { probability: 10, probability_d7: 25, ... }

Strategy#four-seasons-rp -[HAS_SCENARIO active:true]-> each of the 3
```

```
Evaluation {
  id: "eval-20260511"
  date: 2026-05-11
  verdict: "confirms"
  conviction_delta: +5
  events: [
    "CPI YoY 3.1 (speed +0.30, accel +0.15)",
    "GROWTH_COMPOSITE 97.2 (speed -0.90)",
    "^VIX above stress threshold 25"
  ]
  reasoning: "Inflation re-accelerating + growth contracting + VIX stress =
              the regime four-seasons-rp's TIP/GLD sleeves are built for.
              Conviction 65 → 70."
  trace: "3 convergent Tier 1 indicators — strong confirmation"
  created_at: 2026-05-11
}

Evaluation#eval-20260511 -[UPDATES conviction_delta:+5 date:2026-05-11]->
  Strategy#four-seasons-rp
```

Evaluation verdict 'confirms' → mechanical confirmation for the BACKED_BY
invariants of four-seasons-rp (invariant_confrontations, source='evaluation').

ScenarioProbability TS records the shift for UC8 to read:

```
ScenarioProbability { strategy_id:"four-seasons-rp", scenario:"bear",
                      probability:55, shift_d7:+35.0 }
```

---

## Step 6 — Worker cycle: reallocation proposal + innovation

Weekly cycle (Monday 09:00). Planner Pre builds PlannerContext; Worker
receives it + the 3 bridged tools — unaware of Planner, Writeback, storage.

### 6a — WorkerResult (schema in ARCHITECTURE)

```python
WorkerResult {
  regime_assessment: "Stagflation confirmed (78%). CPI accelerating, growth
                      composite contracting, global liquidity tightening.
                      Bear scenario 55% (+35pts shift)."

  ranking_commentary: "Defender holds rank 1 on the 36M window, but the
                       stagflation-designed challenger posts the best 3m
                       return. momentum-macro demoted (Calmar 0.6) and
                       drawdown-excluded (-18.2% < -15% user rule)."

  scenario_adjustments: [
    {strategy_id:"four-seasons-rp", scenario:"bear", probability:55,
     rationale:"VIX numeric trigger hit + CPI acceleration; base 35, bull 10"},
    ...
  ]

  evaluations: [ EvaluationDraft → eval-20260511 above ]

  # defender rank 1 → no switch this week (commentary folded into reasoning)

  reallocation_proposed: ReallocationProposal {
    proposed_allocation: {TIP:25, TLT:22.5, GLD:15, DJP:10, SPY:22.5, cash:5}
    scenario_delta:  {TIP:+10, TLT:-20, GLD:+15, DJP:+7.5, SPY:-20, cash:+7.5}
                     # bear target − current defender allocation
    favors_delta:    {}        # top-FAVORS strategy for stagflation is
                               # four-seasons-rp; its prescribed (base) target
                               # IS the defender's current allocation → 0
    blend_note: "delta = 0.4 × scenario_delta + 0.6 × 0, rounded to 2.5pts,
                 renormalized to 100"
    supporting_invariants: ["gold-stagflation-hedge",
                            "inv-inflation-persistence-tips"]
    reasoning: "Structural anchor unchanged (defender already tracks the
                top-FAVORS base allocation). Tactical bear shift (+35pts,
                55%) justifies a 0.4-weighted tilt: +5 GLD, +5 TIP, +2.5 DJP,
                +2.5 cash funded from -7.5 TLT, -7.5 SPY. Gold tilt backed by
                gold-stagflation-hedge (0.248, 3/3 confirmed); TIP tilt by
                Dalio inv-inflation-persistence-tips (0.750, 8/9)."
  }

  innovations_proposed: [
    ImprovementProposal {
      type: "new_invariant"
      title: "Calmar > 1.5 as strategy selection criterion — optimal threshold"
      rationale: "Backtests 2008/2020/2022: Calmar > 1.5 → max_drawdown < 10%
                  vs < 20% for Calmar < 1."
      spec: {"indicator": "calmar_rolling", "threshold": 1.5, "operator": "gt"}
      source: "Backtests 2008 / 2020 / 2022 (computed 2026-05-12)"
      author: "system", status: "proposed"
      weight_initial: 0.15, floor_weight: 0.05
      trace: "Discovered during portfolio ranking analysis."
    }
  ]

  reasoning: "Defender solid (Calmar 1.9) but allocation lags the bear
              scenario. Paper-mode reallocation proposed rather than a switch."
}
```

### 6b — Planner Post processes the innovation

EventLog append precedes vertex creation (architectural invariant):

```
EventLog { id:"01JY...", ts:2026-05-12T09:00:11, type:"InnovationEvent",
           source_uc:"UC8", source_id:"wresult-20260512",
           payload:'{"invariant_id":"calmar-v2-threshold","status":"proposed"}' }
```

```
Invariant {
  id: "calmar-v2-threshold"
  title: "Calmar > 1.5 as strategy selection criterion — optimal threshold"
  status: "proposed"             ← never integrated without user validation
  author: "system"
  floor_weight: 0.05, weight_initial: 0.15
  weight_effective: 0.15         ← max(0.15 × 1.0 × 1.0, 0.05); no confrontation yet
  confirmation_count: 0, infirmation_count: 0
  market_score: 1.0              ← default until first confrontation
  recency_factor: 1.0            ← created today
  source: "Backtests 2008 / 2020 / 2022 (computed 2026-05-12)"
  trace: "Pending user validation."
  created_at: 2026-05-12, validated_at: null, updated_at: 2026-05-12
}
```

**Telegram to user:**
```
💡 Agent innovation:
   Calmar indicator optimal threshold = 1.5
   Analyzed across 3 crises (2008/2020/2022)
   Current weight: 0.15 (not validated — author: system)
   → Integrate as selection criterion? [YES] [NO]
```

---

## Step 7 — Backtest in a historical regime instance

`Regime#stagflation-2021-03-01` was materialized at UC0 by running the
detector over the 25y backfill (USE_CASES.md UC0 step 10) — it is a normal
seeded instance, not an exception.

```
Regime {
  id: "stagflation-2021-03-01"
  regime_type_id: "falling-growth-rising-inflation"
  tags: ["stagflation", "historical", "post-covid-inflation"]
  start_date: 2021-03-01, end_date: 2022-12-31
  is_current: false, confidence: 91
  events: ["CPI YoY peaked 9.1% Jun 2022",
           "GROWTH_COMPOSITE below 100 from Aug 2022",
           "global liquidity tightening Q4 2021 — Q4 2022"]
  trace: "Materialized at UC0 from the 25y backfill."
  created_at: 2026-01-01, updated_at: 2026-01-01
}
```

```
Backtest {
  id: "bt-four-seasons-rp-2021-2022"
  period: "2021-2022"
  date_start: 2021-01-01, date_end: 2022-12-31
  sharpe_rolling: 0.62            ← computed over the 2y period (not 36M)
  sortino_rolling: 0.98
  calmar_rolling: 1.4
  max_drawdown: -0.081
  total_return: 0.104
  currency: "USD"
  source: "mechanical", status: "integrated"   ← auto for mechanical
  trace: "Prescribed (base-scenario) allocation replayed with monthly
          rebalancing per pinned NAV conventions."
  created_at: 2026-01-01
}

Strategy#four-seasons-rp -[TESTED_IN is_primary:true]->
  Backtest#bt-four-seasons-rp-2021-2022

Backtest#bt-four-seasons-rp-2021-2022 -[IN_REGIME overlap_pct:91]->
  Regime#stagflation-2021-03-01
```

Three distinct number contexts — never to be confused:
- **Backtest vertex**: single historical episode (fixed date range)
- **FAVORS edge**: aggregated across n_periods historical instances (strategy-level)
- **portfolio_weekly_snapshot**: current 36M rolling window (portfolio-level)

---

## Step 8A — Scenario A: defender rank 1 → reallocation Proposal

No switch gate fires (defender is rank 1). Writeback validates the Worker's
reallocation against the mechanical gates (USE_CASES.md UC8-B):

```
sum(proposed) = 25+22.5+15+10+22.5+5 = 100 ✓
tickers ∈ allowed_tickers (non-macro) ∪ {cash} ✓
max asset = TIP 25 ≤ binding cap min(40, 40) ✓
max per-asset |delta| = 7.5 ≥ 5.0 ✓
turnover = (5+7.5+5+2.5+7.5+2.5)/2 = 15 ≤ 30 ✓
```

EventLog append precedes the Proposal vertex:

```
EventLog { id:"01JZ...", ts:2026-05-12T09:00:12, type:"ProposalEvent",
           source_uc:"UC8", source_id:"prop-20260512",
           payload:'{"proposal_type":"reallocation","recommendation":"paper-test"}' }
```

```
Proposal {
  id: "prop-20260512"
  date: 2026-05-12
  proposal_type: "reallocation"
  defender_id: "4s-balanced-defender"
  challenger_id: null
  proposed_allocation: {TIP:25, TLT:22.5, GLD:15, DJP:10, SPY:22.5, cash:5}
  recommendation: "paper-test"
  defender_rank: 1, challenger_rank: null
  gap: {allocation_diff: {TIP:+5, TLT:-7.5, GLD:+5, DJP:+2.5, SPY:-7.5, cash:+2.5}}
  market_context: {framework:"4seasons",
                   regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  reasoning: (ReallocationProposal.reasoning from Step 6a — blend + invariants)
  user_response: "pending"        ← auto-'expired' after 14 days
  paper_started: null
  trace: "First reallocation proposal of the stagflation regime"
  created_at: 2026-05-12
}
```

Snapshot `recommendation` upgraded 'maintain' → 'paper-test' on the defender
row by Writeback.

**Telegram weekly digest:**
```
📊 Regime: Stagflation (78% — falling-growth-rising-inflation)
   Global liquidity: tightening (level 98.4, speed -0.80)

🏆 Portfolio ranking (Sortino USD, rolling 36M):
   1. 4S Balanced            : 1.18 ★ (defender)  Calmar 1.9
   2. 4S Stagflation Defensive: 1.10              Calmar 1.7
   3. Permanent Balanced      : 0.72              Calmar 1.1
   ...
   7. Momentum Macro Rotation : 0.31              Calmar 0.6 ⚠️ (demoted;
      drawdown -18.2% breaches the -15% rule)

🔑 Key Invariants (effective weight):
   • TIPS inflation persistence : 0.750 (8/9 confirmed)     [Dalio]
   • GLD stagflation hedge      : 0.248 (3/3, near ceiling) [system]
   • Calmar > 1.5 filter        : 0.123 (2/3 confirmed)     [system]

🔧 Reallocation proposal (paper-test) — defender stays, allocation tilts:
   TIP 20→25 | GLD 10→15 | DJP 7.5→10 | cash 2.5→5
   TLT 30→22.5 | SPY 30→22.5        (turnover 15%)
   Why: bear scenario 55% (+35pts); gold tilt backed by GLD-stagflation
   invariant (3/3), TIP tilt by Dalio inflation-persistence (8/9).
   → [ACCEPT PAPER-TEST] [REJECT]

📈 Defender (USD, 36M): Sharpe 0.71 | Sortino 1.18 | Calmar 1.9
   Returns: 3m +3.8% | 6m +7.2% | 1y +14.3% | 3y +32.1% | 5y +48.6%
```

---

## Step 8B — Scenario B: challenger beats defender → switch Proposal

*Two weeks later (2026-05-26): the stagflation regime persists and
`4s-stagflation-defensive` edges past the defender on the rolling window.*

```
portfolio_weekly_snapshot {
  date: 2026-05-26, defender: false
  portfolio_id: "4s-stagflation-defensive"
  rank: 1
  sharpe_rolling: 0.74, sortino_rolling: 1.22, calmar_rolling: 1.7
  max_drawdown: -0.052
  gap_to_defender: {sharpe_delta:+0.03, sortino_delta:+0.04, calmar_delta:-0.2,
                    max_drawdown_delta:-0.011}
  recommendation: "paper-test"
  trace: "Challenger overtakes — stagflation persistence rewards the tilt"
}
portfolio_weekly_snapshot {
  date: 2026-05-26, defender: true
  portfolio_id: "4s-balanced-defender"
  rank: 2
  sharpe_rolling: 0.71, sortino_rolling: 1.18, calmar_rolling: 1.9
  max_drawdown: -0.041
  gap_to_defender: null
  recommendation: "monitor"
  trace: "Defender drops to rank 2 — switch gate triggered"
}
```

Writeback switch gates: rank 1 < 2 ✓; sortino gap +0.04 ≥ 0.02 ✓ (outside the
0.02 tie-break window, so no re-ordering); challenger Calmar 1.7 ≥ 1.5 ✓
(the gate compares to the absolute threshold, not to the defender's 1.9);
drawdown -5.2% within -15% ✓; max asset TIP 30 ≤ 40 ✓; max per-asset diff
|GLD 25−10| = 15 ≥ 5 ✓.

```
EventLog { type:"ProposalEvent", source_uc:"UC8", source_id:"prop-20260526",
           payload:'{"proposal_type":"switch","recommendation":"paper-test"}' }

Proposal {
  id: "prop-20260526"
  date: 2026-05-26
  proposal_type: "switch"
  defender_id: "4s-balanced-defender"
  challenger_id: "4s-stagflation-defensive"
  proposed_allocation: null
  recommendation: "paper-test"
  defender_rank: 2, challenger_rank: 1
  gap: {sharpe_delta:+0.03, sortino_delta:+0.04, calmar_delta:-0.2,
        max_drawdown_delta:-0.011,
        allocation_diff:{TIP:+10, GLD:+15, DJP:+7.5, SPY:-20, TLT:-20, cash:+7.5}}
  market_context: {framework:"4seasons",
                   regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  reasoning: "Challenger outranks on Sortino (1.22 vs 1.18) and Sharpe.
              Calmar lower (1.7 vs 1.9) but above the 1.5 absolute gate —
              digest flags the weaker downside profile. Consistent with the
              rejected/accepted reallocation direction of prop-20260512."
  user_response: "pending"
  paper_started: null
  trace: "First switch proposal in 12 weeks"
  created_at: 2026-05-26
}
```

**Telegram to user:**
```
📊 Regime: Stagflation (78%)

🔄 Challenger alert — paper-test recommended:
   Rank 1: 4S Stagflation Defensive   Sortino 1.22  Calmar 1.7
   Rank 2: 4S Balanced (defender)     Sortino 1.18  Calmar 1.9 ★

   Gap: Sortino +0.04 | Sharpe +0.03 | Calmar -0.2 (challenger weaker on
   downside — flagged)

→ [ACCEPT PAPER-TEST] [REJECT]
```

---

## Step 9 — After user acceptance

User accepts → UserDecisionEvent → `user_response: "accepted"`,
`paper_started: 2026-05-26`. V1 does NOT auto-apply: the agent paper-tracks
the challenger; nothing changes until the user manually rebalances.

If the user then manually rebalances into the challenger allocation, the
**defender flag moves** (the clean V1 semantics of a switch — no allocation
surgery on vertices):

```
Portfolio#4s-balanced-defender      { defender: false, updated_at: 2026-06-02,
  trace: "User rebalanced into stagflation-defensive; defender flag moved." }
Portfolio#4s-stagflation-defensive  { defender: true,  updated_at: 2026-06-02,
  trace: "Now the live defender after user's manual rebalance." }
```

HOLDS and DESIGNED_FOR edges are untouched — they describe each portfolio,
not the defender role. The next weekly snapshot computes gaps against the new
defender. (For an accepted **reallocation**, instead: the defender keeps its
flag and its `allocation` map is updated to the applied target after the
user confirms the rebalance via UC9.)

---

## Step 10 — Twelve weeks later: outcome verdict (unified improvement cycle)

On Monday 2026-08-24 the weekly `outcomes.py` job (08:52) finds the Step 8B
switch Proposal (`prop-20260526`) aged ≥ `proposal_outcome_weeks` (12) and
measures it — same NAV conventions, net of `replay_cost_bps` × turnover:

```
EventLog { id:"01K2...", ts:2026-08-24T08:52, type:"OutcomeEvent",
           source_uc:"system", source_id:"prop-20260526",
           payload:'{"kind":"proposal","proposed_return":0.041,
                     "incumbent_return":0.028,"verdict":"won"}' }

Proposal#prop-20260526 {
  outcome: {proposed_return: 0.041, incumbent_return: 0.028, verdict: "won"}
  evaluated_at: 2026-08-24
}
```

(As an accepted paper-test — `paper_started: 2026-05-26`, Step 9 — it was
already tracked in every intervening digest's scoreboard; the +12w verdict
is the maturation point that turns tracking into confrontations.)

The verdict confronts the invariants that backed the challenger
(`source='proposal'`, confirmation, severity 1.0) — their
`confirmation_count`, `updated_at` and `weight_effective` move, closing the
loop: the insight that argued for the proposal is now credited by reality.
The digest scoreboard shows: `Proposals hit-rate: 1/1 (100%) at +12w`.
A 'lost' verdict would instead append infirmations — a repeatedly wrong
insight decays toward its floor and (below
`proposal_invariant_weight_min`) can no longer justify a reallocation.

---

## Appendix — Corpus path: Document → Passage → SUPPORTS → Invariant

Populated by the nightly ingestion job (02:00) — the same `CorpusIngester`
the UC0 seed uses. IngestionEvent precedes the vertex batch.

```
Document {
  id: "dalio-big-debt-crises-2018"
  title: "Principles for Navigating Big Debt Crises"
  author: "dalio"
  source_type: "pdf"
  source_path: "~/data/investment/sources/corpus/dalio-big-debt-crises.pdf"
  ingested_at: 2026-01-15, chunk_count: 312
  trace: "Primary corpus source for inflation and debt-cycle invariants"
}

Document -[CONTAINS position:87 page:142]-> Passage {
  id: "pass-dalio-tips-142"
  content: "TIPS have provided reliable inflation protection in every
            inflationary episode since their introduction in 1997..."
  page: 142, chunk_id: "dalio-big-debt-87"
  embedding: [384 floats]
  created_at: 2026-01-15
  -- no trace: TRACE_EXEMPT, inherits from parent Document
}

Passage#pass-dalio-tips-142 -[SUPPORTS strength:0.90
  excerpt:"TIPS reliable inflation protection in every inflationary episode"]->
  Invariant#inv-inflation-persistence-tips
```

---

## Summary — All entities involved

| Entity | Instances | Role in this cycle |
|--------|-----------|-------------------|
| RegimeType | 1 | Static type — hosts FAVORS and DESIGNED_FOR |
| Regime | 2 | Current (2026-05-01) + historical (2021-03-01, UC0-materialized) |
| MarketData TS | 3 rows | CPI YoY, GROWTH_COMPOSITE, GLOBAL_LIQUIDITY |
| ScenarioProbability TS | 1 row | Bear shift +35pts recorded |
| Strategy | 4 | Seeded ids; FAVORS edges + HOLDS by portfolios |
| Invariant | 3+1 | 2 user-validated discoveries + 1 Dalio + 1 proposed |
| Scenario | 3 | sc-4s-bull/base/bear (four-seasons-rp) |
| Evaluation | 1 | Verdict confirms → mechanical confirmations |
| Backtest | 1 | 2021-2022 episode; IN_REGIME → historical instance |
| Proposal | 2 | 8A reallocation + 8B switch — the two V1 kinds |
| Portfolio | 7 | All ranked; snapshot rows |
| EventLog | 5+ | RegimeEvent, InnovationEvent, 2× ProposalEvent, UserDecisionEvent |
| Document / Passage | 1 / 1 | SUPPORTS → inv-inflation-persistence-tips |

**Edges active:** UPDATES, FAVORS (RegimeType→Strategy), HAS_SCENARIO,
BACKED_BY, TESTED_IN, IN_REGIME (→Regime), HOLDS,
DESIGNED_FOR (→RegimeType), CONTAINS, SUPPORTS.
**V2 additions (not in the V1 schema):** Adaptation vertex, MODIFIES edge —
V1 paper-mode uses Proposal.
Scenario A (reallocation) and Scenario B (switch) cover the two UC8 outcomes;
'maintain' produces no Proposal vertex at all.
