# V1_STRATEGY.md — the adopted strategy + migration plan

> **OVERLAY COMPLETION, 2026-08-07 (owner-approved).** The 200d overlay now
> checks EVERY risky sleeve (IWN joined SPY and GLD) and checks the HAVEN it
> redirects into — when IEF is itself below its 200d line the redirect goes to
> cash. Both came from the M8b Worker, found independently in two runs; the
> haven half is the market-priced form of its own proposal, which had gated the
> haven on CPI (refused: the stack reads prices only). Measured 4-way in one
> process: 11.10% -> 10.71% CAGR, Sortino 1.09 -> 1.17, maxDD -23.78% ->
> -20.61%. Neither change is worth much alone — the haven check does NOTHING on
> its own — and together they cut the drawdown by 3.2 points.
>
> **Superseded again on 2026-08-11** (owner signature): the two spread-trajectory
> knobs are ON at 0.20, and the trend window moved 200 -> 300 the same day,
> taking the pinned pair to **11.57% / -20.61%**, Sortino 1.17 -> 1.30, turnover
> 61.1 -> 53.7. Same provenance for the knobs — the Worker's most repeated
> critique, measured out of sample as well as in; the window came from asking
> what had never been measured (`mechanical/market_signal.py`).


**Status: ADOPTED as the V1 candidate (owner decision, 2026-07-19).**
Backtest-validated only. "Adopted" means it is the strategy the code will be
wired to and that FORWARD PAPER-MODE will validate — NOT that it is proven.
The M6 premise-gate philosophy still governs: real evidence is forward, not
backtest. This pivots the project's operating strategy away from the seeded
Dalio 4-quadrant portfolio-rotation toward a Verdad-style countercyclical
market-signal stack (docs/Countercyclical+Investing; full comparison
docs/STRATEGY_COMPARISON.md).

## The adopted stack

**Regime signal (market-priced, contemporaneous — replaces the lagged CPI/GDP
detector for allocation):**
- credit spread `BAA10Y` vs its 10y trailing median: WIDE → `credit-spread-wide`;
- if TIGHT, slope `T10Y2Y` vs its 10y trailing median: FLAT → `credit-spread-tight-yield-curve-flat`,
  STEEP → `credit-spread-tight-yield-curve-steep`.

Books are named after the SIGNAL STATE that selects them, never after a macro
regime (ADR-007 third addendum, 2026-07-20 — renamed from
growth/inflation/slowdown). Measured, the signal is orthogonal to CPI: the book
formerly called "inflation" averages CPI YoY 2.99 vs 2.23 for the one formerly
called "growth" (docs/IMPROVEMENTS.md I-39). The seeded entity IDs keep their
original spelling because EventLog is append-only.

**Books (concentrated pure-asset tilts; 50% sleeves — the single-asset cap was
raised 40→50 for exactly this concentration, ADR-007 addendum 2026-07-20):**
- `credit-spread-wide`: SPY 50 / IWN 40 / GLD 10
- `credit-spread-tight-yield-curve-flat`: SPY 50 / GLD 40 / IWN 10
- `credit-spread-tight-yield-curve-steep`: VCIT 50 / IEF 40 / IWN 10

**Trend-following overlay:** every CHECKED sleeve — SPY, GLD and IWN since
2026-08-07 — is redirected to IEF by the FRACTION of its moving averages it has
fallen below (GRADUATED since 2026-08-14: two lines, so a sleeve is held in
full, half out, or fully out), and IEF is checked too: when the haven is below
ALL of its own lines the destination becomes cash. (This is the drawdown control; it carries -24% →
without it the stack is -50%.) In risk-off several sleeves can redirect at once,
concentrating the haven to ~90-100%; the haven CHAIN — IEF and the cash fallback
— is therefore EXEMPT from the single-asset cap (ADR-007 addendum choice (a),
extended to cash 2026-08-08 — it is a deliberate safety redirect, not a
conviction bet).

The WINDOW is deliberately not named here. It is a measured knob
(`MA_WINDOW_DAYS`, 300 since 2026-08-11 and 200 before it), and every copy of it
in prose has gone stale within a day of it moving; `mechanical/market_signal.py`
holds the value and `describe_rule()` states it to the Worker.

**Cadence: MONTHLY.** No VIX crisis overlay (measured to hurt at monthly
cadence: 7.57%/-25%).

**Backtest performance. EVERY FIGURE BELOW PREDATES THE 2026-08-13 LOOK-AHEAD
FIX and is optimistic by roughly a point a year** — see "The look-ahead" at the
foot of this file. They are not comparable to anything measured after that date.
Read "The attribution" too before quoting the "+3.80 vs B" as the strategy's
edge: B is a PASSIVE benchmark with zero turnover, so it never earned any of the
bias the stack did, and the margin absorbs the whole of it.

The CURRENT pinned pair is **11.57% CAGR / -20.61%**
(Sortino 1.30, turnover 53.7), and `mechanical/market_signal.py` is its
authority — it carries every supersession with its date, its measurement and its
owner signature. Do not restate it here: this file has quoted a superseded pair
as "the" performance three times.

The table below is the **2026-08-02 state, kept as history** (live DB, 1991-2026,
net of Saxo's real 23 bps per order, vs B = risk-parity All Weather, B net of the
same cost, ADR-010). Three changes have landed since — the overlay completion
(2026-08-07), the two trajectory knobs and the 300-day window (both 2026-08-11):

| | value (2026-08-02, SUPERSEDED) |
|---|---|
| CAGR | 11.14%/yr (+3.80 vs B, both sides net — both halves of the split independently) |
| Sortino | 1.09 (B: 0.92) |
| Max drawdown (daily) | **-23.8%** (breached the -15% rule then in force; the cap is -25% since ADR-007 and the drawdown is -20.61% today, i.e. compliant) |
| Changes | 2.8/yr |
| Fee drag | **0.66 pt/yr** measured (gross 11.80% → net 11.14%) |

Those were the post-hysteresis numbers (ADR-007 fourth addendum, 2026-08-01:
`CONFIRM_DECISIONS = 3` — a book switch waits for 3 confirming monthly
decisions), restated under ADR-010's single cost rate. The pivot was signed on
the un-damped **9.85% / 0.94 / -24% / 3.4 changes-a-year** and the hysteresis
addendum on **11.26% / 1.11**, and the interim 10 bps/side estimate on
**11.51% / 1.14**; all are history, not targets. As of that date neither the
books, the signals, the overlay nor the caps had changed — and that sentence
stopped being true five days later: the OVERLAY has changed twice since
(2026-08-07 completion, 2026-08-11 window) and the CAPS once (-15% → -25%,
40% → 50%). The books and the two signals are still exactly as ADR-007 signed
them.

The "~1.5 pt/yr fee drag" this table used to claim was wrong by a factor of ~2:
the measured drag at the REAL rate is **0.66 pt/yr**. The rate is no longer an
assumption — 23 bps is the owner's actual Saxo per-order commission, with no FX
leg, since every portfolio here is USD held in a USD account. The stack trades
~4.6x more than the static books (1.21 vs ~0.26 sum|dW|/yr), so it carries ~10x
their fee drag; that asymmetry is real and now fully priced in, and the +3.80
edge survives it.

## Why monthly

Weekly and monthly give the SAME return (9.83 vs 9.85) and drawdown (-24%),
but monthly cuts changes 8.2 → 3.4/yr and quadruples the median holding period
(14 → 61 days). This serves three pressures at once, at ~no return cost:
- **fees** — fewer round-trips (Saxo per-order commissions hit small trades);
- **Swiss tax status** — longer holdings move toward the 6-month private-
  investor safe harbor (Circular 36) that protects the 0% capital-gains
  exemption; see OPEN #2;
- **manual execution** — ~3-4 order events/year instead of ~8 with clusters.

## What changes (projected) — by area

Legend: [done] already deployed · [new] to build · [decide] owner call.

- **A. Data / signals — [done].** BAA10Y (credit spread), T10Y2Y (slope), IWN
  (small value), VCIT (IG credit) are seeded and in the live DB. Trailing
  medians and the trend moving averages are computed at decision time (no new
  series).
- **B. Regime classification — [new].** A market-signal regime module
  (credit-spread + slope → growth/inflation/slowdown). Replaces the macro
  detector *for allocation*. The existing CPI/GDP detector can stay for the
  regime graph / invariant conditions, but the allocation decision keys off
  the market signal. (This is I-38, now committed.)
- **C. Strategies / Portfolios (seed) — [new].** Seed the 3 market-signal books as
  Strategy/Portfolio entities. The 7 seeded books are retained as BENCHMARKS
  (B, SPY-proxy, etc.) or retired — [decide].
- **D. Trend-following overlay — [new].** A mechanical module: SPY/GLD sleeve
  → IEF below its moving average, evaluated monthly.
- **E. Cadence weekly → monthly — [new] + [decide-ADR].** CLAUDE.md pins a
  weekly Monday chain (ARCHITECTURE.md). Moving the DECISION cadence to
  monthly is a scheduling change that needs an ADR amendment. Note: the
  catch-up / regime-step / NAV jobs can stay at their natural frequency; only
  the allocation DECISION goes monthly.
- **F. Decision logic (UC8) — [new].** regime → book selection + trend
  overlay, run through the EXISTING binding-cap gates (max_single_asset_pct,
  drawdown rule) in `mechanical/gates.py`. The switch/reallocation-blend path
  is superseded for this stack.
- **G. Docs / ADRs — [decide].** An ADR recording the framework pivot (Dalio
  4-quadrant macro → market-signal countercyclical), CLAUDE.md update,
  MILESTONES revision. "Never contradict an accepted ADR silently" — this
  needs explicit sign-off, not a silent rewrite.

## Impact map — the crossroads is at STRATEGY level, not INFRASTRUCTURE

**KEPT intact (the plumbing — reused unchanged):** market-data pipeline
(Yahoo/FRED + ALFRED vintages + splices, M2), SQLite/EventLog/InvestmentDB
(M1), NAV synthesis `ratios.py` (M4), the replay + calibration harness
(M6 — now used to validate the wired stack against the scratchpad numbers),
the binding-cap gates `mechanical/gates.py`, the corpus/invariant factory
(M7), the Planner/Worker architecture (M8). ADR-002/003/004/005/006 are all
unaffected. M6's mechanical premise-gate verdict is unchanged.

**DEMOTED / superseded (the OLD cognitive core — kept as fallback + benchmark,
NOT deleted until forward paper-mode earns the switch):**
- **Macro regime detector (M3, STOP point).** Its CPI/GDP quadrant output no
  longer drives ALLOCATION — the market-signal regime does. It survives for
  invariant `condition` evaluation and as a monitoring/regime-graph view.
- **FAVORS (M5).** Not used by the new stack at all — consistent with I-35
  (FAVORS' per-regime ranking is noise). Retire from the decision; keep only
  if still wanted for invariant confrontation.
- **UC7 portfolio ranking / `portfolio_weekly_snapshot`.** The new stack
  selects a book by regime, it does not rank-and-switch. Ranking becomes a
  monitoring instrument, out of the decision path.
- **UC8 switch gates + the 0.4·scenario + 0.6·favors reallocation blend.**
  Superseded by regime→book + trend overlay (still run through the SAME
  binding-cap gates).
- **Scenarios (bull/base/bear per strategy) + scenario probabilities.** Out of
  the decision; the credit-spread/slope regime replaces the scenario read.
- **The 7 seeded Dalio portfolios.** Demoted to BENCHMARKS (B, etc.).
- **Weekly Monday decision cadence.** → monthly for the ALLOCATION decision
  (catch-up/NAV/regime-step jobs keep their natural frequency).

**NEW to build:** the market-signal regime module (credit-spread + slope), the
3 market-signal books (seed), the trend-following overlay, the monthly decision
path. (Data for all of it is already deployed — area A.)

**DOC / ADR surface (needs explicit owner sign-off — "never contradict an
accepted ADR silently"):** a new ADR recording the framework pivot (Dalio
4-quadrant macro → market-signal countercyclical market-signal) + monthly cadence;
then revisions to CLAUDE.md (the weekly-chain, FAVORS, ranking-rule, regimes
sections), ARCHITECTURE.md, USE_CASES.md (UC7/UC8), DATA_MODELS.md, and the
MILESTONES roadmap. The knowledge factory (corpus/invariants/Worker) is
FRAMEWORK-AGNOSTIC — it validates market beliefs mechanically — so it survives
the pivot; only what it ORIENTS changes.

## Future steps (roadmap) — ordered, with the crossroads discipline

**Step 0 — record the decision FIRST.** Write the framework-pivot ADR + the
CLAUDE.md/docs revisions before touching code (an accepted ADR must not be
contradicted silently). This is where OPEN #3 is signed off.

**Step 1 — M6-bis: wire the stack, keep the bridge.** Move it from scratchpad
to production: seed the 3 books, build the market-signal regime module + the
trend overlay + the monthly decision path through the existing gates.
**Replay-validate it reproduces 11.14%/-23.8%** (anti-drift check — the same
guarantee that caught the M6 rebalance bug; 9.85% was the pre-hysteresis pair,
ADR-007 fourth addendum). Ship an inspection view.
CRUCIAL: **do NOT delete M3/M5/UC7-8** — keep the old design wired as
fallback + benchmark until forward paper-mode earns the full switch. Passing a
crossroads ≠ burning the bridge.

**Step 2 — resolve the two owner constraints (OPEN #1, #2)** before go-live:
the -15% rule and the Swiss tax status. If -15% must hold, the drawdown brake
that survives MONTHLY cadence is TBD — tested in PAPER-MODE, not more backtest.

**Step 3 — M7 corpus + invariant factory** (build unchanged; re-pointed): the
factory is framework-agnostic; its invariants now orient the credit/slope
signals instead of the 4 quadrants.

**Step 4 — M8 Planner + Worker + gates.** The Worker nuances the monthly
regime/book decision (qualitative reading of the credit-spread/slope state).
**Wired 2026-08-02:** the decision itself runs MECHANICALLY
(`market_signal_cycle.py`, 08:55, before UC8) and its full record — signal
values vs their trailing medians, the dates each input became knowable, the
hysteresis position, the sleeves below their moving average — is passed into the
Planner baseline and rendered into the Worker's context.

**"Nuances" is defined by ADR-011, and it is narrower than the word suggests.**
The mechanical allocation is SOVEREIGN: the Worker can neither cancel it (no
data path exists back into the cycle), nor delay it, nor adjust its weights
(gate 0 of `dispose_reallocation` refuses any cognitive reallocation aimed at a
`TIME_VARYING_PORTFOLIOS` row — until 2026-08-02 that separation held only
because `ms-stack.defender` happens to be False, which **this Step 6 was
scheduled to flip**). What the Worker contributes is the qualitative reading —
where the decision looks wrong, what the signal cannot see, which lighthouses
argue against it — journalled and rendered, never applied. A disagreement with
the INSTRUMENT rather than with the month goes through `innovations_proposed`
(`strategy_revision`), the audited channel that matures mechanically under
ADR-006. Letting an LLM alter a mechanically-validated allocation is exactly
the drift `mechanical/market_signal.py` exists to prevent.

**Step 5 — M8b agentic replay** (best-case screen), same harness.

**Step 6 — M9 go live in PAPER-MODE (forward). THE real gate.** Accumulate
forward evidence over ~6-18 months, each proposal scored at +12w. The +2.5
edge either reproduces on unseen data or it was in-sample. Only here does the
stack stop being a hypothesis. This is also when the old-design bridge can be
retired, if the forward evidence holds. **Two things must be done in that same
commit** (both recorded when they were found, so they cannot be rediscovered
late): give the stack its own trading calendar — today it borrows the bridge
defender's NAV index, so the adopted strategy cannot run without the bridge —
and re-validate the pinned pair. Making the stack the defender is safe on the
allocation side: ADR-011's gate 0 keys on `TIME_VARYING_PORTFOLIOS`, not on the
defender flag. **But it leaves the Worker with no legal allocation target at
all** — UC8-B becomes unreachable in full. Delete it, demote the bridge instead
of retiring it, or give the Worker a capped satellite sleeve: the three futures
are specified in docs/IMPROVEMENTS.md I-46 and must be settled in THIS plan,
not during it. Also close I-45 (+12w scoring is gross) before this step: from
here the outcome ledger stops being fixtures and becomes the go-live evidence.

**Step 7 — V2 auto-execution**, only after forward validation earns it.

## OPEN owner decisions

1. **The -15% drawdown rule.** The stack is -24% (breaches it). For a 10-15y
   ACCUMULATION horizon a deep-but-recovering trough is tolerable (drawdowns
   are buying opportunities with time to recover), which argues for accepting
   it — but it conflicts with the binding cap in `user_profile`. Keep the rule
   (→ needs a monthly-compatible brake, TBD) or relax it for accumulation.
2. **Swiss tax (CTO Saxo).** Median 61-day holding still < the 6-month private-
   investor safe harbor (Circular 36), so a quasi-professional reclassification
   risk remains (would tax all gains as income + AVS vs 0%). Confirm with a
   Swiss fiduciaire; if strict, go quarterly or regime-only (holds longer) or
   use a PEA-equivalent wrapper (tax-free internal rebalancing).
3. **Framework-pivot ADR** (area G) — sign off before the doc rewrites.

## Honest caveats

Everything above is BACKTEST on 1991-2026, a window consulted heavily this
session. The +2.5 edge leans on small-cap value (IWN), a factor that can
underperform for a decade. Real drawdowns exceed backtested minima. The only
validation that counts is forward paper-mode (step 6). Adopting the stack sets
the target; it does not prove it.

**Timeline is SLOW by design (owner note, 2026-07-20).** Forward paper-mode
takes ~6-18 months to yield a statistically meaningful verdict (each proposal
scored at +12w; a monthly-cadence stack generates few proposals). Expect
nothing quickly — and that is the right pace for a 10-15y accumulation-horizon
project. There is no urgency to reach a verdict; the discipline is to let real
unseen data accumulate rather than to conclude fast. The build work (M6-bis →
M8b) proceeds meanwhile, but the go-live verdict waits on time it cannot rush.

---

## What the knowledge factory proposed, and what measuring it said (2026-08-09)

Two days of M8b runs produced **25 distinct innovations** across ~40 readings,
each one appearing twice or more from independent dates and runs. Nine named
knobs `rule_revision.TESTABLE_PARAMETERS` can move, which reduce to **five
distinct experiments** once deduplicated on the override set — five differently
worded proposals resolving to the same constants are one experiment, and paying
for the 35y walk five times would not make the answer truer.

Measured over 1991-2026 by `measure_revision`, judged by the acceptance test the
proposing Worker itself wrote (adopt only if Sortino does not degrade AND max
drawdown improves). Baseline: sortino +1.173, maxDD -20.61%, CAGR +10.72%.

    override                                 sortino   maxDD delta   verdict
    trend_haven=GLD, fallback=IEF             -0.154        -2.92pp  reject
    trend_haven=GLD, fallback=cash            -0.238        -5.11pp  reject
    confirm_decisions=2 (was 3)               -0.010         0.00pp  reject
    trend_sleeves += VCIT                     -0.010         0.00pp  reject
    trend_haven=cash                          not expressible: the primary
                                              haven is trend-CHECKED, so it
                                              needs a price series

**ALL FOUR REJECTED.** The gold-haven family — the Worker's most persistent
allocation intuition, proposed five ways across independent dates — makes the
35-year drawdown three to five points DEEPER. This is ADR-006 doing exactly what
it exists for: belief does not grant integration, history does. It cost minutes
of compute, no LLM call, and no capital.

**The pattern worth carrying forward**, and it is the same one ADR-012 acted on
from the cost side: the Worker's DIAGNOSTIC readings are excellent — one of them
found `max_single_asset_pct` freezing the stack in a stale book through the
whole 2022 drawdown, a real defect the mechanical half could not see about
itself — while its PRESCRIPTIVE allocation intuitions do not survive
measurement. Read it for what the instrument cannot see; measure anything it
proposes before believing it.

**The most repeated critique is not in the table**, because no knob expressed it
until `spread_speed_veto` (below): six distinct formulations of "the book is
selected on the LEVEL of the spread against its trailing median, and should be
selected on its TRAJECTORY" — *"the level-vs-median read stays 'tight' longest
precisely when widening is fastest, because the median trails and the level
starts low"* (2008-08-01, and again independently at 2020-03-02).

Raw output: `~/data/investment/agentic-replay/rule-revisions-measured-*.txt`.

### A free measurement of the stack against the bridge

The on-stack run of 2026-08-09 priced its walk on the stack's own monthly
target and accepted no cognitive tilt, so its A' curve IS the stack. Over
global-financial-crisis (2008-07 .. 2009-01):

    market-signal stack   CAGR +16.95%   sortino  2.48   maxDD  -4.60%
    the bridge's rules    CAGR  -4.05%   sortino -0.51   maxDD -10.38%
    All Weather held      CAGR -17.00%   sortino -1.74   maxDD -15.14%

ONE seven-month episode, so a datapoint and not a verdict — but it is a clean
one, and it bears directly on Step 6 (retiring the bridge): the 200d overlay cut
the drawdown by two thirds against All Weather in the worst equity quarter since
1929. Recorded because it came free and would otherwise be lost in a run log.

### The most repeated critique, measured — and what its failure revealed

`SPREAD_SPEED_VETO` (2026-08-09) makes it testable: defer the risk-on
`credit-spread-wide` book while the spread is still widening faster than a
threshold. OFF by default, so ADR-007's validated numbers are untouched until
something earns a change. Swept over 1991-2026:

    veto (pp/30d)   sortino            maxDD                cagr     verdict
    0.00            +1.173 -> +1.056   -20.61% -> -20.61%   +9.35%   reject
    0.05            +1.173 -> +1.145   -20.61% -> -20.61%  +10.11%   reject
    0.10            +1.173 -> +1.229   -20.61% -> -20.61%  +10.95%   ADOPT
    0.20            +1.173 -> +1.244   -20.61% -> -20.61%  +11.10%   ADOPT
    0.40            +1.173 -> +1.232   -20.61% -> -20.61%  +11.11%   ADOPT

It is the ONLY proposal of the five families that improves anything: at 0.20,
Sortino +0.071 and CAGR +0.38pp for an unchanged drawdown. Under the ORIGINAL
acceptance test it was refused at every threshold, because that test required
the drawdown to IMPROVE and the drawdown does not move — not by a basis point.
Under the Pareto rule that replaced it (below) it is adopted from 0.10 up, and
still refused at 0.00 and 0.05 where it genuinely degrades.

**THAT IMMOBILITY IS THE FINDING.** The stack's worst drawdown is 2020-03-20,
and through the whole covid crash the book in force was already
`credit-spread-tight-yield-curve-flat`; it switched to `credit-spread-wide` only
in June, after the bottom. The veto defers a WIDE reading, so it is inert
exactly where the drawdown is set. The -20.61% is not a property of BOOK
SELECTION at all — it is the 200d overlay's latency, SPY still reading
above-trend on the 2020-03-02 decision and below only by 04-01, nine days after
the trough.

So the acceptance test — "Sortino not degraded AND max drawdown improved" — is
structurally an OVERLAY-ONLY filter. No book-selection revision can ever pass
it, whatever its merit, because book selection does not touch the number the
test is keyed on. Both revisions it has ever adopted (2026-08-07: IWN
trend-checked, the haven trend-checked) were overlay changes, which is now
explained rather than coincidental.

**Settled the same day (owner decision 2026-08-09): the acceptance test is now
PARETO** over the four indicators — adopt iff at least one improves and none
degrades. Rule #1 is intact, since nothing may get worse and a revision buying
return with drawdown is still refused on the spot; what changes is that a
revision improving return at UNCHANGED risk becomes expressible as an adoption
instead of being refused without the test being able to say why. The
generalisation is the owner's: it holds for ANY indicator that does not degrade,
not only for the drawdown.

Implementing it needed a measured noise floor, and the number is measured
rather than chosen. Two things move these indicators without the strategy
changing: float noise (the veto reached the SAME covid trough by a different
arithmetic path — a delta of -2.2e-16, which an exact comparison called a
degradation) and, much larger, the ground itself. Shifting the replay's start
date across 1991 changes nothing about the strategy and moves it this much over
twelve starts:

    indicator      spread   stdev
    sortino         0.71%   0.20%
    cagr            0.54%   0.15%
    calmar          0.54%   0.15%
    max_drawdown    0.00%   0.00%

Corroborated independently: the 2026-08-03 re-seed moved CAGR 0.36% with 418
IDENTICAL decisions (I-48). `NOISE_REL_TOL = 0.0071` is the worst observed
spread. Verified to change no verdict on the day it shipped — it only stops the
system ever calling a shift of the ground an improvement.

**A 1% band was considered and refused.** It would have tolerated the 125-day
overlay's Sortino loss (0.94%) and so adopted its 2.75pp drawdown gain — but it
sits inside the 8-basis-point corridor between the measured floor (0.71%) and
the smallest improvement the sweep calls real (+1.02% of Sortino at
ma_window_days=225). 0.011 down and 0.012 up are the same movement; a number
chosen in that corridor decides one case rather than measuring anything.
Whether a small Sortino loss buys a large drawdown gain is a TRADE-OFF, and it
belongs to the owner stated as one, not laundered through a tolerance.

**What is NOT decided: turning the veto on.** `SPREAD_SPEED_VETO` remains None.
The measurement says adopt at 0.10-0.40, best at 0.20; flipping the constant
changes ADR-007's live allocation, and the gate there is git and an owner
signature, not ADR-006 (`rule_revision` module docstring). The evidence is
recorded; the switch is not thrown.


### The overlay's own windows, swept (2026-08-09)

Today's finding said the drawdown belongs to the overlay, so the overlay's
parameters are where a drawdown improvement could come from. `MA_WINDOW_DAYS`
(200) had never been confronted with an alternative.

    ma_window_days   sortino            maxDD                cagr    turnover  verdict
    100              +1.173 -> +1.105   -20.61% -> -18.10%   10.09%      98.8  reject
    125              +1.173 -> +1.162   -20.61% -> -17.86%   10.56%      82.9  reject
    150              +1.173 -> +1.167   -20.61% -> -20.61%   10.79%      71.8  reject
    175              +1.173 -> +1.190   -20.61% -> -20.61%   10.85%      63.1  ADOPT
    200 (current)                                            10.72%      61.1
    225              +1.173 -> +1.185   -20.61% -> -20.61%   10.92%      54.2  ADOPT
    250              +1.173 -> +1.162   -20.61% -> -21.08%   10.86%      52.2  reject
    300              +1.173 -> +1.191   -20.61% -> -20.61%   11.10%      43.6  ADOPT

`median_window_days` at 1260 / 1890 / 3150: all reject, all degrade Sortino.

Best of the adoptions is **300 days** — same drawdown, +0.38pp CAGR, and
turnover down 29% (61.1 -> 43.6), which is a robustness gain as much as a cost
one.

**The refusal is more interesting than the adoptions.** 125 days is the only
setting that materially improves the drawdown: -20.61% -> **-17.86%**, 2.75
points, exactly what rule #1 wants. It is refused because Sortino gives up
0.94%. The ORIGINAL acceptance test refused it too, for the same reason. So no
test in this system can adopt a TRADE-OFF, by construction — and the one change
that would have bought real safety is the one no rule can take. That is a
doctrine question for the owner, not a defect.

Nothing here is adopted. Both the veto and the 300-day window are fitted on a
single 35-year sample and would need an out-of-sample check before the constant
is touched.

### Out-of-sample: which of them were found, and which were fitted (2026-08-09)

Every candidate above was judged on the same 35 years it was fitted on. Split at
2009 — roughly equal halves, and the break falls between two monetary worlds
rather than inside one — `measure_revision(start=, end=)` re-runs the identical
comparison on each.

    candidate               full 1991-2026   first 1991-2008   second 2009-2026
    spread_speed_veto 0.10  ADOPT            ADOPT             ADOPT
    spread_speed_veto 0.20  ADOPT            ADOPT             ADOPT
    spread_speed_veto 0.40  ADOPT            ADOPT             ADOPT
    ma_window_days 175      ADOPT            reject            ADOPT
    ma_window_days 225      ADOPT            ADOPT             reject
    ma_window_days 300      ADOPT            ADOPT             ADOPT

**175 and 225 are sample artefacts** — each adopts on the whole and fails on one
half, fitted to the other. This is what the split exists to catch, and it caught
two of three on its first use.

**The veto survives everywhere**, and the halves say something the full sample
could not: on 1991-2008 it improves the max drawdown by **+2.96pp**. Its risk
benefit is real; it was invisible over 35 years only because the full-sample trough
is covid, where the veto is inert. At 0.20: Sortino +0.180 on the first half,
+0.105 on the second, CAGR +0.76pp and +0.75pp.

**300 days survives too**, thinly — +0.010 of Sortino on the second half, barely
above the 0.71% noise floor.

So the Worker's most repeated critique is the one solid finding of the screen:
proposed six ways from independent dates, mechanically measurable once given a
knob, robust in and out of sample, and worth three points of drawdown in 2008.
Still not switched on — that is git and an owner signature (ADR-007).

### The velocity theme holds three mechanisms, not one (2026-08-11)

Reading the six wordings rather than counting them: they are one INTUITION —
the spread's trajectory carries information its level does not — proposed
through at least three different mechanisms, which are not interchangeable.

    (a) defer the risk-on wide book while the spread is still widening
    (b) ENTER the wide book on speed alone, whatever the level says
    (c) redirect the equity sleeves on spread direction, without waiting for
        the 200d price trend

`SPREAD_SPEED_VETO` expresses (a). `SPREAD_SPEED_WIDE_TRIGGER` expresses (b),
using the Worker's own candidate value. (c) is sleeve-level and still not
expressible.

    mechanism                     full        1991-2008    2009-2026
    (a) veto        0.10          ADOPT       ADOPT        ADOPT
    (a) veto        0.20          ADOPT       ADOPT        ADOPT
    (a) veto        0.40          ADOPT       ADOPT        ADOPT
    (b) trigger     0.10          reject      reject       ADOPT
    (b) trigger     0.20          reject      reject       reject
    (b) trigger     0.40          reject      reject       reject

(b) is dead: nil at 0.20 and 0.40 — by the time the spread widens that fast the
level is already above its median, so entering on speed adds nothing the rule
does not already do — and at 0.10 it contradicts itself across the halves, the
same sample artefact the 175/225-day windows showed.

**The intuition was right and one of its two directions was wrong**, which is
the whole case for measuring rather than debating. The trajectory should DELAY
taking risk, not anticipate it. Nobody could have called that from the prose:
both readings are defensible, the Worker proposed both, and only the 35-year
walk separates them.

**What 0.20 means.** Spread speed is in points of BAA10Y per 30 days — 2.30% to
2.50% in a month. Measured over the 8722 days of history it is essentially the
p90: the veto bites on 8.7% of days (31.9% at 0.05, 20.9% at 0.10, 2.6% at
0.40), and the extremes of the record are 2020-03-24 (+2.22), 2008-10-22
(+1.83) and 2020-04-01 (+1.55). It isolates real credit events and is idle the
rest of the time, which is why the sweep degrades at 0.00 — there the stack sits
in the lighter books permanently.

### Mechanism (c), built and measured — and what switching it on would change

`SPREAD_STRESS_SLEEVE_GATE` expresses what four separate critiques asked for:
the book is SELECTED because credit is impaired and then holds 90% equities,
with nothing but each sleeve's own price trend between the stack and that bet.
When the spread is wide AND widening faster than the threshold, the equity
sleeves go to the haven without waiting for their own 200d.

    gate    full                1991-2008           2009-2026
    0.00    reject (turn 99)    reject              reject
    0.05    reject              reject              reject
    0.10    reject              ADOPT               reject
    0.20    ADOPT +0.052        ADOPT +0.054        ADOPT +0.010
    0.40    ADOPT               reject (nil)        ADOPT

0.20 again — the same p90 the veto settled on, which is coherent: both fire on
genuine credit events and are idle otherwise. Below it the gate is too eager
(turnover 61 -> 99); above it, nothing in the first half.

**The two robust mechanisms are largely additive** (sortino delta):

                        full      1991-2008   2009-2026
    (a) veto only       +0.071    +0.180      +0.105
    (c) sleeve only     +0.052    +0.054      +0.010
    (a) + (c)           +0.098    +0.230      +0.085

On the first half the pair is +0.230 Sortino, +0.97pp CAGR AND +2.96pp of
drawdown — return and safety together, not a trade-off. On the second half the
sum is sub-additive (+0.085 against +0.115 expected), which is what two
mechanisms reading one signal should do.

So the Worker's most repeated critique was right on TWO of its three
mechanisms, and wrong on the third. No reading of the prose could have
separated them.

**What switching both on would change**, measured decision by decision: 25 of
418 monthly decisions (6.0%), all in credit-stress years — 1998, 2000-01,
2008-10, 2012, 2020. Two of them:

    2008-02 .. 2008-06   now IEF 90 / GLD 10      on: IEF 50 / VCIT 50
    2020-06-01           now SPY 50 / IEF 40 / GLD 10
                         on: SPY 50 / GLD 40 / IEF 10

94% of months are untouched, so the validated rule is intact almost everywhere;
the 6% that move are the months that decide a decade.

**One thing not to over-read.** In 2008 the change goes from 90% Treasuries to
50% investment-grade credit — LESS defensive inside a credit crisis. It measures
better because VCIT was on the floor in late 2008 and rallied hard, so that leg
is one more countercyclical bet, not added protection. The safety shows up
separately, as the +2.96pp of drawdown on 1991-2008.

**Still off.** Both constants are None. Switching them changes ADR-007's live
allocation in exactly the months that matter, and that gate is git and an owner
signature — not ADR-006.

### Switched on (2026-08-11, owner signature)

    SPREAD_SPEED_VETO         = 0.20   defer the risk-on book while spreads widen
    SPREAD_STRESS_SLEEVE_GATE = 0.20   empty that book's equity on the same signal
    SPREAD_SPEED_WIDE_TRIGGER = None   measured nil or unstable — stays off

                     CAGR     Sortino   Calmar   maxDD      turnover
    before          10.72%    1.17      0.52     -20.61%    61.1
    after           11.22%    1.27      0.54     -20.61%    67.7

The pinned pair becomes **11.22% / -20.61%**. This is an amendment to ADR-007,
not a bypass of it: ADR-006 matures invariants and strategies mechanically, and
says so explicitly of the module constants — the gate there is git and an owner
signature. The measurement earned the recommendation; the signature made it live.

Reproducing the rejection of the third mechanism is a command, not a rewrite:
`measure_revision(db, {"spread_speed_wide_trigger": 0.20})`.

### The haven family, closed (2026-08-11)

Every alternative haven the Worker proposed is now measured. Two of them had
been refused by a validation message that was simply WRONG — "not a tradable
sleeve with a price series" — when SHY is active in the catalog with 8755 points
back to 1991. The real constraint was that `run_market_signal` loaded prices for
the five book sleeves only, so a haven outside that set had no series at
runtime. One `LOADABLE_TICKERS` set now serves both the loader and the knob
validation, so "the knob accepts it" and "the run has prices for it" cannot
disagree.

    proposal                          full        1991-2008   2009-2026
    haven = GLD, fallback IEF         reject      —           —
    haven = GLD, fallback cash        reject      —           —
    haven = SHY                       reject      reject      reject
    haven = TLT                       ADOPT       reject      reject
    fallback = SHY                    reject      reject      reject
    haven = cash                      not expressible (the primary haven is
                                      trend-CHECKED, so it needs a series)
    fallback = TIP                    not expressible (series starts 2003)

**TLT is the cleanest out-of-sample kill yet**: it adopts on the full 35 years
and fails BOTH halves — better evidence than the 175/225-day windows, which
each failed only one. Without the split it would have been switched on.

The haven stays IEF, with the cash fallback. The family is closed: the Worker
proposed five alternatives across independent dates, and the history refused all
five.

### The credit-sleeve gate: right premise, wrong conclusion (2026-08-11)

The Worker's fourth and fifth wordings of one theme found a hole the veto ITSELF
opened: deferring the wide reading routes the stack to the slope-decided
tight-steep book, which holds VCIT 50 — investment-grade CREDIT — while the
stress gate emptied the equities beside it. Verbatim: "On 2008-11-03 BAA10Y is
5.53 vs median 2.33 with speed 1.43; that is exactly the condition under which
investment-grade credit should not be treated as a tight-spread carry sleeve."

The critique could not have existed two days earlier: before the veto shipped,
the stack rarely reached that book during credit stress. It reads the rule it is
given, not a memorised one.

`STRESS_GATED_SLEEVES` makes the gated set a knob. Measured:

    gated set              full      1991-2008              2009-2026
    SPY IWN VCIT           reject    reject -0.177 sortino  reject
                                     drawdown 2.96pp DEEPER
    SPY IWN VCIT GLD       ADOPT     reject                 reject

**Gating the credit sleeve destroys exactly the gain the veto bought** — the
same 2.96pp of drawdown, with the sign flipped. The reason is the stack's own
doctrine: the veto's escape route holds investment-grade credit at the bottom of
2008, and the 2009 recovery in that sleeve is what pays. Stress that is PRICED
precedes strong forward returns, and that premise covers credit as much as
equities.

So the premise was right and the conclusion inverted. Nothing to adopt, and the
knob stays at its default (equities only) with the verdict recorded so the
question is not re-opened a sixth time.

**A measurement bug this exposed, worth recording.** The first implementation
measured EXACTLY zero on all three windows — which is not a market fact, and the
implausibility is what exposed it. The gate rewrote entries of the trend-read
map, built only for the 200d-checked set, so gating VCIT did nothing; and
`apply_trend_overlay` separately ignored any sleeve outside that set. One
implausible number, two layers of the same constraint.

---

## CORRECTION (2026-08-11): the out-of-sample first halves were mismeasured

`run_market_signal(start=, end=)` bounds the DECISION dates and not the pricing.
A run asked for 1991-2008 takes its 207 decisions and then holds the last book
FROZEN to the end of the calendar, so its NAV spans 8674 days exactly like a
full run. Every "first half" figure above was therefore measuring "trade through
2008, then sit still for eighteen years": **8.09% CAGR and -20.97% drawdown for a
window that actually contains 13.05% and -13.32%**.

Second halves were never affected — a walk starting in 2009 prices from 2009 —
nor were the full-sample numbers.

**What changes in the conclusions: nothing, and that was luck.** Re-measured
with the pricing bounded to its window, 2 of 12 first-half verdicts flip (TLT as
haven, and the speed-only entry trigger, both reject -> ADOPT), and neither
changes an outcome: both still fail the second half, so both remain MIXED and
unadopted. The 300-day overlay window still adopts on all three windows. The two
live knobs still adopt on all three.

**One published number was wrong and is withdrawn.** "+2.96pp of drawdown on
1991-2008", stated repeatedly for the veto and used to argue that the pair buys
"return and safety together", is an artefact of the frozen tail. Correctly
measured the drawdown is UNCHANGED on every window, for every arm:

    window            arm             CAGR      sortino   maxDD
    full 1991-2026    off             10.75%    +1.176    -20.61%
                      both (live)     11.26%    +1.274    -20.61%
    first 1991-2008   off             12.66%    +1.343    -13.32%
                      both (live)     13.05%    +1.464    -13.32%
    second 2009-2026  off              9.03%    +1.051    -20.61%
                      both (live)      9.62%    +1.136    -20.61%

The activation still holds under Pareto — Sortino and CAGR improve on all three
windows, nothing degrades — but on RETURN at unchanged risk, which is precisely
the trade the acceptance test was widened to express on 2026-08-09. It was never
a drawdown improvement.

**And a better argument appears that the contaminated measurement hid**: the two
mechanisms carry different eras. The sleeve gate dominates the first half
(+0.113 Sortino against the veto's +0.031) and the veto dominates the second
(+0.106 against +0.010). Each covers where the other is weak, which is a
robustness claim the pair could not make before.

`stack_metrics(..., until=)` now bounds the measurement, with a test.

### The trend window, switched to 300 days (2026-08-11, owner signature)

200 was never measured. It came from the scratchpad backtest, where it is the
convention every trend-following study uses, and it survived the whole ADR-007
validation unexamined — swept for the first time on 2026-08-09, and only because
that day's finding said the drawdown belongs to the OVERLAY, so the overlay's
own parameters are where an improvement could come from.

Re-measured against the CURRENT rule (both trajectory knobs live) with the
pricing bounded to each window:

    window            sortino   cagr      calmar   maxDD       turnover
    full 1991-2026     +0.027   +0.36pp   +0.017   unchanged   68 -> 54
    first 1991-2008    +0.011   +0.18pp   +0.013   unchanged   28 -> 22
    second 2009-2026   +0.042   +0.53pp   +0.026   unchanged   39 -> 31

Adopts on all three. The turnover fall of 21% is not a side benefit: fourteen
fewer round trips a year at ADR-010's 23 bps is the cheapest part of the CAGR
gain, and a slower signal is a less fitted one.

    pinned pair    11.22% / -20.61%   ->   11.57% / -20.61%
    sortino        1.27               ->   1.30
    turnover       67.7               ->   53.7

175 and 225 days also adopted on the full sample and were REFUSED, each failing
the half it was not fitted to. The out-of-sample split earned its keep on its
first outing, and would have let two fitted windows through without it.

---

## The attribution: what the SIGNAL earns over the TREND (2026-08-13)

> **Measured BEFORE the look-ahead fix of the same day** (see the section
> below). Every figure here is optimistic, and unevenly so — the stack trades
> more than its control and collected more of the bias. The finding holds in
> DIRECTION and its size roughly halves: the signal's margin is +0.56pp of CAGR,
> not +1.11pp, and Sortino 1.237 vs 1.089 rather than 1.311 vs 1.150. The
> conclusion the section draws — the overlay carries the strategy, the signal
> adds a little — is strengthened by the correction, not weakened.

Every number this file has ever published compares the stack to a PASSIVE
portfolio — All Weather, +3.80pp/yr, the figure ADR-007 was signed on. That
answers "is this better than holding a static balanced book". It does not answer
the question that decides whether the credit/slope read is worth its complexity:

> is it better than the SAME 300d overlay on a book that never rotates?

That arm had never been built. It could not even be asked of the measurement
machinery — `rule_revision.TESTABLE_PARAMETERS` moves knobs INSIDE the rule, and
`BOOKS` is not a knob, so "delete the signal layer" was not expressible. Built
and measured on 2026-08-13, with the two layers switched independently:

    SIGNAL = spread level vs 10y median + slope + CONFIRM_DECISIONS hysteresis
             + SPREAD_SPEED_VETO + SPREAD_STRESS_SLEEVE_GATE
    TREND  = the MA_WINDOW_DAYS overlay per sleeve + the IEF/cash haven chain

    D  signal ON,  trend ON    the live stack
    C  signal ON,  trend OFF   book rotation alone
    B  signal OFF, trend ON    trend-following on a FIXED book  <- the missing arm
    A  signal OFF, trend OFF   buy and hold

All arms are priced by `shadow_book_nav` on ONE `load_series` load, one vintage,
one calendar, at ADR-010's 23 bps — and the measurement is bounded with
`nav.loc[:end]`, so the half-windows do not repeat the frozen-tail error of
2026-08-11. The harness self-checks: arm D over `PINNED_WINDOW` reproduces
11.616% / -20.612%, the pinned pair, before any other arm is reported.

### The finding

Starting from the `credit-spread-wide` book and adding one layer at a time,
1991-2026:

    arm                                  CAGR   sortino    maxDD
    A  buy & hold                      10.60%     0.76   -51.80%
    B  + the trend overlay alone       11.38%     1.10   -21.59%
    D  + the credit/slope signal       11.62%     1.30   -20.61%

**THE OVERLAY BUYS 30 POINTS OF DRAWDOWN; THE SIGNAL BUYS 1.** The signal
layer's entire marginal contribution over the strongest fixed-book competitor is
**+0.24pp of CAGR and +0.20 of Sortino**. Real — Sortino's move is ~28x the
0.71% noise floor — and an order of magnitude smaller than the number this file
has been quoting, because that number was measured against the wrong opponent.

Full decomposition, all three windows:

    arm                                CAGR   sort   calm    maxDD    turn
    --- full 1991-2026 -------------------------------------------------
    D  signal + trend  (LIVE)        11.62%   1.30   0.56  -20.61%    53.7
    C  signal only, no overlay       12.04%   1.10   0.40  -29.85%    39.4
    B  trend only — wide book        11.38%   1.10   0.53  -21.59%    36.0
    B  trend only — flat book        10.71%   1.15   0.52  -20.61%    40.5
    B  trend only — static 60/40      9.52%   1.14   0.50  -18.90%    28.5
    B  trend only — static SPY       12.30%   1.00   0.36  -33.72%    28.0
    A  buy & hold — wide book        10.60%   0.76   0.20  -51.80%     0.0
    A  buy & hold — static SPY       11.00%   0.75   0.20  -55.19%     0.0
    --- first 1991-2008 ------------------------------------------------
    D  signal + trend  (LIVE)        13.23%   1.47   0.99  -13.32%    22.3
    C  signal only, no overlay       10.03%   0.80   0.34  -29.85%    16.8
    B  trend only — wide book        13.04%   1.26   1.17  -11.11%    12.3
    B  trend only — static 60/40     11.25%   1.24   1.55   -7.25%    10.5
    A  buy & hold — wide book         8.03%   0.47   0.17  -46.99%     0.0
    --- second 2009-2026 -----------------------------------------------
    D  signal + trend  (LIVE)        10.15%   1.18   0.49  -20.61%    30.9
    C  signal only, no overlay       12.93%   1.21   0.46  -28.32%    21.6
    B  trend only — flat book         9.90%   1.15   0.48  -20.61%    24.0
    A  buy & hold — flat book        12.56%   1.22   0.53  -23.78%     0.0
    A  buy & hold — static 60/40      9.85%   1.20   0.46  -21.32%     0.0

### Three things this settles

**The -20.61% belongs to the overlay, mechanically and no longer by inference.**
The flat book FROZEN under the same overlay reaches -20.61% — the stack's own
drawdown, to four decimals. `rule_revision.adopt` reasoned its way to this in
August ("the stack's worst drawdown is 2020-03-20, the book in force through the
covid crash was already the tight one"); here it is measured. Book selection
does not move the number the -25% cap binds, and now a test fails if it starts
to (`test_the_stack_still_beats_its_own_control_arm`).

**Rotation alone is not admissible.** Arm C is -29.85%, past the -25% cap. The
stack needs its overlay to clear its own binding constraint; the overlay does
not need the stack.

**The edge is not evenly distributed, and the second half is uncomfortable.** On
1991-2008 the stack is overwhelming (13.23%/1.47/-13.32% against buy & hold's
8.03%/0.47/-46.99%). On 2009-2026 the whole apparatus — 74 target changes, 30.9
turnover — returns 10.15% at Sortino 1.18, while buying the flat book in 2009
and never trading again returns **12.56% at Sortino 1.22 and -23.78%, inside the
cap**. Seventeen years is partly a regime artefact (a bull market punishes every
de-risking rule) and it is also the length of the owner's horizon. It is
recorded here rather than explained away.

### What NOT to conclude

The signal still wins. Against every fixed-book arm on the full sample it is
Pareto-dominant — better or equal on all four indicators — except static SPY,
which wins CAGR by 0.68pp and loses Sortino, Calmar and 13 points of drawdown,
breaching the -25% cap by a wide margin and so inadmissible under the owner's
own rule. Nothing here argues for deleting the signal layer. It argues that its
measured worth is ~0.2-0.9pp of CAGR and ~0.15-0.20 of Sortino, and that every
claim about it should be stated against a fixed book from now on.

### What changed as a result

**The control arm is now a SEEDED, PERSISTED portfolio** (`ms-trend-baseline`),
not a study: `market_signal.run_trend_baseline` prices it, the weekly cycle
refreshes its `portfolio_nav` beside the stack's, and the UC7 ranking and the
digest compare them every week with nobody remembering to. The argument is
verbatim the one that created `ms-stack` — a strategy with no NAV cannot be
measured — applied to the thing the stack has to beat. It freezes
`credit-spread-tight-yield-curve-flat`, chosen by measurement rather than taste:
the signal itself holds that book 197 of 418 monthly decisions (47.1%).

**Forward paper-mode's question changes.** Step 6 must now judge D against
`ms-trend-baseline`, not against a passive benchmark — otherwise it will
confirm the overlay and credit the signal. And the margin being ~0.2pp of CAGR
says what forward evidence can and cannot settle: 6-18 months will never resolve
a return gap that small. What is falsifiable on that horizon is the SORTINO
margin (+0.15 to +0.20, well clear of the noise floor), so that is what the
+12w scoring should watch.

**Where to look next is the overlay, not the signal.** Every knob switched on in
August — the veto, the stress gate, their 0.20 thresholds — lives in the layer
worth +0.24pp, and each was fitted on these same 35 years. The layer that
carries the result has been swept exactly once (200 -> 300 days, 2026-08-09).
The ±band around the moving average, a graduated sleeve instead of a 100/0
switch, and the 125-day window that is still the only setting ever measured to
improve the drawdown materially (-17.86%, refused for 0.94% of Sortino) all live
there — and all three are TRADE-OFFS, which the Pareto test cannot adopt by
construction. That doctrine question is open above and is now blocking the one
layer that demonstrably pays.


---

## The look-ahead, removed (2026-08-13, owner signature)

Found while testing whether the overlay should re-read faster than the monthly
decision. The daily variant measured Sortino 2.38 / CAGR 17.5% / maxDD -8.7%,
which is not a discovery but a symptom: results that improve monotonically with
decision frequency are the signature of same-day information.

**The mechanism.** `shadow_book_nav` applies a target dated t BEFORE day t's
return — `synthesize_nav`'s pinned sequencing, "the portfolio enters the period
already rebalanced" — while the trend read used the CLOSE of day t. The rule set
weights from a price it then earned. No implementer can do that: at the moment
the order goes in, that close does not exist.

**Why it is not noise.** The rule is momentum-shaped, so an asset that ROSE
today is likelier to sit above its moving average today. The rule was therefore
systematically positioned for the very day it was reading — a free move, in one
direction, on every decision date, worth most precisely on the days the
allocation swings hardest.

**It survived from M4 because it is harmless for a static book.** A portfolio
that never decides cannot exploit it, which is why the NAV engine's convention
was validated against Portfolio Visualizer and stayed correct. What was wrong
was feeding it a decision that used same-day data.

**Measured, over 1991-2026 — it scales with turnover exactly as that explanation
predicts:**

    arm                            turnover   as published   lagged 1d   the bias
    market-signal stack (LIVE)         54.3        11.82%      10.86%    0.96pp/yr
    ms-trend-baseline (control)        40.5        10.71%      10.30%    0.41pp/yr
    a static book                       0.0             —           —    0.00pp/yr

...and with frequency: monthly 11.82% -> 10.86%, daily 14.50% -> 8.19%. The
daily overlay was better than monthly ONLY through the bias; honestly measured
it is worse on every indicator, which closes the cadence question.

**What was fixed, and where.** The lag goes on the DECISION read
(`StackSeries.decision_prices`), not on the NAV engine: the engine's convention
is right and is shared with every static portfolio and the M4 golden numbers.
Decide on yesterday's close, trade at today's open, earn today. The two FRED
signals are NOT lagged — ADR-003 already defines a `ts` as the date the value
became knowable, so a second lag would double-count the data model's. Doing so
anyway would cost a further ~0.4pp/yr, recorded so that stays a vintage question
rather than being rediscovered as a new one.

    pinned pair    11.76% / -20.61%   ->   11.21% / -20.61%
    sortino        1.311              ->   1.237
    calmar         0.574              ->   0.547
    turnover       54.3               ->   53.0

**The drawdown does not move**, which is consistent with everything else
measured that day: it belongs to the overlay's latency, and one day either way
does not touch it.

### What this costs, and what it corrects

It is NOT a supersession like the others. The rule did not change and got no
worse; the measurement stopped crediting it with information it never had. Two
consequences the project has to carry:

- **Every stack figure published before this date is optimistic by ~1pp/yr** and
  is not comparable to anything measured after it. That includes the pivot's
  "+3.80 vs B": All Weather has zero turnover and earned none of the bias, so
  the margin absorbs the stack's full 0.96pp.
- **The attribution's headline halves.** The signal layer's margin over its
  frozen-book control was +1.11pp of CAGR and is +0.56pp once both are measured
  honestly; Sortino 1.311 vs 1.150 becomes 1.237 vs 1.089. The finding holds in
  DIRECTION — the signal still earns its place — and its size was overstated by
  about a factor of two, because the stack trades more than the control and so
  collected more of the bias.

Comparisons between arms of SIMILAR turnover are unaffected, which is why the
book search and the overlay sweep stand as measured. The earlier claim that "all
same-cadence comparisons are valid" was too generous and is withdrawn: what
matters is similar TURNOVER, not similar cadence.

### Why now rather than after paper-mode

A backtest promising a point more than the rule can deliver would have surfaced
in eighteen months as the strategy failing — the one misreading Step 6 cannot
afford. It is also the only moment the break in comparability is cheap: no
forward evidence exists yet to be broken.

`test_the_walk_decides_on_the_previous_close_not_the_decision_day` holds the
separation, and it was written because wiring the fix changed the live rule with
the entire suite staying green. Nothing held it; nothing would have noticed it
being undone.


---

## Following the source, checked against it (2026-08-14)

The owner's instruction was to respect Verdad's method rather than search for a
portfolio that works in every era — a regime-conditional allocator is supposed
to be conditional. Re-read against `docs/Countercyclical+Investing`, the
implementation deviates in three places. Two of the deviations are ours and
measure BETTER; the third is a data wall.

**1. The primary indicator — the one real gap, and it is closed by measurement
rather than by preference.** The paper uses the HIGH-YIELD spread. We use
BAA10Y. `BAMLH0A0HYM2` still returns **787 points from 2023-08-15** (verified
2026-08-14; ICE restricted historical redistribution), so the paper's own signal
cannot be backtested at all. The closest obtainable improvement — Baa MINUS Aaa,
a pure credit-quality spread with 10189 points from 1986 — was substituted and
measured WORSE on all three windows (full: CAGR 11.27% -> 10.17%, Sortino
1.237 -> 1.081, reject). The theoretical objection to BAA10Y is sound; removing
the mixture removes information with it. `db/seed_data.py` carries the table.

**2. The trend overlay — ours deviates and wins.** The paper monitors DAILY with
a five-day confirmation ("whenever ETF prices dipped below their 200-day moving
average for five days in a row"). Ours reads once per monthly decision. Measured:

    full 1991-2026                    sortino    maxDD    CAGR   turn
    our monthly overlay, 300d           1.237  -20.61%  11.27%   53.0
    the paper's 200d + 5-day confirm    1.001  -20.17%   9.10%  134.9
    best of that family (300d, 10-day)  1.110  -19.14%  10.06%   99.2

The confirmation filter does its job — daily with NO confirmation scores 0.662 —
but daily monitoring costs 135 of turnover against 53, which is ~2pp/yr at
ADR-010's 23 bps. The paper's backtest is gross of trading costs; ours is not,
and that is the whole difference.

**3. The Dalio regime as a signal — refused by the source itself.** The paper
uses the four quadrants to understand WHICH assets work, never as a trading
signal, and says so: *"These periods are defined in hindsight, according to the
most recent revisions, and are thus useful for understanding the past but would
not have been useful as trading signals at the time."* Our own detector agrees
when measured point-in-time — mean detection lag 34 days, max 63, and all four
quadrants invert their asset ranking between the halves. The credit spread and
the curve ARE the contemporaneous, market-priced proxies for those quadrants;
that substitution is the design, not a shortcut.

**On the 15.8% CAGR.** It is measured over 50 years from 1970; ours is 35 years
from 1991. The paper states that the strategy underperforms a 100% equity
portfolio when the S&P returns more than 15%/yr and outperforms below it — which
describes much of our window and none of the 1970s. Add ADR-010's 0.66pp/yr of
real trading cost and the ~0.6pp of look-ahead removed on 2026-08-13, neither of
which the paper carries. The gap is mostly window and accounting, not
implementation.

**One thing this bought that was not the goal.** Under Baa-Aaa — a credit signal
never used to build it — `wide-flat` still ranks IWN and SPY first on BOTH halves
and gold negative on both, while `wide-steep` still inverts. The 2x2 split of
2026-08-13 reproduces on an independent indicator, which is stronger evidence
than the split-sample test that justified it.


---

## The graduated overlay (2026-08-14, owner signature)

The single moving average was all-or-nothing: it waited for the slow line and
then moved the whole sleeve at once, which is what made it both LATE and
VIOLENT. It now reads each checked sleeve against TWO lines — 150 and 300 days —
and every line breached moves half the sleeve.

    exposure per sleeve      above both lines   below the fast one   below both
                                    held             half out        fully out

    pinned pair    11.21% / -20.61%   ->   11.28% / -15.73%
    sortino        1.237              ->   1.320
    calmar         0.547              ->   0.721
    turnover       53.0               ->   63.2

**4.9 points of drawdown at unchanged return.** It is the only drawdown lever
two days of searching produced, and the search was wide: no book configuration
moves that number at all (seven tested, identical to the basis point), and every
other overlay family measured worse — neutral bands of 1/2/3% (reject on all
three windows), an N-day confirmation (reject, and it makes the drawdown WORSE),
single-window retuning (125 and 150 adopt only on the half they are fitted to),
and the source paper's own daily read with a five-day filter (1.001 Sortino
against our 1.237, drowned by 135 of turnover at ADR-010's 23 bps).

**It is an era bet and it is signed as one.** It adopts on the full sample and on
2009-2026, and REJECTS on 1991-2008 where it costs 0.87pp of CAGR and improves
nothing — the entire drawdown gain is the covid trough. This is not the clean
three-window adoption the project usually requires, and the owner took the trade
knowing which half pays for it.

### Two things deliberately NOT taken

**The equity-heavy tight-flat book.** `SPY 50 / VTI 20 / GLD 30` measured better
still (+0.85pp of CAGR), and VTI is SPY in a second wrapper — 70% US equity
wearing two tickers to satisfy a cap that counts tickers. That is the same
factor-blindness that would have let `TLT 50 / IEF 40` through as 90% duration,
and it is refused for the same reason. Retested with a genuinely different second
sleeve (EFA, international developed), the book change adds nothing over the
overlay alone: 1.327 against 1.321 Sortino, inside the noise floor. **The books
are untouched and the universe gains no ticker.**

**A graduated HAVEN.** The overlay reads IEF against both lines, but the
destination stays binary — cash only when IEF is below both. Splitting the
flight by the haven's own share was measured the same day and is a wash:
identical drawdown, Sortino and CAGR inside the 0.71% noise floor, more
turnover. The same answer the haven check itself gave in August ("C ALONE DOES
NOTHING"), for the same reason: the haven's half-state is rare and, over a month
of holding, cash and IEF barely differ. Recorded so the question is answered by
a number rather than re-asked.

### What the rule now produces, state by state

    wide-flat  (IWN 50 / SPY 50)
      calm                          IWN 50 / SPY 50
      SPY below the fast line       IWN 50 / SPY 25 / IEF 25
      SPY below both                IWN 50 / IEF 50
      SPY and IWN both fully out    IEF 100
      everything out, IEF too       cash 100

    tight-flat  (SPY 50 / GLD 40 / IWN 10)
      calm                          SPY 50 / GLD 40 / IWN 10
      SPY below the fast line       GLD 40 / SPY 25 / IEF 25 / IWN 10
      SPY below both                IEF 50 / GLD 40 / IWN 10
      SPY and IWN both fully out    IEF 60 / GLD 40
      everything out, IEF too       cash 100

`test_no_reachable_book_state_can_be_refused` now enumerates 3^4 states per book
instead of 2^4 and derives the count from `MA_WINDOWS` — the same test whose
docstring warns that a hand-listed enumeration stops guarding the day the code
grows, caught by that warning a second time.
