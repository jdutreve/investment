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
