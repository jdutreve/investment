"""What became of the Worker's SYSTEM proposals — the `data` and `process`
innovations nothing measures on its own.

WHY THIS EXISTS (owner, 2026-11-22). An invariant is confronted over 35 years
and a rule revision replayed, by the machine; a proposal to read a new series
or change how the system works is only recorded (`writeback.commit_innovations`:
"InnovationEvent only") and needs someone to write code. Each session now
reviews them: Claude proposes to implement and measure, or to refuse, and the
owner decides. Without a channel back, the Worker kept proposing what had been
done or refused — the regime-acceleration critique ran from 2026-08 to 2026-11.

A LIST IN THE CODE, like `research_findings.FINDINGS` and for the same reason:
a decision is reviewed and deployed with the change it accompanies, and an
entry is added in the same commit that implements (or declines) the proposal.
The Worker reads it in its context, and each recent idea it filed is labelled
with its decision (`worker_history.idea_fate`).
"""

import dataclasses
from datetime import date
from typing import Literal


@dataclasses.dataclass(frozen=True)
class SystemDecision:
    """One decision on one or more system proposals (the wordings of one idea)."""

    decided_on: date
    innovation_ids: tuple[str, ...]
    outcome: Literal["applied", "refused"]
    idea: str  # the idea in a few words
    reason: str  # what was done, or why not


DECISIONS: tuple[SystemDecision, ...] = (
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01KZRFFJNETGABTF45205RVC2G",),
        outcome="applied",
        idea="the haven exceeding the single-asset cap",
        reason=(
            "the haven chain (IEF and the cash fallback) is exempt from the concentration "
            "cap, ADR-007's second addendum: refusing the flight to safety froze the stack."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01M4MXBEKSTN70DQ20SJW2X3MY",),
        outcome="applied",
        idea="the rule statement naming the checked sleeves",
        reason=(
            "the statement is generated from the live constants and describes the haven's "
            "own all-or-nothing test apart from the graduated sleeves."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01M2ZNKK44D6GJ2JK0VS81KTAN", "inn-01M2CP72AXYS72254S4BN699P1"),
        outcome="applied",
        idea="stale macro figures in the context",
        reason=(
            "the context reads today's macro tape, each line dated, instead of the snapshot "
            "taken when the regime opened."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01M1TN8MM7SSG4G8AYY0725KQ7",),
        outcome="applied",
        idea="the overlay's per-line state in the context",
        reason=(
            "each sleeve's position against each moving average, at the decision and today, "
            "is printed in the digest you read in full."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01M0PWHTH2WHR13CTS64N1HD3M",),
        outcome="refused",
        idea="gold_10y_dev's transform suspected wrong",
        reason=(
            "verified: its monthly changes move with GLD's (correlation +0.41 over 337 "
            "months); its level is negative because the 10-year yield quintupled since 2020, "
            "which shrinks gold priced in yield units."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=(
            "inn-01KZRFFJSASMSQXF57V641QE71",
            "inn-01KZRTP3ZGDSJ1B5XA0ZF89DDT",
            "inn-01KZRFFJN5HZSYEPGJ2XQ05S1F",
            "inn-01KZRTP3XM5ACGCRH3PRC8JNYQ",
        ),
        outcome="refused",
        idea="the regime detector weighting inflation's speed and acceleration",
        reason=(
            "the detector already classifies direction on a smoothed speed, and the regime "
            "label drives no allocation since ADR-007: the stack reads the credit spread and "
            "the yield slope."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01KZRFFJT0T1J2NAHKRX08GDXP",),
        outcome="refused",
        idea="prioritising the integration of the stagflation invariants",
        reason=(
            "an invariant is integrated by its measured record at fixed checkpoints, never "
            "by priority: hastening a cluster would be belief, not evidence (ADR-006)."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01M56Y74S2P5PE7YZSDES4MQA7", "inn-01M57W0FCNFSV00J6WH7PNZQKS"),
        outcome="refused",
        idea="flagging an order computed on a retired book definition",
        reason=(
            "a book changes only in a session with the owner, who is told then; the next "
            "order is always recomputed with the book in force. A check for a rare, attended "
            "event is not worth its code."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01M7TQ257B2YN8PJG7A2HY69EA",),
        outcome="applied",
        idea="what a pending book would hold after the overlay",
        reason=(
            "the digest's pending-switch line says what the pending book would hold through "
            "today's trend lines (the flat book read IEF 100 on 2026-11-22, not SPY 60 / GLD 40)."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01M7F3819EP99XQ68T5DAAZ9MS",),
        outcome="refused",
        idea="flagging missing and inconsistent prints on the macro tape",
        reason=(
            "every row is dated the day it became knowable (ADR-003), so DGS10 minus DGS2 and "
            "T10Y2Y on one date are prints of different days — they differed by more than "
            "0.05 on 42% of dates — and weekend dates are publication lags, not anomalies. A "
            "print that never arrives already raises the macro freshness alert."
        ),
    ),
    SystemDecision(
        decided_on=date(2026, 11, 22),
        innovation_ids=("inn-01M813SZEZ026SCGBWRG0C7H4H",),
        outcome="refused",
        idea="high-yield and investment-grade option-adjusted spreads as an early credit signal",
        reason=(
            "a lead of a few weeks over BAA10Y buys nothing when a book switch waits for three "
            "consecutive monthly confirmations; and FRED now serves these series from "
            "2023-11 only, too short to measure any lead."
        ),
    ),
)


def decision_for(innovation_id: str) -> SystemDecision | None:
    """The decision taken on one filed proposal, if any."""
    return next((d for d in DECISIONS if innovation_id in d.innovation_ids), None)


def describe_decisions(decisions: tuple[SystemDecision, ...] = DECISIONS) -> list[str]:
    """The decisions as prompt lines: refused ideas first, the ones not to raise again."""
    if not decisions:
        return []
    lines = [
        "SYSTEM PROPOSALS ALREADY DECIDED by the owner — data and process ideas, yours or "
        "earlier ones. Do not propose an applied one again, nor a refused one without saying "
        "what new evidence yours brings.",
    ]
    for decision in sorted(decisions, key=lambda d: (d.outcome != "refused", d.decided_on)):
        lines.append(
            f"  [{decision.decided_on.isoformat()}] {decision.outcome.upper()}: "
            f"{decision.idea} — {decision.reason}"
        )
    return lines
