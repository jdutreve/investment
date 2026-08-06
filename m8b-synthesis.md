# M8b — synthesis for the STOP POINT decision

Run of 2026-08-06. Raw material: `m8b-report.txt` (metrics + the 18 readings in
full) and `m8b-report.innovations.json` (11 proposals with their specs). This
document does not decide; it arranges the evidence against the milestone's own
two questions.

**Read this first:** the run is labelled *semi-PIT, best-case*. The market data
is bounded at t, the corpus is not — the Worker of July 2008 reasoned with
invariants born in July 2026. That inflates the behavioural channel by an amount
nobody can measure. M8b is a screen you can fail, not one you can pass.

---

## 1. The numbers

```
                          A'        A        B      A'−A   beats B  accepted  lost
global-financial-crisis  -13.98%  -11.49%  -17.00%  -2.50%    YES       1       1
covid-crash              +21.79%  +24.12%  +21.79%  -2.32%    no        0       0
inflation-shock          -13.30%  -13.77%  -15.54%  +0.47%    YES       4       2
```

A' = the full system. A = the mechanical rules alone. B = hold All Weather.

**Question 1 — does the best-case system beat All Weather at all?** Two yes, one
tie. The covid "no" is A' equal to B on every figure — CAGR, Sortino and
drawdown — because zero reallocations were accepted. It is a tautology, not a
defeat: with nothing accepted, A' *is* the hold curve by construction.

**Question 2 — what does A'−A say?** Less than it looks. Covid's −2.32% contains
no cognitive decision at all, so it measures only that the mechanical arm beat
hold that quarter. Of the two episodes where the Worker actually moved capital:

- 1 reallocation → −2.50%
- 4 reallocations → **+0.47%**, and inflation-shock is also the only episode
  where A' beats hold on all three dimensions at once (return, Sortino, maxDD).

The episode where it acted most is the one where it added. Three episodes and
five reallocations cannot carry a conclusion — but the sign points the opposite
way to the sum of the three deltas, and that is worth knowing before reading
"−2.5, −2.3, +0.5" as a verdict.

---

## 2. What the Worker did, date by date

```
global-financial-crisis        covid-crash                    inflation-shock
2008-07-01  no proposal  ×1    2020-01-02  no proposal  ×1    2022-01-03  LOST
2008-08-01  no proposal        2020-02-03  ⛔ gate 6          2022-02-01  no proposal ×1
2008-09-02  no proposal        2020-03-02  ⛔ min change ×1   2022-03-01  ACCEPTED    ×1
2008-10-01  LOST               2020-04-01  ⛔ gate 6          2022-04-01  ACCEPTED    ×1
2008-11-03  no proposal  ×1    2020-05-01  no proposal        2022-05-02  ACCEPTED    ×2
2008-12-01  no proposal  ×1    2020-06-01  no proposal        2022-06-01  LOST
2009-01-02  ACCEPTED           2020-07-01  no proposal  ×1    2022-07-01  ACCEPTED
                                                              (×N = innovations)
```

**A pattern runs through all three, and it is the single most important thing in
this run.** The Worker's ability to ACT tracks whether the corpus has anything
proven to say about the regime:

- **2008** — it observes and does not propose. The two integrated invariants
  concern inflation and negative real rates; in a deflationary credit crisis
  they are dormant, so nothing is citable and it knows it.
- **2020** — it tries three times and is blocked twice on gate 6 (the cited
  invariant is not eligible) and once for proposing a move too small to pay its
  costs.
- **2022** — inflation regime, the two invariants activate, and four of five
  completed dates produce an accepted reallocation.

Measured on the snapshot: **2 of 253 invariants are `integrated`**, and both are
inflation-shaped. So gate 6 does not throttle uniformly — it lets the Worker act
exactly where the corpus is proven, and silences it everywhere else.

Whether that is the system working (ADR-006: act only on proven ground) or the
cognitive layer being wasted where thinking would matter most is a judgement
call, and it belongs to the owner. What is NOT a judgement call: nothing in the
system currently reports "the corpus was silent this month" as distinct from
"the Worker cited badly". Both surface as the same gate name.

---

## 3. The behavioural channel

11 innovations, every one carrying a spec. Two claims recur **independently, in
separate snapshots, with no shared memory between episodes**:

**The 200-day trend overlay is incomplete** — 2008-07-01, 2008-12-01, and again
2022-05-02 for the GLD sleeve. The rule redirects SPY (and GLD) when below
trend, while IWN carries 40% of the `credit-spread-wide` book unchecked. This is
verifiable in the source: `market_signal.TREND_SLEEVES = ("SPY", "GLD")`.

**The spread signal reads a LEVEL and ignores its SPEED** — 2020-03-02,
2022-03-01, 2022-05-02. Wide-and-stable and wide-and-still-widening are treated
identically by a comparison against a trailing median.

Two structural gaps, found three times each, fourteen years of replayed time
apart. That is reproducible, not an opinion.

Other proposals: two `data` (add high-yield OAS; blend a market-implied
inflation proxy into the regime detector's inflation leg) and one `process`
(audit the stack's backtest-to-live drawdown divergence).

**And ADR-011 held everywhere.** The Worker never re-picked a book. Its own
words, 2008-09-02: *"I am not re-picking the book — but I have logged this as a
strategy_revision so the tension gets measured rather than relitigated each
cycle."* Sovereignty is enforced by gate 0, but it was never tested here: it
never tried.

**The cost, and it is real:** all 8 `strategy_revision` proposals describe a RULE
change and carry no target allocation. `_commit_candidate_portfolio` needs one to
build a NAV, so none of them can be backtested; probation closes them as
`unmeasurable`. The most reproducible output of this run is currently untestable
by the machine that produced it.

---

## 4. The three lost dates

```
2008-10-01   Lehman
2022-01-03   the inflation shock settling in
2022-06-01   peak inflation panic
```

All three hit the 900-second per-date bound. All three are hinge months — the
richest context, the longest reading. Durations of the *successful* dates
(episodes 2-3, post transport fix): 206 · 341 · 348 · 378 · 406 · 408 · 423 ·
445 · 472 · 558 · 661 seconds. Median 408s, tail 661s.

The lost dates were cut, not measured, so how long they needed is unknown. See
`docs/IMPROVEMENTS.md` I-52; the recommendation is 1800s and a re-run of those
three from the journal.

---

## 5. Against the milestone's Definition of Verified

- **best-case check: A' beats B at all?** — 2 of 3, the third a strict tie with
  zero accepted reallocations. Evidence: section 1.
- **behavioural log readable; does it reason sensibly? propose sensible
  improvements?** — the 18 readings are in `m8b-report.txt`; the 11 proposals in
  the JSON. This is the box that needs the owner to READ, not to count.
- **A'−A reported, labelled semi-PIT** — done, section 1, with the caveat that
  it does not isolate the reallocation contribution (ADR-007 removed the
  switches from A' but not from A; MILESTONES was corrected on this).
- **`test_agentic_replay_semipit` green** — yes, in the suite (601 tests).

---

## 6. What I would want the owner to weigh

**For proceeding:** the whole chain ran end to end at historical dates; A' beats
hold in the two episodes where a comparison is meaningful; the one episode with
real cognitive activity is the one that added return; and the knowledge factory
produced reproducible, specific, verifiable critiques of the mechanical rule.

**Against, or at least before:** the cognitive layer is structurally mute in two
regimes out of three, and nothing reports that as a corpus signal; its most
valuable output (rule revisions) cannot currently be measured; 209 of 242
proposed invariants have never been confronted once, which is the root of the
citability drought; and three hinge dates are missing from the sample.

None of those is a reason to stop. All four are reasons to fix something before
this runs on real money — and all four are cheap relative to what they change.
