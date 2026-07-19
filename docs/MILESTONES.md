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
- [ ] flip-flop fixture does not switch before `regime_confirm_prints`
      concordant prints (M3-calibrated to 3; the unit fixture sets its own)
- [ ] STABILITY AUDIT (#4) over 35y: whipsaw count (episodes reversed within
      3 months), median episode length, detector lag (start_date → created_at
      confirmation), and how many candidate switches the
      `regime_confirm_prints` hysteresis suppressed — all reported; whipsaws
      are rare and lag is bounded

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
      stress? risk parity in disinflation?) — INSPECTED, ANSWER IS NO. Not
      pending your judgment; this box is a finding, and the finding is
      negative. Of its own two questions, one passes: barbell under stress
      YES — it tops falling-growth-falling-inflation (2.00) and uncertain
      (1.25), and that is the ONE result surviving a null (p=0.034),
      economically exactly what a barbell is for. The other fails: risk parity
      in disinflation NO — four-seasons-rp is LAST in
      rising-growth-falling-inflation (1.57), 3rd in
      falling-growth-falling-inflation (1.05). And the box's premise does not
      hold anyway (I-35): in 4 of 5 regimes the within-regime ranking is
      indistinguishable from random regime labels (stagflation p=0.94, spread
      0.18 across 4 strategies over 17 episodes). Noise is neither plausible
      nor implausible — there is no claim there to judge. Ticking this would
      assert that a noise matrix passed a plausibility read.
      YOUR call is not "is it plausible" but what to DO: (a) ship to M6 and
      let the walk-forward price the favors leg — recommended, and the M6 DoV
      now says a high stable weight is suspicious; (b) rework FAVORS; (c)
      strike this box as unanswerable at 89 episodes / 5 regimes / 4
      strategies.
- [x] benchmark_valuation populated (asset_class + strategy rows) — the
      cross_class/cross_strategy benchmark — live: 45,455 rows over the 35y,
      `asset_class` 5 ids / 12,230 rows, `strategy` 4 ids / 7,575 rows, plus
      `asset` 13 ids / 25,650 rows (the `asset:<ticker>` handles). Window is
      the CONFRONTATION horizon (12w), not the 756d ranking window.
- [x] confrontation fixture: an active-condition invariant whose effect beats
      its benchmark (by method) moves a weight_effective as computed by hand
      — `test_confrontation_fixture_moves_weight_by_hand` green (tests/
      test_invariants.py): 4/1 confirmations, condition active now, so
      score 0.8 × weight_initial 0.85 × recency 1.0 = 0.68 > floor 0.40.
- [x] seed invariants matured over 35y: each has a real market_score and a
      status verdict (integrated iff N_min AND score ≥ θ AND the 0.50 null
      yields evidence this good ≤ 5% of the time — effect size AND evidence;
      not refuted. ADR-006 M5-bis) — inspect which of your 7 survived, and
      whether the survivors ring true — all 7 measured (N from 8 to 87, none
      on the 0/0 default), all 7 carry the engine's `[birth-matured` verdict
      marker, and ZERO author-supplied verdicts survive (`_force_uncertified`;
      the gold invariant arrived claiming `integrated`/`market_score: 0.78`
      and got neither). Whether the survivors ring true is the ⚔️ Challenge
      below — yours, and NOT a condition of this box:
        integrated  inv-low-real-yields-favor-gold   53/82  0.646  tail 0.005
        proposed    inv-inflation-persistence-tips    9/14  0.643  (N-starved
                                                       — 2003 TIPS floor,
                                                       I-34; held `integrated`
                                                       pre-M5-bis on a 21% coin)
        proposed    inv-liquidity-easing-risk        33/59  0.559
        rejected    inv-rising-growth-equities       44/87  0.506
        rejected    inv-falling-growth-duration      33/72  0.458
        rejected    inv-liquidity-tightening-risk     2/8   0.250
        rejected    inv-diversification-drawdown      1/20  0.050  (mis-posed
                                                       benchmark — I-32, read
                                                       it before believing it)
      One of seven earned integration. That is the fact to dispute.
- [x] scenario probabilities warm-started from 35y base rates (not hand-set) —
      the reallocation blend's scenario leg is historically grounded at go-live.
      Check the ARITHMETIC, not just that the rows exist: a scenario whose
      trigger list is a disjunction must not score below its own widest single
      trigger (the M5-bis catch — 4s bear read 1.37% against a 16.73% `^VIX >
      25` branch) — 12 rows, demonstrably NOT the hand-set values (4s
      base/bear/bull seeded 45/20/35, warm-started 56.78/18.16/25.07), and the
      arithmetic now holds: raw `^VIX > 25` 16.73% ≤ (`^VIX > 25` OR
      stagflation) 18.60% ≤ 16.73+3.57%, a union bounded by max(parts) and
      sum(parts).
- [x] contradiction check: no two integrated invariants give opposing effects
      on the same handle under simultaneously-active conditions (#5) —
      `check_contradictions()` returns []. NOTE it is currently VACUOUS: only
      one invariant is integrated (`inv-low-real-yields-favor-gold`), so there
      is no pair to oppose. It becomes a real check when the integrated set
      grows — and I-33 already limits it then (handle CONTAINMENT is not seen).

**⚔️ Challenge:** does the 35y verdict on YOUR seed philosophy read fair? A
REJECTED invariant is history disagreeing — worth understanding before M6.
(Not a DEMOTED one: that is the validation gate refusing a malformed
condition/effect, which says nothing about the market. The two are distinct
outcomes in `mechanical/invariants.py`.) Read it against I-32 — a fair
measurement of a mis-posed question is not history disagreeing either.

**⚔️ Challenge (added M5-bis):** where did the challenge actually LAND? At
M5 it landed entirely on the invariant engine — 7 bugs, ADR-006's M5/M5-bis
amendments, I-30 — while FAVORS and the scenario warm-start, built in the
same pass, got none. That is backwards for what comes next: M6's mechanical
replay is BLIND to invariant weights (docs/ARCHITECTURE.md) and blends
`0.4×scenario + 0.6×favors`. The scenario leg turned out to be arithmetically
impossible once looked at (see the DoV item above); FAVORS was then tested
against a null too, and its per-regime ranking is noise in 4 of 5 regimes
(I-35). Challenge the half the NEXT milestone consumes, not the half you just
built.

---

## M6 — 🎯 Shadow replay + calibration (1.5 d — Phase 9, PULLED FORWARD) — STOP POINT

The mechanical pipeline is complete: replay it over 35y.

**Definition of Verified**
- [ ] replay_report: hit-rate, agent-follow vs hold-defender net of costs
- [ ] vintage_mode=first_release; vintage sensitivity reported
- [ ] walk-forward calibrated thresholds (~25y calibrate / ~10y validate) — confirmation
      of the winning set happens in the CLI (Telegram arrives at M9)
- [ ] zero PIT assertions failed
- [ ] the calibrated FAVORS blend weight is read against I-35: the per-regime
      ranking it feeds is indistinguishable from random regime labels in 4 of
      5 regimes (stagflation p=0.94), so a HIGH, STABLE favors weight on the
      holdout is SUSPICIOUS, not confirmation — 5 knobs over ~25y can find
      one. A weight driven toward 0 is the result that matches the evidence.

**⚔️ STOP — the mechanical premise gate:** if the replay shows no net
value-add, we discuss BEFORE paying for the LLM wiring. It does NOT auto-kill
the project: the LLM layer might still rescue a weak mechanical core, which is
exactly what M8b (agentic) tests — but building the LLM to chase a failing
mechanical core is a DELIBERATE bet, not the default. This evidence also
decides the final gate thresholds.

**Findings (M6 investigation, 2026-07-16 — numbers on the repaired mechanics:
own-strategy FAVORS guard, scenario hysteresis, maturity floor; all measured
on the real 35y, `mechanical/replay.py` + `calibration.py`):**

- **Headline** (seeded thresholds, weekly, 10 bps): agent-follow 6.83%/y vs
  hold-defender 7.27%/y — edge **-0.44 pts/y**; Sortino **1.024 vs 0.952**;
  Calmar 0.391 vs 0.337; max drawdown **-17.5% vs -21.6%**; hit-rate +12w
  46%. The mechanical core is a RISK REDUCER, not a return generator — and
  it is real adaptation, not naive de-risking: at matched drawdown the static
  defender/barbell frontier yields ~6.5%/y (A beats it by ~+0.35 pts/y; the
  `context arms` block in every replay report now tracks this automatically).
  Caveat kept honest: static permanent-balanced does 7.00%/y at the SAME
  -17.5% drawdown (Sortino 0.959 < A's 1.024) — the value of adaptation over
  the best in-menu static pick is thin.
- **The reallocation leg destroys value on every metric even after repair**
  (alone: edge -0.30, Sortino 0.944 < B's 0.952, hit 0.411); the switch leg
  alone is edge -0.17 / Sortino 1.043 / hit 0.531. The spec already calls the
  mechanical scenario read "a conservative approximation" of the Worker —
  measured, the approximation is negative-value.
- **FAVORS blend weight** (the DoV box above): in the final 729-point regrid
  the winner is favors=0 but favors=0 and favors=1 INTERLEAVE through the top
  15 — the blend's composition no longer separates candidates at all. Read
  WITH I-35 that is the cleanest possible agreement: the reallocation leg is
  noise whichever way it is blended; the only knob that consistently helps is
  capping its damage (turnover=15 sweeps the entire top 15). The pre-repair
  grid had instead manufactured favors=1.0 in-sample, collapsing to -3.2
  pts/y on the holdout — the whipsaw handles were what let it do that.
- **No positive in-sample edge exists anywhere in the 729-point grid** (best
  calibration edge -0.80 pts/y, seed -1.23; holdout column spans -2.4 to
  +0.38 = the grid fitting noise). Two hypotheses the regrid TESTED AND
  REFUTED: a faster trailing window does not help (window=756 sweeps the top
  15; 252/378 never appear — the signal does not improve with speed), and
  more confirmation does not buy signal quality (confirm 2/3/4 interleave).
  There is nothing to `--apply`.
- **DESIGNED_FOR is refuted at the book level for 2 of 4 quadrants**
  (within-regime excess vs the defender over the materialized instances):
  falling-growth-falling-inflation's designed book is the WORST in its own
  regime (-1.22%/instance, win 0.24 over 17 instances — the equities book
  wins 0.76 there); stagflation's designed book is -0.27%/win 0.41. Only
  rising-growth-falling-inflation's mapping holds (+1.33%, win 0.87; momentum
  wins 15/15 there unmapped). This is I-35's counterpart one level down, and
  it explains why the regime-keyed switch experiment (below) fails: the
  signal was faithful, the MAP was wrong.
- **Regime-keyed switching (DESIGNED_FOR instead of trailing Sortino) does
  not rescue the core as-is**: edge -0.27 but Sortino 0.932 < B and mdd
  -19.8%; switch-only regime is WORSE than holding (Sortino 0.869, mdd
  -24.5%) because it parks 68% of the time in the refuted
  falling-growth-defensive book. Fix the map before re-testing the signal.

- **ROOT CAUSE of the map refutation — a SEMANTIC mismatch, not a design
  error in the books (2026-07-19).** Listed the 17 real
  falling-growth-falling-inflation episodes: SPY is POSITIVE in 12 of them
  (+28% Apr-Oct 2020, +21% Oct-2022→Sep-2023, +19% 2010, +15% 2014-15 &
  2017). These are not crises — they are post-crash recoveries and benign
  disinflations. The defensive book (40% long Treasuries) held across them
  MISSES the rebound → win-rate 0.24 in "its own" regime. The one genuine
  crisis in the sibling regime (Jul-2008→Jan-2009: SPY -30.4%, TLT +27.9%)
  is exactly where the defensive book crushes B — but it is 1 episode in 17.
  The books were designed for MARKET regimes (crisis/boom = Dalio's
  surprises-vs-priced), but the detector delivers MACRO-PUBLICATION regimes:
  first-release prints (ADR-003) describe ~2 months of the PAST, plus the
  3-print confirmation hysteresis, so the label arrives AFTER the market has
  already priced and traded the move. "Defensive when published growth falls"
  = buying the umbrella after the storm and holding it through the sunshine.
  This is why the ONLY surviving signal is the `^VIX > 25` stress tag: the
  VIX is the sole regime axis measured at MARKET speed — contemporaneous,
  daily, no publication lag. See I-38.

**⚔️ OPEN — two calls that are the owner's, with the evidence above:**
1. **Gate metric (Task 9.3 — needs an ADR once decided):** "agent-follow ≥
   hold-defender net of costs" does not name its metric. On CAGR the gate is
   CLOSED (-0.44); on Sortino/Calmar/drawdown it is OPEN. Note the user's own
   -15% rule: B breaches it by 6.6 pts over the 35y, A by 2.5.
2. **Scope of the mechanical gate run (Task 9.1):** compute the kind=
   'mechanical' verdict switch-only (the realloc path stays in the code — the
   M8b agentic replay needs it for the Worker) or keep both legs. The realloc
   leg's mechanical approximation is measured negative on every metric.

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
