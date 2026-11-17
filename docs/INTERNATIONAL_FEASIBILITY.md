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
