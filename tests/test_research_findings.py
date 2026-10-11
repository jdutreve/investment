"""What research answered reaches the Worker, and stays tied to its record."""

import subprocess
from datetime import date
from pathlib import Path

from investment.decision_cycle import render_context_for_worker
from investment.mechanical.research_findings import FINDINGS, Finding, describe_findings
from investment.planner.context import PlannerContext

REPO = Path(__file__).resolve().parents[1]


def _empty_context() -> PlannerContext:
    return PlannerContext(
        regime={},
        global_liquidity={},
        ranking=[],
        scenarios=[],
        top_invariants=[],
        recent_proposals=[],
        passages=[],
        notes="",
    )


def test_every_finding_reaches_the_worker_whatever_else_its_context_holds() -> None:
    """THE GUARANTEE. On 2026-10-11 the Worker called the month's book selection
    "the weak link" the morning two tests had refused that reading: the results
    were in folders the repository ignores and nothing carried them to it. A
    context with no decision, no ledger and no lighthouse still states them."""
    text = render_context_for_worker(_empty_context())
    assert "WHAT STRUCTURAL TESTS ALREADY ANSWERED" in text
    for finding in FINDINGS:
        assert finding.question in text
        assert finding.evidence in text
        assert finding.consequence in text


def test_a_finding_points_at_a_record_the_repository_holds() -> None:
    """A finding whose record is an ignored folder is an assertion nobody can
    check from the repository. Each names a TRACKED file."""
    tracked = set(
        subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.split()
    )
    for finding in FINDINGS:
        assert finding.record in tracked, finding.record


def test_a_finding_is_complete_and_dated() -> None:
    for finding in FINDINGS:
        assert finding.about in ("rule", "corpus")
        assert finding.question.endswith("?")
        assert finding.answer and len(finding.answer) <= 40  # a headline, not a paragraph
        assert finding.evidence and finding.consequence
        assert date(2026, 1, 1) <= finding.measured_on <= date.today()


def test_the_rules_findings_come_first_and_in_the_order_they_were_measured() -> None:
    later = Finding(date(2026, 3, 2), "rule", "Later?", "no", "figures", "so what", "docs/X.md")
    earlier = Finding(date(2026, 3, 1), "rule", "Earlier?", "no", "figures", "so what", "docs/X.md")
    corpus = Finding(date(2026, 1, 1), "corpus", "Corpus?", "no", "figures", "so what", "docs/X.md")
    text = "\n".join(describe_findings((later, corpus, earlier)))
    assert text.index("Earlier?") < text.index("Later?") < text.index("Corpus?")
    assert "ANSWER: NO. figures" in text
    assert describe_findings(()) == []
