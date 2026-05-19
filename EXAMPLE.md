# EXAMPLE.md — Full cycle: Stagflation May 2026

Full trace of a real cycle: signals → regime → portfolio ranking
→ Worker cycle → innovation → proposal to user.
Each ArcadeDB entity is instantiated with its actual properties.

**Two complete scenarios are shown:**
- **Scenario A (Steps 1–8A):** defender ranks first → weekly digest, no Proposal vertex
- **Scenario B (Step 8B):** challenger beats defender → Proposal vertex, paper-test

---

## Step 1 — Tier 1 Signals of 11 May 2026

Qualitative events collected via RSS and user deposits. Numeric data (CPI level,
PMI readings) belongs in the MarketData TS (see Step 2), not in Signal content.

```
Signal {
  id: "sig-20260511-cpi", date: 2026-05-11, temporal_date: 2026-05-11, tier: 1
  source: "rss"
  content: "CPI US April 2026: +3.1% YoY, accelerates vs March +2.8%"
  topic: ["cpi","inflation"], embedding: [768 floats]
  agent_version: "1.0.0"
  trace: "Unexpected re-acceleration — invalidates disinflation thesis"
  created_at: 2026-05-11T08:02:00
}

Signal {
  id: "sig-20260511-pmi", date: 2026-05-11, temporal_date: 2026-05-11, tier: 1
  source: "rss"
  content: "PMI Composite US: 47.2 — below 50 second consecutive month"
  topic: ["pmi","growth"], embedding: [768 floats]
  agent_version: "1.0.0"
  trace: "Activity contraction — falling-growth signal"
  created_at: 2026-05-11T08:02:01
}

Signal {
  id: "sig-20260511-vix", date: 2026-05-11, temporal_date: 2026-05-11, tier: 1
  source: "user-deposit"
  content: "VIX: 28.4 — spike above 25"
  topic: ["vix","risk"], embedding: [768 floats]
  agent_version: "1.0.0"
  trace: "Market stress — confirms bear scenario"
  created_at: 2026-05-11T08:05:00
}
```

---

## Step 2 — Regime detection

Regime detection is **mechanical** (daily job 06:50). It reads `level`, `speed`, and
`acceleration` from the MarketData TS — not from qualitative Signals. Signals then
create IMPLIES edges to the Regime instance to record which events are consistent with it.

### 2a — MarketData TS rows (daily mechanical job 06:30)

```
MarketData { ticker:"CPIAUCSL", asset_class:"MACRO", currency:"USD"
  date:2026-05-11
  close:3.1
  level:3.1          ← YoY CPI; threshold rising-inflation = level > 2.5
  speed:+0.30        ← MoM change in YoY CPI; positive → accelerating inflation
  acceleration:+0.15 ← speed itself accelerating → early regime-shift warning
  volume: null       ← macro series; no volume
  regime_id:"regime-stagflation-2026-05" }

MarketData { ticker:"PMICOMP", asset_class:"MACRO", currency:"USD"
  date:2026-05-11
  close:47.2
  level:47.2         ← PMI; threshold falling-growth = level < 50
  speed:-1.40        ← MoM PMI change; negative → growth contracting
  acceleration:-0.30 ← contraction accelerating → worsening signal
  volume: null
  regime_id:"regime-stagflation-2026-05" }

MarketData { ticker:"GLOBAL_LIQ_COMPOSITE", asset_class:"GLOBAL_LIQUIDITY"
  currency:"USD"
  date:2026-05-11
  close:null
  level:98.4         ← below 100 = contraction vs baseline
  speed:-0.80        ← negative → tightening
  acceleration:-0.40 ← tightening accelerating
  volume: null
  regime_id:"regime-stagflation-2026-05" }
```

`global_liquidity: "tightening"` in Proposal.market_context (Step 8B) is derived from
this row: `level < 100 AND speed < 0`.

### 2b — RegimeType (seeded once at UC0) and Regime instance (daily job)

`RegimeType` is seeded at UC0 and never mutated. `Regime` is a concrete occurrence
created/updated by `detect_regime()`. FAVORS and DESIGNED_FOR point to the type;
IMPLIES and IN_REGIME point to the instance.

```
RegimeType {
  id: "falling-growth-rising-inflation"
  name: "Stagflation"
  aliases: ["stagflation"]
  framework_id: "4seasons"
  description: "Falling growth (PMI < 50) combined with rising inflation (CPI > 2.5%)"
  trace: "4 Seasons quadrant: growth falling + inflation rising — seeded UC0"
  created_at: 2026-01-01T00:00:00
}
```

Detection rule from `system_thresholds`: CPI level > 2.5 AND speed > 0 → rising-inflation;
PMI level < 50 AND speed < 0 → falling-growth; both → `falling-growth-rising-inflation`.
Acceleration on both axes → confidence boosted.

```
Regime {
  id: "regime-stagflation-2026-05"
  regime_type_id: "falling-growth-rising-inflation"
  tags: []
  date_start: 2026-05-01, date_end: null
  is_current: true, confidence: 78
  signals_count: 3
  trace: "CPI level 3.1 (speed +0.30, accel +0.15) + PMI 47.2 (speed -1.4)
          + global liquidity tightening (level 98.4, speed -0.80).
          Acceleration on both axes confirms regime, not a transient blip."
  created_at: 2026-05-01T06:52:00
}
```

Qualitative Signals create IMPLIES edges to the **instance**:

```
Signal#cpi -[IMPLIES weight:0.6 tier:1]-> Regime#stagflation-2026-05
Signal#pmi -[IMPLIES weight:0.8 tier:1]-> Regime#stagflation-2026-05
Signal#vix -[IMPLIES weight:0.5 tier:1]-> Regime#stagflation-2026-05
```

---

## Step 3 — Custom strategy and its Invariants

Strategy defined before the FAVORS edges that reference it (Step 4).

```
Strategy {
  id: "custom-stagflation-v2"
  title: "4 Seasons + Gold overlay in stagflation"
  base_strategy: "custom"
  framework_id: "4seasons"
  conviction: 74
  enabled: true
  conditions: "stagflation (CPI > 3% AND PMI < 48) — orthogonal indicator: VIX > 22"
  revision_if: "CPI < 2.5% OR PMI > 52"
  horizon: "6-12 months"
  source: "agent-discovery", status: "active"
  benchmark: "SPY"
  date_opened: 2026-03-01, date_revised: 2026-05-11
  trace: "Discovered March 2026: overweighting GLD + TIPS improves
          Sortino +0.24 vs standard 4 Seasons in stagflation.
          Backtest 2021-2022 confirmed."
  created_at: 2026-03-01T09:00:00
}

Strategy#custom-stagflation-v2 -[BACKED_BY strength:0.9 added_at:2026-03-01
  excerpt:"GLD +18% vs 4S in stagflation 2021-2022"]->
  Invariant#gold-stagflation-hedge

Strategy#custom-stagflation-v2 -[BACKED_BY strength:0.7 added_at:2026-03-01
  excerpt:"Calmar > 1.5 filters unrecoverable drawdowns"]->
  Invariant#calmar-accumulation

Strategy#custom-stagflation-v2 -[BACKED_BY strength:0.6 added_at:2026-03-01
  excerpt:"TIPS reliable inflation protection — Dalio"]->
  Invariant#tips-inflation-hedge
```

### Invariant weight mechanics

**Rule:** `weight_effective = max(weight_initial × market_score × recency_factor, floor_weight)`

`weight_initial` is the **ceiling** — weight can only decay below it (via refutations or
time) or be floored. Confirmations **preserve** weight against decay; they do not push
it above `weight_initial`. Authority gradient is enforced at creation via initial weight
and floor (dalio=0.40, agent-discovery=0.05).

---

**Invariant#gold-stagflation-hedge** — agent-discovery, 3/3 confirmations

```
Invariant {
  id: "gold-stagflation-hedge"
  title: "GLD outperforms in stagflation vs standard 4 Seasons"
  content: "Gold significantly outperforms the standard 4 Seasons gold allocation
            during stagflationary regimes, providing both inflation protection
            and safe-haven premium."
  description: "In stagflation, gold benefits from dual tailwinds: real yields fall
                (inflation up, growth down) and risk aversion rises. Standard 4 Seasons
                holds ~7.5% GLD; this invariant supports a 25-35% overlay."
  example: "2021-2022: GLD +18% vs 4 Seasons GLD allocation +11% while PMI stayed
            below 50 for 8 consecutive months."
  topic: ["gold","stagflation","inflation","allocation"]
  durability: "long-term"
  source: "agent-discovery"
  status: "integrated"
  floor_weight: 0.05              ← agent-discovery floor
  weight_initial: 0.25            ← ceiling for this invariant
  confirmation_count: 3
  infirmation_count: 0
  market_score: 1.0               ← 3/(3+0)
  recency_factor: 0.992           ← max(0.5, 0.5+0.5×exp(-6/365)); confronted 6d ago
  weight_effective: 0.248         ← max(0.25 × 1.0 × 0.992, 0.05) = 0.248
                                     near ceiling — zero refutations preserve it
  last_confronted: 2026-05-11
  embedding: [768 floats]
  trace: "Discovered analyzing stagflation 2021-2022:
          GLD +18% vs GLD 4Seasons +11% in this regime.
          Confirmed: Oct 2024 (partial stagflation), Mar 2026, May 2026 (ongoing)."
  created_at: 2026-03-01T09:00:00
  validated_at: 2026-03-01T09:30:00
}
```

**Invariant#calmar-accumulation** — agent-discovery, 2 confirmations / 1 refutation

```
Invariant {
  id: "calmar-accumulation"
  title: "Calmar ratio > Sharpe as primary metric in accumulation"
  content: "In a Phase 1 accumulation portfolio, Calmar ratio is a more robust
            selection criterion than Sharpe ratio because it penalizes unrecoverable
            drawdowns proportional to return."
  description: "Sharpe penalizes all volatility equally, including upside. Calmar
                (annualized return / |max_drawdown|) directly measures recovery potential
                — critical when the investor has a long horizon but limited capacity to
                absorb a -35% event."
  example: "2008: best-Sharpe strategies had max_drawdown -35% to -50%; best-Calmar
            strategies had max_drawdown -8% to -15%, recovering 3× faster."
  topic: ["calmar","sharpe","drawdown","accumulation","risk"]
  durability: "permanent"
  source: "agent-discovery"
  status: "integrated"
  floor_weight: 0.05
  weight_initial: 0.20
  confirmation_count: 2
  infirmation_count: 1
  market_score: 0.667             ← 2/(2+1)
  recency_factor: 0.920           ← max(0.5, 0.5+0.5×exp(-63/365)); confronted 63d ago
  weight_effective: 0.123         ← max(0.20 × 0.667 × 0.920, 0.05) = 0.123
                                     1 refutation + time decay reduced weight
  last_confronted: 2026-03-15
  embedding: [768 floats]
  trace: "Strategies with best Sharpe had -35% drawdown in 2008/2022,
          unrecoverable in 3 years. Calmar > 1.5 = more robust filter
          for Phase 1 accumulation. One refutation: 2023 bull run where
          high-Sharpe strategies outperformed."
  created_at: 2026-03-01T09:00:00
  validated_at: 2026-03-01T09:30:00
}
```

**Invariant#tips-inflation-hedge** — Dalio corpus, 8 confirmations / 1 refutation

```
Invariant {
  id: "tips-inflation-hedge"
  title: "TIPS protect against persistent inflation"
  content: "TIPS (Treasury Inflation-Protected Securities) provide reliable protection
            against persistent CPI inflation by adjusting principal with CPI, preserving
            real purchasing power when nominal bonds lose value."
  description: "In regimes where inflation is both elevated (CPI > 2.5%) and sustained
                (speed > 0), nominal bonds lose real value while TIPS maintain purchasing
                power. Core to the Dalio All Weather framework."
  example: "2021-2022: TIP +2.3% while TLT -26%. In every inflationary episode
            since 1997 TIPS outperformed nominal Treasuries on real return."
  topic: ["tips","inflation","bonds","real-yield"]
  durability: "permanent"
  source: "corpus"
  author_weight: "dalio"
  status: "integrated"
  floor_weight: 0.40              ← dalio floor — never falls below this
  weight_initial: 0.85            ← high initial authority (Dalio)
  confirmation_count: 8
  infirmation_count: 1
  market_score: 0.889             ← 8/(8+1)
  recency_factor: 0.992           ← confronted 6 days ago
  weight_effective: 0.750         ← max(0.85 × 0.889 × 0.992, 0.40) = 0.750
                                     below initial: 1 refutation (2020 deflation)
  last_confronted: 2026-05-11
  embedding: [768 floats]
  trace: "Dalio All Weather principle — validated over 40 years of data.
          Only refutation: 2020 deflation (extreme case out of scope)."
  created_at: 2026-01-15T02:30:00
  validated_at: 2026-01-15T02:30:00  ← integrated at corpus ingestion time
}
```

---

## Step 4 — FAVORS edges and Portfolio ranking

### 4a — FAVORS edges (RegimeType → Strategy)

Updated after each weekly backtest cycle. Aggregates performance across ALL historical
Regime instances of this type (n_periods=8 = 8 distinct stagflation episodes in history).
These are **aggregated** numbers, distinct from any single backtest or current rolling metric.

Planner query:
```
MATCH (rt:RegimeType)<-[{regime_type_id}]-(r:Regime {is_current:true}),
      (rt)-[f:FAVORS]->(s:Strategy)
```

```
RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.65, sortino_rolling:1.09,
    calmar_rolling:1.7, max_drawdown:-7.2,
    n_periods:8, last_updated:2026-05-11]-> Strategy#custom-stagflation-v2

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

All 4 enabled portfolios ranked by USD rolling metrics (current period, not aggregated).
`gap_to_defender` is null for the defender; challengers show delta vs live portfolio.

```
portfolio_weekly_snapshot {
  date: 2026-05-12, live: true, framework_id: "4seasons"
  portfolio_id: "portfolio-a"
  designed_regime_type_id: "falling-growth-rising-inflation"
  primary_strategy_id: "custom-stagflation-v2"
  allocation: {GLD:20, TIP:30, DJP:15, cash:15, SPY:20}
  rank: 1
  sharpe_rolling: 0.71, sortino_rolling: 1.18, calmar_rolling: 1.9
  max_drawdown: -4.1, volatility: 0.08, total_return: 14.3
  gap_to_defender: null
  recommendation: "maintain"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Defender holds rank 1 — Calmar 1.9 best in class for Phase 1 accumulation"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, live: false, framework_id: "4seasons"
  portfolio_id: "portfolio-4seasons"
  designed_regime_type_id: null         ← framework-neutral; no DESIGNED_FOR edge
  primary_strategy_id: "4seasons"
  allocation: {VTI:30, TIP:15, GLD:7.5, TLT:15, cash:32.5}
  rank: 2
  sharpe_rolling: 0.61, sortino_rolling: 0.94, calmar_rolling: 1.4
  max_drawdown: -8.1, volatility: 0.10, total_return: 11.2
  gap_to_defender: {sharpe_delta:-0.10, sortino_delta:-0.24, calmar_delta:-0.5,
                    max_drawdown_delta:-4.0}
  recommendation: "monitor"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Standard 4 Seasons — solid but below defender; GLD underweighted for stagflation"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, live: false, framework_id: "4seasons"
  portfolio_id: "portfolio-permanent"
  designed_regime_type_id: null
  primary_strategy_id: "permanent"
  allocation: {GLD:25, TLT:25, BIL:25, VTI:25}
  rank: 3
  sharpe_rolling: 0.48, sortino_rolling: 0.72, calmar_rolling: 1.1
  max_drawdown: -9.4, volatility: 0.09, total_return: 9.1
  gap_to_defender: {sharpe_delta:-0.23, sortino_delta:-0.46, calmar_delta:-0.8,
                    max_drawdown_delta:-5.3}
  recommendation: "monitor"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Permanent portfolio — defensive but Calmar below threshold 1.5"
}

portfolio_weekly_snapshot {
  date: 2026-05-12, live: false, framework_id: "4seasons"
  portfolio_id: "portfolio-momentum"
  designed_regime_type_id: null
  primary_strategy_id: "momentum-macro"
  allocation: {QQQ:40, IEF:20, GLD:10, cash:30}
  rank: 4
  sharpe_rolling: 0.22, sortino_rolling: 0.31, calmar_rolling: 0.6
  max_drawdown: -18.2, volatility: 0.16, total_return: 6.2
  gap_to_defender: {sharpe_delta:-0.49, sortino_delta:-0.87, calmar_delta:-1.3,
                    max_drawdown_delta:-14.1}
  recommendation: "monitor"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Momentum-macro — poor stagflation fit; max_drawdown exceeds -15% rule"
}
```

Edges for the live portfolio:

```
Portfolio#a -[HOLDS primary:true weight:1.0 since:2026-03-01]-> Strategy#custom-stagflation-v2
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
  updated_at: 2026-05-11T06:48:00
}

Scenario#base {
  id: "sc-custom-base", name: "base"
  probability: 35, probability_d7: 55
  triggers: ["CPI 2.5-3%", "PMI 48-52"]
  target_allocation: {GLD:20, TIP:25, DJP:15, cash:15, SPY:25}
  currency: "USD"
  trace: "Base scenario probability collapsed from 55% to 35% — bear took the shift"
  updated_at: 2026-05-11T06:48:00
}

Scenario#bull {
  id: "sc-custom-bull", name: "bull"
  probability: 10, probability_d7: 25
  triggers: ["CPI < 2.5%", "PMI > 52"]
  target_allocation: {GLD:10, TIP:15, DJP:10, cash:5, SPY:60}
  currency: "USD"
  trace: "Bull scenario unlikely given CPI acceleration and PMI contraction"
  updated_at: 2026-05-11T06:48:00
}

Strategy#custom-stagflation-v2 -[HAS_SCENARIO active:true]-> Scenario#bear
Strategy#custom-stagflation-v2 -[HAS_SCENARIO active:true]-> Scenario#base
Strategy#custom-stagflation-v2 -[HAS_SCENARIO active:true]-> Scenario#bull

Signal#cpi -[GENERATES date:2026-05-11]-> Evaluation#eval-20260511
Signal#pmi -[GENERATES date:2026-05-11]-> Evaluation#eval-20260511

Evaluation {
  id: "eval-20260511"
  date: 2026-05-11
  verdict: "confirms"
  conviction_delta: +6
  reasoning: "CPI re-accelerates + PMI < 50 + VIX spike = exact trigger conditions
               of custom-stagflation. Conviction 74 → 80."
  trace: "3 convergent Tier 1 signals — strong confirmation"
  created_at: 2026-05-11T09:15:00
}

Evaluation#eval-20260511 -[UPDATES conviction_delta:+6 date:2026-05-11]->
  Strategy#custom-stagflation-v2
```

ScenarioProbability TS records the shift for UC8 to read:

```
ScenarioProbability { strategy_id:"custom-stagflation-v2", scenario:"bear",
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
                      Strategy custom-stagflation-v2 ranks 1st across all metrics."

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
      spec: {"metric": "calmar_rolling", "threshold": 1.5, "operator": "gt"}
      source: "agent-discovery", status: "proposed"
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
  content: "Calmar ratio above 1.5 is the optimal threshold for filtering out
            strategies susceptible to unrecoverable drawdowns in accumulation."
  description: "Derived by backtesting across 2008, 2020, 2022 crises. Threshold
                1.5 minimizes max_drawdown while preserving adequate return."
  example: "2022: strategies with Calmar 1.6+ had max_drawdown < 9%;
            strategies with Calmar 0.8 had max_drawdown -22%."
  topic: ["calmar","threshold","selection","drawdown"]
  durability: "long-term"
  source: "agent-discovery"
  status: "proposed"             ← not committed until user validates
  floor_weight: 0.05
  weight_initial: 0.15
  weight_effective: 0.15         ← max(0.15 × 1.0 × 1.0, 0.05); no confrontation yet
  confirmation_count: 0
  infirmation_count: 0
  market_score: 1.0              ← default until first confrontation
  recency_factor: 1.0            ← created today
  last_confronted: null
  embedding: [768 floats]
  trace: "Backtest analysis 2008/2020/2022: strategies with Calmar > 1.5 had
          max_drawdown < 10% vs < 20% for Calmar < 1. Pending user validation."
  created_at: 2026-05-12T09:20:00
  validated_at: null             ← set when user clicks [YES]
}
```

**Telegram to user:**
```
💡 Innovation agent:
   Calmar ratio optimal threshold = 1.5
   Analyzed across 3 crises (2008/2020/2022)
   Filters unrecoverable drawdowns better than Sharpe
   Current weight: 0.15 (not validated — source: agent-discovery)
   → Integrate as selection criterion? [YES] [NO]
```

User validates → `status: integrated`, `validated_at` set. `weight_effective` evolves
from first market confrontation via Backtest or Evaluation.

---

## Step 7 — Backtest validating the custom strategy

`Regime#stagflation-2021-2022` is a pre-existing instance seeded at UC0, representing
the 2021-2022 stagflation episode (CPI peaked 9.1% Jun 2022, PMI below 50 Q3-Q4 2022).

```
Regime {
  id: "regime-stagflation-2021-2022"       ← pre-existing seeded instance
  regime_type_id: "falling-growth-rising-inflation"
  tags: []
  date_start: 2021-03-01, date_end: 2022-12-31
  is_current: false, confidence: 91
  signals_count: 0                          ← seeded historically; no Signal vertices
  trace: "2021-2022 stagflation episode — CPI peaked 9.1% Jun 2022, PMI < 50
          from Aug 2022. Seeded from regime_history at UC0."
  created_at: 2026-01-01T00:00:00
}
```

```
Backtest {
  id: "bt-custom-stagflation-2021-2022"
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
  created_at: 2026-03-01T10:00:00
}

Strategy#custom-stagflation-v2 -[TESTED_IN is_primary:true]->
  Backtest#bt-custom-stagflation-2021-2022

Backtest#bt-custom-stagflation-2021-2022 -[IN_REGIME overlap_pct:0.91]->
  Regime#stagflation-2021-2022
  -- IN_REGIME points to the specific historical instance, not the type

-- FAVORS edge on RegimeType updated after backtest ingestion
-- (aggregated across all 8 historical periods, not equal to this single backtest):
RegimeType#falling-growth-rising-inflation
  -[FAVORS sharpe_rolling:0.65, sortino_rolling:1.09,
    calmar_rolling:1.7, max_drawdown:-7.2,
    n_periods:8, last_updated:2026-05-11]-> Strategy#custom-stagflation-v2
```

Three distinct numbers in play:
- **Backtest 2021-2022**: sharpe 0.68 / sortino 1.14 / calmar 1.8 — single historical episode
- **FAVORS (aggregated 8 periods)**: sharpe 0.65 / sortino 1.09 / calmar 1.7 — multi-period average
- **portfolio_weekly_snapshot (current 36M rolling)**: sharpe 0.71 / sortino 1.18 / calmar 1.9 — live portfolio today

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
   1. Portfolio A — 4S + Gold overlay : 1.18 ★ (live) Calmar 1.9
   2. Portfolio 4 Seasons standard    : 0.94          Calmar 1.4
   3. Portfolio Permanent             : 0.72          Calmar 1.1
   4. Portfolio Momentum Macro        : 0.31          Calmar 0.6 ⚠️

🔑 Key Invariants (effective weight):
   • GLD stagflation hedge : 0.248 (3/3 confirmed, near ceiling) [agent]
   • TIPS inflation         : 0.750 (8/9 confirmed)              [Dalio]
   • Calmar > 1.5 filter    : 0.123 (2/3 confirmed)              [agent]

ℹ️ Allocation note:
   Current GLD: 20% | Bear target: 35% (+15% gap)
   No proposal — defender holds rank 1. Review manually if you wish to
   align with bear target allocation.

📈 Metrics (USD): Sharpe 0.71 | Sortino 1.18 | Calmar 1.9
```

---

## Step 8B — Scenario B: challenger beats defender → Proposal

*Two weeks later: portfolio-4seasons edges past portfolio-a in the weekly ranking.
GLD pulls back 4% over the fortnight; standard 4 Seasons benefits from broader TLT
allocation as rate expectations shift.*

Updated portfolio_weekly_snapshot:

```
portfolio_weekly_snapshot {
  date: 2026-05-26, live: false, framework_id: "4seasons"
  portfolio_id: "portfolio-4seasons"
  designed_regime_type_id: null
  primary_strategy_id: "4seasons"
  allocation: {VTI:30, TIP:15, GLD:7.5, TLT:15, cash:32.5}
  rank: 1
  sharpe_rolling: 0.74, sortino_rolling: 1.22, calmar_rolling: 1.5
  max_drawdown: -7.8
  gap_to_defender: {sharpe_delta:+0.03, sortino_delta:+0.04, calmar_delta:-0.4,
                    max_drawdown_delta:-3.7}
  recommendation: "paper-test"
  market_context: {framework:"4seasons", regime_type_id:"falling-growth-rising-inflation",
                   confidence:78, global_liquidity:"tightening"}
  trace: "Challenger edges past defender — narrow gap, GLD pullback driven"
}

portfolio_weekly_snapshot {
  date: 2026-05-26, live: true, framework_id: "4seasons"
  portfolio_id: "portfolio-a"
  designed_regime_type_id: "falling-growth-rising-inflation"
  primary_strategy_id: "custom-stagflation-v2"
  allocation: {GLD:20, TIP:30, DJP:15, cash:15, SPY:20}
  rank: 2
  sharpe_rolling: 0.71, sortino_rolling: 1.18, calmar_rolling: 1.9
  max_drawdown: -4.1
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
  created_at: 2026-05-26T09:30:00
}
```

**Telegram to user:**
```
📊 Regime: Stagflation (78%)

🔄 Challenger alert — paper-test recommended:
   Rank 1: Portfolio 4 Seasons standard  Sortino 1.22  Calmar 1.5
   Rank 2: Portfolio A (live)            Sortino 1.18  Calmar 1.9 ★

   Gap: Sortino +0.04 | Sharpe +0.03 | Calmar -0.4 (challenger weaker on drawdown)

   Note: standard 4 Seasons has LOWER Calmar (1.5 vs 1.9) — the gap is narrow
   and driven by a 2-week GLD pullback. Monitor 4 weeks before switching.

→ [ACCEPT PAPER-TEST] [REJECT]
```

---

## Step 9 — Portfolio after user acceptance

User accepts paper-test → `user_response: "accepted"`, `paper_started` now set.
V1 does NOT auto-apply. Agent records acceptance and begins paper-tracking the challenger.
Live portfolio unchanged until user manually rebalances.

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
  live: true, enabled: true
  currency: "CHF"
  benchmark: "SPY"
  phase: "accumulation"
  allocation: {VTI:30, TIP:15, GLD:7.5, TLT:15, cash:32.5}  ← post-rebalance
  max_drawdown_rule: -15.0
  max_single_asset_pct: 40.0
  fx_usd_exposure: 55
  sharpe_rolling: 0.74, sortino_rolling: 1.22, calmar_rolling: 1.5
  max_drawdown: -7.8, volatility: 0.10, total_return: 11.2
  date_revised: 2026-05-26
  trace: "User accepted paper-test and manually rebalanced to 4 Seasons standard.
          Custom gold overlay suspended pending GLD recovery."
  updated_at: 2026-05-26T10:00:00
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
  created_at: 2026-01-15T02:30:00
}

Passage#pass-dalio-tips-142 -[SUPPORTS strength:0.90
  excerpt:"TIPS reliable inflation protection in every inflationary episode"]->
  Invariant#tips-inflation-hedge
```

---

## Summary — All entities involved

| Entity | Instances | Role in this cycle |
|--------|-----------|-------------------|
| Signal | 3 | Qualitative Tier 1 input |
| RegimeType | 1 | Static type — hosts FAVORS and DESIGNED_FOR |
| Regime | 2 | Current (2026-05) + historical (2021-2022) via IN_REGIME |
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

**Three distinct metric contexts — never to be confused:**
- FAVORS edge: aggregated across n_periods historical regime instances
- Backtest vertex: single historical episode (fixed date range)
- portfolio_weekly_snapshot: current rolling window (252d / 36M)

**Edges active:** IMPLIES (→Regime), GENERATES, UPDATES,
FAVORS (RegimeType→Strategy), HAS_SCENARIO, BACKED_BY, TESTED_IN,
IN_REGIME (→Regime), HOLDS, DESIGNED_FOR (→RegimeType), CONTAINS, SUPPORTS.
**Unused:** MODIFIES (V2 only).

**Note:** Adaptation vertex is V2-only. V1 paper-mode uses Proposal.
Scenario A (no Proposal) and Scenario B (Proposal) cover the two distinct UC8 outcomes.
