# Skill — explain the ranking

**Purpose.** EXPLAIN the mechanical ranking. **Never re-rank it.**

The ordering was computed from pinned formulas over a 36-month rolling window
and is not a matter of opinion. Your job is to make it legible: why the leader
leads, what the gaps mean, what would have to change for the order to change.

**Flag two things explicitly**, because they are separate rules and conflating
them misreads the table:

- **Calmar demotion** — `calmar_rolling < 1.0` MOVES the row to the bottom
  regardless of its Sortino.
- **Drawdown-rule exclusion** — breaching the user drawdown rule leaves the row
  exactly where it is and only bars it from the defender role and from proposal
  candidacy.

If a number looks wrong to you, say so in your commentary. Do not compensate by
reordering.
