"""The macro board read in plain words, with the lighthouses it lights.

WHY THIS EXISTS (owner, 2026-10-11: "donne un résumé MACRO simple et didactique
en sortant les invariants qui le soutiennent"). The board gave eighteen
readings and one line naming the four that stood out; the lighthouses were
listed at the foot of the page by weight, with no link to the tape. The two
belong together: an invariant is a claim of the form "when the macro looks like
THIS, THAT follows", so the ones worth showing beside a reading of the macro are
those whose condition holds today on the very series being read.

So each theme of the board (growth, inflation, rates, credit, money, markets)
carries its sentence and the invariants CLEARLY LIT by it, reading at least one
series of that theme.

CLEARLY, WITH A MARGIN (owner, 2026-10-17: "une marge"). The Planner's test —
`planner.context.condition_active_now` — is true at the letter: on 2026-10-11
"when growth is rising and inflation is rising, bonds underperform" was lit by a
growth speed of +0.10, which the tile beside it called flat, and it sat over a
month whose book is the bond book as if it contradicted the order. Here every
inequality must hold by a quarter of what is usual for that feature of that
series (`QUIET_MOVE` times its ten-year standard deviation) — the margin under which
the board itself calls a move nothing. A condition met by a hair is not shown.
The Planner keeps its own test: this is what a reader is shown, not what the
Worker reads. Each is shown as its claim alone;
the panel says once that none of them is established (owner decision D5), and
the records are on the Lighthouses card.

It reports and decides nothing. No weight, verdict or allocation reads it.
"""

import json
import operator
import statistics
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from investment.db.seed_data import SIGNAL_ALIASES
from investment.db.sqlite import InvestmentDB
from investment.market.macro_board import (
    CHANGE_LOOKBACK_DAYS,
    COMPARISON_YEARS,
    QUIET_MOVE,
    asset_moves,
    highlights,
    macro_board,
    overall,
    read_themes,
)
from investment.mechanical.market_signal import STACK_TICKERS

# How many lit invariants a theme shows: the heaviest, the rest are on the
# Worker's side of the fence, not the reader's.
INVARIANTS_PER_THEME = 2


_FEATURES = ("level", "speed", "acceleration")
_PLAIN_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
}
# For an inequality, which side of the threshold the value must be on: +1 above.
_SIDE = {">": 1, ">=": 1, "<": -1, "<=": -1}


def holds_clearly(
    condition: list[dict[str, Any]],
    latest: dict[str, dict[str, float | None]],
    spread: dict[str, dict[str, float]],
    regime_type: str | None,
) -> bool:
    """Whether every predicate holds, each inequality by at least `QUIET_MOVE`
    of the usual spread of its feature (`spread[signal][feature]`). A missing
    reading or spread makes the condition not lit, as in the Planner's test:
    unknown must not read as true."""
    for predicate in condition:
        signal, feature, op, value = (predicate[k] for k in ("signal", "feature", "op", "value"))
        if signal == "regime":
            if regime_type is None or op not in _PLAIN_OPS:
                return False
            if not _PLAIN_OPS[op](regime_type, value):
                return False
            continue
        current = (latest.get(signal) or {}).get(feature)
        if current is None:
            return False
        if op in _PLAIN_OPS:
            if not _PLAIN_OPS[op](current, value):
                return False
            continue
        usual = spread.get(signal, {}).get(feature)
        if op not in _SIDE or not usual:
            return False
        if _SIDE[op] * (current - value) < QUIET_MOVE * usual:
            return False
    return True


async def _readings(
    db: InvestmentDB, signals: set[str], today: date
) -> tuple[dict[str, dict[str, float | None]], dict[str, dict[str, float]]]:
    """Per signal, its latest reading and the ten-year standard deviation of
    each feature — the same window the board ranks a level in."""
    since = today - timedelta(days=round(365.25 * COMPARISON_YEARS))
    latest: dict[str, dict[str, float | None]] = {}
    spread: dict[str, dict[str, float]] = {}
    for signal in signals:
        ticker = SIGNAL_ALIASES.get(signal)
        if ticker is None:
            continue
        rows = await db.query(
            "SELECT level, speed, acceleration FROM market_data "
            "WHERE ticker = :t AND ts >= :since AND ts <= :today ORDER BY ts",
            t=ticker,
            since=since.isoformat(),
            today=today.isoformat(),
        )
        if not rows:
            continue
        latest[signal] = {f: rows[-1][f] for f in _FEATURES}
        spread[signal] = {}
        for feature in _FEATURES:
            values = [float(r[feature]) for r in rows if r[feature] is not None]
            if len(values) > 1:
                spread[signal][feature] = statistics.pstdev(values)
    return latest, spread


def themes_of(condition: list[dict[str, Any]], group_of_ticker: dict[str, str]) -> list[str]:
    """The board themes a condition reads, in the condition's order. A signal
    the board does not show (the regime, a raw ticker) belongs to none."""
    groups = (group_of_ticker.get(SIGNAL_ALIASES.get(p["signal"], "")) for p in condition)
    return list(dict.fromkeys(group for group in groups if group))


async def macro_reading(db: InvestmentDB, today: date | None = None) -> dict[str, Any]:
    """The board, its summary, what stands out on it, per theme the sentence
    and the invariants it lights, and how each asset is moving."""
    today = today or date.today()
    payload = await macro_board(db, today)
    # The same board a month earlier, for what changed colour since.
    earlier = await macro_board(db, today - timedelta(days=CHANGE_LOOKBACK_DAYS))
    themes = read_themes(payload["board"])
    group_of_ticker = {entry["ticker"]: entry["group"] for entry in payload["board"]}

    rows = await db.query(
        "SELECT id, title, status, condition FROM invariant "
        "WHERE status IN ('integrated', 'proposed') "
        "AND condition != '[]' AND effect IS NOT NULL "
        # Established first, then by weight: the order retrieval already uses.
        "ORDER BY status = 'integrated' DESC, weight_effective DESC, id"
    )
    regime = await db.query("SELECT regime_type_id FROM regime WHERE is_current = 1 LIMIT 1")
    regime_type = str(regime[0]["regime_type_id"]) if regime else None
    conditions = {str(r["id"]): json.loads(r["condition"]) for r in rows}
    signals = {p["signal"] for c in conditions.values() for p in c if p["signal"] != "regime"}
    latest, spread = await _readings(db, signals, today)

    lit: dict[str, list[dict[str, Any]]] = {theme["group"]: [] for theme in themes}
    for row in rows:
        condition = conditions[str(row["id"])]
        if not holds_clearly(condition, latest, spread, regime_type):
            continue
        groups = [g for g in themes_of(condition, group_of_ticker) if g in lit]
        # Under the first theme it reads that still has room, once: the same
        # claim printed under three themes reads as three claims.
        home = next((g for g in groups if len(lit[g]) < INVARIANTS_PER_THEME), None)
        if home is None:
            continue
        # The claim and nothing else (owner, 2026-10-11): its title already says
        # what follows, and a record beside each line was noise on this panel.
        # The records are on the Lighthouses card.
        lit[home].append({"id": row["id"], "title": row["title"], "status": row["status"]})
    return {
        **payload,
        "overall": overall(themes),
        "highlights": highlights(payload["board"], earlier["board"]),
        "themes": [{**theme, "invariants": lit[theme["group"]]} for theme in themes],
        # The assets the rule can hold, as moves only: which way, how fast for
        # each, gathering or losing pace. The set is the stack's own so a sleeve
        # added to a book is read here without anyone listing it twice.
        "assets": await asset_moves(db, STACK_TICKERS, today),
    }
