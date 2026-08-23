"""Health alerts on the live allocation path — what the owner must be TOLD,
never what the system refuses to do (docs/DECISIONS.md ADR-009).

Six checks, all read-only, all rendered into the weekly digest:

- **drawdown** — the stack's 36M rolling drawdown against
  `user_profile.max_drawdown_pct`. This is the -25% rule of ADR-007, and it is
  an alert rather than a gate for a measured reason: blocking a proposal only
  freezes a position, and over the three historical episodes a -25% trigger
  would have fired on 2020-03-20, the exact bottom.
- **market-data freshness** — the stack's own price sleeves vs today. This is
  the one that actually protects capital. The stack's only downside control is
  the trend overlay, which re-reads its moving average once per monthly decision;
  if the data pipeline dies, the overlay goes blind while the code keeps
  reporting a perfectly normal month. In 2020 the fall took 25 days.
- **signal freshness** — `BAA10Y` and `T10Y2Y`, the two series that pick the
  book. Its own check because the failure differs in kind: a stale signal does
  not blind the decision, it MISINFORMS it. The forward fill carries the last
  print, so the stack keeps choosing a book from a spread quoted weeks ago and
  nothing else in the digest looks wrong.
- **macro freshness** — every other MACRO series, against its OWN measured
  cadence. The two above watch what picks the book; this watches what the
  WORKER READS. `planner/baseline._macro` hands it the latest print of every
  macro series, so a frozen feed does not misinform the allocation, it
  misinforms the weekly argument — which nothing else re-derives. `warn`, not
  `critical`: macro lags by design (ADR-003), a MISSED scheduled print does not.
- **decision freshness** — no new monthly anchor in over a month. The backstop
  for the case where data flows but the cycle is stuck.
- **anti-drift** — does the wired stack still reproduce the pinned pair ADR-007
  was signed on? Read off the verdict `market_signal_cycle.journal_drift`
  appends every run. Added 2026-08-12, and the gap it closes is the oldest one
  here: the pair lived in a docstring, nothing read it, and the paragraph
  stating the rule had itself drifted two supersessions behind unnoticed.

WHAT THE STACK'S NAV IS, since two of these read it: a PAPER series. ADR-009
gave `ms-stack` a `portfolio_nav` built by `shadow_book_nav` from the decision
walk, which assumes every monthly decision was executed at the close of its
anchor date. Nothing here reads a broker statement — V1 never executes (ADR-006)
and forward paper-mode is where fills, slippage and the owner's actual timing
will show up (docs/V1_STRATEGY.md Step 6). So the drawdown alert reports what
the STRATEGY would have suffered, not what the account did, and it says so.

WHY FRESHNESS NEEDS AN ALARM AT ALL: the failure is SILENT and shaped exactly
like success. `run_market_signal_cycle` answers "already decided" whether the
month was genuinely handled or the pipeline died three weeks ago, and since ~9
months out of 12 the correct outcome is "no change", nothing looks wrong. These
thresholds are deliberately much SHORTER than the monthly decision cadence — an
alarm that takes longer to fire than the cycle it watches can never warn in
time.
"""

import dataclasses
import json
import statistics
from datetime import date

from investment.db.sqlite import InvestmentDB
from investment.market_signal_cycle import DRIFT_EVENT
from investment.mechanical.market_signal import (
    CREDIT_SPREAD,
    MA_WINDOWS,
    STACK_PORTFOLIO_ID,
    STACK_TICKERS,
    YIELD_SLOPE,
)
from investment.writeback.writeback import RULE_MEASUREMENT_EVENT

# The weekly chain refreshes market data every week, so anything past a week
# means at least one chain run did not happen or did not fetch. The same figure
# fits both watched groups: the sleeves are daily exchange prices and the two
# FRED signals are daily business-day series with a ~1-day publication lag.
MARKET_DATA_STALE_DAYS = 7
# `macro_freshness_alert`'s two knobs. The sample is how many prints back the
# cadence is measured over — a year of monthly prints, and enough daily ones
# that a holiday week cannot move the median.
MACRO_CADENCE_SAMPLE = 13
MACRO_OVERDUE_GRACE_DAYS = 14
# The two series that PICK the book (mechanical/market_signal.py).
SIGNAL_TICKERS: tuple[str, ...] = (CREDIT_SPREAD, YIELD_SLOPE)
# One monthly anchor missed. Longer than the cadence by design (35 > ~31), so a
# normal month never trips it, but a skipped month always does.
DECISION_STALE_DAYS = 35


@dataclasses.dataclass(frozen=True)
class Alert:
    """One thing the owner should look at. `level` is 'warn' or 'critical';
    nothing here ever changes what the system does."""

    level: str
    code: str
    message: str


async def stack_drawdown_alert(db: InvestmentDB) -> Alert | None:
    """The -25% rule, on the stack's 36M rolling drawdown.

    Reads `portfolio_nav.drawdown` for the stack — the same column, from the
    same pinned formula, as every portfolio in the ranking. Returns None when
    the stack has no NAV yet (nothing to say) or is inside its limit.

    WHAT THE NUMBER IS, stated because the obvious reading is wrong:
    `ratios.rolling_max_drawdown` is `min(NAV/cummax(NAV) - 1)` WITHIN the
    trailing 756 days — the DEEPEST drawdown of the last 36 months, not the
    current distance from a 36-month high. A stack sitting at an all-time high
    today still reports -30% if it fell that far two years ago, so the message
    must not say "is X% below": it says what was measured, and over what window.
    A consequence ADR-009 does not address: once breached, this alert keeps
    firing every week until the episode ages out of the window (up to 3
    years), even after a full recovery."""
    rows = await db.query(
        "SELECT ts, drawdown FROM portfolio_nav WHERE portfolio_id = :p AND drawdown IS NOT NULL "
        "ORDER BY ts DESC LIMIT 1",
        p=STACK_PORTFOLIO_ID,
    )
    if not rows:
        return None
    caps = await db.query("SELECT max_drawdown_pct FROM user_profile LIMIT 1")
    if not caps:
        return None
    drawdown = float(rows[0]["drawdown"])
    limit = float(caps[0]["max_drawdown_pct"]) / 100.0
    if drawdown >= limit:
        return None
    return Alert(
        level="critical",
        code="stack_drawdown",
        message=(
            f"The stack's deepest drawdown over the last 36 months is {drawdown * 100:.1f}%, "
            f"past your {limit * 100:.0f}% rule (measured as of {rows[0]['ts']} on the PAPER "
            "series — the strategy's own decisions, not your account; it may have recovered "
            "since). Nothing has been blocked — the trend overlay remains the mechanism that "
            "acts. This is for your decision."
        ),
    )


async def _oldest_series(
    db: InvestmentDB, tickers: tuple[str, ...], today: date
) -> tuple[list[str], str, int]:
    """`(tickers with no rows at all, the OLDEST latest-ts among the rest, its
    age in days)`. The oldest is the binding one in both groups: a stack cannot
    be valued on a sleeve it has no price for, nor decided on a signal it has no
    print for. Age is 0 when every ticker is absent (there is nothing to age).

    Never `MAX(ts)` over the whole table: it also holds monthly macro series
    (CPI, GDP) that lag by design, and — the failure that matters — a table-wide
    MAX reads fresh off any ONE series still updating while the feed this check
    exists to watch is dead.

    A ticker with ZERO rows produces no GROUP BY row at all, so it would be
    invisible to the `min` — the worst case (a feed that never delivered) reading
    as fresh off its surviving siblings. Hence the absent list, returned
    separately rather than folded into the age."""
    placeholders = ",".join(f":t{n}" for n in range(len(tickers)))
    rows = await db.query(
        f"SELECT ticker, MAX(ts) AS latest FROM market_data WHERE ticker IN ({placeholders}) "
        "GROUP BY ticker",
        **{f"t{n}": t for n, t in enumerate(tickers)},
    )
    absent = sorted(set(tickers) - {str(r["ticker"]) for r in rows})
    if not rows:
        return absent, "", 0
    latest = min(str(r["latest"]) for r in rows)
    return absent, latest, (today - date.fromisoformat(latest[:10])).days


async def market_data_freshness_alert(db: InvestmentDB, today: date | None = None) -> Alert | None:
    """The newest MarketData row for the stack's own price SLEEVES vs today.
    Silence here is the dangerous state: a dead pipeline leaves the trend overlay
    reading stale prices while every other part of the chain reports success.

    `run_market_signal` refuses to run on a missing sleeve, but it raises inside
    the chain; this is the alert that says WHY on a chain morning."""
    today = today or date.today()
    absent, latest, age = await _oldest_series(db, STACK_TICKERS, today)
    if absent:
        return Alert(
            level="critical",
            code="market_data_missing",
            message=(
                f"No price data at all for stack sleeve(s) {', '.join(absent)}. The stack cannot "
                "be valued or decided — the monthly cycle will abort until the feed is restored."
            ),
        )
    if age <= MARKET_DATA_STALE_DAYS:
        return None
    return Alert(
        level="critical",
        code="market_data_stale",
        message=(
            f"Stack price data stops at {latest}, {age} days ago. The "
            f"{'/'.join(str(w) for w in MA_WINDOWS)}d trend "
            "overlay — the stack's only downside control — cannot see past that date, and "
            "the decision cycle will keep reporting a normal month while blind."
        ),
    )


async def signal_freshness_alert(db: InvestmentDB, today: date | None = None) -> Alert | None:
    """The two series that PICK the book — `BAA10Y` and `T10Y2Y` — vs today.

    A DIFFERENT failure from the sleeves', and the reason this is its own check.
    A dead price feed blinds the overlay; a dead signal feed does not stop the
    decision at all — `run_market_signal` forward-fills the last print, so the
    stack keeps choosing a book from a spread quoted weeks ago and the month
    looks entirely normal. The decision is uninformed rather than absent, which
    is the harder failure to notice.

    An ALERT and never a block, twice over: ADR-003 says a stale print IS what
    was knowable, so acting on it is correct vintage discipline rather than an
    error, and ADR-009 scopes the live path to telling the owner rather than
    refusing. The Proposal's `market_context` already records each input's
    `knowable_at`; this is what puts that age in front of the owner without them
    having to read a JSON payload. Total ABSENCE is the exception and does not
    reach here as an alert alone — `run_market_signal` raises on it (it would
    otherwise default to the 90%-equity book on no signal), and this message is
    the Sunday-morning explanation of that abort."""
    today = today or date.today()
    absent, latest, age = await _oldest_series(db, SIGNAL_TICKERS, today)
    if absent:
        return Alert(
            level="critical",
            code="signal_data_missing",
            message=(
                f"No data at all for market signal(s) {', '.join(absent)}. The book cannot be "
                "chosen — the monthly cycle will abort until the feed is restored."
            ),
        )
    if age <= MARKET_DATA_STALE_DAYS:
        return None
    return Alert(
        level="critical",
        code="signal_data_stale",
        message=(
            f"Market-signal data ({', '.join(SIGNAL_TICKERS)}) stops at {latest}, {age} days ago. "
            "The monthly decision still runs, on the last print it can see: the book it picks is "
            "that stale, and nothing else in the digest will look wrong."
        ),
    )


async def _series_cadence(db: InvestmentDB, ticker: str, today: date) -> tuple[float, int] | None:
    """`(median spacing in days over the last prints, age of the newest)`, or
    `None` when there is too little history to judge a cadence.

    THE CADENCE IS MEASURED, NOT DECLARED, and that is the whole point of this
    helper. `availability_lag_days` (db/seed_data.py) says how long after an
    observation it becomes knowable; it says nothing about how OFTEN the series
    prints, which is what "overdue" needs. A declared table of frequencies is
    also the shape CLAUDE.md warns about — it would name every series that
    existed the day it was written and go quietly wrong on the next one."""
    rows = await db.query(
        "SELECT ts FROM market_data WHERE ticker = :t ORDER BY ts DESC LIMIT :n",
        t=ticker,
        n=MACRO_CADENCE_SAMPLE,
    )
    if len(rows) < 3:
        return None
    stamps = [date.fromisoformat(str(r["ts"])[:10]) for r in rows]
    gaps = [(stamps[i] - stamps[i + 1]).days for i in range(len(stamps) - 1)]
    return statistics.median(gaps), (today - stamps[0]).days


async def macro_freshness_alert(db: InvestmentDB, today: date | None = None) -> Alert | None:
    """Every MACRO series the WORKER reasons on, against its OWN cadence.

    WHEN A SECOND ONE ARRIVES, FIND WHAT NAMED THE FIRST (CLAUDE.md).
    `signal_freshness_alert` above watches `SIGNAL_TICKERS` — "the two series
    that PICK the book" — and that sentence was exact when it was written. Then
    `planner/baseline._macro` started handing the Worker the latest print of
    EVERY macro series so it would stop spending tool calls on them, and a
    dozen series it reasons on became watched by nothing. Measured 2026-08-23:
    the Worker argued risk-on partly from "m2 accel +1.39", a print from
    2026-06-18 — 66 days and two missed monthly releases old — and no line
    anywhere said so.

    A SEPARATE CHECK from the two above, not a widening of them, because the
    stake differs. Those two protect capital: a blind overlay or a misinformed
    book choice. This one protects the READING — the Worker's qualitative
    argument, which no gate re-derives and no mechanical step would refuse.
    Hence `warn`, and hence the deliberately loose threshold: a series that
    lags is normal here (monthly macro lags by design, ADR-003), a series that
    has MISSED A SCHEDULED PRINT is not.

    `max(1.5 x spacing, spacing + 14)` — one rule, no per-ticker table, and it
    reads correctly at every cadence the DB actually holds: a daily series is
    overdue after 15 days, a weekly one after 21, a monthly one after ~46 (two
    weeks into the period it skipped). Simulated over all 17 MACRO series on
    2026-08-23: only the three frozen m2 series fire, the nearest non-firing
    series (JPNASSETS, 23 days) sits at half its threshold.

    Silent when a series is merely absent: that is `_oldest_series`' job for the
    two feeds where absence stops the cycle, and a macro series that never
    existed is a seeding question, not a freshness one.

    `SIGNAL_TICKERS` are EXCLUDED though they are MACRO rows: they are watched
    above at 7 days and `critical`, and reporting the same frozen feed twice in
    one digest — once as capital risk, once as reading quality — teaches the
    owner to skim both lines."""
    today = today or date.today()
    rows = await db.query(
        "SELECT DISTINCT ticker FROM market_data WHERE asset_class = 'MACRO' ORDER BY ticker"
    )
    overdue: list[tuple[str, int, float]] = []
    for row in rows:
        ticker = str(row["ticker"])
        if ticker in SIGNAL_TICKERS:
            continue
        cadence = await _series_cadence(db, ticker, today)
        if cadence is None:
            continue
        spacing, age = cadence
        if age > max(1.5 * spacing, spacing + MACRO_OVERDUE_GRACE_DAYS):
            overdue.append((ticker, age, spacing))
    if not overdue:
        return None
    overdue.sort(key=lambda o: -o[1])
    detail = ", ".join(
        f"{t} ({age}d old, prints every ~{spacing:.0f}d)" for t, age, spacing in overdue
    )
    return Alert(
        level="warn",
        code="macro_data_overdue",
        message=(
            f"Macro series past due: {detail}. Nothing is blocked and the book is unaffected "
            "— these do not pick it. But the weekly reading is built on them, so treat any "
            "argument resting on those numbers as that old."
        ),
    )


async def stack_drift_alert(db: InvestmentDB) -> Alert | None:
    """ADR-007's anti-drift guarantee, surfaced: does the wired stack still
    reproduce the pair it was signed on (`market_signal.PINNED_CAGR` /
    `PINNED_MAX_DRAWDOWN`)?

    A pure READ of the latest `DRIFT_EVENT`, which `market_signal_cycle`
    journals on every run. The measurement is a 35-year walk and belongs to a
    mechanical job, not to a renderer: `build_digest` renders committed rows so
    that any past chain can be re-rendered without recomputing anything, and a
    backtest inside it would also give the digest a new way to RAISE on Sunday
    morning (telegram/digest.py).

    Silent when the check passes, when it has never run, and when it reported
    itself UNMEASURABLE — the last one deliberately: an as-of snapshot bounded
    at 2008 cannot answer a 35-year question, and shouting "drift" at a window
    that is merely short would train the owner to ignore this line.

    WHAT A FIRING MEANS, and it is exactly two things: a rule moved without its
    pinned pair being re-signed in the same commit, or the ground moved under a
    fixed marker (I-48 — rolling backfill start, Yahoo restatements). The
    message says so, because the two need opposite responses and the alert
    cannot tell them apart."""
    rows = await db.query(
        "SELECT payload FROM event_log WHERE type = :t ORDER BY id DESC LIMIT 1",
        t=DRIFT_EVENT,
    )
    if not rows:
        return None
    payload = json.loads(str(rows[0]["payload"]))
    violations = payload.get("violations") or []
    if not payload.get("measurable") or not violations:
        return None
    window = payload.get("window") or ["?", "?"]
    return Alert(
        level="critical",
        code="stack_drift",
        message=(
            f"The stack no longer reproduces its pinned pair over {window[0]}..{window[1]}: "
            f"{'; '.join(str(v) for v in violations)}. Either a rule changed without its pair "
            "being re-signed, or the data moved under it (a re-seed, a Yahoo restatement). "
            "Nothing has been blocked — this is the anti-drift guarantee reporting, not a gate."
        ),
    )


async def decision_freshness_alert(db: InvestmentDB, today: date | None = None) -> Alert | None:
    """No new monthly decision anchor in over a month: the cycle is stuck even
    if the data is flowing."""
    today = today or date.today()
    rows = await db.query(
        "SELECT event_date FROM event_log WHERE type = 'MarketSignalDecisionEvent' "
        "ORDER BY id DESC LIMIT 1"
    )
    if not rows:
        return None
    age = (today - date.fromisoformat(str(rows[0]["event_date"])[:10])).days
    if age <= DECISION_STALE_DAYS:
        return None
    return Alert(
        level="warn",
        code="decision_stale",
        message=(
            f"No market-signal decision for {age} days (cadence is monthly). The allocation "
            "cycle has not run to completion."
        ),
    )


async def rule_tradeoff_alert(db: InvestmentDB) -> Alert | None:
    """A measured revision the machine will NOT decide — the fourth verdict
    (`rule_revision.Verdict`), surfaced because it is the only one that needs a
    human and the only one nothing else reports.

    WHY IT EXISTS. Until 2026-08-13 a revision that bought a large gain for a
    small loss got the word `reject`, identically to one that made everything
    worse, and the strategy was closed on both. A 125-day window (then the knob
    `ma_window_days`, now `ma_windows`) is the case
    that forced the change: -2.75pp of max drawdown, the largest safety gain
    ever measured on this stack, refused for 0.94% of Sortino and never shown to
    anyone. The verdict now names it and this line carries it out.

    A pure READ of the newest `RuleRevisionMeasuredEvent`, like every alert
    here: `build_digest` renders committed rows and must never run a backtest
    (telegram/digest.py). Silent unless the LATEST measurement is a trade-off —
    a trade-off two months old has either been decided or declined, and
    re-raising it every Sunday is how a digest line stops being read.

    `warn`, not `critical`: nothing is broken and nothing is blocked. It is a
    decision waiting, and the message says what the decision costs and buys so
    the owner can take it from the digest without opening a REPL."""
    rows = await db.query(
        "SELECT payload FROM event_log WHERE type = :t ORDER BY id DESC LIMIT 1",
        t=RULE_MEASUREMENT_EVENT,
    )
    if not rows:
        return None
    payload = json.loads(str(rows[0]["payload"]))
    if payload.get("verdict") != "trade-off":
        return None
    traded = payload.get("traded") or "no exchange recorded"
    return Alert(
        level="warn",
        code="rule_tradeoff",
        message=(
            f"A measured rule revision needs YOUR call — '{payload.get('title')}' "
            f"({payload.get('overrides')}): {traded}. Nothing has been adopted or refused: "
            "the Pareto test only decides revisions where nothing gets worse, and this one "
            "trades. Adopting it is a git edit and a signature (ADR-006/ADR-007)."
        ),
    )


async def collect_alerts(db: InvestmentDB, today: date | None = None) -> list[Alert]:
    """Every live-path alert, critical first — the order the digest renders."""
    found = [
        await stack_drawdown_alert(db),
        await stack_drift_alert(db),
        await market_data_freshness_alert(db, today),
        await signal_freshness_alert(db, today),
        await macro_freshness_alert(db, today),
        await decision_freshness_alert(db, today),
        await rule_tradeoff_alert(db),
    ]
    alerts = [a for a in found if a is not None]
    return sorted(alerts, key=lambda a: a.level != "critical")
