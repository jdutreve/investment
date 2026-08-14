"""Unified improvement cycle — the measuring arm (docs/ARCHITECTURE.md
"Unified improvement cycle" / "mechanical/outcomes.py"; docs/TASKS.md Phase 5
outcomes.py; docs/USE_CASES.md line 524). Weekly 08:52, after ranking, before
UC8.

Functions of the cycle:
- `evaluate_proposals()` — the VERDICT core. Each Proposal at `proposal_outcome_
  weeks` (12) is measured: the proposed allocation's synthetic-NAV return since
  `Proposal.date`, net of `replay_cost_bps x turnover`, vs the incumbent held.
  proposed > incumbent -> 'won'. The verdict lands as `Proposal.outcome` + an
  OutcomeEvent, and CONFRONTS the invariants the proposal cited (source=
  'proposal', via the proposal_cites relation / a switch's BACKED_BY).
- `strategy_probation_check()` — INNOVATION-born strategies (`status='proposed'`)
  judged on their FAVORS standing in the current regime at
  +strategy_probation_weeks, and the verdict APPLIED: 'keep' activates the vertex
  (status/enabled + its 3 Scenarios + BACKED_BY edges), 'review' closes it, and a
  candidate that never produced FAVORS at all is closed as unmeasurable once it
  has waited `UNMEASURABLE_PROBATION_MULTIPLIER` windows — nothing stays proposed
  forever (ADR-006). No human gate. The 4 seeded strategies never enter probation.
- `paper_test_progress()` — proposed-vs-incumbent to date for accepted
  paper-tests (read-only; feeds the digest scoreboard).

NOT BUILT — `score_scenarios()` (scenario calibration): SUPERSEDED by ADR-007
(docs/V1_STRATEGY.md "DEMOTED / superseded"): "Scenarios (bull/base/bear per
strategy) + scenario probabilities. Out of the decision; the credit-spread/slope
regime replaces the scenario read." Calibrating scenario probabilities would
score a mechanism the pivot took out of the live allocation decision (the
scenarios survive only as part of the retained Dalio bridge). Its live-path
analog is calibrating the MARKET-SIGNAL regime, not bull/base/bear — a job for
the market-signal stack (V1_STRATEGY roadmap Step 0-7), not this function.
"""

import dataclasses
import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from ulid import ULID

from investment.db.sqlite import InvestmentDB
from investment.mechanical import ratios
from investment.mechanical.gates import (
    ALLOCATION_SUM_TOLERANCE,
    allocation_well_formed,
    concentration_ok,
    effective_caps,
)
from investment.mechanical.invariants import compute_weight_update
from investment.mechanical.market_signal import STACK_PORTFOLIO_ID

CASH = ratios.CASH_TICKER
OUTCOME_EVENT = "OutcomeEvent"
# The 3 scenario names a strategy carries (docs/DATA_MODELS.md scenario.name);
# activation creates exactly these from the innovation spec.
_SCENARIO_NAMES = frozenset({"bull", "base", "bear"})
# The three probabilities are one distribution and must sum to 100
# (docs/ARCHITECTURE.md innovation spec). Same tolerance the knowledge commit
# applies to a Worker scenario update (writeback `_SCENARIO_SUM_TOLERANCE`).
_SCENARIO_SUM_TOLERANCE = 0.1

# How many probation windows a strategy may go WITHOUT any FAVORS before it is
# closed as unmeasurable. The backstop that makes ADR-006's "nothing stays
# proposed forever" true even for a candidate that can never be measured — see
# `strategy_probation_check`. A multiplier rather than a threshold of its own:
# the wait for evidence should track the measurement window, not drift from it.
UNMEASURABLE_PROBATION_MULTIPLIER = 2
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


def _as_float(value: Any, default: float) -> float:
    """A spec-supplied number, `default` when the field holds prose. Innovation
    specs are LLM-authored (writeback `_safe_float`, same rationale): one
    malformed scenario probability must not abort an activation."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
    per-order sum the cost model charges (mechanical/replay.py `shadow_book_nav`:
    "cost = Σ|Δweight| x bps", so a full switch Σ|Δ|=2.0 costs twice the rate —
    46 bps at ADR-010's 23 bps/order). Cash counts like any other sleeve."""
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
    db: InvestmentDB,
    fractions: Mapping[str, float],
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_bps: float,
) -> float | None:
    """The buy-and-hold synthetic-NAV return of `fractions` over [start, end],
    on the pinned NAV conventions (ratios.synthesize_nav: monthly rebalance,
    cash accrues at rf). `None` when the allocation cannot be valued — a
    missing price series, or the window not yet complete in the data (the same
    incomplete-forward-window guard maturation uses; without it the last
    proposals would be scored on a truncated window).

    `cost_bps` BILLS THE DRIFT-REBALANCES INSIDE THE WINDOW (ADR-010 point 2,
    closing docs/IMPROVEMENTS.md I-45). These NAVs are temporary — synthesized
    to answer one question and never persisted — and they were the last ones in
    the system trading for free, which made ADR-010's "every NAV pays" literally
    false and left the ADR carrying a correction note. The measured size is ~1.5
    bp over 12 weeks and it lands on BOTH legs, so no verdict moves; the reason
    to charge it anyway is that a rule with one silent exception is a rule the
    next reader has to rediscover. The caller passes
    `system_thresholds.replay_cost_bps`, which ADR-010 pins equal to
    `ratios.TRADING_COST_BPS` — the same rate the persisted NAVs pay."""
    non_cash = [t for t in fractions if t != CASH]
    prices = {t: await ratios.load_price(db, t) for t in non_cash}
    if any(p.empty for p in prices.values()):
        return None
    rf = await ratios.load_rf_daily(db)
    # `synthesize_nav` needs at least one PRICED sleeve — it builds its calendar
    # from the price frame and returns empty when there is none. An all-cash
    # allocation is not unvaluable though: it compounds at rf, which is exactly
    # what the engine's own cash leg does. Reachable because 'cash' is an
    # allowed reallocation ticker, so a fully defensive proposal is legal and
    # must still be scorable rather than skipped forever as "unvaluable". It
    # pays no rebalance either, and that is arithmetic rather than an exemption:
    # a lone sleeve cannot drift away from a 100% target, so sum|dW| is 0.
    nav = (
        (1.0 + rf).cumprod()
        if not non_cash
        else ratios.synthesize_nav(fractions, prices, rf, cost_bps=cost_bps)
    )
    if nav.empty or nav.index.max() < end:
        return None
    v_start, v_end = _asof(nav, start), _asof(nav, end)
    if v_start is None or v_end is None or v_start == 0.0:
        return None
    return v_end / v_start - 1.0


async def _incumbent_allocation(db: InvestmentDB, proposal: dict[str, Any]) -> dict[str, float]:
    """What was HELD when the proposal was made — the leg the proposed
    allocation must beat.

    Ranking-path proposals read the defender's weekly snapshot. A MARKET-SIGNAL
    proposal cannot: `defender_id` names the book Portfolio, whose snapshot
    carries the BASE allocation, while what the stack actually held is that book
    AFTER the trend overlay (db/seed_data.py: the overlay "is applied at DECISION
    time and is NOT reflected in these static rows"). Scoring against the base
    book would credit or blame the overlay for a position it had already moved
    out of — measuring a portfolio nobody held. The held allocation is therefore
    recorded on the proposal itself at commit (market_signal_cycle
    `build_market_context`) and read back from there.

    The stack's OPENING proposal has no incumbent. Owner decision (2026-08-02):
    it is scored against the BEST-RANKED portfolio at that date, not against
    cash. Cash would have been the easier bar and the wrong question — the
    owner's real alternative to entering the stack was to keep holding the best
    thing already available, so that is what the stack has to beat. Without any
    baseline the opening proposal could never be scored and would stay pending
    forever, which ADR-006 forbids."""
    if proposal["proposal_type"] != "market-signal":
        return await _allocation_at(db, str(proposal["defender_id"]), str(proposal["date"]))
    raw = proposal["market_context"]
    context = json.loads(raw) if isinstance(raw, str) else (raw or {})
    held = {str(k): float(v) for k, v in (context.get("held_allocation") or {}).items()}
    return held or await _best_ranked_allocation(db, str(proposal["date"]))


async def _best_ranked_allocation(db: InvestmentDB, as_of: str) -> dict[str, float]:
    """The rank-1 portfolio's allocation in the latest snapshot at or before
    `as_of` — the opening market-signal proposal's baseline.

    EXCLUDES the stack itself: once `ms-stack` is ranked alongside the books,
    it can be rank 1, and scoring the stack's opening move against the stack
    would compare it to itself and always draw. `{}` when no ranking exists yet,
    which the caller reports as an unmeasurable proposal rather than inventing
    a comparison."""
    rows = await db.query(
        "SELECT allocation FROM portfolio_weekly_snapshot "
        "WHERE date = (SELECT MAX(date) FROM portfolio_weekly_snapshot WHERE date <= :d) "
        "AND portfolio_id != :stack ORDER BY rank ASC LIMIT 1",
        d=as_of,
        stack=STACK_PORTFOLIO_ID,
    )
    if not rows:
        return {}
    parsed = json.loads(str(rows[0]["allocation"]))
    return {str(k): float(v) for k, v in parsed.items()}


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
    rule, FROM PROPOSALS): a SWITCH's are the challenger portfolio's BACKED_BY
    invariants (challenger -> holds -> strategy -> backed_by); every other kind
    reads the `proposal_cites` relation written at commit.

    The branch is keyed on `proposal_type == 'switch'`, not on `== 'reallocation'`
    — ADR-008 added a third type, and a market-signal proposal has a NULL
    `challenger_id`, so the old else-branch would have queried `holds` for
    `challenger_id IS NULL`, returned nothing, and silently skipped the
    confrontation instead of reading its (empty) citation set. Same answer today,
    since a market-signal decision cites nothing (writeback `market_signal_gates`),
    but for the wrong reason — and the wrong reason is what breaks when a fourth
    type arrives."""
    pid = str(proposal["id"])
    if proposal["proposal_type"] == "switch":
        rows = await db.query(
            "SELECT DISTINCT b.invariant_id FROM holds h "
            "JOIN backed_by b ON b.strategy_id = h.strategy_id WHERE h.portfolio_id = :c",
            c=str(proposal["challenger_id"]),
        )
    else:
        rows = await db.query(
            "SELECT invariant_id FROM proposal_cites WHERE proposal_id = :id", id=pid
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
    incumbent_alloc = await _incumbent_allocation(db, proposal)
    proposed_alloc = await _proposed_allocation(db, proposal)
    incumbent_frac, proposed_frac = normalize(incumbent_alloc), normalize(proposed_alloc)
    if not incumbent_frac or not proposed_frac:
        return ProposalOutcome(pid, "", None, None, "allocation missing or empty")

    incumbent_return = await _window_return(db, incumbent_frac, start, end, cost_bps)
    proposed_gross = await _window_return(db, proposed_frac, start, end, cost_bps)
    if incumbent_return is None or proposed_gross is None:
        return ProposalOutcome(pid, "", None, None, "price data does not cover the window")

    # The proposed side pays a one-time ENTRY cost for trading away from what is
    # already held; the incumbent is held, so it pays no entry. The rebalances
    # inside the window are a separate charge, billed to both legs by
    # `_window_return` — `proposed_gross` is gross of the switch only, not gross
    # of everything (ADR-010 point 2).
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


async def evaluate_proposals(db: InvestmentDB, today: date | None = None) -> list[ProposalOutcome]:
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


# -- strategy probation + paper-test tracking -------------------------------


@dataclasses.dataclass(frozen=True)
class ProbationResult:
    strategy_id: str
    verdict: str  # 'keep' (-> activated) | 'review' (-> closed)
    sortino: float | None
    median: float | None
    skipped_reason: str | None = None


async def _current_regime_type(db: InvestmentDB) -> str | None:
    rows = await db.query("SELECT regime_type_id FROM regime WHERE is_current = 1 LIMIT 1")
    return str(rows[0]["regime_type_id"]) if rows else None


async def _innovation_spec(db: InvestmentDB, strategy_id: str) -> dict[str, Any]:
    """The spec the InnovationEvent carried when this strategy was proposed
    (writeback `_commit_strategy_innovation`). Activation needs it to build the 3
    Scenario vertices and the BACKED_BY edges; `{}` when the event predates the
    payload carrying a spec — activation then flips the status alone."""
    rows = await db.query(
        "SELECT payload FROM event_log WHERE type = 'InnovationEvent' AND source_id = :sid "
        "ORDER BY id DESC LIMIT 1",
        sid=strategy_id,
    )
    if not rows:
        return {}
    payload = json.loads(str(rows[0]["payload"]))
    spec = payload.get("spec")
    return spec if isinstance(spec, dict) else {}


async def _scenario_spec_defect(db: InvestmentDB, spec: dict[str, Any]) -> str | None:
    """Why this innovation's scenarios may NOT be activated, or `None`.

    docs/ARCHITECTURE.md pins the contract the spec must already satisfy — "the
    3 bull/base/bear definitions ... target_allocation (sums to 100, complies
    with the binding user caps), initial probabilities (sum to 100)" — and
    nothing enforced it. Activation wrote whatever the LLM produced straight
    into `scenario`, so three probabilities of 90, a short leg or a NaN weight
    all persisted; `scenarios.py` and the reallocation blend then read them as
    fact. This is the same shape as `writeback._base_allocation`, which already
    guards the ONE scenario the candidate Portfolio is built from — the other
    two reached the graph unchecked.

    Deliberately a whole-spec verdict rather than a per-scenario skip: the three
    probabilities are one distribution, and dropping the malformed member of it
    would activate a strategy whose remaining scenarios sum to 60."""
    scenarios = spec.get("scenarios")
    if not isinstance(scenarios, list):
        return "spec carries no scenarios list"
    by_name: dict[str, dict[str, Any]] = {
        str(s.get("name")): s for s in scenarios if isinstance(s, dict)
    }
    if set(by_name) != _SCENARIO_NAMES:
        return f"scenarios are {sorted(by_name)}, expected {sorted(_SCENARIO_NAMES)}"

    probabilities = [_as_float(s.get("probability"), float("nan")) for s in by_name.values()]
    if any(not math.isfinite(p) or p < 0.0 for p in probabilities):
        return "a scenario probability is negative or not a number"
    if abs(sum(probabilities) - 100.0) > _SCENARIO_SUM_TOLERANCE:
        return f"scenario probabilities sum to {sum(probabilities):.1f}, not 100"

    caps = effective_caps(await _user_profile(db), None)
    allowed = await _allowed_tickers(db)
    for name, scenario in sorted(by_name.items()):
        raw = scenario.get("target_allocation")
        if not isinstance(raw, dict):
            return f"'{name}' has no target_allocation map"
        try:
            allocation = {str(k): float(v) for k, v in raw.items()}
        except (TypeError, ValueError):
            return f"'{name}' has a non-numeric weight"
        if not allocation_well_formed(allocation):
            return f"'{name}' is empty, or holds a negative/non-finite weight"
        if abs(sum(allocation.values()) - 100.0) > ALLOCATION_SUM_TOLERANCE:
            return f"'{name}' sums to {sum(allocation.values()):.1f}, not 100"
        if not concentration_ok(allocation, caps):
            return f"'{name}' breaches the {caps.max_single_asset_pct:.0f}% concentration cap"
        unknown = sorted(set(allocation) - allowed)
        if unknown:
            return f"'{name}' holds untradable {unknown}"
    return None


async def _user_profile(db: InvestmentDB) -> dict[str, Any]:
    rows = await db.query("SELECT max_single_asset_pct, max_drawdown_pct FROM user_profile LIMIT 1")
    if not rows:
        raise ValueError("no user_profile row: the binding caps are unknowable")
    return dict(rows[0])


async def _allowed_tickers(db: InvestmentDB) -> frozenset[str]:
    """Same set the reallocation gates use (writeback `_allowed_reallocation_
    tickers`): active tradable tickers plus the synthetic 'cash' sleeve."""
    rows = await db.query(
        "SELECT ticker FROM allowed_tickers WHERE active = 1 AND asset_class != 'MACRO'"
    )
    return frozenset({str(r["ticker"]) for r in rows} | {"cash"})


def _fallback_regime(
    all_favors: list[dict[str, Any]], strategy_id: str
) -> tuple[float, float, str] | None:
    """`(sortino, peer median, regime_type_id)` for the regime where this
    strategy's evidence actually lives, or `None` when it has none anywhere.

    THE REGIME IS CHOSEN BY EVIDENCE, not by recency or by score: the row with
    the most `n_periods`, ties broken on `regime_type_id` for reproducibility
    (the same content-independent tie-break `snapshots._valuation_rows` pins).
    Picking the BEST-scoring regime would let a strategy elect the flattering
    half of its own history; picking the most-measured one asks "where do we
    know the most about it".

    The median is that regime's OWN peer median. A Sortino earned in
    stagflation compared against a disinflation peer group would be a category
    error dressed as a verdict — the whole point of FAVORS being per-regime is
    that the comparison only means something inside one."""
    mine = [f for f in all_favors if str(f["strategy_id"]) == strategy_id]
    if not mine:
        return None
    best = max(mine, key=lambda f: (int(f["n_periods"] or 0), str(f["regime_type_id"])))
    regime = str(best["regime_type_id"])
    peers = sorted(
        float(f["sortino_rolling"])
        for f in all_favors
        if str(f["regime_type_id"]) == regime and f["sortino_rolling"] is not None
    )
    if not peers:
        return None
    return float(best["sortino_rolling"]), float(np.median(peers)), regime


async def _activate_strategy(db: InvestmentDB, strategy_id: str, today: date) -> None:
    """The activation transaction (docs/ARCHITECTURE.md "System Evolution",
    probation PASSES): in ONE transaction the vertex becomes `status='active',
    enabled=1`, its 3 Scenario vertices + HAS_SCENARIO edges are created from the
    innovation spec, BACKED_BY edges point at the invariants it cited, and a
    revision closes the vertex it supersedes (`date_revised` stamped, HOLDS edges
    NOT migrated — repointing a Portfolio stays a user action, UC9).

    Scenarios and edges are created HERE and not at proposal time on purpose: a
    proposed strategy is not part of the graph the weekly jobs read, so it must
    not carry scenarios that `scenarios.py` or the ranking could pick up before
    it earned activation. Called inside the caller's OutcomeEvent transaction."""
    spec = await _innovation_spec(db, strategy_id)
    now = datetime.now(UTC).isoformat()
    await db.command(
        "UPDATE strategy SET status = 'active', enabled = 1, updated_at = :now WHERE id = :id",
        now=now,
        id=strategy_id,
    )
    scenarios = spec.get("scenarios")
    for scenario in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(scenario, dict) or scenario.get("name") not in _SCENARIO_NAMES:
            continue
        await db.command(
            "INSERT OR IGNORE INTO scenario (id, strategy_id, name, probability, triggers, "
            "target_allocation, currency, trace, updated_at) VALUES (:id, :sid, :name, :prob, "
            ":trig, :alloc, :cur, :trace, :now)",
            id=str(scenario.get("id") or f"sc-{strategy_id}-{scenario['name']}"),
            sid=strategy_id,
            name=str(scenario["name"]),
            prob=_as_float(scenario.get("probability"), 0.0),
            trig=json.dumps(scenario.get("triggers", [])),
            alloc=json.dumps(scenario.get("target_allocation", {})),
            cur=str(scenario.get("currency", "USD")),
            trace=f"probation PASS {today.isoformat()}: activated with its parent strategy",
            now=now,
        )
    # `backed_by` is the DOCUMENTED spec key (docs/ARCHITECTURE.md "the proposal
    # spec must be complete": `backed_by : invariant ids (existing,
    # integrated)`). This read `cites`, a name that appears in no spec and no
    # prompt, so a doc-conforming innovation lost every BACKED_BY edge silently
    # — the strategy activated with no invariant behind it, and the
    # confrontation machinery had nothing to credit or blame.
    for invariant_id in spec.get("backed_by") or []:
        # FK-safe: an invariant the spec named but that does not exist is skipped
        # rather than aborting the activation (writeback `_resolve_fk` rationale).
        known = await db.query("SELECT 1 FROM invariant WHERE id = :id", id=str(invariant_id))
        if not known:
            continue
        await db.command(
            "INSERT OR IGNORE INTO backed_by (strategy_id, invariant_id, strength, added_at) "
            "VALUES (:sid, :iid, NULL, :now)",
            sid=strategy_id,
            iid=str(invariant_id),
            now=now,
        )
    supersedes = spec.get("supersedes")
    if supersedes:
        await db.command(
            "UPDATE strategy SET status = 'closed', enabled = 0, date_revised = :today, "
            "updated_at = :now WHERE id = :id AND id != :new",
            today=today.isoformat(),
            now=now,
            id=str(supersedes),
            new=strategy_id,
        )


async def _close_strategy(db: InvestmentDB, strategy_id: str, today: date, reason: str) -> None:
    """Probation FAILS → `status='closed'`, `enabled` stays 0, the reason appended
    to `trace` (docs/ARCHITECTURE.md). A superseded vertex stays active: the
    revision failed, so the incumbent was not displaced."""
    await db.command(
        "UPDATE strategy SET status = 'closed', enabled = 0, "
        "trace = trace || :suffix, updated_at = :now WHERE id = :id",
        suffix=f" | probation {today.isoformat()}: closed — {reason}",
        now=datetime.now(UTC).isoformat(),
        id=strategy_id,
    )


async def strategy_probation_check(
    db: InvestmentDB, today: date | None = None
) -> list[ProbationResult]:
    """Probation verdicts for INNOVATION-born strategies (docs/ARCHITECTURE.md
    "strategy_probation_check" + "System Evolution"). The 4 SEEDED strategies
    (source='corpus') are the baseline and never enter probation.

    A strategy born of an innovation is `status='proposed', enabled=0`
    (writeback `_commit_strategy_innovation`); `strategy_probation_weeks` after
    that birth it is judged on its FAVORS standing in the CURRENT regime type
    against the median of its peers, and the verdict is APPLIED mechanically
    (ADR-006 — "New strategies auto-enable after mechanical probation; no human
    gate", DECISIONS.md:338):
      'keep'   -> the activation transaction (`_activate_strategy`);
      'review' -> closed (`_close_strategy`).

    THREE outcomes, not two: a strategy with no FAVORS at all is closed as
    UNMEASURABLE once it has waited `UNMEASURABLE_PROBATION_MULTIPLIER` windows.
    Without that, the no-evidence branch wrote no OutcomeEvent and the strategy
    returned to `due` every week forever. It is measured through the candidate
    Portfolio created at birth (writeback `_commit_candidate_portfolio`), so the
    normal path is that FAVORS exist by the time the window closes; the backstop
    catches the specs that carried no usable base allocation.
    ARCHITECTURE's other mention of 'review' ("Telegram: propose closure, user
    decides") predates ADR-006; the mechanical path is the one ADR-006 pins, and
    the digest still REPORTS both outcomes.

    The window is anchored on `date_opened`, which `_commit_strategy_innovation`
    stamps in the same transaction as the InnovationEvent — so it IS the
    innovation date ARCHITECTURE anchors on, without a second lookup.

    Idempotent: a strategy that already has a probation OutcomeEvent is never
    re-judged, so the status transition happens exactly once."""
    today = today or date.today()
    thresholds = {
        r["key"]: r["value"] for r in await db.query("SELECT key, value FROM system_thresholds")
    }
    probation_weeks = int(thresholds["strategy_probation_weeks"])
    cutoff = (today - timedelta(weeks=probation_weeks)).isoformat()
    regime_type = await _current_regime_type(db)

    already = {
        str(r["source_id"])
        for r in await db.query(
            "SELECT source_id FROM event_log WHERE type = 'OutcomeEvent' "
            "AND json_extract(payload, '$.kind') = 'probation'"
        )
    }
    due = [
        str(row["id"])
        for row in await db.query(
            "SELECT id FROM strategy WHERE source = 'agent-discovery' AND status = 'proposed' "
            "AND date_opened <= :cutoff ORDER BY id",
            cutoff=cutoff,
        )
        if str(row["id"]) not in already
    ]
    if not due:
        return []
    if regime_type is None:
        # No regime, no FAVORS standing to judge against: the verdict WAITS. It
        # is not a failure — closing a strategy for want of a regime read would
        # punish the system's own blind spot.
        #
        # DELIBERATELY NOT subject to the unmeasurable backstop below, and the
        # asymmetry is the point: "no current regime" is system-wide and
        # REPAIRABLE — one detection run resolves every waiting strategy at once
        # — whereas a candidate with no FAVORS has a defect of its own that no
        # later run fixes. Bounding the first would close strategies for an
        # outage; bounding the second is what ADR-006 requires.
        return [ProbationResult(sid, "", None, None, "no current regime") for sid in due]

    # Every FAVORS row, not just the current regime's: a strategy is judged in
    # the current regime when it has been scored there, and OTHERWISE in the
    # regime where its evidence actually lives (`_fallback_regime`).
    #
    # I-52 CLOSED (entry removed from IMPROVEMENTS), and this is the rule change
    # it needed. The previous state had
    # a strategy scored elsewhere WAITING, unbounded — a regime that never
    # returns kept it proposed forever, which is what ADR-006 forbids, reached
    # from the other side. The alternative was to close it; that destroys real
    # evidence for a calendar accident. ADR-006 says "belief does not grant
    # integration, history does", and the history exists — it is simply filed
    # under another regime. So it is USED, against that regime's own peer median
    # (the only comparison that means anything: a Sortino earned in stagflation
    # is not comparable to a disinflation peer group), and the OutcomeEvent
    # records WHICH regime produced the verdict so no reader mistakes it for a
    # current-regime judgement.
    all_favors = await db.query(
        "SELECT regime_type_id, strategy_id, sortino_rolling, n_periods FROM favors "
        "WHERE sortino_rolling IS NOT NULL"
    )
    sortino_by = {
        str(f["strategy_id"]): f["sortino_rolling"]
        for f in all_favors
        if str(f["regime_type_id"]) == regime_type
    }
    peers = sorted(v for v in sortino_by.values() if v is not None)
    median = float(np.median(peers)) if peers else None
    unmeasurable_cutoff = (
        today - timedelta(weeks=UNMEASURABLE_PROBATION_MULTIPLIER * probation_weeks)
    ).isoformat()
    opened = {
        str(r["id"]): str(r["date_opened"])
        for r in await db.query("SELECT id, date_opened FROM strategy WHERE status = 'proposed'")
    }

    results: list[ProbationResult] = []
    for sid in due:
        sortino = sortino_by.get(sid)
        judged_in = regime_type
        if sortino is None or median is None:
            # Nothing to judge against IN THE CURRENT REGIME. Three outcomes,
            # and keeping them apart is the whole of I-52:
            #
            # 1. The strategy is scored in ANOTHER regime -> judge it there,
            #    against that regime's own peers (`_fallback_regime`). Its
            #    history exists; refusing to read it because the calendar has
            #    moved on would throw away the only evidence there is.
            # 2. No FAVORS anywhere, still inside the wait -> WAIT. A regime
            #    with no completed instances yet is the system's blind spot,
            #    not the strategy's fault.
            # 3. No FAVORS anywhere, past the wait -> CLOSE as unmeasurable. A
            #    candidate whose spec carried no usable base allocation, or
            #    whose sleeves have no prices, never gets a NAV and therefore
            #    never gets FAVORS (writeback `_base_allocation`). Waiting for
            #    it is waiting for something that cannot arrive, and this branch
            #    writes no OutcomeEvent — so before the backstop the strategy
            #    stayed in `due` every week forever, the exact "proposed
            #    forever" ADR-006 forbids.
            #
            # The bound on (2) is a MULTIPLE of the probation window rather than
            # a threshold of its own: how long we wait for evidence should move
            # with how long we measure, and one knob is one thing to calibrate.
            fallback = _fallback_regime(all_favors, sid)
            if fallback is not None:
                sortino, median, judged_in = fallback
            elif opened.get(sid, "") > unmeasurable_cutoff:
                results.append(
                    ProbationResult(sid, "", sortino, median, "no FAVORS in current regime")
                )
                continue
            else:
                reason = (
                    f"no FAVORS in any regime after "
                    f"{UNMEASURABLE_PROBATION_MULTIPLIER * probation_weeks} weeks — unmeasurable"
                )
                async with db.transaction():
                    await db.append_event(
                        type=OUTCOME_EVENT,
                        source_uc=SOURCE_UC,
                        source_id=sid,
                        payload={
                            "kind": "probation",
                            "verdict": "review",
                            "sortino": None,
                            "median": median,
                            "unmeasurable": True,
                        },
                        event_date=today,
                    )
                    await _close_strategy(db, sid, today, reason)
                # `skipped_reason` stays None: this result was NOT skipped, it is
                # a verdict. The "why" lives where it is auditable — the vertex
                # trace and the OutcomeEvent payload's `unmeasurable` flag.
                results.append(ProbationResult(sid, "review", sortino, median))
                continue
        # A 'keep' whose spec cannot be activated becomes a CLOSE, not a
        # half-activation: activation writes the scenarios into the graph that
        # `scenarios.py` and the reallocation blend read as fact, and a book
        # that is short a leg or sums to 60 is not a thing to hold. Closing
        # rather than leaving it proposed keeps ADR-006's "nothing stays
        # proposed forever"; the reason names the defect so a corrected version
        # can be re-proposed.
        #
        # Resolved BEFORE the append, not inside the branch below, because the
        # EventLog entry must carry the verdict that was ACTED ON — deciding
        # after the journal was written is how a log comes to say 'keep' over a
        # closed strategy.
        defect = None
        if sortino >= median:
            defect = await _scenario_spec_defect(db, await _innovation_spec(db, sid))
        verdict = "keep" if sortino >= median and defect is None else "review"
        elsewhere = "" if judged_in == regime_type else f" (judged in regime '{judged_in}')"
        reason = (
            f"malformed scenario spec — {defect}"
            if defect is not None
            else f"Sortino {sortino:.3f} below the peer median {median:.3f}{elsewhere}"
        )
        # EventLog append precedes the vertex writes, same transaction
        # (CLAUDE.md "EventLog"): the verdict and what it DID are one fact.
        async with db.transaction():
            await db.append_event(
                type=OUTCOME_EVENT,
                source_uc=SOURCE_UC,
                source_id=sid,
                payload={
                    "kind": "probation",
                    "verdict": verdict,
                    "sortino": sortino,
                    "median": median,
                    # Distinguishes "measured worse than its peers" from "measured
                    # well but unbuildable" — the same 'review' verdict, two
                    # entirely different follow-ups for the owner.
                    "spec_defect": defect,
                    # WHICH regime produced this verdict. Equal to the current
                    # one in the normal case; different when the strategy was
                    # judged on the regime its evidence lives in (I-52), and a
                    # reader must never mistake the second for the first.
                    "judged_in_regime": judged_in,
                },
                event_date=today,
            )
            if verdict == "keep":
                await _activate_strategy(db, sid, today)
            else:
                await _close_strategy(db, sid, today, reason)
        results.append(ProbationResult(sid, verdict, sortino, median))
    return results


async def paper_test_progress(db: InvestmentDB, today: date | None = None) -> list[dict[str, Any]]:
    """Proposed-vs-incumbent to date for every ACCEPTED paper-test still running
    (docs/ARCHITECTURE.md: "tracked EVERY week from paper_started"). Read-only —
    feeds the digest scoreboard (`digest.build_scoreboard`, which renders the
    running excess docs/TASKS.md Task 6bis.1 asks for); the +12w verdict is
    evaluate_proposals's job. Returns one row per live paper-test with the
    running excess (proposed minus incumbent since paper_started), or None where
    prices don't yet cover it."""
    today = today or date.today()
    rows = await db.query(
        "SELECT * FROM proposal WHERE paper_started IS NOT NULL "
        "AND (outcome IS NULL OR json_extract(outcome, '$.verdict') = 'pending')"
    )
    # The SAME rate the +12w verdict charges, for the same reason the incumbent
    # resolution is shared below: a running excess computed on cheaper NAVs than
    # the verdict's would be a scoreboard that contradicts the verdict it is
    # previewing.
    #
    # WITH A FALLBACK, unlike `evaluate_proposals`, and the difference is the
    # contract rather than an oversight. This function feeds the DIGEST, which is
    # a report and must render from whatever the database holds — including a
    # database that holds almost nothing (`test_build_digest_on_an_empty_db_says
    # _no_proposal`). `evaluate_proposals` decides a verdict, so a missing
    # threshold there is a broken seed and says so. Falling back cannot introduce
    # a second rate: ADR-010 pins `replay_cost_bps` EQUAL to
    # `ratios.TRADING_COST_BPS`, so the constant is the reference and the row is
    # its persisted copy — when the copy is absent, the reference is what the
    # verdict would have used anyway.
    rows_cost = await db.query("SELECT value FROM system_thresholds WHERE key = 'replay_cost_bps'")
    cost_bps = float(rows_cost[0]["value"]) if rows_cost else ratios.TRADING_COST_BPS
    end = pd.Timestamp(today)
    progress: list[dict[str, Any]] = []
    for proposal in rows:
        start = pd.Timestamp(date.fromisoformat(str(proposal["paper_started"])))
        # The SAME incumbent resolution the +12w verdict uses, deliberately.
        # Reading `_allocation_at(defender_id)` directly — as this did — is
        # exactly the error `_incumbent_allocation` was written to prevent, and
        # on the ADOPTED path it does not merely mis-price the incumbent, it
        # erases it: a market-signal proposal's `defender_id` is a BOOK
        # portfolio, the books are `enabled = 0` (ADR-009), and only enabled
        # portfolios get a weekly snapshot — so the lookup returned `{}` and
        # every live paper-test reported `excess: None` forever. The weekly
        # tracking and the verdict must also agree by construction: a running
        # excess computed against a different incumbent than the one that
        # decides won/lost is a scoreboard that contradicts its own verdict.
        incumbent = normalize(await _incumbent_allocation(db, proposal))
        proposed = normalize(await _proposed_allocation(db, proposal))
        inc_ret = await _window_return(db, incumbent, start, end, cost_bps) if incumbent else None
        pro_ret = await _window_return(db, proposed, start, end, cost_bps) if proposed else None
        excess = pro_ret - inc_ret if (inc_ret is not None and pro_ret is not None) else None
        progress.append(
            {
                "proposal_id": str(proposal["id"]),
                "proposed_return": pro_ret,
                "incumbent_return": inc_ret,
                "excess": excess,
            }
        )
    return progress
