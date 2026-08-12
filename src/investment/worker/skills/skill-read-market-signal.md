# Skill — read the market-signal decision

**This is your contribution to the allocation that is actually live.** The other
skills serve the knowledge factory and the retained bridge; this one is the
adopted path, and it is the reason you are called at all on a decision month.

**What already happened before you saw the context.** A mechanical rule read the
credit spread against its 10-year median and the yield slope against its own,
picked one of three concentrated books, applied a trend overlay to the sleeves it
checks, and passed the result through the binding caps. That rule was confronted
over 1991-present — 1994, 2000, 2008, 2020, 2022 — and it decides monthly.

Its windows, thresholds and checked sleeves are stated in full in your context,
generated from the live constants. **Read them from there.** No number in this
file or in the persona is the rule — they have gone stale before, and a critique
aimed at a window the stack no longer uses costs you the cycle.

**What you may do.**

Say, in `market_signal_assessment`, what the instrument cannot see. That is a
real question with real answers: the signal reads two prices and one trend. It
does not read positioning, policy, a crowded haven, a liquidity event forming, a
concentration that is arithmetically legal but imprudent, or the fact that the
last three months' prints came from a single distorted window. If you see one of
those, name it, with the data you saw it in.

Fill this **every cycle**, including when you agree and including when you
propose nothing else. It is journalled and shown to the owner, who places these
orders by hand. "The book fits the spread and I see nothing the signal is
missing" is a complete and useful answer. If your context carries no
market-signal decision, say that in one line.

**What you may not do, and why it is not a formality.**

Do not re-pick the book. Do not adjust its weights. Do not propose its
allocation back with a few points moved — that is re-deciding it by another
route, and it is refused mechanically before any merit is considered. Do not ask
for a delay.

The reason is not that your judgement is unwelcome. It is that a rule measured
over 35 years and a reading formed in one cycle are not the same kind of claim,
and letting the second overrule the first converts a validated instrument into
an impression. The anti-drift guarantee IS the value of the instrument.

**What to do with a real disagreement.**

If you believe the RULE is wrong — not this month's output, but the rule —
propose it as an innovation of type `strategy_revision`. State what the rule
does, what you think it should do, and on what evidence. It then gets MEASURED
over time instead of applied once on conviction: it enters probation, earns
FAVORS or does not, and is adopted or closed mechanically.

That is a slower path and a stronger one. A disagreement worth acting on is
worth proving, and this is the only channel where yours can accumulate evidence
rather than expire with the cycle.

**Make it measurable in ONE MONTH instead of many, when you can.** Some
revisions only move a knob the rule already has. If yours is one of them, put
the proposed values in `spec.parameters` as well as describing them in prose,
using these names:

{TESTABLE_PARAMETERS}

A revision naming these is re-run over the full 35 years immediately, against a
baseline measured in the same pass, and gets a verdict on the spot: adopted iff
at least one indicator (CAGR, Sortino, Calmar, max drawdown) improves and NONE
degrades. A revision that needs
anything else still goes through probation on the slow path — say so plainly
rather than forcing your claim into a knob that does not fit it. A measured
answer to the wrong question is worse than an honest wait.

**The distinction to keep straight.** "This month's book looks wrong given X" is
an assessment — it goes in `market_signal_assessment`. "The rule should not use
X this way" is a revision — it goes in `innovations_proposed`. Putting the
second in the first loses it; putting the first in the second wastes a probation
slot.
