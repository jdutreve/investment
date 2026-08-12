# Investment Agent — V1 (MVP)

A local, single-user agent that builds retirement capital (Phase 1:
accumulation). It reads the macro **regime**, allocates across concrete ETF
books, maintains a living base of **invariants** (investment principles
extracted from books, events and its own market discoveries), and emits
**paper-mode proposals** — measured against reality 12 weeks later.
Rule #1: don't lose. Rule #2: don't forget rule #1.

**V1 never executes a trade.** It ranks, explains, proposes, measures itself,
and improves — application is always the owner's manual decision.

## The adopted strategy (ADR-007) — read this before the other docs

V1 pivoted on 2026-07-20. The allocation decision is the **market-signal
monthly countercyclical stack**: a market-priced credit-spread/slope regime
picks one of 3 concentrated books, a trend-following overlay guards the downside,
and the binding caps dispose. It decides **monthly**, not weekly.

The **Dalio 4-quadrant portfolio ranking** — the defender/challenger duel, the
scenario-driven weekly reallocation, the 0.4/0.6 blend — is **not the live
allocation path**. It is the **retained bridge**: fallback, benchmark, and the
framework-agnostic knowledge factory. It is not deleted until forward
paper-mode earns the switch.

This distinction is the one thing to get right before writing code here. Much
of `docs/TASKS.md`, `docs/ARCHITECTURE.md` and `docs/USE_CASES.md` was written
before the pivot and still describes the bridge in full detail — accurately,
but as the *bridge*, not as the live path. **`docs/V1_STRATEGY.md` is the
authority on which is which**, and every section describing the superseded live
path carries an inline marker pointing back to it.

## How it decides

```
mechanical decides · LLM proposes · reality judges · the owner executes
```

- **Mechanical core (no LLM):** market data (FRED first-release vintages,
  publication-dated), regime detection with print-based hysteresis, NAV +
  Sharpe/Sortino/Calmar (pinned formulas), backtests → FAVORS, the monthly
  market-signal decision, proposal gates, outcome verdicts at +12 weeks. Fully
  replayable — a **35-year point-in-time shadow replay gates go-live**.
- **Mechanical allocation is sovereign (ADR-011):** the Worker *reads* the
  monthly decision and contributes a qualitative reading; it may not cancel it,
  delay it, re-pick the book or adjust its weights. A disagreement with the
  *rule* goes through `innovations_proposed`, where ADR-006's maturation
  measures it.
- **Three LLM roles:** the **Planner** (guardrail) assembles the context and
  catches hallucinations; the **Worker** (Sonnet) interprets and proposes
  (reallocations on the bridge, new/revised strategies, new invariants); the
  **curator** turns deposited books and watched events into invariant
  candidates — dedup-gated, quality-contracted, matured mechanically over 35y
  (no user gate — ADR-006).
- **Unified improvement cycle:** every resource (proposal, invariant, strategy,
  scenario probabilities, thresholds) follows
  *measure → propose → mature → adopt or reject* — fully mechanical.
  Week-over-week improvement is measured on a scoreboard, never asserted.

## Runtime

Runs entirely on a MacBook (ADR-002): **one Python process**, one **SQLite**
file (ADR-004), no daemon, no server, no cloud but the LLM APIs. No clock
cron: an inbox **watcher** ingests deposits ~5 min after they land, and the
weekly Monday chain is **due-on-start** (runs at wake/launch if overdue). Both
LLM roles route through OpenRouter (owner decision 2026-07-21).

**Interfaces** (three fronts, one audited command layer — ADR-005):
Telegram (weekly digest, alerts, chat, preference overrides) · `invest` CLI ·
local web dashboard (`http://127.0.0.1:8765`, read-only SQL console,
semantic search).

## Documentation map

| File | Role |
|---|---|
| `CLAUDE.md` | Entry point — rules, schedules, stack (read first) |
| `docs/V1_STRATEGY.md` | **The ADOPTED strategy (ADR-007)** — read with MILESTONES |
| `docs/MILESTONES.md` | **Execution order** — 13 owner-verifiable increments, STOP points |
| `docs/REVISION_NOTES.md` | V1 scope, core concepts, ranking rule |
| `docs/ARCHITECTURE.md` | Regime algorithm, Planner/Worker cycle, improvement cycle |
| `docs/DATA_MODELS.md` | Schema (13 entities, relations, TS), formulas, weights |
| `docs/USE_CASES.md` | UC0–UC9, gates, Event Watch |
| `docs/TASKS.md` | Full build spec, phase by phase |
| `docs/DECISIONS.md` | ADRs (SQLite, local Mac, vintage discipline, ops layer) |
| `docs/IMPROVEMENTS.md` | Deferred features (I-N), triggers to revisit |
| `docs/EXAMPLE.md` | One full cycle traced end-to-end (stagflation 2026) |

## Getting started

Follow `docs/MILESTONES.md`: M0 (smoke test) → M6 delivers the whole mechanical
core **and the 35y replay evidence with zero LLM spend**; M6-bis wires the
adopted monthly stack; M7+ adds the knowledge factory and the Worker cycle.
Each milestone ends with a *Definition of Verified* — commands the owner runs
and facts the owner can dispute.

## Status

The **mechanical half runs**: seed, market backfill, regime materialization
(35y), NAV + indicators, ranking, backtests → FAVORS, the 35y replay, the
market-signal stack, outcomes, alerts and the digest. `docs/MILESTONES.md`
carries the per-increment record.

The **cognitive half is built but has never run on real data**: the live
EventLog holds no `ProposalEvent`, `EvaluationEvent`, `InnovationEvent` or
`MarketSignalDecisionEvent`. Treat the first live UC8 cycle as an untested path
— it is the one place where code correctness has not yet been confronted with
reality.
