"""The Worker reads back what it already said and filed
(src/investment/planner/worker_history.py), on a real throwaway SQLite."""

import inspect
from datetime import date
from pathlib import Path

from investment.db.sqlite import InvestmentDB
from investment.planner import baseline as bl
from investment.planner.worker_history import (
    WORKER_READING_EVENT,
    describe_past_readings,
    describe_recent_ideas,
    idea_fate,
)
from investment.worker.tools import MARKET_PERIODS, WorkerTools


async def _journal_reading(db: InvestmentDB, made_on: date, point: str) -> None:
    sections = [{"heading": "My reading (judgement, not measurement)", "points": [point]}]
    await db.append_event(
        type=WORKER_READING_EVENT,
        source_uc="UC8",
        source_id=None,
        payload={
            "market_signal_decision_date": "2026-10-01",
            "market_signal_assessment": point,
            "market_signal_reading": {"bottom_line": f"Bottom {made_on}", "sections": sections},
        },
        event_date=made_on,
    )


async def _file_idea(db: InvestmentDB, *, title: str, kind: str, on: str, created: str) -> None:
    await db.command(
        "INSERT INTO innovation (id, type, title, rationale, spec, theme_id, date, trace, "
        "created_at) VALUES (:id, :type, :title, 'r', '{}', 'thm', :on, 't', :created)",
        id=f"inn-{title}-{on}",
        type=kind,
        title=title,
        on=on,
        created=created,
    )


async def test_the_worker_reads_its_last_two_readings_and_none_from_later(
    tmp_path: Path,
) -> None:
    """Newest first, two of them, and none dated after `today` — a replay reads
    only what had been said by its date."""
    db = InvestmentDB(tmp_path / "readings.db")
    try:
        for day, point in (
            (date(2026, 10, 4), "oldest"),
            (date(2026, 10, 11), "IWN round trip"),
            (date(2026, 10, 18), "IWN round trip again"),
            (date(2026, 10, 25), "not yet said"),
        ):
            await _journal_reading(db, day, point)

        readings = (await bl.gather_baseline(db, date(2026, 10, 18))).past_readings
        assert [r["made_on"] for r in readings] == ["2026-10-18", "2026-10-11"]

        text = "\n".join(describe_past_readings(readings))
        assert "Say a point again only if something changed" in text
        assert "Bottom 2026-10-18" in text
        assert "- IWN round trip again" in text
        assert "not yet said" not in text and "oldest" not in text
    finally:
        await db.close()


async def test_the_worker_reads_its_recent_ideas_once_each_with_their_fate(
    tmp_path: Path,
) -> None:
    """One line per distinct title, inside the lookback, and an archived replay
    idea filed under an old date stays out of a context standing at that date."""
    db = InvestmentDB(tmp_path / "ideas.db")
    try:
        for title, kind, on, created in (
            ("Bear steepeners", "new_invariant", "2026-08-23", "2026-08-23T08:00:00+00:00"),
            ("Bear steepeners", "new_invariant", "2026-10-04", "2026-10-04T08:00:00+00:00"),
            ("Flag stale orders", "process", "2026-10-18", "2026-10-18T08:00:00+00:00"),
            ("Too old", "process", "2026-07-01", "2026-07-01T08:00:00+00:00"),
            ("Filed later", "process", "2026-10-25", "2026-10-25T08:00:00+00:00"),
        ):
            await _file_idea(db, title=title, kind=kind, on=on, created=created)
        await db.command(
            "INSERT INTO invariant (id, title, description, source, status, weight_initial, "
            "floor_weight, trace, created_at, updated_at) VALUES ('inv-b', 'Bear steepeners', "
            "'d', 'agent-discovery', 'reference', 0.5, 0.05, 't', '2026-10-04', '2026-10-04')"
        )

        ideas = (await bl.gather_baseline(db, date(2026, 10, 18))).recent_ideas
        assert [i["title"] for i in ideas] == ["Flag stale orders", "Bear steepeners"]

        text = "\n".join(describe_recent_ideas(ideas))
        assert "Do not file one again" in text
        assert "Bear steepeners: became a REFERENCE NOTE, never measured" in text
        assert "Flag stale orders: recorded for the owner; nothing measures it" in text

        # An archived replay idea: a 2008 decision date, filed in 2026.
        await _file_idea(
            db,
            title="Replay idea",
            kind="process",
            on="2008-09-02",
            created="2026-08-10T08:00:00+00:00",
        )
        assert (await bl.gather_baseline(db, date(2008, 10, 1))).recent_ideas == []
    finally:
        await db.close()


def test_every_fate_is_said_in_words() -> None:
    assert idea_fate({"type": "new_invariant", "invariant_status": None}).startswith(
        "no invariant was created"
    )
    assert idea_fate({"type": "new_invariant", "invariant_status": "proposed"}) == (
        "measured over the history, status proposed"
    )
    assert idea_fate({"type": "strategy_revision", "strategy_status": "proposed"}) == (
        "strategy proposed"
    )
    assert "measured settings" in idea_fate({"type": "strategy_revision"})


def test_market_fetch_names_every_period_it_accepts() -> None:
    """The Worker spent two of its twelve calls on '5d' and '10d' (2026-10-25):
    the tool never said which periods exist. Its description now does, and must
    keep naming every one the tool accepts."""
    doc = inspect.getdoc(WorkerTools.market_fetch) or ""
    assert all(period in doc for period in MARKET_PERIODS)
