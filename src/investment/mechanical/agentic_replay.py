"""The agentic replay — M8b's pre-go-live screen (docs/TASKS.md Task 9.4;
docs/MILESTONES.md M8b, a STOP POINT).

The mechanical replay (M6) answers "do the rules beat holding All Weather?".
This answers what M6 cannot: "is the cognitive half worth running?" — by
executing the real Planner, the real Worker and the real knowledge commit at
historical decision dates, and handing the owner what they said.

ONE CHANNEL, since ADR-012 (2026-08-09). This module used to price three
curves — A' following the reallocations the Worker proposed and the gates
accepted, A the mechanical rules, B All Weather held — and the whole apparatus
is gone with the cognitive allocation it measured. What it produces now is the
behavioural log: one market-signal READING per decision date, and the
innovations the cycle proposed.

That is not a lesser screen; it is the half MILESTONES always weighted equally,
and the half that paid. Across two days of runs the NAV channel returned
seven-month deltas that were noise as much as signal, while the readings
produced specific, code-checkable critiques of the mechanical rule — including
the one that found `max_single_asset_pct` freezing the stack in a stale book
through the whole 2022 drawdown. The runs that produced both are archived under
`~/data/investment/agentic-replay/`, and ADR-012 records what they showed.

WHY EPISODES ARE SEPARATE RUNS (owner decision). The 21 dates fall in three
windows with an 11-year gap between the first two, and each is its own walk
over its own snapshot. The reason survives the loss of the NAV: a snapshot
advanced across a decade would spend most of its dates in a world nobody is
asking about.

SEMI-PIT, and the label is not a formality. The world the Worker reads is
bounded at t by `db/as_of_snapshot.py`, but the CORPUS is today's: the
integrated invariants were born in July 2026. That is deliberate (pruning them
empties gate 6 and the screen measures nothing) and it makes this a BEST-CASE
run — a necessary a-priori screen, never go-live performance.
"""

import asyncio
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from investment.db.as_of_snapshot import advance_as_of_snapshot, build_as_of_snapshot
from investment.db.sqlite import InvestmentDB
from investment.decision_cycle import UC8Result, run_decision_cycle
from investment.mechanical.as_of_cycle import AsOfCycle, run_as_of_cycle
from investment.mechanical.replay import (
    EPISODES,
    ReplayInputs,
    _book_calendar,
    decision_dates,
)
from investment.planner.context import PlannerContext
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.worker.result import ImprovementProposal, WorkerResult

logger = logging.getLogger(__name__)

TRIGGER = "agentic replay — the scheduled decision cycle at a historical date"

# The run's artefacts on disk. NAMED FOR THE BEHAVIOUR, not for the milestone
# that first needed it: `m8b` is a line in MILESTONES.md that stops meaning
# anything the week it ships, while `agentic-replay` still says what is in the
# directory in a year. Same rule the modules follow (CLAUDE.md: reference the
# spec from the DOCSTRING, name the thing for what it does) — it just had not
# been applied to paths, log prefixes and run ids.
SCRATCH_DIR_NAME = "agentic-replay"

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
# RE-DERIVED from the first completed run (2026-08-06), as I-52 asked, and the
# owner's reading was right: 900s was cutting the dates worth reading.
#
# The three dates it lost were 2008-10-01 (Lehman), 2022-01-03 (the inflation
# shock settling in) and 2022-06-01 (peak inflation panic) — every one a hinge
# month, the richest context and the longest reading. A bound set from the
# median discards the tail, and the tail is what this screen exists to read.
#
# Successful dates, post transport fix: 206 · 341 · 348 · 378 · 406 · 408 · 423
# · 445 · 472 · 558 · 661 seconds. Median 408, tail 661. The lost ones were CUT
# at the ceiling, so how long they actually needed is unknown — which is the
# argument for headroom rather than for a tight fit. 1800s is 2.7x the observed
# tail: still a runaway guard, no longer a verdict on how long thinking may take.
DATE_TIMEOUT_SECONDS = 1800.0

# The other half of the same policy. See `_cycle_with_retry` for why one retry
# rather than a ladder, and why fail-fast without it loses dates that a single
# re-attempt recovers.
DATE_ATTEMPTS = 2

# A retry needs a MEANINGFUL share of the date's budget, not merely a non-zero
# one. At 2008-10-01 the second attempt inherited 91 seconds of 900 and timed
# out on schedule — the arithmetic was decided before it sent a request. A third
# of the budget is roughly what a healthy date has needed (9-14 min against 15),
# so below that a retry is a formality with a bill.
RETRY_MIN_BUDGET_FRACTION = 1 / 3


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
    """One episode's readings. NO NAV since ADR-012: the Worker does not
    allocate, so there is no A' to price and no A/B to price it against. What
    an episode produces is what M8b's Definition of Verified always weighted
    equally — a behavioural log to READ."""

    name: str
    opens: date
    closes: date
    outcomes: list[DateOutcome]

    @property
    def failed_dates(self) -> int:
        """Dates whose cognitive cycle raised. Read this before the log: an
        episode that lost three of seven decisions is a different claim from one
        that lost none, and nothing else here says so."""
        return sum(1 for o in self.outcomes if o.failure)

    @property
    def innovations(self) -> int:
        return sum(len(o.innovation_proposals) for o in self.outcomes)


async def run_agentic_episode(
    live: Path,
    scratch: Path,
    *,
    name: str,
    opens: date,
    closes: date,
    inputs: ReplayInputs,
    make_agents: Callable[[InvestmentDB], CognitiveAgents],
    user_profile: dict[str, Any],
    system_thresholds: dict[str, float],
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

    # RESUME. Each finished date was journalled to `<scratch>/<name>.dates.jsonl`
    # as it completed, and the snapshot on disk is already advanced to the last
    # of them — so a run interrupted at date 5 of 7 restarts at 6 rather than
    # paying for 1-5 again. Delete the scratch directory to force a clean run.
    #
    # The journal and the snapshot are ONE state: the file records dates whose
    # cycle ran against a snapshot that was advanced BEFORE the cycle, so the
    # last journalled date is exactly where the snapshot stands. Reading one
    # without the other would silently re-advance the clock past a date, which
    # no assertion downstream would catch.
    snapshot = scratch / f"agentic-{name}.db"
    journal = scratch / f"{name}.dates.jsonl"
    outcomes = _read_journal(journal, dates[0].date()) if snapshot.exists() else []
    if outcomes:
        logger.info(
            "agentic-replay %s: resuming after %s (%d dates kept)",
            name,
            outcomes[-1].as_of,
            len(outcomes),
        )
    else:
        snapshot.unlink(missing_ok=True)
        journal.unlink(missing_ok=True)
        build_as_of_snapshot(live, snapshot, dates[0].date())

    # ONE handle for the whole episode, and the agents built against it once.
    # `advance_as_of_snapshot` writes to the same file through its own
    # connection; that is safe here because this one is idle at that moment and
    # never holds an open transaction across the await (ADR-004's single-writer
    # discipline is about concurrent writers, and there are none).
    db = InvestmentDB(snapshot)
    agents = make_agents(db)
    try:
        done = {out.as_of for out in outcomes}
        previous: date | None = outcomes[-1].as_of if outcomes else None
        for stamp in dates:
            as_of = stamp.date()
            if as_of in done:
                continue
            if previous is not None:
                advance_as_of_snapshot(snapshot, live, previous, as_of)
            previous = as_of

            mechanical = await run_as_of_cycle(db, as_of)
            # ONE DATE MAY FAIL WITHOUT ENDING THE EPISODE.
            #
            # This inverts CLAUDE.md's "unhandled errors surface", and the scope
            # of that rule is why: it governs the LIVE weekly chain, where
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
                failed = _failed(as_of, mechanical, result)
                outcomes.append(failed)
                # Journalled like any other: a failed date is a date the resume
                # must NOT retry, or an interrupted run would grind on the same
                # broken date every time it restarts.
                _append_journal(journal, failed)
                continue

            outcome = _record(as_of, mechanical, result)
            outcomes.append(outcome)
            _append_journal(journal, outcome)
            logger.info("agentic-replay %s %s: innovations=%d", name, as_of, outcome.innovations)
    finally:
        await db.close()

    return EpisodeResult(name=name, opens=opens, closes=closes, outcomes=outcomes)


def _record(
    as_of: date,
    mechanical: AsOfCycle,
    result: UC8Result,
) -> DateOutcome:
    """Flatten one cycle into the behavioural log M8b reads.

    THE READING AND THE INNOVATIONS, and since ADR-012 nothing else: the Worker
    proposes no allocation, so there is no gate outcome to record and no
    acceptance to count."""
    return DateOutcome(
        as_of=as_of,
        mechanical=mechanical,
        reading=result.worker_result.market_signal_assessment,
        innovations=len(result.worker_result.innovations_proposed),
        innovation_proposals=tuple(result.worker_result.innovations_proposed),
    )


def _append_journal(path: Path, outcome: DateOutcome) -> None:
    """One finished date, appended as one JSON line, flushed immediately.

    APPEND rather than rewrite: the file is the run's memory of what has been
    paid for, and rewriting it puts every earlier date at risk of the write that
    is interrupted. One line per date also means a truncated last line costs one
    date on resume, not the file (`_read_journal` skips what it cannot parse)."""
    payload = {
        "as_of": outcome.as_of.isoformat(),
        "mechanical": asdict(outcome.mechanical),
        "reading": outcome.reading,
        "innovations": outcome.innovations,
        "innovation_proposals": [p.model_dump(mode="json") for p in outcome.innovation_proposals],
        "failure": outcome.failure,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def _read_journal(path: Path, opens: date) -> list[DateOutcome]:
    """The dates a previous run finished, in order. Missing file -> no resume.

    A line that will not parse is DROPPED, not raised on: the only way to get
    one is a run killed mid-write, and the correct response to a torn last line
    is to redo that one date. Refusing to start would make a crash cost the
    whole episode, which is the failure this file exists to prevent."""
    if not path.exists():
        return []
    outcomes: list[DateOutcome] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            raw = json.loads(line)
            outcomes.append(
                DateOutcome(
                    as_of=date.fromisoformat(raw["as_of"]),
                    mechanical=AsOfCycle(**{**raw["mechanical"], "as_of": opens}),
                    reading=raw["reading"],
                    innovations=raw["innovations"],
                    innovation_proposals=tuple(
                        ImprovementProposal.model_validate(p)
                        for p in raw.get("innovation_proposals", ())
                    ),
                    failure=raw["failure"],
                )
            )
        except (ValueError, KeyError, TypeError):
            logger.warning("agentic-replay: dropping an unreadable journal line in %s", path)
    return outcomes


async def _cycle_with_retry(
    db: InvestmentDB,
    agents: CognitiveAgents,
    *,
    name: str,
    as_of: date,
    user_profile: dict[str, Any],
    system_thresholds: dict[str, float],
) -> UC8Result | BaseException:
    """One date's cognitive cycle: FAIL FAST, THEN RETRY WHAT A RETRY CAN FIX.
    The result, or the exception from the last attempt.

    The two halves are one policy and neither works alone. The wall clock alone
    turns a 55-minute hang into a 15-minute one and still LOSES the date; the
    retry alone would sit behind the same stall twice. Bounded-then-retried, a
    transient fault costs one attempt and the date still lands.

    A RETRY IS FOR A TRANSIENT FAULT, AND ONLY FOR ONE. A dropped socket, a
    malformed response, a provider that hiccupped — re-ask and it lands. But a
    `TimeoutError` from the deadline below says THE BUDGET is what ran out, and
    re-running the same work inside the same budget cannot succeed; it is
    arithmetic, not luck. Measured 2026-08-06 at 2008-10-01: attempt 1 failed
    after 13m29 on a malformed response, attempt 2 inherited 91 seconds of a
    900-second budget and timed out. It contributed a log line.

    So a retry needs a MEANINGFUL share of the budget left, not merely a
    non-zero one (`RETRY_MIN_BUDGET_FRACTION`), and a timeout is never retried.

    "A timeout" MEANS THIS FUNCTION'S DEADLINE, and only since 2026-08-08 does
    the type say so unambiguously. A cut worker call raises
    `WorkerCallExhausted`, not `TimeoutError` (see `worker.agent`), because for
    one run it did raise `TimeoutError` and this clause read a 300-second call
    budget as its own 1800-second one: the date was abandoned with 15 minutes
    unspent. A call that keeps getting cut is a transient fault and belongs in
    the ordinary retry path below; only the deadline reached here is arithmetic.

    AND IT RE-RUNS ONLY WHAT FAILED. The Planner's pre-phase is two LLM calls
    over a snapshot that does not change between attempts, so on a Worker
    failure it would re-buy an identical answer with time the first attempt just
    proved to be short. The context is carried over instead
    (`run_decision_cycle(context=...)`), which also means the retry's Worker
    reads exactly what the first one read — a retry that quietly deliberated on
    a different context would not be a retry.

    ONE retry, not a backoff ladder: a second failure means the problem outlives
    the retry, and more attempts only spend the run's budget confirming it. The
    date is then recorded FAILED and the episode carries on."""
    last: BaseException = RuntimeError("no attempt ran")
    # ONE deadline for the date, shared by every attempt — see
    # `DATE_TIMEOUT_SECONDS`. A per-attempt clock multiplies by the retry count
    # and bounds nothing that matters.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DATE_TIMEOUT_SECONDS
    context: PlannerContext | None = None
    for attempt in range(1, DATE_ATTEMPTS + 1):
        try:
            async with asyncio.timeout_at(deadline):
                if context is None:
                    context = await agents.planner_pre.run(TRIGGER)
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
                    run_id=f"agentic-replay-{name}-{as_of}-a{attempt}",
                    context=context,
                )
        except TimeoutError as exc:
            # NOT retried, by the arithmetic above: the budget is what failed.
            logger.error(
                "agentic-replay %s %s: attempt %d cut by the %.0fs date budget",
                name,
                as_of,
                attempt,
                DATE_TIMEOUT_SECONDS,
            )
            return exc
        except Exception as exc:
            last = exc
            logger.warning(
                "agentic-replay %s %s: attempt %d/%d failed (%s: %s)",
                name,
                as_of,
                attempt,
                DATE_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            if loop.time() >= deadline - DATE_TIMEOUT_SECONDS * RETRY_MIN_BUDGET_FRACTION:
                # A retry that cannot plausibly finish is not started: it would
                # spend minutes to fail on the clock, and log an attempt that
                # never had a chance.
                logger.error("agentic-replay %s %s: too little budget left to retry", name, as_of)
                return last
    logger.error(
        "agentic-replay %s %s: all %d attempts failed, continuing", name, as_of, DATE_ATTEMPTS
    )
    return last


def _failed(
    as_of: date,
    mechanical: AsOfCycle,
    exc: BaseException,
) -> DateOutcome:
    """A date whose cognitive cycle raised. `accepted=False` and no allocation,
    so the shadow book holds through it exactly as it does through a date where
    the Worker proposed nothing — the arithmetic never sees a gap."""
    return DateOutcome(
        as_of=as_of,
        mechanical=mechanical,
        reading="",
        innovations=0,
        failure=f"{type(exc).__name__}: {exc}",
        # The rule targeted something this month whether or not the Worker
        # managed to read it — a failed date must still price the stack's leg,
        # or the on-stack walk would silently hold through it.
    )


def render_report(episodes: list[EpisodeResult]) -> str:
    """THE ONE CHANNEL LEFT, and the one MILESTONES always weighted equally.

    There is no NAV table since ADR-012: the Worker does not allocate, so no A'
    exists to compare against A or B. The question this report answers is the
    STOP POINT's other half — "is the reasoning sensible, are the improvements
    sensible?" — which no number was ever going to settle. The readings are
    printed in FULL rather than counted, because reading them is the test."""
    lines = ["M8b — AGENTIC REPLAY (semi-PIT, best-case; the behavioural channel)", ""]
    for ep in episodes:
        lines += [
            f"## {ep.name}  {ep.opens} .. {ep.closes}   "
            f"({len(ep.outcomes)} decision dates, {ep.failed_dates} failed, "
            f"{ep.innovations} innovations)",
            "",
            "  behavioural log:",
        ]
        for out in ep.outcomes:
            if out.failure:
                # Loud, and counted separately in the header: a date that never
                # ran is not a date on which the Worker had nothing to say, and
                # a report that rendered them alike would overstate the coverage.
                lines.append(f"    {out.as_of}  [!! FAILED]  {out.failure}")
                continue
            lines += [
                f"    {out.as_of}  innovations={out.innovations}",
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


async def _run_all(scratch: Path | None = None, out: Path | None = None) -> list[EpisodeResult]:
    """Every episode, against the live database and the real models.

    The report is rewritten after EACH episode rather than once at the end. Two
    thirds of a paid run is worth keeping when the last third dies, and the sink
    has to exist before the expensive part starts, not after it.

    BOTH PATHS DEFAULT TO SOMEWHERE DURABLE, and that is a correction. `scratch`
    started as "where the throwaway snapshots go", so the first runs pointed it
    at a session temp directory — correct for a 72MB snapshot, wrong the moment
    the resume journal moved in beside it. The journal is not scratch: it is the
    record of what a three-hour paid run has already bought, and it now lives
    next to the database it replays. `out` defaults for the same reason —
    a report written only when someone remembers the flag is a report that is
    missing on the run that mattered."""
    from investment.config import Settings
    from investment.corpus.embedding import InProcessEmbedder
    from investment.mechanical.replay import load_inputs
    from investment.worker.agent import build_worker_agent

    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings fills from .env
    live = Path(settings.db_path)
    # ONE SUBDIRECTORY PER ARM. The resume journal is keyed by episode name, so
    # two arms sharing a directory would each resume from the other's dates.
    scratch = scratch or live.parent / SCRATCH_DIR_NAME
    scratch.mkdir(parents=True, exist_ok=True)
    out = out or scratch / "report.txt"

    db = InvestmentDB(live)
    inputs = await load_inputs(db)
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
                    make_agents=make_agents,
                    user_profile=user_profile,
                    system_thresholds=system_thresholds,
                )
            )
        except Exception:
            logger.exception("agentic-replay episode %s failed, continuing to the next", name)
            continue
        if out is not None:
            out.write_text(render_report(results))
            # Harvested on the SAME cadence as the report, for the same reason:
            # two thirds of a paid run is worth keeping when the last third dies.
            write_innovations(results, out)
            logger.info("report written after %s: %s", name, out)
    return results


def _main() -> int:
    """`python -m investment.mechanical.agentic_replay [--scratch DIR] [--out FILE]`

    Spends real money: ~21 LLM decision cycles against the live database, three
    hours at the observed pace. BOTH FLAGS ARE OPTIONAL AND DEFAULT TO DURABLE
    PATHS under the database's directory — a run that persists nothing because
    a flag was forgotten is the failure this whole module has been rebuilt
    around today. The report is rewritten after every episode, and re-running
    the same command RESUMES from the journal rather than paying again.

    Exit code is 1 if ANY episode or decision date failed. The report is still
    written and still readable — the code exists so a partial run cannot be
    mistaken for a complete one by a caller that only checks the status."""
    import argparse

    parser = argparse.ArgumentParser(description="M8b agentic replay — the pre-go-live screen")
    parser.add_argument(
        "--scratch",
        type=Path,
        help="snapshots AND the resume journal (default: <db dir>/agentic-replay, durable)",
    )
    parser.add_argument(
        "--out", type=Path, help="report path (default: <scratch>/report.txt); always written"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    episodes = asyncio.run(_run_all(args.scratch, args.out))
    print(render_report(episodes))
    incomplete = len(episodes) < len(EPISODES) or any(ep.failed_dates for ep in episodes)
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(_main())
