# Skill — the retained bridge

> **NONE OF THIS IS THE LIVE ALLOCATION.** The Dalio 4-quadrant ranking, the
> defender/challenger comparison and the scenario-driven reallocation were
> superseded by the market-signal monthly stack (see
> `skill-read-market-signal`). They are RETAINED as fallback, benchmark and
> knowledge factory — kept, measured, and not deleted until forward paper-mode
> earns the switch. Three former skills are merged here because they are one
> job: keeping the fallback honest.

## Explain the ranking — never re-rank it

The ordering was computed from pinned formulas over a 36-month rolling window.
It is not a matter of opinion. Make it legible: why the leader leads, what the
gaps mean, what would have to change for the order to change.

Flag two things explicitly, because they are separate rules and conflating them
misreads the table:

- **Calmar demotion** — `calmar_rolling < 1.0` MOVES the row to the bottom
  regardless of its Sortino.
- **Drawdown-rule exclusion** — breaching the user drawdown rule leaves the row
  exactly where it is and only bars it from the defender role and from proposal
  candidacy.

If a number looks wrong to you, say so. Do not compensate by reordering.

## Compare a challenger against the defender

Report the Sortino gap, the Calmar standing and the SHAPE of the downside — a
challenger that leads on return while drawing down harder is not leading. Name
the regime the challenger was designed for and whether it is the one we are in.

Commentary, not a recommendation to switch: no live cycle emits a switch. What
this is for is telling the owner how the fallback is faring against its
alternatives, which is what would make the fallback trustworthy if it is ever
needed.

## Propose a reallocation — the one ACTION here

> Bounded by ADR-011: this may never target a mechanically-allocated portfolio.
> Proposing the stack's allocation back with a few points moved is re-deciding
> it by another route, and is refused before any merit gate.

**When** — one of:
- the active scenario's probability shifted by more than the configured trigger,
- or allocation drift versus the blend target exceeds 5 points.

Otherwise return `null`. Returning null is a real answer; proposing every week
to look useful is how a fallback book gets churned into underperformance.

**How.**

```
proposed_allocation = current + 0.4 x scenario_delta + 0.6 x favors_delta
```

rounded to 2.5-point increments, then renormalized to sum to 100.

**Requirements.**
- Cite at least one supporting invariant, BOTH bright (`weight_effective`) AND
  active (its `condition` holds now) — that exact pair is verified
  mechanically, so a citation failing it is wasted work.
- Explain the blend in `reasoning`: what the scenario leg contributed, what the
  structural leg contributed, and why.
- Weights are percent, long-only, finite, summing to 100. A negative or
  non-finite weight fails validation and you will be asked again.
