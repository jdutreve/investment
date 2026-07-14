"""`invest` CLI (docs/TASKS.md Task 6ter.2) — partial: `sql`, `status`,
`invariants`, `regime`, `ranking`, `nav`. The rest of the command set
(proposals, accept/reject, chat, ...) arrives once ops/api.py and the agent
process exist (M9-M10); this is the "falls back to direct read-only SQLite
when the agent is down" path, used unconditionally for now since there is no
agent process yet.

Reads are direct on SQLite, read-only (ADR-005 one-command-layer rule:
writes only ever go through the running agent — see docs/DECISIONS.md).
"""

import argparse
import contextlib
import dataclasses
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from investment.config import Settings
from investment.market.regime import RegimeThresholds, SeriesHistory
from investment.market.regime import audit as regime_audit

_STATUS_ENTITY_COUNTS = (
    "framework",
    "regime_type",
    "invariant",
    "strategy",
    "scenario",
    "portfolio",
)


class _Ansi:
    """Plain ANSI escapes — no new dependency for a handful of colors
    (CLAUDE.md Stack: nothing beyond the listed frameworks). Never applied to
    --json output: that has to stay valid, unescaped JSON for scripting
    (`| jq`, `| python -c ...`)."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    YELLOW = "\033[33m"


def _color_enabled() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _c(text: str, *codes: str) -> str:
    if not _color_enabled():
        return text
    return f"{''.join(codes)}{text}{_Ansi.RESET}"


def _label(text: str) -> str:
    return _c(text, _Ansi.DIM)


def _value(text: str) -> str:
    return _c(text, _Ansi.BOLD, _Ansi.CYAN)


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _unnest_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    """Columns stored as JSON1 TEXT (MAP/STRING[], DATA_MODELS.md 'Physical
    mapping') come back from SQLite as JSON-encoded strings; parse them back
    into native lists/dicts so --json nests them instead of double-encoding
    (`"tags": "[\\"a\\"]"` -> `"tags": ["a"]`)."""
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, str) and value[:1] in "[{":
            with contextlib.suppress(ValueError):
                value = json.loads(value)
        result[key] = value
    return result


def cmd_sql(db_path: Path, query: str, as_json: bool) -> None:
    con = _connect_readonly(db_path)
    try:
        rows = con.execute(query).fetchall()
    except sqlite3.Error as e:
        print(_c(f"error: {e}", _Ansi.RED), file=sys.stderr)
        raise SystemExit(1) from e
    finally:
        con.close()

    if as_json:
        print(
            json.dumps(
                [_unnest_json_columns(dict(row)) for row in rows], indent=2, ensure_ascii=False
            )
        )
        return

    if not rows:
        print(_c("(no rows)", _Ansi.DIM))
        return
    cols = rows[0].keys()
    print(_c(" | ".join(cols), _Ansi.BOLD, _Ansi.CYAN))
    for row in rows:
        print(" | ".join(str(row[c]) for c in cols))


def _resolve_regime_type_id(con: sqlite3.Connection, regime: str) -> str:
    """--regime accepts either the canonical RegimeType id or one of its
    aliases (e.g. 'stagflation' -> 'falling-growth-rising-inflation') —
    tags always store the canonical id."""
    row = con.execute(
        "SELECT id FROM regime_type "
        "WHERE id = :regime "
        "   OR EXISTS (SELECT 1 FROM json_each(aliases) WHERE value = :regime)",
        {"regime": regime},
    ).fetchone()
    return str(row["id"]) if row else regime


def cmd_invariants(
    db_path: Path,
    regime: str | None,
    tag: str | None,
    status: str | None,
    top: int | None,
    as_json: bool,
) -> None:
    """docs/TASKS.md Task 6ter.2 `invest invariants` — ranked by
    weight_effective DESC, the same conviction ordering the Worker reads."""
    where = []
    params: dict[str, str] = {}
    con = _connect_readonly(db_path)
    if regime is not None:
        where.append("EXISTS (SELECT 1 FROM json_each(invariant.tags) WHERE value = :regime_tag)")
        params["regime_tag"] = f"regime:{_resolve_regime_type_id(con, regime)}"
    if tag is not None:
        where.append("EXISTS (SELECT 1 FROM json_each(invariant.tags) WHERE value = :tag)")
        params["tag"] = tag
    if status is not None:
        where.append("status = :status")
        params["status"] = status

    query = (
        "SELECT id, title, author, status, weight_effective, tags FROM invariant"
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + " ORDER BY weight_effective DESC"
        + (" LIMIT :top" if top is not None else "")
    )
    if top is not None:
        params["top"] = str(top)

    try:
        rows = con.execute(query, params).fetchall()
    finally:
        con.close()

    if as_json:
        print(
            json.dumps(
                [_unnest_json_columns(dict(row)) for row in rows], indent=2, ensure_ascii=False
            )
        )
        return

    if not rows:
        print(_c("(no invariants match)", _Ansi.DIM))
        return
    header = f"{'id':38s} {'author':7s} {'status':10s} {'weight':7s} tags"
    print(_c(header, _Ansi.BOLD, _Ansi.CYAN))
    for row in rows:
        tags = ", ".join(json.loads(row["tags"])) if row["tags"] else ""
        weight = f"{row['weight_effective']:.2f}" if row["weight_effective"] is not None else "-"
        author = row["author"] or "-"
        print(f"{row['id']:38s} {author:7s} {row['status']:10s} {weight:7s} {tags}")


def cmd_status(db_path: Path, as_json: bool) -> None:
    con = _connect_readonly(db_path)
    try:
        defender = con.execute("SELECT id, name FROM portfolio WHERE defender = 1").fetchone()
        last_chain = con.execute(
            "SELECT last_chain_success FROM detector_state WHERE id = 'singleton'"
        ).fetchone()
        pending = con.execute(
            "SELECT COUNT(*) AS n FROM proposal WHERE user_response = 'pending'"
        ).fetchone()
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in _STATUS_ENTITY_COUNTS
        }
    finally:
        con.close()

    if as_json:
        print(
            json.dumps(
                {
                    "defender": dict(defender) if defender else None,
                    "last_chain_success": last_chain["last_chain_success"] if last_chain else None,
                    "pending_proposals": pending["n"],
                    "seed_counts": counts,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    defender_text = f"{defender['name']} ({defender['id']})" if defender else "(none)"
    chain_text = last_chain["last_chain_success"] if last_chain else "(never)"
    pending_color = _Ansi.YELLOW if pending["n"] else _Ansi.CYAN

    print(f"{_label('defender:')} {_value(defender_text)}")
    print(f"{_label('last chain success:')} {_value(chain_text)}")
    print(f"{_label('pending proposals:')} {_c(str(pending['n']), _Ansi.BOLD, pending_color)}")
    print(f"{_label('seed counts:')} {_value(str(counts))}")


def _fetch_regime_rows(con: sqlite3.Connection, history: bool) -> list[dict[str, Any]]:
    where = "" if history else "WHERE regime.is_current = 1"
    query = (
        "SELECT regime.id, regime.regime_type_id, regime_type.name, "
        "regime.start_date, regime.end_date, regime.confidence, regime.tags, "
        "regime.is_current "
        "FROM regime JOIN regime_type ON regime_type.id = regime.regime_type_id "
        f"{where} ORDER BY regime.start_date"
    )
    return [dict(row) for row in con.execute(query).fetchall()]


def cmd_regime(db_path: Path, history: bool, as_json: bool) -> None:
    """docs/MILESTONES.md M3 DoV `invest regime --history` — episodes ranked
    chronologically (or just the current one, without --history)."""
    con = _connect_readonly(db_path)
    try:
        rows = _fetch_regime_rows(con, history)
    finally:
        con.close()

    if as_json:
        print(json.dumps([_unnest_json_columns(row) for row in rows], indent=2, ensure_ascii=False))
        return

    if not rows:
        print(_c("(no regime history — run the seed or catch-up first)", _Ansi.DIM))
        return
    header = f"{'name':32s} {'start':10s} {'end':10s} {'conf':5s} tags"
    print(_c(header, _Ansi.BOLD, _Ansi.CYAN))
    for row in rows:
        tags = ", ".join(json.loads(row["tags"])) if row["tags"] else ""
        end = row["end_date"] or ("current" if row["is_current"] else "-")
        conf = f"{row['confidence']:.0f}" if row["confidence"] is not None else "-"
        print(f"{row['name']:32s} {row['start_date']:10s} {end:10s} {conf:5s} {tags}")


def _series_history(con: sqlite3.Connection, ticker: str) -> SeriesHistory:
    rows = [
        dict(row)
        for row in con.execute(
            "SELECT ts, level, speed, acceleration FROM market_data WHERE ticker = ? ORDER BY ts",
            (ticker,),
        ).fetchall()
    ]
    return SeriesHistory(ts=[row["ts"] for row in rows], rows=rows)


def cmd_regime_audit(db_path: Path, as_json: bool) -> None:
    """docs/MILESTONES.md M3 DoV 'STABILITY AUDIT (#4)' — an independent,
    from-scratch replay of the full market_data history via the pure
    `regime.audit()` (no `InvestmentDB`: reads stay on the CLI's read-only
    connection, per ADR-005)."""
    con = _connect_readonly(db_path)
    try:
        threshold_rows = [
            dict(row) for row in con.execute("SELECT key, value FROM system_thresholds").fetchall()
        ]
        thresholds = RegimeThresholds.from_rows(threshold_rows)
        growth = _series_history(con, "GROWTH_COMPOSITE")
        inflation = _series_history(con, "CPIAUCSL")
        liquidity = _series_history(con, "GLOBAL_LIQUIDITY")
        vix = _series_history(con, "^VIX")
    finally:
        con.close()

    report = regime_audit(growth, inflation, liquidity, vix, thresholds)

    if as_json:
        print(json.dumps(dataclasses.asdict(report), indent=2, ensure_ascii=False))
        return

    whipsaw_color = _Ansi.YELLOW if report.whipsaw_count else _Ansi.CYAN
    median = f"{report.median_episode_days:.0f}d" if report.median_episode_days is not None else "-"
    lag = f"{report.mean_detector_lag_days:.0f}d / {report.max_detector_lag_days}d"

    print(f"{_label('episodes:')} {_value(str(report.episode_count))}")
    print(
        f"{_label('whipsaws (reversed <=3mo):')} "
        f"{_c(str(report.whipsaw_count), _Ansi.BOLD, whipsaw_color)}"
    )
    print(f"{_label('median episode length:')} {_value(median)}")
    print(f"{_label('detector lag (mean / max):')} {_value(lag)}")
    print(f"{_label('raw candidate switches:')} {_value(str(report.raw_candidate_switches))}")
    print(f"{_label('suppressed by hysteresis:')} {_value(str(report.suppressed_switches))}")


_SPARKLINE_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float], width: int = 60) -> str:
    """Terminal sparkline (docs/MILESTONES.md M4 'invest nav <id> terminal
    sparkline') — evenly-sampled down to `width` points, scaled to the
    series' own min/max."""
    if not values:
        return ""
    if len(values) > width:
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    last = len(_SPARKLINE_CHARS) - 1
    return "".join(_SPARKLINE_CHARS[min(last, int((v - lo) / span * last))] for v in values)


def cmd_ranking(db_path: Path, snapshot_date: str | None, as_json: bool) -> None:
    """docs/TASKS.md Task 6ter.2 `invest ranking [--date D]` — the latest (or
    a given date's) portfolio_weekly_snapshot table."""
    con = _connect_readonly(db_path)
    try:
        if snapshot_date is None:
            latest = con.execute("SELECT MAX(date) AS d FROM portfolio_weekly_snapshot").fetchone()
            snapshot_date = latest["d"] if latest else None
        rows = (
            []
            if snapshot_date is None
            else [
                dict(row)
                for row in con.execute(
                    "SELECT rank, portfolio_id, defender, sortino_rolling, calmar_rolling, "
                    "max_drawdown, recommendation, gap_to_defender FROM portfolio_weekly_snapshot "
                    "WHERE date = ? ORDER BY rank",
                    (snapshot_date,),
                ).fetchall()
            ]
        )
    finally:
        con.close()

    if as_json:
        print(json.dumps([_unnest_json_columns(row) for row in rows], indent=2, ensure_ascii=False))
        return

    if not rows:
        print(_c("(no snapshot yet — run the seed or the weekly chain first)", _Ansi.DIM))
        return
    print(f"{_label('date:')} {_value(str(snapshot_date))}")
    header = (
        f"{'rank':4s} {'portfolio':30s} {'def':3s} {'sortino':8s} {'calmar':8s} "
        f"{'max_dd':8s} recommendation"
    )
    print(_c(header, _Ansi.BOLD, _Ansi.CYAN))
    for row in rows:
        defender_mark = "*" if row["defender"] else ""
        sortino = f"{row['sortino_rolling']:.2f}" if row["sortino_rolling"] is not None else "-"
        calmar = f"{row['calmar_rolling']:.2f}" if row["calmar_rolling"] is not None else "-"
        mdd = f"{row['max_drawdown']:.3f}" if row["max_drawdown"] is not None else "-"
        print(
            f"{row['rank']:<4d} {row['portfolio_id']:30s} {defender_mark:3s} "
            f"{sortino:8s} {calmar:8s} {mdd:8s} {row['recommendation']}"
        )


def cmd_nav(db_path: Path, portfolio_id: str, as_json: bool) -> None:
    """docs/TASKS.md Task 6ter.2 `invest nav <portfolio_id>` — NAV series +
    terminal sparkline (docs/MILESTONES.md M4)."""
    con = _connect_readonly(db_path)
    try:
        rows = [
            dict(row)
            for row in con.execute(
                "SELECT ts, nav, daily_return, sharpe_rolling, sortino_rolling, calmar_rolling, "
                "drawdown, vs_benchmark FROM portfolio_nav WHERE portfolio_id = ? ORDER BY ts",
                (portfolio_id,),
            ).fetchall()
        ]
    finally:
        con.close()

    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if not rows:
        print(_c(f"(no NAV history for {portfolio_id})", _Ansi.DIM))
        return
    latest = rows[-1]
    print(f"{_label('portfolio:')} {_value(portfolio_id)}")
    date_range = f"{rows[0]['ts']} -> {latest['ts']} ({len(rows)} rows)"
    print(f"{_label('range:')} {_value(date_range)}")
    nav_str = f"{latest['nav']:.2f}"
    print(f"{_label('nav:')} {_value(nav_str)}")
    for key in (
        "daily_return",
        "sharpe_rolling",
        "sortino_rolling",
        "calmar_rolling",
        "drawdown",
        "vs_benchmark",
    ):
        val = latest[key]
        print(f"{_label(key + ':')} {_value(f'{val:.4f}') if val is not None else '-'}")
    navs = [row["nav"] for row in rows if row["nav"] is not None]
    print(f"{_label('sparkline:')} {_c(_sparkline(navs), _Ansi.CYAN)}")


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    parser = argparse.ArgumentParser(prog="invest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sql_parser = subparsers.add_parser("sql", help="read-only SQL query")
    sql_parser.add_argument("query")
    sql_parser.add_argument("--json", action="store_true", help="output as JSON")

    status_parser = subparsers.add_parser(
        "status", help="regime, defender, last chain, pending items"
    )
    status_parser.add_argument("--json", action="store_true", help="output as JSON")

    invariants_parser = subparsers.add_parser(
        "invariants", help="list invariants, ranked by weight_effective"
    )
    invariants_parser.add_argument("--regime", help="filter by regime tag, e.g. stagflation")
    invariants_parser.add_argument("--tag", help="filter by an exact tag, e.g. asset:GLD")
    invariants_parser.add_argument("--status", help="'proposed' | 'integrated' | 'rejected'")
    invariants_parser.add_argument("--top", type=int, help="limit to the top N")
    invariants_parser.add_argument("--json", action="store_true", help="output as JSON")

    regime_parser = subparsers.add_parser(
        "regime", help="current regime, --history for all episodes, --audit for M3 stability audit"
    )
    regime_parser.add_argument("--history", action="store_true", help="list every episode")
    regime_parser.add_argument(
        "--audit", action="store_true", help="stability audit (whipsaws, lag, hysteresis)"
    )
    regime_parser.add_argument("--json", action="store_true", help="output as JSON")

    ranking_parser = subparsers.add_parser(
        "ranking", help="latest (or --date) portfolio_weekly_snapshot table"
    )
    ranking_parser.add_argument("--date", help="ISO date; defaults to the latest snapshot")
    ranking_parser.add_argument("--json", action="store_true", help="output as JSON")

    nav_parser = subparsers.add_parser(
        "nav", help="NAV series + terminal sparkline for a portfolio"
    )
    nav_parser.add_argument("portfolio_id")
    nav_parser.add_argument("--json", action="store_true", help="output as JSON")

    args = parser.parse_args()
    if args.command == "sql":
        cmd_sql(settings.db_path, args.query, args.json)
    elif args.command == "status":
        cmd_status(settings.db_path, args.json)
    elif args.command == "invariants":
        cmd_invariants(settings.db_path, args.regime, args.tag, args.status, args.top, args.json)
    elif args.command == "regime":
        if args.audit:
            cmd_regime_audit(settings.db_path, args.json)
        else:
            cmd_regime(settings.db_path, args.history, args.json)
    elif args.command == "ranking":
        cmd_ranking(settings.db_path, args.date, args.json)
    elif args.command == "nav":
        cmd_nav(settings.db_path, args.portfolio_id, args.json)


if __name__ == "__main__":
    main()
