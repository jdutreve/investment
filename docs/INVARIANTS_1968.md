# The invariants measured from 1968: does the history before 1991 confirm them?

Owner, 2026-11-15. One invariant is integrated out of 273 measured. The best
candidates confirm at 0.62 to 0.67 on 20 to 45 moments. At that size the
integration bar asks 0.72 to 0.85. The verdict is not too strict: the
evidence is too short. Every confrontation is a 12-week window and the history
starts in 1991. A claim right 65% of the time needs about 80 to 100 moments to
be told from chance. Most conditions hold only part of the time.

The lever: measure the invariants over 1968-2026 instead of 1991-2026. That
adds the 1970s inflation, negative real rates and the Volcker years, and it
is a test out of sample. No figure of the period 1968-1991 has been looked at
for any invariant.

The protocol below was written and committed BEFORE the run, which happens
once. Its result is an entry in `mechanical/research_findings.FINDINGS`.
Research only: everything runs on a COPY of the live database, and
production is not touched. Adopting the longer history in production would
be a separate proposal to the owner.

## Protocol (committed 2026-11-15, before the run)

**The machinery is production's own.** The seed's steps run on a copy of the
live database, with `market_backfill_years = 58` instead of 35:

- the market data backfill;
- the regimes;
- the portfolio NAVs;
- the benchmark valuations;
- the backtests and FAVORS;
- the birth maturation.

The corpus, curation and snapshot steps are skipped. Every invariant is
re-measured by `mature_seed_invariants`, because every input it reads has
changed.

**Data before 1991, ADR-003 kept:**

- **FRED and ALFRED series** go back on their own, with their real
  publication dates. INDPRO and UNRATE have first-release vintages from 1965,
  CPIAUCSL from 1972. Under the seed's own rule (`parse_alfred_first_release`),
  a month is recorded only from its first vintage onward.
- **Series with no publication history** (GDP before its vintages begin in
  1991-12, so DEBT_TO_GDP) do not go back. Nothing revised is used without
  its vintages.
- **The four ETF proxies get a synthetic pre-history,** chained on the proxy's
  first day. Nothing is fitted:
  - VFINX (SPY, VTI) before 1980-01: the S&P 500 daily close plus Shiller's
    monthly dividend spread over the month's trading days. Correlation with
    VFINX over 1980-2023: 0.993 monthly.
  - VUSTX (TLT) before 1986-05: a 20-year par Treasury priced exactly every
    day from DGS20 (coupon y(t-1), sold at y(t) one day shorter). Correlation
    with VUSTX/TLT: 0.983 monthly.
  - VFITX (IEF) before 1986: the same construction at 8.5 years, the middle
    of IEF's 7-10, from DGS10.
  - VFISX (SHY) before 1991: the same construction at 2 years from DGS2,
    which starts in 1976-06. No pre-history before that.
- **BAA10Y** before its daily series (1986): the monthly Moody's Baa minus
  GS10, dated the first day of the next month, as `docs/research/pre1993` did.
- **Absent before 1991,** so their classes and signals start later as today:
  international and emerging equities, TIPS, commodities (the
  "gold-commodities" class is gold alone before 1991), VIX, global and net
  liquidity, DFII5, the metal ratios, delinquencies, oil before 1986.

**The candidates (fixed now).** The integrated claim and the nine best
candidates by rate minus null, with 20 or more moments, as of 2026-11-15:

| id | record | null | condition → handle |
|---|---|---|---|
| 01M326ZB100SYNVEKGD1433EWT | 46/69 (integrated) | 0.48 | irx speed < 0 & growth speed > 0 → equities |
| 01KY2N410K3N39TB6T6C1721MW | 18/27 | 0.50 | inflation > 0 & irx ≤ 2 → inflation-protected |
| 01M57TH7559BKNZWSAF54HDP3M | 29/44 | 0.50 | debt_to_gdp > 250 → bonds |
| 01M3275YRF15WP4BNKJYBWJ7GF | 15/23 | 0.50 | gold_10y_dev > 0.5 → bonds |
| inv-low-real-yields-favor-gold | 54/83 | 0.51 | real_yield < 2.5 → GLD |
| 01M324DYM7BVVGGBV7THEZNR79 | 13/20 | 0.51 | real_yield > 3 → bonds |
| inv-01M6X4HMTNFF00FRB1FJ6NMNR1 | 25/39 | 0.50 | equity_trend speed < -5 & credit_spread speed > … → bonds |
| 01M57T843TMWGJ5VWN780XYVME | 20/32 | 0.50 | credit_growth > 8 → bonds |
| 01M325DETPS3AMVQMD9WGV75EP | 14/20 | 0.58 | equity_trend < 0 → strategy:momentum-macro |
| inv-gold-ratio-trend-tilt | 28/45 | 0.51 | gold_10y_dev > 0 & speed > 0 → gold-commodities |

A candidate enters the test if it gets **10 or more decided moments before
1991-11-01**. Which ones do is decided by the data, not by their results.
TIPS start in 2003, DEBT_TO_GDP has no vintages before 1991, and a
strategy's NAV needs its sleeves, so 01KY2N…, 01M57TH… and 01M325D… are
expected to drop out.

**Q1 — the primary test.** The candidates' record before 1991, pooled: is
their confirmation rate above their pooled null (each candidate's null as
measured on the copy, weighted by its moments)? One-sided binomial at 5%.
- Above, with p < 0.05: **CONFIRMED out of sample.**
- Otherwise: **NOT CONFIRMED.**

Each candidate's own pre-1991 record and p-value is reported beside the
pooled test.

**Q2 — does a record persist across eras?** Take every invariant with 10 or
more decided moments both before and after 1991-11-01. Compute the Spearman
correlation between rate minus null before and rate minus null after.
- ρ > 0 with one-sided p < 0.05: a good record in one era tends to be good
  in the other. **PERSISTS.**
- Otherwise: **DOES NOT PERSIST.**

**Q3 — descriptive.** The verdicts over 1968-2026 against production's:
integrated, rejected and proposed counts, and which invariants change status.

**Guards — the run stops if:**
- a synthetic pre-history's monthly correlation with its proxy over their
  overlap is below 0.95;
- the integrated claim's record restricted to moments from 1991-11-01 on, on
  the copy, differs from production's 46/69 by more than 3 moments. The
  1991+ record must reproduce, or the copy measures something else.
