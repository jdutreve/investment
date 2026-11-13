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
