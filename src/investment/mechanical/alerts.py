"""Health alerts on the live allocation path — what the owner must be TOLD,
never what the system refuses to do (docs/DECISIONS.md ADR-009).

Three checks, all read-only, all rendered into the Monday digest:

- **drawdown** — the stack's 36M rolling drawdown against
  `user_profile.max_drawdown_pct`. This is the -25% rule of ADR-007, and it is
  an alert rather than a gate for a measured reason: blocking a proposal only
  freezes a position, and over the three historical episodes a -25% trigger
  would have fired on 2020-03-20, the exact bottom.
- **market-data freshness** — the stack's own price sleeves vs today. This is
  the one that actually protects capital. The stack's only downside control is
  the 200d overlay, which re-reads its moving average once per monthly decision;
  if the data pipeline dies, the overlay goes blind while the code keeps
  reporting a perfectly normal month. In 2020 the fall took 25 days.
- **decision freshness** — no new monthly anchor in over a month. The backstop
  for the case where data flows but the cycle is stuck.

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
from datetime import date

from investment.db.sqlite import InvestmentDB
from investment.mechanical.market_signal import STACK_PORTFOLIO_ID, STACK_TICKERS

# The weekly chain refreshes market data every Monday, so anything past a week
# means at least one chain run did not happen or did not fetch.
MARKET_DATA_STALE_DAYS = 7
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
    firing every Monday until the episode ages out of the window (up to 3
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
            "since). Nothing has been blocked — the 200d overlay remains the mechanism that "
            "acts. This is for your decision."
        ),
    )


async def market_data_freshness_alert(db: InvestmentDB, today: date | None = None) -> Alert | None:
    """The newest MarketData row vs today. Silence here is the dangerous state:
    a dead pipeline leaves the 200d overlay reading stale prices while every
    other part of the chain reports success."""
    today = today or date.today()
    # Scoped to the STACK's own price sleeves, not `MAX(ts)` over the whole
    # table. The table also holds monthly macro series (CPI, GDP) that lag by
    # design, and — the failure that matters — a table-wide MAX would read fresh
    # off any ONE series still updating while the price feed the 200d overlay
    # depends on was dead. The check must watch what the overlay actually reads.
    # The OLDEST of the sleeves is the binding one: a stack cannot be valued on
    # a sleeve it has no price for.
    placeholders = ",".join(f":t{n}" for n in range(len(STACK_TICKERS)))
    rows = await db.query(
        f"SELECT ticker, MAX(ts) AS latest FROM market_data WHERE ticker IN ({placeholders}) "
        "GROUP BY ticker",
        **{f"t{n}": t for n, t in enumerate(STACK_TICKERS)},
    )
    # A sleeve with ZERO rows produces no GROUP BY row at all, so it is invisible
    # to the `min` below — the worst case (a feed that never delivered) would
    # otherwise read as fresh off its four surviving siblings. `run_market_signal`
    # refuses to run on a missing sleeve, but it raises inside the chain; this is
    # the alert that says WHY on a Monday morning.
    absent = sorted(set(STACK_TICKERS) - {str(r["ticker"]) for r in rows})
    if absent:
        return Alert(
            level="critical",
            code="market_data_missing",
            message=(
                f"No price data at all for stack sleeve(s) {', '.join(absent)}. The stack cannot "
                "be valued or decided — the monthly cycle will abort until the feed is restored."
            ),
        )
    latest = min(str(r["latest"]) for r in rows)
    age = (today - date.fromisoformat(latest[:10])).days
    if age <= MARKET_DATA_STALE_DAYS:
        return None
    return Alert(
        level="critical",
        code="market_data_stale",
        message=(
            f"Stack price data stops at {latest}, {age} days ago. The 200d trend overlay — the "
            "stack's only downside control — cannot see past that date, and the decision "
            "cycle will keep reporting a normal month while blind."
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


async def collect_alerts(db: InvestmentDB, today: date | None = None) -> list[Alert]:
    """Every live-path alert, critical first — the order the digest renders."""
    found = [
        await stack_drawdown_alert(db),
        await market_data_freshness_alert(db, today),
        await decision_freshness_alert(db, today),
    ]
    alerts = [a for a in found if a is not None]
    return sorted(alerts, key=lambda a: a.level != "critical")
