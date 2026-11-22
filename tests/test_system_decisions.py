"""The owner's decisions on the Worker's system proposals reach the Worker
(owner, 2026-11-22): in its context, and on each idea it filed."""

from investment.planner.system_decisions import DECISIONS, decision_for, describe_decisions
from investment.planner.worker_history import idea_fate


def test_each_filed_proposal_has_at_most_one_decision() -> None:
    ids = [i for decision in DECISIONS for i in decision.innovation_ids]
    assert len(ids) == len(set(ids))


def test_refused_ideas_come_first_in_the_context() -> None:
    lines = describe_decisions()
    outcomes = [line.split("] ")[1].split(":")[0] for line in lines[1:]]
    assert outcomes == sorted(outcomes, key=lambda o: o != "REFUSED")


def test_an_idea_reads_the_decision_taken_on_it() -> None:
    decided = DECISIONS[0]
    fate = idea_fate({"type": "process", "ids": f"inn-other,{decided.innovation_ids[0]}"})
    assert fate.startswith(f"{decided.outcome.upper()} by the owner on")
    assert decision_for("inn-unknown") is None
    assert idea_fate({"type": "process", "ids": "inn-unknown", "spec": "{}"}).startswith(
        "recorded for the owner"
    )
