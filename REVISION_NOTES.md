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
- **Portfolio** — concrete ETF allocation; the ranking unit. May have `live=true`
  (the defender).

## Ranking rule

All `Portfolio` rows with `enabled=true` are ranked together. The current regime
and global liquidity state are **context**, not filters. The live defender is
ranked alongside challengers, never excluded, never privileged.

## Mandatory metrics

USD `sharpe_rolling`, `sortino_rolling`, `calmar_rolling` (36M window),
`max_drawdown`, `volatility`, `total_return`, plus live-vs-challenger gap.
Every ranked portfolio must expose its concrete allocation.

## Global liquidity

First-class MarketData family (`asset_class=GLOBAL_LIQUIDITY`). Combined with
the 4-Seasons regime view to explain rankings and proposals. Never a regime
by itself.

## Language

English is the sole language of the project: code, comments, docs, commits,
identifiers.

## V2 boundary

V2 starts only after V1 paper-mode history demonstrates that challenger
recommendations would have beaten the live defender net of costs and risk
over at least 3 months.
