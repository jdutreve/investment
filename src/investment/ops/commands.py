"""THE command layer — one implementation behind three fronts (ADR-005;
docs/TASKS.md Task 6ter.1; docs/USE_CASES.md UC9).

Telegram, the `invest` CLI and the dashboard are FRONTS. They parse an
instruction and render an answer; they never decide anything. Everything that
changes state goes through here, so every user action — whichever front it
arrived on — follows the same validation, the same EventLog-first ordering and
the same gates.

THREE INVARIANTS, and each is a failure this layer exists to prevent:

  1. IDEMPOTENT ACROSS FRONTS. Acting on something already in the requested
     state returns that state and appends NO second `UserDecisionEvent`. The
     owner disables a strategy on the dashboard and then types `/disable` on
     the phone; the second one must read as "already disabled", not write a
     second decision into the audit trail as though they had decided twice.
  2. SINGLE-FLIGHT. The heavy operations share ONE lock (`ops/run_lock.py`), so
     a manual chain during the weekly chain is refused with what is running
     rather than queued behind it — two chains in sequence would run the second
     on the artefacts of the first.
  3. EVENTLOG FIRST. The `UserDecisionEvent` is appended before the row it
     describes, in the same transaction (CLAUDE.md "EventLog").

WHAT IS DELIBERATELY ABSENT: the proposal ACCEPT/REJECT commands UC9 describes.
ADR-006 removed the user-validation gate — a proposal that passes its gates IS
the paper-test, `writeback._insert_market_signal_proposal` sets `paper_started`
for that reason, and `ops/cli.py` already records that `user_response` is a
column nothing writes. Building buttons for it would re-create the gate ADR-006
deleted and put a decision in front of the owner that the system does not
actually wait on. The same reasoning retires `proposal_expiry_days` (see
`mechanical/catchup.py`).

WHAT THE OWNER MAY STILL CHANGE — and it is exactly the list USE_CASES calls
"the owner's rules, never agent-overridden": the drawdown limit, the
concentration limit, and whether a strategy is enabled. Those are preferences,
not theses; the agent adjudicates theses and never these.
"""

import dataclasses
import logging
from datetime import UTC, date, datetime
from typing import Any

from investment.decision_cycle import run_decision_cycle
from investment.mechanical.catchup import run_catchup
from investment.ops.run_lock import AlreadyRunning
from investment.runtime import AgentRuntime
from investment.weekly import run_weekly_chain

logger = logging.getLogger(__name__)

DECISION_EVENT = "UserDecisionEvent"
SOURCE_UC = "UC9"

# Lock names for the heavy operations, alongside `weekly.CHAIN_LOCK`. Distinct
# strings because the refusal message names the holder, and "already running:
# catch-up" and "already running: weekly-chain" send the owner to different
# places.
CATCHUP_LOCK = "catch-up"
CYCLE_LOCK = "uc9-cycle"

# At most ONE ad-hoc cognitive cycle per day (UC9: "it may trigger at most one
# ad-hoc UC8 re-run per day"). Counted from the journal rather than held in
# memory, so a restart cannot reset it — and the journal is where the cycles
# are anyway (`decision_cycle.WORKER_READING_EVENT`).
CYCLE_TRIGGER = "uc9-adhoc"
MAX_ADHOC_CYCLES_PER_DAY = 1


@dataclasses.dataclass(frozen=True)
class CommandResult:
    """What a front renders. `changed` separates "I did it" from "it was
    already so" — the second must not read as an action the owner took."""

    ok: bool
    message: str
    changed: bool = False

    @classmethod
    def refused(cls, message: str) -> "CommandResult":
        return cls(ok=False, message=message, changed=False)

    @classmethod
    def noop(cls, message: str) -> "CommandResult":
        return cls(ok=True, message=message, changed=False)


async def _append_decision(
    runtime: AgentRuntime, action: str, payload: dict[str, Any], source_id: str | None = None
) -> None:
    """The audit row for a user action. Appended inside the caller's
    transaction, before the write it describes."""
    await runtime.db.append_event(
        type=DECISION_EVENT,
        source_uc=SOURCE_UC,
        source_id=source_id,
        payload={"action": action, **payload},
    )


# -- reads ------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StatusFacts:
    """Is it alive, and how current is what it knows.

    SPLIT OUT OF `status` FOR THE THIRD FRONT (M10). The chat fronts want one
    short message; the dashboard header wants the same five facts as fields, and
    the one thing it must not do is parse them back out of the sentence built
    for a phone. So the facts are gathered once and rendered separately —
    Telegram and the CLI through `status`, the browser as JSON. A sixth fact
    added here reaches all three, which is the property the ranking row lost for
    three days in August (`ranking` below says what that cost).

    `running` is None when the lock is free rather than the string "idle": a
    front decides how to say "nothing is running", and a header that wants to
    hide the row entirely should not have to compare against a word."""

    running: str | None
    last_chain: str | None
    market_data: str | None
    last_ranking: str | None
    last_decision: str | None


async def status_facts(runtime: AgentRuntime) -> StatusFacts:
    """The five freshness facts, read from the rows the jobs wrote."""
    db = runtime.db
    marker = await db.query("SELECT last_chain_success FROM detector_state WHERE id = 'singleton'")
    data = await db.query("SELECT MAX(ts) AS ts FROM market_data")
    snapshot = await db.query("SELECT MAX(date) AS d FROM portfolio_weekly_snapshot")
    decision = await db.query(
        "SELECT event_date FROM event_log WHERE type = 'MarketSignalDecisionEvent' "
        "ORDER BY id DESC LIMIT 1"
    )
    holder = runtime.lock.holder
    return StatusFacts(
        running=(
            f"{holder.name} since {holder.since.isoformat(timespec='seconds')}" if holder else None
        ),
        last_chain=(
            str(marker[0]["last_chain_success"])
            if marker and marker[0]["last_chain_success"]
            else None
        ),
        market_data=str(data[0]["ts"]) if data and data[0]["ts"] else None,
        last_ranking=str(snapshot[0]["d"]) if snapshot and snapshot[0]["d"] else None,
        last_decision=str(decision[0]["event_date"]) if decision else None,
    )


async def status(runtime: AgentRuntime) -> CommandResult:
    """What the agent is doing and how current it is.

    Deliberately short: this is the message read on a phone to answer "is it
    alive and did it run". The full picture is the digest, and the numbers
    behind it are `invest sql`."""
    facts = await status_facts(runtime)
    return CommandResult(
        ok=True,
        message=(
            f"🤖 {facts.running or 'idle'}\n"
            f"last chain: {facts.last_chain or 'never'}\n"
            f"market data: {facts.market_data or 'none'}\n"
            f"last ranking: {facts.last_ranking or 'none'}\n"
            f"last allocation decision: {facts.last_decision or 'none'}"
        ),
    )


def _sharpe(value: object) -> str:
    """An indicator that may be NULL. 'n/a' rather than 0.00 — an unmeasured
    Sharpe is not a bad one, the same distinction `digest.pct` makes."""
    return f"{value:.2f}" if isinstance(value, int | float) else "n/a"


async def ranking(runtime: AgentRuntime) -> CommandResult:
    """The latest ranked snapshot, defender starred.

    Read straight off `portfolio_weekly_snapshot` — the row the ranking job
    WROTE, never re-derived here. Re-ranking on read would let the phone and the
    digest disagree about the same Monday."""
    rows = await runtime.db.query(
        "SELECT rank, portfolio_id, sortino_rolling, sharpe_rolling, calmar_rolling, "
        "return_1y, defender FROM portfolio_weekly_snapshot "
        "WHERE date = (SELECT MAX(date) FROM portfolio_weekly_snapshot) ORDER BY rank"
    )
    if not rows:
        return CommandResult.noop("No ranking yet — the chain has not run.")
    lines = []
    for row in rows:
        star = " ★" if row["defender"] else ""
        sortino = row["sortino_rolling"]
        calmar = row["calmar_rolling"]
        # RETURN AND SHARPE ON EVERY FRONT (owner, 2026-08-15). This showed
        # sortino and calmar alone, so the phone and the CLI ranked on
        # risk-adjusted numbers while never saying what the thing RETURNED —
        # the digest had carried both since 2026-08-12 and the other two fronts
        # had not followed. One row of the same snapshot, read three ways: the
        # three must agree about what they show, not only about the order.
        one_year = row["return_1y"]
        metrics = f"1y {one_year * 100:+.1f}%" if isinstance(one_year, int | float) else "1y n/a"
        metrics += (
            f" sortino {sortino:.2f} sharpe {_sharpe(row['sharpe_rolling'])} calmar {calmar:.2f}"
            if isinstance(sortino, int | float) and isinstance(calmar, int | float)
            else " — not yet valued"
        )
        lines.append(f"{row['rank']}. {row['portfolio_id']}{star} — {metrics}")
    return CommandResult(ok=True, message="\n".join(lines))


# -- the owner's rules ------------------------------------------------------


async def set_strategy_enabled(
    runtime: AgentRuntime, strategy_id: str, enabled: bool
) -> CommandResult:
    """Enable or disable a Strategy — a preference, never a thesis (UC9)."""
    rows = await runtime.db.query("SELECT id, enabled FROM strategy WHERE id = :id", id=strategy_id)
    if not rows:
        return CommandResult.refused(f"No strategy '{strategy_id}'. Try /ranking for the ids.")
    if bool(rows[0]["enabled"]) == enabled:
        # INVARIANT 1: no second decision event for a state already reached.
        return CommandResult.noop(
            f"'{strategy_id}' is already {'enabled' if enabled else 'disabled'}."
        )

    async with runtime.db.transaction():
        await _append_decision(
            runtime,
            "set_strategy_enabled",
            {"strategy_id": strategy_id, "enabled": enabled},
            source_id=strategy_id,
        )
        await runtime.db.command(
            "UPDATE strategy SET enabled = :enabled, updated_at = :now WHERE id = :id",
            enabled=enabled,
            now=datetime.now(UTC).isoformat(),
            id=strategy_id,
        )
    return CommandResult(
        ok=True,
        message=f"'{strategy_id}' {'enabled' if enabled else 'disabled'}.",
        changed=True,
    )


async def set_max_drawdown(runtime: AgentRuntime, pct: float) -> CommandResult:
    """The binding drawdown rule, in PERCENT POINTS and negative
    (docs/DATA_MODELS.md "Units convention": `-15.0` = -15%).

    VALIDATED HERE because this number binds real gates: it excludes portfolios
    from the defender role and from proposal candidacy (CLAUDE.md "Binding
    caps") and it arms the drawdown ALERT on the stack (ADR-009). A positive
    number would silently exclude everything; a typo'd -250 would exclude
    nothing, forever, with no error to read."""
    if not -100.0 < pct < 0.0:
        return CommandResult.refused(
            f"A drawdown limit is a negative percentage between -100 and 0 — got {pct}. "
            "Example: /drawdown -20"
        )
    rows = await runtime.db.query("SELECT max_drawdown_pct FROM user_profile LIMIT 1")
    if not rows:
        return CommandResult.refused("No user_profile row — run the seed first.")
    if float(rows[0]["max_drawdown_pct"]) == pct:
        return CommandResult.noop(f"The drawdown limit is already {pct}%.")

    previous = float(rows[0]["max_drawdown_pct"])
    async with runtime.db.transaction():
        await _append_decision(runtime, "set_max_drawdown", {"from": previous, "to": pct})
        await runtime.db.command(
            "UPDATE user_profile SET max_drawdown_pct = :pct, updated_at = :now",
            pct=pct,
            now=datetime.now(UTC).isoformat(),
        )
    return CommandResult(
        ok=True,
        message=(
            f"Drawdown limit {previous}% → {pct}%. It binds from the next gate evaluation; "
            "nothing already committed is revisited."
        ),
        changed=True,
    )


async def set_max_single_asset(runtime: AgentRuntime, pct: float) -> CommandResult:
    """The binding CONCENTRATION cap, in percent points on 0-100.

    THE MISSING HALF OF THE PAIR (added 2026-08-14). `set_max_drawdown` has
    existed since the command layer shipped, and this one had not — so of the two
    caps CLAUDE.md calls binding, the owner could move one from a chat message
    and the other only by editing `.env` and re-running the seed. That asymmetry
    was found the day the cap actually had to move (50 -> 60 for the tight-flat
    book's SPY 60) and cost a full re-seed to apply a single number.

    VALIDATED HERE for the same reason the drawdown rule is: this number binds
    real gates. It refuses any proposal whose largest ASSET CLASS exceeds it
    (`gates.concentration_ok` — a class, not a ticker, since 2026-08-14), and the
    market-signal path takes the stricter of it, `ms-stack`'s and the book's. A
    value at or below zero would refuse every allocation ever proposed; one above
    100 would bind nothing, forever, with no error to read.

    IT DOES NOT TOUCH THE PER-PORTFOLIO ROWS, deliberately: those may only be
    STRICTER (CLAUDE.md "Binding caps"), so raising the user profile alone
    leaves a book capped by its own row exactly as its author intended. Raising a
    book's own cap is a seed change, which is where that decision belongs."""
    if not 0.0 < pct <= 100.0:
        return CommandResult.refused(
            f"A concentration cap is a percentage in (0, 100] — got {pct}. Example: /cap 60"
        )
    rows = await runtime.db.query("SELECT max_single_asset_pct FROM user_profile LIMIT 1")
    if not rows:
        return CommandResult.refused("No user_profile row — run the seed first.")
    previous = float(rows[0]["max_single_asset_pct"])
    if previous == pct:
        return CommandResult.noop(f"The concentration cap is already {pct}%.")

    async with runtime.db.transaction():
        await _append_decision(runtime, "set_max_single_asset", {"from": previous, "to": pct})
        await runtime.db.command(
            "UPDATE user_profile SET max_single_asset_pct = :pct, updated_at = :now",
            pct=pct,
            now=datetime.now(UTC).isoformat(),
        )
    return CommandResult(
        ok=True,
        message=(
            f"Concentration cap {previous}% → {pct}%. It binds the largest ASSET CLASS of an "
            "allocation, not the largest ticker, and applies from the next gate evaluation; "
            "per-portfolio rules stay as they are and may only be stricter."
        ),
        changed=True,
    )


# -- the heavy operations (single-flight) -----------------------------------


async def refresh(runtime: AgentRuntime) -> CommandResult:
    """UC1 alone — fetch, regime step, NAV. `/refresh` in UC9's own words: "same
    prelude, no UC8"."""
    try:
        async with runtime.lock.hold(CATCHUP_LOCK):
            report = await run_catchup(runtime.db, runtime.settings)
    except AlreadyRunning as exc:
        return CommandResult.refused(str(exc))
    skipped = f", skipped {sorted(report.skipped)}" if report.skipped else ""
    return CommandResult(
        ok=True,
        message=(
            f"Catch-up done: {report.tickers_refreshed} series, {report.market_rows} rows, "
            f"{report.regime_episodes} regime change(s), {report.navs_rebuilt} NAVs{skipped}."
        ),
        changed=True,
    )


async def run_chain(runtime: AgentRuntime) -> CommandResult:
    """The whole weekly chain, on demand. The same function the cron and the
    heartbeat call — not a second assembly of the same steps."""
    try:
        result = await run_weekly_chain(runtime)
    except AlreadyRunning as exc:  # pragma: no cover - raised inside run_weekly_chain
        return CommandResult.refused(str(exc))
    if result is None:
        holder = runtime.lock.holder
        return CommandResult.refused(f"already running: {holder.name if holder else 'a chain'}")
    if result.ok:
        return CommandResult(
            ok=True, message=f"Chain complete: {', '.join(result.completed)}.", changed=True
        )
    return CommandResult(
        ok=False,
        message=f"Chain ABORTED at '{result.failed_step}': {result.error}",
        changed=True,
    )


async def adhoc_cycles_today(runtime: AgentRuntime, today: date) -> int:
    """How many ad-hoc cognitive cycles have run today, from the journal.

    Counted rather than remembered: a process restart must not hand the owner a
    fresh allowance, and the journal already records every cycle with its
    trigger (`decision_cycle.journal_worker_reading`)."""
    rows = await runtime.db.query(
        "SELECT COUNT(*) AS n FROM event_log WHERE type = 'WorkerReadingEvent' "
        "AND event_date = :d AND json_extract(payload, '$.trigger') = :t",
        d=today.isoformat(),
        t=CYCLE_TRIGGER,
    )
    return int(rows[0]["n"]) if rows else 0


async def run_cycle(runtime: AgentRuntime, *, today: date | None = None) -> CommandResult:
    """An ad-hoc UC8, at most once a day (UC9).

    PRECEDED BY THE CATCH-UP, always, and that is UC9's own requirement: the
    prelude runs "so the Worker never reasons on stale market data". The RANKING
    context deliberately stays the latest Monday snapshot — no mid-week snapshot
    rewrite — so the cycle nuances the same standings the digest showed.

    Both steps hold ONE lock for the whole operation rather than one each: a
    weekly chain starting between the prelude and the cycle would rewrite the
    ground under it."""
    today = today or date.today()
    used = await adhoc_cycles_today(runtime, today)
    if used >= MAX_ADHOC_CYCLES_PER_DAY:
        return CommandResult.refused(
            f"Already ran {used} ad-hoc cycle today (limit {MAX_ADHOC_CYCLES_PER_DAY}). "
            "The weekly chain runs one anyway."
        )
    thresholds = {
        str(r["key"]): float(r["value"])
        for r in await runtime.db.query("SELECT key, value FROM system_thresholds")
    }
    profile = await runtime.db.query("SELECT * FROM user_profile LIMIT 1")
    if not profile:
        return CommandResult.refused("No user_profile row — run the seed first.")

    try:
        async with runtime.lock.hold(CYCLE_LOCK):
            await run_catchup(runtime.db, runtime.settings)
            result = await run_decision_cycle(
                runtime.db,
                runtime.planner_pre,
                runtime.worker_agent,
                runtime.planner_post,
                trigger=CYCLE_TRIGGER,
                thresholds=thresholds,
                today=today,
            )
    except AlreadyRunning as exc:
        return CommandResult.refused(str(exc))

    reading = result.worker_result.market_signal_assessment.strip()
    return CommandResult(
        ok=True,
        message=f"Cycle done.\n\n🗣 {reading}" if reading else "Cycle done (no reading given).",
        changed=True,
    )


async def save_note(runtime: AgentRuntime, text: str) -> CommandResult:
    """A plain-text message becomes an inbox note (Task 6bis.2: "text ->
    `<ts>-note.md`, the qualitative-event channel").

    WRITTEN TO THE INBOX AND NOT INGESTED HERE, which is the opposite of what
    UC3's events do — and the difference is the point. An event is fetched by
    the agent and must reach the 08:10 sweep the same morning; a note is
    deposited by the OWNER, and the watcher's 5-minute quiet period exists so a
    burst of deposits settles into one batch. Ingesting a note synchronously
    would bypass the batching the watcher was built for.

    NEVER OVERWRITES, and the name is why: seconds are not a fine enough clock
    for a chat front. Two `/note` messages sent in the same second — a thought
    typed in two halves, the ordinary way people write on a phone — produced one
    filename, and `write_text` silently replaced the first with the second. The
    quiet period then guarantees they were still both waiting to be ingested.
    `delivery.write_locally` reached the same answer for the same reason: a
    taken name gets a suffix, because a resolution is a bet on how fast the
    caller is."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    inbox = runtime.settings.inbox_path
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{stamp}-note.md"
    attempt = 2
    while path.exists():
        path = inbox / f"{stamp}-{attempt}-note.md"
        attempt += 1
    path.write_text(text.strip() + "\n", encoding="utf-8")
    logger.info("note saved: %s (%d chars)", path.name, len(text))
    return CommandResult(
        ok=True,
        message=f"Noted — {path.name}. It will be ingested within ~5 minutes.",
        changed=True,
    )


def describe_result(result: CommandResult) -> str:
    """One rendering rule for every front: refusals are marked, no-ops read as
    statements of fact rather than as actions."""
    if not result.ok:
        return f"⛔ {result.message}"
    return result.message if result.changed else f"(i) {result.message}"


def parse_float(raw: str) -> float | None:
    """A number from a chat message, or None. Tolerates a trailing '%' because
    the owner types `/drawdown -20%` as readily as `-20`."""
    try:
        return float(raw.strip().rstrip("%"))
    except ValueError:
        return None
