# REVISION_NOTES.md — Investment Agent V1 Revision

Shared header for all project docs. Read this once; the other files reference it.

## MVP V1 scope

V1 is a **portfolio ranking + digest + paper-mode proposal engine**.
V1 is **not** auto-adaptive. Real execution, V2 auto-validation, and automatic
learning from real allocations are deferred to V2 (see IMPROVEMENTS.md).

## Core concepts

- **Framework** — lens used to interpret markets and design/refine strategies and
  portfolios. Vertex in V1: `Framework`. Single framework seeded: `4seasons`.
- **Regime** — macro state detected inside a framework, using level, speed, and
  acceleration of indicators. Stagflation is a regime (alias of
  `falling-growth-rising-inflation`). Deflation is a tag, never a regime.
- **Strategy** — investment thesis or concept (vertex `Strategy`).
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

## Mandatory indicators

USD `sharpe_rolling`, `sortino_rolling`, `calmar_rolling` (36M window),
`max_drawdown`, `volatility`, plus cumulative `return_3m / 6m / 1y / 3y / 5y`,
plus defender-vs-challenger gap. Every ranked portfolio must expose its concrete
allocation. Indicator (never "ratio") is the canonical generic term.
Drawdown/volatility/return values are stored as decimal fractions
(-0.062 = -6.2%); percent formatting happens only at display
(see DATA_MODELS.md units convention).

## Global liquidity

First-class MarketData family (`asset_class=GLOBAL_LIQUIDITY`). Combined with
the 4-Seasons regime view to explain rankings and proposals. Never a regime
by itself.

## Language

English is the sole language of the project: code, comments, docs, commits,
identifiers.

## V2 boundary

V2 starts only after V1 paper-mode history demonstrates that challenger
recommendations would have beaten the defender net of costs and risk
over at least 3 months.
