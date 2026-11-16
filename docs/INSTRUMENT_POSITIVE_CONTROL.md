# Does the invariant engine find an effect that is there? A positive control

Owner, 2026-11-16. Out of 273 measured invariants, 128 sit at or below chance
and only one is integrated. The engine has been tested against NOISE: it does
not integrate claims that know nothing (`docs/INVARIANT_TASKS.md`, lot 4). It
has never been tested against a KNOWN effect.

A defect in the date alignment, the neutral margin, the median of the other
classes or the moment sampler would produce exactly what we see: every record
near 0.50. This control plants signals whose truth is known and asks whether
the engine finds them.

The protocol below was written and committed BEFORE the run, which happens
once. Its result is an entry in `mechanical/research_findings.FINDINGS`.
Everything runs on a copy of the live database; production is not touched.

## Protocol (committed 2026-11-16, before the run)

**The planted signals.** Let E(t) be the equities class's 12-week return
minus the median of the other classes' 12-week returns, over the 12 weeks
FOLLOWING the weekly date t. It is read from `benchmark_valuation`, by this
script's own code and not by the engine's.

Each planted signal is an ORACLE with a set accuracy p: it is 1 on the weeks
where E(t) is above its median with probability p, and on the other weeks with
probability 1 - p. Its condition is `level > 0.5`; its claim is "equities
outperform the other classes" (`cross_class`, `return`). Among the weeks when
the signal is on, equities beat their median excess a fraction p of the time.
The signal is dated at t: it sees the future on purpose.

**Grid:**
- p = 0.50 (no information, the negative control), 0.55, 0.60, 0.70;
- frequency: COMMON (the signal is drawn in every 12-week block) or RARE (each
  12-week block is drawn with probability 0.2, and the signal is 0 in the
  others);
- 20 random seeds per cell, so 160 planted invariants.

**The machinery is production's own.** The planted series go into
`market_data` and their aliases into the signal registry, in-process. Each
planted invariant is matured by `mature_seed_invariants` and restated by
`restate_invariant`, unchanged. The script reads back from the copy, for each
invariant: its decided record, its rate and its status.

**Criteria — the instrument PASSES only if all three hold:**

1. **C1, rate recovery.** In every cell, the mean measured rate over the 20
   seeds (confirmations over decided moments) is within ±0.05 of p. The
   tolerance allows for the engine's neutral margin and for a moment falling
   mid-week.
2. **C2, no false integration.** At p = 0.50, at most 2 of the 40 planted
   invariants (5%) are integrated.
3. **C3, power on a strong effect.** At p = 0.70 COMMON, at least 16 of 20
   (80%) are integrated.

Also reported, not judged: the integration rate in every cell, and the
number of decided moments per cell.

If C1 fails, the engine does not measure what it claims to, and every record
of the 273 invariants is suspect. If C1 passes, the records are trustworthy,
and the reason so many sit near 0.50 is the size of the effects, not the
instrument.

## Run 1 (2026-11-16): THE INSTRUMENT FAILS

Raw output: `docs/research/2026-11-16-positive-control/RESULT_RUN1.txt`.

| Planted p | Measured rate (common / rare) | Integrated |
|---|---|---|
| 0.50 | 0.483 / 0.480 | 0 of 40 |
| 0.55 | 0.498 / 0.467 | 0 of 40 |
| 0.60 | 0.514 / 0.515 | 0 of 40 |
| 0.70 | 0.545 / 0.562 | 0 of 40 |

- **C1, rate recovery:** FAIL.
- **C2, no false integration:** PASS.
- **C3, power on a strong effect:** FAIL.

The engine integrates no noise, but it flattens real effects to near chance.

**Diagnosis**, made after the run (`sampler_diagnosis.py`):
- **The outcome is right.** The engine's excess at every moment equals this
  script's own E(t) exactly (correlation 1.000).
- **The loss is the moment sampler.** It walked the active days, took one,
  skipped a horizon and took the NEXT active day. A condition that flickers is
  active again a few days after every skip, in good times as in bad. So the
  walker drew about one moment per horizon whatever the condition said, and
  measured the handle's ordinary rate. On the same oracles:

  | Planted p | Truth at the walker's moments | Truth on a fixed grid |
  |---|---|---|
  | 0.50 | 0.496 | 0.476 |
  | 0.60 | 0.542 | 0.592 |
  | 0.70 | 0.592 | 0.696 |

  The fixed grid takes one date every 12 weeks from a fixed anchor, kept only
  if the condition holds on it.

**The fix:** `invariants.sample_moments` now returns the dates of a fixed grid,
one horizon apart from 1960-01-01, on which the condition holds.
- The windows still do not overlap.
- The choice of date no longer depends on the condition.
- The sampler is part of the maturation fingerprint, so every record is
  re-swept once.
- The cost: a burst shorter than a horizon counts only if a grid date falls
  inside it.

**Every record measured before this fix is suspect**, and so are the studies
built on those records: the corpus against noise, the ideas grouped and
judged, and the history from 1968.

## Protocol, run 2 (committed 2026-11-16, before the run)

The same script, the same grid of oracles, the same seeds and the same three
criteria, on a fresh copy, with the engine as fixed above. Run once.
- If it PASSES, the instrument is sound, and re-measuring production is
  proposed to the owner.
- If it FAILS, the fix is not enough and nothing is re-measured.
