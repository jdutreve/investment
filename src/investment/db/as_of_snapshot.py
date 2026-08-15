"""Build the as-of-t copy of the database that the AGENTIC replay reads
(M8b — docs/TASKS.md Task 9.4, docs/MILESTONES.md M8b).

WHY A COPY AND NOT AN `as_of` PARAMETER. The mechanical replay is PIT by
construction: `run_replay` works on frames `load_inputs` already filtered
(replay.py module docstring). The cognitive half cannot be bounded the same way.
The Planner and Worker make 21 DB reads across 14 tables and NOT ONE of them is
bounded by t — and one of them is `db_query`, the tool that runs SQL THE WORKER
ITSELF WROTE (worker/tools.py). No parameter threaded through a call site can
bound a statement a model composes at runtime, so the bound has to sit UNDER all
of them: the replay opens a throwaway copy of the database with every unknowable
row deleted. Every read — the 21, the Worker's own SQL, `market_fetch` — is then
PIT for free, no live signature changes, and replay logic cannot DRIFT from live
logic, which is Task 9.4's own stated reason for reusing the one harness.

One snapshot is BUILT at the first decision date and then ADVANCED to each
following one (`advance_as_of_snapshot`) rather than rebuilt: only the world is
topped up, so everything the replay itself wrote survives and the run is a
sequence of months rather than twenty independent first months.

WHAT IS PRUNED — the WORLD: prices, NAVs, regimes, rankings, valuations,
scenario readings, past decisions, the event log.

WHAT IS NOT — the CORPUS ITSELF: invariants, passages, strategies, frameworks,
the seeded edges, the user profile and thresholds.

THE CUT RUNS BETWEEN AN IDEA AND ITS TRACK RECORD, and that is the whole design.
Pruning the invariants on `created_at` would empty the screen — all four
`integrated` ones were born in July 2026, so gate 6 would have nothing citable at
any historical date, hence no reallocation, hence nothing to measure. So the
ideas survive: M8b is "the best-case pre-go-live screen" (docs/MILESTONES.md) and
best-case means the agent gets today's book of ideas.

Their EVIDENCE does not. `invariant_confrontations` IS pruned, because the 35y
birth maturation dated its confrontations at the historical moments it tested
(1980-2026) rather than at the seed's wall clock — so bounding them at t and
re-deriving the weights (`mechanical/as_of_cycle.reweigh_invariants_asof`) gives
a genuine as-of-t standing. What the replayed agent knows at t is every idea in
the corpus, and exactly how well each had proven out BY THEN.

That leaves one honest look-ahead: an idea nobody had yet in 2008 is available to
be cited in 2008, if its own historical record supported it. It belongs stated in
the report's NOTES, alongside the mechanical mode's own approximations.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

# The WORLD OBSERVATIONS, and the expression giving the date each row became
# knowable. These are the only tables `advance_as_of_snapshot` tops up, and the
# split is the whole reason the replay can be a SEQUENCE (see that function).
# `backtest` is absent on purpose: it hangs off its regime, so its top-up
# predicate is not a column expression and is written out longhand there.
_WORLD_OBSERVATIONS: tuple[tuple[str, str], ...] = (
    ("market_data", "date(ts)"),  # ADR-003: ts = the date it became KNOWABLE
    # The corpus's EVIDENCE, and the one part of it that is dated. The 35y birth
    # maturation wrote these at the historical moments it tested (1980-2026), so
    # bounding them at t yields a genuine as-of-t weight and verdict — which is
    # exactly what M8b's Definition of Verified asks for ("invariant weights read
    # as-of-t; a confrontation dated after t changes no weight before t"). The
    # invariants themselves still survive whole: what is knowable at t is how
    # well an idea had PROVEN OUT by then, not whether anyone had had it.
    ("invariant_confrontations", 'date("date")'),
    ("portfolio_nav", "date(ts)"),
    ("benchmark_valuation", 'date("date")'),
    # Regimes key on `created_at` — the CONFIRMING PRINT's date — and NOT on
    # `start_date`, which is back-dated to the data. A regime begun before t but
    # not yet confirmed at t must stay invisible, else a few weeks of look-ahead
    # leak (replay.py module docstring, "Regimes").
    ("regime", "date(created_at)"),
)

# Rows to DELETE, in FK-dependency order — CHILDREN BEFORE PARENTS, because the
# copy is pruned with `foreign_keys=ON` so a missed dependent aborts loudly here
# rather than surfacing as a corrupt read halfway through an LLM replay.
#
# The predicate is `date(col) > :t` everywhere, never a bare string compare: the
# knowability columns are stored in two shapes (plain dates like regime's
# '1991-09-17', full ISO datetimes like event_log's
# '2026-07-12T17:15:17.393659+00:00'), and only `date()` reads both. Dropping
# the index for a one-off prune is not a cost worth reasoning about.
_PRUNE: tuple[tuple[str, str], ...] = (
    # A citation cannot outlive the proposal it belongs to.
    ("proposal_cites", "proposal_id IN (SELECT id FROM proposal WHERE date(created_at) > :t)"),
    # Backtests are pruned BY THEIR REGIME, never by their own `created_at`:
    # that column holds the SEED's wall clock (2026-07-15 for every row), which
    # is not a knowability date at all — filtering on it would delete every
    # backtest at every historical t. Regime visibility is the real bound, and
    # it is the same one `replay._favors_asof` already applies.
    ("backtest", "regime_id IN (SELECT id FROM regime WHERE date(created_at) > :t)"),
    ("proposal", "date(created_at) > :t"),
    ("evaluation", "date(created_at) > :t"),
    # FAVORS edges carry NO date and aggregate the WHOLE 35y — replay.py refuses
    # to read them for exactly that reason ("reading them would leak the future
    # into every decision") and re-aggregates as-of t instead. They are a DERIVED
    # artefact the live chain rebuilds every week from `backtest`, so the
    # snapshot drops them wholesale and hydration recomputes them from the rows
    # that survived. Deleting rather than recomputing here is what makes the leak
    # impossible: `run_backtests_and_favors` SKIPS a (regime type, strategy) pair
    # with no visible instance at t, which would have left the 2026 edge in place.
    ("favors", "1 = 1"),
    *((table, f"{expr} > :t") for table, expr in _WORLD_OBSERVATIONS),
    ("portfolio_weekly_snapshot", 'date("date") > :t'),
    ("scenario_probability", "date(ts) > :t"),
    ("scenario_calibration", 'date("date") > :t'),
    # The agent's own history, pruned on `event_date` (the DOMAIN date) and not
    # on `ts`. `ts` is the wall-clock append time, which the schema itself calls
    # informational: the 35y regime backfill appended 376 RegimeEvents dated
    # 1991-2026 in three days of July 2026, so an `ts` bound would empty the log
    # at every historical date and hide from the Worker the regime history that
    # WAS knowable at t. `event_date` keeps exactly that and drops the rest.
    ("event_log", "event_date > :t"),
)

# The two regime columns that answer "as of WHEN?" rather than "what happened?".
# Shared, because `advance_as_of_snapshot` has to re-apply them at the new t: a
# regime that looked open at the old t may have closed inside the interval, and a
# newly confirmed regime takes over `is_current`.
_REGIME_ASOF_REPAIRS: tuple[tuple[str, str], ...] = (
    # A regime open at t must LOOK open at t, and "open at t" means its CLOSURE
    # was not yet knowable — not merely that its retroactive `end_date` is in
    # the future.
    #
    # THIS KEYED ON `end_date` UNTIL 2026-08-15 and closed only half of the half
    # it claimed (I-49). A regime whose end_date fell BEFORE t was left closed
    # even when the three confirming prints that made the closure knowable
    # landed after t — so the Worker read, as settled history, a regime nobody
    # on that date knew had ended. `updated_at` on a closed row IS that
    # confirming print (`market.regime._commit_regime` writes both in one
    # transaction), and it is always later than `end_date`, so keying on it
    # subsumes the old rule instead of replacing it.
    ("regime", "UPDATE regime SET end_date = NULL WHERE date(updated_at) > :t"),
    # `is_current` marks 2026's current regime. As-of t it is the latest VISIBLE
    # one — the same `max(created_at, id)` rule replay.py resolves the as-of
    # regime with, so the two paths cannot disagree.
    ("regime", "UPDATE regime SET is_current = 0"),
    (
        "regime",
        "UPDATE regime SET is_current = 1 WHERE id = "
        "(SELECT id FROM regime ORDER BY created_at DESC, id DESC LIMIT 1)",
    ),
)

# Deleting rows is not enough: some columns on SURVIVING rows are written by the
# future and would leak it.
_REPAIRS: tuple[tuple[str, str], ...] = (
    # The Portfolio vertex's indicators are the LATEST aggregate, recomputed by
    # UC6 on every cycle — every one of them is a 2026 number sitting on a row
    # with no date of its own. Blanking them is not data loss: hydrating the
    # snapshot re-runs UC6, which refills them as-of t. What survives blank are
    # the DISABLED books (the three market-signal ones, `enabled = 0`), which
    # `value_portfolios` skips — and NULL is the honest reading there: not
    # valued at t. Leaving them would hand `db_query` the 2026 Sortino of a book
    # the replayed agent has no valuation for.
    (
        "portfolio",
        "UPDATE portfolio SET sharpe_rolling = NULL, sortino_rolling = NULL, "
        "calmar_rolling = NULL, max_drawdown = NULL, volatility = NULL, return_3m = NULL, "
        "return_6m = NULL, return_1y = NULL, return_3y = NULL, return_5y = NULL",
    ),
    *_REGIME_ASOF_REPAIRS,
    # A verdict is written at +12w (`proposal_outcome_weeks`). A proposal made
    # inside that window before t has NO outcome yet, and `user_response` /
    # `paper_started` follow the same clock.
    (
        "proposal",
        "UPDATE proposal SET outcome = NULL, user_response = 'pending', paper_started = NULL "
        'WHERE date("date") > :pending_from',
    ),
)


def build_as_of_snapshot(source: Path, dest: Path, as_of: date) -> Path:
    """Copy `source` to `dest` and delete everything not knowable at `as_of`.

    Returns `dest`, ready to be opened by an `InvestmentDB` the Planner and
    Worker are handed instead of the live one. The caller owns the file's
    lifetime — one snapshot per decision date, deleted after.
    """
    if dest.exists():
        raise FileExistsError(f"as-of snapshot {dest} already exists — refusing to overwrite")
    dest.parent.mkdir(parents=True, exist_ok=True)

    # sqlite3's own backup API, not a file copy: `source` is a live WAL database
    # that the agent may be writing, and only the backup API guarantees a
    # transactionally consistent copy of it.
    src_con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(dest)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()

    # isolation_level=None — true autocommit, then ONE explicit BEGIN below, the
    # same discipline as InvestmentDB (ADR-004). sqlite3's legacy mode would
    # auto-BEGIN before the DELETEs but leave the DROP TRIGGER outside, so a
    # failed prune could roll the deletes back and still leave the copy's
    # append-only guard removed.
    con = sqlite3.connect(dest, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        row = con.execute(
            "SELECT value FROM system_thresholds WHERE key = 'proposal_outcome_weeks'"
        ).fetchone()
        outcome_weeks = float(row[0]) if row else 12.0
        params = {
            "t": as_of.isoformat(),
            "pending_from": (as_of - timedelta(weeks=outcome_weeks)).isoformat(),
        }
        # The append-only triggers guard the AGENT's log and would refuse the
        # event_log DELETE below. They are lifted on the COPY and put back from
        # their own recorded DDL — not a loophole in the guarantee: the copy is a
        # throwaway, and the replay that then runs against it still cannot
        # rewrite the log it appends to. Read from sqlite_master rather than
        # named here, so renaming a trigger in schema.py cannot silently leave
        # the snapshot unprotected.
        triggers = con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'event_log'"
        ).fetchall()

        # ONE transaction: a half-pruned snapshot must never survive, and neither
        # must one whose append-only guard was dropped and not put back.
        con.execute("BEGIN")
        try:
            for name, _ddl in triggers:
                con.execute(f"DROP TRIGGER {name}")
            for table, predicate in _PRUNE:
                con.execute(f"DELETE FROM {table} WHERE {predicate}", params)
            for _table, stmt in _REPAIRS:
                con.execute(stmt, params)
            for _name, ddl in triggers:
                con.execute(ddl)
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
        con.execute("VACUUM")  # 94 MB of freed pages per decision date, else kept
    finally:
        con.close()
    return dest


def advance_as_of_snapshot(snapshot: Path, source: Path, from_t: date, to_t: date) -> None:
    """Move an existing snapshot's clock from `from_t` to `to_t` in place, by
    topping up the WORLD from `source` and leaving everything else alone.

    THIS IS WHAT MAKES THE REPLAY A SEQUENCE. Rebuilding a fresh snapshot per
    decision date reruns each month in isolation: `held_allocation` reads the
    last EMITTED market-signal proposal, finds none in a fresh copy, and every
    month replays as an opening entry. Measured on the live database, October,
    November and December 2008 each emitted a proposal for the SAME book, where
    the live chain emits one and holds twice — three times the proposals and
    three times the trading costs, on a decision that never changed.

    The obvious fix is to carry the agent's artefacts forward from the previous
    snapshot. This is the opposite, and it is the reason it is safer: nothing
    about the AGENT is enumerated here. Advancing the clock touches only the four
    observation tables and leaves proposals, citations, the event log, the
    corpus, the confrontations and every conviction nudge exactly where the
    replay put them. A carry-forward list would be correct today and silently
    wrong the moment the Worker starts writing knowledge — and the failure would
    look like an agent that learns nothing, not like a bug.

    NOT topped up, each for its own reason:
      - `event_log` — mixed: the replay's own appends plus history already
        loaded at build time. Re-inserting live rows would also fight the
        monotonic-ULID guarantee, since live ids come from a different clock.
        The cost is a few historical RegimeEvents inside the interval; the
        `regime` table itself IS topped up, and that is what the baseline reads.
      - Derived artefacts (`portfolio_weekly_snapshot`, `scenario_probability`,
        `favors`) — hydration rewrites them at each date.
      - `proposal`, `proposal_cites`, `evaluation` — the agent's history, which
        is precisely what must survive.
    """
    if to_t <= from_t:
        raise ValueError(f"as-of snapshot advances forward only: {from_t} -> {to_t}")

    con = sqlite3.connect(snapshot, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("ATTACH DATABASE ? AS live", (f"file:{source}?mode=ro",))
        params = {"from_t": from_t.isoformat(), "to_t": to_t.isoformat()}

        con.execute("BEGIN")
        try:
            # Parents before children here — the mirror image of the prune's
            # order, for the same foreign keys.
            for table, expr in _WORLD_OBSERVATIONS:
                con.execute(
                    f"INSERT OR IGNORE INTO {table} SELECT * FROM live.{table} "
                    f"WHERE {expr} > :from_t AND {expr} <= :to_t",
                    params,
                )
            con.execute(
                "INSERT OR IGNORE INTO backtest SELECT * FROM live.backtest WHERE regime_id IN "
                "(SELECT id FROM live.regime WHERE date(created_at) > :from_t "
                " AND date(created_at) <= :to_t)",
                params,
            )
            # `end_date` was NULLed at build time for regimes still open then.
            # Some of those closures became knowable inside the interval, so the
            # column is restored wholesale from `live` and re-hidden at the new t
            # — reading the snapshot's own value could not tell "open" from
            # "closed, and we blanked it".
            con.execute(
                "UPDATE regime SET end_date = "
                "(SELECT r.end_date FROM live.regime r WHERE r.id = regime.id)"
            )
            for _table, stmt in _REGIME_ASOF_REPAIRS:
                con.execute(stmt, {"t": to_t.isoformat()})
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
        con.execute("DETACH DATABASE live")
    finally:
        # close() alone: DETACH here would raise over the real error whenever the
        # ATTACH itself was what failed.
        con.close()


def snapshot_path(scratch: Path, as_of: date) -> Path:
    """Where one decision date's throwaway snapshot lives."""
    return scratch / f"as-of-{as_of.isoformat()}.db"


def _main() -> None:
    """`python -m investment.db.as_of_snapshot <as_of> <dest>` — build one
    snapshot by hand, to inspect what a replayed date can actually see."""
    import argparse

    from investment.config import Settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("as_of", type=date.fromisoformat)
    parser.add_argument("dest", type=Path)
    args = parser.parse_args()

    # pydantic-settings fills every field from .env at runtime, which mypy
    # cannot see — the same ignore replay.py's CLI carries.
    settings = Settings()  # type: ignore[call-arg]
    built = build_as_of_snapshot(Path(settings.db_path), args.dest, args.as_of)
    print(f"as-of {args.as_of} snapshot: {built} ({built.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    _main()
