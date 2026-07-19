"""Walk-forward threshold calibration (docs/TASKS.md Task 9.2; docs/MILESTONES.md
M6 DoV "walk-forward calibrated thresholds (~25y calibrate / ~10y validate)").

This is what turns every hand-picked threshold (Sortino gap 0.02, Calmar 1.5,
blend 0.4/0.6, turnover 30) from an OPINION into a calibrated value — or
exposes that the grid cannot find one that works, which is just as much of a
result.

**Walk-forward split, never calibrate and judge on the same window.** The
pre-2001 spliced portion is lower-quality (more splice joins), so it is
CALIBRATION only; the held-out last ~10y runs on clean native-ETF data.

**Reading the winner (M6 DoV, and the reason this module reports the holdout
separately):** 5 knobs searched over ~25y can find almost anything. A winning
set is EVIDENCE only insofar as it survives the holdout it never saw. In
particular the calibrated `blend_favors_weight` must be read against I-35 —
the per-regime FAVORS ranking it feeds is indistinguishable from random regime
labels in 4 of 5 regimes, so a HIGH, STABLE favors weight is SUSPICIOUS, not
confirmation; a weight driven toward 0 is the result that MATCHES the evidence.

Nothing here writes to `system_thresholds`: "Winning set written to
`system_thresholds` only after user confirmation" — the confirmation lives in
the CLI (`invest calibrate --apply`, M6; Telegram arrives at M9).
"""

import argparse
import asyncio
import dataclasses
import itertools
from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd

from investment.config import Settings
from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios
from investment.mechanical.gates import ProposalThresholds
from investment.mechanical.replay import (
    ReplayInputs,
    ReplayResult,
    ReplayThresholds,
    load_inputs,
    run_replay,
    thresholds_map,
)

# The grid (docs/TASKS.md Task 9.2 names the KNOBS, not their values — these
# are a judgment call, CLAUDE.md "state assumptions explicitly). Each axis
# brackets the seeded value rather than sweeping blindly: the question is
# "is the hand-picked opinion defensible?", so the seeded value must be IN the
# grid and must have to win on merit. Kept deliberately coarse — 729 combos is
# already 6 knobs of freedom over ~25y, which is exactly the overfitting risk
# the holdout exists to price.
#
# Regridded after the first M6 run (owner-approved):
#   - `ranking_tiebreak_window` DROPPED — measured outcome-inert under the
#     current gates: the order differs on ~40% of dates across (0.01/0.02/
#     0.05) yet never changed WHICH challenger passed the gates, so every
#     grid triple scored identically. Fixed at the seeded value; re-add if
#     the gates change shape.
#   - `replay_confirmation_weeks` ADDED on the hypothesis that confirmation
#     is where signal quality is bought (switch-only hit-rate 0.53). Measured
#     on the first regrid run: NOT confirmed — 2/3/4 interleave through the
#     top 15 with no consistent direction.
#   - `rolling_window_days` ADDED on the hypothesis that the trailing window
#     is the stale half of the signal (756d = 3y of past against regimes
#     lasting months); Task 9.2 always named the "36M window" as a knob,
#     deferred for the panel-recompute cost, now paid once in
#     `load_window_panels`. Measured: REFUTED — 756 sweeps the entire top 15,
#     252/378 never appear; the signal does not improve with speed.
#   Both axes stay in the grid: a refuted hypothesis on 1991-2016 is worth
#   re-pricing whenever the mechanics change shape.
GRID: dict[str, tuple[float, ...]] = {
    # switch gate 2 — seeded 0.02 (= the ranking tie window: a gap that does
    # not even separate two rows into different tie groups).
    "proposal_sortino_gap_min": (0.02, 0.10, 0.25),
    # switch gate 3 — seeded 1.5, an absolute floor.
    "proposal_calmar_min": (1.0, 1.5, 2.0),
    # realloc gate 4 — seeded 30.0 (percent points).
    "proposal_max_turnover_pct": (15.0, 30.0, 60.0),
    # the blend — seeded 0.4/0.6, which the two weights must sum to 1.0 to
    # respect (they are the split of ONE delta), so ONE axis drives both:
    # 0.0 = pure FAVORS anchor, 1.0 = pure tactical scenario.
    "blend_scenario_weight": (0.0, 0.4, 1.0),
    # switch/scenario acceptance policy — seeded 2.0.
    "replay_confirmation_weeks": (2.0, 3.0, 4.0),
    # trailing ranking window, trading days — seeded 756.
    "rolling_window_days": (252.0, 378.0, 756.0),
}


@dataclasses.dataclass(frozen=True)
class WindowScore:
    """One threshold set's result on ONE window. `edge` is the headline the
    go-live gate asks about: agent-follow CAGR minus hold-defender CAGR, net of
    costs. Positive = adapting to the regime paid."""

    cagr_agent_follow: float | None
    cagr_hold_defender: float | None
    sortino_agent_follow: float | None
    sortino_hold_defender: float | None
    n_switches: int
    hit_rate_12w: float | None

    @property
    def edge(self) -> float | None:
        a, b = self.cagr_agent_follow, self.cagr_hold_defender
        return None if a is None or b is None else a - b


@dataclasses.dataclass(frozen=True)
class GridPoint:
    """One point of the calibration grid. `thresholds` alone is not enough
    since the regrid: `confirmation_weeks` is a `run_replay` argument and
    `window_days` selects a PANEL (the trailing indicator window), so the two
    ride here rather than being forced into `ReplayThresholds`."""

    thresholds: ReplayThresholds
    confirmation_weeks: float
    window_days: int


@dataclasses.dataclass(frozen=True)
class Candidate:
    point: GridPoint
    calibrate: WindowScore
    validate: WindowScore


@dataclasses.dataclass(frozen=True)
class CalibrationReport:
    """`ranked` is ordered by the CALIBRATION window only — the holdout is
    REPORTED, never optimized against (ranking by it would silently turn the
    holdout into a second training set and destroy the only out-of-sample
    evidence M6 has)."""

    split: date
    ranked: list[Candidate]
    seeded: Candidate

    @property
    def winner(self) -> Candidate:
        return self.ranked[0]


def grid_points(base: ReplayThresholds) -> list[GridPoint]:
    """The full cartesian product of `GRID`. `base` supplies the knobs the grid
    does not search (`proposal_min_allocation_change_pts` — a MEANINGFULNESS
    floor the owner sets, not a performance dial: calibrating it would let the
    search discover that 0.1pt "reallocations" beat doing nothing, which is a
    cost-model artefact, not an edge — and `ranking_tiebreak_window`, dropped
    as outcome-inert, see GRID)."""
    axes = list(GRID)
    combos = itertools.product(*(GRID[axis] for axis in axes))
    return [_build(base, dict(zip(axes, values, strict=True))) for values in combos]


def _build(base: ReplayThresholds, values: dict[str, float]) -> GridPoint:
    scenario_weight = values["blend_scenario_weight"]
    return GridPoint(
        thresholds=ReplayThresholds(
            proposal=ProposalThresholds(
                sortino_gap_min=values["proposal_sortino_gap_min"],
                calmar_min=values["proposal_calmar_min"],
                min_allocation_change_pts=base.proposal.min_allocation_change_pts,
                max_turnover_pct=values["proposal_max_turnover_pct"],
                blend_scenario_weight=scenario_weight,
                # The two weights split ONE delta — see GRID.
                blend_favors_weight=1.0 - scenario_weight,
            ),
            tiebreak_window=base.tiebreak_window,
        ),
        confirmation_weeks=values["replay_confirmation_weeks"],
        window_days=int(values["rolling_window_days"]),
    )


def _score(result: ReplayResult) -> WindowScore:
    return WindowScore(
        cagr_agent_follow=result.metrics_agent_follow.cagr,
        cagr_hold_defender=result.metrics_hold_defender.cagr,
        sortino_agent_follow=result.metrics_agent_follow.sortino,
        sortino_hold_defender=result.metrics_hold_defender.sortino,
        n_switches=result.n_switches,
        hit_rate_12w=result.hit_rate_12w,
    )


def _rank_key(candidate: Candidate) -> tuple[float, float]:
    """Rank on the CALIBRATION window's edge; ties broken by FEWER switches —
    when two sets buy the same edge, the one that trades less is the more
    honest fit (each switch is a cost paid and a degree of freedom used)."""
    edge = candidate.calibrate.edge
    return (edge if edge is not None else float("-inf"), -candidate.calibrate.n_switches)


def walk_forward(
    inputs: ReplayInputs,
    base: GridPoint,
    panels: Mapping[int, dict[str, pd.DataFrame]],
    *,
    start: date,
    split: date,
    end: date,
    cost_bps: float,
    candidates: Sequence[GridPoint] | None = None,
) -> CalibrationReport:
    """Run every candidate over [start, split) then over [split, end], PURE —
    `inputs` and the per-window `panels` are loaded once by the caller, so 729
    combos cost 729 in-memory replays and no SQLite round-trips."""
    candidates = list(candidates if candidates is not None else grid_points(base.thresholds))

    def evaluate(point: GridPoint) -> Candidate:
        windowed = dataclasses.replace(inputs, panel=dict(panels[point.window_days]))

        def score(window_start: date, window_end: date) -> WindowScore:
            return _score(
                run_replay(
                    windowed,
                    point.thresholds,
                    start=window_start,
                    end=window_end,
                    cost_bps=cost_bps,
                    confirmation_weeks=point.confirmation_weeks,
                )
            )

        return Candidate(
            point=point,
            calibrate=score(start, split),
            validate=score(split, end),
        )

    scored = [evaluate(point) for point in candidates]
    return CalibrationReport(
        split=split,
        ranked=sorted(scored, key=_rank_key, reverse=True),
        # The seeded set is scored on the SAME windows and reported alongside:
        # "did the grid beat the opinion, and by how much?" is the question, and
        # a winner that barely edges the seeded set on calibration while losing
        # on the holdout is the grid fitting noise.
        seeded=evaluate(base),
    )


async def load_window_panels(
    db: InvestmentDB, portfolio_ids: Sequence[str], windows: Sequence[int]
) -> dict[int, dict[str, pd.DataFrame]]:
    """One indicator panel per candidate `rolling_window_days` value, computed
    from the already-backfilled `portfolio_nav` NAV series with the SAME pinned
    `ratios.rolling_*` formulas the backfill uses. The 756 panel is recomputed
    too (rather than read back from the DB columns) so every window sits on an
    identical code path — a grid must not compare a stored panel against a
    recomputed one. Trailing windows only: each row stays knowable at its own
    date (ADR-003), whatever the window length."""
    rf = await ratios.load_rf_daily(db)
    navs = {pid: await ratios.load_nav(db, pid) for pid in portfolio_ids}
    panels: dict[int, dict[str, pd.DataFrame]] = {}
    for window in windows:
        panel: dict[str, pd.DataFrame] = {}
        for pid, nav in navs.items():
            if nav.empty:
                panel[pid] = pd.DataFrame()
                continue
            returns = ratios.daily_returns(nav)
            max_dd = ratios.rolling_max_drawdown(nav, window)
            panel[pid] = pd.DataFrame(
                {
                    "sharpe_rolling": ratios.rolling_sharpe(returns, rf, window),
                    "sortino_rolling": ratios.rolling_sortino(returns, rf, window),
                    "calmar_rolling": ratios.rolling_calmar(nav, max_dd, window),
                    "drawdown": max_dd,
                }
            )
        panels[window] = panel
    return panels


async def calibrate(db: InvestmentDB, *, start: date, split: date, end: date) -> CalibrationReport:
    """Task 9.2 entry point. Writes NOTHING (see the module docstring): the
    caller confirms, then `apply_thresholds` persists."""
    from investment.mechanical.replay import load_thresholds

    rows = await db.query("SELECT key, value FROM system_thresholds")
    values = {str(r["key"]): float(r["value"]) for r in rows}
    inputs = await load_inputs(db)
    windows = sorted({int(w) for w in GRID["rolling_window_days"]})
    panels = await load_window_panels(db, sorted(inputs.portfolios), windows)
    base = GridPoint(
        thresholds=await load_thresholds(db),
        confirmation_weeks=values["replay_confirmation_weeks"],
        window_days=int(values["rolling_window_days"]),
    )
    return walk_forward(
        inputs,
        base,
        panels,
        start=start,
        split=split,
        end=end,
        cost_bps=values["replay_cost_bps"],
    )


async def apply_thresholds(db: InvestmentDB, point: GridPoint) -> None:
    """ "Winning set written to `system_thresholds` only after USER
    confirmation" (Task 9.2) — this is the write, and the CLI is the
    confirmation. Appends a UserDecisionEvent BEFORE the update, same
    transaction (CLAUDE.md "EventLog"): a threshold change is a decision, and
    the replay report that justified it must stay auditable."""
    updates = {
        **thresholds_map(point.thresholds),
        "replay_confirmation_weeks": point.confirmation_weeks,
        "rolling_window_days": float(point.window_days),
    }
    async with db.transaction():
        await db.append_event(
            type="UserDecisionEvent",
            source_uc="UC9",
            source_id=None,
            payload={"action": "apply_calibrated_thresholds", "thresholds": updates},
        )
        for key, value in updates.items():
            await db.command(
                "UPDATE system_thresholds SET value = :value, updated_at = :now WHERE key = :key",
                key=key,
                value=value,
                now=date.today().isoformat(),
            )


# -- runner ----------------------------------------------------------------

# ~25y calibrate / ~10y validate over the ~1991 proxy floor (docs/TASKS.md
# Task 9.2). The split is also a DATA-QUALITY boundary, not just a date: the
# pre-2001 spliced portion carries more splice joins, so it is calibration
# only, and the holdout runs on clean native-ETF data.
DEFAULT_SPLIT = date(2016, 1, 1)


def _fmt(score: object, field: str) -> str:
    value = getattr(score, field)
    return "n/a" if value is None else f"{value * 100:+.3f}"


def render(report: CalibrationReport, top: int) -> str:
    lines = [
        f"Walk-forward calibration — calibrate -> {report.split} | validate {report.split} ->",
        "",
        "Ranked by the CALIBRATION window's edge. The holdout is REPORTED, never",
        "optimized against — it is the only out-of-sample evidence M6 has.",
        "",
        f"{'':4s}{'edge cal':>10s}{'edge val':>10s}{'sw':>5s}{'hit':>7s}  thresholds",
    ]
    for i, c in enumerate(report.ranked[:top], start=1):
        lines.append(f"{i:<4d}{_row(c)}")
    lines += ["", f"{'seed':4s}{_row(report.seeded)}   <- the hand-picked opinion"]
    return "\n".join(lines)


def _row(c: Candidate) -> str:
    hit = c.validate.hit_rate_12w
    proposal = c.point.thresholds.proposal
    knobs = (
        f"gap={proposal.sortino_gap_min:g} "
        f"calmar={proposal.calmar_min:g} "
        f"turnover={proposal.max_turnover_pct:g} "
        f"favors={proposal.blend_favors_weight:g} "
        f"confirm={c.point.confirmation_weeks:g} "
        f"window={c.point.window_days}"
    )
    return (
        f"{_fmt(c.calibrate, 'edge'):>10s}{_fmt(c.validate, 'edge'):>10s}"
        f"{c.validate.n_switches:>5d}{('n/a' if hit is None else f'{hit:.3f}'):>7s}  {knobs}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 9 walk-forward threshold calibration (Task 9.2)."
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date(1991, 1, 1))
    parser.add_argument("--split", type=date.fromisoformat, default=DEFAULT_SPLIT)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--top", type=int, default=10, help="how many ranked sets to show")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the winning set to system_thresholds (asks for confirmation first)",
    )
    args = parser.parse_args()

    async def run() -> None:
        db = InvestmentDB(Settings().db_path)  # type: ignore[call-arg]
        try:
            report = await calibrate(db, start=args.start, split=args.split, end=args.end)
            print(render(report, args.top))
            if not args.apply:
                return
            # "Winning set written to `system_thresholds` only after user
            # confirmation" (Task 9.2) — the confirmation is HERE at M6;
            # Telegram takes over at M9.
            print(f"\nApply the winning set to system_thresholds?\n  {report.winner.point}")
            if input("type 'apply' to confirm: ").strip() != "apply":
                print("not applied.")
                return
            await apply_thresholds(db, report.winner.point)
            print("applied.")
        finally:
            await db.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
