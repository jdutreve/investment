# The dollar and the Fed as indicators, measured

Owner, 2026-11-13. A newsletter's macro panel (a crypto bulletin's "liquidity"
and "markets" lines) was read against what the Worker's tape already held.
Three things were missing: a dollar index, the policy path the market prices,
and the Fed's "net liquidity" — its balance sheet less the Treasury's account
and the reverse repo. The owner: "yes, add them and run the study". They join
the tape as `DX-Y.NYB`, `fed_path_2y` (DGS2 − DFF) and `net_liquidity`
(db/seed_data.py). This document asks whether their customary readings hold on
the history this system has.

The protocol below was written and committed BEFORE any figure was computed,
and the study runs once. Its result is an entry in
`mechanical/research_findings.FINDINGS`, so the Worker reads it.

## Protocol (committed 2026-11-13, before the run)

**Data.** The live database, read-only, as of 2026-11-13, for SPY, IEF, GLD and
DJP (total-return, spliced back with their HISTORY_PROXIES) and ^IRX. The new
series are not in the database yet: they are fetched on 2026-11-13 by the
agent's own fetcher (`market/fetcher.py`) — DX-Y.NYB from Yahoo; DGS2, DFF,
WALCL, WTREGEN and RRPONTSYD from FRED — and each is dated at its knowable date
with the lag its registry row carries (DX-Y.NYB 0, DGS2 5, DFF 5, WALCL 5,
WTREGEN 5, RRPONTSYD 2 days). `fed_path_2y` and `net_liquidity` are built by the
agent's own functions (`backtests.fed_path`, `backtests.net_liquidity`), so the
study measures what the tape prints. The calendar is SPY's trading days; every
series is forward-filled onto it from its own knowable dates (ADR-003), never
interpolated.

**Signals**, the state on day t:

| id | ON when |
|---|---|
| DOLLAR_UP | DX-Y.NYB(t) > its mean over its last 200 rows |
| HIKES_PRICED | fed_path_2y(t) > 0 |
| NETLIQ_UP | net_liquidity(t) > net_liquidity as known at t − 91 days |

The thresholds are the customary readings, fixed here: the 200-session trend
the metals study used, zero for the policy path (a term premium sits in it, so
zero is the custom, not a measured neutral), a quarter's change for liquidity.

**Windows.** H1–H6 from 1992-01-02, the first trading day of the first full year
in which every series they read exists (IEF's history starts 1991-10-29), to the
last date with a complete 12-week outcome. H7–H8 from the first date NETLIQ_UP
can be read — WALCL's and WTREGEN's first knowable rows (2002-12) plus 91 days —
to the same end. Each window is cut in two halves at its middle date.

**Outcome.** Twelve weeks, the project's invariant horizon
(`proposal_outcome_weeks`): entry at the close of the trading day AFTER t (the
signal is read on t's close, so it cannot also trade on it), exit at the first
close at least 84 calendar days later. Returns are total returns; SPY − IEF is
the difference of the two; the DFF change is DFF as known at the exit minus DFF
as known at the entry, in points.

**Sampling.** Non-overlapping: one observation every 12 weeks from the window's
start. Robustness: the same grid started 3, 6 and 9 weeks later.

**Hypotheses** — the customary readings, each with its claimed direction:

| # | signal | outcome (12 weeks) | claim |
|---|---|---|---|
| H1 | DOLLAR_UP | GLD return | lower when ON (a strong dollar weighs on gold) |
| H2 | DOLLAR_UP | DJP return | lower when ON (and on commodities) |
| H3 | DOLLAR_UP | SPY return − IEF return | lower when ON (it tightens conditions) |
| H4 | HIKES_PRICED | DFF change, points | higher when ON (the market foretells the Fed) |
| H5 | HIKES_PRICED | IEF return | lower when ON |
| H6 | HIKES_PRICED | SPY return | lower when ON ("don't fight the Fed") |
| H7 | NETLIQ_UP | SPY return | higher when ON |
| H8 | NETLIQ_UP | SPY return − IEF return | higher when ON |

**Statistic.** The difference of means, ON minus OFF, on the offset-0 grid; a
90% interval from 10,000 bootstrap resamples within each group (seed 0); the
one-sided bootstrap p in the claimed direction; the difference on each half;
the difference on each of the four offsets; the share of observations ON.

**Verdict, per hypothesis.**

- SUPPORTED iff the difference has the claimed sign, its 90% interval excludes
  zero, and the sign holds on both halves AND on all four offsets.
- CONTRADICTED iff the 90% interval lies entirely on the other side of zero.
- NOT SUPPORTED otherwise.

Eight hypotheses are tested, so one SUPPORTED by chance is to be expected about
one time in three at this bar; a one-sided p under 0.05/8 = 0.006 is reported
as the stricter, corrected reading. H4 is of a different kind from the others:
it asks whether the indicator says what it claims to say (the Fed's next move),
not whether it times an asset, and a SUPPORTED there is expected rather than
remarkable.

**The price list for a holder.** Daily, from each window's start to the last
SPY close: hold the first asset on day t+1 when the signal was in its
favourable state at t's close, else the second; 23 bp per change of position
(ADR-010); a position is held over (close t+1, close t+2] on the signal read at
close t.

- GLD while DOLLAR_UP is OFF, else cash at ^IRX — against GLD held.
- SPY while HIKES_PRICED is OFF, else IEF — against SPY held and IEF held.
- SPY while NETLIQ_UP is ON, else IEF — against SPY held and IEF held.

CAGR, annualised volatility, Sortino (daily, against ^IRX, annualised), maximum
drawdown, number of changes. No verdict rule: this is what each choice would
have cost or paid, read beside H1–H8.

## Result (run once, 2026-11-13)

No deviation from the protocol. 146 non-overlapping 12-week outcomes for H1–H6
(1992-01-02 to 2026-07-24), 99 for H7–H8 (2003-03-24 to 2026-08-06), offset 0.

| # | signal → outcome | ON − OFF [90%] | p | halves | offsets 0/3/6/9 wk | ON | verdict |
|---|---|---|---|---|---|---|---|
| H1 | DOLLAR_UP → GLD (claim lower) | +0.36% [−1.65, +2.38] | 0.61 | +1.42 / −0.91 | +0.36 +0.59 −0.41 −0.36 | 54% | not supported |
| H2 | DOLLAR_UP → DJP (claim lower) | −2.92% [−5.08, −0.76] | 0.012 | −1.92 / −4.08 | −2.92 −1.30 −0.69 −2.83 | 54% | **supported** |
| H3 | DOLLAR_UP → SPY − IEF (claim lower) | −1.68% [−3.94, +0.57] | 0.11 | −1.85 / −2.14 | −1.68 −1.16 −0.29 −1.18 | 54% | not supported |
| H4 | HIKES_PRICED → DFF change | +0.40 pt [+0.27, +0.54] | < 0.001 | +0.43 / +0.36 | +0.40 +0.45 +0.29 +0.50 | 77% | **supported** |
| H5 | HIKES_PRICED → IEF (claim lower) | −0.90% [−1.94, +0.12] | 0.07 | −0.62 / −1.09 | −0.90 −0.34 −0.24 −0.32 | 77% | not supported |
| H6 | HIKES_PRICED → SPY (claim lower) | +0.70% [−1.39, +2.91] | 0.71 | +0.45 / +0.75 | +0.70 −0.03 −1.10 −0.81 | 77% | not supported |
| H7 | NETLIQ_UP → SPY | −0.56% [−2.91, +1.70] | 0.64 | −2.36 / +1.34 | −0.56 +0.84 −0.84 −0.11 | 59% | not supported |
| H8 | NETLIQ_UP → SPY − IEF | −2.27% [−4.92, +0.30] | 0.93 | −4.40 / +0.26 | −2.27 +0.95 −1.04 −0.04 | 59% | not supported |

**The price list**, daily to 2026-11-06, 23 bp a change:

| from | rule | CAGR | vol | Sortino | max drawdown | changes |
|---|---|---|---|---|---|---|
| 1992-01-02 | GLD held | +7.02% | 16.6% | 0.49 | −45.6% | 0 |
| 1992-01-02 | GLD unless DOLLAR_UP, else cash | +3.38% | 11.5% | 0.18 | −37.9% | 303 |
| 1992-01-02 | SPY held | +10.56% | 18.3% | 0.72 | −55.2% | 0 |
| 1992-01-02 | IEF held | +4.57% | 6.4% | 0.49 | −23.9% | 0 |
| 1992-01-02 | SPY unless HIKES_PRICED, else IEF | +3.80% | 11.1% | 0.23 | −42.4% | 152 |
| 2003-03-24 | SPY held | +11.47% | 18.5% | 0.83 | −55.2% | 0 |
| 2003-03-24 | IEF held | +3.27% | 6.8% | 0.37 | −23.9% | 0 |
| 2003-03-24 | SPY while NETLIQ_UP, else IEF | +6.69% | 15.9% | 0.53 | −49.4% | 222 |

**Today** (as knowable on 2026-11-13): DX-Y.NYB 103.4 against a 200-session
mean of 99.85 — DOLLAR_UP is ON; `fed_path_2y` +0.85 — hikes priced;
`net_liquidity` 5,844 bn against 5,796 bn 91 days earlier — NETLIQ_UP is ON.

**What it says.**

- **The policy path says what it claims to say, and nothing more.** When the
  2-year yield sits above the fed funds rate, the rate rose 0.40 point more
  over the next twelve weeks than when it sat below — on both halves, every
  offset, far past the corrected bar. That is the expectations at work, as
  expected. It does not carry over to assets: equities did no worse when hikes
  were priced, and bonds did only slightly worse (−0.90%, p = 0.07). Holding SPY
  only while no hike was priced earned 3.80% a year against 10.56% held — below
  IEF held. Its ON share (77%) says the zero line is not neutral: a term premium
  keeps the 2-year above the policy rate most of the time.
- **A rising dollar has weighed on commodities, not on gold.** DJP did 2.92%
  worse over twelve weeks with the dollar above its 200-session average, on both
  halves and all offsets — the one asset reading that clears the protocol's bar,
  though not the corrected one (p = 0.012 against 0.006). Gold did not care
  (+0.36%, halves of opposite signs): the customary "strong dollar, weak gold"
  is not in this history at a 12-week horizon. Timing gold on the dollar halved
  its return (3.38% against 7.02%). Equities against bonds leaned the claimed
  way (−1.68%) without clearing the bar. The stack holds no commodity sleeve,
  so the supported reading is about the tape, not about a book.
- **Net liquidity, measured, times nothing.** Rising over a quarter, it was
  followed by slightly WORSE equities (−0.56%) and worse equities against bonds
  (−2.27%), the halves disagreeing in sign. Its popular reputation rests on
  2020–2024; over 2003–2026 it does not time equities, and following it cost
  almost five points a year against SPY held.
- **Consequence for the digest and the Worker:** the three series stay on the
  tape as descriptions of the present. `fed_path_2y` may be read as the market's
  forecast of the Fed's next moves, which it has been; none of the three may be
  read as a timing signal for an asset the stack holds
  (`research_findings.FINDINGS`, 2026-11-13).
