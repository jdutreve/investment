"""The macro board says where a series stands and what it is doing, in words
that can be checked against the figures beside them."""

from datetime import date
from pathlib import Path

import pytest

from investment.db.sqlite import InvestmentDB
from investment.market import macro_board as mb

YIELD = next(line for line in mb.MACRO_BOARD if line.ticker == "DGS10")
TODAY = date(2026, 10, 11)


def _describe(
    level: float, speed: float, acceleration: float, **overrides: object
) -> dict[str, str]:
    arguments: dict[str, object] = {
        "levels": [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, level],
        "speeds": [0.2, -0.2, 0.2, -0.2],  # a usual move of 0.2
        "accelerations": [0.1, -0.1, 0.1, -0.1],
        "period": "over 30 days",
        "as_of": date(2026, 10, 9),
        "today": TODAY,
        "history_years": 10.0,
    }
    arguments.update(overrides)
    return mb.describe(YIELD, level, speed, acceleration, **arguments)  # type: ignore[arg-type]


def test_a_level_is_placed_in_its_own_ten_years() -> None:
    assert mb.position_words(1.00, True)[0] == "the highest in ten years"
    assert mb.position_words(0.96, False)[0] == "near its ten-year high"
    assert mb.position_words(0.85, False)[0] == "high for the last ten years"
    assert mb.position_words(0.50, False) == ("within its usual range", "")
    assert mb.position_words(0.15, False)[0] == "low for the last ten years"
    assert mb.position_words(0.02, True)[0] == "the lowest in ten years"
    assert mb.position_words(None, False)[0] == "too little history to compare"


def test_a_move_is_sized_against_what_is_usual_for_the_series() -> None:
    """Four hundredths of a point and forty are not the same word: thirteen of
    eighteen series read "building" when only the signs were looked at."""
    assert mb.read_move(0.04, 0.03, 0.2, 0.1) == mb.Move(quiet=True)
    assert mb.read_move(0.15, 0.0, 0.2, 0.1).direction == "rising slightly"  # type: ignore[union-attr]
    assert mb.read_move(-0.25, 0.0, 0.2, 0.1).direction == "falling"  # type: ignore[union-attr]
    sharp = mb.read_move(0.42, 0.27, 0.2, 0.1)
    assert sharp == mb.Move(False, "rising sharply", "gathering pace")
    assert sharp.short == "rising sharply"
    assert mb.read_move(0.15, 0.2, 0.2, 0.1).short == ""  # a slight move is not a headline
    assert mb.read_move(-0.42, 0.27, 0.2, 0.1).pace == "losing pace"  # type: ignore[union-attr]
    assert mb.read_move(None, 0.1, 0.2, 0.1) is None


def test_the_sentence_says_position_then_move_and_can_be_checked() -> None:
    words = _describe(5.22, 0.42, 0.27)
    assert words["reading"] == (
        "The highest in ten years (ten-year median 3.75 %). "
        "Rising sharply: +0.42 pt over 30 days, and gathering pace."
    )
    assert words["headline"] == "the 10-year yield at a ten-year high and rising sharply"


def test_nothing_notable_gives_no_headline_and_a_plain_sentence() -> None:
    words = _describe(3.6, 0.01, 0.0)
    assert (
        words["reading"]
        == "Within its usual range (ten-year median 3.55 %). Little changed over 30 days."
    )
    assert words["headline"] == ""


def test_an_old_print_and_a_short_history_are_said() -> None:
    old = _describe(3.6, 0.01, 0.0, as_of=date(2026, 8, 1))
    assert old["reading"].endswith("Last print 2026-08-01.")
    young = _describe(3.6, 0.01, 0.0, history_years=1.5)
    assert young["reading"].startswith("Too little history to compare.")


def test_the_change_is_labelled_with_the_period_it_was_measured_over() -> None:
    assert mb.change_period("CPIAUCSL", 30) == "since the previous print"
    assert mb.change_period("GLOBAL_LIQUIDITY", 30) == "over 7 days"
    assert mb.change_period("DGS10", 30) == "over 30 days"


def test_the_summary_names_what_stands_out_most_and_says_so_when_nothing_does() -> None:
    board = [
        {"headline": "credit spreads low", "salience": 0.7},
        {"headline": "", "salience": 0.9},
        {"headline": "the 10-year yield at a ten-year high", "salience": 1.6},
    ]
    assert mb.summarise(board) == "The 10-year yield at a ten-year high; credit spreads low."
    assert mb.summarise([{"headline": "", "salience": 0.1}]).startswith("Nothing stands out")


def test_the_board_names_each_series_once() -> None:
    tickers = [line.ticker for line in mb.MACRO_BOARD]
    assert len(tickers) == len(set(tickers))
    assert all(line.label and line.short and line.group for line in mb.MACRO_BOARD)


async def test_the_board_reads_the_latest_print_and_nothing_after_today(tmp_path: Path) -> None:
    """A series the database holds is on the board with its latest level and a
    sentence; one it does not hold is absent rather than shown empty."""
    db = InvestmentDB(tmp_path / "board.db")
    try:
        rows = [("2026-08-28", 5.00, 0.20, 0.10), ("2026-10-09", 5.22, 0.42, 0.27)]
        rows.append(("2026-10-20", 9.99, 9.99, 9.99))  # after today: not knowable yet
        for ts, level, speed, acceleration in rows:
            await db.command(
                "INSERT INTO market_data (ticker, asset_class, currency, ts, level, speed, "
                "acceleration) VALUES ('DGS10', 'MACRO', 'USD', :ts, :l, :s, :a)",
                ts=ts,
                l=level,
                s=speed,
                a=acceleration,
            )
        payload = await mb.macro_board(db, TODAY)
    finally:
        await db.close()
    (line,) = payload["board"]
    assert (line["ticker"], line["level"], line["as_of"]) == ("DGS10", 5.22, "2026-10-09")
    assert line["level_text"] == "5.22 %"
    assert line["reading"].startswith("Too little history to compare.")  # six weeks of it
    assert "+0.42 pt over 30 days" in line["reading"]
    assert isinstance(payload["summary"], str) and payload["summary"]


def test_the_colour_reads_the_level_from_its_favourable_side() -> None:
    """The same rank is good news for one series and bad for another."""
    assert mb.news(0.97, "high") == "good"
    assert mb.news(0.97, "low") == "bad"
    assert mb.news(0.85, "low") == mb.news(0.15, "high") == "poor"
    assert mb.news(0.70, "low") == mb.news(0.30, "high") == "watch"
    # A hair on the wrong side of the median is not news.
    assert mb.news(0.45, "high") == mb.news(0.55, "low") == "good"
    assert mb.news(0.97, None) == "neutral"
    assert mb.news(None, "high") == "unknown"


def test_every_series_but_gold_declares_its_favourable_side() -> None:
    undeclared = [line.ticker for line in mb.MACRO_BOARD if line.favourable is None]
    assert undeclared == ["gold_10y_dev"]


def test_a_move_carries_its_size() -> None:
    sizes = []
    for speed in (0.01, 0.5, -1.5, 2.5):
        move = mb.read_move(speed, None, 1.0, None)
        assert move is not None
        sizes.append(move.size)
    assert sizes == ["", "slight", "full", "sharp"]


def _entry(group: str, news: str, short: str, headline: str = "") -> dict[str, object]:
    return {
        "ticker": short,
        "group": group,
        "news": news,
        "short": short,
        "headline": headline,
        "level_text": "1",
        "rank": None,
        "direction": 0,
    }


def test_a_theme_reads_as_its_worst_series_and_names_it() -> None:
    """A summary, not the list: the verdict, then at most two facts — the series
    that set the colour first, then what stands out — with level, standing and
    move; and nothing when nothing does."""
    yield_10y = {
        **_entry("Rates", "bad", "the 10-year yield"),
        "ticker": "DGS10",
        "favourable": "low",
        "level_text": "5.10 %",
        "unusual": True,
        "position": "near its ten-year high",
        "direction": 1,
        "move_size": "slight",
    }
    curve = {**_entry("Rates", "good", "the yield curve"), "level_text": "0.35 pts"}
    real = {
        **_entry("Rates", "good", "the real 10-year yield"),
        "ticker": "real_yield_10y",
        "level_text": "1.10 %",
        "direction": -1,
        "move_size": "full",
        "pace": "gathering pace",
        "salience": 0.9,
    }
    spreads = {
        **_entry("Credit", "good", "credit spreads"),
        "ticker": "BAA10Y",
        "level_text": "1.44 pts",
        "unusual": True,
        "position": "near its ten-year low",
    }
    board = [
        curve,
        yield_10y,
        real,
        spreads,
        {**_entry("Credit", "good", "bank credit"), "level_text": "6.21 %"},
        _entry("Markets", "neutral", "gold's trend"),
        _entry("Money", "good", "money growth"),
    ]
    themes = mb.read_themes(board)
    assert [(t["group"], t["news"]) for t in themes] == [
        ("Rates", "bad"),
        ("Credit", "good"),
        ("Money", "good"),
    ]
    assert themes[0]["sentence"] == (
        "Near its worst in ten years: the 10-year yield (DGS10) at 5.10 %, near a ten-year "
        "high, edging up; the real 10-year yield (real_yield_10y) at 1.10 %, falling and "
        "accelerating."
    )
    assert themes[1]["sentence"] == (
        "Favourable: credit spreads (BAA10Y) at 1.44 pts, near a ten-year low."
    )
    assert themes[2]["sentence"] == "Favourable; nothing stands out."


def _series(ticker: str, **fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "ticker": ticker,
        "short": f"the {ticker}",
        "news": "good",
        "rank": 0.5,
        "position": "within its usual range",
        "level_text": "1.00 %",
        "direction": 0,
        "move_size": "",
        "favourable": "low",
        "speed": 0.0,
        "decimals": 2,
        "unit": "%",
        "change_period": "over 30 days",
        "group": "Rates",
    }
    return {**base, **fields}


def test_highlights_say_what_changed_and_leave_the_rest_to_the_themes() -> None:
    """The extremes and the full moves are in the theme sentences; the
    highlights carry only what none of them says."""
    board = [
        _series("yield", rank=1.0, news="bad", position="near its ten-year high", direction=1),
        _series("spread", rank=0.02, position="near its ten-year low"),
        _series("bills", move_size="full", direction=1, speed=0.25),
        _series("liquidity", news="poor", favourable="high"),
        _series("quiet"),
    ]
    earlier = [_series("liquidity", news="good"), _series("quiet")]
    lines = mb.highlights(board, earlier)
    assert [(line["kind"], line["tone"]) for line in lines] == [
        ("Changed in a month", "poor"),
        ("Direction of travel", "poor"),
    ]
    assert lines[0]["text"] == ("Liquidity (liquidity): favourable a month ago, unfavourable now.")
    assert lines[1]["text"] == "2 series worsening, 0 improving, 3 steady."


def test_a_theme_in_trouble_names_the_probable_cause() -> None:
    board = [
        {**_series("DGS10", short="the 10-year yield", news="bad", rank=0.97), "salience": 1},
        _series("CPIAUCSL", short="inflation", direction=1, group="Inflation"),
    ]
    (rates, _) = mb.read_themes(board)
    assert rates["sentence"].endswith("Probable cause: inflation (CPIAUCSL) rising.")


def test_a_board_where_nothing_stands_out_has_no_highlight() -> None:
    assert mb.highlights([_series("quiet")], [_series("quiet")]) == []


def test_a_cause_names_only_the_drivers_that_point_the_same_way() -> None:
    board = [
        _series("DGS10", rank=1.0, direction=1),
        _series("real_yield_10y", short="the real 10-year yield", rank=0.9),  # stands high
        _series("CPIAUCSL", short="inflation", direction=1),  # moves the right way
        _series("^IRX", short="short rates", direction=-1),  # moves the wrong way
    ]
    assert mb.probable_cause(board[0], 1, board) == (
        "Probable cause: the real 10-year yield (real_yield_10y) high and inflation (CPIAUCSL) "
        "rising."
    )
    # Nothing on the board would produce a FALL in the 10-year yield but the bill.
    assert mb.probable_cause(board[0], -1, board) == "Probable cause: short rates (^IRX) falling."
    alone = [_series("UNRATE")]
    assert mb.probable_cause(alone[0], 1, alone) == "No cause visible on this board."


def test_every_driver_is_a_series_of_the_board() -> None:
    tickers = {line.ticker for line in mb.MACRO_BOARD}
    assert set(mb.DRIVERS) == tickers
    assert {driver for drivers in mb.DRIVERS.values() for driver, _ in drivers} <= tickers


def test_an_asset_move_is_said_in_percent_with_its_size_and_pace() -> None:
    """An asset reads as a move only: which way, how large for that asset, and
    whether it is gathering or losing pace."""
    usual = [0.02, -0.02, 0.02, -0.02]  # a usual move of 2%
    usual_acceleration = [0.01, -0.01, 0.01, -0.01]
    falling = mb.describe_asset_move(-0.045, -0.02, usual, usual_acceleration, "over 30 days")
    assert falling["reading"] == "Falling sharply: -4.5 % over 30 days, and gathering pace."
    assert (falling["direction"], falling["move_size"]) == (-1, "sharp")
    rising = mb.describe_asset_move(0.025, -0.005, usual, usual_acceleration, "over 30 days")
    assert rising["reading"] == "Rising: +2.5 % over 30 days, but losing pace."
    quiet = mb.describe_asset_move(0.003, 0.0, usual, usual_acceleration, "over 30 days")
    assert quiet["reading"] == "Little changed over 30 days (+0.3 %)."
    assert quiet["direction"] == 0
    unknown = mb.describe_asset_move(None, None, [], [], "over 30 days")
    assert unknown["reading"] == "Too little history to say how it is moving."


async def test_an_asset_move_is_read_in_percent_not_in_dollars(tmp_path: Path) -> None:
    """The same path at ten times the price reads the same: a dollar speed would
    size every recent move of an asset that has risen against smaller old ones.
    An asset the database does not price is absent."""
    db = InvestmentDB(tmp_path / "assets.db")
    try:
        days = [date(2024, 1, 1).toordinal() + 7 * n for n in range(120)]
        for ticker, scale in (("SPY", 1.0), ("GLD", 10.0)):
            for n, day in enumerate(days):
                level = scale * (100 + 3 * ((n % 6) - 3) + n * 0.1)
                await db.command(
                    "INSERT INTO market_data (ticker, asset_class, currency, ts, level) "
                    "VALUES (:t, 'X', 'USD', :ts, :l)",
                    t=ticker,
                    ts=date.fromordinal(day).isoformat(),
                    l=level,
                )
        moves = await mb.asset_moves(db, ("SPY", "IWN", "GLD"), date.fromordinal(days[-1]))
    finally:
        await db.close()
    assert [m["ticker"] for m in moves] == ["SPY", "GLD"]
    spy, gld = moves
    assert spy["as_of"] == date.fromordinal(days[-1]).isoformat()
    assert spy["reading"] == gld["reading"]
    assert spy["change_pct"] == pytest.approx(gld["change_pct"])
