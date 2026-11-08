"""What research has already answered about the rule and about the corpus — the
ONLY channel by which a measured result that is not a setting reaches the
Worker.

WHY THIS EXISTS. On 2026-10-11 the Worker wrote that "the selection is the weak
link" of the month's decision and named its "boundary fragility" — a slope 0.02
above its median. Two pre-registered tests had refused exactly that reading the
day before and that same morning: the branch it questioned does not cost, and
softening the threshold does nothing. It could not have known. The results sat
in research folders the repository ignores, and nothing carried them to its
context.

`rule_revision.describe_measured` is the neighbouring channel and cannot serve:
`revision_measurement` holds what `measure_revision` can express, a VALUE of a
knob the rule already has. Holding another book, blending two of them, sending
the flight somewhere else, judging a record against its era — those are
variants of the rule's structure or facts about the corpus, and no knob names
them.

A LIST IN THE CODE, not a table. A finding is a statement about the rule as it
is written here and about what was measured on it; it is reviewed, versioned
and deployed with that rule, and a change to the rule that makes a finding
stale is a change to this file in the same commit. The agent's database is
written by the agent alone (ADR-004), and research is read-only by design.

HOW A NEW RESULT GETS HERE. When an experiment concludes — a protocol written
before its figures, run once — its answer is added to `FINDINGS` with the
tracked document that records it. That is the last step of the experiment, not
an optional write-up: a result that is not here does not exist for the Worker,
which will then raise the question again, plausibly and at length.
"""

import dataclasses
import re
from datetime import date
from typing import Literal


@dataclasses.dataclass(frozen=True)
class Finding:
    """One question put to the history, and what it answered."""

    measured_on: date
    about: Literal["rule", "corpus", "indicators"]
    question: str
    answer: str  # the headline, a few words
    evidence: str  # the figures that carry it, with their window
    consequence: str  # what it means for a reading or a proposal
    record: str  # the tracked document that holds the full result


FINDINGS: tuple[Finding, ...] = (
    Finding(
        measured_on=date(2026, 10, 4),
        about="rule",
        question=(
            "Should a fast rise of the 10-year yield (more than 20 bp in 30 days) send the "
            "bond sleeves, IEF and VCIT, to cash?"
        ),
        answer="refused",
        evidence=(
            "1993-2026: CAGR -0.70 point a year, Sortino -0.086 [90%: -0.167, -0.023], "
            "maximum drawdown unchanged. Thresholds of 10 and 30 bp do not rescue it."
        ),
        consequence=(
            "A separate duration brake on a rates signal lost; the trend overlay already "
            "takes the bond sleeves out when their own price says so."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 5),
        about="rule",
        question=(
            "Should a private-credit boom (credit/GDP up more than 10 points in three "
            "years) cut the equity sleeves by a fifth?"
        ),
        answer="not validated",
        evidence=(
            "1993-2026: -0.30 point of CAGR a year for +0.012 of Sortino "
            "[90%: -0.025, +0.053]; a constant reduction of the same size does as well; "
            "the signal was on in ONE episode, 2000-2010."
        ),
        consequence=(
            "The equities it would cut are the ones the trend overlay had kept because "
            "they were rising; it removes return, not risk."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 10),
        about="rule",
        question=(
            "Does the tight-steep branch cost — would the stack do better holding its "
            "tight-flat book (SPY 60 / GLD 40) wherever it holds the bond book "
            "(then VCIT 50 / IEF 40 / IWN 10)?"
        ),
        answer="no — it does not cost",
        evidence=(
            "Stack minus that variant, Sortino: 1977-2026 +0.072 [90%: -0.015, +0.180]; "
            "1977-1993, never used to build the rule, +0.179 (0.4% of resamples against); "
            "1993-2026 +0.021, noise. Fourteen stays in the bond book: thirteen closed, "
            "+22.2 log points; the fourteenth, open since 2025-07, -14.6, the worst in "
            "49 years."
        ),
        consequence=(
            "The stay that began in 2025-07 is a bad stay, not evidence that the branch "
            "is the weak link. Dropping the bond book is not to be proposed without new "
            "data."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 17),
        about="rule",
        question=(
            "Should the bond book's 10% equity sleeve (then VCIT 50 / IEF 40 / IWN 10) hold "
            "another asset of the project, or nothing, rather than small-cap value?"
        ),
        answer="no — not distinguishable",
        evidence=(
            "Alternative minus stack, Sortino, 1977-2026: GLD -0.008 [90%: -0.032, +0.013], "
            "SPY -0.004, no sleeve -0.005, cash -0.014, VTI -0.008; on shorter windows TLT, "
            "DJP, SHY, EFA, QQQ, IWM, IYR, EEM, TIP within +/-0.02. Only DBC (2007-2026) "
            "+0.061 passes an uncorrected 5%, not the 0.05/13 bar, and half of it is the "
            "2008 commodity spike; DJP from 1992 shows +0.011. Dropping the sleeve saves "
            "about one order a year."
        ),
        consequence=(
            "No asset in that seat changes anything measurable, so the tie went to the "
            "simpler book: VCIT 50 / IEF 50 since 2026-10-17. Do not propose restoring the "
            "sleeve or filling it with another asset without new data."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 11),
        about="rule",
        question=(
            "Is the slope's threshold fragile — should the stack hold half of each "
            "same-spread book when the slope is within 0.10 point of its ten-year median?"
        ),
        answer="no — it does nothing",
        evidence=(
            "Blend minus stack, Sortino: 1977-2026 -0.005 [90%: -0.027, +0.014]; the same "
            "on every window and with bands of 0.05 and 0.20. In the 2025-07 stay the "
            "22-point gap to the tight-flat book was earned while the slope was 0.13 to "
            "0.28 ABOVE its median, not at the edge."
        ),
        consequence=(
            "A slope a few hundredths from its median is not a weakness of the decision. "
            "A band, a dead zone or a blend around that median is not to be proposed "
            "again."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 11),
        about="rule",
        question=(
            "When the haven IEF is itself below its trend, should the weight bound for "
            "cash go to the sleeves that are still fully in trend?"
        ),
        answer="no — it costs",
        evidence=(
            "Variant minus stack, Sortino: 1977-2026 -0.051 [90%: -0.099, -0.013], 0.7% "
            "of resamples at or above zero; negative on every window. Eighteen runs, "
            "twelve against, the largest losses in 1978 and 1994; over 2026-08 to 2026-10 "
            "the variant lost too."
        ),
        consequence=(
            "Cash while SPY is still rising is the rule working as measured: a falling "
            "Treasury market is a rate shock, and what still trends at its start seldom "
            "does for long. The overlay removes and never replaces — by measurement."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 11),
        about="rule",
        question=(
            "The spread has sat under its ten-year median four decisions in five since "
            "2014, so the equity-heavy books almost never fire: should the medians be "
            "read on a shorter or a longer window?"
        ),
        answer="no — ten years stays",
        evidence=(
            "median_window_years at 3, 5, 7 and 15 over 1977-2026 and four sub-windows. "
            "Shorter is WORSE where it was expected to help: 2009-2026 CAGR 10.35% falls "
            "to 6.50% (3 years), 8.38% (5), 8.56% (7), because a short median reads the "
            "still-falling spread of 2010 as tight and leaves equities mid-recovery. Five "
            "years adopts on 1977-1993 only; fifteen moves nothing."
        ),
        consequence=(
            "The stack trailing SPY since 2014 (9.3% against 15.4% a year over ten years, "
            "same Sortino, drawdown -16.5% against -33.7%) is the price of its drawdown "
            "control in a bull market, not a mis-set window."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 11),
        about="rule",
        question=(
            "Does the 60% cap hold the stack back — would more SPY in the tight-flat "
            "book (SPY 60 / GLD 40) earn more?"
        ),
        answer="no — and the peak at 60 is not real",
        evidence=(
            "The book at SPY 50 to 100 over 1977-2026. Sortino against the rule: 50 "
            "+0.006, 70 -0.026, 80 -0.070, 90 -0.130, none proven; 100 -0.198, proven "
            "worse. Since 2009 more SPY buys NO return (10.39% at 60, 10.36% at 90: gold "
            "earned as much inside this book) and costs 1.2 point of drawdown a notch; "
            "over 1977-1993 it costs 0.8 point of return a notch."
        ),
        consequence=(
            "Where the cap sits is a choice of risk, not of return: from 50 to 70 the "
            "Sortino does not choose but the drawdown differs, about a point a notch. The "
            "stack trailing SPY since 2014 is not its share of equity: it is the trend "
            "overlay leaving at each fall and re-entering after the recovery began, the "
            "price of its drawdown control."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 11),
        about="rule",
        question=(
            "What does the trend overlay cost a year, and what does it pay — would the "
            "stack do better holding only part of it?"
        ),
        answer="a price list, no verdict",
        evidence=(
            "Over a whole cycle it costs nothing: 1993-2026 returns 11.46% without it "
            "and 11.49% with it, for a maximum drawdown of -40.9% against -16.5%. Since "
            "2009, between two crashes, it costs 4.2 points a year (proven), about one "
            "per quarter of intensity. Half the overlay stays inside the -25% rule "
            "(-20.7%) and takes 2007-09 at -20.7% instead of -6.0%. It does nothing for "
            "1987 or 1980-82 and little for 2020 and 2022. With one unit invested a month "
            "for 20 years from every start month, the stack with NO overlay ends ahead of "
            "the rule on 28% of starts (median wealth 0.85 of the rule's, worst 0.48); at "
            "three quarters of the overlay, 33% and 0.97."
        ),
        consequence=(
            "The stack trailing SPY since 2014 is 2.7 points from the books and 3.3 from "
            "the overlay, an insurance premium paid in quiet years. Over an accumulation "
            "horizon the overlay is worth MORE, not less: what it saves in a long bear "
            "market compounds to the end. Do not argue that a long horizon makes the "
            "overlay too cautious; the history says the opposite."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 11),
        about="rule",
        question=(
            "Should the overlay's two lines be far apart — a fast exit on six months and "
            "a slow one on four years, instead of 150 and 300 days?"
        ),
        answer="no — the pair changes nothing",
        evidence=(
            "ma_windows [126, 1008] against the rule over 1977-2026: Sortino +0.010 "
            "[90%: -0.08, +0.10], return +0.12 point a year. With one unit invested a "
            "month it ends ahead of the rule on 55% of 15-year starts and 43% of 20-year "
            "ones. The slow line alone takes the short falls in full (2020 -22.8%); the "
            "fast line alone leaves too often."
        ),
        consequence=(
            "The overlay's result comes from having a trend exit, not from where its "
            "lines sit. Another pair of windows is not to be proposed without a reason "
            "these did not cover. A single four-year line halves the number of orders "
            "for the same return and a deeper drawdown, which matters only if turnover "
            "does."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 10, 10),
        about="corpus",
        question="Is an invariant's record evidence about its condition, or about the era?",
        answer="partly the era",
        evidence=(
            "A moment is confirmed against the handle's median over the WHOLE sample. "
            "Judged against the ten years around each moment, the corpus's edge over its "
            "placebo falls from +1.56 to +0.87 point and is no longer distinguishable. "
            "Gold carries most of it: 'low real yields favor gold' is 54/83 on the whole "
            "sample and 43/85 inside its era."
        ),
        consequence=(
            "A long record on gold or on bonds may say only that its condition held in "
            "that asset's good years. The short records of rare conditions keep their "
            "rate inside their era."
        ),
        record="docs/INVARIANT_TASKS.md",
    ),
    Finding(
        measured_on=date(2026, 10, 10),
        about="corpus",
        question=(
            "Do the invariants, grouped by idea and judged on what follows, separate the "
            "next quarter or the next year from an ordinary one?"
        ),
        answer="no",
        evidence=(
            "178 ideas from 247 invariants. Twelve-week return +0.05 point gross and "
            "-0.14 net of trading cost; twelve-month return -0.03; drawdown +0.01 "
            "(twelve weeks) and -0.08 (twelve months). None distinguishable from the same "
            "conditions moved to dates where they mean nothing."
        ),
        consequence=(
            "A lighthouse describes the present and raises a question; it does not "
            "forecast. Do not present one as a prediction of the next months."
        ),
        record="docs/INVARIANT_TASKS.md",
    ),
    Finding(
        measured_on=date(2026, 11, 1),
        about="rule",
        question=(
            "Does it cost the stack that the owner places the monthly order the Monday after "
            "the Sunday digest, rather than on the decision date the backtest trades on?"
        ),
        answer="no, but the backtest is optimistic",
        evidence=(
            "1977-2026. Sunday path minus an order placed on the decision date: -0.09 pp a "
            "year [-0.42, +0.23], Sortino -0.014, 30% of resamples at or above zero. That "
            "weekday order minus the backtest: -0.62 pp a year, Sortino -0.091 [-0.143, "
            "-0.046]; since 1993 -0.83 pp, Sortino -0.132. The backtest trades at the very "
            "close its decision reads, which no hand-placed order can; the cost is broad "
            "(34 of 50 years)."
        ),
        consequence=(
            "Do not propose publishing the order on a weekday: it buys nothing measurable. "
            "Since this finding every signal-driven NAV (the stack, ms-trend-baseline, "
            "AAAF-R) is priced on the session after its decision, so today's figures already "
            "carry the cost. Measured that way, the stack's lead over its best frozen book "
            "(tight-flat) is +0.125 Sortino on 1977-2026 (16% of resamples at or below zero) "
            "and +0.052 since 1993 (36%): positive, established on no window."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 11, 1),
        about="rule",
        question=(
            "Would SPY 60 / GLD 40 held without the trend overlay, never sold and steered "
            "with purchases only, do as well as the stack for less in fees?"
        ),
        answer="no: cheaper, much riskier",
        evidence=(
            "1977-2026, never traded: 10.53% a year against the stack's 12.19%, Sortino "
            "0.629 against 1.071 (0% of resamples at or above), max drawdown -47.8% against "
            "-29.0%, worst year -31.7% against -11.3%. Since 1993: 10.34% against 10.51%, "
            "Sortino 0.747 against 1.170, drawdown -47.1% against -15.3%. Rebalanced yearly: "
            "Sortino 0.719, drawdown -35.5%. The stack's fees, already in its figures: "
            "1.06-1.19 points a year."
        ),
        consequence=(
            "The overlay sells the drawdown and pays for its fees. Do not propose dropping "
            "it to save trading costs; the stack's edge over a book under the same overlay "
            "is the open question, not the overlay."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 11, 1),
        about="rule",
        question=(
            "Is a frozen book a better choice than the stack, given that its edge over the "
            "best frozen book is not established?"
        ),
        answer="no: the best book is hindsight",
        evidence=(
            "Chosen each January with the data of the time: 7.53% a year against the "
            "stack's 10.61% from 1987, Sortino 0.642 against 1.059 (+0.415, 0% of resamples "
            "at or below zero). Picked once and held, it loses for all 30 start years "
            "1987-2016. The best frozen book (tight-flat) is known only in hindsight; it was "
            "the worst of four on 1977-1993."
        ),
        consequence=(
            "Do not propose replacing the stack with one frozen book: which book is best "
            "is not knowable in advance, and the switching is what protects against picking "
            "the wrong one."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 11, 1),
        about="rule",
        question=(
            "Should a steep curve whose 10-year yield is rising fast (a bear steepener) read "
            "as flat, so the stack holds SPY 60 / GLD 40 instead of the bond book?"
        ),
        answer="refused on 48 years",
        evidence=(
            "SLOPE_BEAR_VETO at 0.10, 0.15, 0.20, 0.30 on 1977-07 to 2025-06, the 2025 stay "
            "excluded: Sortino -0.010 to +0.018 (32-56% of resamples at or below zero); "
            "since 1993 -0.018 to +0.008. Its gain is the episode that prompts it: +8.2 "
            "points a year from 2025-07 at 0.10."
        ),
        consequence=(
            "The bear steepener is detectable but acting on it is noise in the history. Do "
            "not propose it again on the strength of the 2025-26 stay."
        ),
        record="docs/V1_STRATEGY.md",
    ),
    Finding(
        measured_on=date(2026, 11, 8),
        about="indicators",
        question=(
            "Do the digest's metal indicators — the gold switch and its two legs, the silver "
            "and copper trends, the gold/silver and copper/gold ratios against their "
            "12-month average — foretell the next 12 weeks as their customary readings say?"
        ),
        answer="no — none of the eight readings holds",
        evidence=(
            "2004-01 to 2026-07, 99 non-overlapping 12-week outcomes, ON minus OFF: gold "
            "switch on GLD -0.64% [90%: -3.80%, +2.28%]; real-yield leg -0.59%; GLD trend "
            "+0.86% [-2.05%, +3.66%]; silver and copper trends on themselves +1.08%, +0.02%. "
            "Closest: copper/gold rising on SPY minus IEF +2.07% [-0.28%, +4.50%], p=0.07, "
            "one offset of four negative. Timing GLD on the switch: CAGR 4.84% against "
            "9.96% held, Sortino 0.37 against 0.73, drawdown -45.4% against -45.6%."
        ),
        consequence=(
            "Read them as a description of the present, not a forecast: none earns a "
            "forecast, a tilt or an invariant on its own. Timing gold on the switch halved "
            "its return without cutting its drawdown. The one lead, copper/gold on equities "
            "over bonds, did not replicate out of sample (next finding)."
        ),
        record="docs/METAL_INDICATORS.md",
    ),
    Finding(
        measured_on=date(2026, 11, 8),
        about="indicators",
        question=(
            "Does copper/gold above its 12-month average favour equities over bonds out of "
            "sample, and does it say copper will keep beating gold?"
        ),
        answer="no to both",
        evidence=(
            "1993-2003, monthly IMF copper, 48 outcomes: SPY minus IEF +1.75% "
            "[90%: -2.13%, +5.48%], one offset of four reversed. Copper minus gold: "
            "2004-2026 +2.57% [-1.62%, +6.83%]; 1993-2003 -4.60% [-9.97%, +0.71%]. Held "
            "outright 2004-2026: gold +10.44% a year, drawdown -44.4%; copper +8.06%, -69.4%."
        ),
        consequence=(
            "The copper/gold ratio describes the growth mood; it does not time equities "
            "against bonds or copper against gold. Do not propose trading on it, or "
            "swapping gold for copper, without new data."
        ),
        record="docs/METAL_INDICATORS.md",
    ),
)

_HEADINGS = {
    "rule": "about the rule",
    "corpus": "about the lighthouses",
    "indicators": "about the indicators",
}


def describe_findings(findings: tuple[Finding, ...] = FINDINGS) -> list[str]:
    """The findings as prompt lines, the rule's first, each with its date."""
    if not findings:
        return []
    lines = [
        "WHAT STRUCTURAL TESTS ALREADY ANSWERED — questions about the rule, the "
        "lighthouses and the indicators that are not a setting of the rule, each put to a "
        "protocol written "
        "before its figures. They weigh as much as the settings above. Before you call a "
        "part of the rule its weak link, or propose one of these repairs, read here: the "
        "question may have been asked and answered. Do not raise one again without saying "
        "what new data, or what different mechanism, yours brings.",
    ]
    for about, heading in _HEADINGS.items():
        selected = sorted((f for f in findings if f.about == about), key=lambda f: f.measured_on)
        if not selected:
            continue
        lines.append(f"  {heading}:")
        for finding in selected:
            lines += [
                f"    [{finding.measured_on.isoformat()}] {finding.question}",
                f"      ANSWER: {finding.answer.upper()}. {headline_evidence(finding.evidence)}",
                f"      SO: {finding.consequence}",
            ]
    return lines


# THE HEADLINE FIGURES, NOT THE WHOLE TABLE (owner, 2026-11-03: trim the
# context). The full evidence is in the record each finding names; the Worker
# needs enough of it to quote the result right.
EVIDENCE_CHARS = 220


def headline_evidence(evidence: str) -> str:
    """`evidence` cut to about `EVIDENCE_CHARS`, the sentences that carry figures
    kept first, then the others while they fit, in their own order; an
    ellipsis marks the cut. A first figure-bearing sentence longer than the
    budget is cut at a word."""
    if len(evidence) <= EVIDENCE_CHARS:
        return evidence
    sentences = re.split(r"(?<=[.;])\s+", evidence)
    # A measured figure: a decimal, a percentage or a count of n/m.
    figured = [
        i for i, sentence in enumerate(sentences) if re.search(r"\d\.\d|\d%|\d/\d", sentence)
    ]
    order = figured + [i for i in range(len(sentences)) if i not in figured]
    kept: list[int] = []
    for i in order:
        if sum(len(sentences[j]) + 1 for j in kept) + len(sentences[i]) <= EVIDENCE_CHARS:
            kept.append(i)
    if not kept or (figured and not set(kept) & set(figured)):
        return sentences[order[0]][:EVIDENCE_CHARS].rsplit(" ", 1)[0] + " …"
    return " ".join(sentences[i] for i in sorted(kept)) + " …"
