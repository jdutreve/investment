# EXAMPLE.md — Full cycle: Stagflation May 2026

Full trace of a real cycle: events → regime → portfolio ranking
→ Worker cycle → innovation → proposal to user.
Each ArcadeDB entity is instantiated with its actual properties.

**Two complete scenarios are shown:**
- **Scenario A (Steps 1–8A):** defender ranks first → weekly digest, no Proposal vertex
- **Scenario B (Step 8B):** challenger beats defender → Proposal vertex, paper-test

**Vocabulary convention:** financial / economic terms throughout. Performance numbers
are *indicators* (Sharpe, Sortino, Calmar, max_drawdown, total_return, volatility),
never "ratios" as a generic field name.

**Reference asset universe** (Phase 1 accumulation, USD-quoted, used to build all
example portfolios): equities `SPY VTI QQQ EFA EEM`; rates `TLT IEF SHY BIL`;
inflation-protected `TIP`; gold & commodities `GLD DJP DBC`; cash `cash` (USD MMF).
Currency overlay quotes back to CHF when the portfolio currency is CHF.

---

## Step 1 — Tier 1 events of 11 May 2026

Numeric indicators (CPI, PMI, VIX, prices) flow into the **MarketData TS** (Step 2),
not into Signal vertices. Signal is reserved for purely qualitative events with no
direct TS analogue (e.g. *"Greenspan nomination"*, *"central-bank communiqué shift"*).
Even those may eventually be promoted to **Event vertices** in V2 — the Signal vertex
itself is under review (Event is a proper Vertex with edges, Signal is a thin record).

For 11 May 2026, all incoming Tier 1 information is numeric and is captured directly
in the MarketData TS rows below. No Signal vertex is created for this cycle.

---

## Step 2 — Regime detection

Regime detection is **mechanical** (daily job 06:50). It reads `level`, `speed`, and
`acceleration` from the MarketData TS.

### 2a — MarketData TS rows (daily mechanical job 06:30)

```
MarketData { ticker:"CPIAUCSL", asset_class:"MACRO", currency:"USD"
  date:2026-05-11
  level:3.1          ← YoY CPI; threshold rising-inflation = level > 2.5
  speed:+0.30        ← MoM change in YoY CPI; positive → accelerating inflation
  acceleration:+0.15 ← speed itself accelerating → early regime-shift warning
}

MarketData { ticker:"PMICOMP", asset_class:"MACRO", currency:"USD"
  date:2026-05-11
  level:47.2         ← PMI; threshold falling-growth = level < 50
  speed:-1.40        ← MoM PMI change; negative → growth contracting
  acceleration:-0.30 ← contraction worsening
}

MarketData { ticker:"GLOBAL_LIQ_COMPOSITE", asset_class:"GLOBAL_LIQUIDITY"
  currency:"USD"
  date:2026-05-11
  level:98.4         ← below 100 = contraction vs baseline
  speed:-0.80        ← negative → tightening
  acceleration:-0.40 ← tightening decelerating (pace of contraction easing)
}
```

`global_liquidity: "tightening"` in Proposal.market_context (Step 8B) is derived from
this row: `level < 100 AND speed < 0`.

### 2b — RegimeType (seeded once at UC0) and Regime instance (daily job)

`RegimeType` is seeded at UC0 and never mutated. `Regime` is a concrete occurrence
created/updated by `detect_regime()`. FAVORS and DESIGNED_FOR point to the type;
IN_REGIME points to the instance.

```
RegimeType {
  id: "falling-growth-rising-inflation"
  name: "Stagflation"
  aliases: ["stagflation"]
  framework_id: "4seasons"
  description: "Falling growth (PMI < 50) combined with rising inflation (CPI > 2.5%).
                Maps to the 4 Seasons quadrant 'growth falling + inflation rising'.
                Seeded at UC0."
  created_at: 2026-01-01
}
```

Detection rule from `system_thresholds`: CPI level > 2.5 AND speed > 0 → rising-inflation;
PMI level < 50 AND speed < 0 → falling-growth; both → `falling-growth-rising-inflation`.
Acceleration on both axes → confidence boosted.

```
Regime {
  id: "stagflation-2026-05-01"
  regime_type_id: "falling-growth-rising-inflation"
  tags: ["macro", "stagflation", "inflation-rising", "growth-falling",
         "phase-1-accumulation", "global-liquidity-contraction"]
  start_date: 2026-05-01, end_date: null
  is_current: true, confidence: 78
  events: [
    "CPI level 3.1 (speed +0.30, accel +0.15)",
    "PMI 47.2 (speed -1.40, accel -0.30)",
    "global liquidity tightening (level 98.4, speed -0.80, accel -0.40)"
  ]
  trace: "Acceleration on both axes confirms regime, not a transient blip."
  created_at: 2026-05-01
  updated_at: 2026-05-11    ← as_of
}
```

---

## Step 3 — Custom strategy and its Invariants

Strategy defined before the FAVORS edges that reference it (Step 4).

```
Strategy {
  id: "stagflation-custom-v2"
  title: "4 Seasons + Gold overlay in stagflation"
  description: "Adapted 4 Seasons allocation tilted toward GLD and TIPS to capture
                stagflation's dual tailwinds: falling real yields and rising risk
                aversion. Overrides the standard 7.5% GLD with a 20-35% overlay
                and replaces the long-bond sleeve with TIPS."
  regime_type_id: "falling-growth-rising-inflation"
  framework_id: "4seasons"
  conviction: 74
  enabled: true
  conditions: "stagflation (CPI > 3% AND PMI < 48) — orthogonal indicator: VIX > 22"
  source: "agent-discovery"
  status: "active"
  date_opened: 2026-03-01, date_revised: 2026-05-11
  trace: "Discovered March 2026: overweighting GLD + TIPS improves
          Sortino +0.24 vs standard 4 Seasons in stagflation.
          Backtest 2021-2022 confirmed."
  created_at: 2026-03-01
  updated_at: 2026-05-11
}

Strategy#stagflation-custom-v2 -[BACKED_BY strength:0.9 added_at:2026-03-01
  excerpt:"GLD +18% vs 4S in stagflation 2021-2022"]->
  Invariant#gold-stagflation-hedge

Strategy#stagflation-custom-v2 -[BACKED_BY strength:0.7 added_at:2026-03-01
  excerpt:"Calmar > 1.5 filters unrecoverable drawdowns"]->
  Invariant#calmar-accumulation

Strategy#stagflation-custom-v2 -[BACKED_BY strength:0.6 added_at:2026-03-01
  excerpt:"TIPS reliable inflation protection — Dalio"]->
  Invariant#tips-inflation-hedge
```

Convention: strategy IDs lead with the `regimeType.alias` (`stagflation-…`) so the
target regime is immediately visible. Framework-neutral strategies (`4seasons`,
`permanent`, `momentum-macro`) keep their canonical names because they are not tied
to a single regime type.

### Invariant weight mechanics

**Rule:** `weight_effective = max(weight_initial × market_score × recency_factor, floor_weight)`

`weight_initial` is the **ceiling** — weight can only decay below it (via refutations or
time) or be floored. Confirmations **preserve** weight against decay; they do not push
it above `weight_initial`. Authority gradient is enforced at creation via initial weight
and floor (`author:"dalio"` → floor 0.40, `author:"system"` → floor 0.05).

---

**Invariant#gold-stagflation-hedge** — 3/3 confirmations

```
Invariant {
  id: "gold-stagflation-hedge"
  title: "GLD outperforms in stagflation vs standard 4 Seasons"
  description: "In stagflation, gold benefits from dual tailwinds: real yields fall
                (inflation up, growth down) and risk aversion rises. Standard 4 Seasons
                holds ~7.5% GLD; this invariant supports a 25-35% overlay."
  example: "2021-2022: GLD +18% vs 4 Seasons GLD allocation +11% while PMI stayed
            below 50 for 8 consecutive months."
  topic: ["gold","stagflation","inflation","allocation"]
  tags: ["asset:GLD", "asset-class:commodities", "indicator:max_drawdown",
         "regime:stagflation"]
  source: "Backtest stagflation 2021-2022 — GLD +18% vs 4S GLD allocation +11%
           (computed 2026-03-01 from yfinance daily closes)"
  author: "system"
  status: "integrated"
  floor_weight: 0.05              ← system (agent-discovery) floor
  weight_initial: 0.25            ← ceiling for this invariant
  confirmation_count: 3
  infirmation_count: 0
  market_score: 1.0               ← 3/(3+0)
  recency_factor: 0.992           ← max(0.5, 0.5+0.5×exp(-6/365)); 6d since last confrontation
  weight_effective: 0.248         ← max(0.25 × 1.0 × 0.992, 0.05) = 0.248
                                     near ceiling — zero refutations preserve it
  embedding: [768 floats]
  trace: "Discovered analyzing stagflation 2021-2022:
          GLD +18% vs GLD 4Seasons +11% in this regime.
          Confirmed: Oct 2024 (partial stagflation), Mar 2026, May 2026 (ongoing)."
  created_at: 2026-03-01
  validated_at: 2026-03-01
  updated_at: 2026-05-11
}
```

**Invariant#calmar-accumulation** — 2 confirmations / 1 refutation

```
Invariant {
  id: "calmar-accumulation"
  title: "Calmar indicator > Sharpe as primary selection metric in accumulation"
  description: "Sharpe penalizes all volatility equally, including upside. Calmar
                (annualized return / |max_drawdown|) directly measures recovery potential
                — critical when the investor has a long horizon but limited capacity to
                absorb a -35% event. In Phase 1 accumulation, Calmar is the primary
                selection indicator."
  example: "2008: best-Sharpe strategies had max_drawdown -35% to -50%; best-Calmar
            strategies had max_drawdown -8% to -15%, recovering 3× faster."
  topic: ["calmar","sharpe","drawdown","accumulation","risk"]
  tags: ["indicator:calmar", "indicator:sharpe", "indicator:max_drawdown",
         "phase:accumulation"]
  source: "Backtests 2008 / 2020 / 2022 cross-validation (computed 2026-03-01)"
  author: "system"
  status: "integrated"
  floor_weight: 0.05
  weight_initial: 0.20
  confirmation_count: 2
  infirmation_count: 1
  market_score: 0.667             ← 2/(2+1)
  recency_factor: 0.920           ← max(0.5, 0.5+0.5×exp(-63/365)); 63d since last confrontation
  weight_effective: 0.123         ← max(0.20 × 0.667 × 0.920, 0.05) = 0.123
                                     1 refutation + time decay reduced weight
  embedding: [768 floats]
  trace: "Strategies with best Sharpe had -35% drawdown in 2008/2022,
          unrecoverable in 3 years. Calmar > 1.5 = more robust filter
          for Phase 1 accumulation. One refutation: 2023 bull run where
          high-Sharpe strategies outperformed."
  created_at: 2026-03-01
  validated_at: 2026-03-01
  updated_at: 2026-03-15
}
```

**Invariant#tips-inflation-hedge** — Dalio corpus, 8 confirmations / 1 refutation

```
Invariant {
  id: "tips-inflation-hedge"
  title: "TIPS protect against persistent inflation"
  description: "In regimes where inflation is both elevated (CPI > 2.5%) and sustained
                (speed > 0), nominal bonds lose real value while TIPS maintain purchasing
                power. Core to the Dalio All Weather framework."
  example: "2021-2022: TIP +2.3% while TLT -26%. In every inflationary episode
            since 1997 TIPS outperformed nominal Treasuries on real return."
  topic: ["tips","inflation","bonds","real-yield"]
  tags: ["asset:TIP", "asset-class:fixed-income", "indicator:real-yield",
         "regime:inflation-rising"]
  source: "Dalio — Principles for Navigating Big Debt Crises (2018), p.142
           (document#dalio-big-debt-crises-2018, passage#pass-dalio-tips-142)"
  author: "dalio"
  status: "integrated"
  floor_weight: 0.40              ← dalio floor — never falls below this
  weight_initial: 0.85            ← high initial authority (Dalio)
  confirmation_count: 8
  infirmation_count: 1
  market_score: 0.889             ← 8/(8+1)
  recency_factor: 0.992           ← 6 days since last confrontation
  weight_effective: 0.750         ← max(0.85 × 0.889 × 0.992, 0.40) = 0.750
                                     below initial: 1 refutation (2020 deflation)
  embedding: [768 floats]
  trace: "Dalio All Weather principle — validated over 40 years of data.
          Only refutation: 2020 deflation (extreme case out of scope)."
  created_at: 2026-01-15
  validated_at: 2026-01-15        ← integrated at corpus ingestion time
  updated_at: 2026-05-11
}
```

---

## Step 4 — FAVORS edges and Portfolio ranking

### 4a — FAVORS edges (RegimeType → Strategy)

Updated after each weekly backtest cycle. The edge indicators come from **synthetic
backtests of the strategy's prescribed allocation** replayed over every historical
Regime instance of this type — they are *strategy-level* numbers (allocation-only,
no live frictions) and must not be confused with portfolio-level rolling indicators
(Step 4b), which include the live portfolio's actual fills, drift and rebalancing.
`n_periods=8` = 8 distinct stagflation episodes in history.

Planner query:
```
MATCH (rt:RegimeType)<-[{regime_type_id}]-(r:Regime {is_current:true}),
      (rt)-[f:FAVORS]->(s:Strategy)
```

```
RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.65, sortino_rolling:1.09,
    calmar_rolling:1.7, max_drawdown:-7.2,
    n_periods:8, last_updated:2026-05-11]-> Strategy#stagflation-custom-v2

RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.58, sortino_rolling:0.91,
    calmar_rolling:1.3, max_drawdown:-8.8,
    n_periods:8, last_updated:2026-05-11]-> Strategy#4seasons

RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.45, sortino_rolling:0.69,
    calmar_rolling:1.0, max_drawdown:-10.1,
    n_periods:8, last_updated:2026-05-11]-> Strategy#permanent

RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.20, sortino_rolling:0.28,
    calmar_rolling:0.5, max_drawdown:-19.4,
    n_periods:8, last_updated:2026-05-11]-> Strategy#momentum-macro
```

### 4b — Portfolio ranking (portfolio_weekly_snapshot, Monday 08:00)

All 4 enabled portfolios ranked by USD rolling indicators (current period, not
aggregated). `gap_to_defender` is null for the defender; challengers show delta
vs the defender.

**Indicator window:** rolling **36 months** (≈ 756 trading days). Cumulative-return
columns (`return_3m`, `return_6m`, `return_1y`, `return_3y`, `return_5y`) are computed
on the calendar windows ending at `date`.

**Rank rule:** primary key = `sortino_rolling` (desc). Tie-break (within 0.02):
secondary = `calmar_rolling` (desc), tertiary = `max_drawdown` (less negative wins).
Snapshots with `calmar_rolling < 1.0` are demoted to the bottom regardless of Sortino
(Invariant#calmar-accumulation gate). The Phase 1 max-drawdown rule (-15%) is a
hard exclusion for the defender role.

```
portfolio_weekly_snapshot {
  date: 2026-05-12, defender: true, framework_id: "4seasons"
  portfolio_id: "portfolio-a"
  designed_regime_type_id: "falling-growth-rising-inflation"
  primary_strategy_id: "stagflation-custom-v2"
  allocation: {GLD:20, TIP:30, DJP:15, cash:15, SPY:20}
  rank: 1
  sharpe_rolling: 0.71, sortino_rolling: 1.18, calmar_rolling: 1.9
  max_drawdown: -4.1, volatility: 0.08
  return_3m: +3.8, return_6m: +7.2, return_1y: +14.3,
  return_3y: +32.1, return_5y: +48.6
  gap_to_defender: null
  recommendation: "maintain"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Defender holds rank 1 — Calmar 1.9 best in class for Phase 1 accumulation"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, defender: false, framework_id: "4seasons"
  portfolio_id: "portfolio-4seasons"
  designed_regime_type_id: null         ← framework-neutral; no DESIGNED_FOR edge
  primary_strategy_id: "4seasons"
  allocation: {VTI:30, TIP:15, GLD:7.5, TLT:15, cash:32.5}
  rank: 2
  sharpe_rolling: 0.61, sortino_rolling: 0.94, calmar_rolling: 1.4
  max_drawdown: -8.1, volatility: 0.10
  return_3m: +2.4, return_6m: +5.1, return_1y: +11.2,
  return_3y: +24.8, return_5y: +39.5
  gap_to_defender: {sharpe_delta:-0.10, sortino_delta:-0.24, calmar_delta:-0.5,
                    max_drawdown_delta:-4.0}
  recommendation: "monitor"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Standard 4 Seasons — solid but below defender; GLD underweighted for stagflation"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, defender: false, framework_id: "4seasons"
  portfolio_id: "portfolio-permanent"
  designed_regime_type_id: null
  primary_strategy_id: "permanent"
  allocation: {GLD:25, TLT:25, BIL:25, VTI:25}
  rank: 3
  sharpe_rolling: 0.48, sortino_rolling: 0.72, calmar_rolling: 1.1
  max_drawdown: -9.4, volatility: 0.09
  return_3m: +1.7, return_6m: +3.9, return_1y: +9.1,
  return_3y: +19.4, return_5y: +33.0
  gap_to_defender: {sharpe_delta:-0.23, sortino_delta:-0.46, calmar_delta:-0.8,
                    max_drawdown_delta:-5.3}
  recommendation: "monitor"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Permanent portfolio — defensive but Calmar below threshold 1.5"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, defender: false, framework_id: "4seasons"
  portfolio_id: "portfolio-momentum"
  designed_regime_type_id: null
  primary_strategy_id: "momentum-macro"
  allocation: {QQQ:40, IEF:20, GLD:10, cash:30}
  rank: 4
  sharpe_rolling: 0.22, sortino_rolling: 0.31, calmar_rolling: 0.6
  max_drawdown: -18.2, volatility: 0.16
  return_3m: -0.4, return_6m: +1.2, return_1y: +6.2,
  return_3y: +14.7, return_5y: +27.3
  gap_to_defender: {sharpe_delta:-0.49, sortino_delta:-0.87, calmar_delta:-1.3,
                    max_drawdown_delta:-14.1}
  recommendation: "monitor"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Momentum-macro — poor stagflation fit; max_drawdown exceeds -15% rule"
}
```

Edges for the defender portfolio:

```
Portfolio#a -[HOLDS primary:true weight:1.0 since:2026-03-01]-> Strategy#stagflation-custom-v2
Portfolio#a -[DESIGNED_FOR rationale:"custom overlay built for stagflation regime"]->
              RegimeType#falling-growth-rising-inflation
  -- DESIGNED_FOR points to the type: "designed for stagflation in general",
  --   not for the May 2026 occurrence specifically
```

---

## Step 5 — Scenarios and Evaluation

```
Scenario#bear {
  id: "sc-custom-bear", name: "bear"
  probability: 55               ← was 20 seven days ago
  probability_d7: 20
  triggers: ["VIX > 25", "CPI > 3%", "PMI < 48"]
  target_allocation: {GLD:35, TIP:30, DJP:15, cash:15, SPY:5}
  currency: "USD"
  trace: "Massive shift +35pts — stagflation bear confirmed"
  updated_at: 2026-05-11
}

Scenario#base {
  id: "sc-custom-base", name: "base"
  probability: 35, probability_d7: 55
  triggers: ["CPI 2.5-3%", "PMI 48-52"]
  target_allocation: {GLD:20, TIP:25, DJP:15, cash:15, SPY:25}
  currency: "USD"
  trace: "Base scenario probability collapsed from 55% to 35% — bear took the shift"
  updated_at: 2026-05-11
}

Scenario#bull {
  id: "sc-custom-bull", name: "bull"
  probability: 10, probability_d7: 25
  triggers: ["CPI < 2.5%", "PMI > 52"]
  target_allocation: {GLD:10, TIP:15, DJP:10, cash:5, SPY:60}
  currency: "USD"
  trace: "Bull scenario unlikely given CPI acceleration and PMI contraction"
  updated_at: 2026-05-11
}

Strategy#stagflation-custom-v2 -[HAS_SCENARIO active:true]-> Scenario#bear
Strategy#stagflation-custom-v2 -[HAS_SCENARIO active:true]-> Scenario#base
Strategy#stagflation-custom-v2 -[HAS_SCENARIO active:true]-> Scenario#bull

MarketData#CPIAUCSL@2026-05-11 -[GENERATES date:2026-05-11]-> Evaluation#eval-20260511
MarketData#PMICOMP@2026-05-11  -[GENERATES date:2026-05-11]-> Evaluation#eval-20260511

Evaluation {
  id: "eval-20260511"
  date: 2026-05-11
  verdict: "confirms"
  conviction_delta: +6
  reasoning: "CPI re-accelerates + PMI < 50 + VIX spike = exact trigger conditions
              of stagflation-custom. Conviction 74 → 80."
  trace: "3 convergent Tier 1 indicators — strong confirmation"
  created_at: 2026-05-11
}

Evaluation#eval-20260511 -[UPDATES conviction_delta:+6 date:2026-05-11]->
  Strategy#stagflation-custom-v2
```

ScenarioProbability TS records the shift for UC8 to read:

```
ScenarioProbability { strategy_id:"stagflation-custom-v2", scenario:"bear",
                      probability:55, shift_d7:+35.0 }
```

---

## Step 6 — Worker cycle and Innovation

Weekly cycle (Monday 09:00). Planner Pre queries DB and builds PlannerContext.
Worker receives context via tool calls only — unaware of Planner, Writeback, or storage.

### 6a — Simplified WorkerResult

```python
WorkerResult {
  regime_assessment: "Stagflation confirmed (78%). CPI accelerating, PMI contracting,
                      global liquidity tightening. Bear scenario 55% (+35pts shift).
                      Strategy stagflation-custom-v2 ranks 1st across all indicators."

  portfolio_ranking: [
    {portfolio_id:"portfolio-a",        rank:1, sortino:1.18, calmar:1.9},
    {portfolio_id:"portfolio-4seasons", rank:2, sortino:0.94, calmar:1.4},
    {portfolio_id:"portfolio-permanent",rank:3, sortino:0.72, calmar:1.1},
    {portfolio_id:"portfolio-momentum", rank:4, sortino:0.31, calmar:0.6},
  ]

  proposal_recommended: null   # defender rank 1 → no switch proposal this week

  innovations_proposed: [
    ImprovementProposal {
      type: "data"
      title: "Calmar > 1.5 as strategy selection criterion — optimal threshold"
      rationale: "Backtests 2008/2020/2022: Calmar > 1.5 → max_drawdown < 10%
                  vs < 20% for Calmar < 1. Threshold 1.5 optimal across 3 crises."
      spec: {"indicator": "calmar_rolling", "threshold": 1.5, "operator": "gt"}
      source: "Backtests 2008 / 2020 / 2022 (computed 2026-05-12)"
      author: "system"
      status: "proposed"
      weight_initial: 0.15, floor_weight: 0.05
      trace: "Discovered during portfolio ranking analysis — Calmar consistent filter
              for unrecoverable drawdowns in Phase 1 accumulation."
    }
  ]

  reasoning: "Defender holds rank 1 with strong Calmar (1.9 > threshold 1.5).
              GLD overlay confirmed effective in current stagflation.
              No switch recommended. Allocation drift vs bear target noted
              (+15% GLD gap) — user may wish to review manually."
}
```

### 6b — Planner Post processes the innovation

Event TS append precedes vertex creation (architectural invariant):

```
Event { type:"InnovationEvent", source_uc:"UC7", source_id:"wresult-20260512",
        payload:'{"invariant_id":"calmar-v2-threshold","status":"proposed"}' }
```

```
Invariant {
  id: "calmar-v2-threshold"
  title: "Calmar > 1.5 as strategy selection criterion — optimal threshold"
  description: "Threshold derived by backtesting across 2008, 2020, 2022 crises.
                Calmar > 1.5 minimizes max_drawdown while preserving adequate return
                — selection filter for Phase 1 accumulation strategies."
  example: "2022: strategies with Calmar 1.6+ had max_drawdown < 9%;
            strategies with Calmar 0.8 had max_drawdown -22%."
  topic: ["calmar","threshold","selection","drawdown"]
  tags: ["indicator:calmar", "indicator:max_drawdown", "phase:accumulation"]
  source: "Backtests 2008 / 2020 / 2022 (computed 2026-05-12)"
  author: "system"
  status: "proposed"             ← not committed until user validates
  floor_weight: 0.05
  weight_initial: 0.15
  weight_effective: 0.15         ← max(0.15 × 1.0 × 1.0, 0.05); no confrontation yet
  confirmation_count: 0
  infirmation_count: 0
  market_score: 1.0              ← default until first confrontation
  recency_factor: 1.0            ← created today
  embedding: [768 floats]
  trace: "Backtest analysis 2008/2020/2022: strategies with Calmar > 1.5 had
          max_drawdown < 10% vs < 20% for Calmar < 1. Pending user validation."
  created_at: 2026-05-12
  validated_at: null             ← set when user clicks [YES]
  updated_at: 2026-05-12
}
```

**Telegram to user:**
```
💡 Innovation agent:
   Calmar indicator optimal threshold = 1.5
   Analyzed across 3 crises (2008/2020/2022)
   Filters unrecoverable drawdowns better than Sharpe
   Current weight: 0.15 (not validated — author: system)
   → Integrate as selection criterion? [YES] [NO]
```

User validates → `status: integrated`, `validated_at` set. `weight_effective` evolves
from first market confrontation via Backtest or Evaluation.

---

## Step 7 — Backtest validating the custom strategy

`Regime#stagflation-2021-03-01` is a pre-existing instance seeded at UC0, representing
the 2021-2022 stagflation episode (CPI peaked 9.1% Jun 2022, PMI below 50 Q3-Q4 2022).

```
Regime {
  id: "stagflation-2021-03-01"             ← pre-existing seeded instance
  regime_type_id: "falling-growth-rising-inflation"
  tags: ["macro", "stagflation", "historical", "post-covid-inflation"]
  start_date: 2021-03-01, end_date: 2022-12-31
  is_current: false, confidence: 91
  events: [
    "CPI peaked 9.1% Jun 2022",
    "PMI below 50 from Aug 2022",
    "global liquidity tightening Q4 2021 — Q4 2022"
  ]
  trace: "2021-2022 stagflation episode — seeded from regime_history at UC0."
  created_at: 2026-01-01
  updated_at: 2026-01-01
}
```

```
Backtest {
  id: "bt-stagflation-custom-2021-2022"
  period: "2021-2022"
  date_start: 2021-01-01, date_end: 2022-12-31
  sharpe_rolling: 0.68            ← 2-year period only; differs from 36M rolling
  sortino_rolling: 1.14
  calmar_rolling: 1.8             ← above threshold 1.5
  max_drawdown: -6.8
  total_return: 12.9
  currency: "USD"
  source: "mechanical"            ← Python computed; agent proposed the strategy
  status: "integrated"
  trace: "Calmar 1.8 in stagflation 2021-2022 — best across all seeded strategies.
          Confirms gold overlay improves downside protection."
  created_at: 2026-03-01
}

Strategy#stagflation-custom-v2 -[TESTED_IN is_primary:true]->
  Backtest#bt-stagflation-custom-2021-2022

Backtest#bt-stagflation-custom-2021-2022 -[IN_REGIME overlap_pct:0.91]->
  Regime#stagflation-2021-03-01
  -- IN_REGIME points to the specific historical instance, not the type

-- FAVORS edge on RegimeType updated after backtest ingestion
-- (aggregated across all 8 historical periods, not equal to this single backtest):
RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.65, sortino_rolling:1.09,
    calmar_rolling:1.7, max_drawdown:-7.2,
    n_periods:8, last_updated:2026-05-11]-> Strategy#stagflation-custom-v2
```

Three distinct number contexts in play:
- **Backtest 2021-2022**: sharpe 0.68 / sortino 1.14 / calmar 1.8 — single historical episode
- **FAVORS (aggregated 8 periods, strategy-level)**: sharpe 0.65 / sortino 1.09 / calmar 1.7
- **portfolio_weekly_snapshot (current 36M rolling, portfolio-level)**: sharpe 0.71 / sortino 1.18 / calmar 1.9

---

## Step 8A — Scenario A: defender ranks first (no Proposal)

Defender portfolio-a holds rank 1. UC8 evaluates gates:
- Challenger gap > threshold? No → no switch recommendation
- Allocation drift from bear target? Yes (+15% GLD) — noted in digest, not a Proposal

No Proposal vertex created. Weekly digest sent.

Event TS records the ranking cycle:
```
Event { type:"RankingEvent", source_uc:"UC7", source_id:"snap-20260512",
        payload:'{"defender_rank":1,"top_portfolio":"portfolio-a","no_proposal":true}' }
```

**Telegram weekly digest:**
```
📊 Regime: Stagflation (78% — falling-growth-rising-inflation)
   Global liquidity: tightening (level 98.4, speed -0.80)

🏆 Portfolio ranking (Sortino USD, rolling 36M):
   1. Portfolio A — 4S + Gold overlay : 1.18 ★ (defender) Calmar 1.9
   2. Portfolio 4 Seasons standard    : 0.94             Calmar 1.4
   3. Portfolio Permanent             : 0.72             Calmar 1.1
   4. Portfolio Momentum Macro        : 0.31             Calmar 0.6 ⚠️

🔑 Key Invariants (effective weight):
   • GLD stagflation hedge : 0.248 (3/3 confirmed, near ceiling) [system]
   • TIPS inflation        : 0.750 (8/9 confirmed)               [Dalio]
   • Calmar > 1.5 filter   : 0.123 (2/3 confirmed)               [system]

ℹ️ Allocation note:
   Current GLD: 20% | Bear target: 35% (+15% gap)
   No proposal — defender holds rank 1. Review manually if you wish to
   align with bear target allocation.

📈 Indicators (USD, 36M rolling): Sharpe 0.71 | Sortino 1.18 | Calmar 1.9
   Returns: 3m +3.8% | 6m +7.2% | 1y +14.3% | 3y +32.1% | 5y +48.6%
```

---

## Step 8B — Scenario B: challenger beats defender → Proposal

*Two weeks later: portfolio-4seasons edges past portfolio-a in the weekly ranking.
GLD pulls back 4% over the fortnight; standard 4 Seasons benefits from broader TLT
allocation as rate expectations shift.*

Updated portfolio_weekly_snapshot:

```
portfolio_weekly_snapshot {
  date: 2026-05-26, defender: false, framework_id: "4seasons"
  portfolio_id: "portfolio-4seasons"
  designed_regime_type_id: null
  primary_strategy_id: "4seasons"
  allocation: {VTI:30, TIP:15, GLD:7.5, TLT:15, cash:32.5}
  rank: 1
  sharpe_rolling: 0.74, sortino_rolling: 1.22, calmar_rolling: 1.5
  max_drawdown: -7.8
  return_3m: +2.9, return_6m: +5.7, return_1y: +11.8,
  return_3y: +25.4, return_5y: +40.1
  gap_to_defender: {sharpe_delta:+0.03, sortino_delta:+0.04, calmar_delta:-0.4,
                    max_drawdown_delta:-3.7}
  recommendation: "paper-test"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Challenger edges past defender — narrow gap, GLD pullback driven"
}

portfolio_weekly_snapshot {
  date: 2026-05-26, defender: true, framework_id: "4seasons"
  portfolio_id: "portfolio-a"
  designed_regime_type_id: "falling-growth-rising-inflation"
  primary_strategy_id: "stagflation-custom-v2"
  allocation: {GLD:20, TIP:30, DJP:15, cash:15, SPY:20}
  rank: 2
  sharpe_rolling: 0.71, sortino_rolling: 1.18, calmar_rolling: 1.9
  max_drawdown: -4.1
  return_3m: +3.5, return_6m: +6.9, return_1y: +14.0,
  return_3y: +31.8, return_5y: +48.3
  gap_to_defender: null
  recommendation: "monitor"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Defender drops to rank 2 — challenger triggers paper-test gate"
}
```

UC8 evaluates gates: challenger rank 1, sortino gap +0.04 > threshold → Proposal.
Concentration check: challenger allocation max asset = VTI 30% < 40% ✅

Event TS append precedes Proposal vertex:
```
Event { type:"ProposalEvent", source_uc:"UC8", source_id:"prop-20260526",
        payload:'{"proposal_id":"prop-20260526","recommendation":"paper-test"}' }
```

```
Proposal {
  id: "prop-20260526"
  date: 2026-05-26
  defender_id: "portfolio-a"
  challenger_id: "portfolio-4seasons"
  recommendation: "paper-test"
  defender_rank: 2
  challenger_rank: 1
  gap: {
    sharpe_delta: +0.03, sortino_delta: +0.04, calmar_delta: -0.4,
    max_drawdown_delta: -3.7,
    allocation_diff: {TLT:+15, GLD:-12.5, TIP:-15, VTI:+30, SPY:-20, DJP:-15,
                      cash:+12.5}
  }
  market_context: {
    framework: "4seasons", regime_type_id: "falling-growth-rising-inflation",
    confidence: 78, global_liquidity: "tightening"
  }
  reasoning: "portfolio-4seasons edges past defender on Sortino (1.22 vs 1.18)
              and Sharpe (0.74 vs 0.71). Calmar lower (1.5 vs 1.9) — still
              above threshold 1.5. Standard 4 Seasons benefits from TLT
              re-entry as rate expectations moderate.
              Recommend paper-test: monitor for 4 weeks before any manual switch."
  user_response: "pending"
  paper_started: null            ← set only when user accepts (Step 9)
  trace: "First challenger promotion in 12 weeks"
  created_at: 2026-05-26
}
```

**Telegram to user:**
```
📊 Regime: Stagflation (78%)

🔄 Challenger alert — paper-test recommended:
   Rank 1: Portfolio 4 Seasons standard  Sortino 1.22  Calmar 1.5
   Rank 2: Portfolio A (defender)        Sortino 1.18  Calmar 1.9 ★

   Gap: Sortino +0.04 | Sharpe +0.03 | Calmar -0.4 (challenger weaker on drawdown)

   Note: standard 4 Seasons has LOWER Calmar (1.5 vs 1.9) — the gap is narrow
   and driven by a 2-week GLD pullback. Monitor 4 weeks before switching.

→ [ACCEPT PAPER-TEST] [REJECT]
```

---

## Step 9 — Portfolio after user acceptance

User accepts paper-test → `user_response: "accepted"`, `paper_started` now set.
V1 does NOT auto-apply. Agent records acceptance and begins paper-tracking the challenger.
Defender portfolio unchanged until user manually rebalances.

```
Proposal#prop-20260526 {
  user_response: "accepted"
  paper_started: 2026-05-26      ← set at acceptance, not at creation
}
```

If user manually rebalances to portfolio-4seasons allocation, the Portfolio vertex is
updated by agent for paper-tracking:

```
Portfolio {
  id: "portfolio-a"
  name: "4 Seasons + Gold overlay CHF"
  framework_id: "4seasons"
  defender: true, enabled: true
  currency: "CHF"
  benchmark: "SPY"
  phase: "accumulation"
  allocation: {VTI:30, TIP:15, GLD:7.5, TLT:15, cash:32.5}  ← post-rebalance
  max_drawdown_rule: -15.0
  max_single_asset_pct: 40.0
  fx_usd_exposure: 55
  sharpe_rolling: 0.74, sortino_rolling: 1.22, calmar_rolling: 1.5
  max_drawdown: -7.8, volatility: 0.10
  return_3m: +2.9, return_6m: +5.7, return_1y: +11.8,
  return_3y: +25.4, return_5y: +40.1
  date_revised: 2026-05-26
  trace: "User accepted paper-test and manually rebalanced to 4 Seasons standard.
          Custom gold overlay suspended pending GLD recovery."
  updated_at: 2026-05-26
}

Portfolio#a -[HOLDS primary:true weight:1.0 since:2026-05-26]-> Strategy#4seasons
Portfolio#a -[DESIGNED_FOR rationale:"4 Seasons neutral framework allocation"]->
              RegimeType#falling-growth-rising-inflation
```

---

## Appendix — Corpus path: Document → Passage → SUPPORTS → Invariant

Shown separately: this path is populated by the nightly ingestion job (02:00).

```
Document {
  id: "dalio-big-debt-crises-2018"
  title: "Principles for Navigating Big Debt Crises"
  author: "dalio"
  source_type: "pdf"
  source_path: "/data/investment/inbox/dalio-big-debt-crises.pdf"
  ingested_at: 2026-01-15, chunk_count: 312
  trace: "Primary corpus source for inflation and debt-cycle invariants"
}

Document -[CONTAINS position:87 page:142]-> Passage {
  id: "pass-dalio-tips-142"
  content: "TIPS have provided reliable inflation protection in every inflationary
            episode since their introduction in 1997, including the 2021-2022 surge..."
  page: 142, chunk_id: "dalio-big-debt-87"
  embedding: [768 floats]
  created_at: 2026-01-15
}

Passage#pass-dalio-tips-142 -[SUPPORTS strength:0.90
  excerpt:"TIPS reliable inflation protection in every inflationary episode"]->
  Invariant#tips-inflation-hedge
```

---

## Summary — All entities involved

| Entity | Instances | Role in this cycle |
|--------|-----------|-------------------|
| Signal | 0 | Reserved for qualitative events with no TS analogue — none this cycle |
| RegimeType | 1 | Static type — hosts FAVORS and DESIGNED_FOR |
| Regime | 2 | Current (2026-05-01) + historical (2021-03-01) via IN_REGIME |
| MarketData TS | 3 rows | CPI, PMI, global liquidity — level/speed/accel |
| ScenarioProbability TS | 1 row | Bear shift +35pts recorded |
| Strategy | 4 | FAVORS edges + HOLDS by portfolios |
| Invariant | 3+1 | Differentiated weights; 1 proposed (status:proposed) |
| Scenario | 3 | Bull/base/bear — all with trace, triggers, target_allocation |
| Evaluation | 1 | Verdict: confirms, date mandatory |
| Backtest | 1 | 2021-2022 period; numbers distinct from rolling and aggregated |
| Proposal | 1 | Scenario B: paper_started null at creation, set at acceptance |
| Portfolio | 4 | All ranked; portfolio_weekly_snapshot |
| portfolio_weekly_snapshot | 6 rows | 4 (Step 4b) + 2 (Step 8B) |
| Document | 1 | Dalio corpus source |
| Passage | 1 | SUPPORTS → Invariant#tips-inflation-hedge |

**Three distinct indicator contexts — never to be confused:**
- FAVORS edge: aggregated across n_periods historical regime instances (strategy-level)
- Backtest vertex: single historical episode (fixed date range)
- portfolio_weekly_snapshot: current rolling window (252d / 36M) — portfolio-level

**Edges active:** GENERATES (MarketData→Evaluation), UPDATES,
FAVORS (RegimeType→Strategy), HAS_SCENARIO, BACKED_BY, TESTED_IN,
IN_REGIME (→Regime), HOLDS, DESIGNED_FOR (→RegimeType), CONTAINS, SUPPORTS.
**Unused:** MODIFIES (V2 only), IMPLIES (no Signal vertices this cycle).

**Note:** Adaptation vertex is V2-only. V1 paper-mode uses Proposal.
Scenario A (no Proposal) and Scenario B (Proposal) cover the two distinct UC8 outcomes.
