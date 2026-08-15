"""The localhost HTTP front (ADR-005; docs/TASKS.md Task 6ter.1) — the browser's
way into the same command layer Telegram and the CLI already use.

TWO SURFACES, DIFFERENT RULES, and the difference is the security model:

  - `/api/*` requires the `X-Ops-Token` header. Binding to 127.0.0.1 does NOT
    make this private: any web page the owner visits can POST to
    http://127.0.0.1:8765 from their browser, with their loopback, and a
    same-site cookie scheme would not help because there are no cookies. What
    stops it is that a CUSTOM HEADER forces a CORS preflight, and this server
    answers no preflight from any origin — so a cross-origin caller cannot send
    the header at all, and without it every call is 403.
  - the dashboard's own files are served WITHOUT a token, because the browser
    has to fetch them before it can know one. The token is injected into
    `index.html` at serve time (`_serve_index`). That is safe for the mirror
    reason: a cross-origin page cannot READ our HTML — fetch is blocked by CORS
    and an iframe by the same-origin policy. A `/api/token` endpoint, or a
    token in a `.js` file, would NOT be safe: `<script src=...>` is exempt from
    CORS, so any page could pull it in and read the global it defines.

EVERY READ GOES THROUGH THE AGENT'S ONE CONNECTION (`runtime.db`), and the
tempting alternative is wrong. A second read-only handle would be outside the
writer's transaction and therefore on a DIFFERENT committed snapshot, so a
single page load could take its header from one instant and its table from
another — a ranking dated one Sunday beside a decision from the next. It would
also give up what `InvestmentDB._serialized` exists for: with one connection a
reader is INSIDE any open transaction, and that guard is what stops a front
reading rows a rollback is about to erase. The cost is waiting for one
transaction, not for a chain — the weekly chain is dozens of short transactions
with LLM calls between them, and nothing holds a BEGIN across a network call.

WRITES GO ONLY THROUGH `ops/commands.py`. No handler here touches a table.

AGENT DOWN MEANS NO DASHBOARD. This server lives inside the agent process, so
when the agent is not running there is nothing to answer and the browser shows
a connection error. That is the design, not a gap: the offline matrix in Task
6ter belongs to `invest`, which opens the database file itself. A dashboard that
outlived its own agent would be a second process, which ADR-005 refused and the
M10 amendment went on refusing.
"""

import asyncio
import dataclasses
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from investment.db.seed_data import BENCHMARK_PORTFOLIOS
from investment.db.sqlite import InvestmentDB
from investment.mechanical.market_signal import STACK_PORTFOLIO_ID
from investment.ops import commands
from investment.ops.rows import unnest_json_columns
from investment.ops.run_lock import AlreadyRunning
from investment.runtime import AgentRuntime
from investment.telegram.digest import collect_digest_inputs
from investment.writeback.writeback import MARKET_SIGNAL_EVENT

logger = logging.getLogger(__name__)

# What the application carries, as typed keys rather than bare strings.
# `web.AppKey` is aiohttp's own answer to a dict that everything reaches into:
# with a plain string every lookup is `Any`, so a handler that read
# `app["runtme"]` — or read the runtime and used it as a connection — would
# type-check perfectly and fail at request time. These make both mistakes
# compile errors under `mypy --strict`.
RUNTIME: web.AppKey[AgentRuntime] = web.AppKey("runtime")
TOKEN: web.AppKey[str] = web.AppKey("token")
JOBS: web.AppKey[set[asyncio.Task[None]]] = web.AppKey("jobs")

# The token file, beside the database. chmod 600 — it is the only thing between
# a visited web page and the owner's write commands.
TOKEN_FILENAME = "ops_token"
TOKEN_HEADER = "X-Ops-Token"

# Where the built dashboard lands. Vite writes here (ADR-005 amendment, M10);
# the directory is absent until someone has run the build, and `_serve_index`
# says so in a sentence rather than 404-ing.
DIST_DIR = Path(__file__).parent / "dashboard" / "dist"

# The marker the built index.html carries for the token to be spliced into. A
# placeholder in the source file rather than a string built here, so the page
# renders identically whether Vite serves it or this does.
TOKEN_PLACEHOLDER = "__OPS_TOKEN__"

# How many NAV points a portfolio detail returns. The full series is ~35 years
# of daily rows (152k across all portfolios); a chart 900px wide cannot show
# them and a browser should not parse them. Thinned by stride, never truncated:
# dropping the tail would silently redraw history as though it stopped.
NAV_MAX_POINTS = 1200


def ensure_token(db_path: Path) -> str:
    """Read the ops token, creating it on first start.

    STABLE ACROSS RESTARTS, deliberately. Regenerating per launch would log the
    owner out of an open dashboard tab every time the agent restarts — and the
    agent restarts on every wake, which on a laptop is many times a day. The
    file is the shared secret; the CLI reads it too."""
    path = db_path.parent / TOKEN_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    # 0600 AFTER writing, and on every regeneration: the default umask would
    # leave it world-readable, and on a shared machine that is every local
    # account holding the owner's write commands.
    path.chmod(0o600)
    logger.info("ops token generated at %s", path)
    return token


def _json_default(value: Any) -> Any:
    """Dates and frozen dataclasses (`Alert`) into JSON.

    `dataclasses.asdict` rather than `__dict__`: `Alert` is frozen and may gain
    nested records, and asdict recurses where `__dict__` would emit an object
    the browser cannot read."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    raise TypeError(f"not JSON-serializable: {type(value).__name__}")


def json_response(payload: Any, *, status: int = 200) -> web.Response:
    """One JSON writer for every endpoint, so no handler invents its own
    encoding of a date."""
    return web.json_response(
        payload, status=status, dumps=lambda p: json.dumps(p, default=_json_default)
    )


async def _rows(db: InvestmentDB, sql: str, **params: Any) -> list[dict[str, Any]]:
    """Query, with JSON columns unnested — the shape the browser wants, and the
    same one `invest sql --json` produces."""
    return [unnest_json_columns(row) for row in await db.query(sql, **params)]


# -- reads ------------------------------------------------------------------


async def handle_status(request: web.Request) -> web.Response:
    """The header strip: is it alive, and how current is what it knows."""
    runtime = request.app[RUNTIME]
    return json_response(dataclasses.asdict(await commands.status_facts(runtime)))


async def handle_overview(request: web.Request) -> web.Response:
    """The Overview page — THE DIGEST'S OWN INPUTS, served as JSON.

    Not a second assembly of the same figures: `collect_digest_inputs` is the
    function the weekly Telegram digest renders, so the two fronts cannot
    disagree about a Sunday without failing a type check first
    (`telegram/digest.DigestInputs`)."""
    runtime = request.app[RUNTIME]
    return json_response(await collect_digest_inputs(runtime.db))


async def _ranking_payload(db: InvestmentDB, snapshot_date: str | None) -> dict[str, Any]:
    dates = [
        str(r["date"])
        for r in await _rows(
            db, "SELECT DISTINCT date FROM portfolio_weekly_snapshot ORDER BY date DESC"
        )
    ]
    chosen = snapshot_date or (dates[0] if dates else None)
    rows = (
        await _rows(
            db,
            "SELECT * FROM portfolio_weekly_snapshot WHERE date = :d ORDER BY rank ASC",
            d=chosen,
        )
        if chosen
        else []
    )
    return {
        "date": chosen,
        "available_dates": dates,
        "rows": rows,
        # SENT, because the front cannot derive it. Benchmark-ness is a KIND
        # held in Python (`seed_data.BENCHMARK_PORTFOLIOS`), not a column and
        # deliberately not a flag — the flag on the snapshot means the DRAWDOWN
        # breach, and CLAUDE.md's ranking rule is explicit that these are two
        # separate rules with two separate mechanisms. A page that inferred one
        # from the other would merge them again on the only screen where the
        # difference is visible: a yardstick is ranked so it is seen, a breached
        # portfolio is ranked so it is judged, and neither may be proposed.
        "benchmark_ids": sorted(BENCHMARK_PORTFOLIOS),
    }


async def handle_ranking(request: web.Request) -> web.Response:
    """The ranked snapshot, in the STORED `rank` order.

    Never re-sorted here and never re-derived: the ranking job wrote this order
    (`mechanical/snapshots.py`), and a front that re-ranked on read is how the
    browser and the phone start describing the same Sunday differently. The
    date list lets the page re-render a past week exactly as the digest can."""
    runtime = request.app[RUNTIME]
    return json_response(await _ranking_payload(runtime.db, request.query.get("date")))


async def _nav_series(db: InvestmentDB, portfolio_id: str) -> list[dict[str, Any]]:
    """A portfolio's paper NAV, thinned to something a chart can draw.

    THINNED BY STRIDE AND ALWAYS KEEPING THE LAST POINT. A `LIMIT` would cut
    the series somewhere arbitrary; taking every Nth row keeps the shape, and
    appending the final row keeps the end — which is the one point the owner
    actually looks at, and the one a stride will drop whenever the length is not
    a multiple of it."""
    rows = await _rows(
        db,
        "SELECT ts, nav FROM portfolio_nav WHERE portfolio_id = :p ORDER BY ts ASC",
        p=portfolio_id,
    )
    if len(rows) <= NAV_MAX_POINTS:
        return rows
    stride = len(rows) // NAV_MAX_POINTS + 1
    thinned = rows[::stride]
    if (len(rows) - 1) % stride:  # the stride missed the final row
        thinned.append(rows[-1])
    return thinned


async def handle_portfolio(request: web.Request) -> web.Response:
    """One portfolio: its row, its allocation, its NAV series, its own caps."""
    runtime = request.app[RUNTIME]
    portfolio_id = request.match_info["portfolio_id"]
    portfolio = await _rows(runtime.db, "SELECT * FROM portfolio WHERE id = :p", p=portfolio_id)
    if not portfolio:
        return json_response({"error": f"no portfolio '{portfolio_id}'"}, status=404)
    snapshots = await _rows(
        runtime.db,
        "SELECT * FROM portfolio_weekly_snapshot WHERE portfolio_id = :p ORDER BY date DESC "
        "LIMIT 12",
        p=portfolio_id,
    )
    return json_response(
        {
            "portfolio": portfolio[0],
            "latest_snapshot": snapshots[0] if snapshots else None,
            "recent_snapshots": snapshots,
            "nav": await _nav_series(runtime.db, portfolio_id),
        }
    )


async def handle_stack(request: web.Request) -> web.Response:
    """The market-signal stack — ADR-007's live allocation path.

    THE TIMELINE IS THE JOURNAL, not the proposals. The stack decides every
    month but emits a Proposal only on the ~3 months a year it moves, so a
    proposal-driven timeline would show three rows a year and call the other
    nine nothing at all. `MarketSignalDecisionEvent` has one row per decision
    date, moved or not, blocked or not — which is exactly what makes a HOLDING
    month distinguishable from a BLOCKED one on the page
    (`telegram/digest._market_signal_block` makes the same argument)."""
    runtime = request.app[RUNTIME]
    decisions = await _rows(
        runtime.db,
        "SELECT id, event_date, payload FROM event_log WHERE type = :t ORDER BY id DESC LIMIT 60",
        t=MARKET_SIGNAL_EVENT,
    )
    return json_response(
        {
            "decisions": decisions,
            "nav": await _nav_series(runtime.db, STACK_PORTFOLIO_ID),
            "profile": next(
                iter(await _rows(runtime.db, "SELECT * FROM user_profile LIMIT 1")), None
            ),
        }
    )


# -- writes: the ONLY mutating surface, and it dispatches to commands.py ------

# Commands that return immediately. Anything that runs the chain, the catch-up
# or a cognitive cycle is minutes to tens of minutes of work; holding an HTTP
# request open for it would time out in the browser long before it finished, and
# the answer the owner needs ("it started") is available at once.
_INSTANT: dict[str, Callable[[AgentRuntime, dict[str, Any]], Awaitable[commands.CommandResult]]] = {
    "enable": lambda rt, a: commands.set_strategy_enabled(rt, str(a["strategy_id"]), enabled=True),
    "disable": lambda rt, a: commands.set_strategy_enabled(
        rt, str(a["strategy_id"]), enabled=False
    ),
    "drawdown": lambda rt, a: commands.set_max_drawdown(rt, float(a["pct"])),
    "cap": lambda rt, a: commands.set_max_single_asset(rt, float(a["pct"])),
    "note": lambda rt, a: commands.save_note(rt, str(a["text"])),
}

_BACKGROUND: dict[str, Callable[[AgentRuntime], Awaitable[commands.CommandResult]]] = {
    "refresh": commands.refresh,
    "chain": commands.run_chain,
    "cycle": commands.run_cycle,
}


async def handle_cmd(request: web.Request) -> web.Response:
    """Every state change the browser can make, dispatched to `ops/commands.py`.

    NOTHING IS DECIDED HERE. This handler parses a JSON body, calls the command
    layer and returns what it said — including its refusals, verbatim. The
    idempotency, the validation and the EventLog-first ordering all live in the
    layer, which is the point of having one: the same action from the phone and
    from the browser cannot take two different paths."""
    runtime = request.app[RUNTIME]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return json_response({"ok": False, "message": "body must be JSON"}, status=400)
    name = str(body.get("command", ""))
    args = body.get("args") or {}

    if name in _INSTANT:
        try:
            result = await _INSTANT[name](runtime, args)
        except (KeyError, TypeError, ValueError) as exc:
            return json_response({"ok": False, "message": f"bad arguments: {exc}"}, status=400)
        return json_response(dataclasses.asdict(result))

    if name in _BACKGROUND:
        return json_response(dataclasses.asdict(_start_job(request.app, runtime, name)))

    return json_response({"ok": False, "message": f"unknown command '{name}'"}, status=400)


def _start_job(app: web.Application, runtime: AgentRuntime, name: str) -> commands.CommandResult:
    """Launch a long operation and answer at once.

    THE TASK IS HELD, not fired and forgotten (CLAUDE.md asyncio rule): an
    un-referenced task can be garbage-collected mid-run, and its exception would
    surface as a warning at interpreter shutdown rather than in the log of the
    run that failed. The handle is dropped in the done-callback.

    THE RUN-LOCK IS NOT CHECKED HERE. It is checked inside the command, where
    the check and the acquisition happen with no await between them
    (`ops/run_lock.py`); testing it first would be a check-then-act race that
    could report "started" for a job the lock then refuses."""
    jobs = app[JOBS]

    async def run() -> None:
        try:
            result = await _BACKGROUND[name](runtime)
            logger.info("dashboard job %s finished: %s", name, result.message)
        except AlreadyRunning as exc:
            logger.info("dashboard job %s refused: %s", name, exc)
        except Exception:
            logger.exception("dashboard job %s failed", name)

    task = asyncio.create_task(run(), name=f"dashboard-{name}")
    jobs.add(task)
    task.add_done_callback(jobs.discard)
    return commands.CommandResult(
        ok=True,
        message=f"'{name}' started — follow it in the header and the EventLog.",
        changed=True,
    )


# -- the dashboard's own files ----------------------------------------------


async def _serve_index(request: web.Request) -> web.Response:
    """`index.html` with the ops token spliced in.

    The one place the token crosses into the page, and it crosses inside HTML
    the browser will not hand to another origin. See this module's docstring for
    why an endpoint returning the token would be strictly worse."""
    index = DIST_DIR / "index.html"
    if not index.exists():
        return web.Response(
            status=503,
            content_type="text/plain",
            text=(
                "The dashboard has not been built yet.\n\n"
                f"  cd {DIST_DIR.parent} && npm install && npm run build\n\n"
                "The JSON API is already up — try /api/status with the X-Ops-Token header."
            ),
        )
    html = index.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, request.app[TOKEN])
    # NEVER CACHED. The token is in the body, and a cached copy would outlive a
    # regenerated token file with no way for the owner to know why every call
    # started returning 403.
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


@web.middleware
async def auth_middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    """403 unless an `/api/*` call carries the right token.

    `secrets.compare_digest` rather than `==`: the comparison is against a
    secret, and the early-exit of a normal string compare is a timing oracle.
    The cost of being careful here is nothing; the cost of being wrong is the
    write surface."""
    if request.path.startswith("/api/"):
        supplied = request.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(supplied, request.app[TOKEN]):
            return json_response(
                {
                    "ok": False,
                    "message": (
                        f"missing or wrong {TOKEN_HEADER}. The dashboard gets it from the page "
                        "it was served; a script reads it from the ops_token file."
                    ),
                },
                status=403,
            )
    return await handler(request)


def build_app(runtime: AgentRuntime) -> web.Application:
    """The aiohttp application, wired to one running agent."""
    app = web.Application(middlewares=[auth_middleware])
    app[RUNTIME] = runtime
    app[TOKEN] = ensure_token(runtime.settings.db_path)
    app[JOBS] = set()

    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/overview", handle_overview)
    app.router.add_get("/api/ranking", handle_ranking)
    app.router.add_get("/api/portfolio/{portfolio_id}", handle_portfolio)
    app.router.add_get("/api/stack", handle_stack)
    app.router.add_post("/api/cmd", handle_cmd)

    app.router.add_get("/", _serve_index)
    if DIST_DIR.exists():
        # The hashed asset bundle. Registered last and only when it exists, so a
        # missing build leaves the API and the explanatory page working.
        app.router.add_static("/assets", DIST_DIR / "assets")

    return app


async def start_api(runtime: AgentRuntime) -> web.AppRunner:
    """Bind the server on 127.0.0.1 and return its runner for shutdown.

    LOOPBACK ONLY, never 0.0.0.0: this is a single-user local agent (ADR-002),
    and the token protects against the owner's own browser, not against a
    network. Binding wide would put the write surface of a financial agent on
    whatever café wifi the laptop is on."""
    app = build_app(runtime)
    apprunner = web.AppRunner(app)
    await apprunner.setup()
    port = runtime.settings.local_api_port
    site = web.TCPSite(apprunner, host="127.0.0.1", port=port)
    await site.start()
    logger.info("dashboard + API on http://127.0.0.1:%d", port)
    return apprunner
