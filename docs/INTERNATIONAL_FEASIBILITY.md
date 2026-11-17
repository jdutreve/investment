# Can the invariants be measured across countries? A short feasibility test

Owner, 2026-11-17. On 58 years of US data, one corpus idea holds out of sample:
a high real yield is followed by bonds outperforming. Most other records
reverse from one era to the other. More INDEPENDENT data is the only lever
left. Other countries are the source the corpus itself uses (Dimson, Marsh &
Staunton, *Triumph of the Optimists*).

This test asks two things before any build:
- do FRED's OECD series carry enough of five countries to measure a claim?
- does the one confirmed idea hold there?

The protocol was written and committed BEFORE the run, which happens once.
Research only: nothing enters the database.

## Protocol (committed 2026-11-17, before the run)

**Data**, from FRED's OECD Main Economic Indicators, monthly, current vintage:
- long-term government yield (`IRLTLT01xxM156N`);
- CPI, year-on-year (`CPALTT01xxM659N`), read with a one-month publication
  lag;
- 3-month rate (`IR3TIB01xxM156N`);
- share price index (`SPASTT01xxM661N`, price only).

The countries are GB, DE, JP, CA and FR, with the US as the in-sample
reference. Japan's long yield starts in 1989 and its short rate in 2002, so
it may not qualify.

**Construction, per country:**
- moments on a fixed quarterly grid (January, April, July, October), so they
  never overlap;
- the real yield at a moment: the long yield minus the latest published
  year-on-year CPI;
- the bond return over the next three months: a 10-year par government bond
  priced exactly each month from the long yield;
- outcome A, the clean one: bond return minus cash (the 3-month rate over
  three months);
- outcome B: bond return minus the share index's price change. Dividends are
  missing, but the bias is the same with or without the condition, so it
  cancels in the comparison;
- a moment is confirmed when its outcome is above that country's own median
  over every quarter, so the unconditional rate is 0.50 by construction;
- the condition: real yield above 3%, the confirmed US claim's threshold.

**Criteria:**
1. **FEASIBLE** if at least 4 of the 5 countries have 20 or more quarters
   with the condition true.
2. **THE IDEA HOLDS INTERNATIONALLY** if, pooled over the non-US countries
   that qualify, outcome A's confirmation rate is above 0.50 with one-sided
   binomial p < 0.05.

Outcome B, each country's own record, and the US reference are reported,
not judged.

## Result (2026-11-17, run once)

Raw output: `docs/research/2026-11-17-international/RESULT.txt`.

| Country | Quarters | Real yield above 3% | A: bond beats cash | B: bond beats equities |
|---|---|---|---|---|
| GB | 1960-2025, 260 | 108 | 63/108 = 0.58 (p 0.05) | 54/108 = 0.50 |
| DE | 1960-2025, 261 | 144 | 77/144 = 0.53 (p 0.23) | 78/144 = 0.54 |
| JP | 2002-2021, 76 | 3 | too few | too few |
| CA | 1956-2025, 277 | 120 | 66/120 = 0.55 (p 0.16) | 71/120 = 0.59 (p 0.03) |
| FR | 1970-2025, 221 | 92 | 52/92 = 0.57 (p 0.13) | 46/92 = 0.50 |
| US (reference) | 1964-2025, 243 | 80 | 42/80 = 0.53 | 44/80 = 0.55 |

1. **FEASIBLE: yes.** Four of the five countries have 92 to 144 quarters with
   the condition true. Japan has too short a history of yields.
2. **THE IDEA HOLDS INTERNATIONALLY.** Pooled over GB, DE, CA and FR, outcome
   A is 258/464 = 0.556, one-sided p = 0.009. Outcome B, reported only, is
   249/464 = 0.537, p = 0.06.

## Reading

The idea goes the same way in all four countries. It is weak in each
(0.53 to 0.58), and clear only together: 464 independent quarters say what
no single country can. That is the case for measuring across countries.

The size, not the direction, differs from the US engine's 0.84-0.87. The
two are different questions:
- the engine compares bonds with the median of all the other classes,
  gold included, and judges against the handle's own baseline with a
  neutral margin;
- this test compares bonds with cash only, or with equities only.

On this simpler construction, the US itself reads 0.53. The 0.84 measures
something narrower, and its size should not be taken as the size of the
effect abroad.

The data allows the build for yields, inflation, short rates and equity
prices, from about 1960, in four of five countries. It does not allow gold,
credit spreads or the US-only series. Equity dividends are missing and
would have to be estimated.
