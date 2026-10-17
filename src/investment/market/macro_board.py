"""The macro tape as ONE board: every series worth reading, under a name a
person recognises, with a sentence that says where it stands against its own
past and what it is doing.

WHY THIS EXISTS (owner, 2026-10-11: "Regime est difficile à lire et il manque
des valeurs MACRO et leur vitesse et accélération"). The Overview page showed a
regime label, a confidence and the liquidity composite — the detector's
CONCLUSION, without the readings it is drawn from. The series were all in
`market_data` with their level, speed and acceleration; nothing laid them out.

PROSE, NOT COLUMNS (owner, the same day: "remplace building, as-of et 2 years
par de la prose qui explique simplement la situation de chaque variable"). A
first version gave each series a momentum word, a date and a two-year curve,
and thirteen of eighteen read "building": true of the signs and useless to a
reader, because a move of four hundredths of a point and a move of forty were
the same word. A sentence can say both things a number alone cannot — WHERE the
level sits in the last ten years, and whether the move is large FOR THIS SERIES.

THE SENTENCES ARE MECHANICAL. No model writes them: a level is ranked in its
own ten years, a move is sized against that series' usual move, and the words
follow from the two ranks by the thresholds below. So the same print reads the
same on every front and on every run, and a sentence can be checked against the
figures beside it.

A REGISTRY, because a board is a choice. `market_data` also holds the raw
components of the composites (central-bank balance sheets, GDP, M2 in dollars);
those are inputs, not readings. A series named here that the database does not
hold is absent from the board — a young database is a legitimate state.

A COLOUR SAYS GOOD NEWS OR BAD (owner, 2026-10-11: "la couleur doit représenter
good or bad news"). A first version coloured the distance from normal on either
side, so a credit spread at its tightest and one at its widest were the same
orange, and the reader still had to work out which he was looking at. Each
series now declares which side is FAVOURABLE — for an investor building
capital: growth, credit and liquidity up, rates, inflation, unemployment,
spreads and volatility down — and the colour is where the level sits in its ten
years, read from that side. The direction of the move is read the same way,
improving or worsening. The polarity is a stated convention, one word per line
of the registry, not a measurement; a series with no honest side (gold against
its trend) declares none and stays grey.

Read-only, and it decides nothing: the regime detector and the market-signal
rule read these series themselves.
"""

import dataclasses
import statistics
from datetime import date, timedelta
from typing import Any, Literal

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.market.derivatives import (
    OBSERVATION_LOOKBACK_TICKERS,
    WEEKLY_LOOKBACK_DAYS_TICKERS,
    asof_lag,
)

# What a level and a move are compared with: the series' own last ten years.
COMPARISON_YEARS = 10
# Below this much history the comparison is not made, and the sentence says so.
MIN_COMPARISON_YEARS = 3
# Where a level is "high" or "low" in its ten years, and where it is at an end.
HIGH_RANK, EXTREME_RANK = 0.80, 0.95
# From which share of its ten years, counted from the unfavourable end, a level
# is good news. Under the median on purpose: a level a hair on the wrong side of
# its median is not news, and a hard split at 0.50 coloured it as a warning.
FAVOURABLE_FROM = 0.40
# A move against the series' USUAL move (the standard deviation of its speed):
# under a quarter of it nothing happened, at one it is a full move, at two a
# sharp one. The same scale sizes the acceleration.
QUIET_MOVE, FULL_MOVE, SHARP_MOVE = 0.25, 1.0, 2.0
# A print older than this is said to be old, in the sentence.
STALE_DAYS = 45
# How many series the one-line summary names.
SUMMARY_SERIES = 4


@dataclasses.dataclass(frozen=True)
class MacroLine:
    """One series of the board."""

    ticker: str
    label: str
    short: str  # how the one-line summary names it
    unit: str  # shown after the level: "%", "pts", "" for an index
    group: str
    decimals: int = 2
    # Which side of its history is good news; None when neither honestly is.
    favourable: Literal["high", "low"] | None = None


MACRO_BOARD: tuple[MacroLine, ...] = (
    MacroLine("GROWTH_COMPOSITE", "Growth composite (index)", "growth", "", "Growth", 1, "high"),
    MacroLine(
        "INDPRO", "Industrial production, year on year", "industry", "%", "Growth", 2, "high"
    ),
    MacroLine("UNRATE", "Unemployment rate", "unemployment", "%", "Growth", 1, "low"),
    MacroLine("CPIAUCSL", "Consumer prices, year on year", "inflation", "%", "Inflation", 2, "low"),
    MacroLine("^IRX", "3-month Treasury bill", "short rates", "%", "Rates", 2, "low"),
    MacroLine("DGS10", "10-year Treasury yield", "the 10-year yield", "%", "Rates", 2, "low"),
    MacroLine(
        "T10Y2Y", "Yield curve, 10-year minus 2-year", "the yield curve", "pts", "Rates", 2, "high"
    ),
    MacroLine(
        "real_rate",
        "Real short rate (bill minus inflation)",
        "the real short rate",
        "%",
        "Rates",
        2,
        "low",
    ),
    MacroLine(
        "real_yield_10y", "Real 10-year yield", "the real 10-year yield", "%", "Rates", 2, "low"
    ),
    MacroLine(
        "BAA10Y", "Credit spread, Baa minus 10-year", "credit spreads", "pts", "Credit", 2, "low"
    ),
    MacroLine(
        "CREDIT_GROWTH", "Bank credit, year on year", "bank credit", "%", "Credit", 2, "high"
    ),
    MacroLine("DEBT_TO_GDP", "Non-financial debt to GDP", "debt to GDP", "%", "Credit", 1, "low"),
    MacroLine("DRSFRMACBS", "Mortgage delinquency rate", "delinquencies", "%", "Credit", 2, "low"),
    MacroLine(
        "GLOBAL_LIQUIDITY", "Global liquidity (index)", "global liquidity", "", "Money", 1, "high"
    ),
    MacroLine("m2_yoy", "Broad money M2, year on year", "money growth", "%", "Money", 2, "high"),
    MacroLine("^VIX", "Equity volatility (VIX)", "volatility", "", "Markets", 1, "low"),
    MacroLine(
        "equity_trend",
        "Equities against their trend",
        "the equity trend",
        "%",
        "Markets",
        1,
        "high",
    ),
    MacroLine(
        "gold_10y_dev", "Gold/yield ratio against its 7-year trend", "gold's trend", "", "Markets"
    ),
)


def change_period(ticker: str, default_lookback_days: int) -> str:
    """Over what a series' speed is measured, in words — the same three cases
    `derivatives.compute_derivatives` branches on, so the board cannot label a
    one-print change as a monthly one."""
    if ticker in OBSERVATION_LOOKBACK_TICKERS:
        return "since the previous print"
    return f"over {WEEKLY_LOOKBACK_DAYS_TICKERS.get(ticker, default_lookback_days)} days"


def rank_in(level: float, history: list[float]) -> float | None:
    """The share of the history at or below `level`: 1.0 at its highest."""
    return sum(value <= level for value in history) / len(history) if history else None


def news(rank: float | None, favourable: str | None) -> str:
    """The board's colour: where a level sits in its ten years, read from the
    side that is good news for this series. `good` from
    `FAVOURABLE_FROM` up, `watch` under it, `poor` in the unfavourable fifth, `bad` at the
    unfavourable end — the thresholds the words already use."""
    if favourable is None:
        return "neutral"
    if rank is None:
        return "unknown"
    score = rank if favourable == "high" else 1.0 - rank
    if score <= 1.0 - EXTREME_RANK:
        return "bad"
    if score <= 1.0 - HIGH_RANK:
        return "poor"
    return "good" if score >= FAVOURABLE_FROM else "watch"


def position_words(rank: float | None, at_an_end: bool) -> tuple[str, str]:
    """`(sentence fragment, short form for the summary)` for where a level sits
    in its ten years. The short form is empty inside the usual range: a summary
    names what stands out."""
    if rank is None:
        return "too little history to compare", ""
    if rank >= EXTREME_RANK:
        return (
            ("the highest in ten years", "at a ten-year high")
            if at_an_end
            else (
                "near its ten-year high",
                "near a ten-year high",
            )
        )
    if rank >= HIGH_RANK:
        return "high for the last ten years", "high"
    if rank <= 1.0 - EXTREME_RANK:
        return (
            ("the lowest in ten years", "at a ten-year low")
            if at_an_end
            else (
                "near its ten-year low",
                "near a ten-year low",
            )
        )
    if rank <= 1.0 - HIGH_RANK:
        return "low for the last ten years", "low"
    return "within its usual range", ""


@dataclasses.dataclass(frozen=True)
class Move:
    """How a series is moving, sized against what is usual for THAT series."""

    quiet: bool  # under a quarter of its usual move: nothing happened
    direction: str = ""  # "rising", "falling sharply", "rising slightly"
    pace: str = ""  # "gathering pace" | "losing pace" | "" when the pace is steady

    @property
    def size(self) -> str:
        """`slight`, `full` or `sharp`; empty for a quiet series."""
        if self.quiet:
            return ""
        return {"sharply": "sharp", "slightly": "slight"}.get(self.direction.split()[-1], "full")

    @property
    def short(self) -> str:
        """The form the one-line summary uses: the direction alone, and only
        for a full move. A summary that carried every slight move and its pace
        ran to four lines and named nothing."""
        return "" if self.quiet or self.direction.endswith("slightly") else self.direction


def read_move(
    speed: float | None,
    acceleration: float | None,
    usual_move: float | None,
    usual_acceleration: float | None,
) -> Move | None:
    """`None` when the series has no speed yet or no history to size it with."""
    if speed is None or not usual_move:
        return None
    size = abs(speed) / usual_move
    if size < QUIET_MOVE:
        return Move(quiet=True)
    direction = "rising" if speed > 0 else "falling"
    if size >= SHARP_MOVE:
        direction += " sharply"
    elif size < FULL_MOVE:
        direction += " slightly"
    pace = ""
    if (
        acceleration is not None
        and usual_acceleration
        and abs(acceleration) / usual_acceleration >= QUIET_MOVE
    ):
        pace = "gathering pace" if (speed > 0) == (acceleration > 0) else "losing pace"
    return Move(quiet=False, direction=direction, pace=pace)


def _amount(value: float, line: MacroLine) -> str:
    unit = {"%": " pt", "pts": " pt"}.get(line.unit, "")
    return f"{value:+.{line.decimals}f}{unit}"


def _level(value: float, line: MacroLine) -> str:
    return f"{value:.{line.decimals}f}{' ' + line.unit if line.unit else ''}"


def describe(
    line: MacroLine,
    level: float,
    speed: float | None,
    acceleration: float | None,
    levels: list[float],
    speeds: list[float],
    accelerations: list[float],
    period: str,
    as_of: date,
    today: date,
    history_years: float,
) -> dict[str, Any]:
    """The sentence a reader gets for one series, the fragment the summary
    uses, and the same reading as fields a table can draw: where the level sits
    between its ten-year low and high, and which way it moves. Pure: the
    figures in, the words out."""
    compared = levels if history_years >= MIN_COMPARISON_YEARS else []
    rank = rank_in(level, compared)
    at_an_end = bool(compared) and (level >= max(compared) or level <= min(compared))
    position, short_position = position_words(rank, at_an_end)
    usual_move = statistics.pstdev(speeds) if len(speeds) > 1 else None
    usual_acceleration = statistics.pstdev(accelerations) if len(accelerations) > 1 else None
    move = read_move(speed, acceleration, usual_move, usual_acceleration)

    first = position[0].upper() + position[1:]
    if compared:
        first += f" (ten-year median {_level(statistics.median(compared), line)})"
    sentences = [first + "."]
    if move is not None and move.quiet:
        sentences.append(f"Little changed {period}.")
    elif move is not None and speed is not None:
        pace = {"gathering pace": ", and gathering pace", "losing pace": ", but losing pace"}
        sentences.append(
            f"{move.direction.capitalize()}: {_amount(speed, line)} {period}"
            f"{pace.get(move.pace, '')}."
        )
    if (today - as_of).days > STALE_DAYS:
        sentences.append(f"Last print {as_of.isoformat()}.")
    short_move = move.short if move is not None else ""
    fragment = " and ".join(part for part in (short_position, short_move) if part)
    return {
        "reading": " ".join(sentences),
        "headline": f"{line.short} {fragment}" if fragment else "",
        "news": news(rank, line.favourable),
        # Out of its usual range on EITHER side: the words are worth showing.
        "unusual": bool(short_position),
        "position": position,
        "rank": rank,
        "median": statistics.median(compared) if compared else None,
        "low": min(compared) if compared else None,
        "high": max(compared) if compared else None,
        # -1 falling, 0 quiet or unknown, +1 rising; and how large for this series.
        "direction": 0 if move is None or move.quiet or not speed else (1 if speed > 0 else -1),
        "move_size": move.size if move is not None else "",
        "pace": move.pace if move is not None else "",
        "favourable": line.favourable,
    }


def summarise(board: list[dict[str, Any]]) -> str:
    """One line naming the series that stand out most — the furthest from the
    middle of their ten years, then the sharpest movers."""
    notable = sorted(
        (entry for entry in board if entry["headline"]),
        key=lambda entry: -entry["salience"],
    )[:SUMMARY_SERIES]
    if not notable:
        return "Nothing stands out: every series is within its usual range and little changed."
    text = "; ".join(entry["headline"] for entry in notable)
    return text[0].upper() + text[1:] + "."


# How a theme reads, from the WORST news among its series: one unfavourable
# reading is what a reader needs to hear about a group, not the average of it.
NEWS_ORDER = ("good", "watch", "poor", "bad")
THEME_WORDS = {
    "good": "favourable",
    "watch": "a little less favourable than usual",
    "poor": "unfavourable",
    "bad": "near its worst in ten years",
}


# How many series a theme's sentence names: what matters, not the list.
THEME_FACTS = 2


def _moving(entry: dict[str, Any]) -> str:
    """A series' move in the fewest words: a slight move "edges", a full one
    rises or falls, a sharp one does so sharply; the pace only on a real move."""
    if not entry.get("direction"):
        return ""
    up = entry["direction"] > 0
    size = entry.get("move_size") or "full"
    if size == "slight":
        return "edging up" if up else "edging down"
    words = ("rising" if up else "falling") + (" sharply" if size == "sharp" else "")
    pace = {"gathering pace": " and accelerating", "losing pace": ", but slowing"}
    return words + pace.get(entry.get("pace") or "", "")


def _with_ticker(entry: dict[str, Any]) -> str:
    """A series as prose names it, with its ticker beside the name (owner,
    2026-10-17: every series or value shown carries its ticker), so a sentence
    can be traced to the tile and the row below it."""
    return f"{entry['short']} ({entry['ticker']})"


def _fact(entry: dict[str, Any]) -> str:
    """One series as a fact: its level, where that sits in ten years when it
    is unusual, and its move."""
    parts = [f"{_with_ticker(entry)} at {entry['level_text']}"]
    if entry.get("unusual"):
        parts.append(str(entry["position"]).replace("its ", "a "))
    if moving := _moving(entry):
        parts.append(moving)
    return ", ".join(parts)


def read_themes(board: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One plain sentence per group of the board, in the board's order. A group
    whose series all declare no favourable side has no reading and is left out.

    A SUMMARY, NOT THE LIST (owner, 2026-10-17: "un résumé pour rapidement
    prendre les choses importantes à savoir"). The board below carries every
    series with its level and move; the sentence says how the theme reads and
    the facts that make it read so — at most `THEME_FACTS` of them: the series
    that set its colour first, then any that stands out (an unusual level, a
    full move) — and nothing when nothing does."""
    themes: list[dict[str, Any]] = []
    for group in dict.fromkeys(entry["group"] for entry in board):
        members = [e for e in board if e["group"] == group]
        judged = [e for e in members if e["news"] in NEWS_ORDER]
        if not judged:
            continue
        worst = max((e["news"] for e in judged), key=NEWS_ORDER.index)
        culprits = [e for e in judged if worst != "good" and e["news"] == worst]
        standing_out = [
            e
            for e in members
            if e not in culprits and (e.get("unusual") or e.get("move_size") in ("full", "sharp"))
        ]
        by_salience = sorted(standing_out, key=lambda e: -float(e.get("salience") or 0))
        facts = [_fact(e) for e in (culprits + by_salience)[:THEME_FACTS]]
        sentence = THEME_WORDS[worst].capitalize()
        sentence += (": " + "; ".join(facts) + ".") if facts else "; nothing stands out."
        # WHERE THE TROUBLE IS, ITS PROBABLE CAUSE (owner, 2026-10-11: "pour
        # chaque constat ajoute la cause probable"): on the series that makes
        # the theme poor or bad, read toward its unfavourable side.
        if worst in ("poor", "bad") and culprits:
            culprit = culprits[0]
            way = 1 if culprit.get("favourable") == "low" else -1
            cause = probable_cause(culprit, way, board)
            if cause.startswith("Probable cause"):
                sentence += " " + cause
        themes.append({"group": group, "news": worst, "sentence": sentence})
    return themes


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# How far back "what changed" looks, and how many lines each kind of highlight
# may take: a list of what stands out that names everything names nothing.
CHANGE_LOOKBACK_DAYS = 30
MAX_EXTREMES, MAX_MOVERS, MAX_CHANGES = 3, 2, 2


# WHAT USUALLY DRIVES EACH SERIES, among the other series of the board: the
# driver, and +1 when the two move together, -1 when they move opposite (owner,
# 2026-10-11: "pour chaque constat ajoute la cause probable"). Like the
# favourable side, this is a STATED CONVENTION — textbook macro, one line per
# series — and not a measurement: nothing here was fitted or tested. It is used
# one way only: a highlight names those of its drivers that are, today, moving
# or standing the way that would produce it. When none is, it says so, rather
# than name a cause the board does not show.
DRIVERS: dict[str, tuple[tuple[str, int], ...]] = {
    "GROWTH_COMPOSITE": (("INDPRO", 1), ("UNRATE", -1), ("CREDIT_GROWTH", 1)),
    "INDPRO": (("GROWTH_COMPOSITE", 1), ("real_rate", -1)),
    "UNRATE": (("GROWTH_COMPOSITE", -1), ("INDPRO", -1)),
    "CPIAUCSL": (("m2_yoy", 1), ("GROWTH_COMPOSITE", 1)),
    "^IRX": (("CPIAUCSL", 1), ("GROWTH_COMPOSITE", 1)),
    "DGS10": (("real_yield_10y", 1), ("CPIAUCSL", 1), ("^IRX", 1)),
    "T10Y2Y": (("DGS10", 1), ("^IRX", -1)),
    "real_rate": (("^IRX", 1), ("CPIAUCSL", -1)),
    "real_yield_10y": (("DGS10", 1), ("^IRX", 1)),
    "BAA10Y": (("^VIX", 1), ("DRSFRMACBS", 1), ("GROWTH_COMPOSITE", -1)),
    "CREDIT_GROWTH": (("^IRX", -1), ("GROWTH_COMPOSITE", 1)),
    "DEBT_TO_GDP": (("CREDIT_GROWTH", 1), ("GROWTH_COMPOSITE", -1)),
    "DRSFRMACBS": (("UNRATE", 1), ("^IRX", 1)),
    "GLOBAL_LIQUIDITY": (("^IRX", -1), ("DGS10", -1), ("m2_yoy", 1)),
    "m2_yoy": (("CREDIT_GROWTH", 1), ("^IRX", -1)),
    "^VIX": (("BAA10Y", 1), ("equity_trend", -1)),
    "equity_trend": (("GROWTH_COMPOSITE", 1), ("real_yield_10y", -1), ("^VIX", -1)),
    "gold_10y_dev": (("real_yield_10y", -1), ("CPIAUCSL", 1)),
}


def probable_cause(entry: dict[str, Any], way: int, board: list[dict[str, Any]]) -> str:
    """Which of a series' usual drivers are, on this board, consistent with it
    being high or rising (`way` +1) or low or falling (-1): a driver counts when
    it MOVES the right way, or STANDS high or low on the right side."""
    by_ticker = {e["ticker"]: e for e in board}
    found: list[str] = []
    for ticker, sign in DRIVERS.get(entry["ticker"], ()):
        driver = by_ticker.get(ticker)
        if driver is None:
            continue
        needed = way * sign
        if driver["direction"] == needed:
            found.append(f"{_with_ticker(driver)} {'rising' if needed > 0 else 'falling'}")
        elif driver["rank"] is not None and (
            driver["rank"] >= HIGH_RANK if needed > 0 else driver["rank"] <= 1 - HIGH_RANK
        ):
            found.append(f"{_with_ticker(driver)} {'high' if needed > 0 else 'low'}")
    if not found:
        return "No cause visible on this board."
    return f"Probable cause: {_listed(found)}."


def _named(entry: dict[str, Any]) -> str:
    short = str(entry["short"]).removeprefix("the ")
    return f"{short[0].upper() + short[1:]} ({entry['ticker']})"


def _improving(entry: dict[str, Any]) -> bool | None:
    """Whether the series moves toward its favourable side; None when it is
    quiet or declares no side."""
    if not entry["direction"] or entry["favourable"] is None:
        return None
    return bool((entry["direction"] > 0) == (entry["favourable"] == "high"))


def highlights(board: list[dict[str, Any]], earlier: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What a reader should not miss, most unusual first — each line with its
    `kind` and the `tone` it is coloured in:

    - a level at an END of its ten years, on either side: the unusual is worth
      reading when it is good news too;
    - a FULL move (sized against that series' own usual move) not already named;
    - a series whose colour CHANGED since `earlier`, the board a month before;
    - the direction of travel: how many series are worsening against improving.

    Each line carries its PROBABLE CAUSE (`probable_cause`): the usual drivers
    of that series which, on this board, point the same way.

    RESTORED 2026-10-17 (owner): the extremes and full moves were removed when
    the theme sentences said them; those sentences are now a hover on the theme
    name, and this is again the one place the page states them.

    WORDS ONLY (owner, 2026-10-11: "les chiffres sont inutiles et ajoutent du
    bruit, car déjà spécifiés"): every figure is on the series' own tile, and a
    summary that repeats them is the board a second time. Pure, and built from
    the board alone, so it cannot say what the tiles do not show."""
    lines: list[dict[str, str]] = []
    told: set[str] = set()

    extremes = sorted(
        (
            e
            for e in board
            if e["rank"] is not None and max(e["rank"], 1 - e["rank"]) >= EXTREME_RANK
        ),
        key=lambda e: -max(e["rank"], 1 - e["rank"]),
    )[:MAX_EXTREMES]
    for e in extremes:
        moving = ""
        if e["direction"]:
            moving = " and still " + ("rising" if e["direction"] > 0 else "falling")
        lines.append(
            {
                "kind": "At an extreme",
                "tone": e["news"],
                "text": f"{_named(e)} {e['position']}{moving}.",
                "cause": probable_cause(e, 1 if e["rank"] >= 0.5 else -1, board),
            }
        )
        told.add(e["ticker"])

    movers = [e for e in board if e["move_size"] in ("full", "sharp") and e["ticker"] not in told]
    movers.sort(key=lambda e: e["move_size"] != "sharp")
    for e in movers[:MAX_MOVERS]:
        verb = "rising" if e["direction"] > 0 else "falling"
        improving = _improving(e)
        # ONE COLOUR PER SERIES, its level's (owner, 2026-10-17): this line was
        # coloured by the direction of the move, so global liquidity read red
        # here and orange on its own tile. The direction is said in words.
        way = "" if improving is None else (", improving" if improving else ", worsening")
        lines.append(
            {
                "kind": "Moving",
                "tone": e["news"],
                "text": (
                    f"{_named(e)} {verb} "
                    f"{'much faster' if e['move_size'] == 'sharp' else 'faster'} than usual{way}."
                ),
                "cause": probable_cause(e, e["direction"], board),
            }
        )
        told.add(e["ticker"])

    before = {e["ticker"]: e["news"] for e in earlier}
    changed = [
        e
        for e in board
        if e["ticker"] not in told
        and e["news"] in NEWS_ORDER
        and before.get(e["ticker"]) in NEWS_ORDER
        and before[e["ticker"]] != e["news"]
    ]
    changed.sort(
        key=lambda e: -abs(NEWS_ORDER.index(e["news"]) - NEWS_ORDER.index(before[e["ticker"]]))
    )
    for e in changed[:MAX_CHANGES]:
        lines.append(
            {
                "kind": "Changed in a month",
                "tone": e["news"],
                "text": (
                    f"{_named(e)}: {THEME_WORDS[before[e['ticker']]]} a month ago, "
                    f"{THEME_WORDS[e['news']]} now."
                ),
                # Worse means it went away from its favourable side, better toward it.
                "cause": probable_cause(
                    e,
                    (1 if e["favourable"] == "low" else -1)
                    * (
                        1
                        if NEWS_ORDER.index(e["news"]) > NEWS_ORDER.index(before[e["ticker"]])
                        else -1
                    ),
                    board,
                ),
            }
        )

    travel = [_improving(e) for e in board]
    down, up = travel.count(False), travel.count(True)
    if down or up:
        # Where the worsening sits: the theme holding most of it.
        groups = [e["group"] for e, way in zip(board, travel, strict=True) if way is False]
        worst = max(set(groups), key=groups.count) if groups else ""
        lines.append(
            {
                "kind": "Direction of travel",
                "tone": "good" if up > down else ("watch" if up == down else "poor"),
                "text": (
                    f"{down} series worsening, {up} improving, {len(board) - down - up} steady."
                ),
                "cause": (
                    f"Mostly {worst.lower()}: {groups.count(worst)} of the {down} worsening."
                    if worst
                    else ""
                ),
            }
        )
    return lines


async def _default_lookback_days(db: InvestmentDB) -> int:
    """The speed lookback of a series with none of its own — the one
    `derivatives.compute_derivatives` was run with."""
    lookback = await db.query(
        "SELECT value FROM system_thresholds WHERE key = 'derivative_lookback_short'"
    )
    return int(lookback[0]["value"]) if lookback else 30


# THE ASSETS, READ AS MOVES AND NOTHING ELSE (owner, 2026-10-17: "parle
# uniquement des assets en hausse, baisse, vitesse/accélération"). Beside the
# macro, what the assets are doing: rising or falling, how fast for that asset,
# and whether the move is gathering or losing pace. Not where they sit against
# a moving average, which is the trend overlay's vocabulary and belongs with the
# order.
#
# IN PERCENT, NOT IN DOLLARS. `market_data.speed` for a price is a dollar
# difference, and a price that has doubled in ten years makes the same
# percentage move twice as large in dollars, so sizing today's move against ten
# years of them would call every recent move "sharp". The change is therefore
# the return over the lookback, and its acceleration the change in that return
# over the same lookback — then sized by `read_move`, the board's own rule.
def describe_asset_move(
    change: float | None,
    acceleration: float | None,
    changes: list[float],
    accelerations: list[float],
    period: str,
) -> dict[str, Any]:
    """One asset's move in words and fields. `change` and `acceleration` are
    fractions (0.021 = +2.1%); the lists are the asset's own history of both,
    which say what a usual move is. Pure."""
    usual_change = statistics.pstdev(changes) if len(changes) > 1 else None
    usual_acceleration = statistics.pstdev(accelerations) if len(accelerations) > 1 else None
    move = read_move(change, acceleration, usual_change, usual_acceleration)
    # A STILL ASSET CAN BE TURNING (owner, 2026-10-17: "pourquoi Treasuries est
    # steady alors que l'accélération est élevée ?"). `read_move` gives a pace
    # only to a move that exists; an asset flat over the month whose speed moved
    # by a real amount — a fall that stopped, a rise that stalled — read
    # "steady" beside a bar that showed the turn. It is named by the side it is
    # turning towards, on the same quarter-of-usual threshold.
    pace = move.pace if move is not None else ""
    if (
        move is not None
        and move.quiet
        and acceleration is not None
        and usual_acceleration
        and abs(acceleration) / usual_acceleration >= QUIET_MOVE
    ):
        pace = "turning up" if acceleration > 0 else "turning down"
    if move is None or change is None:
        reading = "Too little history to say how it is moving."
    elif move.quiet:
        turning = f", but {pace}" if pace else ""
        reading = f"Little changed {period} ({change * 100:+.1f} %){turning}."
    else:
        pace_words = {"gathering pace": ", and gathering pace", "losing pace": ", but losing pace"}
        reading = (
            f"{move.direction.capitalize()}: {change * 100:+.1f} % {period}"
            f"{pace_words.get(move.pace, '')}."
        )
    return {
        "change_pct": None if change is None else change * 100,
        "period": period,
        "direction": 0 if move is None or move.quiet or not change else (1 if change > 0 else -1),
        "move_size": move.size if move is not None else "",
        "pace": pace,
        # The two magnitudes a picture needs (owner, 2026-10-17: "visualiser
        # vitesse et accélération"): the change and its acceleration in units
        # of THIS asset's usual one — the same scale `read_move` sizes words on.
        "speed_z": _in_usual_units(change, usual_change),
        "acceleration_z": _in_usual_units(acceleration, usual_acceleration),
        "reading": reading,
    }


def _in_usual_units(value: float | None, usual: float | None) -> float | None:
    return None if value is None or not usual else value / usual


async def asset_moves(
    db: InvestmentDB, tickers: tuple[str, ...], today: date | None = None
) -> list[dict[str, Any]]:
    """How each asset in `tickers` is moving as of `today`, in the order given;
    an asset the database does not price is absent."""
    today = today or date.today()
    days = await _default_lookback_days(db)
    since = today - timedelta(days=round(365.25 * COMPARISON_YEARS) + 2 * days)
    first_compared = pd.Timestamp(today - timedelta(days=round(365.25 * COMPARISON_YEARS)))
    out: list[dict[str, Any]] = []
    for ticker in tickers:
        rows = await db.query(
            "SELECT ts, level FROM market_data WHERE ticker = :t AND ts >= :since "
            "AND ts <= :today AND level IS NOT NULL ORDER BY ts",
            t=ticker,
            since=since.isoformat(),
            today=today.isoformat(),
        )
        if not rows:
            continue
        level = pd.Series(
            [float(r["level"]) for r in rows],
            index=pd.to_datetime([str(r["ts"])[:10] for r in rows]),
        )
        change = level / asof_lag(level, days) - 1
        acceleration = change - asof_lag(change, days)
        compared = change.index >= first_compared
        latest_change, latest_acceleration = change.iloc[-1], acceleration.iloc[-1]
        out.append(
            {
                "ticker": ticker,
                "as_of": level.index[-1].date().isoformat(),
                **describe_asset_move(
                    None if pd.isna(latest_change) else float(latest_change),
                    None if pd.isna(latest_acceleration) else float(latest_acceleration),
                    change[compared].dropna().tolist(),
                    acceleration[compared].dropna().tolist(),
                    f"over {days} days",
                ),
            }
        )
    return out


async def macro_board(db: InvestmentDB, today: date | None = None) -> dict[str, Any]:
    """The board as of `today` — one entry per registered series the database
    holds, in the registry's order — and its one-line summary."""
    today = today or date.today()
    default_days = await _default_lookback_days(db)
    since = today - timedelta(days=round(365.25 * COMPARISON_YEARS))
    board: list[dict[str, Any]] = []
    for line in MACRO_BOARD:
        rows = await db.query(
            "SELECT ts, level, speed, acceleration FROM market_data "
            "WHERE ticker = :t AND ts >= :since AND ts <= :today AND level IS NOT NULL "
            "ORDER BY ts",
            t=line.ticker,
            since=since.isoformat(),
            today=today.isoformat(),
        )
        if not rows:
            continue
        latest = rows[-1]
        as_of = date.fromisoformat(str(latest["ts"])[:10])
        first_day = date.fromisoformat(str(rows[0]["ts"])[:10])
        level = float(latest["level"])
        speeds = [float(r["speed"]) for r in rows if r["speed"] is not None]
        accelerations = [float(r["acceleration"]) for r in rows if r["acceleration"] is not None]
        levels = [float(r["level"]) for r in rows]
        period = change_period(line.ticker, default_days)
        words = describe(
            line,
            level,
            latest["speed"],
            latest["acceleration"],
            levels,
            speeds,
            accelerations,
            period,
            as_of,
            today,
            (as_of - first_day).days / 365.25,
        )
        rank = rank_in(level, levels)
        usual = statistics.pstdev(speeds) if len(speeds) > 1 else 0.0
        move_size = (
            abs(float(latest["speed"])) / usual if latest["speed"] is not None and usual else 0.0
        )
        board.append(
            {
                **dataclasses.asdict(line),
                "level": level,
                "level_text": _level(level, line),
                "speed": latest["speed"],
                "acceleration": latest["acceleration"],
                "change_period": period,
                "as_of": as_of.isoformat(),
                **words,
                # How much a series stands out: distance from the middle of its
                # ten years, plus how large its move is for it.
                "salience": abs((rank if rank is not None else 0.5) - 0.5) * 2
                + min(move_size, 3) / 3,
            }
        )
    return {"board": board, "summary": summarise(board)}
