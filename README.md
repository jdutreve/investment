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
  replayable over 35 years of point-in-time vintages — which is how a strategy
  earns its numbers. **Go-live is gated on forward paper-mode** (ADR-007 §5),
  not on that replay: ADR-013 removed the replay gate, whose predicate was the
  agent's proposals beating the defender, once ADR-012 removed the agent's
  proposals from the allocation path entirely.
- **Mechanical allocation is sovereign (ADR-012, subsuming ADR-011):** the
  Worker *reads* the monthly decision and contributes a qualitative reading; it
  may not cancel it, delay it, re-pick the book or adjust its weights — and
  proposes no allocation at all, for any book. A disagreement with the *rule*
  goes through `innovations_proposed`, where ADR-006's maturation measures it.
- **Three LLM roles:** the **Planner** (guardrail) assembles the context and
  catches hallucinations; the **Worker** interprets and proposes (new/revised
  strategies, new invariants, process critiques — never an allocation); the
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
weekly Sunday chain is **due-on-start** (runs at wake/launch if overdue). Both
LLM roles route through OpenRouter (owner decision 2026-07-21).

**Interfaces** (three fronts, one audited command layer — ADR-005). Two are
built: Telegram (weekly digest, alerts, chat, preference overrides) and the
`invest` CLI (`status`, `ranking`, `nav`, `regime`, `regime-audit`,
`invariants`, `sql`). The local web dashboard (`127.0.0.1:8765`) is **M10 and
not yet built** — `ops/commands.py` and the run-lock it needs exist since M9.

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

## Running it

```bash
uv run python -m investment.seed     # UC0, safe to re-run
uv run python -m investment.main     # the agent, in the foreground
```

The agent serves the **dashboard** at http://127.0.0.1:8765 alongside the
Telegram bot and the scheduler. Its React front is built once, and only when
the source changes:

```bash
cd src/investment/ops/dashboard && npm install && npm run build
```

`node_modules` and `dist/` are gitignored (build-time only, 128MB of
dependencies for 16 source files), so a fresh clone runs the two commands
above. Skipping them costs only the browser front: the agent, the API, the
Telegram bot and `invest` all work without it, and the page itself answers with
the exact build command until it exists.

`deploy/` carries the LaunchAgent and the install/inspect/uninstall commands.
**The first start is not a dry run** — the weekly chain is due on a database
that has never run one, so it decides the month's allocation and emits the
stack's opening entry. Watch it once in the foreground first.

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

The **agent now runs itself**: one process holds the inbox watcher, the Sunday
cron, the wake heartbeat and the Telegram bot; the weekly chain is complete
(catch-up → event watch → curation → the mechanical block → the monthly
allocation decision → UC8 → digest); every front dispatches to one command
layer.

The **cognitive half is built but has never run on real data**: the live
EventLog holds no `ProposalEvent`, `EvaluationEvent`, `InnovationEvent` or
`MarketSignalDecisionEvent`. Treat the first live UC8 cycle as an untested path
— it is the one place where code correctness has not yet been confronted with
reality.
