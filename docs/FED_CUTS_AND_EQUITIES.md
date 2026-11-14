# The Fed's cuts or the bill market's: which one the integrated claim reads

Owner, 2026-11-14. The corpus's only integrated invariant (Dalio,
`01M326ZB100SYNVEKGD1433EWT`) was titled "when the central bank cuts short
rates while the economy is already accelerating, equities rally". Its
condition reads `irx.speed < 0 AND growth.speed > 0`. That is the 3-month
Treasury bill yield lower than 30 days earlier, by any amount, while the
growth composite rose over its last month. It is the bill market, which
anticipates the Fed and also moves on flights to safety, debt-ceiling
episodes and noise. It is not the Fed's decision. Its record is 46/69 against
a null of 0.48.

The registry had no policy-rate signal. On the owner's request it now has
`fed_funds`, aliased to `fed_funds_policy`: the median of the last 20 daily
DFF prints. The effective rate alone spikes at month and quarter ends, and
before 2008 it strayed from the target for days. Read raw, a 30-day fall of
0.10 point fired on 89 days of 1997, a year without a cut. The median and
the threshold below were chosen on that series alone, against the FOMC's
known calendar, before any outcome was read: with 20 prints a 30-day fall of
more than 0.20 point fires on 0 to 4 days a year without a cut (1993, 1994,
1997, 1999, 2000, 2004–2006), and through every cutting cycle.

The question: is it the decision or the anticipation that the claim's record
rests on?

The protocol below was written and committed BEFORE any outcome was computed,
and the study runs once. Its result is an entry in
`mechanical/research_findings.FINDINGS`. Research only: one read-only SQLite
transaction; no invariant is created by the run.

## Protocol (committed 2026-11-14, before the run)

**Data and machinery.** The live database, read-only, after the derived
series `fed_funds_policy` has been materialised by the agent's own function
(`backtests._materialize_fed_signals`). Moments, outcomes, margin, null and
verdict are computed by the October bench's code, which is tested equal to
production's, exactly as the engine would measure each condition as an
invariant. The guard of `docs/EPISODE_COUNTING.md` applies: at shift zero the
bench must reproduce the integrated claim's stored record (46/69), or the run
stops.

**The effect**, the same for every hypothesis: the integrated claim's own,
`asset-class:equities | return | cross_class | outperform`. Equities beat the
median of the other asset classes over the next 12 weeks, judged against the
measured null with the production margin.

**Hypotheses**, each a condition measured as the engine would:

| # | condition | reads |
|---|---|---|
| H0 | `irx.speed < 0` AND `growth.speed > 0` | the integrated claim, re-read (the guard) |
| H1 | `fed_funds.speed < -0.20` AND `growth.speed > 0` | the Fed cut within the month, growth rising |
| H2 | `irx.speed < 0` AND `growth.speed > 0` AND `fed_funds.speed >= -0.20` | the bill market falling, growth rising, no cut |
| H3 | `fed_funds.speed < -0.20` | the Fed cut within the month, whatever growth does |

**Reported per hypothesis.** Decided moments, confirmations, the rate, the
null, the number of episodes holding a decided moment, the adopted rule's
status (`integrated`, `proposed`, `rejected`), and the share of 400
run-permutation placebo lives (seed 0) in which the condition is integrated at
least once. The October and November placebos are the yardstick for "could
chance do this".

**Reading, fixed here.** A hypothesis CARRIES the effect if its rate is at
least θ = 0.60 over at least 20 decided moments.

- **The decision carries it** if H1 carries the effect and H2 does not.
- **The anticipation carries it** if H2 carries the effect and H1 does not
  (or H1 has fewer than 20 decided moments).
- **Both** if both carry it; **neither shown** if neither does.

H3 tells whether growth matters to the cut reading.

**Consequence, fixed here.** The integrated claim's title is corrected to say
what it measures whatever the result. If the decision carries the effect, the
H1 condition is proposed to the corpus through the normal path (the Worker's
innovation, measured by the engine), not inserted by this study.

## Known limitations

- Cutting cycles are few: about ten since 1991, so H1 and H3 rest on few
  episodes, and the 20-moment floor may leave H1 unread.
- Cuts cluster in recessions, when growth is falling: H1's two conditions
  rarely hold together. That is the claim as Dalio stated it, not a defect of
  the test.
- A cut is read up to about a month late (the median's half-width plus DFF's
  5-day lag).
