# STRATEGY_COMPARISON.md — global view of the strategies tested

Post-M6 exploration (2026-07). All figures measured on the **live DB** over a
**common window (1991-2026)**, **ALL METRICS DAILY** (CAGR; Sharpe & Sortino
annualized ×√252; MaxDD is the real intra-day trough — what the -15% rule
cares about). Cost 20 bps per rotation. `B` = the seeded defender
`4s-balanced-defender` (risk-parity All Weather) — the benchmark to beat.

> An earlier version of this table used MONTHLY drawdowns, which understated
> intra-month troughs and wrongly made Verdad look -15%-compliant (it showed
> -10% holdout; daily it is -20%). Fixed: everything below is daily. Daily
> Sharpe/Sortino are lower than monthly for every strategy (more noise) but
> shifted uniformly, so the ranking is unchanged.

## The table

| Strategy | CAGR | Sharpe | Sortino | MaxDD | ≤ -15%? | Holdout 2016-26 (CAGR/Sh/DD) |
|---|---|---|---|---|---|---|
| 100% SPY | 11.0% | 0.53 | 0.75 | -55% | no | 15.1 / 0.76 / -34 |
| **Verdad (signal + trend)** | **9.8%** | 0.70 | 0.99 | -24% | no | **8.9 / 0.66 / -20** |
| **Verdad + VIX→barbell** | 8.7% | **0.71** | **1.01** | **-14%** | **yes** | 6.8 / 0.58 / -14 |
| 60/40 (SPY/IEF) | 8.8% | 0.62 | 0.88 | -33% | no | 9.6 / 0.72 / -21 |
| Momentum 9m / top3 / abs | 8.1% | 0.46 | 0.64 | -37% | no | 10.2 / 0.59 / -29 |
| **B — risk-parity All Weather** | 7.3% | 0.67 | 0.95 | -22% | no | 6.8 / 0.59 / -22 |
| **My stack (macro + crisis)** | 6.3% | 0.64 | 0.91 | **-13%** | **yes** | 7.0 / 0.73 / -13 |
| IG credit held (VCIT) | 4.9% | 0.46 | 0.66 | -21% | no | 3.4 / 0.21 / -21 |
| 10y Treasury held (IEF) | 4.8% | 0.38 | 0.55 | -24% | no | 1.1 / -0.13 / -24 |

## NAV chart — growth of $100, log scale (1991–2026)

![NAV growth of $100, log scale, 1991-2026: Verdad, Verdad+VIX, my macro+crisis
stack, SPY and B](nav_chart.svg)

Interactive version (crosshair + hover, light/dark, self-contained):
**https://claude.ai/code/artifact/5cdbee30-ac12-4098-9096-1840979e2a8c**

Five lines: **Verdad** (blue, ×25), **Verdad + VIX→barbell** (orange, ×18),
**my macro+crisis stack** (green, ×8), **SPY** (grey, ×36 — the return ceiling,
−55% drawdown), **B** (grey dashed, ×11). Crisis bands (dot-com / GFC / COVID)
are shaded: the VIX brake visibly pays in 2000-02 and 2008 (orange falls far
less than blue) and visibly whipsaws in 2020 (sells the crash, misses the V,
never recovers to blue). Log scale — equal vertical distance = equal % gain.
Rebuild: `scratchpad/nav_export2.py` → `build_chart.py`; republish the same file
path to keep the URL.

## What it says

**Three candidates, by objective:**
- **Best all-around, and the V1 lead → Verdad + VIX→barbell.** The Verdad
  regime stack with a fast VIX crisis brake. **Best Sharpe (0.71) and Sortino
  (1.01) of the whole table, beats B by +1.4 on return, AND respects the -15%
  rule (-14%)** over the 35y — the only line that does all three. Weakness: its
  holdout return edge is ~0 (whipsaws on the fast V-recoveries that dominated
  2016-2026 — Verdad's own warned failure mode of vol de-risking).
- **Max return, beats B robustly → Verdad baseline (signal+trend).** 9.8% CAGR,
  +2.5 vs B robust in AND out of sample (daily +2.6/+2.8/+2.05), best return
  after SPY. Cost: -24% drawdown (breaches -15%, ~ B's -22%).
- **Deepest drawdown control → my macro+crisis stack.** -13%, best holdout
  Sharpe (0.73), respects -15% — but low return (6.3%, below B).

**The small-value + IG-credit additions are what power the Verdad stack.**
Adding them to the menu (owner-directed) is precisely what lifts the stack from
bond-like to +2.5 vs B. See the correction note below — an earlier bug hid this.

**Momentum is weaker than first thought.** Cross-asset momentum on the common
1991-2026 window is 8.1% / Sharpe 0.46 / -37% (daily) — above B on raw return
but the worst risk-adjusted of the 8%+ strategies, with large drawdowns. The
earlier "+3-5 vs B" figure included pre-1991 data (outside B's window). Not the
lead.

**The synthesis WORKS (Verdad + VIX→barbell): the first line to beat B on
return AND respect -15%** — +1.4 vs B, -14% drawdown, best Sharpe/Sortino of
all. The trade it makes explicit: the VIX brake converts the -24% baseline
drawdown into -14% but whipsaws on fast V-recoveries, so its holdout edge is
~0 (2016-2026 was V-recovery-heavy). Over a full cycle including real bear
markets (the 2000s), the brake pays; in a decade of instant rebounds it costs.

## The correction that produced this table (intellectual-honesty note)

Earlier post-M6 runs concluded "the faithful Verdad stack does not beat B" and
"a bond held to maturity beats this stack" (consigned then, now marked
SUPERSEDED in docs/MILESTONES.md M6). Both were a **measurement bug**: those
runs fed `replay.load_inputs().prices`, which loads only portfolio/scenario
constituents — so IWN and VCIT (40-50% of the Verdad books) had no price series
and were held FLAT at 0% return. The crippled stack scored 6.8%. With a
complete price dict the stack scores 9.7-9.85% and beats B. The lesson: when a
new asset is added to the MENU but not to any portfolio/scenario, it is absent
from `load_inputs().prices`; any backtest that holds it must load prices
directly.

## What still stands (correctly measured — those books held no IWN/VCIT)

- Seeded-book regime rotation (macro detector, the 4-quadrant map) does not
  beat B on return — I-35 / M6 findings hold.
- The macro+crisis stack (6.3% / -13%) is a genuine risk-reducer, correctly
  measured.
- M6's mechanical premise gate (kind='mechanical' replay) verdict is unchanged
  — it never used the new assets.

## Menu / signals deployed to the live DB (2026-07-19, backup taken first)

- **IWN** small-cap value (proxy DFSVX, 1993, daily corr 0.966).
- **VCIT** IG corporate credit (proxy VFICX, 1993, monthly 0.978, resampled
  validation like GLD).
- **BAA10Y** credit spread (Moody's Baa − 10y, 1986) — the market-priced
  business-cycle signal; substitutes for the ICE-licensing-truncated HY OAS.
- **T10Y2Y** yield-curve slope (1976, already present) — the inflation-axis
  signal.
Aliases `credit_spread`, `yield_slope` in `SIGNAL_ALIASES`. Neither IWN nor
VCIT is a benchmark class (they are sleeves; adding them would shift every
invariant's cross_class median — see seed_data.py).

## Owner decision (the exploration has converged)

Two defensible V1 leads, both beating B, differing only on the drawdown/edge
trade:
1. **Verdad + VIX→barbell** — beats B (+1.4), respects -15% (-14%), best
   risk-adjusted; holdout edge ~0 (whipsaw). The choice if the -15% rule is
   firm.
2. **Verdad baseline** — beats B by +2.5 robustly in AND out of sample, but
   -24% drawdown (breaches -15%). The choice if return dominates.

STOP optimizing further: the 2016-2026 holdout has been consulted many times
this session, so any new knob that improves it is likely overfitting. The
honest next validation for either lead is FORWARD paper-mode, not more
backtest. Remaining backtest-safe work is engineering, not search: wire the
chosen stack as a real Strategy/Portfolio set + signal into the pipeline
(currently these live only in scratchpad).
