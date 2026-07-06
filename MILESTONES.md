# MILESTONES.md — Incremental implementation plan

Execution-order view of TASKS.md, sliced so the OWNER can
challenge and verify each increment before the next starts. This is a
STEERING document: check boxes, strike milestones, add findings — the
specs stay in the other files.

**Ordering principles:**
1. **Mechanical before LLM** — and the 25y replay BEFORE any Planner/
   Worker wiring: the replay only needs the mechanical pipeline, and it is
   the evidence that validates (or kills) the thresholds and the premise.
2. **Each milestone ships its own inspection view** (CLI/dashboard) — the
   owner's verification instrument arrives with the data it inspects.
3. **Each milestone has a Definition of Verified**: commands the owner
   types and facts the owner can dispute — never just "tests pass".

Rhythm: one commit per milestone. Explicit STOP points at M3, M6, M7 —
where owner judgment is the acceptance criterion (the three places the
system can be technically correct and substantively wrong).

**Incremental seed:** `python -m investment.seed` is idempotent and is
RE-RUN at M1/M2/M3/M4/M7 — each run completes the UC0 steps whose
prerequisites now exist and SKIPS the rest with a warning (M1: static
steps 1-5,7,8; M2 adds 9; M3 adds 10; M4 adds 11-13; M7 adds 6/6b). The
closing SeedEvent inventory reflects what ran.

---

## M0 — Foundation + smoke test (0.5 d — Phase 0)

brew, dirs, `.env`, uv, `spike_sqlite.py` (Task 0.5).

Note: Task 0.7 (launchd) is only WRITTEN here — the LaunchAgent is
loaded at M9, when an agent worth running exists.

**Definition of Verified**
- [ ] smoke test: schema persists after reopen
- [ ] atomic rollback of (event_log append + entity insert)
- [ ] 200k TS rows < 2 min; 756-row range read < 50 ms
- [ ] 1k embeddings → cosine top-20 < 10 ms

---

## M1 — Schema + wrapper + static seed (1 d — Phase 1, 1ter partial)

Tables, `InvestmentDB`, seed data (6 invariants, 4 strategies, 12
scenarios, 7 portfolios) + **minimal `invest sql` / `invest status`**.

**Definition of Verified**
- [ ] `invest sql "SELECT id, weight_initial, floor_weight FROM invariant"`
- [ ] re-run seed → zero duplicates, 2 SeedEvents (partial inventory —
      static steps only at this stage)
- [ ] counts: 13 entity / 5 M:N / 3 TS / 10 doc tables

**⚔️ Challenge point:** the seeds ARE your investment philosophy encoded —
reread the 6 invariants, 4 strategy conditions, 7 allocations line by line.

---

## M2 — Market data pipeline (1.5 d — Phase 2 partial)

Yahoo+FRED fetcher, ALFRED first-release vintages, publication dating,
transforms, composites, 25y backfill.

**Definition of Verified**
- [ ] CPI YoY at dates you know, via `invest sql`
- [ ] GROWTH_COMPOSITE through 2008 and 2020 tells the story you know
- [ ] publication dates spot-checked against the real BLS/Fed calendar
- [ ] GLOBAL_LIQUIDITY: QE/QT episodes visible

**⚔️ Challenge:** does the growth composite match your macro memory?

---

## M3 — Regime detector + 25y materialization (1 d — Phase 2 end) — STOP POINT

Per-print `step()`, hysteresis, `detector_state`, historical episodes.

**Definition of Verified**
- [ ] `invest regime --history`: 2008 falling-growth, 2021-22 stagflation,
      plausible transition dates, ≥10 episodes
- [ ] flip-flop fixture does not switch before 2 concordant prints

**⚔️ STOP:** every episode is a historical fact you can dispute. Do not
proceed until the regime history reads true.

---

## M4 — NAV engine + indicators + first ranking (1.5 d — Phase 5bis partial)

Pinned conventions, snapshot, ranking + **CLI views** (`invest ranking`,
`invest nav <id>` terminal sparkline — the dashboard pages come at M10).

**Definition of Verified**
- [ ] golden numbers vs an external source (SPY Sharpe on the window,
      60/40 NAV vs Portfolio Visualizer, within tolerance)
- [ ] `test_nav_conventions_golden` green
- [ ] first snapshot: defender ranked, gaps computed

---

## M5 — Backtests + FAVORS + mechanical confrontations (1 d)

**Definition of Verified**
- [ ] FAVORS matrix regime × strategy is plausible (barbell favored under
      stress? risk parity in disinflation?)
- [ ] confrontation fixture moves a weight_effective as computed by hand

---

## M6 — 🎯 Shadow replay + calibration (1.5 d — Phase 9, PULLED FORWARD) — STOP POINT

The mechanical pipeline is complete: replay it over 25y.

**Definition of Verified**
- [ ] replay_report: hit-rate, agent-follow vs hold-defender net of costs
- [ ] vintage_mode=first_release; vintage sensitivity reported
- [ ] walk-forward calibrated thresholds (15y/10y split) — confirmation
      of the winning set happens in the CLI (Telegram arrives at M9)
- [ ] zero PIT assertions failed

**⚔️ STOP — the premise gate:** if the replay shows no net value-add, we
discuss BEFORE paying for the LLM wiring. This evidence also decides the
final gate thresholds.

---

## M7 — Corpus + invariant factory (2 d — Phases 1bis, 3, curation) — STOP POINT

In-process embeddings, ingester, watcher, curator + dedup gate +
consolidation + quality contract + mechanical maturation (no user gate —
ADR-006). Includes the KNOWLEDGE SLICE of Writeback (EventLog-first
persistence of candidates + the dedup gate) — the decision slice of Writeback
comes at M8.

**Definition of Verified**
- [ ] deposit the Dalio book → HOW MANY candidates, of WHAT quality?
- [ ] dedup gate: a near-duplicate candidate becomes a curation
- [ ] consolidation: multi-batch dupes merged, none silently dropped
- [ ] SUPPORTS links land on seeded invariants

**⚔️ STOP — the qualitative core:** you INSPECT the real candidates (a
build-time sanity read, not a runtime gate — ADR-006); the
candidates/principles ratio tells whether the factory converges. The quality
contract faces reality here.

---

## M8 — Planner + Worker + gates + first full chain (2 d — Phases 4, 5, 6)

Baseline + 1a/1b + Worker + Call 2 guardrail + Writeback gates,
`outcomes.py` (proposal verdicts, calibration, probation — fixture-tested
now, armed by real time at M11) + scoreboard render, full Monday chain on
fixtures (UC3 event watch not built yet → the chain SKIPS it until M9),
digest rendered in terminal.

**Definition of Verified**
- [ ] simulated Monday on fixtures end to end
- [ ] bear-shift fixture (+35pts) → reallocation proposal passes gates
- [ ] Call 2 downgrades an unevidenced verdict to neutral (fixture)
- [ ] digest readable and complete

**⚔️ Challenge:** Worker reasoning quality; are the 12-15 selected
invariants the right ones?

---

## M9 — Telegram + Event Watch + real-life scheduling (1.5 d — Phases 6bis, 3.2, 7 + ops core)

Includes `ops/commands.py` (the command layer core — the bot's buttons
dispatch to it) and the RUN-LOCK (the real-life week can collide the
Monday chain with an ad-hoc UC8). M10 keeps the API/dashboard/token/
idempotency/async-jobs hardening.

**Definition of Verified**
- [ ] one week of real operation on the Mac
- [ ] deposit → candidates on Telegram within ~5 min
- [ ] lid closed Monday → wake → due-on-start chain runs once
- [ ] a Fed press item → triaged event document (or discarded as routine)

---

## M10 — Full ops + hardening (1 d — Phase 6ter)

Dashboard 8 pages, `invest` full CLI over the API, X-Ops-Token,
idempotency, async jobs (commands.py and run-lock exist since M9).

**Definition of Verified**
- [ ] cross-front equivalence tests green (bot vs dashboard vs CLI)
- [ ] API without token → 403; `invest` offline matrix behaves
- [ ] daily-use comfort: YOUR verdict after a week

---

## M11 — Outcome loop in steady state (no new code — armed by time)

Verdicts at +12 weeks, scoreboard, probation, calibration — fixture-tested
in M8, verified on real history as it accumulates.

**Definition of Verified**
- [ ] first real outcome verdict matches a hand computation
- [ ] scoreboard renders in digest and dashboard

---

**Total: ~14.5 days.** After M11: 3 months of paper-mode history →
the V2 boundary discussion (REVISION_NOTES).
