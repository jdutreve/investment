# Skill — evaluate a strategy

**Purpose.** One verdict per enabled strategy: does the new evidence confirm,
weaken or invalidate its thesis?

**Inputs.** The current regime, the strategy's FAVORS standing, its scenario
probabilities and the invariants backing it — all already in your context.

**Method.** Compare what the strategy CLAIMS (its conditions) against what the
data SHOWS. Cite the numbers you used.

**Output contract** — this is not style guidance. Your verdicts feed
confrontations: they move invariant weights, which change what later cycles are
allowed to lean on. A verdict is an act, not a comment.

- `confirms` — current-regime data consistent with the strategy's conditions
  AND supportive FAVORS. Cite the numbers.
- `invalidates` — explicit contradicting data. Cite ticker and value.
- `weakens` — directional evidence against, not conclusive.
- `neutral` — the default when evidence is thin. **Never force a verdict.**
  A neutral verdict with honest reasoning is worth more than a confident one
  the data does not carry.

`conviction_delta` ∈ [-10, +10]. Every non-neutral verdict cites at least one
concrete data point in `events[]`.

These four words are the only accepted values, and the range is enforced: an
invented verdict or an out-of-range delta fails validation and you will be
asked again.
