"""Unified improvement cycle — the measuring arm (docs/ARCHITECTURE.md
"Unified improvement cycle" / "mechanical/outcomes.py"; docs/TASKS.md Phase 5
outcomes.py; docs/USE_CASES.md line 524). Weekly 08:52, after ranking, before
UC8.

M8 slice: `evaluate_proposals()` — the VERDICT core. Each Proposal that reaches
`proposal_outcome_weeks` (12) of age is measured: the proposed allocation's
synthetic-NAV return since `Proposal.date`, net of `replay_cost_bps x turnover`,
vs the incumbent (defender allocation as of that date) held. proposed > incumbent
→ 'won', else 'lost'. The verdict lands as `Proposal.outcome` + an OutcomeEvent
(kind=proposal), EventLog first (CLAUDE.md "EventLog" rule).

DEFERRED, with a real boundary — NOT a stub:
- **invariant confrontations source='proposal'** (the loop-closing step,
  ARCHITECTURE): a won/lost verdict should confirm/infirm the invariants the
  proposal cited. But the cited set is not machine-readable from a Proposal
  row — a reallocation cites its invariants only inside free-text `reasoning`
  (docs/DATA_MODELS.md Proposal: no structured cited-invariant column), and how
  Writeback persists them is a Phase-6 decision not yet made. This lands with
  the Writeback wiring, where the linkage is defined, not before.
- **paper-test tracking** (proposed-vs-incumbent to date from `paper_started`)
  and **score_scenarios / strategy_probation_check**: separate functions of the
  same cycle, following increments.
"""

import dataclasses
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
from ulid import ULID

from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios
from investment.mechanical.invariants import compute_weight_update

CASH = ratios.CASH_TICKER
OUTCOME_EVENT = "OutcomeEvent"
# The proposals being closed originate in UC8; the measurement is its own job
# but belongs to that use-case's loop (docs/USE_CASES.md UC8 / "Outcome
# evaluation").
SOURCE_UC = "UC8"


@dataclasses.dataclass(frozen=True)
class ProposalOutcome:
    """One evaluated proposal. `skipped_reason` is set (and verdict left empty)
    when the outcome window has not COMPLETED in the available price data — the
    proposal stays 'pending' and is retried next week, exactly as birth
    maturation defers an incomplete forward window."""

    proposal_id: str
    verdict: str  # 'won' | 'lost' | '' when skipped
    proposed_return: float | None
    incumbent_return: float | None
    skipped_reason: str | None = None


# -- pure core --------------------------------------------------------------


def normalize(allocation: Mapping[str, float]) -> dict[str, float]:
    """Percent (or any) weights → fractions summing to 1 (docs/DATA_MODELS.md
    'Units convention'; same rule as ratios._normalize_weights). Empty or
    all-zero allocation → {} (uninvestable — the caller treats it as
    unvaluable)."""
    total = sum(allocation.values())
    if total <= 0:
        return {}
    return {ticker: weight / total for ticker, weight in allocation.items()}


def turnover(incumbent_frac: Mapping[str, float], proposed_frac: Mapping[str, float]) -> float:
    """`Σ|Δweight|` over the union of tickers, in fractions — the UN-halved
    per-side sum the cost model charges (mechanical/replay.py `shadow_book_nav`:
    "cost = Σ|Δweight| x bps", a full switch Σ|Δ|=2.0 costs 20 bps at 10 bps/
    side). Cash counts like any other sleeve."""
    keys = set(incumbent_frac) | set(proposed_frac)
    return sum(abs(proposed_frac.get(k, 0.0) - incumbent_frac.get(k, 0.0)) for k in keys)


def verdict(proposed_return: float, incumbent_return: float) -> str:
    """'won' iff the proposed allocation beat the incumbent over the window
    (docs/ARCHITECTURE.md: "verdict: 'won' if proposed > incumbent else
    'lost'"). A tie is 'lost' — the burden of proof is on the challenger, the
    incumbent is not displaced by a draw."""
    return "won" if proposed_return > incumbent_return else "lost"


def _asof(nav: pd.Series, when: pd.Timestamp) -> float | None:
    """The NAV value as-of `when` (latest at or before). `None` if the series
    does not yet reach that date."""
    eligible = nav.index[nav.index <= when]
    if len(eligible) == 0:
        return None
    return float(nav.loc[eligible[-1]])


# -- async DB layer ---------------------------------------------------------


async def _allocation_at(db: InvestmentDB, portfolio_id: str, as_of: str) -> dict[str, float]:
    """A portfolio's allocation as of a date — the latest weekly snapshot at or
    before it (docs/ARCHITECTURE.md: "defender allocation as of Proposal.date").
    `{}` if the portfolio had no snapshot yet."""
    rows = await db.query(
        "SELECT allocation FROM portfolio_weekly_snapshot "
        "WHERE portfolio_id = :pid AND date <= :d ORDER BY date DESC LIMIT 1",
        pid=portfolio_id,
        d=as_of,
    )
    if not rows:
        return {}
    parsed = json.loads(rows[0]["allocation"])
    return {str(k): float(v) for k, v in parsed.items()}


async def _window_return(
    db: InvestmentDB, fractions: Mapping[str, float], start: pd.Timestamp, end: pd.Timestamp
) -> float | None:
    """The buy-and-hold synthetic-NAV return of `fractions` over [start, end],
    on the pinned NAV conventions (ratios.synthesize_nav: monthly rebalance,
    cash accrues at rf). `None` when the allocation cannot be valued — a
    missing price series, or the window not yet complete in the data (the same
    incomplete-forward-window guard maturation uses; without it the last
    proposals would be scored on a truncated window)."""
    non_cash = [t for t in fractions if t != CASH]
    prices = {t: await ratios.load_price(db, t) for t in non_cash}
    if any(p.empty for p in prices.values()):
        return None
    rf = await ratios.load_rf_daily(db)
    nav = ratios.synthesize_nav(fractions, prices, rf)
    if nav.empty or nav.index.max() < end:
        return None
    v_start, v_end = _asof(nav, start), _asof(nav, end)
    if v_start is None or v_end is None or v_start == 0.0:
        return None
    return v_end / v_start - 1.0


async def _proposed_allocation(db: InvestmentDB, proposal: dict[str, Any]) -> dict[str, float]:
    """What the proposal would hold (docs/ARCHITECTURE.md: "switch: challenger
    allocation; realloc: proposed_allocation"). A switch reads the challenger
    portfolio's allocation as of the proposal date; a reallocation carries its
    full target inline."""
    if proposal["proposal_type"] == "switch":
        return await _allocation_at(db, str(proposal["challenger_id"]), str(proposal["date"]))
    raw = proposal["proposed_allocation"]
    parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return {str(k): float(v) for k, v in parsed.items()}


async def _cited_invariants(db: InvestmentDB, proposal: dict[str, Any]) -> list[str]:
    """The invariants a Proposal leaned on (docs/ARCHITECTURE.md confrontation
    rule, FROM PROPOSALS): a reallocation's are the `proposal_cites` relation
    written at commit; a switch's are the challenger portfolio's BACKED_BY
    invariants (challenger -> holds -> strategy -> backed_by)."""
    pid = str(proposal["id"])
    if proposal["proposal_type"] == "reallocation":
        rows = await db.query(
            "SELECT invariant_id FROM proposal_cites WHERE proposal_id = :id", id=pid
        )
    else:
        rows = await db.query(
            "SELECT DISTINCT b.invariant_id FROM holds h "
            "JOIN backed_by b ON b.strategy_id = h.strategy_id WHERE h.portfolio_id = :c",
            c=str(proposal["challenger_id"]),
        )
    return [str(r["invariant_id"]) for r in rows]


async def _confront_cited(
    db: InvestmentDB, proposal: dict[str, Any], won: bool, half_life: float, today: date
) -> None:
    """source='proposal' confrontations (docs/ARCHITECTURE.md: "won -> confirmation
    for each qualifying cited invariant; lost -> infirmation"). Called inside
    `_evaluate_one`'s transaction. The reallocation's cited invariants were
    proven condition-ACTIVE by gate 6 at proposal time, so they qualify by
    construction; a per-window as-of re-check is a refinement (deferred).
    Weights move through the SAME compute_weight_update primitive as every other
    source."""
    pid = str(proposal["id"])
    cited = await _cited_invariants(db, proposal)
    if not cited:
        return
    verdict_tag = "confirmed" if won else "refuted"
    placeholders = ",".join(f":i{n}" for n in range(len(cited)))
    params = {f"i{n}": iid for n, iid in enumerate(cited)}
    rows = await db.query(
        "SELECT id, weight_initial, floor_weight, confirmation_count, infirmation_count "
        f"FROM invariant WHERE id IN ({placeholders})",
        **params,
    )
    now = datetime.now(UTC).isoformat()
    for row in rows:
        cc = int(row["confirmation_count"]) + (1 if won else 0)
        ic = int(row["infirmation_count"]) + (0 if won else 1)
        score, recency, w_eff = compute_weight_update(
            float(row["weight_initial"]), float(row["floor_weight"]), cc, ic, 0, half_life
        )
        await db.command(
            "INSERT INTO invariant_confrontations "
            "(id, invariant_id, moment_context, date, verdict, severity, source, source_id) "
            "VALUES (:id, :iid, :ctx, :date, :verdict, 1.0, 'proposal', :src)",
            id=str(ULID()),
            iid=str(row["id"]),
            ctx=f"proposal:{pid}",
            date=today.isoformat(),
            verdict=verdict_tag,
            src=pid,
        )
        await db.command(
            "UPDATE invariant SET confirmation_count = :cc, infirmation_count = :ic, "
            "market_score = :score, recency_factor = :recency, weight_effective = :weff, "
            "updated_at = :now WHERE id = :id",
            cc=cc,
            ic=ic,
            score=score,
            recency=recency,
            weff=w_eff,
            now=now,
            id=str(row["id"]),
        )


async def _evaluate_one(
    db: InvestmentDB,
    proposal: dict[str, Any],
    cost_bps: float,
    horizon: timedelta,
    half_life: float,
    today: date,
) -> ProposalOutcome:
    pid = str(proposal["id"])
    start_d = date.fromisoformat(str(proposal["date"]))
    end_d = start_d + horizon
    if end_d > today:
        return ProposalOutcome(pid, "", None, None, "outcome window not yet reached")

    start, end = pd.Timestamp(start_d), pd.Timestamp(end_d)
    incumbent_alloc = await _allocation_at(db, str(proposal["defender_id"]), str(proposal["date"]))
    proposed_alloc = await _proposed_allocation(db, proposal)
    incumbent_frac, proposed_frac = normalize(incumbent_alloc), normalize(proposed_alloc)
    if not incumbent_frac or not proposed_frac:
        return ProposalOutcome(pid, "", None, None, "allocation missing or empty")

    incumbent_return = await _window_return(db, incumbent_frac, start, end)
    proposed_gross = await _window_return(db, proposed_frac, start, end)
    if incumbent_return is None or proposed_gross is None:
        return ProposalOutcome(pid, "", None, None, "price data does not cover the window")

    # The proposed side pays a one-time entry cost for trading away from what is
    # already held; the incumbent is held, so it pays nothing.
    cost = turnover(incumbent_frac, proposed_frac) * cost_bps / 10_000.0
    proposed_return = proposed_gross - cost
    v = verdict(proposed_return, incumbent_return)

    outcome = {
        "proposed_return": proposed_return,
        "incumbent_return": incumbent_return,
        "verdict": v,
    }
    async with db.transaction():
        # EventLog append precedes the vertex write (CLAUDE.md "EventLog").
        await db.append_event(
            type=OUTCOME_EVENT,
            source_uc=SOURCE_UC,
            source_id=pid,
            payload={"kind": "proposal", **outcome},
            event_date=today,
        )
        await db.command(
            "UPDATE proposal SET outcome = :outcome, evaluated_at = :when WHERE id = :id",
            outcome=json.dumps(outcome),
            when=today.isoformat(),
            id=pid,
        )
        # Close the loop: confront the invariants the proposal cited (same txn).
        await _confront_cited(db, proposal, won=v == "won", half_life=half_life, today=today)
    return ProposalOutcome(pid, v, proposed_return, incumbent_return)


async def evaluate_proposals(
    db: InvestmentDB, today: date | None = None
) -> list[ProposalOutcome]:
    """Close every Proposal that has reached `proposal_outcome_weeks` and is
    still pending (docs/ARCHITECTURE.md "Unified improvement cycle"). Idempotent:
    a proposal whose verdict is already 'won'/'lost' is not re-read; one whose
    window has not completed stays 'pending' and is retried next week. Returns
    one `ProposalOutcome` per candidate examined (skips included, so the caller
    can log what deferred and why)."""
    today = today or date.today()
    thresholds = {
        r["key"]: r["value"] for r in await db.query("SELECT key, value FROM system_thresholds")
    }
    horizon = timedelta(weeks=int(thresholds["proposal_outcome_weeks"]))
    cost_bps = float(thresholds["replay_cost_bps"])
    half_life = float(thresholds["recency_half_life_days"])

    # Pending = NULL outcome (fresh) OR verdict still 'pending'. `json_extract`
    # on a NULL column returns NULL, so both are captured by the IS NULL / =
    # 'pending' pair without a separate branch.
    proposals = await db.query(
        "SELECT * FROM proposal "
        "WHERE outcome IS NULL OR json_extract(outcome, '$.verdict') = 'pending' "
        "ORDER BY date"
    )
    results = []
    for proposal in proposals:
        results.append(await _evaluate_one(db, proposal, cost_bps, horizon, half_life, today))
    return results
