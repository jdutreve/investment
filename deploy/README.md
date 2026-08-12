# deploy — running the agent on the Mac

One user LaunchAgent, one process (ADR-002 / ADR-004). Nothing here is
installed by the test suite or by any command in the repo: putting a file in
`~/Library/LaunchAgents` and bootstrapping it is a change to the machine, so it
is a deliberate act with the commands written out.

## Before the first start

The agent refuses to start on an unseeded database (`main.run_agent`), so:

```bash
uv run python -m investment.seed          # UC0 — safe to re-run
mkdir -p ~/data/investment/logs           # launchd will NOT create it
```

Run it in the foreground once, and read what it does:

```bash
uv run python -m investment.main
```

**The first start is not a dry run.** The chain is DUE on a database that has
never run one, so it will refresh the market data, triage the central-bank
feeds, rank, take the month's allocation decision — emitting the stack's
**opening entry**, a real order — run one cognitive cycle against the LLM, and
send the digest to Telegram. Watch it once before handing it to launchd.

## Install

```bash
cp deploy/com.jp.investment-agent.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jp.investment-agent.plist
```

`bootstrap` is the modern verb; `launchctl load` still works and prints a
deprecation notice.

## Inspect

```bash
launchctl print gui/$(id -u)/com.jp.investment-agent   # state, PID, exit codes
tail -f ~/data/investment/logs/agent.log
tail -f ~/data/investment/logs/agent.error.log
invest status                                          # or /status on Telegram
```

## Stop, restart, uninstall

```bash
launchctl kickstart -k gui/$(id -u)/com.jp.investment-agent   # restart
launchctl bootout gui/$(id -u)/com.jp.investment-agent        # stop + unload
rm ~/Library/LaunchAgents/com.jp.investment-agent.plist       # uninstall
```

`bootout` sends SIGTERM, which the agent handles: it finishes the transaction
in flight, checkpoints the WAL and closes (`main.run_agent`). Killing it with
`-9` skips that — the database survives (WAL), but the current chain step does
not, and the chain stays DUE so the next start redoes it.

## What it does while it runs

- **inbox watcher** — polls `~/data/investment/inbox` every 60s, waits for a
  5-minute quiet period, then ingests the batch and curates it.
- **Monday cron** — 08:00 Europe/Zurich.
- **heartbeat** — every 5 minutes, asks whether the chain is DUE. This is the
  path that matters on a laptop: a cron set for 08:00 on a closed lid never
  fires, so the chain runs on the first heartbeat after wake instead. Both
  triggers are safe because the chain is guarded by its marker and the run-lock.
- **Telegram bot** — `/status`, `/ranking`, `/refresh`, `/chain`, `/cycle`,
  `/enable`, `/disable`, `/drawdown`. Anything else you send is filed as a note.

## If it will not start

The three failures that look alike in the log:

| symptom | cause |
|---|---|
| `seed not run` | the database is empty — run UC0 first |
| `ValidationError` on startup | a key missing from `.env` (`pydantic-settings` refuses to start rather than run half-configured) |
| launchd restarts it every 10s | it is crashing at startup; `agent.error.log` has the traceback, and `ThrottleInterval` is what keeps the loop readable |
