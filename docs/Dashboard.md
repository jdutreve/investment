## Dashboard High-level technical guidance

Just enough direction to keep things on track — specific choices are left to the Coding Agent.

- Build it as a single web app using **Vite, React and TypeScript**.
- It runs fully locally and starts with **one simple command**; no accounts, no cloud, no internet
  needed to use it.
- **Prefer popular, well-supported libraries over custom code**. Don't hand-roll what a mature library does well.
- Keep the implementation simple and conventional. Library, data and structure choices are the
  Coding Agent's call, as long as the requirements and the success criteria below are met.
- The app will be running on the host computer; ensure the server is configured so that it can be viewed in a browser on the host computer.

> **Serving model — decided by the owner, 2026-08-15.** ADR-005 and CLAUDE.md
> pinned the dashboard as "server-rendered HTML + vanilla `fetch` + inline SVG —
> no build step, no CDN, no new framework". React wins on the ground that eight
> data-dense pages of filters, sortable tables and charts are past what vanilla
> JS carries comfortably. **Vite/React/TS compiles to STATIC ASSETS served by
> the agent's own aiohttp process on 127.0.0.1:8765** — so same-origin holds
> (the `X-Ops-Token` model and the one-command start are unchanged), the runtime
> is still one process, and nothing is fetched from a CDN. The ADR-005 clauses
> that break are "no build step" and "no new framework"; **both are retired by
> the ADR-005 amendment (M10, 2026-08-15) in docs/DECISIONS.md**, and CLAUDE.md's
> dashboard line follows it. No Vite dev server in the shipped path.
>
> Everything below specifies behaviour rather than rendering, deliberately: a
> success criterion that can only be checked by reading the DOM is a criterion
> that stops being true the first time the UI is restyled.

## Look and feel

Applies to the whole app:

- Make it **sharp and modern, but still clean and professional**.
- Use the color palette **`#ecad0a` (amber), `#209dd7` (blue) and `#753991` (purple)**, together with grays.
- **Avoid** these — they read as generic "AI-generated" tells: background gradients, purple
  backgrounds, buttons with gradients, and panels or cards with a single accent border line down one
  side.
- Include visual / icon elements for main nav items, for edit and delete actions on table rows, and
  where it makes sense, but avoid unnecessary emojis.

## Not in scope (v1)

Deliberately left out to keep this small and focused. Do not build these:

- No login, user accounts, multiple users or permissions — it's single-user and local.
- Single currency only (US dollars); no multi-currency.
- **No trade execution, no broker connection, no order placement.** V1 executes
  nothing (ADR-006); the owner alone places orders, by hand, from what the
  dashboard shows. Every performance figure on every page is PAPER.
- **No proposal ACCEPT/REJECT gate.** ADR-006 removed the user-validation gate:
  a proposal that passes its gates *is* the paper-test. `ops/commands.py`
  documents this absence deliberately. Building the buttons would re-create a
  gate the system does not wait on, and put a decision in front of the owner
  that nothing actually waits for.
- No editing of agent-owned data. The owner changes preferences (drawdown, cap,
  enable/disable); the agent adjudicates theses. There is no "edit invariant"
  or "delete proposal" anywhere.

## Functionality

### What this is

The **third front of one command layer** (ADR-005). Telegram is the remote
front, `invest` is the terminal front, this is the browser front — and all
three call the same `ops/commands.py`. A front parses an instruction and
renders an answer; it never decides anything and it never computes a number
that a job already wrote.

It exists because Telegram is a narrow pipe (no tables, no charts) and the
SQLite file, while open, is not an interface. What ships today: the command
layer, the run-lock, the CLI and the bot. What this spec adds: `ops/api.py`
(does not exist yet) and `ops/dashboard/` (empty directory).

### Ground rules — these apply to every phase below

- **One command layer.** Every state change goes through `ops/commands.py`.
  No dashboard-only write path, no direct `UPDATE`, no side channel around the
  gates and the audit trail.
- **Reads direct, writes through the agent.** SQLite WAL gives concurrent
  readers for free, so pages read the live DB file read-only. Writes go only
  through the running agent's serialized asyncio path. **Agent down → every
  read page still works in full; every write control is visibly disabled with
  the reason, never silently inert.**
- **`X-Ops-Token` on every API call**, read from `~/data/investment/ops_token`
  (chmod 600). Binding to 127.0.0.1 does *not* stop a web page from POSTing to
  it; the custom header forces a CORS preflight that fails cross-origin. No
  token → 403.
- **Idempotent across fronts.** Acting on a state already reached renders
  "already …" and appends **no** second `UserDecisionEvent` — the same
  `CommandResult.changed` distinction the CLI and bot already render.
- **Single-flight.** `{catchup, chain, cycle}` share one run-lock. A click
  while it is held is refused with the holder's name and start time, never
  queued behind it.
- **Read the row the job wrote; never re-derive.** Re-ranking or recomputing an
  indicator at render time is how the phone and the browser start disagreeing
  about the same Sunday.
- **A missing measurement is never a zero.** NULL renders `n/a` (the
  `digest.pct` / `commands._sharpe` convention). An unmeasured Sharpe is not a
  bad one.
- **Show the date of what you read.** Every panel carries the date of its
  source row, so a stale page reads as stale rather than as current.
- Timestamps stored UTC, displayed Europe/Zurich. All values USD. All rendered
  text escaped. Errors surfaced, never swallowed.

---

> **Scope for M10 (owner, 2026-08-15): Phases 1–3.** They are the screens read
> on a Sunday morning before an order is placed by hand — is it alive and
> current, where do the portfolios stand, and what did the stack decide.
> Phases 4–6 are specified here so the shell is built to hold them, but they
> ship after daily use has an opinion about what it actually wants. M10's
> "daily-use comfort: YOUR verdict after a week" is judged on Phases 1–3.
> **`ops/api.py` does not exist yet** — it is Phase 1 work, not a dependency
> already in place, and M10's 1-day budget in docs/MILESTONES.md predates that
> discovery.

### Phase 1 — Shell, API, and the Overview page

**Features**

- `ops/api.py`: aiohttp bound to **127.0.0.1** only, port `LOCAL_API_PORT`
  (8765), started by `main.py` alongside the scheduler, watcher and bot. JSON
  read endpoints + `POST /api/cmd` dispatching to `ops/commands.py`. Token gate
  on every route. Long operations return "job started" immediately and run in
  an executor, so the API, watcher and bot stay responsive.
- App shell: persistent nav with an icon per section (Overview, Ranking, Stack,
  Invariants, Knowledge, Proposals & Outcomes, Events, Console) and a header
  carrying the agent's live state — idle, or the lock holder and since when —
  plus last chain success, latest market-data date, latest decision date.
- **Overview = the weekly digest as a page**, same content, better layout:
  - alerts first, critical first (`mechanical/alerts.collect_alerts`: stack
    drawdown, stack drift, market-data freshness, signal freshness, decision
    freshness, rule trade-off);
  - regime header — type, confidence, tags, liquidity;
  - market-signal stack standing — Sortino, Calmar, deepest 36-month drawdown,
    labelled PAPER;
  - defender with NAV sparkline;
  - scoreboard — hit-rate (won / decided), paper-tests in progress, strategies
    in probation;
  - latest proposal; last chain run and the steps it completed.

**Success criteria**

- Started with the documented single command, the Overview opens in the host
  browser showing real data from `~/data/investment/investment.db`.
- Every figure on the Overview matches that week's Telegram digest field for
  field — same numbers, same `n/a`s, same alert set in the same order.
- With the agent stopped, the Overview still renders in full and the header
  says the agent is down.
- No token → 403 on every endpoint, and the page renders the refusal rather
  than a blank panel.
- A section with no data yet ("the chain has not run") renders an explicit
  empty state, never a zero.

---

### Phase 2 — Ranking and portfolio detail

**Features**

- The latest `portfolio_weekly_snapshot`, in the stored `rank` order.
  Client-side sorting is a *view* over that table; it never becomes the order
  of record.
- Row badges, each distinct and separately legible, because these are four
  different rules: **defender ★**, **BENCHMARK** (`all-weather-USD`, `spy-USD`
  — ranked so they are seen, refused by kind so they can never be proposed),
  **demoted** (`calmar_rolling < 1.0`), **excluded from candidacy** (breaches
  the user drawdown rule). Benchmark-ness and drawdown-breach are *not* the
  same flag and must not render as one.
- Columns: rank, portfolio, 1y return, Sortino, Sharpe, Calmar, max drawdown,
  volatility, NAV, recommendation.
- Date picker: re-render any past snapshot date, exactly as the digest does.
- Portfolio detail: allocation **grouped by asset class** (so the concentration
  cap is legible against the thing it actually binds — `SPY 50 / VTI 20` is 70%
  of one exposure), NAV chart over the full series, indicator panel, gap to
  defender, designed-for regime, primary strategy, and the portfolio's own cap
  where it is stricter than the profile's.

**Success criteria**

- Row order matches `invest ranking` and Telegram `/ranking` exactly, with the
  defender starred in all three.
- Sorting by any column never changes which row is defender, benchmark,
  demoted or excluded.
- A benchmark row is visible in the ranking and offers no control that would
  propose it.
- NAV series with different inception dates are never presented as comparable
  levels — the page states the inception per series.
- Picking a past date re-renders that week's stored numbers, including under a
  binding cap that has since moved.

---

### Phase 3 — The market-signal stack (the live allocation path)

The page the original 8-page spec does not have. That list was written when the
ranking *was* the allocation path; ADR-007 made the monthly market-signal stack
the live one and the page list never followed. This is the screen the owner
actually reads before placing an order by hand.

**Features**

- Decision timeline from `MarketSignalDecisionEvent` — one row per decision
  date, **moved or not, blocked or not**.
- The latest decision in full:
  - held book vs target book (labelled *target* whenever a gate refused — never
    "book", which would name a target as though it were a position);
  - held → target allocation, with the diff **rendered as the order to place**;
  - signal reads: value vs 10-year trailing median, each with its
    `knowable_at` date, so vintage discipline (ADR-003) is visible rather than
    assumed;
  - trend overlay: window(s) and which tickers are below trend;
  - hysteresis: pending switch and its confirmation count out of the required
    number;
  - the binding cap actually applied — the stricter of user profile / `ms-stack`
    / the book — and the `HAVEN_EXEMPT` exemption where it applied;
  - gate outcome.
- **A blocked decision is LOUD**: a banner saying nothing was proposed and the
  stack is FROZEN in its previous book. It must never render like a month that
  legitimately did not move.
- Worker challenge (`WorkerReadingEvent`) under the mechanical reasoning, with
  its own decision date, explicitly marked when it is a reading of a *previous*
  month's decision.
- Stack NAV chart with the 36-month rolling drawdown shown against the -25%
  rule — which on this path raises an alert and does not block (ADR-009).

**Success criteria**

- Every decision date since the stack's inception is listed, holding months
  included — the timeline has no gaps.
- A blocked decision reads unmistakably differently from a no-change decision
  (verified on a real blocked row or a fixture).
- The rendered diff is a *placeable* order — tickers and target weights — and
  it matches the digest's move line for the same decision.
- A stale Worker reading is shown with the decision it belongs to, never
  silently dropped and never attached to the current one.
- Nothing on the page implies execution; "paper" is legible throughout.
- The Worker's contribution reads as a *reading*, never as an allocation — the
  Worker does not allocate (ADR-012).

---

### Phase 4 — Invariants and knowledge *(after M10)*

**Features**

- Invariant table over the full corpus (674 rows today): filter by regime type,
  tag, status, author tier; sort by `weight_effective`; text search.
- Detail: statement, provenance, author tier and its floor,
  `weight_initial × market_score × recency_factor` shown as a computation with
  the floor marked *when it is what binds*, confrontation timeline
  (`invariant_confrontations`), N / score, and the verdict **in words**.
- **Three verdicts, never two**: `integrated`, `rejected`, and `proposed` =
  *insufficient evidence*. The page must say "not enough evidence yet", because
  "proposed" reading as "rejected" is the exact misreading ADR-006 legislated
  against.
- SUPPORTS passages and BACKED_BY strategies, both navigable.
- `weight_effective` curve over time.
- Knowledge browser: documents → passages, curated passages, and semantic
  search over both embedding matrices.

**Success criteria**

- The three verdicts are visually and textually distinct, and "proposed" reads
  as insufficient evidence.
- A dormant invariant's recency factor does not decay on the page — decay is
  condition-relative, not wall-clock.
- Filters compose, and the displayed row count is the true filtered count.
- Semantic search returns the same top-k as `invest search` for the same query.
- A passage containing markup renders escaped and cannot break the page.

---

### Phase 5 — Proposals, outcomes and the EventLog *(after M10)*

**Features**

- Proposal ledger: type, date, allocation, gates passed, `paper_started`, the
  +12w verdict, and the temporary NAVs that scored it — charged at
  `TRADING_COST_BPS` like every other NAV.
- Scoreboard detail: cumulative hit-rate (won / decided), paper-tests in
  progress with their running proposed-vs-incumbent, strategies in probation.
- The innovation recurrence ledger and the measured rule revisions
  (`innovation`, `revision_measurement`) — what the Worker keeps proposing, and
  what measurement said about it.
- EventLog audit tail: filter by type (21 types), source UC and date range;
  **ULID order preserved as the canonical append order**; payloads expandable.

**Success criteria**

- No ACCEPT/REJECT control exists anywhere in the UI.
- The hit-rate shown equals `build_scoreboard`'s and counts the same population
  as the paper-test panel beside it.
- EventLog rows render in ULID order, and paging neither reorders nor drops a
  row.
- A pending verdict renders as pending — never as a loss.
- No secret ever appears in a rendered payload.

---

### Phase 6 — Actions and the SQL console *(after M10)*

**Features**

- **The owner's rules, and only these** — the list USE_CASES calls "never
  agent-overridden": drawdown limit, concentration cap, strategy
  enable/disable. Each control shows the current value, the validation
  `commands.py` already enforces, and the consequence sentence it already
  returns ("binds from the next gate evaluation; nothing already committed is
  revisited").
- Runs: refresh (catch-up), full weekly chain, one ad-hoc cognitive cycle —
  with the 1/day allowance shown as **remaining**, and the run-lock holder shown
  while held. Long ops return immediately; progress is followed on the page.
- Note box → inbox drop, with its "ingested within ~5 minutes" note.
- Read-only SQL console: single statement, keyword blacklist (mutations, DDL,
  **and `ATTACH`/`PRAGMA`**), sanity `LIMIT 5000`, results as a table, errors
  shown verbatim. This is the power-user escape hatch — the Worker's 20-row cap
  is a guardrail for the LLM, not for the owner.

**Success criteria**

- Every write goes through `ops/commands.py`: no `INSERT`/`UPDATE`/`DELETE`
  appears anywhere in the dashboard or API layer.
- Setting the cap to the value it already has renders "already 60%" and appends
  **no** second `UserDecisionEvent` — verified by reading the EventLog after
  the click.
- The same action taken on Telegram and then here produces **one** decision
  row, and the second front reports the current state with who did it and when.
- Clicking a run while the chain holds the lock is refused with the holder's
  name and start time.
- An out-of-range drawdown (positive, or `-250`) is refused with the same
  message the CLI gives.
- The console refuses `ATTACH`, `PRAGMA` and every mutation; a query over a
  248k-row table returns capped, not hung.
- With the agent stopped: the note box still works (it is a filesystem drop),
  and every DB-mutating control is disabled with the explicit refusal.

## Final success criteria

The dashboard is complete, and the Coding Agent may stop, when **all** of the following are true:

- All UI sections work.
- The app ships with historical and actual data, so it looks alive on first launch.
- The look-and-feel rules are met and none of the banned elements appear anywhere.
- The dashboard looks stunning: compelling information, well presented.
- **Most importantly: the product has been validated by actually using it end to end in a real
  browser — clicking through every section as a real user would, performing the actions above, and
  visually inspecting each screen. Passing unit tests is necessary but NOT sufficient; the Coding
  Agent must confirm the running product works and looks right, not merely that the tests are green.**
