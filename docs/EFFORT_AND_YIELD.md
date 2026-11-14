# Where the effort goes and what it has returned

Owner request, 2026-11-14: "the effort is badly spread against the gain —
deal with it". An account of what each part of the system has produced,
counted on the live database and the repository that day, and the decisions
it suggests. Nothing in production is changed by this document.

## What each part has produced

**The mechanical rule** (credit spread and slope pick a book, a trend overlay
on top; `market_signal.py`, about 2,400 lines). It is the whole of the
measured result: 10.2% a year since 1993 for a worst drawdown of −15%, against
10.7% and −55% for SPY held. With one unit invested a month for 20 years it
ends with a median wealth of 3.97 per unit contributed, against 3.13 for SPY
held and 3.75 without the overlay
(`docs/research/2026-10-11-accumulation-horizon`).

**The research on the rule** (28 entries in `research_findings.FINDINGS`,
each with a protocol written before its figures). Fourteen ask whether a
variant of the rule would do better; none was adopted on its return. The one
change they led to, the bond book dropping its small-cap sleeve, was a
simplification decided on a measured tie. The rule has resisted every repair proposed, which is worth knowing,
and means the next repair is unlikely to pay.

**The indicators on the tape** (ten entries since 2026-11-08: metals,
copper/gold, the dollar, the Fed's path, net liquidity, real rates). Two
partial positives: cheap copper against gold at six months, and a strong
dollar before weaker commodities. The stack holds neither commodity. No
indicator times an asset the stack holds.

**The corpus and its engine** (9 books, 5,789 passages, 655 curator batches;
the corpus, curator, Planner, Worker, writeback and watch modules are about
9,500 lines of 31,000, and `invariants.py` 2,100 more). 1,816 claims: 1,543
reference notes that cannot be measured, 262 proposed, 10 rejected, 1
integrated. Judged as signals the corpus is at 0.517 against 0.501 for its
own conditions moved to dates where they mean nothing, and does not separate
the next quarter from an ordinary one (2026-10-10). The four books ingested
on 2026-11-14 gave 16 measurable claims, none away from the null.

**The Worker** (17 completed cycles, 21 readings). 60 innovations proposed:
36 rule revisions over 19 themes, 10 invariants, 10 process remarks, 4 data
requests. One was applied: a crude oil series. 18 forecasts made since
2026-10-25; none has come due, the first resolves on 2026-11-22 and the
thirtieth is not yet made.

## Reading

The return comes from one small mechanical rule, and four months of work
around it have confirmed it rather than improved it. The corpus and the
cognitive layer are about a third of the code and most of the running cost,
and their measured contribution to the allocation is nil so far. That was
the owner's position already (2026-10-10: the corpus is material for the
Worker's proposals, to be judged downstream), and the downstream judgement
is this: 60 proposals, one applied, forecasts not yet scored.

The commits say the same: of the 77 since 2026-10-11, about 48 have the
corpus, the Worker, the indicators or the digest as their subject.

## What it suggests, the owner's to accept or refuse

1. **No new book until the corpus has shown a downstream yield.** Each book
   costs curator calls, maintenance and attention, and nine of them have not
   moved a decision. The exception would be a book bringing a mechanism the
   registry cannot yet express, which is a signal to add, not a book to read.
2. **A date and a bar for the Worker's forecasts.** They are the one output of
   the cognitive layer that is scored against reality. Once 30 have come due
   (about the end of January 2027 at the present pace), compare their Brier
   score with the base rate the `forecast` table already stores. If they do no
   better, the Worker's reading stays as commentary and no further work goes
   into its context; if they do, that is the first measured reason to invest
   in it.
3. **No further indicator study as a timing signal** unless it brings a
   mechanism the ten did not cover. The tape describes the present; that is
   its use.
4. **The gains still on the table are outside the rule**, and none needs
   research: holding what the stack says with real money (the stack is on
   paper; the owner has been in cash and metals since 2026-10-18), earning the
   bill rate on the cash the stack holds (the NAV assumes ^IRX; a brokerage
   cash balance usually pays less), and the turnover (1.06 to 1.19 points a
   year, already in the stack's figures).

## The owner's answer (2026-11-14): none retained

"The corpus is context for the Worker according to the current context. None
retained; I am not convinced of the relevance."

The account above judges the corpus by what it was not built for. It counts
integrated claims, signal rates and allocations moved; the corpus is there so
that the Worker reads the present with what the books say about situations
like it, retrieved for the context of the day. Whether that reading is better
with the corpus than without it is not measured by any figure in this
document. The four proposals are refused and nothing changes: books keep
being ingested, the Worker's context keeps being worked on, and indicators
keep being studied when the owner asks.
