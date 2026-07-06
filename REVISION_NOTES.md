# REVISION_NOTES.md — Investment Agent V1 Revision

Shared header for all project docs. Read this once; the other files reference it.

## MVP V1 scope

V1 is a **portfolio ranking + digest + paper-mode proposal engine**.
V1 is **not** auto-adaptive. Real execution, V2 auto-validation, and automatic
learning from real allocations are deferred to V2 (see IMPROVEMENTS.md).

V1 emits two kinds of paper-mode proposals (see USE_CASES.md UC8):
- **switch** — replace the defender with a challenger portfolio from the
  ranked universe (closed decision space, mechanical gates).
- **reallocation** — adjust the defender's own allocation (Worker-proposed
  from the scenario/FAVORS delta blend, mechanically validated by Writeback).
Both are recommendations only; the user applies changes manually in V1.

## Unified improvement cycle

Every improvable resource (Proposal, Invariant, Strategy, scenario
probabilities, thresholds) follows the same loop: **measure current
performance → propose → user gate where required → mechanical maturation
window → adopt or reject**. Proposals get an outcome verdict at +12 weeks
(won/lost vs the incumbent, feeding invariant confrontations
`source='proposal'`); new/revised strategies run a 12-week probation;
scenario probabilities are calibration-scored; thresholds are calibrated by
the Phase 9 walk-forward replay. The weekly digest scoreboard renders these
measurements — week-over-week improvement is measured, not asserted. Spec
in ARCHITECTURE.md "Unified improvement cycle".

## Core concepts

- **Framework** — lens used to interpret markets and design/refine strategies and
  portfolios. Vertex in V1: `Framework`. Single framework seeded: `4seasons`.
  `Strategy.framework_id` means "lens under which the strategy is evaluated in
  V1", not the strategy's intellectual origin.
- **Regime** — macro state detected inside a framework, using level, speed, and
  acceleration of indicators. Stagflation is a regime (alias of
  `falling-growth-rising-inflation`). Deflation is a tag, never a regime.
  The growth axis is `GROWTH_COMPOSITE` (FRED-native: z(INDPRO YoY) −
  z(UNRATE Δ3m), rebased to index 100) — chosen over ISM PMI because it is
  automatic, free and perennial (see ARCHITECTURE.md).
- **Strategy** — investment thesis or concept (vertex `Strategy`). Seeded ids:
  `four-seasons-rp`, `permanent-browne`, `barbell-taleb`, `momentum-macro`
  (distinct from Framework ids to avoid name collisions).
- **Portfolio** — concrete ETF allocation; the ranking unit. May have
  `defender=true` (exactly one at a time).

## Ranking rule

All `Portfolio` rows with `enabled=true` are ranked together. The current regime
and global liquidity state are **context**, not filters. The defender
(`defender=true`) is ranked alongside challengers, never excluded, never privileged.
Primary sort: `sortino_rolling` DESC; tie-break (within 0.02): `calmar_rolling`
DESC; final tie-break: `max_drawdown` (less negative wins). Portfolios with
`calmar_rolling < 1.0` are demoted to the bottom. A portfolio whose
`max_drawdown` breaches the user rule (-15%) stays in the ranking but is
excluded from the defender role and from proposal candidacy.

## Risk rules precedence

`user_profile` rules (`max_drawdown_pct`, `max_single_asset_pct`) are the
**binding** constraints for the defender role and for proposal candidacy
(both switch and reallocation). Per-portfolio `max_drawdown_rule` /
`max_single_asset_pct` may only be **stricter** than the user rules, never
looser; the seed must comply. Writeback enforces the stricter of the two.

## Mandatory indicators

USD `sharpe_rolling`, `sortino_rolling`, `calmar_rolling` (36M window),
`max_drawdown`, `volatility`, plus cumulative `return_3m / 6m / 1y / 3y / 5y`,
plus defender-vs-challenger gap. Every ranked portfolio must expose its concrete
allocation. Indicator (never "ratio") is the canonical generic term.
Drawdown/volatility/return values are stored as decimal fractions
(-0.062 = -6.2%); percent formatting happens only at display
(see DATA_MODELS.md units convention). All indicator formulas are pinned in
DATA_MODELS.md "Calculation conventions" — two implementations must produce
the same numbers.

## Global liquidity

First-class MarketData family (`asset_class=GLOBAL_LIQUIDITY`). Combined with
the 4-Seasons regime view to explain rankings and proposals. Never a regime
by itself. Composite rebased to index 100 (>100 easing, <100 tightening) —
see DATA_MODELS.md.

## Audit log

The `EventLog` append-only vertex type is
the audit spine: **every UC side-effect is appended to EventLog before the
corresponding vertex/edge commit**. History backfill = 25 years for macro
series (ETF prices limited by inception dates), so historical Regime instances
and FAVORS aggregation are meaningful from day one.

## Language

English is the sole language of the project: code, comments, docs, commits,
identifiers.

## V2 boundary

V2 starts only after V1 paper-mode history demonstrates that challenger
recommendations would have beaten the defender net of costs and risk
over at least 3 months. The **Phase 9 shadow replay** (25y meta-backtest of
the mechanical pipeline, point-in-time, net of costs) provides the initial
evidence and gates go-live; the 3 months of real paper-mode (measured by
the weekly `outcomes.py` scoreboard) then confirm it forward before V2.
