"""The LIVE monthly allocation decision — ADR-007's adopted strategy, wired
(docs/V1_STRATEGY.md; docs/MILESTONES.md M6-bis "Remaining build: wire the live
monthly decision path").

One transaction, end to end:

    PIT inputs -> credit-spread/slope signal -> target book -> 200d overlay
    -> binding caps -> EventLog -> Proposal(proposal_type='market-signal')

and from there the existing machinery takes over unchanged: the digest renders
the proposal, `outcomes.evaluate_proposals` scores it at +12w.

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
in the weekly Monday chain unguarded.
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
    MA_WINDOW_DAYS,
    MEDIAN_WINDOW_DAYS,
    YIELD_SLOPE,
    Decision,
    persist_stack_nav,
    run_market_signal,
    stack_metrics,
)
from investment.writeback.writeback import MARKET_SIGNAL_EVENT, dispose_market_signal

logger = logging.getLogger(__name__)

# The stack's own strategy vertex (db/seed_data.py) — stamped on every decision
# so the journal says which strategy is speaking, not just which book won.
STRATEGY_ID = "market-signal-stack"

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
    yet — both normal, neither an error."""

    decision: Decision | None
    held_allocation: dict[str, float]
    gate_outcome: GateOutcome | None
    proposal_id: str | None
    skipped_reason: str | None = None

    @property
    def emitted(self) -> bool:
        return self.proposal_id is not None


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
    """What the stack holds coming into this decision: the allocation of the
    last market-signal Proposal actually EMITTED.

    Not the book Portfolio's static `allocation` row, which is the BASE book
    before the 200d overlay (db/seed_data.py says so explicitly) — holding SPY
    50 when the overlay redirected that sleeve into IEF would misreport the
    position and, downstream, misprice the incumbent leg of the +12w verdict.
    Empty before the first proposal: the stack holds nothing yet, so the first
    decision proposes the initial entry."""
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
    below their 200d MA, what was held and what is targeted. A reader six months
    from now must be able to reconstruct WHY this book, from this row alone,
    without re-running anything."""
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
            "window_days": MA_WINDOW_DAYS,
            "below_trend": list(decision.below_trend),
            "sleeves": {
                ticker: {
                    "price": read.price,
                    "moving_average": read.moving_average,
                    "below": read.below,
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
    returns without writing."""
    today = today or date.today()

    run = await run_market_signal(db, start=HISTORY_START, end=today, cost_bps=cost_bps)
    if not run.decisions:
        return MarketSignalCycleResult(None, {}, None, None, "no decision date in the window")

    # The NAV refresh happens BEFORE the monthly idempotency check, and the
    # order is load-bearing. The stack's NAV is a WEEKLY artefact like every
    # other portfolio's — it is what the ranking and the -25% drawdown alert
    # read — while the DECISION is monthly. Refreshing it after the early return
    # would leave it untouched on the ~3 Mondays a month that decide nothing, so
    # the alert built to catch stale data would itself have gone stale.
    await persist_stack_nav(db, run, window)

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
        "market-signal %s: signal=%s held=%s below-trend=%s gate=%s proposal=%s",
        decision_date,
        decision.signalled,
        decision.held,
        list(decision.below_trend),
        outcome.failed_gate or "passed",
        proposal_id or "-",
    )
    return MarketSignalCycleResult(decision, held, outcome, proposal_id)
