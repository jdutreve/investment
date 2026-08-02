"""Weekly digest render (docs/TASKS.md Task 6bis.1; template in docs/EXAMPLE.md
Steps 8A/8B). Renders the Monday 09:30 digest as text — regime header, ranked
table with the defender starred, key invariants, the proposal block
(reallocation old->new or switch), the scoreboard, and the defender's returns.

PERCENT FORMATTING HAPPENS HERE ONLY (docs/TASKS.md Task 6bis.1): every other
layer keeps decimal fractions; the presentation edge is the single place a
0.038 becomes "+3.8%". Weights stay decimal (they are 0-1 fractions the owner
reads as weights, not percentages — matching the EXAMPLE template).
"""

import json
from typing import Any

from investment.db.sqlite import InvestmentDB
from investment.decision_cycle import WORKER_READING_EVENT
from investment.mechanical.alerts import Alert, collect_alerts
from investment.mechanical.market_signal import STACK_PORTFOLIO_ID
from investment.mechanical.outcomes import paper_test_progress
from investment.mechanical.snapshots import is_demoted
from investment.writeback.writeback import MARKET_SIGNAL_EVENT

# The invariants the digest lists: the heaviest lit lighthouses, not the whole
# corpus (docs/EXAMPLE.md Step 8A shows a handful).
DIGEST_INVARIANTS = 5


def pct(fraction: float | None, *, signed: bool = False) -> str:
    """A decimal fraction as a percentage string (0.038 -> '3.8%', or '+3.8%'
    signed). `None` -> 'n/a' — an unmeasured value is not zero."""
    if fraction is None:
        return "n/a"
    value = fraction * 100.0
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def _regime_header(regime: dict[str, Any], liquidity: dict[str, Any]) -> list[str]:
    # `confidence` is one of the documented exceptions to "every other layer
    # keeps decimal fractions": DATA_MODELS lists it with the `_pct`/`_rule`
    # fields and pins it 0-100, and `regime.compute_confidence` clamps to that
    # range. Running it through `pct` double-converted it — the live DB's 64.38
    # rendered as "6438.1%".
    confidence = regime.get("confidence")
    conf = f"{confidence:.1f}%" if isinstance(confidence, int | float) else str(confidence)
    lines = [
        f"📊 Regime: {regime.get('regime_name', '?')} "
        f"({conf} — {regime.get('regime_type_id', '?')})"
    ]
    if liquidity:
        # Through `_num` like every other indicator in this file: these are raw
        # REALs off `market_data`, and unformatted they print the full float
        # repr (the live DB's level renders as 95.83616874214097).
        lines.append(
            f"   Global liquidity: level {_num(liquidity.get('level'))}, "
            f"speed {_num(liquidity.get('speed'))}"
        )
    return lines


def _ranking_block(ranking: list[dict[str, Any]]) -> list[str]:
    """The ranked table, with the two markers of CLAUDE.md's "Ranking rule" —
    which are SEPARATE rules and must not be conflated into one warning:
    `calmar_rolling < 1.0` MOVES the row to the bottom, while a drawdown breach
    leaves it exactly where it is and only restricts what it is eligible for.

    They are also sourced differently, and deliberately: demotion is derived
    here through the ranker's own predicate (`snapshots.is_demoted`) because its
    input is on the row, while the exclusion is READ from the column the ranking
    job stamped — it was judged against the cap in force on that date, and
    re-deriving it would re-judge a past snapshot under today's cap
    (db/schema.py)."""
    lines = ["", "🏆 Portfolio ranking (Sortino USD, rolling 36M):"]
    for row in ranking:
        star = " ★ (defender)" if row.get("defender") else ""
        sortino = row.get("sortino_rolling")
        calmar = row.get("calmar_rolling")
        line = (
            f"   {row.get('rank')}. {row.get('portfolio_id')}: "
            f"{sortino:.2f}{star}  Calmar {calmar:.1f}"
            if isinstance(sortino, int | float) and isinstance(calmar, int | float)
            else f"   {row.get('rank')}. {row.get('portfolio_id')}{star}"
        )
        if isinstance(calmar, int | float) and is_demoted(calmar):
            line += f" ⚠️ (demoted: Calmar {calmar:.1f} below 1.0)"
        # NULL on rows written before the column existed reads as "not
        # recorded" and prints nothing — distinct from 0, "measured, compliant".
        if row.get("excluded_from_candidacy"):
            dd = row.get("max_drawdown")
            measured = f" (drawdown {pct(dd)})" if isinstance(dd, int | float) else ""
            line += f" ⛔ excluded from defender role and proposal candidacy{measured}"
        lines.append(line)
    return lines


def _invariant_block(invariants: list[dict[str, Any]]) -> list[str]:
    if not invariants:
        return []
    lines = ["", "🔑 Key Invariants (effective weight):"]
    for inv in invariants:
        weight = inv.get("weight_effective")
        weight_str = f"{weight:.3f}" if isinstance(weight, int | float) else "?"
        counts = (
            f" ({inv['confirmation_count']}/"
            f"{inv['confirmation_count'] + inv['infirmation_count']} confirmed)"
            if "confirmation_count" in inv and "infirmation_count" in inv
            else ""
        )
        author = f" [{inv.get('author') or 'system'}]"
        lines.append(f"   • {inv.get('title', '?')}: {weight_str}{counts}{author}")
    return lines


def _market_signal_block(
    decision: dict[str, Any] | None, worker_reading: dict[str, Any] | None = None
) -> list[str]:
    """ADR-007's live monthly decision, rendered from its JOURNAL entry
    (`MarketSignalDecisionEvent`, writeback `dispose_market_signal`), not from a
    Proposal row.

    WHY THE JOURNAL. The adopted strategy decides every month but emits a
    Proposal only on the ~3 months a year it moves, and the bridge's UC8 can
    emit a reallocation on the same Monday. Reading this block off
    `_latest_proposal` therefore lost the decision twice over: on every holding
    month (no proposal exists) and — worse — on a moving month, where the 09:00
    reallocation has the later `created_at` and won the single ORDER BY slot.
    The months the digest dropped were exactly the months the owner had an order
    to place. The journal has one row per decision date, moved or not, blocked or
    not, so this block is now unconditional and the proposal slot below stays the
    bridge's.

    Shows the two signal comparisons and the overlay reads, not just the
    resulting book: the owner places these orders by hand and is entitled to see
    what moved the money. Falls back gracefully on a thin payload — a digest must
    never be the thing that fails on Monday morning."""
    if not decision:
        return []
    signals = decision.get("signals") or {}
    held = decision.get("held_allocation") or {}
    target = decision.get("target_allocation") or {}
    moves = [
        f"{t} {held.get(t, 0):g}→{target.get(t, 0):g}"
        for t in sorted(set(held) | set(target))
        if held.get(t, 0) != target.get(t, 0)
    ]
    # `held_book` is the book the HYSTERESIS has committed to, which is what the
    # stack holds on every reachable month — except a blocked one, where the
    # move never happened and the stack is still in the previous book. Saying
    # "book X" there would be the same error the Worker context carried until
    # ADR-011's pass: naming a target as though it were a position. So the
    # header calls it the TARGET whenever the gate refused.
    gate = decision.get("gate")
    blocked = bool(gate) and gate != "passed"
    label = "target book" if blocked else "book"
    lines = [
        "",
        f"🧭 Market-signal decision (paper-test) — {label} "
        f"{decision.get('held_book', '?')}, decided {decision.get('decision_date', '?')}:",
        "   " + (" | ".join(moves) if moves else "no change — the stack holds its book"),
    ]
    # A refused decision writes no Proposal (ADR-009: the gates are regression
    # guards, so this means a config or code change, never a market event). It
    # must be LOUD rather than absent — otherwise it renders identically to a
    # month that legitimately did not move.
    if blocked:
        lines.append(
            f"   🚨 BLOCKED by gate '{gate}' — nothing was proposed. The stack is FROZEN in its "
            "previous book, not in the one named above."
        )
    for ticker, read in signals.items():
        median = read.get("trailing_median")
        lines.append(
            f"   {ticker} {_g(read.get('value'))} vs 10y median {_g(median)}"
            f" (knowable {read.get('knowable_at', '?')})"
        )
    overlay = decision.get("trend_overlay") or {}
    below = overlay.get("below_trend") or []
    lines.append(
        f"   200d overlay: {', '.join(below)} below trend" if below else "   200d overlay: clear"
    )
    hysteresis = decision.get("hysteresis") or {}
    if hysteresis.get("pending_book"):
        lines.append(
            f"   Pending switch to {hysteresis['pending_book']}: "
            f"{hysteresis.get('pending_count')}/{hysteresis.get('confirm_decisions')} confirmations"
        )
    if decision.get("reasoning"):
        lines.append(f"   Why: {decision['reasoning']}")
    lines.extend(_worker_challenge_lines(worker_reading, decision.get("decision_date")))
    return lines


def _worker_challenge_lines(reading: dict[str, Any] | None, decision_date: Any) -> list[str]:
    """The Worker's reading of the mechanical decision (ADR-011's "journalled and
    RENDERED" half), from the latest `WorkerReadingEvent`.

    Rendered INSIDE the market-signal block, not as a section of its own: it is a
    critique OF this decision, and separating the two would print an opinion with
    its subject three blocks away. It follows the mechanical `Why:` deliberately
    — the owner reads what the instrument did, then what the Worker makes of it.

    STALENESS IS SHOWN, NEVER SILENTLY DROPPED. The decision is monthly and the
    Worker runs weekly, so the normal case is several readings of the same
    anchor; but if the cognitive cycle fails or is skipped, the latest reading
    can belong to a PREVIOUS month's decision — an opinion on a book that is no
    longer in force. Hiding it would recreate, one field over, exactly the
    disappearance this feature exists to fix, so it is printed with its own
    date attached.

    Being inside the block also means no decision, no challenge: before the first
    mechanical decision the block is absent entirely, and the reading it would
    carry is the Worker saying it had none to read."""
    if not reading:
        return []
    assessment = str(reading.get("market_signal_assessment") or "").strip()
    if not assessment:
        return []
    read_date = reading.get("market_signal_decision_date")
    stale = bool(decision_date) and bool(read_date) and read_date != decision_date
    suffix = f" (reading of the {read_date} decision — NOT the one above)" if stale else ""
    return [f"   🗣 Worker challenge{suffix}: {assessment}"]


def _stack_block(stack: dict[str, Any] | None) -> list[str]:
    """The stack's own standing — shown every week, proposal or not. The
    36-month drawdown here is the number the -25% rule is about (ADR-009); the
    alert at the top of the digest fires off this same column, so a reader can
    always see how close it is rather than only hearing when it breaks.

    Labelled PAPER, and the label is not modesty: `ms-stack`'s `portfolio_nav`
    is built by `shadow_book_nav` from the decision walk, so it assumes every
    monthly decision filled at the close of its anchor date. It is what the
    strategy would have done, printed beside portfolios measured the same way —
    never a statement about the owner's account (V1 executes nothing, ADR-006).
    'drawdown 36M' is the DEEPEST drawdown inside the trailing 756 days, not
    today's distance from a high (mechanical/alerts.py states the same)."""
    if not stack:
        return []
    # Every indicator is formatted defensively, as everywhere else in this file:
    # `calmar_rolling` is cagr/|max_drawdown| and goes NULL when the window holds
    # no drawdown at all, and the rolling indicators are NULL for the first rows
    # of any series. A digest that raises on Monday morning is worse than one
    # that prints 'n/a'.
    return [
        "",
        f"🧱 Market-signal stack (paper): Sortino {_num(stack.get('sortino_rolling'))} | "
        f"Calmar {_num(stack.get('calmar_rolling'))} | "
        f"deepest drawdown 36M {pct(stack.get('drawdown'))}",
    ]


def _num(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, int | float) else "n/a"


def _g(value: Any) -> str:
    """A signal level for the digest. These are rates in percent points (a
    BAA10Y of 2.14 means 2.14 points), NOT the decimal fractions `pct` converts
    — passing them through `pct` would print a credit spread of 214%."""
    return "n/a" if not isinstance(value, int | float) else f"{value:.2f}"


def _proposal_block(proposal: dict[str, Any] | None) -> list[str]:
    """The BRIDGE's proposal slot (switch / reallocation). Market-signal
    proposals are deliberately not routed here — they are rendered by
    `_market_signal_block` off their journal, so the two paths cannot compete
    for one slot."""
    if proposal is None:
        return ["", "🟢 No bridge proposal this week — maintain."]
    if proposal.get("proposal_type") == "reallocation":
        current = proposal.get("current_allocation", {})
        proposed = proposal.get("proposed_allocation", {})
        moves = [
            f"{t} {current.get(t, 0):g}→{proposed.get(t, 0):g}"
            for t in sorted(set(current) | set(proposed))
            if current.get(t, 0) != proposed.get(t, 0)
        ]
        lines = [
            "",
            "🔧 Reallocation proposal (paper-test) — defender stays, allocation tilts:",
            "   " + " | ".join(moves),
        ]
    else:
        lines = [
            "",
            f"🔀 Switch proposal ({proposal.get('recommendation', 'monitor')}): "
            f"{proposal.get('challenger_id')} over {proposal.get('defender_id')}",
        ]
    lines.append(f"   Why: {proposal.get('reasoning', '')}")
    return lines


def _alert_block(alerts: list[Alert]) -> list[str]:
    """Health alerts FIRST in the digest, before the regime header — the two
    freshness alarms mean the numbers below them may be describing a world that
    no longer exists, so they cannot sit at the bottom where a skimmed digest
    would miss them (mechanical/alerts.py)."""
    if not alerts:
        return []
    icon = {"critical": "🚨", "warn": "⚠️"}
    return [f"{icon.get(a.level, '⚠️')} {a.message}" for a in alerts] + [""]


def _scoreboard_block(scoreboard: dict[str, Any]) -> list[str]:
    won, total = scoreboard.get("hit_rate", (0, 0))
    rate = pct(won / total) if total else "n/a"
    lines = ["", "📋 Scoreboard:", f"   Proposals hit-rate: {won}/{total} ({rate}) at +12w"]
    paper = scoreboard.get("paper_tests", [])
    if paper:
        lines.append(f"   Paper-tests in progress: {len(paper)}")
        # "paper-tests in progress WITH PROPOSED-VS-INCUMBENT TO DATE"
        # (docs/TASKS.md Task 6bis.1). The count alone answered "how many", never
        # "are they working" — and the running excess is the only thing in the
        # digest that says whether a live paper-test is ahead before its +12w
        # verdict lands. 'n/a' where prices do not yet cover the window, never 0.
        for test in paper:
            lines.append(
                f"      {test.get('proposal_id')}: "
                f"{pct(test.get('excess'), signed=True)} vs incumbent since paper_started"
            )
    if scoreboard.get("probations"):
        lines.append(f"   Strategies in probation: {len(scoreboard['probations'])}")
    if scoreboard.get("calibration_flags"):
        lines.append(f"   Scenario calibration flags: {len(scoreboard['calibration_flags'])}")
    return lines


def _defender_block(metrics: dict[str, Any] | None) -> list[str]:
    if not metrics:
        return []
    # `_num`, not a bare interpolation: these come straight off the snapshot as
    # REALs, and the defender's live Sharpe printed as 0.6479648177000503. Same
    # 2-decimal presentation as `_stack_block`, so the two blocks the owner
    # compares are formatted alike.
    lines = [
        "",
        f"📈 Defender (USD, 36M): Sharpe {_num(metrics.get('sharpe_rolling'))} | "
        f"Sortino {_num(metrics.get('sortino_rolling'))} | "
        f"Calmar {_num(metrics.get('calmar_rolling'))}",
    ]
    returns = " | ".join(
        f"{label} {pct(metrics.get(key), signed=True)}"
        for label, key in (
            ("3m", "return_3m"),
            ("6m", "return_6m"),
            ("1y", "return_1y"),
            ("3y", "return_3y"),
            ("5y", "return_5y"),
        )
        if metrics.get(key) is not None
    )
    if returns:
        lines.append(f"   Returns: {returns}")
    return lines


def render_digest(
    *,
    regime: dict[str, Any],
    global_liquidity: dict[str, Any],
    ranking: list[dict[str, Any]],
    invariants: list[dict[str, Any]],
    proposal: dict[str, Any] | None,
    scoreboard: dict[str, Any],
    defender_metrics: dict[str, Any] | None = None,
    alerts: list[Alert] | None = None,
    stack: dict[str, Any] | None = None,
    market_signal: dict[str, Any] | None = None,
    worker_reading: dict[str, Any] | None = None,
) -> str:
    """The full weekly digest as text (docs/EXAMPLE.md Steps 8A/8B). All the
    percent formatting lives in the block helpers; the inputs are decimal
    fractions.

    `market_signal` (the ADOPTED path's latest journalled decision) and
    `proposal` (the RETAINED BRIDGE's latest switch/reallocation) are separate
    inputs on purpose — see `_market_signal_block`. The adopted path is rendered
    FIRST, above the bridge's slot, because it is the one the owner acts on."""
    blocks = [
        _alert_block(alerts or []),
        _regime_header(regime, global_liquidity),
        _ranking_block(ranking),
        _invariant_block(invariants),
        _market_signal_block(market_signal, worker_reading),
        _stack_block(stack),
        _proposal_block(proposal),
        _scoreboard_block(scoreboard),
        _defender_block(defender_metrics),
    ]
    return "\n".join(line for block in blocks for line in block)


async def build_scoreboard(db: InvestmentDB) -> dict[str, Any]:
    """Assemble the scoreboard (docs/TASKS.md Task 6bis.1): the cumulative +12w
    hit-rate (won / decided) and the paper-tests still in progress from the
    proposal ledger, plus the agent-discovery strategies still IN probation.

    `paper_tests` comes from `outcomes.paper_test_progress` rather than from a
    second query over the same ledger. Two reasons, and the first is that the
    spec asks for the running proposed-vs-incumbent, which only that function
    computes — it had no caller at all, so what it measured reached nobody. The
    second is that the two selections are the same population (paper_started set,
    verdict still pending) and duplicating it here is how a scoreboard starts
    counting a different set of tests than the one it prices.

    "In probation" is `status='proposed'` with no probation OutcomeEvent yet —
    born of an innovation, not yet judged (docs/ARCHITECTURE.md "System
    Evolution": a strategy is proposed/disabled for the whole probation window,
    and `outcomes.strategy_probation_check` is what activates or closes it).

    `calibration_flags` stays empty: scenario calibration is SUPERSEDED by
    ADR-007 (score_scenarios was removed), so there is no calibration to flag —
    the block omits it."""
    rows = await db.query("SELECT json_extract(outcome, '$.verdict') AS verdict FROM proposal")
    won = sum(1 for r in rows if r["verdict"] == "won")
    lost = sum(1 for r in rows if r["verdict"] == "lost")
    paper = await paper_test_progress(db)
    probations = await db.query(
        "SELECT id FROM strategy WHERE source = 'agent-discovery' AND status = 'proposed' "
        "AND id NOT IN ("
        "  SELECT source_id FROM event_log WHERE type = 'OutcomeEvent' "
        "  AND json_extract(payload, '$.kind') = 'probation')"
    )
    return {
        "hit_rate": (won, won + lost),
        "paper_tests": paper,
        "probations": probations,
        "calibration_flags": [],
    }


# -- the DB -> digest assembler ---------------------------------------------


def _json_map(raw: Any) -> dict[str, Any]:
    """A JSON-text column as a dict (`{}` when NULL or unparseable — the digest
    degrades a block, it never fails to render)."""
    if not isinstance(raw, str):
        return raw if isinstance(raw, dict) else {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _latest_proposal(
    db: InvestmentDB, ranking: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The BRIDGE proposal the digest renders: the most recent switch or
    reallocation, resolved into what `_proposal_block` reads. A reallocation
    needs `current_allocation` — the allocation it moves AWAY from, which lives
    on the defender's snapshot row, not on the Proposal (the vertex stores the
    target and the diff). `None` when the ledger is empty.

    MARKET-SIGNAL ROWS ARE EXCLUDED. They are rendered from their journal by
    `_market_signal_block`; leaving them in this ORDER BY meant the two paths
    raced for one slot, and on a moving month the 09:00 reallocation's later
    `created_at` beat the 08:55 decision — hiding the only order the owner
    actually had to place."""
    rows = await db.query(
        "SELECT * FROM proposal WHERE proposal_type != 'market-signal' "
        "ORDER BY date DESC, created_at DESC LIMIT 1"
    )
    if not rows:
        return None
    proposal = dict(rows[0])
    proposal["proposed_allocation"] = _json_map(proposal.get("proposed_allocation"))
    current = next(
        (r for r in ranking if str(r.get("portfolio_id")) == str(proposal.get("defender_id"))),
        None,
    )
    proposal["current_allocation"] = _json_map(current.get("allocation")) if current else {}
    return proposal


async def build_digest(db: InvestmentDB) -> str:
    """The weekly digest rendered from the DB alone (docs/MILESTONES.md M8:
    "digest rendered in terminal"). Mechanical and read-only — every input is a
    committed row, so the digest can be re-rendered for any past Monday's state
    without re-running the cognitive cycle, and the M9 Telegram send and the M10
    dashboard both call THIS rather than reassembling the payload themselves.

    The invariants shown are the heaviest INTEGRATED ones (what the ranking is
    allowed to lean on), which is also the set gate 6 admits."""
    regime_rows = await db.query(
        "SELECT r.regime_type_id, r.confidence, rt.name AS regime_name "
        "FROM regime r JOIN regime_type rt ON rt.id = r.regime_type_id "
        "WHERE r.is_current = 1 LIMIT 1"
    )
    liquidity_rows = await db.query(
        "SELECT level, speed FROM market_data WHERE ticker = 'GLOBAL_LIQUIDITY' "
        "ORDER BY ts DESC LIMIT 1"
    )
    ranking = await db.query(
        "SELECT * FROM portfolio_weekly_snapshot "
        "WHERE date = (SELECT MAX(date) FROM portfolio_weekly_snapshot) ORDER BY rank ASC"
    )
    invariants = await db.query(
        "SELECT title, weight_effective, confirmation_count, infirmation_count, author "
        "FROM invariant WHERE status = 'integrated' "
        "ORDER BY weight_effective DESC LIMIT :n",
        n=DIGEST_INVARIANTS,
    )
    defender = next((r for r in ranking if r.get("defender")), None)
    return render_digest(
        regime=dict(regime_rows[0]) if regime_rows else {},
        global_liquidity=dict(liquidity_rows[0]) if liquidity_rows else {},
        ranking=[dict(r) for r in ranking],
        invariants=[dict(r) for r in invariants],
        proposal=await _latest_proposal(db, [dict(r) for r in ranking]),
        scoreboard=await build_scoreboard(db),
        defender_metrics=dict(defender) if defender else None,
        alerts=await collect_alerts(db),
        stack=await _stack_standing(db),
        market_signal=await _latest_market_signal_decision(db),
        worker_reading=await _latest_worker_reading(db),
    )


async def _latest_worker_reading(db: InvestmentDB) -> dict[str, Any] | None:
    """The latest journalled Worker reading (`decision_cycle.WORKER_READING_EVENT`).

    Read off the EventLog by descending ULID, like the decision itself: the id IS
    the append order (CLAUDE.md "EventLog"). `None` before the first cognitive
    cycle, which is the normal state of a DB whose mechanical path has run and
    whose UC8 has not."""
    rows = await db.query(
        "SELECT payload FROM event_log WHERE type = :t ORDER BY id DESC LIMIT 1",
        t=WORKER_READING_EVENT,
    )
    return _json_map(rows[0]["payload"]) if rows else None


async def _latest_market_signal_decision(db: InvestmentDB) -> dict[str, Any] | None:
    """The latest journalled market-signal decision (writeback
    `MARKET_SIGNAL_EVENT`), plus the `reasoning` prose of the Proposal it
    emitted, if it emitted one.

    Read off the EventLog — whose monotonic ULID id IS the append order
    (CLAUDE.md "EventLog") — so `ORDER BY id DESC LIMIT 1` is the latest by
    construction. The journal carries one row per decision date whether or not
    money moved, which is exactly why the digest reads it instead of the
    Proposal ledger. `None` before the first decision."""
    rows = await db.query(
        "SELECT payload FROM event_log WHERE type = :t ORDER BY id DESC LIMIT 1",
        t=MARKET_SIGNAL_EVENT,
    )
    if not rows:
        return None
    decision = _json_map(rows[0]["payload"])
    if not decision:
        return None
    proposal_id = decision.get("proposal_id")
    if proposal_id:
        prose = await db.query("SELECT reasoning FROM proposal WHERE id = :id", id=str(proposal_id))
        if prose:
            decision["reasoning"] = prose[0]["reasoning"]
    return decision


async def _stack_standing(db: InvestmentDB) -> dict[str, Any] | None:
    """The stack's latest NAV row. `None` before its NAV is backfilled — then
    the block is simply absent rather than printing zeros."""
    rows = await db.query(
        "SELECT sortino_rolling, calmar_rolling, drawdown FROM portfolio_nav "
        "WHERE portfolio_id = :p AND drawdown IS NOT NULL ORDER BY ts DESC LIMIT 1",
        p=STACK_PORTFOLIO_ID,
    )
    return dict(rows[0]) if rows else None
