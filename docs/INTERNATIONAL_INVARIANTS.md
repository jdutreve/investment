# Measuring the invariants across countries

Owner, 2026-11-17. The feasibility test (`docs/INTERNATIONAL_FEASIBILITY.md`)
showed two things:
- four OECD countries carry enough history to measure a claim;
- the corpus's one confirmed idea holds abroad, weakly in each country and
  clearly pooled.

More independent data is the only lever left for integrating more of the
books (`docs/INVARIANTS_1968.md`). This document is the build, in phases,
with a stop before production.

## Phase 1 — research: every translatable invariant in five countries

**Translatable** means the claim names only series every country has, and a
class every country has:
- conditions on `inflation`, `irx`, `real_rate`, `real_yield` and
  `equity_trend` only;
- a `cross_class` claim on equities, bonds or cash.

That is 74 of the 273 measured invariants (2026-11-17). The others need series
the OECD does not carry per country: the growth composite, credit spreads,
liquidity, gold, the debt leg, the dollar, oil.

**Method: a country is a copy of the database.** For each of US, GB, DE, CA
and FR, a copy of the live database is built in which:
- the series that feed the translatable signals and classes are REPLACED by
  that country's series:

  | Production series | Replaced by |
  |---|---|
  | `CPIAUCSL` | CPI year-on-year |
  | `^IRX` | 3-month rate |
  | `DGS10` | long yield |
  | `SPY` | share price index |
  | `TLT` | a 10-year par bond priced from the long yield |

- every other priced or macro series is DELETED. The other classes are then
  empty, and no untranslatable invariant can borrow US data.

Then production's own `materialize_benchmark_valuation` derives the country's
`real_rate`, `real_yield_10y`, `equity_trend` and asset classes, and
`mature_seed_invariants` measures every invariant on the fixed grid. The US
is built the same way, from its OECD series, so the five countries are
measured alike.

**Dating (ADR-003).** OECD values are monthly, current vintage:
- yields, short rates and share prices are monthly averages, dated the first
  day of the next month;
- CPI is dated the 15th of the next month;
- daily rows carry the last known value forward.

CPI year-on-year is rarely revised, and yields and prices never are.
Dividends are missing from the share index. The bias is constant, and a
claim is judged against the handle's own baseline, so it largely cancels. It
is a known limit.

**The sanity check, first.** Before anything is read, an oracle is planted in
the GB copy, built as in `docs/INSTRUMENT_POSITIVE_CONTROL.md` (p = 0.70, 20
seeds, common). The engine must measure it at 0.65 or more on monthly
country data, or the run stops.

**The pooled test.** For each translatable invariant, its decided moments in
the five countries are grouped by grid date:
- each date contributes the share of confirmations among the countries where
  the condition held;
- a one-sided t-test over the dates compares that share with the invariant's
  null, its per-country nulls weighted by moments.

Clustering by date absorbs the correlation between countries on the same
date. A date is one observation, however many countries agree.

**Decision (fixed now).** Building the cross-country measurement into
production is worth it iff, controlling the false discovery rate at 10%
(Benjamini-Hochberg over the translatable invariants), **three or more**
invariants pass the pooled test. Otherwise phase 1 is the end, and its result
is a finding. Reported either way:
- each invariant's per-country and pooled record;
- the US-only record beside it, to show what the other countries add.

This protocol was written and committed BEFORE the run, which happens once.

## Result, phase 1 (2026-11-17, run once)

Scripts and raw output: `docs/research/2026-11-17-international/`
(`countries.py`, `pooled.py`, `RESULT_PHASE1.txt`).

**Sanity check: PASS.** The oracle planted at 0.70 in the GB copy reads
0.700 on monthly country data.

**DECISION: STOPS HERE.** No invariant passes a 10% false discovery rate.
The bar for the best of 71 tests is p ≤ 0.0014, and the best is 0.0063.

But the corpus is not empty. **Nine** of the 71 translatable invariants have
a pooled p below 0.05, where chance gives 3.6. The best ones say the same
thing in every country:

| Claim | Pooled | p | US / CA / DE / FR / GB |
|---|---|---|---|
| Real rate above 3% → bonds outperform | 0.641 vs 0.518 | 0.006 | 25/38, 43/65, 34/52, 25/40, 41/67 |
| Negative real short rate, moderate inflation → equities outperform | 0.611 vs 0.496 | 0.013 | 25/37, 25/43, 23/40, 26/42, 19/31 |
| Sharply rising inflation → equities underperform | 0.635 vs 0.505 | 0.016 | 13/18, 10/16, 5/8, 19/33, 19/29 |
| Equities below their 10-month average → bonds outperform | 0.626 vs 0.517 | 0.017 | 33/58, 46/69, 47/76, 49/80, 36/54 |
| High real yield → bonds outperform | 0.588 vs 0.517 | 0.033 | 30/54, 51/81, 60/100, 46/82, 41/71 |

**Reading.** Five countries do not add five times the evidence:
- the countries move together on the same dates, and a date is one
  observation;
- the 74 translatable claims are many tests at once.

What the extra countries do add is **consistency**. The bond claims —
high real rates, equities in a downtrend — hold in each country taken
separately, which a US accident would not do.

The corpus holds real, modest regularities, about 0.60 against a null near
0.51. They are visible as a set (nine where 3.6 are expected), and none is
strong enough to stand alone against 71 simultaneous tests. Building the
cross-country measurement into production would not integrate them; the
protocol's bar says so, and it stops here.
