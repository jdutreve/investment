"""M6 unit tests for the walk-forward calibration (docs/TASKS.md Task 9.2) —
grid construction and the confirmed-write path of `mechanical/calibration.py`.
"""

from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical.calibration import GRID, GridPoint, apply_thresholds, grid_points
from investment.mechanical.gates import ProposalThresholds
from investment.mechanical.replay import ReplayThresholds

BASE = ReplayThresholds(
    proposal=ProposalThresholds(
        sortino_gap_min=0.02,
        calmar_min=1.5,
        min_allocation_change_pts=5.0,
        max_turnover_pct=30.0,
        blend_scenario_weight=0.4,
        blend_favors_weight=0.6,
    ),
    tiebreak_window=0.02,
)


def test_grid_contains_every_seeded_value() -> None:
    """The question the grid answers is "is the hand-picked opinion
    defensible?" — so the seeded value must be IN each axis, forced to win on
    merit."""
    assert 0.02 in GRID["proposal_sortino_gap_min"]
    assert 1.5 in GRID["proposal_calmar_min"]
    assert 30.0 in GRID["proposal_max_turnover_pct"]
    assert 0.4 in GRID["blend_scenario_weight"]
    assert 2.0 in GRID["replay_confirmation_weeks"]
    assert 756.0 in GRID["rolling_window_days"]


def test_grid_points_split_one_delta_and_pin_the_unsearched_knobs() -> None:
    points = grid_points(BASE)
    assert len(points) == 729  # 3^6
    for point in points:
        p = point.thresholds.proposal
        # The blend weights split ONE delta (scenario + favors = 1).
        assert p.blend_scenario_weight + p.blend_favors_weight == pytest.approx(1.0)
        # Unsearched knobs come from base, never invented: the meaningfulness
        # floor and the (outcome-inert, dropped) tiebreak window.
        assert p.min_allocation_change_pts == 5.0
        assert point.thresholds.tiebreak_window == 0.02


async def test_apply_thresholds_writes_all_grid_keys_after_the_event(tmp_path: Path) -> None:
    """The confirmed write covers the regridded axes too — a winning
    `confirmation_weeks`/`window_days` silently dropped on apply would make
    the live chain run a DIFFERENT set than the one the user confirmed. The
    UserDecisionEvent lands in the same transaction (CLAUDE.md 'EventLog')."""
    db = InvestmentDB(tmp_path / "calibration.db")
    keys = [
        "proposal_sortino_gap_min",
        "proposal_calmar_min",
        "proposal_min_allocation_change_pts",
        "proposal_max_turnover_pct",
        "ranking_tiebreak_window",
        "blend_scenario_weight",
        "blend_favors_weight",
        "replay_confirmation_weeks",
        "rolling_window_days",
    ]
    for key in keys:
        await db.command(
            "INSERT INTO system_thresholds (key, value, updated_at) VALUES (:k, 0.0, 'seed')",
            k=key,
        )

    point = GridPoint(thresholds=BASE, confirmation_weeks=3.0, window_days=378)
    await apply_thresholds(db, point)

    rows = await db.query("SELECT key, value FROM system_thresholds")
    values = {r["key"]: r["value"] for r in rows}
    assert values["replay_confirmation_weeks"] == 3.0
    assert values["rolling_window_days"] == 378.0
    assert values["proposal_calmar_min"] == 1.5
    events = await db.query("SELECT type, payload FROM event_log")
    assert len(events) == 1
    assert events[0]["type"] == "UserDecisionEvent"
    await db.close()
