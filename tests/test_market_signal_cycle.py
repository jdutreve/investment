"""The LIVE monthly market-signal decision (ADR-007) end to end —
`market_signal_cycle.run_market_signal_cycle` + `writeback.dispose_market_signal`,
against a real throwaway SQLite.

What these pin, in the order the finding that prompted them listed the gaps:
the decision reaches a `proposal_type='market-signal'` row at all; the row
carries the audit record (held + target allocation, both signals with their
knowable dates, the below-trend sleeves); the caps bind and block; a month that
changes nothing writes a journal entry and no proposal; a second run in the same
month is a no-op; and the +12w outcome path scores the result against what was
HELD rather than against the book's static row.
"""

import itertools
import json
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from investment import market_signal_cycle as MSC
from investment.chain import run_chain
from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as MS
from investment.mechanical import outcomes
from investment.mechanical.gates import Caps
from investment.telegram.digest import build_digest
from investment.writeback import writeback as W

USER = {"max_single_asset_pct": 50.0, "max_drawdown_pct": -25.0}

WIDE = "credit-spread-wide"
TIGHT_STEEP = "credit-spread-tight-yield-curve-steep"

# The books hold these five; the 200d MA needs 200 prints and the trailing
# medians want 252, so the fixture runs a little over two years of daily data.
DAYS = 700
START = date(2024, 1, 1)


def _decision(
    *,
    held: str = WIDE,
    signalled: str = WIDE,
    target: dict[str, float] | None = None,
    below: tuple[str, ...] = (),
    pending: str | None = None,
    pending_count: int = 0,
) -> MS.Decision:
    """A Decision built directly, for the disposition tests — they exercise the
    gates and the commit, not the walk that produced the numbers."""
    trend = {
        t: MS.TrendRead(price=100.0, moving_average=110.0 if t in below else 90.0, below=t in below)
        for t in MS.TREND_SLEEVES
    }
    return MS.Decision(
        date=pd.Timestamp("2026-08-03"),
        signalled=signalled,
        held=held,
        pending=pending,
        pending_count=pending_count,
        spread=2.4,
        spread_median=1.8,
        slope=0.4,
        slope_median=1.1,
        trend=trend,
        target=target if target is not None else dict(MS.BOOKS[held]),
        changed=True,
    )


async def _seed(db: InvestmentDB) -> None:
    """The minimum the live path reads: the 5 stack tickers as allowed, the 3
    book Portfolios with their own caps, and — see `_seed_defender` — the
    bridge defender whose NAV index supplies the trading calendar."""
    for ticker, cls in (
        ("SPY", "equities"),
        ("IWN", "equities"),
        ("GLD", "gold-commodities"),
        ("VCIT", "bonds"),
        ("IEF", "bonds"),
    ):
        await db.command(
            "INSERT INTO allowed_tickers (ticker, asset_class, currency, source, transform, "
            "active) VALUES (:t, :c, 'USD', 'yahoo', 'none', 1)",
            t=ticker,
            c=cls,
        )
    await db.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('market-signal', 'MS', 1, 't', '2026-01-01')"
    )
    for book_id in MS.BOOK_PORTFOLIO_IDS.values():
        await db.command(
            "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, "
            "benchmark, allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, "
            "updated_at) VALUES (:id, :id, 'market-signal', 0, 1, 'CHF', 'b', '{}', -25.0, 50.0, "
            "'accumulation', 't', '2026-01-01')",
            id=book_id,
        )


async def _seed_defender(db: InvestmentDB) -> None:
    """The bridge defender and its NAV rows.

    Not incidental scaffolding — a REAL dependency of the live path, pinned here
    so it cannot be forgotten: `run_market_signal` takes its trading calendar
    from `replay._book_calendar`, i.e. from the defender's NAV index, and both
    the monthly decision dates and the signal alignment step on that calendar.
    Deliberate (it is what keeps the live clock identical to the backtest's), but
    it means the market-signal path cannot run on a DB with no NAV-backfilled
    defender — which matters when the retained Dalio bridge is eventually
    retired (docs/V1_STRATEGY.md Step 6)."""
    await db.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) VALUES "
        "('def-pf', 'D', 'market-signal', 1, 1, 'CHF', 'b', '{\"SPY\": 100}', -25.0, 100.0, "
        "'accumulation', 't', '2026-01-01')"
    )
    await db.command(
        "INSERT INTO user_profile (user_id, currency, benchmark, max_drawdown_pct, "
        "max_single_asset_pct, phase, created_at, updated_at) VALUES ('u', 'CHF', 'b', -25.0, "
        "50.0, 'accumulation', '2026-01-01', '2026-01-01')"
    )
    await db.append_ts_batch(
        "portfolio_nav",
        [
            {
                "portfolio_id": "def-pf",
                "currency": "USD",
                "ts": (START + timedelta(days=i)).isoformat(),
                "nav": 100.0 + i * 0.1,
            }
            for i in range(DAYS)
        ],
    )


async def _series(db: InvestmentDB, ticker: str, values: list[float]) -> None:
    """A daily series on CALENDAR days. The trading calendar the live path steps
    on is derived from these prices, so a 7-day week is fine here: what the
    tests exercise is the decision, not the exchange holiday map."""
    rows = [
        {
            "ts": (START + timedelta(days=i)).isoformat(),
            "ticker": ticker,
            "asset_class": "equities",
            "currency": "USD",
            "level": v,
        }
        for i, v in enumerate(values)
    ]
    await db.append_ts_batch("market_data", rows)


async def _market(
    db: InvestmentDB,
    *,
    spread: float,
    slope: float,
    spy_trend: str = "up",
    spread_wide: bool = False,
) -> None:
    """Daily series for the whole fixture window. `spread`/`slope` are held flat
    so the trailing median equals the value; the classifier's comparison is
    strict (`>`), so a flat spread reads TIGHT and the slope decides the book.
    `spread_wide` ramps the spread instead — a monotonic rise sits above its own
    trailing median at every point, which is the WIDE state (and the only book
    holding SPY, hence the only one the 200d overlay can act on)."""
    rising = [100.0 + i * 0.1 for i in range(DAYS)]
    # A sleeve that rises then falls hard ends BELOW its own 200d MA.
    falling = [100.0 + i * 0.1 for i in range(DAYS - 60)] + [
        100.0 + (DAYS - 60) * 0.1 - i * 1.5 for i in range(60)
    ]
    await _series(db, "SPY", falling if spy_trend == "down" else rising)
    for ticker in ("IWN", "GLD", "VCIT", "IEF"):
        await _series(db, ticker, rising)
    await _series(db, "^IRX", [2.0] * DAYS)
    spread_series = [spread + i * 0.002 for i in range(DAYS)] if spread_wide else [spread] * DAYS
    await _series(db, MS.CREDIT_SPREAD, spread_series)
    await _series(db, MS.YIELD_SLOPE, [slope] * DAYS)
    await _seed_defender(db)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "ms.db")
    await _seed(conn)
    yield conn
    await conn.close()


# -- pure: the hysteresis state machine, shared by replay and live path ------


def test_advance_hysteresis_first_decision_commits_immediately() -> None:
    assert MS.advance_hysteresis(None, None, 0, WIDE) == (WIDE, None, 0)


def test_advance_hysteresis_holds_until_confirmed() -> None:
    held, pending, count = WIDE, None, 0
    for expected in range(1, MS.CONFIRM_DECISIONS):
        held, pending, count = MS.advance_hysteresis(held, pending, count, TIGHT_STEEP)
        assert (held, pending, count) == (WIDE, TIGHT_STEEP, expected)
    held, pending, count = MS.advance_hysteresis(held, pending, count, TIGHT_STEEP)
    assert (held, pending, count) == (TIGHT_STEEP, None, 0)


def test_advance_hysteresis_flicker_resets_the_count() -> None:
    # A candidate that flickers back to the held book abandons its progress.
    held, pending, count = MS.advance_hysteresis(WIDE, TIGHT_STEEP, 2, WIDE)
    assert (held, pending, count) == (WIDE, None, 0)


def test_build_targets_is_a_projection_of_the_walk() -> None:
    """The anti-drift contract: the replay's change-point map is exactly the
    changed entries of the walk the live path reads. If these two ever diverge,
    the live decision stops being the one the backtest validated."""
    idx = pd.date_range("2020-01-31", periods=8, freq="ME")
    spread = pd.Series([2.0] * 4 + [1.0] * 4, index=idx)
    median = pd.Series([1.5] * 8, index=idx)
    slope = pd.Series([2.0] * 8, index=idx)
    slope_median = pd.Series([1.0] * 8, index=idx)
    prices = {t: pd.Series([100.0] * 8, index=idx) for t in MS.TREND_SLEEVES}
    mas = {t: pd.Series([1.0] * 8, index=idx) for t in MS.TREND_SLEEVES}
    args = (list(idx), spread, slope, median, slope_median, mas, prices)

    walk = MS.walk_decisions(*args)
    assert len(walk) == 8  # every decision journalled, not just the changes
    assert MS.build_targets(*args) == {d.date: d.target for d in walk if d.changed}


# -- the gate set that binds the live decision -------------------------------


def test_market_signal_gates_exempt_the_trend_haven_but_bind_every_other_sleeve() -> None:
    caps = MS.Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)
    allowed = frozenset(MS.STACK_TICKERS)
    # Risk-off: both sleeves redirected, IEF at 90 — passes, by ADR-007 (a).
    assert W.market_signal_gates({"IEF": 90.0, "IWN": 10.0}, {}, caps, allowed).passed
    # The same 90 on a NON-haven sleeve is refused.
    assert (
        W.market_signal_gates({"SPY": 90.0, "IWN": 10.0}, {}, caps, allowed).failed_gate
        == "max_single_asset_pct"
    )
    # An unknown sleeve, and a book that does not sum to 100.
    assert (
        W.market_signal_gates({"IEF": 50.0, "TSLA": 50.0}, {}, caps, allowed).failed_gate
        == "allowed_tickers"
    )
    assert (
        W.market_signal_gates({"IEF": 40.0}, {}, caps, allowed).failed_gate
        == "allocation_sums_to_100"
    )


def test_no_reachable_book_state_can_be_refused() -> None:
    """ADR-009's central finding, pinned so it cannot quietly stop being true:
    across every book x overlay state the strategy can actually reach, no gate
    refuses. These are REGRESSION GUARDS against a config or code change, not a
    safety control on the allocation — and a future reader must not mistake the
    one for the other.

    THE STATES ARE DERIVED FROM THE CHECKED SET, not listed. Hand-listed, they
    were {}, {SPY}, {GLD}, {SPY,GLD} — four subsets of a two-sleeve overlay,
    frozen there while the overlay grew to check IWN and then the haven itself.
    The unlisted states were not rare: `IEF below trend` is the one that sends
    the book to 100% cash, it was refused by the single-asset cap, and it hit
    four of the seven 2022 dates on the M8b run of 2026-08-08 — a state this
    test asserted was unreachable while the strategy reached it monthly. A
    guard that enumerates by hand stops guarding the day the code grows."""
    caps = MS.Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)
    allowed = frozenset(MS.STACK_TICKERS)
    checked = (*MS.TREND_SLEEVES, MS.TREND_HAVEN)
    states = [
        (book, frozenset(below))
        for book in MS.BOOKS
        for r in range(len(checked) + 1)
        for below in itertools.combinations(checked, r)
    ]
    assert len(states) == len(MS.BOOKS) * 2 ** len(checked)
    for book, below in states:
        target = MS.apply_trend_overlay(MS.BOOKS[book], below)
        assert W.market_signal_gates(target, {}, caps, allowed).passed, (book, below)

    # ...but a DEACTIVATED ticker does trip them, which is what they are for.
    assert not W.market_signal_gates(
        dict(MS.BOOKS["credit-spread-wide"]), {}, caps, allowed - {"IWN"}
    ).passed


def test_turnover_and_min_change_do_not_bind_a_book_switch() -> None:
    """The reallocation-blend knobs would block the strategy outright: the books
    barely overlap, so a switch is a ~90-100% turnover move against a 30% cap."""
    caps = MS.Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)
    wide, steep = MS.BOOKS[WIDE], MS.BOOKS[TIGHT_STEEP]
    from investment.mechanical.gates import turnover_pct

    assert turnover_pct(wide, steep) > 30.0  # would fail the realloc gate
    assert W.market_signal_gates(dict(steep), {}, caps, frozenset(MS.STACK_TICKERS)).passed


@pytest.mark.parametrize("absent", [MS.CREDIT_SPREAD, MS.YIELD_SLOPE])
async def test_an_absent_signal_series_refuses_the_run_instead_of_defaulting(
    db: InvestmentDB, absent: str
) -> None:
    """The silent failure the sleeve guard already covers, on the two inputs that
    PICK the book. With no `BAA10Y` rows the median is NaN, `classify_regime`
    treats that as warm-up and answers `credit-spread-wide` — the 90%-equity book
    — so a dead FRED feed would have quietly allocated the most aggressive book
    on no signal at all, and `knowable_at: None` reads identically to a genuine
    warm-up. The live cycle has no pre-check of its own (the seed's
    `_missing_stack_series` does not run there), so the refusal belongs here."""
    await _market(db, spread=1.0, slope=2.0)
    await db.command("DELETE FROM market_data WHERE ticker = :t", t=absent)
    with pytest.raises(ValueError, match=f"missing signal series.*{absent}"):
        await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))


# -- the live transaction ----------------------------------------------------


async def test_first_decision_emits_a_market_signal_proposal_eventlog_first(
    db: InvestmentDB,
) -> None:
    await _market(db, spread=1.0, slope=2.0)
    result = await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))

    assert result.emitted, result.gate_outcome
    assert result.held_allocation == {}  # nothing held before the opening entry

    rows = await db.query("SELECT * FROM proposal")
    assert len(rows) == 1
    proposal = rows[0]
    assert proposal["proposal_type"] == "market-signal"
    assert proposal["defender_id"] == MS.BOOK_PORTFOLIO_IDS[result.decision.held]
    assert proposal["recommendation"] == "paper-test"
    assert proposal["paper_started"] == "2025-11-03"
    # ADR-008: the ranked-duel columns do not apply to this path.
    assert proposal["challenger_id"] is None
    assert proposal["defender_rank"] is None
    assert proposal["gap"] is None

    # EventLog-first, and BOTH events land: the decision journal and the proposal.
    events = await db.query("SELECT id, type, source_id FROM event_log ORDER BY id")
    types = [e["type"] for e in events]
    assert types == [W.MARKET_SIGNAL_EVENT, W.PROPOSAL_EVENT]
    assert events[-1]["source_id"] == proposal["id"]


async def test_the_stack_row_tracks_the_last_emitted_proposal(db: InvestmentDB) -> None:
    """The two notions that both used to be called "held", pinned so they cannot
    silently collapse into one.

    `portfolio.allocation` on `ms-stack` is the BOOK IN FORCE — what the strategy
    is in. `snapshots.py` and the ranking read it, and the paper NAV models the
    stack as continuously invested since 1991, so it is never empty. The
    market-signal Proposal chain is the OWNER POSITION — empty until the opening
    entry, because V1 executes nothing and the owner holds what the digest told
    them to buy. Before the first proposal the two legitimately DISAGREE (the
    seeded warm-up book against nothing held), which is why the decision reads
    the proposal chain and not the row. From the first emitted proposal onward
    they must agree exactly: `dispose_market_signal` writes both inside one
    transaction."""
    await db.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) VALUES "
        "(:id, 'S', 'market-signal', 0, 1, 'CHF', 'b', :alloc, -25.0, 50.0, 'accumulation', 't', "
        "'2026-01-01')",
        id=MS.STACK_PORTFOLIO_ID,
        alloc=json.dumps(MS.BOOKS[WIDE]),  # the seeded initial value
    )
    await _market(db, spread=1.0, slope=2.0)

    async def stack_row() -> dict[str, float]:
        rows = await db.query(
            "SELECT allocation FROM portfolio WHERE id = :i", i=MS.STACK_PORTFOLIO_ID
        )
        return {str(k): float(v) for k, v in json.loads(str(rows[0]["allocation"])).items()}

    # Before the opening entry: the row says a book, the decision says nothing
    # held — and that gap is what makes the opening proposal happen at all.
    assert await stack_row() == MS.BOOKS[WIDE]
    assert await MSC.held_allocation(db) == {}

    result = await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))
    assert result.emitted
    proposal = (await db.query("SELECT proposed_allocation FROM proposal"))[0]
    assert await stack_row() == json.loads(str(proposal["proposed_allocation"]))
    assert await MSC.held_allocation(db) == await stack_row()


async def test_proposal_records_the_full_audit_record(db: InvestmentDB) -> None:
    """The finding's explicit requirement: held allocation, effective target,
    the signals, the dates the inputs became knowable, and the below-trend
    sleeves — all reconstructible from the proposal row alone."""
    await _market(db, spread=1.0, slope=2.0, spy_trend="down", spread_wide=True)
    result = await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))
    assert result.emitted
    assert result.decision is not None and result.decision.held == WIDE

    row = (await db.query("SELECT * FROM proposal"))[0]
    context = json.loads(str(row["market_context"]))

    assert context["held_allocation"] == {}
    assert context["target_allocation"] == json.loads(str(row["proposed_allocation"]))
    assert context["cadence"] == "monthly"
    assert context["held_book"] in MS.BOOKS
    assert context["held_book_portfolio_id"] == row["defender_id"]

    for ticker in (MS.CREDIT_SPREAD, MS.YIELD_SLOPE):
        signal = context["signals"][ticker]
        assert signal["value"] is not None
        # ADR-003: every input says when it became knowable, and never later
        # than the decision it fed.
        assert signal["knowable_at"] <= context["decision_date"]

    overlay = context["trend_overlay"]
    assert overlay["window_days"] == MS.MA_WINDOW_DAYS
    assert "SPY" in overlay["below_trend"]  # the fixture drives SPY under its MA
    assert overlay["sleeves"]["SPY"]["price"] < overlay["sleeves"]["SPY"]["moving_average"]
    # ...and the overlay actually moved the money it said it moved.
    assert "SPY" not in context["target_allocation"]
    assert context["target_allocation"][MS.TREND_HAVEN] >= MS.BOOKS[context["held_book"]]["SPY"]


async def test_second_run_in_the_same_month_is_a_no_op(db: InvestmentDB) -> None:
    await _market(db, spread=1.0, slope=2.0)
    first = await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))
    assert first.emitted

    # A week later, same monthly anchor: nothing is written a second time.
    again = await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 10))
    assert again.skipped_reason is not None
    assert again.proposal_id is None
    assert len(await db.query("SELECT id FROM proposal")) == 1
    assert len(await db.query("SELECT id FROM event_log")) == 2


async def test_unchanged_month_journals_the_decision_without_a_proposal(
    db: InvestmentDB,
) -> None:
    """2.8 book changes a year: most months hold. The decision must still be
    recorded — it advanced the hysteresis and re-read the overlay."""
    await _market(db, spread=1.0, slope=2.0)
    first = await MSC.run_market_signal_cycle(db, USER, today=date(2025, 10, 6))
    assert first.emitted
    assert first.decision is not None

    second = await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))
    assert second.skipped_reason is None
    assert second.proposal_id is None
    # A holding month PASSES its gates — it is the strategy working, not a
    # refusal. `emitted`/`blocked` are the two things a caller may ask.
    assert second.gate_outcome is not None and second.gate_outcome.passed
    assert not second.emitted and not second.blocked
    assert second.held_allocation == first.decision.target

    assert len(await db.query("SELECT id FROM proposal")) == 1
    journal = await db.query(
        "SELECT payload FROM event_log WHERE type = :t ORDER BY id", t=W.MARKET_SIGNAL_EVENT
    )
    assert len(journal) == 2
    latest = json.loads(str(journal[-1]["payload"]))
    assert latest["moves"] is False
    assert latest["proposal_id"] is None


async def test_a_blocked_decision_journals_and_emits_nothing(db: InvestmentDB) -> None:
    """A refused decision leaves an audit trail but no Proposal — "Worker
    proposes, Writeback disposes" holds for the mechanical path too."""
    decision = _decision(target={"SPY": 60.0, "IWN": 40.0})  # 60 breaches the 50 cap
    outcome, proposal_id = await W.dispose_market_signal(
        db,
        decision,
        {},
        {"decision_date": "2026-08-03"},
        USER,
        today=date(2026, 8, 3),
    )
    assert not outcome.passed
    assert outcome.failed_gate == "max_single_asset_pct"
    assert proposal_id is None
    assert await db.query("SELECT id FROM proposal") == []

    journal = await db.query(
        "SELECT payload FROM event_log WHERE type = :t", t=W.MARKET_SIGNAL_EVENT
    )
    assert json.loads(str(journal[0]["payload"]))["gate"] == "max_single_asset_pct"


async def test_emit_is_keyed_on_what_is_held_not_on_the_walk(db: InvestmentDB) -> None:
    """Self-healing: after a block, the stack is in the wrong book while the
    walk reports "no change". Keying the emit on the HELD allocation re-proposes
    what the blocked decision could not."""
    held = dict(MS.BOOKS[TIGHT_STEEP])
    decision = _decision(held=WIDE)  # target is the WIDE book, held is the steep one
    outcome, proposal_id = await W.dispose_market_signal(
        db,
        decision,
        held,
        {"decision_date": "2026-08-03"},
        USER,
        today=date(2026, 8, 3),
    )
    assert outcome.passed and proposal_id is not None

    # ...and a decision that lands on exactly what is held emits nothing.
    outcome, proposal_id = await W.dispose_market_signal(
        db,
        _decision(held=WIDE),
        dict(MS.BOOKS[WIDE]),
        {"decision_date": "2026-09-01"},
        USER,
        today=date(2026, 9, 1),
    )
    assert outcome.passed and proposal_id is None


async def test_decision_then_digest_through_the_chain_runner(db: InvestmentDB) -> None:
    """The tail of the live path: the decision composes as a chain step, and the
    digest then renders it FROM THE DB ALONE — the signals with their knowable
    dates, the overlay, and the move. This is what the owner actually receives."""
    await _market(db, spread=1.0, slope=2.0, spy_trend="down", spread_wide=True)
    rendered: dict[str, str] = {}

    async def decide() -> None:
        await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))

    async def digest() -> None:
        rendered["text"] = await build_digest(db)

    result = await run_chain(db, [("market-signal", decide), ("digest", digest)], "run-1")
    assert result.ok and result.completed == ["market-signal", "digest"]

    text = rendered["text"]
    assert "🧭 Market-signal decision (paper-test)" in text
    assert "credit-spread-wide" in text
    assert f"{MS.CREDIT_SPREAD} " in text and "vs 10y median" in text
    assert "knowable 2025-11-01" in text
    assert "200d overlay: SPY below trend" in text
    assert f"{MS.TREND_HAVEN} 0→50" in text  # the overlay's redirect, in the move line
    assert "Paper-tests in progress: 1" in text


async def test_a_bridge_reallocation_the_same_monday_cannot_hide_the_decision(
    db: InvestmentDB,
) -> None:
    """The two paths write on the same Monday — the market-signal decision at
    08:55, UC8's reallocation at 09:00 — with the same `Proposal.date`. Reading
    the digest's single proposal slot by `date DESC, created_at DESC` therefore
    handed the slot to the BRIDGE and dropped the adopted strategy's decision,
    on precisely the months it moves money and the owner has an order to place.
    Both must appear."""
    await _market(db, spread=1.0, slope=2.0, spy_trend="down", spread_wide=True)
    await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))

    # The bridge's reallocation, committed later the same morning.
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
        "recommendation, market_context, reasoning, paper_started, trace, created_at) VALUES "
        "('realloc-1', '2025-11-03', 'reallocation', 'def-pf', '{\"SPY\": 100}', 'paper-test', "
        "'{}', 'bridge tilt', '2025-11-03', 't', '2999-01-01T09:00:00+00:00')"
    )

    # Rendered AS OF that Monday: the bridge slot is windowed on the digest's
    # own date (`DIGEST_PROPOSAL_WINDOW_DAYS`), so a digest built today would
    # correctly drop a 2025 proposal rather than reprint it as this week's.
    text = await build_digest(db, today=date(2025, 11, 3))
    assert "🧭 Market-signal decision (paper-test)" in text
    assert f"{MS.TREND_HAVEN} 0→50" in text
    assert "🔧 Reallocation proposal" in text and "bridge tilt" in text


async def test_the_digest_shows_the_decision_on_a_month_that_does_not_move(
    db: InvestmentDB,
) -> None:
    """~9 months a year the stack holds, so no Proposal exists at all. The
    decision still happened — it advanced the hysteresis and re-read the 200d
    overlay — and the journal is what makes it renderable."""
    await _market(db, spread=1.0, slope=2.0)
    await MSC.run_market_signal_cycle(db, USER, today=date(2025, 10, 6))
    second = await MSC.run_market_signal_cycle(db, USER, today=date(2025, 11, 3))
    assert not second.emitted

    text = await build_digest(db)
    assert "🧭 Market-signal decision (paper-test)" in text
    assert "no change — the stack holds its book" in text
    assert "decided 2025-11-01" in text


async def test_a_blocked_decision_is_loud_in_the_digest(db: InvestmentDB) -> None:
    """A refused decision writes no Proposal, so without this line it renders
    exactly like a month that legitimately held — while the stack is in fact
    frozen off its target."""
    await W.dispose_market_signal(
        db,
        _decision(target={"SPY": 60.0, "IWN": 40.0}),  # 60 breaches the 50 cap
        {},
        {"decision_date": "2026-08-03", "held_book": WIDE},
        USER,
        today=date(2026, 8, 3),
    )
    text = await build_digest(db)
    assert "BLOCKED by gate 'max_single_asset_pct'" in text
    assert "FROZEN in its previous book, not in the one named above" in text
    # ...and the header must not present that book as a POSITION: the move
    # never happened, so it is a target, not what is held.
    assert f"target book {WIDE}" in text


# -- the outcome end: scored against what was HELD ---------------------------


async def test_outcome_scores_against_the_held_allocation_not_the_static_book(
    db: InvestmentDB,
) -> None:
    """A market-signal proposal's incumbent is the POST-overlay book it actually
    held, read off `market_context` — never the Portfolio's static row, which
    carries the base book the overlay may have already moved out of."""
    held = {"IEF": 90.0, "IWN": 10.0}
    proposal = {
        "id": "p1",
        "proposal_type": "market-signal",
        "defender_id": "ms-growth-book",
        "date": "2026-01-05",
        "market_context": json.dumps({"held_allocation": held}),
    }
    assert await outcomes._incumbent_allocation(db, proposal) == held


async def test_opening_proposal_is_scored_against_the_best_available_portfolio(
    db: InvestmentDB,
) -> None:
    """Owner decision: the stack's first entry has no incumbent, so it is judged
    against the BEST-ranked alternative — the thing that would otherwise have
    been held — not against cash, which would be an easier and less meaningful
    bar. `ms-stack` is excluded so the comparison cannot be with itself."""
    best = {"SPY": 60.0, "IEF": 40.0}
    for rank, (pid, alloc) in enumerate(
        [(MS.STACK_PORTFOLIO_ID, {"IWN": 100.0}), ("def-pf", best), ("other", {"GLD": 100.0})], 1
    ):
        await db.command(
            "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
            "allocation, rank, market_context, recommendation, trace) VALUES ('2026-01-01', :p, 0, "
            "'market-signal', :a, :r, '{}', 'maintain', 't')",
            p=pid,
            a=json.dumps(alloc),
            r=rank,
        )
    opening = {
        "id": "p1",
        "proposal_type": "market-signal",
        "defender_id": "ms-growth-book",
        "date": "2026-01-05",
        "market_context": json.dumps({"held_allocation": {}}),
    }
    # rank 1 is the stack itself and is skipped; rank 2 is the real alternative.
    assert await outcomes._incumbent_allocation(db, opening) == best


async def test_outcome_reads_citations_from_proposal_cites_for_a_market_signal_row(
    db: InvestmentDB,
) -> None:
    """The old branch keyed on `== 'reallocation'`, sending a market-signal row
    (challenger_id NULL) down the switch query. It must read `proposal_cites` —
    empty here, since the mechanical decision cites nothing."""
    proposal = {"id": "p1", "proposal_type": "market-signal", "challenger_id": None}
    assert await outcomes._cited_invariants(db, proposal) == []


async def test_evaluated_market_signal_proposal_reaches_a_verdict(db: InvestmentDB) -> None:
    """The full tail: a proposal past its +12w window gets an outcome written,
    so a market-signal decision is measurable — not stuck pending forever."""
    await _market(db, spread=1.0, slope=2.0)
    await db.command(
        "INSERT INTO system_thresholds (key, value, updated_at) VALUES "
        "('proposal_outcome_weeks', 12, 't'), ('replay_cost_bps', 10, 't'), "
        "('recency_half_life_days', 365, 't')"
    )
    proposed = {"IEF": 50.0, "VCIT": 50.0}
    await db.command(
        "INSERT INTO proposal (id, date, proposal_type, defender_id, proposed_allocation, "
        "recommendation, market_context, reasoning, trace, created_at) VALUES ('p1', "
        "'2024-06-03', 'market-signal', 'ms-growth-book', :alloc, 'paper-test', :ctx, 'r', "
        "'t', '2024-06-03')",
        alloc=json.dumps(proposed),
        ctx=json.dumps({"held_allocation": {"SPY": 50.0, "IWN": 40.0, "GLD": 10.0}}),
    )

    results = await outcomes.evaluate_proposals(db, today=date(2025, 12, 1))
    assert len(results) == 1
    assert results[0].skipped_reason is None, results[0].skipped_reason
    assert results[0].verdict in {"won", "lost"}
    row = (await db.query("SELECT outcome FROM proposal WHERE id = 'p1'"))[0]
    assert json.loads(str(row["outcome"]))["verdict"] == results[0].verdict


def test_a_malformed_held_book_is_refused_loudly_not_read_as_no_change() -> None:
    """`dispose_market_signal` decides whether to emit on
    `max_allocation_change_pts(held, target) > 0`, so a NaN in the HELD book
    makes that comparison False and a real rotation would emit nothing while the
    journal recorded `moves: False` — the month's entire output lost in silence.
    Whether it bites depends on set-iteration order (3 of 10 ticker spellings
    trip it), which is exactly why it must be refused rather than measured.

    Refused, not treated as "nothing held": a refusal is LOUD in the digest
    (ADR-009 renders a blocked decision with a 🚨), where a guessed incumbent
    would quietly misprice the +12w verdict."""
    caps = Caps(max_single_asset_pct=50.0, max_drawdown_pct=-25.0)
    allowed = frozenset(MS.STACK_TICKERS)
    target = dict(MS.BOOKS["credit-spread-tight-yield-curve-steep"])

    assert W.market_signal_gates(target, {"SPY": 50.0, "IWN": 50.0}, caps, allowed).passed
    assert (
        W.market_signal_gates(target, {"SPY": 50.0, "IWN": float("nan")}, caps, allowed).failed_gate
        == "held_allocation_well_formed"
    )
    # the OPENING entry holds nothing, and that is not malformed
    assert W.market_signal_gates(target, {}, caps, allowed).passed
