"""World events (src/investment/watch/world_events.py). Real throwaway SQLite,
a `TestModel`-driven day triage, and no network: the day's wikitext comes from
the `fetch` argument, which is the job's transport."""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import aiohttp
import pytest
from pydantic_ai import Agent, UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

from investment.corpus.embedding import InProcessEmbedder
from investment.corpus.ingester import CorpusIngester
from investment.db.sqlite import InvestmentDB
from investment.planner.baseline import _recent_events
from investment.watch import world_events as WE

TODAY = date(2026, 11, 8)

# The shape of the portal's page for 2026-11-03, trimmed.
NOV_3 = """{{Current events|year=2026|month=11|day=3|top=yes}}
<!-- All news items below this line -->
'''Armed conflicts and attacks'''
*[[2026 Iran war]]
**[[2026 Iran war fuel crisis]]
***The [[Reserve Bank of Australia|Reserve Bank]] of [[Australia]] raises its benchmark \
[[interest rate]] by 0.25 percentage points to 4.85%. [https://example.org/rba (Reuters)]

'''Politics and elections'''
*[[2026 United States elections]]
**[[News media in the United States|U.S. news]] organizations project that the \
[[Democratic Party (United States)|Democratic Party]] will control the House and the \
Senate. [https://example.org/ap (''AP'')] [https://example.org/nyt (''The New York Times'')]

'''Sports'''
*A {{convert|200|kg|lb|adj=on}} trophy is unveiled. [https://example.org/sport (BBC)]
<!-- All news items above this line -->
"""


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "world.db")
    yield conn
    await conn.close()


@pytest.fixture
def ingester(db: InvestmentDB, embedder: InProcessEmbedder) -> CorpusIngester:
    return CorpusIngester(db, embedder)


def _agent(picks: list[dict[str, object]]) -> Agent[int, WE.WorldDayTriage]:
    """The day triage without a transport. Plain `output_type` for the reason
    `test_event_watch._agent` gives; the index validator is the production one."""
    agent: Agent[int, WE.WorldDayTriage] = Agent(
        TestModel(custom_output_args={"picks": picks}),
        deps_type=int,
        output_type=WE.WorldDayTriage,
    )
    agent.output_validator(WE.picks_point_at_items)
    return agent


async def _page(session: aiohttp.ClientSession, day: date) -> str:
    return NOV_3


ELECTION = {
    "index": 1,
    "title": "Democrats win control of both chambers of the US Congress",
    "summary": "US news organizations project Democratic control of the House and Senate.",
    "why_it_matters": "Divided government narrows the fiscal path: pressure on deficits eases.",
    "entities": ["United States", "Democratic Party"],
}


def test_a_day_parses_into_items_under_their_story() -> None:
    items = WE.parse_day(date(2026, 11, 3), NOV_3)

    assert [item.category for item in items] == [
        "Armed conflicts and attacks",
        "Politics and elections",
        "Sports",
    ]
    rba, election, sport = items
    assert rba.topics == ("2026 Iran war", "2026 Iran war fuel crisis")
    assert rba.text.startswith("The Reserve Bank of Australia raises")
    assert rba.sources == (("Reuters", "https://example.org/rba"),)
    assert "Democratic Party will control" in election.text
    assert [name for name, _ in election.sources] == ["AP", "The New York Times"]
    assert sport.text == "A 200 kg trophy is unveiled."  # the template rendered, not dropped


def test_the_page_title_is_the_portals_spelling() -> None:
    assert WE.page_title(date(2026, 11, 3)) == "2026_November_3"


async def test_the_days_to_read_are_the_window_minus_what_was_read(db: InvestmentDB) -> None:
    await WE.journal_day(db, date(2026, 11, 7), [])

    days = await WE.days_to_read(db, TODAY)

    assert days[0] == date(2026, 10, 4)  # oldest first, 35 days back
    assert days[-1] == date(2026, 11, 6)  # yesterday was read; today never is
    assert len(days) == WE.EVENT_LOOKBACK_DAYS - 1


async def test_a_pick_becomes_an_event_the_worker_reads_as_a_world_event(
    db: InvestmentDB, ingester: CorpusIngester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(WE, "EVENT_LOOKBACK_DAYS", 1)

    report = await WE.run_world_events(db, ingester, _agent([ELECTION]), TODAY, fetch=_page)

    assert report.days_read == 1 and report.picked == 1 and not report.failed
    events = await _recent_events(db, TODAY)
    assert [(e["stream"], e["title"]) for e in events] == [
        ("world", "Democrats win control of both chambers of the US Congress")
    ]
    assert events[0]["dated"] == "2026-11-07"
    # The day is read once: a second run finds nothing to read.
    again = await WE.run_world_events(db, ingester, _agent([ELECTION]), TODAY, fetch=_page)
    assert again.days_read == 0


async def test_a_day_whose_page_fails_is_read_again_next_run(
    db: InvestmentDB, ingester: CorpusIngester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(WE, "EVENT_LOOKBACK_DAYS", 1)

    async def missing(session: aiohttp.ClientSession, day: date) -> str:
        raise LookupError("missingtitle")

    report = await WE.run_world_events(db, ingester, _agent([]), TODAY, fetch=missing)

    assert report.failed == {"2026-11-07": "LookupError: missingtitle"}
    assert await WE.days_to_read(db, TODAY) == [date(2026, 11, 7)]


async def test_a_pick_that_names_no_item_of_the_day_is_refused() -> None:
    with pytest.raises(UnexpectedModelBehavior):
        await _agent([{**ELECTION, "index": 9}]).run("day", deps=3)


async def test_a_rate_limit_stops_the_run_and_leaves_the_days_for_the_next(
    db: InvestmentDB, ingester: CorpusIngester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-08: after one refusal every further request was refused too,
    34 of them in seven seconds. The run stops instead."""
    monkeypatch.setattr(WE, "EVENT_LOOKBACK_DAYS", 3)
    monkeypatch.setattr(WE, "REQUEST_SPACING_SECONDS", 0.0)
    asked: list[date] = []

    async def refused(session: aiohttp.ClientSession, day: date) -> str:
        asked.append(day)
        raise WE.RateLimitedError("refused twice (429)")

    report = await WE.run_world_events(db, ingester, _agent([]), TODAY, fetch=refused)

    assert asked == [date(2026, 11, 5)]  # the oldest day, then nothing more
    assert report.days_read == 0
    assert len(await WE.days_to_read(db, TODAY)) == 3
