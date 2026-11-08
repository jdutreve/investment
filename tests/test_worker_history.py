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
        assert "Flag stale orders: recorded for the owner; no measurement was stated" in text

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
    assert (
        idea_fate(
            {
                "type": "new_invariant",
                "invariant_status": "proposed",
                "confirmations": 7,
                "infirmations": 3,
            }
        )
        == "measured over the history: 7 confirmed, 3 refuted, status proposed"
    )
    demoted = {
        "type": "new_invariant",
        "invariant_status": "reference",
        "invariant_trace": "t [DEMOTED to reference knowledge: unknown signal: 'cpi_yoy']",
    }
    assert idea_fate(demoted).endswith("never measured: unknown signal: 'cpi_yoy'")
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


def test_the_weekly_proposal_is_shown_to_the_owner_but_not_read_back_twice() -> None:
    """Owner 2026-11-08: the Worker's proposal reaches every channel as a
    section of its reading; the Worker reads it back once, in its recent ideas."""
    from investment.planner.worker_history import describe_past_readings
    from investment.worker.result import PROPOSAL_HEADING, ImprovementProposal, proposal_sections

    proposal = ImprovementProposal(
        type="data",
        title="Read the Bank of Japan instead of the SNB",
        rationale="The BoJ moves global long yields; the stack holds no franc asset.",
        spec={},
        trace="t",
        measure="how often a BoJ item reaches the reading in 12 weeks",
    )
    sections = proposal_sections([proposal])
    assert sections == [
        {
            "heading": PROPOSAL_HEADING,
            "points": [
                "[data] Read the Bank of Japan instead of the SNB — The BoJ moves global "
                "long yields; the stack holds no franc asset. Measured by: how often a BoJ "
                "item reaches the reading in 12 weeks"
            ],
        }
    ]
    reading = {"bottom_line": "Nothing is asked.", "sections": sections}
    lines = describe_past_readings(
        [{"made_on": "2026-11-08", "decision_date": "2026-11-02", "reading": reading}]
    )
    assert not any(PROPOSAL_HEADING in line for line in lines)
    assert proposal_sections([]) == []


def test_a_ticker_with_an_alias_is_named_by_its_alias() -> None:
    from investment.db.seed_data import SIGNAL_ALIASES, canonical_signal
    from investment.worker.curator import Predicate

    assert canonical_signal("^VIX") == "vix"
    assert canonical_signal("DCOILWTICO") == "oil"
    assert canonical_signal("credit_spread") == "credit_spread"
    assert canonical_signal("DGS10") == "DGS10"  # no alias: left for the gate to judge
    assert Predicate(signal="BAA10Y", feature="level", op="<", value=2).signal == "credit_spread"
    assert {"vix", "oil"} <= set(SIGNAL_ALIASES)


async def test_an_applied_series_idea_is_measured_by_readings_and_invariants(
    tmp_path: Path,
) -> None:
    """Owner 2026-11-08: the Worker's oil series, applied. Its own plan was to
    count the readings that cite the series against before; the machine adds
    the invariants now written on it, with their record."""
    from investment.planner.worker_history import describe_applied_ideas
    from investment.writeback.recurrence import mark_applied

    db = InvestmentDB(tmp_path / "applied.db")
    try:
        await db.command(
            "INSERT INTO innovation (id, type, title, rationale, spec, theme_id, date, trace, "
            "created_at) VALUES ('inn-oil', 'data', 'Add a crude oil price series', 'r', "
            '\'{"add": ["DCOILWTICO"], "measure": "readings citing oil over 12 weeks"}\', '
            "'thm', '2026-11-08', 't', '2026-11-08T08:00:00+00:00')"
        )
        await _journal_reading(db, date(2026, 11, 1), "no oil series yet")
        await _journal_reading(db, date(2026, 11, 8), "the oil shock, unpriced")
        await mark_applied(db, "inn-oil", "DCOILWTICO", date(2026, 11, 8))
        await _journal_reading(db, date(2026, 11, 15), "DCOILWTICO speed +9 feeds inflation")
        await _journal_reading(db, date(2026, 11, 22), "nothing on energy")
        await db.command(
            "INSERT INTO invariant (id, title, description, source, status, condition, "
            "weight_initial, floor_weight, confirmation_count, infirmation_count, trace, "
            "created_at, updated_at) VALUES ('inv-oil', 'Oil shocks sink bonds', 'd', "
            "'agent-discovery', 'proposed', "
            '\'[{"signal": "oil", "feature": "speed", "op": ">", "value": 10}]\', '
            "0.2, 0.05, 41, 30, 't', '2026-11-15T09:00:00+00:00', '2026-11-15')"
        )

        context = await bl.gather_baseline(db, date(2026, 11, 22))
        [idea] = context.applied_ideas
        assert idea["applied_on"] == "2026-11-08" and idea["became"] == "DCOILWTICO"
        assert idea["readings"] == {"since": 2, "naming_since": 1, "naming_before": 0}
        text = "\n".join(describe_applied_ideas(context.applied_ideas))
        assert "readings citing oil over 12 weeks" in text
        assert "Readings since that name DCOILWTICO: 1 of 2; in as many readings before: 0." in text
        assert "Oil shocks sink bonds — status proposed, 41 confirmed, 30 refuted." in text
        [recent] = context.recent_ideas
        assert idea_fate(recent).startswith("APPLIED by the owner, as DCOILWTICO")
        # A replay standing before the application reads nothing applied.
        assert (await bl.gather_baseline(db, date(2026, 11, 7))).applied_ideas == []
    finally:
        await db.close()
