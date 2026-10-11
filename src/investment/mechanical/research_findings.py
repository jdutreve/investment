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
from datetime import date
from typing import Literal


@dataclasses.dataclass(frozen=True)
class Finding:
    """One question put to the history, and what it answered."""

    measured_on: date
    about: Literal["rule", "corpus"]
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
            "(VCIT 50 / IEF 40 / IWN 10)?"
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
)

_HEADINGS = {
    "rule": "about the rule",
    "corpus": "about the lighthouses",
}


def describe_findings(findings: tuple[Finding, ...] = FINDINGS) -> list[str]:
    """The findings as prompt lines, the rule's first, each with its date."""
    if not findings:
        return []
    lines = [
        "WHAT STRUCTURAL TESTS ALREADY ANSWERED — questions about the rule and about the "
        "lighthouses that are not a setting of the rule, each put to a protocol written "
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
                f"      ANSWER: {finding.answer.upper()}. {finding.evidence}",
                f"      SO: {finding.consequence}",
            ]
    return lines
