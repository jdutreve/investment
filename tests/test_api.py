"""The localhost API (`ops/api.py`, ADR-005, docs/TASKS.md Task 6ter.1).

Real throwaway SQLite and a real aiohttp server on an ephemeral port. What is
pinned here is what the browser front is allowed to be: token-gated on `/api/*`,
never a second write path, and never a second assembly of numbers the digest
already assembles.

The RENDERING is not tested here — that is the React app's business, and a test
asserting on markup would break on every restyle while proving nothing about the
data. What matters is that the front has nothing else to call and gets the same
figures every other front gets.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from investment.db.sqlite import InvestmentDB
from investment.ops import api
from investment.ops.run_lock import RunLock
from investment.runtime import AgentRuntime

SNAPSHOT_DATE = "2026-08-09"


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "api.db")
    await conn.command(
        "INSERT INTO user_profile (user_id, currency, benchmark, max_drawdown_pct, "
        "max_single_asset_pct, phase, created_at, updated_at) VALUES ('u', 'USD', 'b', -25.0, "
        "60.0, 'accumulation', '2026-01-01', '2026-01-01')"
    )
    await conn.command(
        "INSERT INTO framework (id, name, enabled, trace, created_at) "
        "VALUES ('4s', 'Four Seasons', 1, 't', '2026-01-01')"
    )
    await conn.command(
        "INSERT INTO strategy (id, title, description, framework_id, status, enabled, "
        "conviction, conditions, source, trace, created_at, updated_at) VALUES "
        "('momentum-macro', 'M', 'd', '4s', 'active', 1, 50, 'always', 'corpus', 't', "
        "'2026-01-01', '2026-01-01')"
    )
    await conn.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, trace, updated_at) VALUES "
        "('ms-stack', 'Stack', '4s', 1, 1, 'USD', 'SPY', '{\"SPY\": 60, \"IEF\": 40}', "
        "-25.0, 60.0, 'accumulation', 't', '2026-01-01')"
    )
    for day in range(1, 8):
        await conn.command(
            "INSERT INTO portfolio_nav (portfolio_id, currency, ts, nav) "
            "VALUES ('ms-stack', 'USD', :ts, :nav)",
            ts=f"2026-08-0{day}",
            nav=100.0 + day,
        )
    await conn.command(
        "UPDATE portfolio SET return_1y = 0.042, sharpe_rolling = 1.11, "
        "sortino_rolling = 1.63, calmar_rolling = 1.94 WHERE id = 'ms-stack'"
    )
    # One of the four books the stack switches between — DISABLED, since the
    # stack holds one allocation at a time rather than four positions, and
    # never surfaces in `portfolio_weekly_snapshot` (ranking) for that reason.
    await conn.command(
        "INSERT INTO portfolio (id, name, framework_id, defender, enabled, currency, benchmark, "
        "allocation, max_drawdown_rule, max_single_asset_pct, phase, return_1y, sharpe_rolling, "
        "sortino_rolling, calmar_rolling, trace, updated_at) VALUES "
        "('ms-slowdown-book', 'Slowdown Book', '4s', 0, 0, 'USD', 'SPY', "
        "'{\"VCIT\": 50, \"IEF\": 40}', -25.0, 60.0, 'accumulation', 0.072, 0.25, 0.36, 0.77, "
        "'t', '2026-01-01')"
    )
    await conn.append_event(
        type="MarketSignalDecisionEvent",
        source_uc="UC8",
        source_id=None,
        payload={
            "decision_date": SNAPSHOT_DATE,
            "held_book": "credit-spread-tight-yield-curve-steep",
            "gate": "passed",
            "held_allocation": {"VCIT": 50, "IEF": 40},
            "target_allocation": {"VCIT": 50, "IEF": 40},
        },
    )
    await conn.command(
        "INSERT INTO portfolio_weekly_snapshot (date, portfolio_id, defender, framework_id, "
        "allocation, rank, sortino_rolling, calmar_rolling, market_context, recommendation, "
        "trace) VALUES (:d, 'ms-stack', 1, '4s', '{\"SPY\": 60}', 1, 1.4, 1.2, '{}', "
        "'maintain', 't')",
        d=SNAPSHOT_DATE,
    )
    yield conn
    await conn.close()


def _runtime(db: InvestmentDB, tmp_path: Path, lock: RunLock | None = None) -> AgentRuntime:
    """Only what the API touches: the connection, the lock, and the two
    settings it reads. Building the agents would need an API key and 90MB of
    model, and no endpoint under test reaches them."""
    settings = SimpleNamespace(
        db_path=tmp_path / "api.db", inbox_path=tmp_path / "inbox", local_api_port=0
    )
    return cast(AgentRuntime, SimpleNamespace(db=db, settings=settings, lock=lock or RunLock()))


@pytest.fixture
async def client(db: InvestmentDB, tmp_path: Path) -> AsyncIterator[TestClient[Any, Any]]:
    app = api.build_app(_runtime(db, tmp_path))
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


def _auth(app: web.Application) -> dict[str, str]:
    return {api.TOKEN_HEADER: app[api.TOKEN]}


# -- the token gate ---------------------------------------------------------


async def test_api_without_token_is_403(client: TestClient[Any, Any]) -> None:
    """Localhost binding does not protect against the owner's own browser: any
    page they visit can POST here. The header is the whole defence."""
    for path in ("/api/status", "/api/overview", "/api/ranking", "/api/stack"):
        response = await client.get(path)
        assert response.status == 403, path


async def test_wrong_token_is_403_on_the_write_surface(client: TestClient[Any, Any]) -> None:
    response = await client.post(
        "/api/cmd", json={"command": "cap", "args": {"pct": 30}}, headers={api.TOKEN_HEADER: "no"}
    )
    assert response.status == 403


async def test_token_is_stable_across_restarts_and_chmod_600(tmp_path: Path) -> None:
    """A token regenerated per launch would log the owner out of an open tab on
    every wake — and the agent wakes many times a day on a laptop."""
    db_path = tmp_path / "sub" / "investment.db"
    first = api.ensure_token(db_path)
    assert api.ensure_token(db_path) == first
    token_file = db_path.parent / api.TOKEN_FILENAME
    assert token_file.stat().st_mode & 0o777 == 0o600


async def test_the_dashboard_page_needs_no_token(client: TestClient[Any, Any]) -> None:
    """The browser must fetch the page before it can know a token. The page is
    what carries the token in — see the module docstring on why an endpoint
    handing it out would be strictly worse."""
    response = await client.get("/")
    assert response.status in (200, 503)  # 503 = built assets absent, still not 403


# -- reads: the same numbers as every other front ---------------------------


async def test_status_reports_the_lock_holder(db: InvestmentDB, tmp_path: Path) -> None:
    lock = RunLock()
    app = api.build_app(_runtime(db, tmp_path, lock))
    async with TestClient(TestServer(app)) as client:
        async with lock.hold("weekly-chain"):
            response = await client.get("/api/status", headers=_auth(app))
            assert response.status == 200
            assert "weekly-chain" in str((await response.json())["running"])

        idle = await (await client.get("/api/status", headers=_auth(app))).json()
        # None, not the word "idle": a front decides how to say "nothing is
        # running" (`commands.StatusFacts`).
        assert idle["running"] is None


async def test_overview_serves_the_digest_inputs(client: TestClient[Any, Any]) -> None:
    """The Overview is specified as the digest with a better layout, and this is
    what makes that true by construction rather than by hand: the endpoint
    serves `collect_digest_inputs`, the same call the weekly Telegram digest
    renders."""
    app = cast(web.Application, client.app)
    payload = await (await client.get("/api/overview", headers=_auth(app))).json()
    from investment.telegram.digest import DigestInputs

    assert set(payload) == set(DigestInputs.__annotations__)


async def test_ranking_keeps_the_stored_order_and_lists_its_dates(
    client: TestClient[Any, Any],
) -> None:
    app = cast(web.Application, client.app)
    payload = await (await client.get("/api/ranking", headers=_auth(app))).json()
    assert payload["date"] == SNAPSHOT_DATE
    assert payload["available_dates"] == [SNAPSHOT_DATE]
    assert [r["rank"] for r in payload["rows"]] == [1]
    # JSON columns arrive as objects, not as double-encoded strings.
    assert payload["rows"][0]["allocation"] == {"SPY": 60}


async def test_a_missing_portfolio_is_404_not_an_empty_page(client: TestClient[Any, Any]) -> None:
    app = cast(web.Application, client.app)
    response = await client.get("/api/portfolio/does-not-exist", headers=_auth(app))
    assert response.status == 404


async def test_nav_thinning_keeps_the_first_and_last_points(
    db: InvestmentDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last point is the one the owner looks at, and a bare stride drops it
    whenever the length is not a multiple of the stride."""
    monkeypatch.setattr(api, "NAV_MAX_POINTS", 3)
    series = await api._nav_series(db, "ms-stack")
    full = await db.query(
        "SELECT ts, nav FROM portfolio_nav WHERE portfolio_id = 'ms-stack' ORDER BY ts ASC"
    )
    assert len(series) < len(full)
    assert series[0]["ts"] == full[0]["ts"]
    assert series[-1]["ts"] == full[-1]["ts"]


async def test_stack_carries_return_and_sharpe_for_itself_and_its_books(
    client: TestClient[Any, Any],
) -> None:
    """Owner feedback (2026-08-15, after the first visual pass): the Stack page
    showed the decision but never what it earned, on the stack or on the other
    books it could have held. Both are in this one payload — no page should
    need four extra round trips to `/api/portfolio` to draw the comparison."""
    app = cast(web.Application, client.app)
    payload = await (await client.get("/api/stack", headers=_auth(app))).json()

    assert payload["stack_portfolio"]["return_1y"] == pytest.approx(0.042)
    assert payload["stack_portfolio"]["sharpe_rolling"] == pytest.approx(1.11)

    books = {b["signal_state"]: b for b in payload["books"]}
    assert "credit-spread-tight-yield-curve-steep" in books
    held = books["credit-spread-tight-yield-curve-steep"]
    assert held["id"] == "ms-slowdown-book"
    assert held["return_1y"] == pytest.approx(0.072)
    assert held["sharpe_rolling"] == pytest.approx(0.25)
    # A book with no row in this throwaway DB (three of the four were never
    # inserted) is simply absent, not a null-filled placeholder row.
    assert len(books) == 1

    # `live_trend` needs a priced series for every STACK_TICKERS sleeve
    # (`market_signal.load_series` raises otherwise) — this throwaway DB has
    # none, and the endpoint must degrade to `None` rather than 500 (2026-08-19:
    # a best-effort enrichment failing must not take the rest of the page
    # down with it).
    assert payload["live_trend"] is None


# -- writes: one command layer, and nothing else ----------------------------


async def test_a_command_goes_through_the_command_layer(client: TestClient[Any, Any]) -> None:
    app = cast(web.Application, client.app)
    runtime = app[api.RUNTIME]
    response = await client.post(
        "/api/cmd", json={"command": "cap", "args": {"pct": 45}}, headers=_auth(app)
    )
    body = await response.json()
    assert body["ok"] and body["changed"]
    rows = await runtime.db.query("SELECT max_single_asset_pct FROM user_profile")
    assert rows[0]["max_single_asset_pct"] == 45.0
    decisions = await runtime.db.query(
        "SELECT payload FROM event_log WHERE type = 'UserDecisionEvent'"
    )
    assert len(decisions) == 1
    assert json.loads(str(decisions[0]["payload"]))["action"] == "set_max_single_asset"


async def test_repeating_a_command_appends_no_second_decision(
    client: TestClient[Any, Any],
) -> None:
    """Invariant 1 of the command layer, reached through the browser front: the
    owner sets the cap here and then types /cap on the phone. The second one
    must read as a statement of fact, not write a second decision into the audit
    trail as though they had decided twice."""
    app = cast(web.Application, client.app)
    runtime = app[api.RUNTIME]
    for _ in range(2):
        await client.post(
            "/api/cmd", json={"command": "cap", "args": {"pct": 55}}, headers=_auth(app)
        )
    second = await (
        await client.post(
            "/api/cmd", json={"command": "cap", "args": {"pct": 55}}, headers=_auth(app)
        )
    ).json()
    assert second["ok"] and not second["changed"]
    assert "already 55" in second["message"]
    decisions = await runtime.db.query("SELECT id FROM event_log WHERE type = 'UserDecisionEvent'")
    assert len(decisions) == 1


async def test_an_invalid_cap_is_refused_with_the_layers_own_message(
    client: TestClient[Any, Any],
) -> None:
    """The front never composes its own explanation of a refusal it did not
    make — the validation lives in `ops/commands.py` for every front at once."""
    app = cast(web.Application, client.app)
    body = await (
        await client.post(
            "/api/cmd", json={"command": "cap", "args": {"pct": 150}}, headers=_auth(app)
        )
    ).json()
    assert not body["ok"]
    assert "percentage in (0, 100]" in body["message"]


async def test_an_unknown_command_is_refused(client: TestClient[Any, Any]) -> None:
    app = cast(web.Application, client.app)
    response = await client.post(
        "/api/cmd", json={"command": "accept", "args": {"id": "prop-1"}}, headers=_auth(app)
    )
    # 'accept' in particular: ADR-006 removed the user gate, so the browser has
    # no more of a proposal decision to make than any other front.
    assert response.status == 400


async def test_no_handler_writes_outside_the_command_layer() -> None:
    """The structural version of "one command layer": a future endpoint that
    reaches for the database directly fails here rather than in review.

    The check is on the WRITE METHODS of `InvestmentDB`, not on SQL keywords in
    the text. The first version grepped for "INSERT"/"DROP" and failed on this
    module's own prose ("a stride will DROP the final row"), which is the kind
    of test that gets loosened until it tests nothing. `command` and
    `transaction` are the only two ways this connection mutates anything, and
    neither has any business in a front."""
    source = Path(api.__file__).read_text(encoding="utf-8")
    for write_method in (".command(", ".transaction(", ".append_event(", ".create_vertex("):
        assert write_method not in source, write_method
