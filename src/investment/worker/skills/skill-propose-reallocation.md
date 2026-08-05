# Skill — propose a reallocation

> **RETAINED BRIDGE (ADR-007), BOUNDED BY ADR-011.** This adjusts the retained
> fallback book, never the adopted market-signal stack. A reallocation aimed at
> a time-varying (mechanically allocated) portfolio is refused before any merit
> gate is even considered — mechanical allocation is sovereign. Proposing the
> stack's own allocation back with a few points moved is re-deciding it by
> another route, and is refused as such.
>
> Your contribution to the ADOPTED path is your READING of it
> (`market_signal_assessment`) plus, if you think the RULE is wrong rather than
> this month's output, an innovation (`strategy_revision`) — where the claim
> gets measured over time instead of applied once on conviction.

**When to propose** — one of:
- the active scenario's probability shifted by more than the configured
  trigger, or
- allocation drift versus the blend target exceeds 5 points.

Otherwise return `null`. Returning null is a real answer; proposing every week
to look useful is how a fallback book gets churned into underperformance.

**How to build it.**

```
proposed_allocation = current + 0.4 x scenario_delta + 0.6 x favors_delta
```

rounded to 2.5-point increments, then renormalized to sum to 100.

**Requirements.**
- Cite at least one supporting invariant. It must be BOTH bright
  (`weight_effective`) AND active (its `condition` holds now) — that exact pair
  is verified mechanically, so a citation failing it is wasted work.
- Explain the blend in `reasoning`: what the scenario leg contributed, what the
  structural leg contributed, and why.
- Weights are percent, long-only, finite, summing to 100. A negative or
  non-finite weight fails validation and you will be asked again.
