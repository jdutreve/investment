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

## Result (run 2026-11-16, once)

Scripts and raw outputs: `docs/research/2026-11-15-invariants-1968/`
(`prehistory.py`, `run.py`, `analysis.py`, `RESULT.txt`). The extended copy
itself is kept outside the repository.

**Deviations from the protocol, all known before any result:**
- **VFITX** starts in 1991-10 on Yahoo, not 1986, so IEF's synthetic part
  covers 1962-1991. Its guard passed at 0.962.
- **LBMA** answered 403 and **^BCOM** returned nothing. The seed kept the
  stored gold history (from 1968) and commodities (from 1991), as it does in
  production.
- **The regime history** was not re-detected before 1992: the step only
  detects new prints. No measurable invariant has a regime predicate, so no
  record depends on it.
- **The analysis script** failed on a reserved word (`null`) before printing
  anything. It was fixed and run once.

**Every pre-history guard passed:** VFINX 0.993, VUSTX 0.986, VFITX 0.962,
VFISX 0.969.

**THE REPRODUCTION GUARD TRIPPED.** The integrated claim's record from
1991-11-01 on reads 49/73 on the copy, against 46/69 in production: 4 moments
apart, for a tolerance of 3. Diagnosis, made after the stop:
- the two records share 67 moments, all with the same verdict;
- 2 moments are only in production (the first, 1991-12-16, and 1995-10-06);
- 6 are only on the copy: 1992-02-14 and 1992-05-08, where a history that
  starts earlier phases its 12-week moments differently, and four scattered
  ones (1994, 2003, 2013, 2024), where the null re-measured on 58 years moves
  the neutral margin.

The copy measures the same thing, but the rule written beforehand was
broken. By the protocol, **the figures below are not a verdict**. They are
reported as found, and they all point the same way.

**Q1 — the candidates before 1991: NOT CONFIRMED.** Pooled, 101/184 = 0.549
against a null of 0.507, one-sided p = 0.14. Six candidates entered the test:

| Candidate | Before 1991 | Null | p |
|---|---|---|---|
| Rates falling, growth rising → equities (the integrated one) | 18/40 = 0.45 | 0.49 | 0.74 |
| Low real yields → gold | 17/33 = 0.52 | 0.50 | 0.52 |
| Real yield above 3% → bonds | 22/32 = 0.69 | 0.55 | 0.08 |
| Equity collapse with spreads widening → bonds | 12/15 = 0.80 | 0.52 | 0.02 |
| Heavy credit growth → bonds | 14/30 = 0.47 | 0.48 | 0.64 |
| Gold/10y ratio above its trend → gold | 18/34 = 0.53 | 0.51 | 0.47 |

The other four lacked data before 1991, as expected: TIPS, debt/GDP, a
strategy NAV, and the gold/10y ratio above 0.5.

**Q2 — DOES NOT PERSIST.** Over the 137 invariants with 10 or more moments on
each side, the Spearman correlation of rate minus null before and after 1991
is -0.13 (one-sided p = 0.94). A good record after 1991 says nothing about the
record before it. Mean rate minus null: +0.013 before, +0.020 after.

**Q3 — the verdicts over 1968-2026.**
- Rejected goes from 10 to 37, proposed from 262 to 235.
- One invariant is still integrated, but not the same one. "Rates falling
  while growth rises, equities beat the rest" falls back to proposed: 18/40
  before 1991. "Flight to quality" (a fast equity collapse with spreads
  widening favours bonds) is integrated, at 38/54.

## Reading

The longer history does not lift the candidates. It sorts them, and mostly
downward: 27 more rejections, one integration exchanged for another. The
integrated claim of 1991-2026 did not hold in the 1970s and 1980s. That
matches what `docs/FED_CUTS_AND_EQUITIES.md` found: the claim reads the bill
market's anticipation, and that market behaved differently under the
inflation of the 1970s. The one claim that held in both eras is the
flight-to-quality one, which is also the most mechanical.

Above all, a record does not persist from one era to the other. That is the
answer to the owner's question of 2026-11-15. With more history, the books'
testable claims are not confirmed; they are found to depend on their era.
Production is unchanged: adopting the 1968 history would be a separate
decision, and these figures give no reason to take it for the sake of
integrations.

## Protocol, run 2 — on the fixed moment grid (committed 2026-11-16, before the run)

Run 1 was measured with the moment sampler that the positive control later
showed to be defective (`docs/INSTRUMENT_POSITIVE_CONTROL.md`). Its records,
and so its three answers, do not stand. Run 2 asks the same three questions
with the engine as fixed, on the same extended copy. The market data, regimes,
NAVs, benchmarks and backtests of run 1 are reused unchanged; only the
maturation runs again (`mature_seed_invariants`, every record re-swept). Run
once.

**The candidates (fixed now)** are production's ten best after the re-measure
of 2026-11-16, by rate minus null, with 20 or more moments:

| id | record | null | condition → handle |
|---|---|---|---|
| 01M324DMGDXT46BGC4Y6TXHFK6 | 16/21 | 0.50 | real_yield > 3 → bonds |
| 01M324DYM7BVVGGBV7THEZNR79 | 16/22 | 0.51 | real_yield > 3 → bonds |
| inv-gold-ratio-trend-tilt | 23/33 | 0.51 | gold_10y_dev > 0 & rising → gold-commodities |
| 01M327DFBV8WC7T0K3BBBD6VHZ | 18/28 | 0.48 | gold_10y_dev > 0.5 → equities |
| 01M3275YRF15WP4BNKJYBWJ7GF | 17/26 | 0.50 | gold_10y_dev > 0.5 → bonds |
| inv-low-real-yields-favor-gold | 58/88 | 0.51 | real_yield < 2.5 → GLD |
| 01M57TH7559BKNZWSAF54HDP3M | 28/44 | 0.50 | debt_to_gdp > 250 → bonds |
| inv-liquidity-easing-risk | 28/45 | 0.48 | broad money rising & accelerating → equities |
| 01KY2Q6ZP79MZ1XSRMZX96GWDT | 18/29 | 0.48 | growth rising & inflation falling → equities |
| 01M3295PB07HKMZSPN4KFZQB16 | 16/26 | 0.48 | real_rate < 0 & broad money … → equities |

**Q1, Q2 and Q3** are run 1's, unchanged: the same 10-moment entry rule, the
same pooled one-sided binomial at 5%, the same Spearman test over the
invariants with 10 or more moments on both sides, and the same verdict
comparison with production (now production after the re-measure).

**The guard, replaced.** Run 1's guard tripped because the old sampler phased
its moments from where the data began. On the fixed grid the moment dates are
the same whatever the start, so the guard asks more:
- over the ten candidates, the copy's moments from 1991-11-01 on must share
  at least 90% of their dates with production's;
- at least 90% of the shared moments must carry the same verdict.

Otherwise the run stops. The neutral margin may still move a few moments,
because the null is re-measured on 58 years.
