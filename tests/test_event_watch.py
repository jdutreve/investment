"""UC3 Event Watch (`watch/event_watch.py`, docs/USE_CASES.md UC3).

Real throwaway SQLite, a `TestModel`-driven triage agent, no network. The three
properties worth pinning are the ones that decide whether this job is safe to
run unattended every week: routine items leave NO trace, an item the model
cannot describe is NOT written, and nothing is ever triaged twice.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from investment.corpus.embedding import InProcessEmbedder
from investment.corpus.ingester import CorpusIngester
from investment.db.sqlite import InvestmentDB
from investment.watch import event_watch as EW
from investment.watch.event_watch import EventTriage, FeedItem

FED = FeedItem(
    source="Federal Reserve",
    title="Federal Reserve issues FOMC statement",
    url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
    published="Wed, 29 Jul 2026 18:00:00 GMT",
    text="The Committee decided to lower the target range.",
)
ORDER = FeedItem(
    source="Federal Reserve",
    title="Board announces approval of the application by Coastal Bank",
    url="https://www.federalreserve.gov/newsevents/pressreleases/orders20260804c.htm",
    published="Tue, 4 Aug 2026 20:30:00 GMT",
    text="",
)
SNB = FeedItem(
    source="Swiss National Bank",
    title="2026-08-12 - Banknotes and coins - Access to cash",
    url="https://www.snb.ch/en/the-snb/mandates-goals/cash/access-to-cash",
    published=None,
    text="",
)


def _agent(result: EventTriage) -> Agent[None, EventTriage]:
    """A triage agent that answers `result` without a transport.

    PLAIN `output_type`, not the production `NativeOutput` wrapper: TestModel
    has no native-structured-output path and refuses it outright. What is under
    test here is the job around the model — routine discarded, flagged not
    written, deduped — and the wrapper is a property of the transport, pinned
    where it belongs (`openrouter_client.build_native_output_model`)."""
    return Agent(TestModel(custom_output_args=result), output_type=EventTriage)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "events.db")
    yield conn
    await conn.close()


@pytest.fixture
def ingester(db: InvestmentDB, embedder: InProcessEmbedder) -> CorpusIngester:
    return CorpusIngester(db, embedder)


# -- pure shaping -----------------------------------------------------------


def test_the_sources_are_pinned_with_their_domains() -> None:
    """Changing the watched set means editing this constant (Task 3.2). The
    domains are pinned beside the URLs so a redirect off-domain is visible."""
    for source in EW.EVENT_SOURCES:
        assert source["domain"] in source["url"]
        assert source["url"].startswith("https://")


def test_an_item_with_no_text_still_reaches_the_model_as_such() -> None:
    """The ECB and SNB feeds carry a title and nothing else (measured
    2026-08-12). The prompt's honesty rule is written for exactly that case, so
    the emptiness must be VISIBLE rather than papered over."""
    rendered = EW.render_item(SNB)
    assert "title only" in rendered
    assert "the feed carried no date" in rendered
    assert SNB.url in rendered


def test_the_filename_carries_the_headline_because_the_title_is_read_from_it() -> None:
    """`ingester.title_from` reads the Document's title off the FILENAME and
    `document_id_for` hashes that title into the id. A hash-only name would put
    "event 3f2a91" in the corpus as the title of an FOMC statement."""
    name = EW.event_filename(FED)
    assert name.startswith("Federal Reserve issues FOMC statement")
    assert name.endswith(".md")
    assert EW.event_filename(FED) == EW.event_filename(FED)  # stable
    assert EW.event_filename(FED) != EW.event_filename(ORDER)


def test_a_title_of_pure_punctuation_still_produces_a_name() -> None:
    """Not worth a crash. The URL hash is always there to fall back on."""
    odd = FeedItem(source="s", title="/// ??? ///", url="https://x.test/a", published=None, text="")
    assert EW.event_filename(odd).startswith("event ")


def test_the_slug_drops_the_characters_a_path_would_choke_on() -> None:
    item = FeedItem(
        source="s",
        title='ECB/SNB: "joint" action — 50% swap line',
        url="https://x.test/b",
        published=None,
        text="",
    )
    name = EW.event_filename(item)
    assert "/" not in name[:-3] and '"' not in name and ":" not in name


def test_the_document_keeps_the_sourced_text_apart_from_the_recalled_context() -> None:
    triage = EventTriage(
        verdict="major",
        summary="The Committee lowered the target range by 25bp.",
        entities=["FOMC", "federal funds rate"],
        enrichment="Follows three holds; the previous cut was in 2025.",
    )
    document = EW.event_document(FED, triage)
    assert "## Summary" in document and "## Context" in document
    assert "## As published" in document and FED.text in document
    assert "FOMC, federal funds rate" in document


# -- dedupe -----------------------------------------------------------------


async def test_an_already_ingested_url_is_not_seen_again(db: InvestmentDB) -> None:
    """The whole idempotency story, and why there is no state table: the corpus
    already knows what it ingested (UC3 step 1)."""
    await db.command(
        "INSERT INTO document (id, title, kind, source_type, source_path, ingested_at, trace) "
        "VALUES ('doc-1', 't', 'event', 'url', :url, '2026-08-01', 't')",
        url=FED.url,
    )
    assert await EW.unseen(db, [FED, ORDER]) == [ORDER]


async def test_the_same_url_twice_in_one_run_is_triaged_once(db: InvestmentDB) -> None:
    """One release can appear in two feeds. Paying a model call twice for one
    URL is the cheapest kind of waste to avoid."""
    assert await EW.unseen(db, [FED, FED]) == [FED]


async def test_nothing_fetched_means_nothing_queried(db: InvestmentDB) -> None:
    assert await EW.unseen(db, []) == []


# -- the run ----------------------------------------------------------------


async def test_a_routine_item_leaves_no_document_only_a_verdict(
    db: InvestmentDB, ingester: CorpusIngester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discarded means discarded: no document, no file, no embedding. These
    feeds are mostly bank-merger approvals — keeping them would bury the one
    item a year that matters.

    What it DOES leave is the one-line judgement row, which is what stops the
    same administrative order being re-triaged next Sunday (`unseen`)."""
    monkeypatch.setattr(EW, "EVENT_SOURCES", ())
    inbox = tmp_path / "inbox"
    agent = _agent(EventTriage(verdict="routine", reasoning="an administrative order"))

    async def one_item(session: object, source: object) -> list[FeedItem]:
        return [ORDER]

    monkeypatch.setattr(EW, "fetch_feed", one_item)
    monkeypatch.setattr(EW, "EVENT_SOURCES", ({"name": "Fed", "url": "u", "domain": "d"},))

    report = await EW.run_event_watch(db, ingester, agent)

    assert report.routine == 1 and report.major == 0
    assert report.ingested == [] and report.flagged == []
    assert await db.query("SELECT id FROM document") == []
    assert not list(inbox.glob("*.md"))  # and nothing reached the watcher's queue
    judged = await db.query(
        "SELECT source_id, payload FROM event_log WHERE type = :t", t=EW.TRIAGE_EVENT
    )
    assert [str(r["source_id"]) for r in judged] == [ORDER.url]
    assert "routine" in str(judged[0]["payload"])


async def test_an_item_the_model_cannot_describe_is_flagged_not_written(
    db: InvestmentDB, ingester: CorpusIngester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE PROPERTY THAT PROTECTS THE CORPUS. A fabricated enrichment would be
    embedded, retrieved months later and read as sourced — so when the model
    says it cannot describe the item, nothing is written and the owner is
    asked (UC3: "flagged and pushed to Telegram instead of being
    hallucinated")."""
    agent = _agent(
        EventTriage(
            verdict="major",
            needs_user_input=True,
            reasoning="the feed carried a headline and no text",
        )
    )

    async def one_item(session: object, source: object) -> list[FeedItem]:
        return [SNB]

    monkeypatch.setattr(EW, "fetch_feed", one_item)
    monkeypatch.setattr(EW, "EVENT_SOURCES", ({"name": "SNB", "url": "u", "domain": "d"},))

    report = await EW.run_event_watch(db, ingester, agent)

    assert report.major == 1 and report.ingested == []
    assert [item.url for item in report.flagged] == [SNB.url]
    assert await db.query("SELECT id FROM document") == []
    assert SNB.url in EW.flagged_message(report.flagged)


async def test_a_major_item_is_ingested_under_its_url(
    db: InvestmentDB, ingester: CorpusIngester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ingested SYNCHRONOUSLY so the 08:10 sweep sees it, and recorded under the
    press-release URL rather than the temp file it was written to — otherwise
    the dedupe never matches and the event is re-triaged every week."""
    inbox = tmp_path / "inbox"
    agent = _agent(
        EventTriage(
            verdict="major",
            summary="The Committee lowered the target range by 25bp.",
            entities=["FOMC"],
            enrichment="Follows three holds.",
        )
    )

    async def one_item(session: object, source: object) -> list[FeedItem]:
        return [FED]

    monkeypatch.setattr(EW, "fetch_feed", one_item)
    monkeypatch.setattr(EW, "EVENT_SOURCES", ({"name": "Fed", "url": "u", "domain": "d"},))

    report = await EW.run_event_watch(db, ingester, agent)

    assert report.major == 1 and len(report.ingested) == 1
    # NOTHING was left in the inbox: the watcher polls it every 60s and would
    # re-ingest the file as a book under the same document_id, overwriting
    # `source_path` with the file path and breaking the dedupe forever.
    assert not list(inbox.glob("*"))
    rows = await db.query("SELECT kind, source_type, source_path, author FROM document")
    assert len(rows) == 1
    assert rows[0]["kind"] == "event"
    assert rows[0]["source_type"] == "url"
    assert rows[0]["source_path"] == FED.url  # the dedupe key, not the temp file
    assert rows[0]["author"] == "Federal Reserve"
    # ...and it dedupes on the next run, which is what makes the job idempotent.
    assert await EW.unseen(db, [FED]) == []


async def test_one_unreachable_source_does_not_cost_the_others(
    db: InvestmentDB, ingester: CorpusIngester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One central bank's outage must not cost the week's watch — and must not
    fail silently either, hence the named entry in `failed`."""
    agent = _agent(EventTriage(verdict="routine"))

    async def flaky(session: object, source: dict[str, str]) -> list[FeedItem]:
        if source["name"] == "dead":
            raise RuntimeError("connection reset")
        return [ORDER]

    monkeypatch.setattr(EW, "fetch_feed", flaky)
    monkeypatch.setattr(
        EW,
        "EVENT_SOURCES",
        ({"name": "dead", "url": "u", "domain": "d"}, {"name": "alive", "url": "u", "domain": "d"}),
    )

    report = await EW.run_event_watch(db, ingester, agent)

    assert "dead" in report.failed and "connection reset" in report.failed["dead"]
    assert report.fetched == 1 and report.routine == 1


async def test_a_triage_that_stops_answering_is_bounded_and_costs_only_itself(
    db: InvestmentDB, ingester: CorpusIngester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE BOUND THAT WAS MISSING, and the defect is one this project has already
    paid for once. httpx's read timeout re-arms on every chunk, so a response
    that dribbles never trips it (`openrouter_client.py`), and
    `worker/agent.py` carries the same fix after a call burned 497 seconds
    against a dead socket. The first live run made it visible here: one ECB item
    took 3m38s while its siblings took 3-10s, and nothing bounded it.

    With 54 items and no deadline on the chain itself, one stalled socket hangs
    the Monday. So the call has a wall clock, and an item that runs out of it is
    recorded and skipped — the other fifty-three still get their turn."""
    monkeypatch.setattr(EW, "TRIAGE_CALL_BUDGET_SECONDS", 0.05)

    class _Stalled:
        async def run(self, prompt: str) -> object:
            await asyncio.sleep(30)  # never answers within the budget
            raise AssertionError("the budget should have fired")

    async def two_items(session: object, source: object) -> list[FeedItem]:
        return [FED, ORDER]

    monkeypatch.setattr(EW, "fetch_feed", two_items)
    monkeypatch.setattr(EW, "EVENT_SOURCES", ({"name": "Fed", "url": "u", "domain": "d"},))

    report = await EW.run_event_watch(db, ingester, cast(Any, _Stalled()))

    assert set(report.failed) == {FED.url, ORDER.url}
    assert all("TimeoutError" in reason for reason in report.failed.values())
    # Nothing ingested, so next week sees both as new and retries them.
    assert report.ingested == []
    assert await EW.unseen(db, [FED, ORDER]) == [FED, ORDER]


# -- the dedupe: an item is judged ONCE --------------------------------------


async def test_a_routine_item_is_not_re_triaged_the_following_week(
    db: InvestmentDB, ingester: CorpusIngester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE COST THAT WENT UNNOTICED BECAUSE THE CORPUS STAYED CORRECT. The
    dedupe was by URL against `document.source_path`, and a routine item
    creates no Document — so every Sunday the same 15-20 slow-moving feed
    entries came back as new and were paid for again. The feeds turn over in
    weeks, not days."""

    async def one_item(session: object, source: object) -> list[FeedItem]:
        return [ORDER]

    monkeypatch.setattr(EW, "fetch_feed", one_item)
    monkeypatch.setattr(EW, "EVENT_SOURCES", ({"name": "Fed", "url": "u", "domain": "d"},))
    agent = _agent(EventTriage(verdict="routine", reasoning="administrative"))

    first = await EW.run_event_watch(db, ingester, agent)
    assert first.new == 1 and first.routine == 1

    second = await EW.run_event_watch(db, ingester, agent)
    assert second.new == 0  # never reaches the model at all
    assert second.routine == 0


async def test_a_flagged_item_asks_the_owner_only_once(
    db: InvestmentDB, ingester: CorpusIngester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`needs_user_input` is not ingested either, so the same unanswerable
    headline was pushed to Telegram every week, forever."""

    async def one_item(session: object, source: object) -> list[FeedItem]:
        return [SNB]

    monkeypatch.setattr(EW, "fetch_feed", one_item)
    monkeypatch.setattr(EW, "EVENT_SOURCES", ({"name": "SNB", "url": "u", "domain": "d"},))
    agent = _agent(
        EventTriage(verdict="major", needs_user_input=True, reasoning="a headline and no text")
    )

    assert len((await EW.run_event_watch(db, ingester, agent)).flagged) == 1
    assert (await EW.run_event_watch(db, ingester, agent)).flagged == []


async def test_an_ingested_item_needs_no_second_record(
    db: InvestmentDB, ingester: CorpusIngester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A major item's Document already carries the URL, and two records of one
    fact is the drift `unseen` warns about."""

    async def one_item(session: object, source: object) -> list[FeedItem]:
        return [FED]

    monkeypatch.setattr(EW, "fetch_feed", one_item)
    monkeypatch.setattr(EW, "EVENT_SOURCES", ({"name": "Fed", "url": "u", "domain": "d"},))
    agent = _agent(EventTriage(verdict="major", summary="rates held", reasoning="policy"))

    assert len((await EW.run_event_watch(db, ingester, agent)).ingested) == 1
    assert await db.query("SELECT id FROM event_log WHERE type = :t", t=EW.TRIAGE_EVENT) == []
    assert (await EW.run_event_watch(db, ingester, agent)).new == 0
