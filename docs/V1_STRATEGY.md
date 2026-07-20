# V1_STRATEGY.md — the adopted strategy + migration plan

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
- credit spread `BAA10Y` vs its 10y trailing median: WIDE → `growth`;
- if TIGHT, slope `T10Y2Y` vs its 10y trailing median: FLAT → `inflation`,
  STEEP → `slowdown`.

**Books (concentrated pure-asset tilts; 50% sleeves — the single-asset cap was
raised 40→50 for exactly this concentration, ADR-007 addendum 2026-07-20):**
- `growth`: SPY 50 / IWN 40 / GLD 10
- `inflation`: SPY 50 / GLD 40 / IWN 10
- `slowdown`: VCIT 50 / IEF 40 / IWN 10

**Trend-following overlay:** each of the SPY and GLD sleeves is redirected to
IEF whenever that asset is below its 200-day moving average. (This is the
drawdown control; it carries -24% → without it the stack is -50%.) In risk-off
both sleeves can redirect at once, concentrating IEF to ~90%; the trend-haven
sleeve is therefore EXEMPT from the single-asset cap (ADR-007 addendum, choice
(a) — it is a deliberate safety redirect, not a conviction bet).

**Cadence: MONTHLY.** No VIX crisis overlay (measured to hurt at monthly
cadence: 7.57%/-25%).

**Backtest performance** (live DB, 1991-2026, net 20 bps/rotation, vs B =
risk-parity All Weather):

| | value |
|---|---|
| CAGR | 9.85%/yr (+2.5 vs B, robust in AND out of sample) |
| Sortino | 0.94 (B: 0.95) · Sharpe ≈ B |
| Max drawdown (daily) | **-24%** (breaches the user's -15% rule) |
| Changes | 3.4/yr · median holding ~61 days |
| Fee drag | ~1.5 pt/yr already netted out |

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
  medians and the 200d MA are computed at decision time (no new series).
- **B. Regime classification — [new].** A market-signal regime module
  (credit-spread + slope → growth/inflation/slowdown). Replaces the macro
  detector *for allocation*. The existing CPI/GDP detector can stay for the
  regime graph / invariant conditions, but the allocation decision keys off
  the market signal. (This is I-38, now committed.)
- **C. Strategies / Portfolios (seed) — [new].** Seed the 3 Verdad books as
  Strategy/Portfolio entities. The 7 seeded books are retained as BENCHMARKS
  (B, SPY-proxy, etc.) or retired — [decide].
- **D. Trend-following overlay — [new].** A mechanical module: SPY/GLD sleeve
  → IEF below the 200d MA, evaluated monthly.
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
  4-quadrant macro → Verdad market-signal countercyclical), CLAUDE.md update,
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
3 Verdad books (seed), the 200d trend-following overlay, the monthly decision
path. (Data for all of it is already deployed — area A.)

**DOC / ADR surface (needs explicit owner sign-off — "never contradict an
accepted ADR silently"):** a new ADR recording the framework pivot (Dalio
4-quadrant macro → Verdad countercyclical market-signal) + monthly cadence;
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
200d trend overlay + the monthly decision path through the existing gates.
**Replay-validate it reproduces 9.85%/-24%** (anti-drift check — the same
guarantee that caught the M6 rebalance bug). Ship an inspection view.
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

**Step 5 — M8b agentic replay** (best-case screen), same harness.

**Step 6 — M9 go live in PAPER-MODE (forward). THE real gate.** Accumulate
forward evidence over ~6-18 months, each proposal scored at +12w. The +2.5
edge either reproduces on unseen data or it was in-sample. Only here does the
stack stop being a hypothesis. This is also when the old-design bridge can be
retired, if the forward evidence holds.

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
