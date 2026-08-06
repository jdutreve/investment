"""The agentic replay — M8b's pre-go-live screen (docs/TASKS.md Task 9.4;
docs/MILESTONES.md M8b, a STOP POINT).

The mechanical replay (M6) answers "do the rules beat holding All Weather?".
This answers the question M6 cannot: "does the COGNITIVE half add anything?" —
by running the real Planner, the real Worker and the real gates at historical
decision dates, and comparing the capital that follows them.

THREE CURVES, per episode:
  A'  agent-follow  — the shadow book follows the reallocations the Worker
                      proposed AND the gates accepted;
  A   mechanical    — the same window, same dates, no Worker (`run_replay`);
  B   hold          — the initial defender, untouched. All Weather.
WHAT `A' - A` ACTUALLY MEASURES, which is NOT quite what MILESTONES wrote.
The milestone says the delta isolates the reallocation contribution because
"switches are mechanical in both arms". That was true before ADR-007. It is not
true now: the live cycle emits NO switch at all (writeback.py — "ADR-007
superseded the ranked defender/challenger duel, so no live cycle emits a
switch"), so A' contains none, while A still runs the bridge's mechanical switch
arm. The delta therefore compares THE COGNITIVE CYCLE AS IT WILL RUN against THE
MECHANICAL RULES AS THEY WERE MEASURED — two whole policies, not one isolated
term. That is still the comparison M8b needs, and pretending otherwise would put
a false precision on a 7-month number that is noise as much as signal.

WHICH BOOK A' TRACKS, and why it is the BRIDGE. Two books move in this system.
`ms-stack` is ADR-007's live allocation and ADR-011 makes it SOVEREIGN — gate 0
of `dispose_reallocation` refuses any cognitive reallocation aimed at it, so the
Worker cannot move it by construction and an A'/A comparison there would be zero
by definition, measuring nothing. The bridge defender (`4s-balanced-defender`)
is where the Worker may still reallocate, so it is where a delta can exist. The
market-signal decision still runs at every replayed date — not to feed A', but
because the Worker READS it, and a Worker deliberating on a `market_signal: {}`
baseline is not the Worker that will run live.

WHY EPISODES ARE SEPARATE RUNS (owner decision). The 21 dates fall in three
windows with an 11-year gap between the first two. One continuous curve would
freeze the book across those gaps, and a 14-year A' vs B would be dominated by
two frozen decades rather than by any decision. So each episode is its own
mini-replay with its own three curves. The horizon is short (7 months), so a
single delta is noise as much as signal — which is why M8b is a SCREEN, necessary
and not sufficient, and why the second channel (the behavioural log) carries
equal weight in its Definition of Verified.

SEMI-PIT, and the label is not a formality. The world the Worker reads is bounded
at t by `db/as_of_snapshot.py`, but the CORPUS is today's: the integrated
invariants were born in July 2026. That is deliberate (pruning them empties gate
6 and the screen measures nothing) and it makes this a BEST-CASE run — a
necessary a-priori screen, never go-live performance.
"""

import asyncio
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic_ai import Agent

from investment.db.as_of_snapshot import advance_as_of_snapshot, build_as_of_snapshot
from investment.db.sqlite import InvestmentDB
from investment.decision_cycle import UC8Result, run_decision_cycle
from investment.mechanical.as_of_cycle import AsOfCycle, run_as_of_cycle
from investment.mechanical.replay import (
    EPISODES,
    NavMetrics,
    ReplayInputs,
    ReplayThresholds,
    _book_calendar,
    decision_dates,
    nav_metrics,
    run_replay,
    shadow_book_nav,
)
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.worker.result import ImprovementProposal, WorkerResult

logger = logging.getLogger(__name__)

TRIGGER = "M8b agentic replay — the scheduled decision cycle at a historical date"

# TOTAL wall clock for one decision date — every attempt INSIDE it, not each.
#
# The distinction is the whole point and the first version got it wrong: a
# 15-minute budget PER attempt with 2 attempts is a 30-minute date, and 21 of
# those is the ten-hour run the bound was added to prevent. Bounding each part
# of a thing does not bound the thing.
#
# 900s against a healthy date's measured 5m09s. That is not "3x headroom for
# one cycle" — it is room for a first attempt to stall, be cut, and a retry to
# still complete inside the same budget. A date that has already burned twelve
# minutes gets the three that remain, which is correct: it has given ample
# evidence that it is not the healthy case.
#
# The number is provisional and should be re-derived from the FIRST full run's
# distribution of healthy-date durations, not from the single measurement it
# rests on today.
DATE_TIMEOUT_SECONDS = 900.0

# The other half of the same policy. See `_cycle_with_retry` for why one retry
# rather than a ladder, and why fail-fast without it loses dates that a single
# re-attempt recovers.
DATE_ATTEMPTS = 2


@dataclass(frozen=True)
class CognitiveAgents:
    """The three roles of one UC8 cycle, all bound to the SAME database handle."""

    planner_pre: PlannerPre
    worker: Agent[None, WorkerResult]
    planner_post: PlannerPost


@dataclass(frozen=True)
class DateOutcome:
    """One replayed decision date, for the behavioural log M8b reads as its
    second channel — "does the Worker reason sensibly?" is answered here, not by
    any NAV."""

    as_of: date
    mechanical: AsOfCycle
    reading: str
    proposed_allocation: dict[str, float] | None
    gate: str | None
    accepted: bool
    innovations: int
    # The innovations THEMSELVES, not just their count. M8b's Definition of
    # Verified asks "does it propose sensible improvements?" — a question no
    # integer can answer, and the report used to print only the integer. Same
    # reasoning as `reading`: this channel is judged by READING it.
    #
    # Held whole rather than as titles because they must OUTLIVE the run — see
    # `write_innovations`. The snapshot they were born in is deleted; the
    # proposals must not die with it.
    innovation_proposals: tuple[ImprovementProposal, ...] = ()
    # Set when the cognitive cycle RAISED on this date. Every other field then
    # carries the neutral value, and the date contributes no allocation — the
    # shadow book simply holds. See `run_agentic_episode` for why one bad date
    # does not end the episode.
    failure: str | None = None


@dataclass(frozen=True)
class EpisodeResult:
    name: str
    opens: date
    closes: date
    outcomes: list[DateOutcome]
    metrics_agentic: NavMetrics
    metrics_mechanical: NavMetrics
    metrics_hold: NavMetrics
    nav_agentic: pd.Series
    nav_mechanical: pd.Series
    nav_hold: pd.Series

    @property
    def accepted_reallocations(self) -> int:
        return sum(1 for o in self.outcomes if o.accepted)

    @property
    def failed_dates(self) -> int:
        """Dates whose cognitive cycle raised. Read this BEFORE the CAGRs: the
        NAV of an episode that lost three of seven decisions is a different
        claim from one that lost none, and nothing else in the metrics says so."""
        return sum(1 for o in self.outcomes if o.failure)

    @property
    def cagr_delta_vs_mechanical(self) -> float | None:
        """A' - A: the Worker's reallocation contribution, in CAGR points.
        `None` when either arm has too little data to have a CAGR at all — the
        honest answer, and one a caller must handle rather than read as zero."""
        agentic, mechanical = self.metrics_agentic.cagr, self.metrics_mechanical.cagr
        if agentic is None or mechanical is None:
            return None
        return agentic - mechanical

    @property
    def beats_all_weather(self) -> bool | None:
        """The STOP POINT's first question. `at all?` is the milestone's own
        wording — this is a screen, so the bar is a sign, not a margin."""
        agentic, hold = self.metrics_agentic.cagr, self.metrics_hold.cagr
        if agentic is None or hold is None:
            return None
        return agentic > hold


async def run_agentic_episode(
    live: Path,
    scratch: Path,
    *,
    name: str,
    opens: date,
    closes: date,
    inputs: ReplayInputs,
    thresholds: ReplayThresholds,
    make_agents: Callable[[InvestmentDB], CognitiveAgents],
    user_profile: dict[str, Any],
    system_thresholds: dict[str, float],
    cost_bps: float,
    confirmation_weeks: float,
) -> EpisodeResult:
    """One episode: walk its monthly decision dates with the real cognitive
    cycle, then price the book that followed it.

    ONE snapshot for the whole episode, advanced date by date — a fresh copy per
    date would replay every month as an opening entry
    (`advance_as_of_snapshot`). The snapshot is the Worker's whole world: it is
    handed to the Planner and to the Worker's tools in place of the live
    database, which is what bounds `db_query` — SQL the model writes itself, and
    the one read no parameter could ever bound.

    `make_agents` is a FACTORY rather than three ready agents because the
    Planner and the Worker's tools bind a database handle at construction
    (`PlannerPre(db, ...)`, `build_worker_agent(db, ...)`). Passing instances
    built against the live database would have handed the Worker the very
    connection the snapshot exists to replace — the leak would have been total
    and silent, since every query would still have answered.
    """
    calendar = _book_calendar(inputs)
    dates = decision_dates(calendar, opens, closes, "monthly")
    if not dates:
        raise ValueError(f"episode {name}: no decision date in {opens}..{closes}")

    initial = inputs.portfolios[inputs.initial_defender_id].allocation
    targets: dict[pd.Timestamp, dict[str, float]] = {dates[0]: dict(initial)}
    outcomes: list[DateOutcome] = []

    snapshot = scratch / f"agentic-{name}.db"
    snapshot.unlink(missing_ok=True)
    build_as_of_snapshot(live, snapshot, dates[0].date())

    # ONE handle for the whole episode, and the agents built against it once.
    # `advance_as_of_snapshot` writes to the same file through its own
    # connection; that is safe here because this one is idle at that moment and
    # never holds an open transaction across the await (ADR-004's single-writer
    # discipline is about concurrent writers, and there are none).
    db = InvestmentDB(snapshot)
    agents = make_agents(db)
    try:
        previous: date | None = None
        for stamp in dates:
            as_of = stamp.date()
            if previous is not None:
                advance_as_of_snapshot(snapshot, live, previous, as_of)
            previous = as_of

            mechanical = await run_as_of_cycle(db, as_of)
            # ONE DATE MAY FAIL WITHOUT ENDING THE EPISODE.
            #
            # This inverts CLAUDE.md's "unhandled errors surface", and the scope
            # of that rule is why: it governs the LIVE Monday chain, where
            # aborting is right because a half-run cycle would be written to the
            # graph and acted on. Nothing here is live — this is an offline
            # research harness whose output is a report, and its expensive part
            # is already spent by the time a late date fails. Losing six
            # completed cognitive readings because the seventh raised is the
            # worse failure, and it is the one that was in place.
            #
            # NOT swallowed: the date is recorded as FAILED with its error, it
            # appears in the report, and `_main` exits non-zero. What changes is
            # only whether the other dates survive it.
            #
            # AND IT IS BOUNDED BY A WALL CLOCK, which is the half that was
            # missing. Measured on the first real run (2026-08-06): a healthy
            # date completed in 5m09s, while one stalled connection held a date
            # for 55 MINUTES before failing and another hung 18 minutes inside a
            # single request — both under a 300s per-request timeout, because a
            # per-REQUEST timeout bounds a request, not a cycle, and the client's
            # own retries multiply it. At that rate 21 dates is a ten-hour run
            # made mostly of failures.
            #
            # The bound belongs HERE rather than in the transport: this is the
            # unit the harness actually cares about, and a wall clock around it
            # is immune to whatever the layers below do with their own timeouts.
            result = await _cycle_with_retry(
                db,
                agents,
                name=name,
                as_of=as_of,
                user_profile=user_profile,
                system_thresholds=system_thresholds,
            )
            if isinstance(result, BaseException):
                outcomes.append(_failed(as_of, mechanical, result))
                continue

            outcome = _record(as_of, mechanical, result)
            outcomes.append(outcome)
            if outcome.accepted and outcome.proposed_allocation:
                targets[stamp] = dict(outcome.proposed_allocation)
            logger.info(
                "m8b %s %s: gate=%s accepted=%s innovations=%d",
                name,
                as_of,
                outcome.gate or "n/a",
                outcome.accepted,
                outcome.innovations,
            )
    finally:
        await db.close()

    window = calendar[(calendar >= dates[0]) & (calendar <= pd.Timestamp(closes))]
    nav_agentic, _ = shadow_book_nav(targets, inputs.prices, inputs.rf, cost_bps, window)

    # Arm A and arm B from the SAME harness the go-live gate uses, over the same
    # window and the same dates — a hand-rolled mechanical arm here would be the
    # second decision loop Task 9.4 forbids.
    mechanical_run = run_replay(
        inputs,
        thresholds,
        start=dates[0].date(),
        end=closes,
        cost_bps=cost_bps,
        confirmation_weeks=confirmation_weeks,
        cadence="monthly",
    )

    # `run_replay` prices its book from the first decision date to the END OF THE
    # CALENDAR, not to `end` — invisible in M6, where the replay runs to the last
    # trading day anyway, and wrong here: arm B came back with 549 daily points
    # against the agentic arm's 123, so the three CAGRs were measured over different windows
    # and the delta was an artefact. All three curves are cut to the episode.
    nav_mechanical = mechanical_run.nav_agent_follow.loc[: pd.Timestamp(closes)]
    nav_hold = mechanical_run.nav_hold_defender.loc[: pd.Timestamp(closes)]

    return EpisodeResult(
        name=name,
        opens=opens,
        closes=closes,
        outcomes=outcomes,
        metrics_agentic=nav_metrics(nav_agentic, inputs.rf),
        metrics_mechanical=nav_metrics(nav_mechanical, inputs.rf),
        metrics_hold=nav_metrics(nav_hold, inputs.rf),
        nav_agentic=nav_agentic,
        nav_mechanical=nav_mechanical,
        nav_hold=nav_hold,
    )


def _record(as_of: date, mechanical: AsOfCycle, result: UC8Result) -> DateOutcome:
    """Flatten one cycle into the log line M8b's behavioural channel reads.

    `accepted` is deliberately NOT "the Worker proposed something": a proposal
    the gates refused moves no capital, and conflating the two would credit the
    Worker for reallocations Writeback threw out."""
    reallocation = result.worker_result.reallocation_proposed
    return DateOutcome(
        as_of=as_of,
        mechanical=mechanical,
        reading=result.worker_result.market_signal_assessment,
        proposed_allocation=dict(reallocation.proposed_allocation) if reallocation else None,
        gate=result.gate_outcome.failed_gate if result.gate_outcome else None,
        accepted=result.proposal_id is not None,
        innovations=len(result.worker_result.innovations_proposed),
        innovation_proposals=tuple(result.worker_result.innovations_proposed),
    )


async def _cycle_with_retry(
    db: InvestmentDB,
    agents: CognitiveAgents,
    *,
    name: str,
    as_of: date,
    user_profile: dict[str, Any],
    system_thresholds: dict[str, float],
) -> UC8Result | BaseException:
    """One date's cognitive cycle: FAIL FAST, THEN RETRY. The result, or the
    exception from the last attempt.

    The two halves are one policy and neither works alone. The wall clock alone
    turns a 55-minute hang into a 15-minute one and still LOSES the date; the
    retry alone would sit behind the same stall twice. Bounded-then-retried, a
    transient fault costs one attempt and the date still lands.

    The faults this exists for are transient by nature. On 2026-08-06 the
    MacBook's lid closed mid-run (ADR-002 — this machine sleeps, which is why
    the whole schedule is due-on-start rather than cron): every in-flight
    socket died, and dates failed on `ModelAPIError: Connection error` while
    the models themselves were answering 200 on either side of the gap. That
    is precisely the fault a retry is for, and precisely the one a bare
    fail-fast throws away.

    ONE retry, not a backoff ladder. The wall clock already spent 15 minutes
    proving this attempt is not coming back; a second failure means the problem
    outlives the retry (the lid is still shut, the key is wrong, the model is
    gone) and more attempts only spend the run's budget confirming it. The date
    is then recorded FAILED and the episode carries on."""
    last: BaseException = RuntimeError("no attempt ran")
    # ONE deadline for the date, shared by every attempt — see
    # `DATE_TIMEOUT_SECONDS`. A per-attempt clock multiplies by the retry count
    # and bounds nothing that matters.
    deadline = asyncio.get_running_loop().time() + DATE_TIMEOUT_SECONDS
    for attempt in range(1, DATE_ATTEMPTS + 1):
        try:
            async with asyncio.timeout_at(deadline):
                return await run_decision_cycle(
                    db,
                    agents.planner_pre,
                    agents.worker,
                    agents.planner_post,
                    trigger=TRIGGER,
                    user_profile=user_profile,
                    thresholds=system_thresholds,
                    today=as_of,
                    # The attempt is IN the run id: a retry writes its own
                    # journalled reading, and two rows from one date that
                    # cannot be told apart is a worse trace than none.
                    run_id=f"m8b-{name}-{as_of}-a{attempt}",
                )
        except Exception as exc:
            last = exc
            logger.warning(
                "m8b %s %s: attempt %d/%d failed (%s: %s)",
                name,
                as_of,
                attempt,
                DATE_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            if asyncio.get_running_loop().time() >= deadline:
                # The budget is the date's, so a retry that cannot start is not
                # attempted — pretending otherwise would log an attempt that
                # was cut before its first request.
                logger.error("m8b %s %s: date budget spent, no retry left", name, as_of)
                return last
    logger.error("m8b %s %s: all %d attempts failed, continuing", name, as_of, DATE_ATTEMPTS)
    return last


def _failed(as_of: date, mechanical: AsOfCycle, exc: BaseException) -> DateOutcome:
    """A date whose cognitive cycle raised. `accepted=False` and no allocation,
    so the shadow book holds through it exactly as it does through a date where
    the Worker proposed nothing — the arithmetic never sees a gap."""
    return DateOutcome(
        as_of=as_of,
        mechanical=mechanical,
        reading="",
        proposed_allocation=None,
        gate=None,
        accepted=False,
        innovations=0,
        failure=f"{type(exc).__name__}: {exc}",
    )


def render_report(episodes: list[EpisodeResult]) -> str:
    """The two channels M8b's STOP POINT is judged on, side by side. The NAV
    table alone would answer half the question — "does it beat All Weather?" —
    and MILESTONES weights "does the Worker reason sensibly?" equally, so the
    readings are printed in full rather than counted."""
    lines = ["M8b — AGENTIC REPLAY (semi-PIT, best-case; NOT go-live performance)", ""]
    for ep in episodes:
        lines += [
            f"## {ep.name}  {ep.opens} .. {ep.closes}   "
            f"({len(ep.outcomes)} decision dates, {ep.failed_dates} failed)",
            f"  A' agentic    cagr={_pct(ep.metrics_agentic.cagr)}  "
            f"sortino={_num(ep.metrics_agentic.sortino)}  "
            f"maxDD={_pct(ep.metrics_agentic.max_drawdown)}",
            f"  A  mechanical cagr={_pct(ep.metrics_mechanical.cagr)}  "
            f"sortino={_num(ep.metrics_mechanical.sortino)}  "
            f"maxDD={_pct(ep.metrics_mechanical.max_drawdown)}",
            f"  B  all-weather cagr={_pct(ep.metrics_hold.cagr)}  "
            f"sortino={_num(ep.metrics_hold.sortino)}  "
            f"maxDD={_pct(ep.metrics_hold.max_drawdown)}",
            f"  A' - A = {_pct(ep.cagr_delta_vs_mechanical)}   "
            f"beats all-weather: {ep.beats_all_weather}   "
            f"accepted reallocations: {ep.accepted_reallocations}",
            "",
            "  behavioural log:",
        ]
        for out in ep.outcomes:
            if out.failure:
                # Loud, and counted separately in the header: a date that never
                # ran is not a date on which the Worker chose to do nothing, and
                # a report that renders them alike would overstate the coverage
                # the NAV rests on.
                lines.append(f"    {out.as_of}  [!! FAILED]  {out.failure}")
                continue
            verdict = "accepted" if out.accepted else (out.gate or "no proposal")
            lines += [
                f"    {out.as_of}  [{verdict}]  innovations={out.innovations}",
                f"      {out.reading}",
            ]
            lines += [f"      innovation: {p.title}" for p in out.innovation_proposals]
        lines.append("")
    return "\n".join(lines)


def write_innovations(episodes: list[EpisodeResult], report: Path) -> Path:
    """Every innovation the Worker proposed, in full, next to the report.

    THE SNAPSHOT IS NOT A SINK. A replayed innovation reaches the same
    production path a live one does — `writeback/knowledge.py` gives it a
    `strategy` vertex at `status='proposed'`, which is the entrance to ADR-006's
    probation (`outcomes.strategy_probation_check`: 12 weeks, FAVORS percentile
    against the regime median, kept or closed mechanically). It then dies with
    the snapshot, because the snapshot is deleted. The machinery ran and its
    output was thrown away.

    Written rather than INJECTED into the live database, deliberately. These are
    proposed by a Worker reading 2008 through a 2026 corpus, so their motivation
    carries hindsight the live queue does not; and probation measures forward
    FAVORS from the date of birth, which for a replayed proposal is a date that
    has already happened. Auto-injecting would put 21 replays' worth of
    proposals into a queue whose clock cannot judge them.

    What survives instead is the SPEC — and a spec is portable. The first run
    produced "Extend the 200-day trend overlay to the IWN sleeve" carrying its
    evidence AND its own acceptance test (re-run the 35y backtest, adopt only if
    Sortino holds and maxDD improves). That is testable today, by hand or by a
    later job, and it is exactly what M8b's second channel exists to harvest.
    The owner decides which ones enter the live cycle as real innovations."""
    path = report.with_suffix(".innovations.json")
    harvest = [
        {
            "episode": ep.name,
            "as_of": out.as_of.isoformat(),
            "proposal": proposal.model_dump(mode="json"),
        }
        for ep in episodes
        for out in ep.outcomes
        for proposal in out.innovation_proposals
    ]
    path.write_text(json.dumps(harvest, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("harvested %d innovations to %s", len(harvest), path)
    return path


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.2f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


async def _run_all(scratch: Path, out: Path | None) -> list[EpisodeResult]:
    """Every episode, against the live database and the real models.

    The report is rewritten after EACH episode rather than once at the end. Two
    thirds of a paid run is worth keeping when the last third dies, and the sink
    has to exist before the expensive part starts, not after it."""
    from investment.config import Settings
    from investment.corpus.embedding import InProcessEmbedder
    from investment.mechanical.replay import load_inputs, load_thresholds
    from investment.worker.agent import build_worker_agent

    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings fills from .env
    live = Path(settings.db_path)

    db = InvestmentDB(live)
    inputs = await load_inputs(db)
    thresholds = await load_thresholds(db)
    rows = await db.query("SELECT key, value FROM system_thresholds")
    system_thresholds = {str(r["key"]): float(r["value"]) for r in rows}
    profile = await db.query(
        "SELECT max_drawdown_pct, max_single_asset_pct FROM user_profile LIMIT 1"
    )
    user_profile = dict(profile[0])
    await db.close()

    embedder = InProcessEmbedder(settings.embedding_model)

    def make_agents(snapshot_db: InvestmentDB) -> CognitiveAgents:
        return CognitiveAgents(
            planner_pre=PlannerPre(
                snapshot_db, embedder, settings.planner_model, settings.openrouter_api_key
            ),
            worker=build_worker_agent(
                snapshot_db, settings.worker_model, settings.openrouter_api_key
            ),
            planner_post=PlannerPost(settings.planner_model, settings.openrouter_api_key),
        )

    results: list[EpisodeResult] = []
    for name, opens, closes in EPISODES:
        # Episode-level equivalent of the per-date guard: an episode that dies
        # in its NAV arithmetic must not cost the episodes that have not run
        # yet. The already-written report stays on disk, and the next episode
        # rewrites it with its own results appended.
        try:
            results.append(
                await run_agentic_episode(
                    live,
                    scratch,
                    name=name,
                    opens=opens,
                    closes=closes,
                    inputs=inputs,
                    thresholds=thresholds,
                    make_agents=make_agents,
                    user_profile=user_profile,
                    system_thresholds=system_thresholds,
                    cost_bps=system_thresholds["replay_cost_bps"],
                    confirmation_weeks=system_thresholds["replay_confirmation_weeks"],
                )
            )
        except Exception:
            logger.exception("m8b episode %s failed, continuing to the next", name)
            continue
        if out is not None:
            out.write_text(render_report(results))
            # Harvested on the SAME cadence as the report, for the same reason:
            # two thirds of a paid run is worth keeping when the last third dies.
            write_innovations(results, out)
            logger.info("report written after %s: %s", name, out)
    return results


def _main() -> int:
    """`python -m investment.mechanical.agentic_replay --scratch DIR [--out FILE]`

    Spends real money: ~21 LLM decision cycles against the live database. With
    `--out` the report is rewritten after every episode, so an episode that dies
    on its last date does not take the two that already ran down with it.

    Exit code is 1 if ANY episode or decision date failed. The report is still
    written and still readable — the code exists so a partial run cannot be
    mistaken for a complete one by a caller that only checks the status."""
    import argparse

    parser = argparse.ArgumentParser(description="M8b agentic replay — the pre-go-live screen")
    parser.add_argument("--scratch", type=Path, required=True, help="where snapshots are built")
    parser.add_argument("--out", type=Path, help="write the report here as well as to stdout")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args.scratch.mkdir(parents=True, exist_ok=True)

    episodes = asyncio.run(_run_all(args.scratch, args.out))
    print(render_report(episodes))
    incomplete = len(episodes) < len(EPISODES) or any(ep.failed_dates for ep in episodes)
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(_main())
