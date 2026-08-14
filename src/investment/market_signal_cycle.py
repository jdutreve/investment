"""The LIVE monthly allocation decision — ADR-007's adopted strategy, wired
(docs/V1_STRATEGY.md; docs/MILESTONES.md M6-bis "Remaining build: wire the live
monthly decision path").

The monthly decision, end to end:

    PIT inputs -> credit-spread/slope signal -> target book -> trend overlay
    -> binding caps -> EventLog -> Proposal(proposal_type='market-signal')

and from there the existing machinery takes over unchanged: the digest renders
the proposal, `outcomes.evaluate_proposals` scores it at +12w.

WHAT IS AND IS NOT ATOMIC, stated because the arrow chain above invites the
wrong reading. The DISPOSITION — journal event, proposal event, Proposal vertex,
`ms-stack.allocation` — is one transaction, in `writeback.dispose_market_signal`.
The NAV refresh below it is NOT in that transaction and deliberately runs before
it (see `run_market_signal_cycle` on why it must happen on non-deciding weeks
too), so a disposition that then fails leaves the paper NAV a step ahead of the
position. That self-heals: the NAV is wholly DERIVED from market data and the
pure walk, and `persist_stack_nav` rebuilds it in full (INSERT OR REPLACE) on
every run. The digest and the +12w outcome are separate jobs entirely.

WHY THIS IS ITS OWN CYCLE, NOT PART OF UC8. UC8 is the cognitive chain (Planner
-> Worker -> Planner -> Writeback); its output is a JUDGEMENT. The market-signal
allocation is not a judgement — it is the mechanical readout of a signal
validated over 35 years, and letting an LLM alter it would break the anti-drift
guarantee `mechanical/market_signal.py` exists to hold. So the decision runs
mechanically here, and the Worker reads its result as context (decision_cycle.py
`render_context_for_worker`) to NUANCE it — exactly the division
docs/V1_STRATEGY.md Step 4 asks for ("the Worker nuances the monthly
regime/book decision"), and never to override it.

STATE. There is no market-signal state table, deliberately. Everything the next
decision needs is already persistent and append-only:
- the hysteresis state (held book, pending candidate, confirmation count) is
  RECOMPUTED by replaying `walk_decisions` over the whole history to `today` —
  the same walk the backtest runs, so live and replay cannot disagree;
- what the stack actually HOLDS is the last emitted market-signal Proposal;
- every decision, moving or not, is journalled as a `MarketSignalDecisionEvent`.
A state table would be a fourth copy of facts that already exist, and the one
that could silently drift from the other three.

KNOWN DEPENDENCY, stated because it will bite later. The trading calendar this
walks — and therefore the monthly decision dates and the signal alignment —
comes from `replay._book_calendar`, i.e. from the NAV index of the RETAINED
BRIDGE's defender portfolio. That is deliberate today: it is what keeps the live
clock bit-identical to the backtest's, and re-deriving the calendar from the
stack's own prices would move the pinned 11.26%/-23.8% and need re-validation.
But it means the ADOPTED strategy cannot run on a DB with no NAV-backfilled
Dalio defender, so retiring the bridge (docs/V1_STRATEGY.md Step 6) must first
give the stack its own calendar, re-validating the numbers in the same commit.

CADENCE. Monthly (docs/V1_STRATEGY.md "Why monthly"). The decision date is
`replay.decision_dates(..., 'monthly')` — the first trading day of the month,
the same clock the backtest stepped on — NOT "whenever the chain happens to
run". A chain that first runs mid-month therefore decides on that month's
anchor date, with the data knowable then; a second run in the same month is a
no-op (idempotent on the journalled decision date), which is what lets this sit
in the weekly weekly chain unguarded.
"""

import dataclasses
import json
import logging
from datetime import date
from typing import Any

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical.gates import GateOutcome
from investment.mechanical.market_signal import (
    BOOK_PORTFOLIO_IDS,
    CONFIRM_DECISIONS,
    COST_BPS,
    CREDIT_SPREAD,
    MA_WINDOWS,
    MEDIAN_WINDOW_DAYS,
    YIELD_SLOPE,
    Decision,
    DriftCheck,
    check_drift,
    load_series,
    persist_stack_nav,
    persist_trend_baseline_nav,
    run_market_signal,
    run_trend_baseline,
    stack_metrics,
)
from investment.writeback.writeback import (
    MARKET_SIGNAL_EVENT,
    SOURCE_UC,
    dispose_market_signal,
)

logger = logging.getLogger(__name__)

# The stack's own strategy vertex (db/seed_data.py) — stamped on every decision
# so the journal says which strategy is speaking, not just which book won.
STRATEGY_ID = "market-signal-stack"

# The ANTI-DRIFT verdict, journalled on every cycle run — declared here beside
# the only thing that appends it, as `decision_cycle.WORKER_READING_EVENT` is.
#
# APPENDED EVEN WHEN IT PASSES, and that is the point rather than noise: a check
# whose passes leave no trace cannot answer "did it run", which is exactly the
# silent-failure shape mechanical/alerts.py exists to name. The row is what lets
# the digest alert be a pure READ — build_digest renders committed rows and must
# never itself run a 35-year backtest (telegram/digest.py).
DRIFT_EVENT = "MarketSignalDriftEvent"

# 1991 is the backtest's start (35y of ALFRED first-release vintages via the
# HISTORY_PROXIES splice). The live walk starts there too: the hysteresis state
# and the 10y trailing medians are path-dependent, so a short window would not
# reproduce the book the backtest says is held today.
HISTORY_START = date(1991, 1, 1)

# The pinned 36M window (DATA_MODELS "Calculation conventions"), the one every
# `*_rolling` indicator uses — and, since ADR-009, the window the stack's
# drawdown rule is measured on. Defaulted rather than read from
# `system_thresholds` so the cycle needs no extra query; callers with the
# threshold loaded pass it.
ROLLING_WINDOW_DAYS = 756

# A stand-in for a series the run did not load, so `_knowable_at` answers None
# instead of the caller branching on absence at four call sites.
_EMPTY = pd.Series(dtype=float)


@dataclasses.dataclass(frozen=True)
class MarketSignalCycleResult:
    """One monthly decision's outcome. `skipped_reason` is set (everything else
    empty) when the month was already decided or the stack has no decision date
    yet — both normal, neither an error.

    `gate_outcome.passed` and `proposal_id` are INDEPENDENT: passed + no
    proposal is a holding month (the common case), refused + no proposal is a
    regression guard tripping (ADR-009: only a code or config change can do
    that). `emitted` and `blocked` name the two, so no caller has to re-derive
    the combination."""

    decision: Decision | None
    held_allocation: dict[str, float]
    gate_outcome: GateOutcome | None
    proposal_id: str | None
    skipped_reason: str | None = None

    @property
    def emitted(self) -> bool:
        return self.proposal_id is not None

    @property
    def blocked(self) -> bool:
        return self.gate_outcome is not None and not self.gate_outcome.passed


async def strategy_is_enabled(db: InvestmentDB) -> bool:
    """Whether the owner has left the stack switched on (`strategy.enabled`).

    THE COMMAND SAID "disabled" AND NOTHING STOPPED. `/disable
    market-signal-stack` writes `strategy.enabled = 0` and appends the
    UserDecisionEvent (ops/commands.py), and until this check existed no
    consumer on the live path ever read the column: the cycle kept deciding, the
    proposal kept reaching the digest, and the ranking filters
    `portfolio.enabled` — a different column on a different row. A control that
    reports success and changes nothing is worse than no control, because the
    owner stops looking.

    Enabling is a PREFERENCE and never a thesis (UC9), which is why this is
    honoured rather than argued with: the agent adjudicates theses, never the
    owner's rules.

    A MISSING ROW READS AS ENABLED. On a database seeded before the strategy
    vertex existed, the absence of a row is not a decision to switch anything
    off, and defaulting to "off" would silently stop the only live allocation
    path in the system."""
    rows = await db.query("SELECT enabled FROM strategy WHERE id = :id", id=STRATEGY_ID)
    return bool(rows[0]["enabled"]) if rows else True


async def last_decision_date(db: InvestmentDB) -> str | None:
    """The decision date of the most recent journalled decision — the
    idempotency key. Read off the EventLog, whose monotonic ULID id IS the
    append order (CLAUDE.md "EventLog"), so `ORDER BY id DESC LIMIT 1` is the
    latest by construction and needs no tie-break on `event_date`."""
    rows = await db.query(
        "SELECT payload FROM event_log WHERE type = :t ORDER BY id DESC LIMIT 1",
        t=MARKET_SIGNAL_EVENT,
    )
    if not rows:
        return None
    payload = json.loads(str(rows[0]["payload"]))
    decision_date = payload.get("decision_date")
    return str(decision_date) if decision_date else None


async def held_allocation(db: InvestmentDB) -> dict[str, float]:
    """The OWNER'S POSITION coming into this decision: the allocation of the last
    market-signal Proposal actually EMITTED.

    One of TWO things the codebase calls "held", and the distinction decides
    which one this function may read. `portfolio.allocation` on `ms-stack` is the
    BOOK IN FORCE — which book the strategy is in, never empty, read by the
    snapshot and the ranking. This is the other one: what the owner was actually
    told to buy. V1 executes nothing (ADR-006), so before the opening entry the
    owner holds NOTHING while the strategy has been in a book since 1991, and the
    two legitimately disagree. Reading the row here would compare the target to a
    position nobody has — and on a fresh DB whose target equals the seeded
    warm-up book, it would emit no proposal at all and the opening order, the
    entire deliverable of a paper-mode V1, would never reach the digest.

    Nor the BOOK Portfolios' `allocation` rows, for a third reason: those are the
    base books before the trend overlay (db/seed_data.py says so explicitly), so
    holding SPY 50 when the overlay redirected that sleeve into IEF would
    misprice the incumbent leg of the +12w verdict.

    Empty result = the opening entry, a case `outcomes._incumbent_allocation`
    handles deliberately (scored against the best-ranked portfolio at that
    date)."""
    rows = await db.query(
        "SELECT proposed_allocation FROM proposal WHERE proposal_type = 'market-signal' "
        "ORDER BY date DESC, created_at DESC LIMIT 1"
    )
    if not rows or rows[0]["proposed_allocation"] is None:
        return {}
    parsed = json.loads(str(rows[0]["proposed_allocation"]))
    return {str(k): float(v) for k, v in parsed.items()}


def _knowable_at(series: pd.Series, t: pd.Timestamp) -> str | None:
    """The date an input became KNOWABLE at decision time (ADR-003): the last
    date the RAW series carried an observation at or before `t`. The decision
    itself reads a forward-filled value, which is correct — a stale print is
    what was knowable — but that makes the age of the input invisible unless it
    is recorded here. A spread quoted from a 40-day-old print is a materially
    different decision from one quoted this morning, and only this field can
    tell the two apart after the fact."""
    if series.empty:
        return None
    eligible = series.index[series.index <= t]
    if len(eligible) == 0:
        return None
    return str(pd.Timestamp(eligible[-1]).date())


def build_market_context(
    decision: Decision,
    held: dict[str, float],
    raw_series: dict[str, pd.Series],
    stack_drawdown: float | None,
) -> dict[str, Any]:
    """The full decision record stamped on the Proposal (`market_context`) and
    on the journal event. This is the audit surface: every input, the date each
    became knowable, the signal state, the hysteresis position, the sleeves
    below their moving average, what was held and what is targeted. A reader
    six months from now must be able to reconstruct WHY this book, from this row
    alone, without re-running anything."""
    return {
        "strategy": STRATEGY_ID,
        "framework": "market-signal",
        "cadence": "monthly",
        "decision_date": str(decision.date.date()),
        "signal_state": decision.signalled,
        "held_book": decision.held,
        "held_book_portfolio_id": BOOK_PORTFOLIO_IDS[decision.held],
        "hysteresis": {
            "pending_book": decision.pending,
            "pending_count": decision.pending_count,
            "confirm_decisions": CONFIRM_DECISIONS,
        },
        "signals": {
            CREDIT_SPREAD: {
                "value": decision.spread,
                "trailing_median": decision.spread_median,
                "median_window_days": MEDIAN_WINDOW_DAYS,
                "knowable_at": _knowable_at(raw_series.get(CREDIT_SPREAD, _EMPTY), decision.date),
            },
            YIELD_SLOPE: {
                "value": decision.slope,
                "trailing_median": decision.slope_median,
                "median_window_days": MEDIAN_WINDOW_DAYS,
                "knowable_at": _knowable_at(raw_series.get(YIELD_SLOPE, _EMPTY), decision.date),
            },
        },
        "trend_overlay": {
            # A NEW FIELD RATHER THAN A WIDENED ONE. `window_days` was an int
            # for every row ever written; emitting "150/300" under that name
            # would have changed a committed field's TYPE in place, so a reader
            # would meet an int or a string depending on the row's age and
            # neither would be wrong to expect the other. Old rows keep their
            # int under the old name, new rows carry the list under the new one,
            # and readers below take whichever is present.
            "windows_days": list(MA_WINDOWS),
            "below_trend": list(decision.below_trend),
            "sleeves": {
                ticker: {
                    "price": read.price,
                    "moving_averages": list(read.moving_averages),
                    # The lines it is actually BELOW — see `TrendRead.breached`.
                    # A single "moving_average" here showed the SLOWEST line, so
                    # a half-out sleeve journalled price > moving_average beside
                    # below=True.
                    "breached": list(read.breached),
                    "share": read.share,
                    "below": read.below,
                    # WHY it is below, when the two numbers beside it do not say
                    # so. `SPREAD_STRESS_SLEEVE_GATE` marks a sleeve below with a
                    # price ABOVE its average, and this record is the audit
                    # surface — a reader six months out comparing price to MA
                    # would read that as a bug. `TrendRead` has carried the flag
                    # since the gate shipped; it reached no reader until now,
                    # which is the same defect as a journal that contradicts the
                    # decision it journals.
                    "credit_gated": read.credit_gated,
                    "knowable_at": _knowable_at(raw_series.get(ticker, _EMPTY), decision.date),
                }
                for ticker, read in decision.trend.items()
            },
        },
        "held_allocation": held,
        "target_allocation": decision.target,
        # NAMED IN FULL because two different drawdowns are in play and confusing
        # them would misread the -25% rule. This is the stack's WHOLE-WINDOW
        # (1991->now) max drawdown, recorded as provenance for the decision. The
        # rule itself binds the 36-MONTH ROLLING drawdown (ADR-009/010), which
        # lives in `portfolio_nav.drawdown` and is what mechanical/alerts.py
        # reads. Whole-window is monotone and would never recover; rolling is
        # the one that describes today.
        "stack_max_drawdown_full_window": stack_drawdown,
    }


async def journal_drift(db: InvestmentDB, today: date) -> DriftCheck:
    """Run the anti-drift check and append its verdict (`DRIFT_EVENT`).

    ADR-007's whole guarantee is that the wired stack reproduces the numbers it
    was signed on, and until 2026-08-12 nothing enforced it: the pair lived in a
    docstring, no test read it, and the paragraph stating the rule had itself
    drifted two supersessions behind without anything noticing. This is that
    guarantee given a mechanism — measured every week, journalled whatever it
    says, surfaced by `alerts.stack_drift_alert`.

    AN ALERT AND NEVER A BLOCK, like every other check on this path (ADR-009).
    Drift means one of two things — a rule changed without its pair being
    re-signed, or the data moved under a fixed marker (I-48) — and neither is
    something the cycle can resolve by refusing to decide. Aborting the chain
    would also cost the owner the digest that carries the explanation.

    Returns the verdict so a caller can act on it; the live cycle only journals
    it and lets the week's digest do the telling."""
    check = await check_drift(db)
    async with db.transaction():
        await db.append_event(
            type=DRIFT_EVENT,
            source_uc=SOURCE_UC,
            source_id=STRATEGY_ID,
            payload=check.as_payload(),
            event_date=today,
        )
    if check.drifted:
        # WARNING and not info: this is the one log line that says a validated
        # instrument no longer reproduces its validation.
        logger.warning("market-signal ANTI-DRIFT: %s", "; ".join(check.violations))
    elif not check.measurable:
        logger.info("market-signal anti-drift not measurable: %s", check.reason)
    return check


async def run_market_signal_cycle(
    db: InvestmentDB,
    user_profile: dict[str, Any],
    *,
    today: date | None = None,
    cost_bps: float = COST_BPS,
    window: int = ROLLING_WINDOW_DAYS,
) -> MarketSignalCycleResult:
    """Run one live monthly decision. Idempotent: a second call in the same
    month re-derives the same decision date, sees it already journalled, and
    returns without writing.

    A DISABLED STRATEGY STOPS EVERYTHING HERE, before the walk: no NAV refresh,
    no drift verdict, no decision, no proposal. Refreshing the NAV of a strategy
    the owner has switched off would keep the ranking and the drawdown alert
    speaking for it, which is the same lie in a quieter voice."""
    today = today or date.today()

    if not await strategy_is_enabled(db):
        logger.info("market-signal cycle skipped: %s is disabled by the owner", STRATEGY_ID)
        return MarketSignalCycleResult(None, {}, None, None, f"{STRATEGY_ID} is disabled")

    # ONE load, both arms. `load_series` reads the calendar, the risk-free curve
    # and every price and moving average exactly once, and hands the SAME frames
    # to the stack and to its control — so a difference between the two can never
    # be an artefact of one of them having loaded a slightly different world
    # (`replay._book_calendar`: "both arms share it, so A - B can never be an
    # artefact of a calendar difference").
    series = await load_series(db)
    run = await run_market_signal(
        db, start=HISTORY_START, end=today, cost_bps=cost_bps, series=series
    )
    if not run.decisions:
        return MarketSignalCycleResult(None, {}, None, None, "no decision date in the window")

    # The NAV refresh happens BEFORE the monthly idempotency check, and the
    # order is load-bearing. The stack's NAV is a WEEKLY artefact like every
    # other portfolio's — it is what the ranking and the -25% drawdown alert
    # read — while the DECISION is monthly. Refreshing it after the early return
    # would leave it untouched on the ~3 weekly runs a month that decide nothing, so
    # the alert built to catch stale data would itself have gone stale.
    await persist_stack_nav(db, run, window)
    # THE CONTROL ARM, refreshed on exactly the same weekly footing and for the
    # same reason (2026-08-13). It is the strategy minus its signal layer, and it
    # is priced here rather than in a study so the ranking compares the two every
    # week without anyone remembering to: the attribution that motivated it found
    # the signal worth +0.24pp of CAGR against this arm, so whether that margin
    # survives forward is THE question paper-mode has to answer, and a question
    # measured once in August 2026 is not one paper-mode can answer at all.
    await persist_trend_baseline_nav(
        db,
        await run_trend_baseline(
            db, start=HISTORY_START, end=today, cost_bps=cost_bps, series=series
        ),
        window,
    )
    # The anti-drift check, on the SAME weekly footing and BEFORE the monthly
    # early return, for the same reason the NAV refresh is: the question "does
    # the wired stack still reproduce the pair it was signed on" has nothing to
    # do with whether this month has already decided, and a check that only ran
    # on deciding weeks would go three weeks out of four without looking.
    await journal_drift(db, today)

    decision = run.decisions[-1]
    decision_date = str(decision.date.date())
    if await last_decision_date(db) == decision_date:
        return MarketSignalCycleResult(decision, {}, None, None, f"{decision_date} already decided")

    held = await held_allocation(db)
    stack_drawdown = (await stack_metrics(db, run)).max_drawdown
    context = build_market_context(decision, held, run.raw_series, stack_drawdown)

    outcome, proposal_id = await dispose_market_signal(
        db, decision, held, context, user_profile, today=today
    )
    logger.info(
        "market-signal %s: signal=%s held=%s below-trend=%s gate=%s outcome=%s",
        decision_date,
        decision.signalled,
        decision.held,
        list(decision.below_trend),
        outcome.failed_gate or "passed",
        # THREE outcomes, not two, and the log must not conflate them: a gate
        # refusal is a code/config bug (ADR-009), a hold is the strategy working
        # (~9 months a year), a proposal is an order for the owner.
        f"proposal {proposal_id}" if proposal_id else ("blocked" if not outcome.passed else "hold"),
    )
    return MarketSignalCycleResult(decision, held, outcome, proposal_id)
