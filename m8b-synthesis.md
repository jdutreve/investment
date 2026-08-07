# M8b — synthesis for the STOP POINT decision

**TWO independent runs**, 2026-08-06 and 2026-08-07. Run 2 replayed the
`global-financial-crisis` and `inflation-shock` episodes from scratch with the
per-date bound raised to 1800s and completed all 21 dates; `covid-crash` was
resumed from its journal and is therefore identical in both, by construction.

Raw material: `m8b-report.txt` + `m8b-report.innovations.json` (run 2), and the
same pair under `run1-archive/`.

This document does not decide; it arranges the evidence against the milestone's
own two questions. Having two runs of the same experiment is what makes most of
it possible — it turns "does the Worker reason well?" from an impression into a
question about what REPRODUCES.

**Read this first:** the run is labelled *semi-PIT, best-case*. The market data
is bounded at t, the corpus is not — the Worker of July 2008 reasoned with
invariants born in July 2026. That inflates the behavioural channel by an amount
nobody can measure. M8b is a screen you can fail, not one you can pass.

---

## 1. The numbers, and what survived a second run

```
                       run 1                          run 2
                    A'−A  beats B  accepted     A'−A  beats B  accepted  lost
global-financial   -2.50%   YES       1        -1.43%   YES       1       0
covid-crash        -2.32%   tie       0        -2.32%   tie       0       0
inflation-shock    +0.47%   YES       4        -0.20%   YES       1       0
```

Run 2 in full: GFC A' −12.92% / A −11.49% / B −17.00%; covid A' +21.79% /
A +24.12% / B +21.79%; inflation A' −13.96% / A −13.77% / B −15.54%.

**Question 1 — does the best-case system beat All Weather at all? STABLE: yes,
2 of 3, both runs.** The covid "no" is A' equal to B on every figure — CAGR,
Sortino and drawdown — because zero reallocations were accepted. With nothing
accepted, A' *is* the hold curve by construction: a tautology, not a defeat.

**Question 2 — what does A'−A say? NOT REPRODUCIBLE, and this is the finding.**
The inflation-shock delta CHANGES SIGN between two runs of the same experiment
(+0.47% → −0.20%), and total accepted reallocations fall from 5 to 2. Same
dates, same rebuilt snapshots, same gates; only the model's non-determinism
differs.

So the NAV channel measures nothing usable here. That is not a criticism of the
Worker — it is that 2 to 5 decisions spread over three 7-month windows cannot
carry a signal. **Any reading of A'−A as evidence for or against the cognitive
layer should be discarded**, including the one this document made after run 1
("the episode where it acted most is the one where it added"). It did not
survive.

---

## 2. What the Worker did, date by date (run 2)

```
global-financial-crisis        covid-crash                    inflation-shock
2008-07-01  no proposal  ×1    2020-01-02  no proposal  ×1    2022-01-03  no proposal ×1
2008-08-01  no proposal  ×1    2020-02-03  ⛔ gate 6          2022-02-01  no proposal ×1
2008-09-02  no proposal  ×1    2020-03-02  ⛔ min change ×1   2022-03-01  ACCEPTED    ×1
2008-10-01  no proposal  ×1    2020-04-01  ⛔ gate 6          2022-04-01  no proposal
2008-11-03  no proposal        2020-05-01  no proposal        2022-05-02  no proposal
2008-12-01  no proposal        2020-06-01  no proposal        2022-06-01  no proposal ×1
2009-01-02  ACCEPTED           2020-07-01  no proposal  ×1    2022-07-01  no proposal
                                                              (×N = innovations)
```

**The pattern that holds across both runs:** the Worker's ability to ACT tracks
whether the corpus has anything proven to say about the regime.

- **2008** — it observes and does not propose. The two integrated invariants
  concern inflation and negative real rates; in a deflationary credit crisis
  they are dormant, so nothing is citable and it knows it.
- **2020** — it tries three times and is blocked twice on gate 6 (the cited
  invariant is not eligible), once for a move too small to pay its costs.
- **2022** — the inflation regime activates both invariants, and this is where
  the accepted reallocations occur in both runs.

Measured on the snapshot: **2 of 253 invariants are `integrated`**, and both are
inflation-shaped. Gate 6 does not throttle uniformly — it lets the Worker act
exactly where the corpus is proven, and silences it everywhere else.

Whether that is the system working (ADR-006: act only on proven ground) or the
cognitive layer being wasted where thinking would matter most is the owner's
call. What is NOT a judgement call: nothing currently reports "the corpus was
silent this month" as distinct from "the Worker cited badly". Both surface as
the same gate name.

---

## 3. The behavioural channel — what reproduced

11 innovations in each run, every one carrying a spec. The covid ones are
identical by construction (resumed), so only 2008 and 2022 test reproducibility.
**Two claims recur across independent runs, on different dates, with no shared
memory:**

**The 200-day trend overlay is incomplete.** Run 1: twice in the 2008 episode
(2008-07-01, 2008-12-01) plus a GLD variant in 2022. Run 2: three times in 2008
(2008-07-01, 2008-08-01, 2008-10-01) plus "cap the small-value sleeve". The rule
redirects SPY and GLD when below trend while IWN carries 40% of the
`credit-spread-wide` book unchecked. Verifiable in source:
`market_signal.TREND_SLEEVES = ("SPY", "GLD")`.

**The haven is fixed and should not be.** Both runs raise it **on the same
date**, 2022-02-01, in different words: redirecting a below-trend sleeve into
IEF is, at 5% CPI and a −5% real short rate, a flight into the asset that is
also falling. `TREND_HAVEN = "IEF"` is unconditional.

Non-reproducing but sound: the spread signal reads a LEVEL and ignores its SPEED
(run 1, three times: 2020-03-02, 2022-03-01, 2022-05-02 — but two of those are
covid, i.e. shared). This targets a weakness `market_signal.py` has already
MEASURED and documented: of 36 book changes over 409 monthly decisions, 25% were
decided by a margin under 2% and 14 reversed within 3 months. The adopted remedy
was `CONFIRM_DECISIONS = 3`; the Worker proposes a different one. The question is
not whether it works but whether it adds anything ON TOP of the confirmation
rule.

**A quality caveat, found by checking the specs against the code.** Two run-1
proposals misstate the current rule — they claim the overlay covers "SPY only"
when `TREND_SLEEVES` includes GLD, which the run's own logs show
(`below-trend=['SPY','GLD']`). A third states it correctly. **The Worker reasons
well on what it reads and less well on what it recalls of the rule.** Its
critiques are sound; its descriptions of the status quo need checking.

**ADR-011 held everywhere, both runs.** The Worker never re-picked a book. Its
own words: *"I am not re-picking the book — but I have logged this as a
strategy_revision so the tension gets measured rather than relitigated each
cycle."* Gate 0 enforces sovereignty, but it was never tested: it never tried.

**The cost, and it is real:** the `strategy_revision` proposals describe RULE
changes and carry no target allocation. `_commit_candidate_portfolio` needs one
to build a NAV, so none can be backtested and probation closes them as
`unmeasurable`. **The most reproducible output of this screen is untestable by
the machine that produced it.**

---

## 4. Reliability of the harness itself

Run 1 lost 3 dates of 21, all to the 900s per-date bound: 2008-10-01 (Lehman),
2022-01-03, 2022-06-01 — every one a hinge month. Run 2, at 1800s, lost **none**.

**But the hypothesis that hinge months need more thinking time is NOT confirmed,
and this document asserted it after run 1.** 2008-10-01 completed in 6m23 in run
2 — well inside the OLD bound. What killed it in run 1 was a malformed response
after 13m29 leaving 91 seconds for the retry. The dates were not long; they
absorbed a transient stall. The raised bound helps by leaving room for a stall
AND a retry, which is a different justification from the one first given.

Run 2 saw three transient faults (two `JSONDecodeError`, one
`UsageLimitExceeded`) and the retry recovered all three. Without it the run
would have lost three dates instead of zero.

Reaching this point required six fixes during the day, three of them in
PRODUCTION code paths: a tool refusal aborting the whole cycle, a SQL error
aborting the whole cycle, and the shared HTTP transport (a bare `timeout=300.0`
setting connect/read/write/pool alike, so a reused dead keep-alive connection
cost 497 seconds against OpenRouter's own 1.3s record). For a gate whose question
is "is this ready for real money", that the system needed six repairs to complete
one run is evidence in its own right — of immaturity now repaired, and of how
much of it only surfaces under a real workload.

---

## 5. Against the milestone's Definition of Verified

- **best-case check: A' beats B at all?** — yes, 2 of 3, in both runs; the third
  a strict tie with zero accepted reallocations.
- **behavioural log readable; sensible reasoning? sensible improvements?** — 21
  readings in `m8b-report.txt`, 11 proposals in the JSON, plus run 1's under
  `run1-archive/`. **This is the box that needs the owner to READ.**
- **A'−A reported, labelled semi-PIT** — reported, and shown NOT to reproduce.
  It also does not isolate the reallocation contribution (ADR-007 removed the
  switches from A' but not from A; MILESTONES was corrected).
- **`test_agentic_replay_semipit` green** — yes (601 tests).

---

## 6. What I would want the owner to weigh

**For proceeding:** the whole chain ran end to end at historical dates, twice;
A' beats hold in the two episodes where a comparison is meaningful, in both
runs; ADR-011 held without ever being tested; and the knowledge factory produced
specific, verifiable, code-checkable critiques of the mechanical rule — two of
which a second independent run found again.

**Against, or at least before:** the cognitive layer is structurally mute in two
regimes of three, and nothing reports that as a corpus signal rather than a
Worker failure; its most valuable output (rule revisions) cannot be measured by
the current machinery; 209 of 242 proposed invariants have never been confronted
once, which is the root of the citability drought; and the NAV channel cannot
support any conclusion at this sample size, so "does the cognitive half pay for
itself?" remains unanswered — by design of the screen, not by its failure.

None of those is a reason to stop. All are reasons to fix something before this
runs on real money, and all are cheap relative to what they change.
