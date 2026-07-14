# MILESTONES.md — Incremental implementation plan

Execution-order view of TASKS.md, sliced so the OWNER can
challenge and verify each increment before the next starts. This is a
STEERING document: check boxes, strike milestones, add findings — the
specs stay in the other files.

**Ordering principles:**
1. **Mechanical before LLM** — and the 35y replay BEFORE any Planner/
   Worker wiring: the replay only needs the mechanical pipeline, and it is
   the evidence that validates (or kills) the thresholds and the premise.
2. **Each milestone ships its own inspection view** (CLI/dashboard) — the
   owner's verification instrument arrives with the data it inspects.
3. **Each milestone has a Definition of Verified**: commands the owner
   types and facts the owner can dispute — never just "tests pass".

Rhythm: one commit per milestone. Explicit STOP points at M3, M6, M7, M8b —
where owner judgment is the acceptance criterion (the places the system can
be technically correct and substantively wrong). M6 and M8b are the two
pre-go-live premise gates: M6 (mechanical core beats All Weather, PIT) and
M8b (best-case full system beats All Weather, semi-PIT).

**Incremental seed:** `python -m investment.seed` is idempotent and is
RE-RUN at M1/M2/M3/M4/M5/M7 — each run completes the UC0 steps whose
prerequisites now exist and SKIPS the rest with a warning (M1: static
steps 1-5,7,8 — seed invariants carry their `condition`/`effect` but are
not yet matured; M2 adds 9; M3 adds 10; M4 adds 12-13; M5 adds 10b
(benchmark_valuation) + 11 (backtests/FAVORS) + 11b (birth maturation of
the seed invariants over 35y) + 11c (scenario probability warm-start over
35y); M7 adds 6/6b (corpus invariants, matured the same way)). The closing
SeedEvent inventory reflects what ran.

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
reread the 6 invariants (each now a `condition` → `effect`/method, machine-
readable), 4 strategy conditions, 7 allocations line by line. Note: these
invariants face the SAME 35y maturation at M5 — belief does not grant
`integrated` status, history does (ADR-006).

---

## M2 — Market data pipeline (1.5 d — Phase 2 partial)

Yahoo+FRED fetcher, ALFRED first-release vintages, publication dating,
transforms, composites, 35y macro backfill + HISTORY_PROXIES splice.

**Definition of Verified**
- [ ] CPI YoY at dates you know, via `invest sql`
- [ ] GROWTH_COMPOSITE through 2008 and 2020 tells the story you know
- [ ] publication dates spot-checked against the real BLS/Fed calendar
- [ ] GLOBAL_LIQUIDITY: QE/QT episodes visible (from ~2002, WALCL)
- [ ] HISTORY_PROXIES resolve (as SHIPPED — db/seed_data.py: Yahoo VFINX/
      VUSTX/VFITX/VFISX/VIPSX/FDIVX + ^BCOM commodities; LBMA gold feed. The
      pre-build plan's GOLDAMGBD228NLBM/SPGSCITR/VBMFX guesses were corrected
      live at M2; BIL's cash sleeve became the synthetic 'cash' asset, not an
      ETF splice); report the ACTUAL tradable floor (target 1991)
- [ ] splice ARTIFACT gate (#3): each proxy/ETF overlap has return-corr ≥ 0.95
      and no >3σ gap at the join — `test_splice_continuity` green; no join spike

**⚔️ Challenge:** does the growth composite match your macro memory?

---

## M3 — Regime detector + 35y materialization (1 d — Phase 2 end) — STOP POINT

Per-print `step()`, hysteresis, `detector_state`, historical episodes.

**Definition of Verified**
- [ ] `invest regime --history`: 1994 rate shock, 2000 dot-com, 2008
      falling-growth, 2021-22 stagflation, plausible transition dates,
      ≥12 episodes (the 35y window adds the 90s)
- [ ] flip-flop fixture does not switch before 2 concordant prints
- [ ] STABILITY AUDIT (#4) over 35y: whipsaw count (episodes reversed within
      3 months), median episode length, detector lag (start_date → created_at
      confirmation), and how many candidate switches the 2-print hysteresis
      suppressed — all reported; whipsaws are rare and lag is bounded

**⚔️ STOP:** every episode is a historical fact you can dispute. Do not
proceed until the regime history reads true.

---

## M4 — NAV engine + indicators + first ranking (1.5 d — Phase 5bis partial)

Pinned conventions, snapshot, ranking + **CLI views** (`invest ranking`,
`invest nav <id>` terminal sparkline — the dashboard pages come at M10).

**Definition of Verified**
- [x] golden numbers vs an external source (SPY Sharpe on the window,
      All Weather NAV vs Portfolio Visualizer, within tolerance — also
      validates the ALL_WEATHER_BENCHMARK everything is compared to)
      — done against PV itself, Jan 2017-Dec 2025, SAME tickers/weights, PV
      `rebalanceType=monthly` + dividends reinvested (method below). All
      Weather: CAGR 6.05 vs 6.08%, stdev 9.29 vs 9.25%, maxDD -22.54 vs
      -22.53%, Sharpe 0.42 vs 0.43, Sortino 0.63 vs 0.64. SPY: Sharpe 0.82
      vs 0.83, maxDD and Sortino exact. Plus SPY calendar-year returns
      through the engine == SPY adjusted close to 0.00bp, and within 0-4bp
      of published values (slickcharts/financecharts).
- [x] `test_nav_conventions_golden` green
- [x] first snapshot: defender ranked, gaps computed — on the live 35y DB:
      defender ranks 6/7 (never privileged), gaps null only for the defender,
      and the Sortino-group tie-break visibly decides barbell (0.99/calmar
      3.03) above the defender (0.99/calmar 1.12).

**How to re-run the PV check (M6/M8b will want it):** PV is fully automatable
— no login, and `robots.txt` is `Disallow:` (allow-all). Two traps cost an hour
at M4, both SILENT:
- it 403s on a non-browser User-Agent (that alone is what made this look
  "manual"); send a normal browser UA and it returns 200;
- POST `backtest-portfolio` with `symbol1..N`/`allocation1_1..`/`total1=100`,
  `rebalanceType=4` (monthly), `reinvestDividends=true` — the value is the
  STRING `true`, and anything else (e.g. `1`) silently falls back to **No**,
  i.e. PRICE return, which for this basket understates CAGR by ~2.1pp/y of
  dividends. ALWAYS assert the echoed-back `<option ... selected>` and the
  reported period, never trust the request.

**PV has TWO backtesters, and only one is capped — use both.**
- `backtest-portfolio` (real tickers) is capped for anonymous users at a
  ROLLING ~10-year window, silently: it echoes back whatever `startYear` you
  send while clipping the data (requesting 2007-2016 returns only Jul 2016-Dec
  2016; every ticker, including VTI (live since 2001), reports the same
  `Jan 2017 - ...` floor). Chunking cannot evade it. This is the ticker-EXACT
  check → the 2017-2025 numbers above.
- `backtest-asset-class-allocation` (index series: `TotalStockMarket`,
  `LongTreasury`, `IntermediateTreasury`, `Gold`, `Commodities`) is **NOT
  capped** — `startYear` reaches 1972 and it returns the full window. Those
  sleeves are conceptually exactly what HISTORY_PROXIES stand in for
  (VFINX / VUSTX / VFITX / LBMA gold), so this is what validates the SPLICED
  era, which no ticker-based tool ever can (the ETFs did not exist: DJP 2006,
  GLD 2004, TLT/IEF 2002).

**Spliced-era check (the one that matters, since the splice is where the M4 bug
was).** PV's only 2007-limited sleeve is `Commodities`; the other four reach
1972+. So drop the 7.5% commodity sleeve and renormalise (32.43 / 43.24 /
16.22 / 8.11) — 92.5% of the benchmark over **Jan 1992-Dec 2025 (34y, ~41% of
it on proxy data)**: CAGR 7.61 vs 7.46%, Sharpe 0.69 vs 0.68, Sortino 1.06 vs
1.04, stdev 7.78 vs 7.35%, maxDD -25.09 vs -23.19%. The two larger residuals
are expected and directional, not error: our TLT (20y+, duration ~17) is longer
than PV's LongTreasury index, so 2022's rate shock hits us harder — the same
reason lazyportfolioetf's IEI-based variant shows a shallower -20.58%. Before
the splice fix this same check read stdev 22.5% and maxDD -52.7%, so it is also
the standing regression test for the splice.

Corroborating, non-PV: SPY calendar-year returns exact vs published, and
lazyportfolioetf's 30Y All Weather (CAGR 6.97 vs 7.36%, Sharpe 0.63 vs 0.68 —
residuals explained by their IEI/DBC vs our IEF/DJP).

---

## M5 — Backtests + FAVORS + benchmark_valuation + mechanical confrontations (1 d)

**Definition of Verified**
- [ ] FAVORS matrix regime × strategy is plausible (barbell favored under
      stress? risk parity in disinflation?)
- [ ] benchmark_valuation populated (asset_class + strategy rows) — the
      cross_class/cross_strategy benchmark
- [ ] confrontation fixture: an active-condition invariant whose effect beats
      its benchmark (by method) moves a weight_effective as computed by hand
- [ ] seed invariants matured over 35y: each has a real market_score and a
      status verdict (integrated iff N_min/θ, not refuted) — inspect which of
      your 6 survived, and whether the survivors ring true
- [ ] scenario probabilities warm-started from 35y base rates (not hand-set) —
      the reallocation blend's scenario leg is historically grounded at go-live
- [ ] contradiction check: no two integrated invariants give opposing effects
      on the same handle under simultaneously-active conditions (#5)

**⚔️ Challenge:** does the 35y verdict on YOUR seed philosophy read fair? A
demoted invariant is history disagreeing — worth understanding before M6.

---

## M6 — 🎯 Shadow replay + calibration (1.5 d — Phase 9, PULLED FORWARD) — STOP POINT

The mechanical pipeline is complete: replay it over 35y.

**Definition of Verified**
- [ ] replay_report: hit-rate, agent-follow vs hold-defender net of costs
- [ ] vintage_mode=first_release; vintage sensitivity reported
- [ ] walk-forward calibrated thresholds (~25y calibrate / ~10y validate) — confirmation
      of the winning set happens in the CLI (Telegram arrives at M9)
- [ ] zero PIT assertions failed

**⚔️ STOP — the mechanical premise gate:** if the replay shows no net
value-add, we discuss BEFORE paying for the LLM wiring. It does NOT auto-kill
the project: the LLM layer might still rescue a weak mechanical core, which is
exactly what M8b (agentic) tests — but building the LLM to chase a failing
mechanical core is a DELIBERATE bet, not the default. This evidence also
decides the final gate thresholds.

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

## M8b — Agentic replay: the best-case pre-go-live screen (0.5 d — Task 9.4) — STOP POINT

The SAME replay harness as M6, `include_worker=True` — the live chain
accelerated (no reimplemented decision loop). Because the corpus is known from
t=start, it is a **best-case** run → a NECESSARY a-priori screen: *if even this
cannot beat All Weather, the real-time system has no chance.* Not a *sufficient*
proof (semi-PIT; real-time performance = forward paper-mode). Default cadence
'episodes' (≈20 LLM runs) to bound cost.

**Definition of Verified**
- [ ] best-case check: A' (agentic-follow) beats B (All Weather) at all?
- [ ] behavioral log readable: at 2008 / 2020 / stagflation, does the Worker
      reason sensibly? does it propose sensible improvements?
- [ ] delta A' − A reported — isolates the REALLOCATION contribution (switches
      are mechanical in both); LABELLED "semi-PIT, not go-live performance"
- [ ] `test_agentic_replay_semipit`: invariant weights read as-of-t; a
      confrontation dated after t changes no weight before t; agent-discovery
      absent from the run

**⚔️ STOP:** if the best-case system can't beat All Weather, or the Worker's
reasoning reads incoherent, do NOT proceed to live. Judge the Worker on BOTH
channels: decisions (A' − A) AND the improvements it proposes (off-NAV, in the
log) — A' ≈ A alone does not condemn it.

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

**Total: ~15 days.** After M11: 3 months of paper-mode history →
the V2 boundary discussion (REVISION_NOTES).
