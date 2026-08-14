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
SeedEvent inventory reflects what ran. `DEFERRED_STEPS` is now EMPTY: with
6/6b landed, every UC0 step has an implementation.

Step 6 reads `SOURCES_PATH` (its first consumer), NOT the repo — the corpus
is large and copyrighted, and a corpus kept beside the code is one
`git add -A` away from being published. Step 6b is the only LLM call in the
seed, and it is cheap exactly once: the `curated_passage` checkpoint means a
re-seed over an unchanged corpus makes zero API calls. Both steps run LAST
despite their number, because curation reads the signal registry and compares
against the seeded invariants.

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
- [ ] counts: 13 entity / 6 M:N / 3 TS / 13 doc tables

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

**Post-gate exploration (2026-07-19, owner-steered, scratchpad only — nothing
in product code; the 2016-2026 window has been consulted repeatedly during
this exploration, so it is no longer pristine holdout: the assembly below is
holdout-INFORMED, and its real validation is forward paper-mode):**

- **The switching criterion is AMPLITUDE, not patience.** Sweeping the
  detector (smoothing 4-12mo x confirm 3-6 prints x dead-band x1-x3) on
  segmentation quality: widening the dead-band x3 halves the episodes
  (90 -> 45, 1.3 switches/y), pushes 'uncertain' (= hold, do nothing) to 81%
  of the time, and EXPLODES the behavioral distinctness of the episodes that
  do fire (SPY spread across quadrants 14.5 -> 53.7 pts/y in knowable
  windows). More confirmation prints does the opposite — episodes lengthen
  but distinctness collapses. Requiring a PRONOUNCED smoothed move is the
  "don't over-react" that works; waiting longer is not.
- **Split-stability (sm=4/cp=3/noise x3):** SPY sign per quadrant is
  consistent across 1991-2016 / 2016-2026 — positive in every pronounced
  confirmed quadrant EXCEPT stagflation (-18.5/-8.3). One stable macro
  signal. And TLT is NOT a stagflation hedge (+35.5 -> -69.9, the 2021-22
  bond crash): the stagflation asset is REAL (GLD/DJP), not duration —
  consistent with I-35's barbell-under-stress and the crisis-layer runs.
- **DESIGNED_FOR partially REHABILITATED — the earlier refutation was about
  GRANULARITY, not the book.** The seeded stagflation book, deployed only on
  PRONOUNCED confirmed stagflation (5 episodes/35y, 4% of the time), is
  positive on BOTH sides of the split (+0.02/+0.37 pts/y edge) — the same
  book that lost (win 0.41) under the fine detector's 17 micro-episodes. The
  fine granularity was drowning a real signal in false positives.
- **3-layer architecture measured** (base + macro stagflation-tilt + VIX
  crisis overlay): the layers have OPPOSITE roles — macro adds return
  (+0.13/y full window, sign-stable), crisis costs return (-0.33 to -0.76/y)
  but buys drawdown compliance. The full stack is the FIRST configuration in
  all of M6 to satisfy the owner's -15% rule over the full 35y (mdd -14.8%,
  Sortino 1.024 vs B's 0.953, cost 33bps/y vs B — which breaches its own
  rule by 6.6pts).
- **Owner architecture decisions (2026-07-19):** (a) AMPLITUDE is the switch
  criterion; (b) B is an absolute BENCHMARK only — never the defender/base;
  (c) coarse granularity, never over-react; (d) the GROWTH axis (pronounced
  decroissance) enters the macro layer alongside inflation — evidence note:
  in knowable windows, pronounced falling-growth-falling-inflation is
  historically EQUITY-POSITIVE both halves (+30/+42 — post-crash recoveries;
  the crash itself belongs to the VIX layer), so the growth axis's stable
  expression is risk-ON, not defense.

**Countercyclical state-of-the-art test (2026-07-19, Verdad/Rasmussen —
docs/Countercyclical+Investing). Menu additions DEPLOYED to the live DB
(backup taken first): IWN small-cap value (proxy DFSVX, 1993, corr 0.966),
VCIT IG credit (proxy VFICX, 1993, monthly 0.978), BAA10Y credit spread
(1986) + T10Y2Y slope (1976) as market-priced regime signals. NOTE: the
paper's exact HY OAS (BAMLH0A0HYM2) is ICE-licensing-truncated on FRED to a
~3y window — useless for backtest; BAA10Y (Moody's Baa − 10y, the Fama-French
default spread) is the long-history substitute.**

> **⚠️ CORRECTION (2026-07-19, later same day) — the three bullets and the
> "CONVERGENT VERDICT" below are SUPERSEDED by a measurement bug, do not cite
> them. The market-signal stack runs used `replay.load_inputs().prices`, which loads
> ONLY portfolio/scenario constituents — so IWN and VCIT (40-50% of the market-signal
> books) were absent and held FLAT at 0% return, crippling the stack. Re-run
> with a full price dict (daily): the market-signal stack (credit-spread+slope regime
> + 200d trend-following) does **9.85%/y vs B 7.27% — edge +2.6 full, +2.8
> calibrate, +2.05 HOLDOUT**, Sortino ≈ B, drawdown -24% (daily). It BEATS B
> on return, robustly in and out of sample, and has the best Sharpe of every
> strategy tested. The small-value + IG-credit additions are exactly what
> powers it. "A bond beats this stack" and "nothing beats B" were artifacts of
> the zeroed sleeves. Authoritative corrected view: docs/STRATEGY_COMPARISON.md.
> What still STANDS (those runs used seeded books with no IWN/VCIT, correctly
> priced): SEEDED-book regime rotation and the macro+crisis stack do not beat B
> on return (the latter is a risk-reducer: 6.2%/-13%, the only -15%-compliant
> line); cross-asset momentum on the common 1991-2026 window is 8.0%/Sharpe
> 0.71/-36% — above B on raw return, worse risk-adjusted (the earlier +3-5 pts
> included pre-1991 data outside B's window).

- **[SUPERSEDED — see correction above] Faithful market-signal replication (market-signal regime: credit spread + slope
  vs 10y trailing medians → growth/inflation/slowdown books, equity-heavy,
  + 200d trend-following overlay) STILL does not beat B on return**: -0.49/y
  full, -0.26 calib, -1.02 holdout. Trend-following is what makes it viable
  at all (drawdown -30% → -17%, Sortino 0.53 → 0.86); the naive real-asset
  tilt without it was -4.0/y.
- **vs 60/40 and 100% equity, decade by decade** — the mechanism REPLICATES
  market-signal's pattern faithfully but NOT its headline, and the reason is the
  PERIOD, not the code. 2000-2010 (two recessions): STACK 8.3%/-12% CRUSHES
  60/40 2.9%/-33% and SPY -0.5%/-55% (+540bps, the "lost decade" where market-signal
  shines). But 1991-2026 is THREE equity bulls (1990s/2010s/2020s) + ONE hard
  decade, so the full-window average LOSES to the equity-heavy benchmarks
  (STACK 6.8% vs 60/40 8.8% vs SPY 11.0%). market-signal's +570bps over 1970-2020
  came from TWO hard decades (1970s stagflation + 2000s); the 1970s is below
  our ~1991 data floor. Not reproducible in-sample — a period artifact.
- **[SUPERSEDED — the 6.8% "stack" here was the bug-crippled one; corrected
  stack is 9.85% and beats a bond ladder and B] THE DAMNING SUMMARY (owner's
  framing): a corporate bond held to maturity beats this stack risk-adjusted.** Buy-and-hold over the period: STACK
  6.8%/-17% marked; IG credit fund (VCIT) 5.2%/-20.6%; a held-to-maturity IG
  ladder ≈ 5.5-6%/~0% REALIZED drawdown (coupons off ~7% starting yields, no
  mark). So the entire active apparatus (regimes, signals, trend-following,
  ~86 switches) earns ~1pt over a bond fund, LOSES to B risk-parity
  (7.1-7.3%/-22%), and is beaten risk-adjusted by a boring bond ladder held
  to term. It does not justify its complexity on this data.

**⚔️ [SUPERSEDED by the CORRECTION above — the market-signal stack DOES beat B by
+2.5 once IWN/VCIT are priced] CONVERGENT VERDICT (all angles, 2026-07-19): no mechanical approach —
regime rotation (macro OR market-priced, up to the faithful state of the art)
— beats B on RETURN over 1991-2026; every variant lands -0.3 to -1.5/y and
only improves drawdown. The +500-1000bps target vs B (risk parity) is not
supported by ANY benchmark or method; market-signal's edge is vs the WEAK 60/40, and
is period-carried by the 1970s we lack. Three forks for the owner:**
  1. **Return goal kept** → the ONLY return-positive lead measured is slow
     cross-asset MOMENTUM (26-52w: +2.5-3.5/y full window, though trend-decade
     concentrated) — NOT regime classification. Explore it properly.
  2. **The M8b bet** → does the LLM/Worker layer rescue a weak mechanical core?
     (The STOP's "deliberate bet, not the default".)
  3. **Accept the risk-reducer profile** → comparable-to-B CAGR, better
     drawdown, -15% rule respected; alpha must then come from M7 knowledge /
     V2 discovery, not rotation.

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

## M6-bis — Wire the adopted market-signal monthly stack (ADR-007) — STOP POINT

**Added by ADR-007 (accepted 2026-07-20).** The post-M6 exploration converged
on the market-signal monthly countercyclical stack, which — once the
`load_inputs().prices` bug was fixed (it starved IWN/VCIT to 0%) — beats B by
+2.5/y robustly in AND out of sample at -24% drawdown. See `docs/V1_STRATEGY.md`
(the adopted spec + full impact map) and `docs/STRATEGY_COMPARISON.md`. This
supersedes M6 OPEN fork 1's "momentum-only return lead" read: the return-
positive lead is the market-signal stack, not momentum (8.1% / Sharpe 0.46 / -37%).

**Build (Step 1 of the roadmap — keep the bridge, do NOT delete M3/M5/UC7-8):**
1. Seed the 3 books as Strategy/Portfolio (credit-spread-wide SPY50/IWN40/GLD10,
   credit-spread-tight-yield-curve-flat SPY50/GLD40/IWN10, credit-spread-tight-yield-curve-steep VCIT50/IEF40/IWN10 — renamed from
   growth/inflation/slowdown by ADR-007's third addendum).
2. Market-signal regime module: `BAA10Y` vs 10y trailing median
   (WIDE→credit-spread-wide), else `T10Y2Y` vs 10y median (FLAT→credit-spread-tight-yield-curve-flat,
   STEEP→credit-spread-tight-yield-curve-steep). Replaces the
   macro detector FOR ALLOCATION only (I-38).
3. 200d trend overlay: SPY/GLD sleeve → IEF when below its 200-day MA.
4. Monthly decision path through the EXISTING `mechanical/gates.py` binding
   caps (now -25% per ADR-007).

**Definition of Verified:** replay-validate the wired stack reproduces the
pinned numbers — the anti-drift check that caught the M6 rebalance-order bug —
and it runs monthly end-to-end through the caps.

**AUTOMATED 2026-08-12, and no longer restated here.** This DoV named
`11.14% / -23.8%` for two supersessions after the pair had moved, which is the
defect it exists to catch happening to its own statement of the target. The pair
now lives as `market_signal.PINNED_CAGR` / `PINNED_MAX_DRAWDOWN` — the single
authority — and three things read it: `tests/test_anti_drift.py` (the DoV,
executed against the live database, skipped with a reason where the 35 years are
absent), `market_signal_cycle.journal_drift` (every Monday, verdict appended to
the EventLog) and `alerts.stack_drift_alert` (a divergence in the digest, never
a block — ADR-009). Superseding the pair now means moving that constant in the
same commit or watching the check go red, which is the git gate ADR-006 does not
reach. The historical pairs (11.14% at ADR-010's rate, 11.26% double-charged,
9.85% / -24% pre-hysteresis) are narrated in `mechanical/market_signal.py`. The OLD design stays wired as fallback + benchmark; forward
paper-mode (M9), not this milestone, is what earns the full switch.

**Status (2026-07-20): core DoV MET — anti-drift PASSES, caps clean.**
`mechanical/market_signal.py` (pure `classify_regime`/`apply_trend_overlay`/
`build_targets` + `run_market_signal` driver) reproduces the numbers EXACTLY on the
live DB: CAGR 9.85%, Sortino 0.94, maxDD -23.8%, 3.4 changes/yr, ZERO cap
breach. 10 unit tests + 204 suite green.

**Superseded 2026-08-01 (ADR-007 fourth addendum, owner-arbitrated):**
`CONFIRM_DECISIONS = 3` hysteresis on the book switch → **CAGR 11.26%, Sortino
1.11, maxDD -23.8% (unchanged), 2.8 changes/yr, turnover 53.9x → 42.0x**,
re-verified exactly against the A/B prototype on a DB snapshot. Motivation: 25%
of the 36 historical book changes were decided by a sub-2% margin and 14
reversed within 3 months, on flips costing ~90% turnover. Robust in both split
halves; the sweep has an interior optimum (it collapses at a 50% dead-band); the
zero-turnover control has a higher CAGR but -52% drawdown.

**Cap finding RESOLVED (ADR-007 addendum, choice (a)).** When BOTH SPY and GLD
are below their 200d MA (risk-off: 2008-09, 2020, 2022...), the overlay
redirects both sleeves into IEF, concentrating the HAVEN to ~90% — the
deliberate flight to safety the validated 9.85% includes. The owner chose to
EXEMPT the trend-haven sleeve (IEF) from the single-asset cap (`gates.
concentration_ok(..., exempt={IEF})`) over splitting into SHY/cash — simplicity,
and the haven is a safety redirect not a conviction bet. Narrow, named
exception; the cap still binds every other sleeve (empty `exempt` default).

**Book seeding: DONE (commit 493eec0).** The 3 books exist as Portfolio
entities with `holds` edges to the `market-signal-stack` strategy
(`ms-growth-book` primary). Renamed 2026-07-20 after the signal state that
selects them — ADR-007 addendum 3; the entity IDs keep their original
growth/inflation/slowdown spelling because EventLog is append-only.

**Live wiring: DONE 2026-08-02.** The remaining build — "wire the live monthly
decision path into UC8/Writeback" — is complete. `market_signal_cycle.py` runs
the live monthly decision end to end (PIT inputs → credit-spread/slope signal →
book → 200d overlay → binding caps → EventLog → `Proposal(proposal_type=
'market-signal')`, and from there the existing machinery: digest, +12w outcome),
composable as a chain step (`tests/test_market_signal_cycle.py` runs it through
the real `run_chain`) and idempotent on the month's decision date.

**What is atomic, precisely** (the earlier wording here claimed the whole
pipeline was one transaction, which it is not): the DISPOSITION is — journal
event, proposal event, Proposal vertex and `ms-stack.allocation` all commit
together in `writeback.dispose_market_signal`. The stack's NAV refresh is
deliberately OUTSIDE it and runs first, because the NAV is a weekly artefact and
must move on the ~3 Mondays a month that decide nothing; a disposition that then
fails leaves the paper NAV a step ahead of the position, which self-heals since
`persist_stack_nav` rebuilds the whole derived series (INSERT OR REPLACE) on
every run. The digest and the +12w outcome are separate jobs.

**It is SPECIFIED at 08:55, not yet scheduled there.** `chain.py` is a
scheduler-agnostic runner over a caller-supplied step list, and nothing
assembles the Monday list outside tests — that wiring (launchd, the alert on
abort, the actual 08:55 slot) is **M9**. The distinction matters: until M9 the
adopted strategy decides only when something calls it.

Anti-drift preserved BY CONSTRUCTION, not by parallel implementation: the live
path and the replay both read `walk_decisions`, the single decision clock;
`build_targets` is now a projection of it (`{d.date: d.target for d in walk if
d.changed}`). Re-verified on the live DB after the refactor: **CAGR 11.26%,
Sortino 1.11, maxDD -23.8%, 97 changes over 418 decisions, turnover 42.0x, ZERO
cap violations** — the pinned pair of that day, unchanged to the digit. ADR-010
then restated it at the owner's real Saxo commission (23 bps/order, no FX):
**11.14% / 1.09 / -23.8%**, turnover and drawdown unmoved. That is now the
anti-drift target. Same strategy throughout — only the price of a trade changed.

Design decisions taken during the wiring, each stated in the code:
- **No state table.** The held book is recomputed by replaying the walk to
  `today` (so live and backtest cannot disagree); what the stack HOLDS is the
  last emitted market-signal Proposal; every decision, moving or not, is
  journalled as a `MarketSignalDecisionEvent`. A fourth copy of those facts is
  the one that could drift.
- **Emit is keyed on the HELD allocation, not on `Decision.changed`** — that is
  what makes the path self-healing after a gate block.
- **Gate set is deliberately not `reallocation_gates`.** Sum-to-100 + the
  single-asset cap (IEF trend-haven exempt) + allowed tickers APPLY, and ADR-009
  documents them for what they are — **regression guards against a config or
  code change, not a safety control**; the DRAWDOWN leg is NOT among them (it
  alerts, it never blocks). `max_turnover_pct` (30) and
  `min_allocation_change_pts` do NOT apply (a book switch is a ~90-100% turnover
  move by construction — the 30% ceiling would block every switch the strategy
  exists to make); gate 6 and the 4-week cooldown do NOT (nothing is cited, and
  the cooldown would suppress the 200d overlay's re-entry, which IS the
  drawdown control).
- **The opening proposal is scored against the BEST-RANKED portfolio** at that
  date, excluding the stack itself — not against cash, which was the earlier
  draft and the easier bar. The owner's real alternative to entering the stack
  was to keep holding the best thing already available. Without a baseline it
  could never be scored at all, which ADR-006 forbids.

**⚠️ Carried forward to bridge retirement (Step 6):** the live path takes its
trading calendar from `replay._book_calendar`, i.e. from the retained Dalio
defender's NAV index. Deliberate — it keeps the live clock bit-identical to the
backtest's — but the ADOPTED strategy therefore cannot run on a DB without a
NAV-backfilled bridge defender. Retiring the bridge must give the stack its own
calendar and re-validate the pinned numbers in the same commit.

**Coherence pass, 2026-08-02 (same day, after the wiring).** Re-reading the
commit surfaced four defects and two imprecise claims; all are fixed, and the
ones that changed what is safe to believe are recorded as an addendum to
ADR-009 rather than silently patched.
- The drawdown alert's message misdescribed its own number (`rolling_max_drawdown`
  is the DEEPEST drawdown inside the trailing 756 days, not the distance from a
  36-month high — a stack at an all-time high can report -30%).
- `mechanical/alerts.py` shipped with **no tests**, while being the entirety of
  what ADR-009 substituted for the removed gate → `tests/test_alerts.py`.
- The digest's single proposal slot let the bridge's 09:00 reallocation beat the
  08:55 market-signal decision on `created_at`, hiding the adopted strategy's
  order on exactly the months it had one. The decision now renders from its
  JOURNAL (one row per decision date, moved or not), and the proposal slot is
  the bridge's alone.
- A sleeve with ZERO price rows was invisible to the freshness check (no GROUP
  BY row to be the `min`), so the worst feed failure read as the healthiest.
- A re-seed reset `ms-stack.allocation` — which is now STATE, not config — back
  to the warm-up book. The seeded value is an initial one only.
- `dispose_market_signal` returned `refused("no_change")` on a holding month:
  ~9 months a year reported as a gate refusal. Gates and movement are now
  independent (`emitted` / `blocked`).
- Two claims restated honestly: the stack's `portfolio_nav` is a **paper**
  series (it assumes every monthly target filled at its anchor close — V1
  executes nothing), and the caps bound to the live decision are now the
  stricter of user ∧ `ms-stack` ∧ book, not the book's alone.

**Second coherence pass, 2026-08-02.** One defect, one gap, one homonymy.
- **An absent `BAA10Y` or `T10Y2Y` decided the book anyway.** `run_market_signal`
  refused a missing price sleeve but not a missing SIGNAL: the empty series
  reindexes to all-NaN, `classify_regime` reads that as warm-up, and the stack
  silently holds `credit-spread-wide` — the 90%-equity book — on no signal at
  all, indistinguishable from a genuine warm-up (`knowable_at` is None in both).
  It now raises, symmetric with the sleeve guard. The seed keeps its own
  pre-check and still SKIPS rather than raises (incremental-seed contract); the
  live cycle had no pre-check, which is why the guard belongs in the driver.
- **Stale signals now alert** (`alerts.signal_freshness_alert`, its own check
  next to the sleeves'). Different failure in kind: a dead price feed BLINDS the
  overlay, a dead signal feed MISINFORMS the decision — the forward fill carries
  the last print, so the book is still chosen, from a spread quoted weeks ago,
  and nothing else in the digest looks wrong. An alert and never a block: ADR-003
  says a stale print is what was knowable, ADR-009 scopes the live path to
  telling rather than refusing.
- **Two things were both called "held".** `ms-stack.allocation` is the BOOK IN
  FORCE — which book the strategy is in, never empty, read by the snapshot and
  the ranking, consistent with a paper NAV that runs from 1991. The
  market-signal Proposal chain is the OWNER POSITION — empty until the opening
  entry, because V1 executes nothing and the owner holds what the digest told
  them to buy. Before the first proposal they legitimately disagree; from it
  onward they are written in one transaction and must match (pinned by a test).
  No behaviour changed — `held_allocation` was already reading the right one —
  but three comments claimed the decision reads the portfolio row, which it must
  never do: on a fresh DB whose target equals the seeded warm-up book, that
  reading emits no proposal and the opening order, the entire deliverable of a
  paper-mode V1, never reaches the digest.

**ADR-011's other half, 2026-08-02.** The ADR's gate 0 shipped; its clause 3 —
the Worker's qualitative reading is "journalled and rendered" — did not. The
Worker was ASKED for that reading by the system prompt ("that reading is your
contribution") with no field to receive it, so it landed in `regime_assessment`
or `reasoning`, and neither had a reader anywhere in `src/` outside the
Planner-Post prompt they are serialised into. On a cycle that proposed nothing,
the Worker's entire output was discarded.
- `WorkerResult.market_signal_assessment`, REQUIRED like the other prose fields
  (Phase-1bis: a partial answer fails validation; the empty state is a sentence,
  not an absent key). Both copies of the prompt updated, and a test pins that
  the prompt names the field the schema declares.
- `decision_cycle.journal_worker_reading` appends a `WorkerReadingEvent` on
  EVERY cycle, before the guardrail — it records what the Worker SAID, which is
  a fact regardless of what Planner Post later kept. Payload carries the whole
  prose surface, the trigger, the `run_id`, and the market-signal
  `decision_date` the reading judges. This also closes a hole it exposed: UC8
  appended no cycle-level event, so a week that proposed and confronted nothing
  was indistinguishable from a week the chain never ran.
- Digest: "🗣 Worker challenge" INSIDE the market-signal block, after the
  mechanical `Why:` — a critique three blocks from its subject is a loose
  opinion. A reading of an OLDER decision (failed or skipped cognitive cycle) is
  shown with its own date rather than dropped: hiding it would recreate, one
  field over, the disappearance this fixes.
- E2E test with `ms-stack` as defender through the real `run_decision_cycle`,
  not just the Writeback unit: gate 0 refuses on jurisdiction AND the reading is
  journalled anyway — which is the whole trade the ADR makes.

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
- [x] IDEMPOTENT: re-running the curator on an unchanged document makes zero
      LLM calls and creates zero rows (`curated_passage` checkpoint). Added
      after the 2026-07-21 full-corpus run: the curator has three callers, so
      without this every Monday sweep would re-spend the run and duplicate its
      output.
- [x] RESUMABLE: each batch is persisted as it returns, in one transaction
      with its checkpoint rows — a crash costs the batch in flight, not the
      run. (That run kept nothing: 29 admissible candidates and all 50
      reference notes were lost to a `print`-only harness.)
- [x] dedup is structural, not just semantic: two claims merge only on
      cosine > 0.80 AND matching effect AND non-disjoint conditions.
      Measured: "wide spreads → equities underperform" and "tight spreads →
      equities outperform" sit at cosine 0.907 and must NOT merge (I-42).

**⚔️ STOP — the qualitative core:** you INSPECT the real candidates (a
build-time sanity read, not a runtime gate — ADR-006); the
candidates/principles ratio tells whether the factory converges. The quality
contract faces reality here.

**Full-corpus result, 2026-07-21** (3 books, 1822 passages, 34 min, ~7 cents):
1822 passages -> 45 candidates -> 34 weighted invariants + 209 reference notes
-> 35y confrontation -> **1 integrated, 7 rejected, 26 insufficient evidence**.
The 7 rejections are refutations, not data gaps: N runs 44-122 with scores
0.38-0.52. The survivor is the inverted yield curve (market_score 0.88, N=8).
One weighted invariant could not be measured at all (no benchmark for its
handle).

That funnel — 1822 passages for 1 integrated principle — IS the ratio this
STOP asks about. Judge it before M8.

---

## M8 — Planner + Worker + gates + first full chain (2 d — Phases 4, 5, 6)

Baseline + 1a/1b + Worker + Call 2 guardrail + Writeback gates,
`outcomes.py` (proposal verdicts, calibration, probation — fixture-tested
now, armed by real time at M11) + scoreboard render, full Monday chain on
fixtures (UC3 event watch not built yet → the chain SKIPS it until M9),
digest rendered in terminal.

**Definition of Verified**
- [x] simulated Monday on fixtures end to end (`test_simulated_monday.py`)
- [x] bear-shift fixture (+35pts) → reallocation proposal passes gates
      (`test_decision_cycle.py::test_bear_shift_reallocation_passes_gates_and_persists`)
      — VERIFIED AT M8 AND SINCE RETIRED: ADR-012 deleted the cognitive
      reallocation, so the test went with it. The checkbox stays ticked because
      it records what was true when the milestone closed; the gate arithmetic it
      pinned now lives in `tests/test_gates.py`, exercised by the bridge's
      replay.
- [x] Call 2 downgrades an unevidenced verdict to neutral
      (`test_planner_post.py::test_unevidenced_verdict_is_downgraded_to_neutral`)
- [x] digest readable and complete (`test_digest.py::test_render_is_complete_and_readable`,
      `::test_build_digest_renders_from_the_db_alone`)

**✅ Blocker CLEARED 2026-07-21 — ADR-008.** The `proposal` table was shaped
for the ranked defender/challenger duel ADR-007 superseded. Owner chose option
(a): `defender_rank` and `gap` are now NULLABLE and `proposal_type` accepts
`'market-signal'`. NULL states plainly that rank and gap do not apply, rather
than overloading `gap` with a signal state that every later reader would have
to decode. Applied to the live DB (the table was empty — 0 rows — so it cost
nothing, and pre-go-live `CREATE TABLE IF NOT EXISTS` absorbs it without
opening the numbered-migration convention). Readers must now branch on
`proposal_type` before trusting either column.

**✅ Watch CLEARED 2026-07-30 — gate 6 stays `integrated`-only.** Measured on
the live DB with the production predicates (`cited_invariant_eligible` +
`condition_active_now`), not estimated. **The gate itself was deleted on
2026-08-14** with the rest of the citation apparatus (ADR-012 left the Worker no
field to cite from). The measurement below did NOT die with it: it is why the
Planner still shows the Worker `integrated`-only invariants, and it is the
finding any future citation channel starts from rather than re-litigates.

- The corpus is 253 rows but **209 are UC4 curator notes flagged `reference
  knowledge: no effect to measure`** — empty condition, never confronted, not
  confrontable by construction. The **measurable corpus is 44**: 4 `integrated`,
  11 `rejected`, 29 `proposed`. The "1 of 42" figure above was a pre-maturation
  snapshot.
- **Citable today under the strict rule: 2** (`inv-low-real-yields-favor-gold`
  score 0.65 / N=82, `inv-high-inflation-equities` score 0.64 / N=44). The other
  two `integrated` are dormant right now, not disqualified. Gate 6 is
  satisfiable — the near-unsatisfiability fear does not survive measurement.
- The clause that actually binds is **ACTIVE-now (16/44)**, not `status`
  (`N >= N_min` passes 42/44, `score >= θ` 11/44). Loosening `status` therefore
  buys much less than it looks.
- **Loosening by WEIGHT is rejected outright**: it takes the citable set to
  218/253 (184 even at a 0.50 bar), because `weight_effective` is dominated by
  the author-tier FLOOR (0.40 for dalio) and the 209 unmeasurable notes have an
  empty condition, so they read as permanently active. Weight measures tier, not
  evidence — that variant would let a real allocation move lean on never-measured
  prose.
- The evidence-shaped variant (`proposed` + `score >= θ` + `N >= N_min` — clears
  the effect-size test, fails only the binomial tail) is principled but currently
  **empty: +5 measurable candidates, all dormant today, 0 change to the citable
  set.** Revisit when the corpus has matured, not now.

Owner decision: keep the strict spec. Method of record, to re-run at M8b/M11:
open the live DB read-only, exclude the `reference knowledge` rows, then apply
`gates.cited_invariant_eligible` and `context.condition_active_now` verbatim —
the gate itself, never a paraphrase of it — and report the clause-by-clause
funnel (integrated / weight / score>=theta / N>=N_min / ACTIVE-now) rather than
the single final count, which hides which clause binds.

**Still open, separate from gate 6** — those 209 `reference knowledge` rows are
permanently `proposed`, which contradicts ADR-006's "nothing stays proposed
forever". They are not confrontable, so no maturation can ever resolve them:
they need a distinct status (`reference`) rather than sitting in the weighted
pool and skewing every corpus count. Not a gate-6 problem; queued as its own
decision.

**✅ Coherence pass 2026-08-02/03 — four verdicts the system computed and then
dropped, plus the gates that were not gates.**

- `outcomes.paper_test_progress` resolved the incumbent off the defender's
  snapshot, but a market-signal proposal's `defender_id` is a BOOK, and the
  books are `enabled = 0` (ADR-009) so they have no snapshot: every live
  paper-test reported `excess: None` forever. It also had NO caller — the
  scoreboard printed a count where Task 6bis.1 asks for proposed-vs-incumbent
  to date. Both closed; the +12w verdict and the weekly tracking now share one
  resolver.
- The digest's demotion warning read a `demoted` key no writer ever produced,
  so it was unreachable in production and only ever fired on a hand-built test
  dict. Derived now from `snapshots.is_demoted`; on the live DB it immediately
  printed two rows it had always been silent about.
- The user-drawdown exclusion flag (Phase 5bis spec, EXAMPLE.md Step 8B) did
  not exist. Now a STORED column — the cap already moved once (-15 -> -25) and
  the digest re-renders past Mondays, so deriving it would re-judge July under
  December's rule. Applied to the live DB by ALTER TABLE (31 rows, all NULL).
- A short leg and a NaN weight both passed all five reallocation gates: every
  gate is a comparison and every comparison against NaN is False, while a short
  leg is arithmetically ordinary. `hypothesis` was added on the owner's
  go-ahead and found, on its first run, that guarding the target was half the
  fix — gates 3 and 4 measure AGAINST the incumbent, so a NaN in `current`
  blinded them identically. Same hole on the market-signal path, where it would
  have made a real book rotation emit no proposal at all.

Left open with a trigger: **I-47** (the replay's caps ignore per-portfolio
rules, so it is looser than live Writeback) and the live DB being behind the
seed (the 3 `ms-*-book` rows are still `enabled = 1`; a re-seed fixes it and
changes the ranking).

**⚔️ Challenge:** Worker reasoning quality; are the 12-15 selected
invariants the right ones?

---

## M8b — Agentic replay: the pre-go-live screen (Task 9.4) — STOP POINT

The SAME harness as M6 with the cognitive cycle running — the live chain
accelerated at historical dates, no reimplemented decision loop. Because the
corpus is known from t=start it is a **best-case** run: a NECESSARY a-priori
screen, never a sufficient proof (semi-PIT; real-time performance is forward
paper-mode). Episodes cadence (~21 LLM cycles) to bound cost.

**ONE CHANNEL, since ADR-012 (2026-08-09).** This milestone used to ask two
questions — "does A' beat All Weather?" and "does the Worker reason sensibly?"
— and weighted them equally. The first is gone with the cognitive allocation it
measured: the Worker does not allocate, so there is no A' to price. What
remains is the question that paid.

**Definition of Verified**
- [ ] behavioural log readable: at 2008 / 2020 / the inflation shock, does the
      Worker reason sensibly? does it propose sensible improvements? This is
      the box the OWNER must READ — no number stands in for it
- [ ] the innovations are harvested and outlive the run
      (`report.innovations.json`), so a `strategy_revision` can be measured by
      ADR-006 rather than admired once
- [ ] failed dates are counted and loud in the report — an episode that lost
      three of seven decisions is a different claim from one that lost none
- [ ] `test_agentic_replay_semipit`: invariant weights read as-of-t; a
      confrontation dated after t changes no weight before t; agent-discovery
      absent from the run

**⚔️ STOP:** if the Worker's reasoning reads incoherent, or its proposals are
not the kind of thing ADR-006 could ever measure, do NOT proceed to live. The
mechanical premise gate is M6's and is unaffected — it never involved the
Worker.

**What the runs of 2026-08-08/09 found**, kept because it is the evidence
ADR-012 rests on and the reason this milestone shrank: the NAV channel returned
seven-month deltas that were noise as much as signal, while the readings
produced specific, code-checkable critiques of the mechanical rule — including
the one that found `max_single_asset_pct` freezing the stack in a stale book
through the whole 2022 drawdown. Archived under
`~/data/investment/agentic-replay/`.

---

## Corpus health — the invariant population, corrected 2026-08-07

The M8b run showed the Worker unable to cite an invariant in two regimes of
three, and "209 of 253 invariants have never been confronted" looked like the
cause. It was not. **They have no `effect`** — no falsifiable consequence — so
nothing can measure them, whatever their condition. The correlation is total:
209 of 209 without an effect are unconfronted; 43 of 44 with one are confronted.

They are REFERENCE KNOWLEDGE misfiled as `proposed`. `mature_invariant` already
demotes them (`REFERENCE_STATUS = "reference"`, a terminal state whose comment
reads "filed as 'proposed' it was an open promise the system could never
keep") — the sweep had simply never run over them. All 209 date from a single
curation pass on 2026-07-21, the day `InvariantCandidate` was fixed to require
`condition` and `effect`. The upstream hole was closed then; the rows it
produced stayed.

`mature_seed_invariants` re-run on the live DB (backup:
`investment.db.bak-pre-reference-20260807`):

```
before   integrated 4 · proposed 238 · rejected 11
after    integrated 4 · proposed  29 · reference 209 · rejected 11
```

**What this changes about the M8b reading.** The corpus is not 2 citable of 253.
It is 44 confrontable claims, of which 4 have earned integration and 29 are
still accumulating evidence — an ordinary maturation funnel. The citability
drought at gate 6 is real but its cause is narrower than it looked: not a
corpus full of dead weight, but a small confrontable population whose
integrated members happen to be inflation-shaped, hence dormant in a
deflationary crisis.

Reference knowledge is not lost: it still reaches the Worker through the same
corpus, embedding and semantic search. Only its verdict lifecycle differs —
and ADR-006's "nothing stays proposed forever" no longer has 209 open promises
it cannot keep.

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
- [x] **re-seed first** — done, see the closed item below

**BUILD COMPLETE 2026-08-12. Every DoV item above is a fact about a week of
wall clock, not about code**, so they stay open until the agent has run one.
What exists now:

- `main.py` — the process: inbox watcher (with its post-batch curation, backup
  and message to the owner), Sunday 08:00 cron, 5-minute heartbeat,
  due-on-start, graceful shutdown, and the `flock` that refuses a second agent
  on the same database. Cron AND heartbeat, deliberately: ADR-002's laptop
  sleeps, so a cron on a closed lid never fires; both go through
  `chain_if_due`, guarded by `detector_state.last_chain_success` and the
  run-lock.
- `weekly.py` — the chain, **12 steps**, complete: catch-up → UC3 event watch →
  UC4 curation sweep → backtests → scenarios → invariant weights → valuations →
  ranking → outcomes → market-signal decision → UC8 → digest. (It said 10 and
  listed 12, which is the count drifting from the thing it counts — the list is
  authoritative, as `weekly.weekly_steps` is authoritative over both.) A failed
  run is retried WHOLE, so the mechanical steps recompute on fresher prices;
  UC8 alone is guarded against a second pass in the same week
  (`weekly.weekly_cycle_already_ran`), because it is the only step that spends
  an LLM call and appends fresh journal rows every time it runs.
- `mechanical/catchup.py` (UC1), `watch/event_watch.py` (UC3),
  `corpus/curation_sweep.py` (UC4's weekly pass) — the three jobs the chain
  needed and did not have.
- `ops/commands.py` + `ops/run_lock.py` — ADR-005's one command layer and the
  single-flight lock; `telegram/bot.py` is its first front.
- `telegram/notify.py`, `db/backup.py`, `runtime.py`,
  `deploy/com.jp.investment-agent.plist` + `deploy/README.md`.

Not built, each with the reason written where it would have gone: the ACCEPT/
REJECT proposal buttons and `proposal_expiry_days` (ADR-006 removed the user
gate they belong to), the conversational chat (the note-vs-question
disambiguation is an owner decision; the note channel works meanwhile), and
UC3's bounded-domain fetch (it needs an HTML-to-text extractor the project does
not have). The go-live gate of Task 9.3 was retired by ADR-013.

**THE FIRST START IS A SUPERVISED ACT.** The chain is DUE on a database that
has never run one, so it emits the stack's opening entry — a real order — spends
a cognitive cycle and sends the digest. `deploy/README.md` says so at the top.

**✅ CLOSED 2026-08-03 — the live DB's strategy prose is current.** The
carried-over item (deferred 2026-08-01) warned that three Worker-visible fields
on `market-signal-stack` held superseded text: `description` naming the books
`growth/inflation/slowdown`, and `conditions`/`trace` predating the fourth
addendum. The re-seed that closed I-48 fixed all three in passing — SeedEvent
dated 2026-08-03, backup `investment.db.bak-pre-reseed-20260803`.

Verified on the live DB 2026-08-05, before M8b: `description` names
`credit-spread-wide` / `credit-spread-tight-yield-curve-flat` /
`credit-spread-tight-yield-curve-steep`; `conditions` carries the confirmation
rule ("switch confirmed over 3 monthly decisions"); `trace` reads
11.26%/yr, Sortino 1.11, maxDD -23.8% and labels 9.85% as the un-damped pair.
The three book Portfolios carry the new names too.

Left here rather than deleted, because the warning outlived its cause by two
days and nearly bought a second 35y backfill for nothing: **check the row before
re-seeding on the strength of a note.**

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
