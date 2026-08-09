# Skill — the retained bridge

> **NONE OF THIS IS THE LIVE ALLOCATION.** The Dalio 4-quadrant ranking, the
> defender/challenger comparison and the scenario-driven blend were
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

## You do not reallocate it

ADR-012: allocation is mechanical everywhere, including here. The bridge's
defender is rebalanced by the 0.4 x scenario + 0.6 x structural blend applied in
code, with no cognitive step — which is what makes it a clean benchmark: two
mechanical policies compared, no model variance inside either.

Your job on the bridge is the paragraph above this one — explain how the
fallback is faring against its alternatives, so the owner would know whether to
trust it if the stack ever failed. If you think the BLEND ITSELF is wrong, that
is a `strategy_revision` innovation, where it gets measured over 35 years
instead of applied once.
