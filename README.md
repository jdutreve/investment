# Investment Agent — V1 (MVP)

A local, single-user agent that builds retirement capital (Phase 1:
accumulation). It detects the macro **regime** (Dalio 4 Seasons), **ranks**
concrete ETF portfolios by USD risk-adjusted indicators, maintains a living
base of **invariants** (investment principles extracted from books, events
and its own market discoveries), and emits weekly **paper-mode proposals**
(switch defender / reallocate) — measured against reality 12 weeks later.
Rule #1: don't lose. Rule #2: don't forget rule #1.

**V1 never executes a trade.** It ranks, explains, proposes, measures
itself, and improves — application is always the owner's manual decision.

## How it decides

```
mechanical decides · LLM proposes · the user arbitrates · reality judges
```

- **Mechanical core (no LLM):** market data (FRED first-release vintages,
  publication-dated), regime detection with print-based hysteresis, NAV +
  Sharpe/Sortino/Calmar (pinned formulas), backtests → FAVORS, proposal
  gates, outcome verdicts at +12 weeks. Fully replayable — a **25-year
  point-in-time shadow replay gates go-live**.
- **Three LLM roles:** the **Planner** (Qwen, guardrail) assembles the
  optimal context and catches hallucinations; the **Worker** (Sonnet)
  interprets and proposes (reallocations, new/revised strategies, new
  invariants); the **curator** (Sonnet) turns deposited books and
  watched events into invariant candidates — dedup-gated, quality-contracted,
  user-validated.
- **Unified improvement cycle:** every resource (proposal, invariant,
  strategy, scenario probabilities, thresholds) follows
  *measure → propose → validate → mature → adopt or reject*.
  Week-over-week improvement is measured on a scoreboard, never asserted.

## Runtime

Runs entirely on a MacBook (ADR-002): **one Python process**, one **SQLite**
file (ADR-004), no daemon, no server, no cloud but the LLM APIs. No clock
cron: an inbox **watcher** ingests deposits ~5 min after they land, and the
weekly Monday chain is **due-on-start** (runs at wake/launch if overdue).

**Interfaces** (three fronts, one audited command layer — ADR-005):
Telegram (weekly digest, alerts, chat, validations) · `invest` CLI ·
local web dashboard (`http://127.0.0.1:8765`, read-only SQL console,
semantic search).

## Documentation map

| File | Role |
|---|---|
| `CLAUDE.md` | Entry point — rules, schedules, stack (read first) |
| `REVISION_NOTES.md` | V1 scope, core concepts, ranking rule |
| `investment-ARCHITECTURE.md` | Regime algorithm, Planner/Worker cycle, improvement cycle |
| `DATA_MODELS.md` | Schema (13 entities, relations, TS), formulas, weights |
| `USE_CASES.md` | UC0–UC9, gates, Event Watch |
| `investment-TASKS.md` | Full build spec, phase by phase |
| `MILESTONES.md` | **Execution order** — 11 owner-verifiable increments, 3 STOP points |
| `DECISIONS.md` | ADRs (SQLite, local Mac, vintage discipline, ops layer) |
| `IMPROVEMENTS.md` | Deferred features (I-N), triggers to revisit |
| `EXAMPLE.md` | One full cycle traced end-to-end (stagflation 2026) |

## Getting started

Follow `MILESTONES.md`: M0 (smoke test) → M6 delivers the whole mechanical
core **and the 25y replay evidence with zero LLM spend**; M7+ adds the
knowledge factory and the weekly Worker cycle. Each milestone ends with a
*Definition of Verified* — commands the owner runs and facts the owner can
dispute.

**Status: specifications complete and dry-run validated; implementation
not started.**
