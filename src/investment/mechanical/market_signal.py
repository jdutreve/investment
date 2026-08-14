"""The market-signal monthly stack — V1's ADOPTED allocation (ADR-007).

A countercyclical, market-priced strategy after Verdad/Rasmussen (the origin of
the approach; docs/V1_STRATEGY.md carries the attribution). Named neutrally here.

The strategy the pivot adopted (docs/V1_STRATEGY.md): a market-priced,
CONTEMPORANEOUS regime read (credit spread + yield slope, no CPI/GDP lag) picks
one of three CONCENTRATED books, and a trend-following overlay
(`MA_WINDOWS`) redirects every checked sleeve — and the haven itself — to
intermediate Treasuries, by the FRACTION of its moving averages the sleeve has
fallen below, or to cash when the haven has fallen below all of its own. Decision cadence is MONTHLY
(docs/V1_STRATEGY.md "Why monthly").

ANTI-DRIFT (the point of this module): the numbers that earned the pivot — 9.85%
CAGR / -24% daily max drawdown, +2.5 vs B, robust in AND out of sample — came
out of a scratchpad backtest (`global_table_daily.py`, the "signal+trend"
line). This is that logic ported verbatim onto the SAME NAV engine the backtest
used (`replay.shadow_book_nav`, itself pinned equal to the M4-validated
`ratios.synthesize_nav`), so wiring the stack cannot silently diverge from the
figures ADR-007 was signed on.

Those figures have been superseded TWICE, both times deliberately and
owner-arbitrated:
- 2026-08-01, `CONFIRM_DECISIONS` hysteresis: 9.85% -> 11.26% CAGR, Sortino
  0.94 -> 1.11, drawdown unchanged;
- 2026-08-02, ADR-010's single cost rate: 11.26% -> **11.14%**, Sortino 1.09,
  drawdown still -23.8%. Two disagreeing guesses were replaced by one measured
  rate: the stack had been charged 20 bps PER SIDE while `replay_cost_bps` said
  10 and the spec claimed "20 bps/rotation", and every static book it is ranked
  against paid nothing at all. Now Saxo's real 23 bps/order (no FX — every
  portfolio is USD in a USD account) bills all of them, drift-rebalance
  included.

- 2026-08-07, the OVERLAY COMPLETION below (IWN trend-checked, and the haven
  trend-checked too): 11.10% -> **10.71%** CAGR, Sortino 1.09 -> **1.17**,
  drawdown -23.78% -> **-20.61%**, turnover 42.0 -> 61.1. Measured as a 4-way
  A/B in one process on one data vintage, which is the only way to compare
  against a moving baseline (I-48):

      A  current rule            11.10%   1.09   -23.78%   turnover 42.0
      B  + IWN trend-checked     10.79%   1.17   -23.33%            53.4
      C  + haven trend-checked   11.03%   1.09   -23.78%            48.2
      D  both (adopted)          10.71%   1.17   -20.61%            61.1

  C ALONE DOES NOTHING and B alone barely moves the drawdown; together they cut
  it by 3.2 points. The interaction is the point: adding IWN is what sends large
  weight into the haven, and only then does checking the haven matter. Adopted
  against the acceptance test the proposing Worker wrote itself — "adopt only if
  it does not degrade Sortino and improves maxDD" — which D passes on both legs,
  for 0.39pp of CAGR and 45% more turnover (already charged at ADR-010's 23 bps).
  It also buys real headroom against the binding -25% cap the stack was sitting
  1.2 points inside.

- 2026-08-11, THE TWO SPREAD-TRAJECTORY KNOBS turned on at 0.20 (owner
  signature; ADR-007 is amended, not bypassed — this is the git gate ADR-006
  explicitly does not reach): 10.72% -> **11.22%** CAGR, Sortino 1.17 ->
  **1.27**, Calmar 0.52 -> 0.54, drawdown unchanged at -20.61%, turnover 61.1 ->
  67.7.

  Both came out of the Worker's most repeated critique — six wordings across
  independent M8b dates saying the book is selected on the spread's LEVEL and
  should read its TRAJECTORY. `SPREAD_SPEED_VETO` defers the risk-on book while
  the spread is still widening; `SPREAD_STRESS_SLEEVE_GATE` empties that book's
  equity sleeves on the same condition. Each adopts alone on the full sample AND
  on both halves of it, and together they are additive (+0.098 Sortino against
  +0.071 and +0.052 apart; on 1991-2008, +0.230 Sortino, +0.97pp CAGR and
  +2.96pp of drawdown).

  A THIRD mechanism from the same theme — enter the risk-on book on speed alone
  — measured nil or unstable and is NOT on. It stays in the code as
  `SPREAD_SPEED_WIDE_TRIGGER = None`, so its rejection is reproducible by a
  command rather than by a rewrite.

  25 of the 418 monthly decisions change, all in credit-stress years
  (docs/V1_STRATEGY.md has two worked examples).

- 2026-08-11, THE TREND WINDOW 200 -> 300 (owner signature): 11.22% ->
  **11.57%** CAGR, Sortino 1.27 -> **1.30**, drawdown unchanged at -20.61%,
  turnover 67.7 -> **53.7**. The window had never been measured — 200 came from
  the scratchpad backtest by convention and survived the whole ADR-007
  validation unexamined. Adopts on the full sample and on BOTH halves, with the
  pricing bounded to each; 175 and 225 also adopted on the full sample and were
  refused, each failing the half it was not fitted to.

- 2026-08-13, THE 2x2 SPLIT OF THE WIDE BRANCH (owner signature): 11.57% ->
  **11.76%** CAGR, Sortino 1.30 -> **1.31**, Calmar 0.54 -> 0.57, drawdown
  unchanged at -20.61%, turnover 53.7 -> 54.3. The wide branch had never
  consulted the slope, so one book served two states; splitting them and giving
  wide-flat `IWN 50 / SPY 50` is the ONE book change both halves of the sample
  agree on (see BOOKS for the ranking table). A search that fitted all four
  books instead scored Sortino 1.47 -> 1.56 on the training half and
  1.18 -> 1.08 out of sample — recorded because it is the cost of the thing NOT
  done here.

  THE FIRST CHANGE THIS MODULE HAS EVER MADE TO THE BOOKS. Every supersession
  above moved a threshold or the overlay; the books came from the Verdad paper
  and had never been confronted with data. That they were nearly right is worth
  as much as the correction: three of the four states carry no stable ranking at
  all, and acting on them measurably destroys value.

- 2026-08-13, THE ONE-DAY LOOK-AHEAD REMOVED (owner signature): 11.76% ->
  **11.21%** CAGR, Sortino 1.311 -> **1.237**, Calmar 0.574 -> 0.547, drawdown
  UNCHANGED at -20.61%, turnover 54.3 -> 53.0. The trend read used the CLOSE of
  the decision day to set weights that then earned that same day's return —
  `StackSeries.decision_prices` carries the full account. THIS IS NOT A
  SUPERSESSION LIKE THE OTHERS: the rule did not change and got no better or
  worse, the MEASUREMENT stopped crediting it with information it never had. So
  the drop is not a loss, and every figure above it in this list is optimistic
  by roughly a point a year — they are history in a stronger sense than usual
  and must not be compared to anything measured after this line.

  Signed BEFORE forward paper-mode rather than after, deliberately: a backtest
  promising a point more than the rule can deliver would have shown up in
  eighteen months as the strategy failing, which is the one misreading Step 6
  cannot afford. It is also the only moment the break in comparability is cheap,
  since no forward evidence exists yet to be broken.

- 2026-08-14, THE GRADUATED OVERLAY (owner signature): one 300-day line becomes
  two, 150 and 300, each breached line moving HALF the sleeve instead of all of
  it. 11.21% -> **11.28%** CAGR, Sortino 1.237 -> **1.320**, Calmar 0.547 ->
  **0.721**, and the drawdown -20.61% -> **-15.73%**, turnover 53.0 -> 63.2.

  FOUR AND NINE TENTHS POINTS OF DRAWDOWN AT UNCHANGED RETURN, and it is the
  only drawdown lever two days of searching found: no book configuration moves
  that number at all (seven tested, identical to the basis point), and every
  other overlay family — neutral bands, N-day confirmation, single-window
  retuning, and the paper's own daily read — measured worse. See MA_WINDOWS.

  AN ERA BET, SIGNED AS ONE. The whole gain is post-2009 (the covid trough); on
  1991-2008 it costs 0.87pp of CAGR and improves nothing. It adopts on the full
  sample and on the second half and REJECTS on the first, so it is not the clean
  three-window adoption this module usually demands — the owner took the trade
  knowing which half paid for it (docs/V1_STRATEGY.md).

- 2026-08-14, THE EQUITY DIAL TURNED UP (owner signature): the tight-flat book
  goes SPY 50 / GLD 40 / IWN 10 -> **SPY 60 / GLD 40**, which needed the
  single-asset cap raised 50 -> 60 (user profile, `ms-stack`, `ms-inflation-book`
  and the control arm; the other books stay at 50, since a per-portfolio rule
  may only be stricter). 11.28% -> **11.60%** CAGR, Sortino 1.320 -> **1.342**,
  drawdown -15.73% -> **-16.50%**, turnover unchanged at 63.2.

  NOT A DISCOVERY — A PRICE. The equity share is a monotone risk dial, and 60 is
  simply where Sortino peaks before more equity starts buying return with pure
  drawdown (see BOOKS). It gives back 0.77 of the 4.9 points the graduated
  overlay had just bought, for 0.32pp of CAGR.

- 2026-08-14, VCIT UNDER THE OVERLAY (owner signature) — **the one supersession
  in this list that the measurement ARGUES AGAINST**, and it is recorded as a
  conviction call rather than dressed as a finding. 11.60% -> **11.53%** CAGR,
  Sortino 1.342 -> **1.330**, drawdown unchanged at -16.50%, turnover 63.2 ->
  63.9. It rejects on the full sample and on 2009-2026 and is marginally
  positive on 1991-2008.

  It is INSURANCE on the book in force today. `tight-steep` is 90% fixed income
  and holds VCIT 50 with nothing watching it; it has been held 61 times in 8
  episodes and seven of the eight had FALLING rates, so the sample cannot price
  the risk. Frozen to that book through a 2022, the rule loses 8.85% unguarded
  and 4.66% guarded — half — and at the trough the unguarded book sits at
  VCIT 50 / cash 50 with the overlay unable to reach the half that is falling.
  The premium is at the noise floor; the payout is 4.2 points in a shape the
  record does not contain. See TREND_SLEEVES for the full argument, including
  the part that says a backtest cannot price this and the Pareto test was right
  on its own terms.

The pinned pair is therefore **11.53% / -16.50%**. The earlier figures are
history, not targets. Any OTHER divergence from 11.53% is drift and must be
explained, which is what this module exists to guarantee.

That sentence named 10.71% for two supersessions after 10.71% stopped being the
pinned figure — the anti-drift reference itself drifting, in the one paragraph
whose whole job is to hold still. It is the same defect as every stale rule text
this module has fixed, so: the number here and the number in the bold pair above
are ONE fact, and an edit that moves one moves both.

AND IT IS NOW ENFORCED RATHER THAN NARRATED (2026-08-12). `PINNED_CAGR` /
`PINNED_MAX_DRAWDOWN` below hold the pair as CONSTANTS, `drift_violations`
confronts them, `market_signal_cycle.journal_drift` runs that confrontation
every week and `alerts.stack_drift_alert` puts a divergence in the digest;
`tests/test_anti_drift.py` runs the same check against the live database. Read
those constants' own comment before restating any figure here: the machine
checks the pair measured with the pricing BOUNDED to the window, which is
0.05pp away from the free-tail figure this paragraph quotes, and it explains
why a reference has to be measured that way.

ONE STANDING EXPLANATION IS ALREADY KNOWN, and naming it here is what keeps the
sentence above honest (docs/IMPROVEMENTS.md I-48): the guarantee assumes
immutable inputs over a fixed window, and neither holds. The seed's backfill
start is `today - 35y` — ROLLING — while `market_signal_cycle.HISTORY_START` is
fixed at 1991-01-01, and Yahoo restates adjusted closes retroactively. The
2026-08-03 re-seed measured the effect: 11.14% -> 11.10%, drawdown unchanged,
with 418 identical decisions — the ground moving under a fixed marker, not
drift in the logic. Treat a divergence under ~0.1pp as that; anything larger
still has to be explained.

PURE decision logic (`classify_regime`, `apply_trend_overlay`,
`advance_hysteresis`, `walk_decisions`) takes already-loaded series and holds no
I/O — the same separation as `mechanical/gates.py`, so the classifier is
unit-testable without a DB. `run_market_signal` is the thin I/O driver.

`walk_decisions` is the SINGLE decision clock: it emits one `Decision` per
monthly decision date, and BOTH consumers derive from it — the replay via
`build_targets` (which keeps only the change points `shadow_book_nav` wants) and
the LIVE monthly path (`market_signal_cycle.py`) via the last entry of the same
walk run with `end=today`. That is how the live path "calls the identical
function the replay validates": not a shared helper, the shared WALK. A live
decision that disagreed with the backtest would have to disagree with itself.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.market.derivatives import compute_derivatives
from investment.mechanical import ratios, replay
from investment.mechanical.gates import Caps, concentration_ok, drawdown_ok
from investment.mechanical.replay import NavMetrics, nav_metrics, shadow_book_nav

# The 3 books (docs/V1_STRATEGY.md). Concentrated tilts: the 50% sleeves are the
# measured source of the +2.5-vs-B edge and the reason the single-asset cap was
# raised 40 -> 50 (ADR-007 addendum). Weights are allocation percent points.
#
# NAMED AFTER THE SIGNAL STATE THAT SELECTS THEM, not after a macro regime
# (renamed 2026-07-20, ADR-007 addendum; previously growth/inflation/slowdown).
# The old names asserted a macro reading the books do not have: measured over
# the 418 monthly decisions, the market signal is essentially ORTHOGONAL to CPI
# — each book spent 28-33% of its time with CPI YoY above 3% against a 31.3%
# base rate, and the book then called "inflation" averaged CPI 2.99 vs 2.23 for
# the one called "growth" (docs/IMPROVEMENTS.md I-39). Since the Worker is an
# LLM that reads these keys as semantic context, a book called "inflation" that
# does not track inflation is a reasoning hazard, not just untidy naming.
# FOUR BOOKS SINCE 2026-08-13, and the fourth is a SPLIT of the wide one rather
# than an addition. Until then the wide branch never consulted the slope — a
# deliberate asymmetry ADR-007 inherited from the Verdad paper — so 180 of the
# 418 monthly decisions shared one book across two states that behave in
# opposite ways. Mean forward 3m return by state, measured on each half of the
# sample independently (docs/V1_STRATEGY.md "The books, revisited"):
#
#     wide-flat   TRAIN  IWN +4.68  SPY +3.03  IEF +1.75  VCIT +1.09  GLD -0.67
#                 VALID  IWN +6.57  SPY +5.10  VCIT +1.52  IEF +0.08  GLD -0.14
#     wide-steep  TRAIN  GLD +3.72  IEF +2.01  VCIT +0.79  IWN +0.72  SPY -2.50
#                 VALID  IWN +7.56  SPY +6.52  GLD +4.78  VCIT +2.52  IEF +0.66
#
# WIDE-FLAT IS THE ONLY STATE OF THE FOUR WHOSE RANKING AGREES ACROSS THE HALVES:
# IWN first in both, SPY second in both, GLD NEGATIVE in both. The old shared
# book (SPY 50 / IWN 40 / GLD 10) contradicted all three facts at once — it
# under-weighted the best asset, over-weighted the second and held 10% of the
# only consistent loser. `IWN 50 / SPY 50` is that correction and nothing more.
#
# WIDE-STEEP KEEPS THE VERDAD BOOK UNTOUCHED, deliberately: its ranking INVERTS
# between halves (SPY worst at -2.50, then second-best at +6.52), so there is no
# stable evidence to act on. A search that fitted all four states scored better
# on the training half and WORSE out of sample (Sortino 1.47 -> 1.56 train,
# 1.18 -> 1.08 validate), which is the cost of acting anyway, measured.
BOOKS: dict[str, dict[str, float]] = {
    "credit-spread-wide-yield-curve-flat": {"IWN": 50.0, "SPY": 50.0},
    "credit-spread-wide-yield-curve-steep": {"SPY": 50.0, "IWN": 40.0, "GLD": 10.0},
    # SPY 60 SINCE 2026-08-14, up from SPY 50 / GLD 40 / IWN 10, and it needed
    # the single-asset cap raised 50 -> 60 to be expressible at all. Measured
    # against the graduated overlay as baseline, the equity share is a pure RISK
    # DIAL — each 10 points of SPY buys ~0.15pp of CAGR and costs ~1 point of
    # drawdown, monotonically, which is what equity does and not a signal:
    #
    #     SPY 50 (was)  sortino 1.321  maxDD -15.73%  CAGR 11.34%
    #     SPY 60        sortino 1.342  maxDD -16.50%  CAGR 11.66%   <- Sortino peak
    #     SPY 70        sortino 1.323  maxDD -17.70%  CAGR 11.81%
    #     SPY 80        sortino 1.289  maxDD -18.98%  CAGR 11.94%
    #     SPY 90        sortino 1.245  maxDD -20.31%  CAGR 12.07%
    #
    # 60 IS WHERE SORTINO TURNS. Past it the drawdown is paid for with no gain
    # in return quality, so "more equity is better" is false and the owner took
    # the one point the measurement supports rather than the most aggressive one.
    #
    # THE EQUITY IS US-ONLY, and that was tested rather than assumed. Splitting
    # the sleeve toward MSCI World (SPY + EFA, the only developed-ex-US series
    # with 35 years) DEEPENS the drawdown on every window — 1.328/-17.98% at
    # ~World weights, 1.147/-22.61% at ex-US only — because developed equity
    # falls WITH US equity exactly when diversification would pay. The book's
    # diversification is the 40% gold, i.e. across asset classes; a second
    # equity region diversifies inside the factor that is already the risk.
    "credit-spread-tight-yield-curve-flat": {"SPY": 60.0, "GLD": 40.0},
    "credit-spread-tight-yield-curve-steep": {"VCIT": 50.0, "IEF": 40.0, "IWN": 10.0},
}

# The trend overlay: these sleeves are redirected to the haven when their own
# price is below their `MA_WINDOW_DAYS` moving average. This is the drawdown
# control (-24% with it, -50% without — docs/V1_STRATEGY.md).
#
# IWN JOINED 2026-08-07. It is 40% of the credit-spread-wide book and was held
# at full weight whatever its own trend did — the M8b Worker found this in both
# independent runs, five times in total, and it is verifiable here: a rule whose
# whole premise is that credit is impaired kept maximum exposure to the most
# credit-sensitive equity sleeve there is. Every RISKY sleeve is now checked;
# the haven is handled separately below.
# VCIT JOINED 2026-08-14, AND IT IS THE ONE CHANGE IN THIS MODULE THE
# MEASUREMENT ARGUES AGAINST. It is an owner conviction call, recorded as one.
#
# WHAT THE MEASUREMENT SAYS, in full and not selectively: adding VCIT rejects on
# all three windows against the current rule — full 1991-2026 Sortino
# 1.342 -> 1.330 and CAGR 11.66% -> 11.58%, valid 2009-2026 1.333 -> 1.321, with
# the drawdown UNCHANGED everywhere. On the train half it is marginally positive
# (1.367 -> 1.373). The 2026-08-09 sweep rejected it too, on the older rule.
#
# WHAT THE MEASUREMENT CANNOT SAY. VCIT is 50% of the tight-steep book, which is
# 90% fixed income and is the book in force today. That book has been held 61
# times across 8 episodes, and SEVEN OF THE EIGHT had FALLING rates — from -16bp
# to -122bp on the 10-year. The eighth is the current one, at +20bp. So its
# 6.99% CAGR, 1.05 Sharpe and -4.87% drawdown were all earned in a falling-rate
# world, and the sample contains no observation of it under duration stress.
#
# The counterfactual it cannot price, measured on the rule with the book frozen:
#
#     2022 rate shock (+231bp)   VCIT unguarded  -8.85%  (trough -10.1%)
#                                VCIT checked    -4.66%  (trough  -6.2%)
#
# Half the loss. And at the deepest point the unguarded book sat at VCIT 50 /
# cash 50 — the overlay had moved IEF and IWN out and could not touch the rest.
#
# THE SHAPE THAT HURTS IS NOT "A RATE SHOCK". 1994 (+200bp) and 2013 (+129bp)
# both leave this book positive, because the flight to quality bids its
# Treasuries. It is the 2022 shape specifically: rates up, spreads wider and
# equities down together, with no haven bid.
#
# SO THIS IS INSURANCE, AND A BACKTEST CANNOT PRICE INSURANCE AGAINST A LOSS ITS
# SAMPLE DOES NOT CONTAIN. The premium is ~0.012 of Sortino and 0.08pp of CAGR,
# which is at the measured noise floor (0.71% of Sortino ~ 0.010); the payout is
# 4.2 points in the one configuration the record lacks. The Pareto test refuses
# it correctly on its own terms, and its own terms are what is being overridden.
#
# The earlier reasoning this replaces was sound and stays true where it applies:
# VCIT's drawdowns are usually shallow, so exiting below trend avoids a small
# loss AND misses the recovery, twice paying 23 bps. That is why it costs
# something in 34 of 35 years. The owner is buying the 35th.
TREND_SLEEVES: tuple[str, ...] = ("SPY", "GLD", "IWN", "VCIT")

# The EQUITY members of the checked set — the sleeves credit stress transmits to
# first. GLD is trend-checked but is not equity, and the distinction is what the
# sleeve gate below acts on.
#
# Listed rather than read from `allowed_tickers.asset_class` because this module
# is pure decision logic with no DB (the same reason `TREND_SLEEVES` is a
# constant), and pinned to the catalog by a test so the two cannot drift.
EQUITY_SLEEVES: tuple[str, ...] = ("SPY", "IWN")

# THE HAVEN IS TREND-CHECKED TOO, and that is the other half of the same fix.
#
# The Worker's sharpest line of the 21 M8b readings, 2022-02-01, raised in BOTH
# runs on that same date: "the overlay trends the sleeve it exits but not the
# sleeve it enters". A rule that flees a falling asset into a falling asset is
# not a drawdown control. In February 2022 it moved 40% into IEF while IEF was
# below its own 200d line, in the worst bond tape of the 35-year sample.
#
# ITS OWN PROPOSAL WAS TO GATE THE HAVEN ON CPI. Refused: the stack reads
# PRICES ONLY (no macro regime, no policy, no positioning), and coupling it to
# the inflation print would reintroduce exactly the macro dependency ADR-007
# removed. The market-priced expression of the same insight is symmetry — apply
# to the destination the test already applied to the origin. When the haven is
# itself below trend, the redirect goes to cash, which cannot fall.
TREND_HAVEN = "IEF"
TREND_FALLBACK_HAVEN = ratios.CASH_TICKER

# BOTH DESTINATIONS OF THE HAVEN CHAIN ARE EXEMPT FROM THE SINGLE-ASSET CAP
# (owner, 2026-08-08), defined once because two call sites enforce it and their
# docstrings promise each other they use "the same exemption".
#
# The ADR-007 addendum exempted IEF, and that was the whole chain at the time.
# When the haven became trend-checked with a cash fallback (2026-08-07), the
# exemption did not follow it, and the flight to safety became unreachable in
# exactly the tape that needs it: measured on the M8b run of 2026-08-08, four of
# the seven inflation-shock dates (2022-03-01, -05-02, -06-01, -07-01) had all
# four sleeves AND the haven below trend, produced the 100%-cash target, and had
# it refused by the 50% cap. The stack sat in its stale book through the 2022
# drawdown.
#
# ADR-009 had already reasoned this out for the DRAWDOWN leg — refusing a
# proposal cannot exit a position, only freeze one, and the proposal blocked
# during a drawdown IS the overlay's flight to safety — and scoped that leg out
# of this path. The argument transfers wholesale to the concentration leg: cash
# at 100% is not a conviction bet, it is the absence of one, and it is the only
# destination left when every checked instrument is falling.
HAVEN_EXEMPT = frozenset({TREND_HAVEN, TREND_FALLBACK_HAVEN})

# The STACK itself as a Portfolio vertex — the object that is actually held, as
# opposed to the 3 books, which are only ever held conditionally and always
# through the overlay. It exists so the stack has a `portfolio_nav` series like
# every other portfolio: without one, its 36M rolling drawdown (the measure the
# -25% cap is about) cannot be computed at all, and the ranking compares three
# static fictions nobody holds instead of the one thing that is (ADR-009).
STACK_PORTFOLIO_ID = "ms-stack"

# THE STACK'S REAL COMPETITOR, and until 2026-08-13 it had never been on the
# start line.
#
# The stack was only ever measured against All Weather (+3.80pp/yr, ADR-007) —
# a PASSIVE portfolio, which answers "is this better than holding a static
# balanced book" and not the question that decides whether the signal layer
# earns its complexity: is it better than THE SAME TREND OVERLAY on a book that
# never rotates? Measured 2026-08-13 (docs/V1_STRATEGY.md "The attribution"),
# starting from the credit-spread-wide book and adding one layer at a time:
#
#     arm                                CAGR    sortino   maxDD
#     buy & hold, no signal, no trend   10.60%     0.76   -51.80%
#     + the trend overlay alone         11.38%     1.10   -21.59%
#     + the credit/slope signal (LIVE)  11.62%     1.30   -20.61%
#
# THE OVERLAY BUYS 30 POINTS OF DRAWDOWN; THE SIGNAL BUYS 1. The signal's whole
# marginal contribution over a fixed book is +0.24pp of CAGR and +0.20 of
# Sortino — real, and an order of magnitude smaller than the number the pivot
# was signed on, because that number was measured against the wrong opponent.
#
# So the opponent is now a PERSISTED SERIES rather than a scratchpad table: it
# gets a `portfolio_nav` like the stack, refreshed by the same weekly job, and
# the ranking and the digest compare them without anyone remembering to. The
# argument is verbatim the one that created `ms-stack` above — a strategy with
# no NAV cannot be measured at all — applied to the thing the stack must beat.
TREND_BASELINE_PORTFOLIO_ID = "ms-trend-baseline"

# WHICH book the baseline freezes, MEASURED rather than picked: it is the one
# the signal itself holds most often over the 418 monthly decisions —
# credit-spread-tight-yield-curve-flat, 197 of them (47.1%), against 38.3% for
# credit-spread-wide and 14.6% for the steep book. "The stack minus its signal"
# is therefore the book the stack spends most of its life in, which is the least
# arbitrary freeze available and the one a reader can check.
#
# A second measurement agrees, and it is the stronger argument: this book under
# the overlay reproduces the stack's max drawdown to four decimals (-20.61%).
# That is `V1_STRATEGY.md`'s own finding made mechanical — the covid trough that
# sets the stack's worst drawdown happened while this book was in force, so
# freezing it changes nothing about the number the -25% cap binds. The baseline
# and the stack fail identically, which is exactly what a control must do.
TREND_BASELINE_BOOK = "credit-spread-tight-yield-curve-flat"

# Decision key -> the seeded Portfolio vertex that IS that book (db/seed_data.py
# PORTFOLIOS). The live path needs it because `Proposal.defender_id` is a
# portfolio id, not a signal state. The entity ids keep their original
# growth/inflation/slowdown spelling: they already appear in committed EventLog
# payloads, which are append-only (seed_data's ms-growth-book note). This map is
# the ONE place the frozen spelling meets the renamed decision keys, so no other
# module has to know both vocabularies.
# `ms-growth-book` follows the WIDE-STEEP key, not the wide-flat one, and the
# choice is the frozen allocation rather than the name: that row holds
# SPY 50 / IWN 40 / GLD 10, which is the book wide-steep KEEPS. wide-flat took a
# new allocation, so it takes a new vertex (`ms-wide-flat-book`) instead of
# silently redefining what a committed EventLog id points at.
BOOK_PORTFOLIO_IDS: dict[str, str] = {
    "credit-spread-wide-yield-curve-flat": "ms-wide-flat-book",
    "credit-spread-wide-yield-curve-steep": "ms-growth-book",
    "credit-spread-tight-yield-curve-flat": "ms-inflation-book",
    "credit-spread-tight-yield-curve-steep": "ms-slowdown-book",
}

# The market-signal series and their trailing-median lookbacks. ~10y median
# (2520 trading days) with a 1y warm-up floor, matching the backtest.
CREDIT_SPREAD = "BAA10Y"
YIELD_SLOPE = "T10Y2Y"
MEDIAN_WINDOW_DAYS = 2520
MEDIAN_MIN_DAYS = 252
# THE TREND WINDOW, 300 since 2026-08-11 (owner signature) and 200 before it.
#
# 200 was never measured — it was inherited from the scratchpad backtest, where
# it is the convention every trend-following study uses, and it went unexamined
# through the whole ADR-007 validation. Swept for the first time on 2026-08-09
# because that day's finding said the drawdown belongs to the OVERLAY, so the
# overlay's own parameters are where an improvement could come from.
#
# Re-measured against the CURRENT rule (both trajectory knobs live) with the
# pricing bounded to each window — the correction of the same day:
#
#     window            sortino   cagr     turnover
#     full 1991-2026     +0.027   +0.36pp    68 -> 54
#     first 1991-2008    +0.011   +0.18pp    28 -> 22
#     second 2009-2026   +0.042   +0.53pp    39 -> 31
#
# Adopts on all three, drawdown unchanged everywhere, and turnover falls 21%.
# The turnover is not a side benefit: 14 fewer round trips a year at ADR-010's
# 23 bps is the cheapest part of the CAGR gain, and a slower signal is a less
# fitted one.
#
# 175 and 225 also adopted on the full sample and were REFUSED, each failing the
# half it was not fitted to — the out-of-sample split earning its keep on its
# first outing.
#
# TWO WINDOWS SINCE 2026-08-14 (owner signature), and the change is the OVERLAY'S
# SHAPE rather than its length: a sleeve is read against EVERY window here and
# each one breached redirects an equal share of it, so exposure steps 100/50/0
# instead of 100/0. The single 300-day line was all-or-nothing — it waited for
# the slow average and then moved the whole sleeve at once.
#
#     full 1991-2026   sortino 1.237 -> 1.321   maxDD -20.61% -> -15.73%   CAGR 11.27% -> 11.34%
#     train 1991-2008  sortino 1.465 -> 1.365   maxDD unchanged            CAGR 13.21% -> 12.34%
#     valid 2009-2026  sortino 1.076 -> 1.296   maxDD -20.61% -> -15.73%   CAGR  9.48% -> 10.45%
#
# 4.9 POINTS OF DRAWDOWN AT UNCHANGED RETURN, and it is the only drawdown lever
# two days of searching found — the books cannot move that number at all (seven
# configurations, identical to the basis point) and every other overlay family
# (neutral bands, N-day confirmation, single-window retuning) measured worse.
#
# IT IS AN ERA BET AND MUST BE READ AS ONE: the whole gain is post-2009 (the
# covid trough), and on 1991-2008 it costs 0.87pp of CAGR while improving nothing.
# Signed as a trade-off the owner took, not as a free lunch (docs/V1_STRATEGY.md).
MA_WINDOWS: tuple[int, ...] = (150, 300)

# Every ticker any book can hold — what `run_market_signal` must load prices for. The
# bug that once crippled this stack (docs/STRATEGY_COMPARISON.md correction note)
# was loading a prices dict MISSING IWN/VCIT, which then held flat at 0%; naming
# the set here makes that omission impossible to repeat silently.
STACK_TICKERS: tuple[str, ...] = ("SPY", "IWN", "GLD", "VCIT", "IEF")

# Instruments a HAVEN knob may name beyond the books' own five. Not the same
# question as "what the books hold": a haven is where the overlay flees to, and
# nothing says the best destination is already a sleeve.
#
# The M8b Worker proposed SHY and TIP as havens, and both were refused with the
# message "not a tradable sleeve with a price series" — which was FALSE and
# worth catching: SHY is active in the catalog with 8755 points back to
# 1991-10-29, exactly the history the 35-year walk needs. The real constraint
# was never data, it was that `run_market_signal` only loaded the five tickers
# the books use, so a haven outside that set had no series AT RUNTIME.
#
# SHY and TLT qualify (full history from 1991 and 1986). TIP does NOT and is
# deliberately absent: its series starts 2003-12-05, so a revision naming it
# could only ever be measured on two thirds of the sample, and a verdict from a
# different window is not comparable to the baseline it is judged against
# (docs/IMPROVEMENTS.md I-48).
HAVEN_CANDIDATES: tuple[str, ...] = ("SHY", "TLT")

# What the price loader must serve: the books' sleeves plus every instrument a
# haven knob is allowed to name. ONE set, so "the knob accepts it" and "the run
# has prices for it" cannot disagree — which is precisely how SHY came back as
# `KeyError: 'SHY'` from deep inside a pandas frame on 2026-08-09.
LOADABLE_TICKERS: tuple[str, ...] = (*STACK_TICKERS, *HAVEN_CANDIDATES)

# The stack is charged at the SAME per-order rate as every other NAV in the
# system (ADR-010): Saxo's real 23 bps. Was 20 here — which happened to be
# double `replay_cost_bps` AND double the "20 bps/rotation" the spec claimed,
# while every static book it is ranked against paid nothing.
COST_BPS = ratios.TRADING_COST_BPS

# Consecutive monthly decisions that must name the SAME new book before the
# stack switches (measured 2026-08-01, full 35y + split sample).
#
# `classify_regime` is a bare comparison against a trailing median, so a signal
# hovering at its own median flips the book on an arbitrarily small difference:
# of the 36 book changes over 409 monthly decisions, 25% were decided by a
# margin under 2% and 14 reversed within 3 months. Books barely overlap (wide is
# SPY/IWN/GLD, steep is VCIT/IEF/IWN), so such a flip is close to a 90% round
# trip. Waiting for confirmation lifts CAGR 9.85% -> 11.26% and Sortino
# 0.94 -> 1.11 at an UNCHANGED -23.8% drawdown, in both halves of the history
# split independently; the sweep degrades past ~4, so this is a real optimum,
# not "trade less" (buy-and-hold scores 10.32% CAGR but -52% drawdown).
#
# 3 is `regime_confirm_prints`' value, deliberately: one hysteresis convention
# across the project. A separate constant, NOT a read of that threshold —
# recalibrating the macro detector must never silently move the allocation.
# Holding the two candidate books during the wait (the literal "intersection"
# proposal) measured WORSE than simply waiting, and raised turnover.
CONFIRM_DECISIONS = 3

# THE PINNED WINDOW — every figure in the ANTI-DRIFT note above was measured
# over it, and it is what `run_market_signal` defaults to.
#
# ONE constant because it was THREE literals: this function's own `start=`/`end=`
# defaults and `rule_revision.FULL_WINDOW`, which existed precisely to name "the
# window `run_market_signal` defaults to" and could only do so by copying it.
# Same shape as every other defect this module has fixed — a fact stated in
# several places, with nothing making them agree.
PINNED_WINDOW: tuple[date, date] = (date(1991, 1, 1), date(2026, 7, 1))

# THE PINNED PAIR, AS CONSTANTS AND NO LONGER AS PROSE.
#
# Until 2026-08-12 these numbers existed only in the ANTI-DRIFT paragraph above,
# and NOTHING read them: no test, no CLI command, no chain step. The whole
# guarantee — "any OTHER divergence is drift and must be explained" — rested on
# a human remembering to run a REPL and compare against a docstring.
#
# It failed exactly as an unread number does. That paragraph told the reader to
# explain any divergence from 10.71% while the bold pair two lines up said
# 11.57%: the anti-drift REFERENCE had itself drifted, two supersessions behind,
# and no mechanism could notice because no mechanism looked.
#
# So the pair is a constant, `drift_violations` compares against it, and two
# callers run that comparison (a live-DB test and the Monday drift check whose
# verdict the digest renders). A deliberate supersession now moves this constant
# in the same commit or turns the check red — which is the git gate ADR-006 does
# not reach, held by a machine instead of by a memory.
#
# THE CAGR HERE IS 11.582% AND THE NOTE ABOVE SAYS 11.53%. That is not a third
# stale copy — it is the same run measured the only way a REFERENCE can be, and
# building this check is what exposed the difference.
#
# `run_market_signal(end=)` bounds the DECISION dates and not the pricing (the
# methodology error of 2026-08-11, fixed for the half-sample sweeps and never
# noticed for the headline). So the signed 11.57% is "decide to 2026-07-01, then
# hold the last book to the end of whatever data exists today" — a figure that
# MOVES every time a new price lands. Measured on the live DB, 2026-08-12:
#
#     priced to 2026-08-11 (free tail)  11.5305%   <- the signed figure
#     priced to 2026-07-01 (bounded)    11.5824%
#     max drawdown, both of them       -16.5004%
#
# A reference that drifts with the calendar cannot detect drift: pinned at the
# free-tail value, this check would have cried wolf within about three months on
# the passage of time alone. So `check_drift` bounds the pricing at the window's
# end and this constant is that bounded number. Same strategy, same window, same
# data — only the measurement is made repeatable. The drawdown is untouched by
# the tail (identical to six digits across every cut), which is why -20.61%
# needs no such restatement.
#
# OWNER CALL, stated rather than assumed: the prose figure stays as signed, and
# if you prefer the machine to check the free-tail number instead, this is the
# line to change — but it will then need re-pinning every quarter.
PINNED_CAGR = 0.11582
PINNED_MAX_DRAWDOWN = -0.16500

# WHAT COUNTS AS DRIFT, in percentage POINTS of the indicator.
#
# Not zero, and the reason is measured rather than chosen (I-48, restated in the
# note above): the seed's backfill start is `today - 35y` and therefore ROLLING,
# while the walk's start is fixed, and Yahoo restates adjusted closes
# retroactively. The 2026-08-03 re-seed moved CAGR by 0.04pp with 418 IDENTICAL
# decisions — the ground moving under a fixed marker. The module's own standing
# instruction is "treat a divergence under ~0.1pp as that; anything larger still
# has to be explained", and this is that sentence made executable.
#
# The drawdown gets the same band and will not need it: across twelve replay
# start dates its spread was 0.00% (mechanical/rule_revision.py), so it moves
# when the STRATEGY moves and essentially never otherwise.
DRIFT_TOLERANCE_PP = 0.1


def _windows_text() -> str:
    """`MA_WINDOWS` as prose — "150/300". Generated, like everything else in
    `describe_rule`: a hand-typed window has gone stale within a day of moving,
    three times."""
    return "/".join(str(w) for w in MA_WINDOWS)


def describe_rule(caps: Caps | None = None) -> str:
    """The stack's rule as prompt text, GENERATED FROM THE CONSTANTS ABOVE.

    The Worker is asked to challenge this rule, which means it has to know what
    the rule IS. Until now nothing told it: its context carried the month's
    DECISION (book, weights, which sleeves were below trend) but never the
    mechanism, so it reasoned about the rule from whatever it recalled.

    It recalled wrong. Across the two M8b runs it twice described the overlay as
    covering "SPY only" while `TREND_SLEEVES` includes GLD — and its own run's
    logs printed `below-trend=['SPY','GLD']` on the very dates it said so. A
    third reading stated it correctly. Sound critiques, unreliable descriptions
    of the status quo, and no way for it to tell which it was doing.

    Generated rather than written out, for the same reason `describe_schema`
    reads the tables from SQLite: a hand-copied rule is wrong at the first edit
    and wrong SILENTLY, which is the exact failure this repairs. Change
    `TREND_SLEEVES` and this text changes with it.

    That promise was only half kept, and it broke the same day. The sleeve list
    and haven name interpolated, but the SENTENCE around them was hand-written
    and said the overlay redirects below-trend sleeves to IEF, full stop — six
    hours after the haven itself became trend-checked with a cash fallback. The
    Worker read it, believed a 100% IEF book was still reachable, and spent an
    innovation proposing the fallback that already existed.

    IT BROKE AGAIN ON 2026-08-11, the same way and by my own hand: the two
    spread-trajectory knobs went live and this text did not mention them. Five
    replayed dates bought six innovations and FOUR re-proposed a feature that
    was already running — the veto twice, the sleeve gate once, both together at
    absurd values once. The measurement machinery worked perfectly on all four
    and rejected them, which is the loop doing its job on a question nobody
    should have had to ask.

    So the rule text now states EVERY knob that is on, including the ones added
    last, and `test_describe_rule_states_every_active_knob` pairs it with
    `rule_revision.TESTABLE_PARAMETERS` so the next knob cannot ship silent.
    Knobs that are OFF are deliberately absent: this describes the rule that
    decided, not the rule's option list.

    It does NOT breach the Worker's unawareness of Planner/Writeback/storage
    (worker/agent.py): the stack is an INVESTMENT instrument whose output the
    Worker already reads and is invited to challenge. Telling it how the
    instrument works is telling it about the market, not about the plumbing."""
    caps = caps or Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)
    books = "\n".join(
        f"    {name}: " + ", ".join(f"{t} {w:.0f}" for t, w in holdings.items())
        for name, holdings in BOOKS.items()
    )
    # The checked set is the sleeves PLUS the haven — `walk_decisions` computes
    # `below_trend` over exactly this set, and the haven's own read is what
    # selects TREND_FALLBACK_HAVEN. Deriving it here rather than naming the
    # sleeves alone is what keeps this text true when the overlay changes.
    checked = (*TREND_SLEEVES, TREND_HAVEN)
    # The trajectory knobs, stated ONLY when they are on — an "off" line would
    # invite the Worker to propose switching on what is already off, which is
    # the mirror of the defect this repairs.
    trajectory = ""
    if SPREAD_SPEED_VETO is not None:
        trajectory += (
            f"  4. Spread TRAJECTORY veto: when the spread is above its median but still\n"
            f"     widening faster than {SPREAD_SPEED_VETO:g} points per "
            f"{SPREAD_SPEED_LOOKBACK_DAYS} days, the wide reading is\n"
            "     DEFERRED and the slope decides the book instead.\n"
        )
    if SPREAD_STRESS_SLEEVE_GATE is not None:
        trajectory += (
            f"  5. Spread-stress sleeve gate: under those same conditions "
            f"({SPREAD_STRESS_SLEEVE_GATE:g} per\n"
            f"     {SPREAD_SPEED_LOOKBACK_DAYS} days), these sleeves "
            f"({', '.join(STRESS_GATED_SLEEVES)}) are sent to the\n"
            f"     haven whatever their own {_windows_text()} lines say.\n"
        )
    return (
        "THE MECHANICAL RULE THAT DECIDED THIS MONTH (market-signal stack)\n"
        # "three books" was written here when there were three, and the fourth
        # arrived on 2026-08-13 — the same defect this whole function exists to
        # repair, one line below the sentence that repairs it. Counted now.
        f"  1. Credit spread (BAA10Y) vs its {MEDIAN_WINDOW_DAYS // 252}-year trailing\n"
        f"     median, and yield slope (T10Y2Y) vs its own, select ONE of {len(BOOKS)} books\n"
        "     (the two indicators are read independently — their 2x2 names the state):\n"
        f"{books}\n"
        f"  2. A book change is applied only after {CONFIRM_DECISIONS} consecutive\n"
        "     monthly decisions agree (hysteresis against boundary flip-flop).\n"
        f"  3. GRADUATED trend overlay on {_windows_text()}-day moving averages:\n"
        f"     {', '.join(checked)} — and ONLY these — are checked against EACH of\n"
        f"     their own lines, and every line breached moves 1/{len(MA_WINDOWS)} of that\n"
        "     sleeve to the haven. So a sleeve is held in full, half out, or fully out —\n"
        "     not all-or-nothing. Price and lines are read at the PREVIOUS close (the\n"
        "     rule decides on what was knowable before the order goes in).\n"
        f"     What is redirected goes to {TREND_HAVEN}; and when {TREND_HAVEN} is ITSELF\n"
        f"     below EVERY one of its lines, the destination becomes\n"
        f"     {TREND_FALLBACK_HAVEN} instead — including the {TREND_HAVEN} a book\n"
        "     already holds. Sleeves outside the checked set are held at book\n"
        "     weight whatever their own trend does.\n"
        + trajectory
        # THE CAPS ARE PART OF WHAT DECIDED THE MONTH, so a text that omits them
        # describes a rule the stack does not follow. Raised THREE times across
        # independent runs — "ms-stack carries max_single_asset_pct=50, yet the
        # overlay produces a 90% IEF sleeve" — and every time it was a correct
        # reading of a contradiction that only looked like one, because the
        # haven exemption (ADR-007 second addendum, extended 2026-08-08) lives
        # in code the Worker cannot see. Three innovations spent on a question
        # one sentence answers.
        + (
            f"  Binding caps: no sleeve above {int(caps.max_single_asset_pct)}% EXCEPT the haven "
            f"chain ({', '.join(sorted(HAVEN_EXEMPT))}),\n"
            "     which is a flight to safety and not a conviction bet, so a 90-100% haven\n"
            "     book is legal by design.\n"
        )
        + "  The rule reads PRICES only: no macro regime, no policy, no positioning."
    )


@dataclasses.dataclass(frozen=True)
class TrendRead:
    """One trend sleeve's overlay read at a decision date. Carries the two
    numbers the comparison was made on, not just its boolean answer: "SPY is
    below trend" is unauditable, "SPY 512.40 vs MA 548.10" is.

    BOTH NUMBERS ARE AS OF THE PREVIOUS CLOSE since 2026-08-13, not the decision
    date's — the rule decides on what was knowable before the order goes in
    (`StackSeries.decision_prices`). Anything rendering these must not label them
    with the decision date: the digest line that said "redirected to IEF" while
    the target was cash is the same defect, and a journal that contradicts the
    decision is worse than no journal."""

    price: float
    # ONE PER `MA_WINDOWS` LINE, in that order; None before a line has warmed up.
    moving_averages: tuple[float | None, ...]
    # The FRACTION of the sleeve redirected: one vote per breached line, so two
    # windows give 0 / 0.5 / 1. Replaced the boolean `below` on 2026-08-14 when
    # the overlay became graduated — a bool cannot say "half out", and a rule
    # that moves half a sleeve needs a journal that can record it.
    share: float
    # WHY it is out, when the price alone does not say so. A sleeve redirected
    # by `SPREAD_STRESS_SLEEVE_GATE` reads share 1.0 with a price ABOVE every
    # moving average, and a reader comparing the numbers would call that a bug.
    # Recording the cause is the same discipline as the digest line that said
    # "redirected to IEF" while the target was cash (fixed 2026-08-08): a
    # journal that contradicts the decision is worse than no journal.
    credit_gated: bool = False

    @property
    def below(self) -> bool:
        """Any weight redirected at all. Kept as the word every reader outside
        this module already uses (the digest, the Worker context, the logs):
        they ask "is this sleeve out", and the graduated answer belongs to the
        overlay, not to the sentence describing it."""
        return self.share > 0.0

    @property
    def moving_average(self) -> float | None:
        """The SLOWEST line that has warmed up — what a one-number rendering
        should show, since it is the one the old single-window rule used and the
        one a reader comparing against `price` expects. None during warm-up."""
        ready = [ma for ma in self.moving_averages if ma is not None]
        return ready[-1] if ready else None


@dataclasses.dataclass(frozen=True)
class Decision:
    """ONE monthly decision, with everything that produced it.

    This is the audit record the live path persists (and the replay could): the
    raw signal state, the book actually HELD after hysteresis, the sleeves below
    their moving average, and the post-overlay target. `changed` is True iff
    the target differs from the previous decision's — i.e. iff this decision
    moves money.

    Medians are `None` during warm-up (before MEDIAN_MIN_DAYS of history), which
    is the state `classify_regime` answers with the credit-spread-wide default;
    keeping it None rather than NaN is what lets the whole record serialise
    straight into a Proposal's `market_context` as valid JSON."""

    date: pd.Timestamp
    signalled: str  # what the signal said THIS decision, before hysteresis
    held: str  # the book actually in force after hysteresis
    pending: str | None  # a different book waiting for confirmation
    pending_count: int  # consecutive decisions it has been waiting
    spread: float
    spread_median: float | None
    slope: float
    slope_median: float | None
    trend: dict[str, TrendRead]  # per TREND_SLEEVES sleeve
    target: dict[str, float]  # post-overlay effective allocation
    changed: bool

    @property
    def below_trend(self) -> tuple[str, ...]:
        """The sleeves the overlay redirected to TREND_HAVEN this decision."""
        return tuple(t for t, read in self.trend.items() if read.below)


@dataclasses.dataclass(frozen=True)
class MarketSignalRun:
    """A backtest/replay of the stack over a window.

    `targets` maps each CHANGE date -> the book that took effect (only dates
    where the allocation actually changed, matching `shadow_book_nav`'s
    time-varying target contract); `turnover` is its summed round-trip turnover.
    `decisions` is the FULL journal — every decision date, changed or not — which
    is what the live path needs: a decision that lands on the held book still
    advances the hysteresis counter and still has to be recorded.
    `raw_series` keeps each input UN-forward-filled, so the live path can report
    the date every input became knowable (ADR-003)."""

    nav: pd.Series
    targets: dict[pd.Timestamp, dict[str, float]]
    turnover: float
    decisions: list[Decision] = dataclasses.field(default_factory=list)
    raw_series: dict[str, pd.Series] = dataclasses.field(default_factory=dict)


# -- pure decision logic (no I/O — unit-testable, shared with the live path) --


# THE WORKER'S MOST REPEATED CRITIQUE, made measurable (2026-08-09). OFF by
# default: `None` leaves `classify_regime` exactly as ADR-007 validated it, and
# `rule_revision` can switch it on to earn a 35-year verdict before anything is
# adopted.
#
# Six distinct formulations across independent dates and runs said one thing:
# the book is selected on the LEVEL of the spread against its trailing median,
# and should be selected on its TRAJECTORY. At 2020-03-02 — "BAA10Y 2.27 vs
# median 2.59 says 'tight'. The level is tight; the TRAJECTORY is not. 2008
# taught exactly this lesson: the level-vs-median read stays 'tight' longest
# precisely when widening is fastest, because the median trails and the level
# starts low."
#
# THE DIRECTION IS THE OPPOSITE OF WHAT "VETO" SUGGESTS, and getting it backwards
# would measure the reverse of the claim. `credit-spread-wide` is the RISK-ON
# book (SPY 50 / IWN 40 / GLD 10): the countercyclical bet that stress is already
# priced. The Worker's objection is to taking that bet while the stress is still
# forming — "the spread is wide because a credit event is still forming"
# (2008-07-01). So the veto DEFERS the wide reading while the spread is still
# widening faster than the threshold, falling through to the curve branch and
# its lighter book. It does not accelerate into it.
#
# Units are the spread's own (percentage points of BAA10Y) over
# SPREAD_SPEED_LOOKBACK_DAYS.
SPREAD_SPEED_VETO: float | None = 0.20

# THE SAME THEME'S OTHER MECHANISM, and the two are not the same claim.
#
# Reading the six wordings of the velocity critique showed at least three
# distinct proposals inside one theme (2026-08-11): defer the wide book while
# the spread widens (the veto above), ENTER it on speed regardless of level
# (this), and redirect the equity sleeves on spread direction without waiting
# for the 200d (not expressible yet). Grouping them was right; treating them as
# interchangeable was not.
#
# This is the countercyclical bet taken EARLIER: "when BAA10Y speed and
# acceleration are both strongly positive, treat the credit regime as
# spread-wide regardless of the level-vs-median" (Worker, verbatim, with its own
# candidate of +0.20). The premise is ADR-007's own — stress that is priced
# precedes strong forward returns — so a spread gapping out is the signal
# arriving before the level catches up, and the 200d overlay still guards the
# downside.
#
# PRECEDENCE, since the two knobs pull opposite ways: the trigger is an ENTRY on
# speed and is read first; the veto questions the LEVEL-based read and applies
# only to it. Setting both is coherent but measures a rule nobody proposed, so
# they are swept separately.
SPREAD_SPEED_WIDE_TRIGGER: float | None = None

# MECHANISM (c) OF THE VELOCITY THEME, and the one four separate critiques asked
# for: "gate the credit-spread-wide book's equity sleeves on spread DIRECTION,
# not only on the 200d price trend", "credit-regime gate on the IWN sleeve",
# "credit-contagion gate on the small-cap sleeve".
#
# The complaint underneath all four: the book is SELECTED because credit is
# impaired, and it then holds 90% equities — the most credit-sensitive exposure
# there is — with nothing but each sleeve's own price trend between the stack
# and that bet. The 200d reads one price at a time and cannot carry the
# cross-signal that chose the book.
#
# Verbatim (Worker, 2026-08): "When BAA10Y is above its trailing median AND its
# trailing speed is positive, treat the selected book's equity sleeves as
# below-trend — redirect them to the haven — regardless of price vs the 200d."
# Both conditions, as proposed: a wide LEVEL and a widening TRAJECTORY. The
# proposal said a 3-month speed and this uses the 30-day series the rule already
# computes, which is a deviation worth knowing when reading the verdict.
#
# Distinct from the veto, which defers the whole BOOK: this keeps the book and
# empties its equity, so the two are separable hypotheses and are swept apart.
SPREAD_STRESS_SLEEVE_GATE: float | None = 0.20

# WHICH sleeves that gate empties. A knob since 2026-08-11, and the Worker found
# the reason four wordings in: the veto's escape route is the slope-decided
# tight-steep book, which holds VCIT 50 — investment-grade CREDIT — and the gate
# was emptying the equities beside it while leaving the sleeve most directly
# exposed to the very spread that triggered the gate.
#
# Verbatim: "On 2008-11-03 BAA10Y is 5.53 vs median 2.33 with speed 1.43; that
# is exactly the condition under which investment-grade credit should not be
# treated as a tight-spread carry sleeve."
#
# A HOLE THE VETO ITSELF OPENED. Before the veto shipped the day before, the
# stack rarely reached the tight-steep book during credit stress; deferring the
# wide reading is what routes it there. The critique could not have existed
# earlier, which is the clearest evidence yet that the Worker reads the rule it
# is actually given rather than a memorised one.
#
# Defaults to the equities, i.e. to the behaviour that was measured and adopted.
STRESS_GATED_SLEEVES: tuple[str, ...] = EQUITY_SLEEVES

# Matches `system_thresholds.derivative_lookback_short`, and the speed itself is
# computed by `market.derivatives.compute_derivatives` rather than differenced
# here — CLAUDE.md's "two implementations must produce the same numbers" applies
# to a knob that will be compared against readings the Worker saw.
SPREAD_SPEED_LOOKBACK_DAYS = 30


def classify_regime(
    spread: float,
    spread_median: float | None,
    slope: float,
    slope_median: float | None,
    spread_speed: float | None = None,
) -> str:
    """The market-signal regime (docs/V1_STRATEGY.md "Regime signal"): the two
    indicators are read INDEPENDENTLY and their 2x2 names the state — credit
    spread vs its 10y median (WIDE = stress is PRICED, so the countercyclical
    response is to buy risk), yield slope vs its own (FLAT / STEEP).

    THE 2x2 IS NEW ON 2026-08-13 and replaces an asymmetry: the wide branch used
    to return before consulting the slope, so `credit-spread-wide` covered both
    curve states with one book. Measured on each half of the sample separately,
    those two states rank the five sleeves in opposite orders — see BOOKS, which
    carries the numbers and the reason only ONE of the two books changed.

    The returned key names the SIGNAL STATE, not a macro regime — see BOOKS.

    A missing median (warm-up, before MEDIAN_MIN_DAYS of history) reads WIDE,
    exactly as the backtest did rather than stalling; the slope's own warm-up
    reads FLAT, so the warm-up book is `credit-spread-wide-yield-curve-flat` and
    the trend overlay still guards its downside."""
    fast = (
        SPREAD_SPEED_WIDE_TRIGGER is not None
        and spread_speed is not None
        and not pd.isna(spread_speed)
        and spread_speed > SPREAD_SPEED_WIDE_TRIGGER
    )
    wide = spread_median is None or pd.isna(spread_median) or spread > spread_median
    # See SPREAD_SPEED_VETO: defer the risk-on book while the crack is still
    # opening. A missing speed (warm-up) never vetoes — the rule falls back to
    # the level read it has always used. `fast` (SPREAD_SPEED_WIDE_TRIGGER,
    # measured nil and OFF) forces the wide read before the veto can defer it.
    if wide and not fast and SPREAD_SPEED_VETO is not None and spread_speed is not None:
        wide = pd.isna(spread_speed) or spread_speed <= SPREAD_SPEED_VETO
    # THE SLOPE IS READ ON BOTH BRANCHES since the 2x2 (2026-08-13). Warm-up —
    # a missing slope median — reads FLAT on both, which keeps the pre-2x2
    # warm-up behaviour on the tight side and gives the wide side the
    # equity-tilted book the backtest defaulted to.
    steep = slope_median is not None and not pd.isna(slope_median) and slope >= slope_median
    curve = "steep" if steep else "flat"
    return f"credit-spread-{'wide' if wide or fast else 'tight'}-yield-curve-{curve}"


def apply_trend_overlay(book: Mapping[str, float], shares: Mapping[str, float]) -> dict[str, float]:
    """Redirect each redirectable sleeve to the haven, by the FRACTION `shares`
    gives it. Weights merge additively — if a book already holds the haven (the
    credit-spread-tight-yield-curve-steep book holds IEF), a redirected sleeve
    adds to it.

    GRADUATED SINCE 2026-08-14: `shares` maps ticker -> 0..1 where it used to be
    a set of tickers that were fully out. A sleeve below one of two lines moves
    half its weight and keeps the rest, which is the whole point of the change
    (see MA_WINDOWS). A share of 0 or 1 reproduces the old set exactly.

    THE HAVEN IS CHOSEN BY THE SAME TEST it applies to everything else: when
    TREND_HAVEN is itself FULLY out, the destination becomes TREND_FALLBACK_HAVEN.
    `shares` therefore carries the haven's own read as well as the sleeves' — see
    `walk_decisions`, which computes it for `TREND_SLEEVES + (TREND_HAVEN,)`.

    THE DESTINATION IS BINARY WHILE THE ORIGIN IS GRADUATED, and that asymmetry
    is measured rather than overlooked. Splitting the flight by the haven's own
    share — half to IEF, half to cash when IEF is below one line of two — was
    measured on 2026-08-14 and is a wash: the drawdown is identical (-15.73%),
    Sortino and CAGR move inside the 0.71% noise floor, and turnover rises. Same
    result the haven check itself gave in August ("C ALONE DOES NOTHING"), for
    the same mechanical reason: the haven's half-state is rare and, over a month
    of holding, cash and IEF barely differ. Recorded here so the question is
    answered by a number the next time it is asked.

    A book that HOLDS the haven as a sleeve (steep: IEF 40) also has that weight
    moved when the haven is below trend — it is the same asset failing the same
    test, and leaving it in place while refusing to redirect INTO it would be
    incoherent."""
    haven = TREND_FALLBACK_HAVEN if shares.get(TREND_HAVEN, 0.0) >= 1.0 else TREND_HAVEN
    adjusted: dict[str, float] = {}
    for ticker, weight in book.items():
        # REDIRECTABLE = trend-checked OR stress-gated. The second half was
        # missing and is the other layer of one constraint: `walk_decisions`
        # could mark VCIT below, and this loop then ignored it because VCIT is
        # not in the checked set, so the gate measured as exactly zero. One
        # implausible number, two places to fix.
        share = (
            shares.get(ticker, 0.0)
            if ticker in (*TREND_SLEEVES, TREND_HAVEN, *STRESS_GATED_SLEEVES)
            else 0.0
        )
        moved = float(weight) * share
        kept = float(weight) - moved
        # `> _EPSILON`, not `> 0`: a share of exactly 1.0 leaves a float residue
        # that would otherwise seed a 1e-14 sleeve into every target, and those
        # rows reach the digest, the caps and the Proposal payload.
        if kept > _EPSILON:
            adjusted[ticker] = adjusted.get(ticker, 0.0) + kept
        if moved > _EPSILON:
            adjusted[haven] = adjusted.get(haven, 0.0) + moved
    return adjusted


# Weight below which a sleeve is not a position but float noise (see above).
_EPSILON = 1e-9


def advance_hysteresis(
    held: str | None, pending: str | None, pending_count: int, signalled: str
) -> tuple[str, str | None, int]:
    """One step of the `CONFIRM_DECISIONS` hysteresis: the stack stays in the
    book it is committed to until a DIFFERENT book has been named that many
    decisions in a row. The first decision commits immediately (`held is None` —
    there is nothing to hold yet), and a candidate that flickers back resets the
    count. Returns the state AFTER the step: `(held, pending, pending_count)`.

    Pulled out of the walk so the state machine is one testable function rather
    than a loop body — and so the live path can state its carried-over state in
    the same three names it reads back from the previous decision."""
    if held is None or signalled == held:
        return signalled, None, 0
    count = pending_count + 1 if pending == signalled else 1
    if count >= CONFIRM_DECISIONS:
        return signalled, None, 0
    return held, signalled, count


def walk_decisions(
    dates: Sequence[pd.Timestamp],
    spread: pd.Series,
    slope: pd.Series,
    spread_median: pd.Series,
    slope_median: pd.Series,
    moving_averages: Mapping[int, Mapping[str, pd.Series]],
    prices: Mapping[str, pd.Series],
    spread_speed: pd.Series | None = None,
) -> list[Decision]:
    """Walk the decision clock and record EVERY decision — the full journal.

    `moving_averages` is keyed by WINDOW then by ticker since 2026-08-14 — one
    frame per `MA_WINDOWS` line, because the overlay now votes across them.

    The trend overlay is NOT damped: it re-reads every moving average on every
    decision, so the drawdown control keeps reacting while a book switch waits
    out its confirmation window."""
    decisions: list[Decision] = []
    previous: dict[str, float] | None = None
    held: str | None = None
    pending: str | None = None
    pending_count = 0
    for t in dates:
        signalled = classify_regime(
            _at(spread, t),
            _at(spread_median, t),
            _at(slope, t),
            _at(slope_median, t),
            None if spread_speed is None else _at(spread_speed, t),
        )
        held, pending, pending_count = advance_hysteresis(held, pending, pending_count, signalled)
        # THE HAVEN IS READ TOO, not only the sleeves: `apply_trend_overlay`
        # needs its own trend to decide whether redirecting INTO it is still
        # sound (see that function). It is reported in `trend` alongside the
        # sleeves, so the digest and the Worker see the same read the rule used.
        trend = {
            ticker: _trend_read(
                _at(prices[ticker], t),
                [_at(moving_averages[w][ticker], t) for w in moving_averages],
            )
            for ticker in (*TREND_SLEEVES, TREND_HAVEN)
            if ticker in prices and all(ticker in frame for frame in moving_averages.values())
        }
        # THE CREDIT GATE, applied to the READS rather than beside them, so the
        # journalled trend and the book that follows from it cannot disagree.
        #
        # IT MUST REACH SLEEVES THE OVERLAY DOES NOT CHECK, and the first version
        # could not — it rewrote entries of `trend`, which is built only for
        # `TREND_SLEEVES + TREND_HAVEN`, so gating VCIT did literally nothing
        # and measured as EXACTLY zero on all three windows. That zero read like
        # a finding about the market ("the veto path never holds VCIT") and was
        # a bug in this loop; the implausibility of three identical zeros is
        # what exposed it.
        #
        # A gated sleeve outside the checked set gets a read of its own, marked
        # `credit_gated`, so the journal explains a redirect the overlay never
        # ordered. Being stress-gated does NOT make a sleeve trend-checked: the
        # two memberships are separate sets and each was measured on its own.
        #
        # VCIT WAS THIS COMMENT'S EXAMPLE UNTIL 2026-08-14 and is no longer one:
        # it joined `TREND_SLEEVES` that day on an owner conviction call (see
        # that constant), so it now has both reads. It is still NOT in
        # `STRESS_GATED_SLEEVES` — gating the credit sleeve was measured
        # separately and rejected on every window, and one override does not
        # carry the other. The branch below stays because the DISTINCTION is
        # real and a future knob can put a sleeve in one set and not the other;
        # it is simply no longer exercised by the default configuration.
        if SPREAD_STRESS_SLEEVE_GATE is not None and spread_speed is not None:
            median = _at(spread_median, t)
            speed = _at(spread_speed, t)
            stressed = (
                not pd.isna(median)
                and _at(spread, t) > median
                and not pd.isna(speed)
                and speed > SPREAD_STRESS_SLEEVE_GATE
            )
            if stressed:
                for ticker in STRESS_GATED_SLEEVES:
                    read = trend.get(ticker)
                    if read is not None:
                        # FULLY out, whatever the lines said: the gate overrides
                        # the price read rather than adding a vote to it.
                        trend[ticker] = dataclasses.replace(
                            read, share=1.0, credit_gated=read.share < 1.0
                        )
                    elif ticker in prices:
                        trend[ticker] = TrendRead(
                            price=_at(prices[ticker], t),
                            moving_averages=tuple(
                                _opt(_at(frame[ticker], t)) if ticker in frame else None
                                for frame in moving_averages.values()
                            ),
                            share=1.0,
                            credit_gated=True,
                        )
        # `ticker`, not `t` — `t` is the decision date in this scope, and while
        # a generator expression has its own, reusing the name here reads as a
        # shadow to anyone auditing the walk.
        book = apply_trend_overlay(
            BOOKS[held], {ticker: read.share for ticker, read in trend.items()}
        )
        decisions.append(
            Decision(
                date=t,
                signalled=signalled,
                held=held,
                pending=pending,
                pending_count=pending_count,
                spread=_at(spread, t),
                spread_median=_opt(_at(spread_median, t)),
                slope=_at(slope, t),
                slope_median=_opt(_at(slope_median, t)),
                trend=trend,
                target=book,
                changed=book != previous,
            )
        )
        previous = book
    return decisions


def build_targets(
    dates: Sequence[pd.Timestamp],
    spread: pd.Series,
    slope: pd.Series,
    spread_median: pd.Series,
    slope_median: pd.Series,
    moving_averages: Mapping[int, Mapping[str, pd.Series]],
    prices: Mapping[str, pd.Series],
    spread_speed: pd.Series | None = None,
) -> dict[pd.Timestamp, dict[str, float]]:
    """The change-point map `shadow_book_nav` consumes: a target ONLY on the
    dates the book actually changes (a monthly re-evaluation that lands on the
    same book pays no turnover). A pure projection of `walk_decisions` — the
    replay and the live path cannot drift because there is only one walk.

    `spread_speed` IS FORWARDED, and its absence here was a trap rather than a
    live bug: both trajectory knobs read that series, so a caller that omitted it
    got a projection of a DIFFERENT rule — the pre-2026-08-11 one — under a
    docstring promising it could not drift. `run_market_signal` builds its own
    change-point map inline and passes the speed, which is why nothing measured
    wrong; this signature is what keeps that true for the next caller."""
    decisions = walk_decisions(
        dates, spread, slope, spread_median, slope_median, moving_averages, prices, spread_speed
    )
    return {d.date: d.target for d in decisions if d.changed}


def _at(series: pd.Series, t: pd.Timestamp) -> float:
    """Point read that tolerates a decision date off the series index (returns
    NaN), so `classify_regime`'s warm-up default fires instead of a KeyError."""
    value = series.get(t)
    return float("nan") if value is None else float(value)


def _trend_read(price: float, moving_averages: Sequence[float]) -> TrendRead:
    """One sleeve's overlay read across every `MA_WINDOWS` line.

    `share` is the FRACTION of the sleeve the overlay redirects: one vote per
    window, so two windows give 0 / 0.5 / 1. A missing average (warm-up) is NOT
    below trend and NOT a vote — the overlay stays out of the way until a line
    has enough history to speak, the same "unmeasured is not bad" rule
    `gates.drawdown_ok` applies. A sleeve whose windows have ALL failed to warm
    up therefore reads share 0.0 rather than dividing by zero."""
    lines = tuple(_opt(ma) for ma in moving_averages)
    ready = [ma for ma in lines if ma is not None]
    breached = sum(1 for ma in ready if price < ma)
    return TrendRead(
        price=price,
        moving_averages=lines,
        share=breached / len(ready) if ready else 0.0,
    )


def _opt(value: float) -> float | None:
    """NaN -> None, so a warm-up median serialises as JSON `null` rather than
    the bare token `NaN`, which `json.loads` accepts but no other reader does."""
    return None if pd.isna(value) else value


# -- gate confrontation (the caps still BIND the adopted stack — CLAUDE.md) ---


def cap_violations(run: MarketSignalRun, caps: Caps, stack_drawdown: float | None) -> list[str]:
    """The binding-cap confrontation M6-bis's DoV asserts is empty. Every target
    book must clear the single-asset cap (now 50) EXCEPT the trend-haven sleeve,
    and the STACK's realized drawdown must clear the drawdown cap (now -25%,
    applied to the stack, not to each book standalone — ADR-007). Returns the
    failing gate names, [] if none.

    The haven CHAIN is exempted from the single-asset cap (`HAVEN_EXEMPT`:
    ADR-007 addendum choice (a) for IEF, extended to the cash fallback by the
    owner on 2026-08-08): the overlay's flight-to-safety can pile both
    equity/gold sleeves into IEF (~90% in risk-off) and, when IEF is itself
    below trend, all of it into cash — the deliberate drawdown control, not a
    conviction bet. Uses the SAME `gates.py` predicate the live Writeback runs,
    with the same exemption, so a book blocked live was blocked here too.

    A BUILD-TIME check over a whole backtest, deliberately not the live gate:
    the live path's equivalent (`writeback.market_signal_gates`) sees one
    decision, this sees every target the run ever held, and it is the drawdown
    leg that separates them — here it is the WHOLE-WINDOW figure the DoV
    asserts, whereas live the rule is a 36-month rolling ALERT and never blocks
    (ADR-009). Called by the M6-bis validation and by `test_market_signal.py`."""
    violations: list[str] = []
    for t, book in sorted(run.targets.items()):
        if not concentration_ok(book, caps, exempt=HAVEN_EXEMPT):
            violations.append(f"max_single_asset_pct@{t.date()}")
    if not drawdown_ok(stack_drawdown, caps):
        violations.append("max_drawdown_pct@stack")
    return violations


def drift_violations(metrics: NavMetrics) -> list[str]:
    """The ANTI-DRIFT check, made executable: how the freshly measured stack
    differs from the pinned pair, [] when it does not. One string per diverging
    indicator, carrying both numbers and the gap — "cagr" alone would send the
    reader back to the REPL this check exists to replace.

    PURE, over an already-measured `NavMetrics`, for the same reason
    `cap_violations` is pure over an already-walked run: the rule is testable on
    synthetic numbers without a 35-year database, and the two callers that DO
    need one (the live-DB test, the Monday check the digest renders) share this
    one comparison rather than each writing their own.

    A MISSING indicator is not drift — it is a window too short to measure, the
    same "unmeasured is not bad" rule `gates.drawdown_ok` applies. The caller is
    what must decide whether an unmeasurable window is itself worth reporting;
    here it simply cannot be a violation."""
    checks = (
        ("cagr", metrics.cagr, PINNED_CAGR),
        ("max_drawdown", metrics.max_drawdown, PINNED_MAX_DRAWDOWN),
    )
    violations = []
    for name, measured, pinned in checks:
        if measured is None:
            continue
        gap_pp = (measured - pinned) * 100.0
        if abs(gap_pp) > DRIFT_TOLERANCE_PP:
            violations.append(
                f"{name} {measured * 100:.2f}% vs pinned {pinned * 100:.2f}% "
                f"({gap_pp:+.2f}pp, tolerance {DRIFT_TOLERANCE_PP:.2f}pp)"
            )
    return violations


@dataclasses.dataclass(frozen=True)
class DriftCheck:
    """One anti-drift verdict — what was measured, over what, and whether it
    still matches the pinned pair.

    `measurable` is False when the database cannot answer the question at all,
    which is NOT a pass and must never render as one: an as-of snapshot bounded
    at 2008 (db/as_of_snapshot.py) prices seventeen years and would report a
    catastrophic 'drift' against a 35-year figure. The verdict says so instead,
    with the reason, so a silent skip cannot be mistaken for a clean run."""

    measurable: bool
    cagr: float | None
    max_drawdown: float | None
    violations: list[str]
    reason: str | None = None

    @property
    def drifted(self) -> bool:
        return self.measurable and bool(self.violations)

    def as_payload(self) -> dict[str, object]:
        """The journal shape (`market_signal_cycle.DRIFT_EVENT`) — and what the
        digest alert reads back. Both numbers are recorded even when they pass:
        a check whose passes leave no trace cannot answer "did it run", which is
        the failure mode mechanical/alerts.py exists to name."""
        return {
            "window": [PINNED_WINDOW[0].isoformat(), PINNED_WINDOW[1].isoformat()],
            "measurable": self.measurable,
            "reason": self.reason,
            "cagr": self.cagr,
            "max_drawdown": self.max_drawdown,
            "pinned_cagr": PINNED_CAGR,
            "pinned_max_drawdown": PINNED_MAX_DRAWDOWN,
            "tolerance_pp": DRIFT_TOLERANCE_PP,
            "violations": self.violations,
        }


async def check_drift(db: InvestmentDB) -> DriftCheck:
    """Re-measure the stack over `PINNED_WINDOW` and confront the pinned pair.

    A SECOND walk, deliberately, and not a reuse of the live cycle's: that one
    runs to `today` and this one must run to the window the pair was measured
    over, or the comparison degrades a little more every week that passes and
    the check ends up reporting the calendar. It costs ~0.5s on the live DB
    against a monthly decision — the entire reason this was never automated was
    assumed rather than measured.

    UNMEASURABLE rather than drifted when the priced NAV stops short of the
    window's end. `run_market_signal(end=)` bounds the DECISION dates and not
    the pricing, so `stack_metrics(until=)` is what actually bounds the
    measurement (the methodology error of 2026-08-11) — and a database that
    simply has no data past 2008 produces a genuine, correctly measured
    seventeen-year figure that is not comparable to the pinned one."""
    try:
        run = await run_market_signal(db, start=PINNED_WINDOW[0], end=PINNED_WINDOW[1])
    except ValueError as exc:
        # The refusals `run_market_signal` raises on a missing sleeve or signal
        # series. A real failure, but freshness has its own louder alert and
        # this check must not be what turns a data outage into a drift alarm.
        return DriftCheck(False, None, None, [], f"the stack could not be run ({exc})")
    nav = run.nav.dropna()
    if nav.empty:
        return DriftCheck(False, None, None, [], "the stack priced no NAV over the window")
    last = nav.index[-1].date()
    if last < PINNED_WINDOW[1] - timedelta(days=_PINNED_WINDOW_GRACE_DAYS):
        return DriftCheck(
            False,
            None,
            None,
            [],
            f"priced data stops at {last}, short of the pinned window's {PINNED_WINDOW[1]}",
        )
    metrics = await stack_metrics(db, run, until=PINNED_WINDOW[1])
    return DriftCheck(True, metrics.cagr, metrics.max_drawdown, drift_violations(metrics))


# How far short of the pinned window's end the priced NAV may stop and still be
# measured: the window ends on a fixed calendar date, and the last trading day
# at or before it can fall a few days earlier over a holiday or a weekend.
_PINNED_WINDOW_GRACE_DAYS = 7


# -- I/O driver -------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StackSeries:
    """Every series the walk reads, loaded and derived ONCE.

    Extracted from `run_market_signal` when the trend baseline arrived (2026-08-13):
    two arms that must be comparable cannot each build their own calendar, their
    own risk-free curve or their own moving averages. `replay._book_calendar`'s
    docstring already states the guarantee this preserves — "both arms share it,
    so A - B can never be an artefact of a calendar difference" — and the
    baseline exists precisely to be subtracted from the stack."""

    calendar: pd.DatetimeIndex
    rf: pd.Series
    # WHAT THE NAV IS PRICED ON. Never handed to the walk — see `decision_prices`.
    prices: dict[str, pd.Series]
    # WHAT THE WALK DECIDES ON: the same prices, one trading day back.
    #
    # THE LOOK-AHEAD THIS REMOVES (owner decision, 2026-08-13). `shadow_book_nav`
    # applies a target dated t BEFORE day t's return — `synthesize_nav`'s pinned
    # sequencing, "the portfolio enters the period already rebalanced" — while
    # the trend read used the CLOSE of day t. So the rule set weights from a
    # price it then earned, which no implementer can do: at the moment the order
    # goes in, that close does not exist yet.
    #
    # HARMLESS FOR A STATIC BOOK, which is why it survived from M4 to here: a
    # portfolio that never decides cannot exploit it. It is NOT harmless for a
    # trend rule, and not as noise either — the rule is momentum-shaped, so an
    # asset that ROSE today is likelier to sit above its average today, and the
    # rule was therefore systematically positioned for the very day it was
    # reading. A free move, in one direction, on every decision date.
    #
    # Measured over 1991-2026: 11.82% -> 11.27% CAGR, Sortino 1.311 -> 1.237,
    # max drawdown UNCHANGED at -20.61%. It scaled with turnover exactly as that
    # explanation predicts — 0.96pp for the stack (turnover 54.3), 0.41pp for
    # its frozen-book control (40.5), 0.00pp for a static book — and with
    # frequency: a daily-overlay variant measured 14.50% and was worth 8.19%
    # once lagged, i.e. it was better than monthly ONLY through the bias.
    #
    # The fix is here and not in `shadow_book_nav`: the NAV engine's convention
    # is right and is shared with every static portfolio and the M4 golden
    # numbers. What was wrong is feeding a decision that used same-day data.
    # Decide on yesterday's close, trade at today's open, earn today.
    #
    # THE TWO FRED SIGNALS ARE NOT LAGGED, deliberately: ADR-003 already defines
    # a MarketData `ts` as the date the value became KNOWABLE (ALFRED
    # first-release), so lagging them here would apply a second lag on top of
    # the data model's. Measured separately, doing so would cost a further
    # ~0.4pp/yr — recorded so the question can be re-opened as a vintage
    # question, which is what it is, rather than rediscovered as a new one.
    decision_prices: dict[str, pd.Series]
    spread_raw: pd.Series
    slope_raw: pd.Series
    spread: pd.Series
    slope: pd.Series
    spread_median: pd.Series
    slope_median: pd.Series
    spread_speed: pd.Series
    moving_averages: dict[int, dict[str, pd.Series]]
    # The signal series that are ABSENT, empty when both are present. Carried
    # rather than raised on, because only one of the two arms depends on them —
    # see the note at the assignment.
    missing_signals: list[str] = dataclasses.field(default_factory=list)


async def load_series(db: InvestmentDB) -> StackSeries:
    """Read and derive everything both arms decide on.

    Raises on a missing SLEEVE — no arm can price a book whose constituents have
    no series, and holding one flat at 0% is the bug that once crippled this
    stack. A missing SIGNAL series is recorded instead: it disables the stack
    (`run_market_signal` refuses) and is irrelevant to the control arm."""
    inputs = await replay.load_inputs(db)
    calendar = replay._book_calendar(inputs)
    rf = await ratios.load_rf_daily(db)

    prices = {t: await ratios.load_price(db, t) for t in LOADABLE_TICKERS}
    prices = {t: p for t, p in prices.items() if not p.empty}
    missing = set(STACK_TICKERS) - set(prices)
    if missing:
        # The exact failure the correction note warns about — refuse to run a
        # stack silently missing a sleeve rather than hold it flat at 0%.
        raise ValueError(f"market-signal stack missing price series for {sorted(missing)}")

    # Keep the RAW series alongside the calendar-aligned one: the ffill that
    # carries a stale print forward is right for the decision (it is what was
    # knowable) but destroys the publication date, and ADR-003 vintage discipline
    # is only auditable if the live path can say WHEN each input became knowable.
    spread_raw = await ratios.load_price(db, CREDIT_SPREAD)
    slope_raw = await ratios.load_price(db, YIELD_SLOPE)
    # RECORDED HERE, REFUSED IN `run_market_signal` — and the split is the point.
    #
    # An absent signal series reindexes to all-NaN, `classify_regime` reads that
    # as warm-up, and the stack silently holds `credit-spread-wide` — the
    # 90%-equity book — on no signal at all. It is not a warm-up: the decision
    # would be uninformed rather than early, and nothing downstream could tell
    # the two apart (`knowable_at` is None in both cases). So the stack must
    # refuse, and it does, one function down.
    #
    # But the CONTROL ARM reads no signal at all, and a shared loader that
    # raised here would have handed it a prerequisite it does not have — the
    # arm would have been unbuildable on a price-only database, which is exactly
    # the fixture the seed's incremental contract has to survive. The refusal
    # therefore belongs to the consumer that actually depends on the series, not
    # to the loader that merely fetches it.
    #
    # Only ABSENT is caught at all, and deliberately: a series that STOPPED
    # updating ffills and still decides, which `alerts.signal_freshness_alert`
    # reports rather than blocks. An alert and never a block is the owner's
    # recorded decision (docs/MILESTONES.md, second coherence pass 2026-08-02) —
    # ADR-003 says a stale print IS what was knowable, and ADR-009 scopes the
    # live path to telling rather than refusing.
    missing_signals = [
        t for t, s in ((CREDIT_SPREAD, spread_raw), (YIELD_SLOPE, slope_raw)) if s.empty
    ]
    spread = spread_raw.reindex(calendar).ffill()
    slope = slope_raw.reindex(calendar).ffill()
    spread_median = spread.rolling(MEDIAN_WINDOW_DAYS, min_periods=MEDIAN_MIN_DAYS).median()
    slope_median = slope.rolling(MEDIAN_WINDOW_DAYS, min_periods=MEDIAN_MIN_DAYS).median()
    # Both DECISION-TIME views, shifted together so a sleeve's price and its own
    # line are always read as of the same close (see `decision_prices`). Shifting
    # only one of the two would compare today's price to yesterday's average,
    # which is a different rule and a worse one.
    #
    # SHIFTED ON EACH SERIES' OWN INDEX, and the average is still computed the
    # way it always was — the lag is the ONLY change. Reindexing to the shared
    # calendar first and averaging on that instead moved the result a further
    # 0.03pp, which is a second change wearing the first one's clothes: an
    # instrument's previous close is its own previous close, whether or not the
    # defender's NAV index happens to have that day.
    decision_prices = {t: p.shift(1) for t, p in prices.items()}
    # ONE FRAME PER `MA_WINDOWS` LINE since 2026-08-14 — the overlay votes across
    # them, so they are built together and shifted together.
    moving_averages = {
        window: {
            ticker: prices[ticker].rolling(window, min_periods=window).mean().shift(1)
            for ticker in (*TREND_SLEEVES, TREND_HAVEN)
        }
        for window in MA_WINDOWS
    }
    # Computed unconditionally and cheap: the walk ignores it while
    # SPREAD_SPEED_VETO is None, and computing it only when the knob is set
    # would make the measured variant read a series the baseline never built.
    spread_speed = compute_derivatives(spread, CREDIT_SPREAD, SPREAD_SPEED_LOOKBACK_DAYS)["speed"]
    return StackSeries(
        calendar=calendar,
        rf=rf,
        prices=prices,
        decision_prices=decision_prices,
        spread_raw=spread_raw,
        slope_raw=slope_raw,
        spread=spread,
        slope=slope,
        spread_median=spread_median,
        slope_median=slope_median,
        spread_speed=spread_speed,
        moving_averages=moving_averages,
        missing_signals=missing_signals,
    )


async def run_market_signal(
    db: InvestmentDB,
    *,
    start: date = PINNED_WINDOW[0],
    end: date = PINNED_WINDOW[1],
    cadence: str = "monthly",
    cost_bps: float = COST_BPS,
    series: StackSeries | None = None,
) -> MarketSignalRun:
    """Load the series, run the pure logic, price it on the shared NAV engine.
    Defaults reproduce ADR-007's backtest window and MONTHLY cadence.

    `series` lets a caller running BOTH arms load once and hand the same frames
    to each (`run_trend_baseline`, the attribution). Omitted, it loads its own,
    so every existing call site is unchanged."""
    s = series or await load_series(db)
    if s.missing_signals:
        # THE REFUSAL, at the only arm that depends on it (see `load_series`).
        # `seed._missing_stack_series` pre-checks the same two series to SKIP
        # rather than raise (the incremental-seed contract); the LIVE cycle has
        # no such pre-check, and this is what stops it deciding blind.
        raise ValueError(f"market-signal stack missing signal series for {s.missing_signals}")
    dates = replay.decision_dates(s.calendar, start, end, cadence)
    decisions = walk_decisions(
        dates,
        s.spread,
        s.slope,
        s.spread_median,
        s.slope_median,
        s.moving_averages,
        s.decision_prices,
        s.spread_speed,
    )
    targets = {d.date: d.target for d in decisions if d.changed}
    nav, turnover = shadow_book_nav(targets, s.prices, s.rf, cost_bps, s.calendar)
    return MarketSignalRun(
        nav=nav,
        targets=targets,
        turnover=turnover,
        decisions=decisions,
        raw_series={CREDIT_SPREAD: s.spread_raw, YIELD_SLOPE: s.slope_raw, **s.prices},
    )


def trend_baseline_targets(
    dates: Sequence[pd.Timestamp],
    moving_averages: Mapping[int, Mapping[str, pd.Series]],
    prices: Mapping[str, pd.Series],
    book: Mapping[str, float] | None = None,
) -> dict[pd.Timestamp, dict[str, float]]:
    """The CONTROL arm's change-point map: `TREND_BASELINE_BOOK` frozen, the
    same `MA_WINDOW_DAYS` overlay re-read on the same monthly clock, the same
    IEF/cash haven chain — and NO credit input of any kind.

    It calls `apply_trend_overlay` and `_trend_read`, the live functions, for the
    reason every measurement in this module does: a control reimplemented beside
    the thing it controls measures the reimplementation. The only difference from
    `walk_decisions` is what it does NOT do — no `classify_regime`, no
    `advance_hysteresis`, no stress gate. Those three ARE the signal layer, so
    their absence is the experiment.

    The stress gate is deliberately out even though it acts on sleeves rather
    than on the book: it fires on a credit condition, so it belongs to the layer
    under test, not to the overlay."""
    held = dict(book if book is not None else BOOKS[TREND_BASELINE_BOOK])
    targets: dict[pd.Timestamp, dict[str, float]] = {}
    previous: dict[str, float] | None = None
    for t in dates:
        shares = {
            ticker: _trend_read(
                _at(prices[ticker], t),
                [_at(moving_averages[w][ticker], t) for w in moving_averages],
            ).share
            for ticker in (*TREND_SLEEVES, TREND_HAVEN)
            if ticker in prices and all(ticker in frame for frame in moving_averages.values())
        }
        target = apply_trend_overlay(held, shares)
        if target != previous:
            targets[t] = target
        previous = target
    return targets


async def run_trend_baseline(
    db: InvestmentDB,
    *,
    start: date = PINNED_WINDOW[0],
    end: date = PINNED_WINDOW[1],
    cadence: str = "monthly",
    cost_bps: float = COST_BPS,
    series: StackSeries | None = None,
) -> MarketSignalRun:
    """Price the control arm — same window, same clock, same NAV engine, same
    ADR-010 cost rate as `run_market_signal`, so the difference between them is
    the signal layer and nothing else.

    Returns a `MarketSignalRun` with `decisions` EMPTY, and that is not a gap to
    be filled later: a `Decision` records what the signal decided, and this arm
    has no signal. Its whole journal is `targets` — the dates the overlay moved
    money — which is exactly what `shadow_book_nav` priced and what
    `persist_trend_baseline_nav` writes. `stack_metrics` reads only `nav`, so
    both arms are measured by one function, which is the comparability the
    attribution rests on."""
    s = series or await load_series(db)
    dates = replay.decision_dates(s.calendar, start, end, cadence)
    targets = trend_baseline_targets(dates, s.moving_averages, s.decision_prices)
    nav, turnover = shadow_book_nav(targets, s.prices, s.rf, cost_bps, s.calendar)
    return MarketSignalRun(nav=nav, targets=targets, turnover=turnover)


async def persist_trend_baseline_nav(
    db: InvestmentDB, run: MarketSignalRun, window: int
) -> ratios.NavBackfillResult:
    """Write the control arm's daily NAV under `TREND_BASELINE_PORTFOLIO_ID`.

    Every caveat on `persist_stack_nav` applies verbatim — a PAPER series priced
    on the decision walk, no slippage, no fills — and it must, because the two
    are compared: a control measured on friendlier assumptions than the arm it
    controls proves nothing. Same producer, same cost rate, same conventions.

    THIS IS WHAT MAKES THE COMPARISON PERMANENT. `ratios.value_portfolios`, the
    UC7 ranking and the digest all read `portfolio_nav`, so once this row exists
    the baseline arrives in the ranking beside the stack every week, through the
    same formulas, with no one having to remember to run an attribution. The
    question "does the signal still beat a frozen book" stops being a study
    someone did once in August 2026."""
    return await ratios.persist_nav(db, TREND_BASELINE_PORTFOLIO_ID, run.nav.dropna(), window)


async def persist_stack_nav(
    db: InvestmentDB, run: MarketSignalRun, window: int
) -> ratios.NavBackfillResult:
    """Write the stack's daily NAV to `portfolio_nav` under STACK_PORTFOLIO_ID.

    A PAPER SERIES, and every reader of it must know so. `shadow_book_nav`
    prices the DECISION WALK: it assumes each monthly target was executed at the
    close of its anchor date, with no slippage, no partial fill and no delay
    between the digest landing and the owner placing the order. V1 executes
    nothing (ADR-006), so no realized series exists to compare it to; this is
    what the STRATEGY would have done, and it is legitimate to rank it against
    portfolios measured the same way — but it is not a statement about the
    owner's account, and the digest and the drawdown alert both say so. Closing
    that gap is forward paper-mode (docs/V1_STRATEGY.md Step 6). One consequence
    worth naming: the walk includes the current month's target even if
    `market_signal_gates` then refused it, so a blocked decision leaves the NAV
    a month ahead of the position. Reachable only through a code or config
    change (ADR-009), which is a bug being surfaced, not a market event.

    The series is `run.nav` — the one `shadow_book_nav` already produced, which
    follows the book through every switch AND every overlay redirect. Persisting
    it is what makes the stack measurable at all: `ratios.value_portfolios`, the
    UC7 ranking and the digest all read `portfolio_nav`, so once this row exists
    the stack's 36M rolling drawdown, Sortino and Calmar arrive through exactly
    the same formulas as every portfolio it is compared against.

    Rebuilt in full on each run rather than appended: the series is DERIVED from
    market data and the pure walk, so recomputing is both cheap and the only way
    a late-arriving price vintage can correct history it should have been in
    (`append_ts_batch` is INSERT OR REPLACE, so this is idempotent).

    The Portfolio vertex's `allocation` is deliberately NOT touched here. That
    column records what the stack HOLDS, which changes only when a decision is
    committed — so it is written inside `writeback.dispose_market_signal`'s
    transaction, after its EventLog append (CLAUDE.md "EventLog"). Writing it
    here would move held state on a week that decided nothing."""
    return await ratios.persist_nav(db, STACK_PORTFOLIO_ID, run.nav.dropna(), window)


async def stack_metrics(
    db: InvestmentDB, run: MarketSignalRun, *, until: date | None = None
) -> NavMetrics:
    """Daily NAV metrics of the run (CAGR, Sortino, max drawdown) — the numbers
    the DoV checks against the pinned pair (see the module's ANTI-DRIFT note).
    Earlier figures are history, and naming a superseded target here is how a
    drift check comes to certify the wrong number.

    `until` BOUNDS THE MEASUREMENT, and its absence was a methodology error
    found on 2026-08-11. `run_market_signal(start=, end=)` bounds the DECISION
    dates and not the pricing: a run asked for 1991-2008 takes its 207 decisions
    and then holds the last book FROZEN to the end of the calendar, so its NAV
    covers 8674 days exactly like the full run. Every "first half" verdict
    measured that day was therefore measuring "trade through 2008, then sit
    still for eighteen years" — including the out-of-sample checks that
    justified switching two knobs into the live rule.

    The second-half verdicts were never affected (a walk starting in 2009 prices
    from 2009), nor were the full-sample ones."""
    rf = await ratios.load_rf_daily(db)
    nav = run.nav.dropna()
    if until is not None:
        nav = nav.loc[: pd.Timestamp(until)]
    return nav_metrics(nav, rf)
