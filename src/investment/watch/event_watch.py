"""UC3 — Event Watch (docs/USE_CASES.md UC3; docs/TASKS.md Task 3.2).
Sunday 08:05 (the chain's day since 2026-08-12), after the catch-up and before
the curation sweep.

NOT A FEED VACUUM, and the constraint is the design. Broad RSS, YouTube and X
are deferred with their triggers written down (docs/IMPROVEMENTS.md I-9/I-26);
what runs here is a narrow watch over a few PINNED official sources. Changing
the set means editing `EVENT_SOURCES` — a runtime table would be a second place
for the same three URLs to live, and nothing has yet needed to edit them from a
phone.

THE URLS WERE VERIFIED, NOT GUESSED (Task 3.2: "URLs pinned at implementation,
never guessed"). Each was fetched and parsed on 2026-08-12 before being written
down; the two that 404'd are recorded beside the ones that answered, so the next
person does not re-derive them.

FOUR STEPS, and only one of them is a model:

  1. FETCH — feedparser over the pinned URLs.
  2. DEDUPE — by URL, against `document.source_path` for what was ingested and
     against the triage journal for what was judged and discarded. No state
     table: the corpus already knows what it has ingested, and the EventLog
     already records every judgement (`unseen` explains why it takes both).
     This is what makes the job idempotent, which is what lets the cron and the
     heartbeat both reach it.
  3. TRIAGE — the item's text is fetched from its official page
     (`article_text.fetch_article`), then one model call per new item
     (skill-triage-events.md): MAJOR or ROUTINE. Routine is DISCARDED — no
     document, no embedding, nothing but the one-line journal row that stops it
     being re-judged next week. These feeds are mostly bank merger approvals and
     conference invitations; keeping them would bury the one item a year that
     matters.
  4. INGEST — synchronously, through the same `CorpusIngester` the watcher uses,
     so the 08:10 curation sweep sees the event minutes later rather than next
     week.

THE OFFICIAL PAGE IS THE SOURCE TEXT, NOT THE FEED, AND NOTHING IS ASKED OF THE
OWNER (owner, 2026-10-18: "ne me pose plus ces questions … ce tri doit être
automatique et pertinent"). The feeds carry a headline — the ECB and the SNB
nothing else — and the triage used to read only that, flagging what it could
not describe as `needs-user-input` for the owner to answer. Telegram was off,
so the question reached no one: 11 of the first 23 items judged major never
entered the corpus, the Fed's and the ECB's September rate hikes among them.
The text was one request away on the same domain, which is UC3's "bounded
fetch". An item whose page cannot be fetched is skipped un-journalled and
retried the next week, like a failed triage.

WHY THE SKILL FILE LIVES HERE and not in `worker/skills/`: that directory is the
WORKER's prompt, and `worker/agent.py::load_skills` concatenates every `.md` it
finds there — a file dropped in it would silently join the Worker's system
prompt with nothing but a log line to say so.
"""

import asyncio
import dataclasses
import hashlib
import logging
import re
import tempfile
from pathlib import Path
from typing import Literal

import aiohttp
import feedparser
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models.openai import OpenAIChatModelSettings

from investment.corpus.ingester import CorpusIngester
from investment.db.sqlite import InvestmentDB
from investment.openrouter_client import build_native_output_model
from investment.redact import redact_exception
from investment.watch.article_text import fetch_article

logger = logging.getLogger(__name__)

# The watched sources. Verified live on 2026-08-12 — each URL was fetched and
# parsed, and what it actually carries is recorded here because the triage
# prompt's honesty rule depends on it:
#
#   federalreserve.gov/feeds/press_all.xml   20 entries, ~90-char summaries.
#     ALL releases, monetary policy included: 4 of the 20 were /monetary/ links,
#     among them "Federal Reserve issues FOMC statement" (2026-07-29). So the
#     separate press_monetary.xml feed is a strict subset and is not watched —
#     it would arrive, dedupe against the same URL, and cost a triage call.
#   ecb.europa.eu/rss/press.html             15 entries, NO summary — title only.
#   snb.ch/public/en/rss/news                20 entries, NO summary, NO published
#     date. Its titles carry their own date ("2026-08-12 - Banknotes and coins
#     - ..."), which is why dedupe is by URL and never by date.
#
# Two candidates 404'd and are recorded so nobody re-derives them:
# ecb.europa.eu/press/rss/press.html and snb.ch/en/rss/news.
EVENT_SOURCES: tuple[dict[str, str], ...] = (
    {
        "name": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "domain": "federalreserve.gov",
    },
    {
        "name": "European Central Bank",
        "url": "https://www.ecb.europa.eu/rss/press.html",
        "domain": "ecb.europa.eu",
    },
    {
        "name": "Swiss National Bank",
        "url": "https://www.snb.ch/public/en/rss/news",
        "domain": "snb.ch",
    },
)

# How many items per source reach the triage. The feeds carry 15-20 and the job
# runs weekly, so this is a CEILING against a feed that suddenly republishes its
# archive — not a window: anything genuinely new is at the top, and everything
# already ingested dedupes away before a model is called.
MAX_ITEMS_PER_SOURCE = 20

# A press item is a paragraph, not a book: the model needs one short answer and
# no tools.
TRIAGE_TIMEOUT_SECONDS = 120.0
TRIAGE_RETRIES = 2
FEED_TIMEOUT_SECONDS = 30.0

# A WALL CLOCK ON ONE TRIAGE, which is the only bound that sees a stalling
# stream — and the reason it exists is written in `openrouter_client.py`:
# httpx's read timeout re-arms on every chunk, so a response that dribbles
# never trips it. `worker/agent.py` already carries this exact fix
# (`WORKER_CALL_BUDGET_SECONDS`) after a call once burned 497 seconds against a
# dead socket.
#
# Measured on the first live run (2026-08-12): one ECB item took 3m38s while
# its siblings took 3-10s, and neither the model setting nor the read timeout
# noticed — the call completed, so it was slow rather than hung, but nothing
# BOUNDED it. With 54 items a stalled socket hangs the whole weekly chain, and
# the chain has no deadline of its own.
#
# Generous against the 3m38s that was real work: this refuses a call that has
# stopped being work, not one that is thinking.
TRIAGE_CALL_BUDGET_SECONDS = 300.0

# A WALL CLOCK ON THE WHOLE SLOT, because bounding each call leaves the job
# unbounded: 54 items each allowed 300 seconds is four and a half hours, and the
# 08:05 step blocks the nine steps behind it — the ranking, the allocation
# decision and the digest all wait on it. A per-part limit is not a limit on the
# unit.
#
# Twenty minutes against a first live run that took under two: this stops a
# morning of stalled sockets, not a busy week. What it costs when it trips is
# stated in `run_event_watch` — the un-triaged items are simply still new next
# week, which is the same recovery an unreachable feed gets.
EVENT_WATCH_BUDGET_SECONDS = 1200.0

# One row per item the triage judged and DISCARDED — the verdicts that create no
# Document and would otherwise be re-triaged every week (`unseen`).
TRIAGE_EVENT = "EventTriageEvent"

# How much of the headline reaches the filename. Long enough to be the
# Document's title in the corpus, short enough to stay under every filesystem's
# name limit with the hash suffix attached.
_TITLE_SLUG_CHARS = 120

SKILL_PATH = Path(__file__).parent / "skill-triage-events.md"
SOURCE_UC = "UC3"


class EventTriage(BaseModel):
    """The triage verdict for one item. `reasoning` is journalled with a
    discarded item, so a verdict can be read back."""

    model_config = {"extra": "forbid"}

    verdict: Literal["major", "routine"]
    summary: str = Field(default="")
    entities: list[str] = Field(default_factory=list)
    enrichment: str = Field(default="")
    reasoning: str = Field(default="")

    @model_validator(mode="after")
    def _a_major_item_says_what_happened(self) -> "EventTriage":
        """A major verdict with no summary is not an answer: the Worker reads
        the summary, and the backfill of 2026-10-18 ingested three such items
        under an empty heading. Raised here, it is a validation error the model
        is sent back to correct (`TRIAGE_RETRIES`)."""
        if self.verdict == "major" and not self.summary.strip():
            raise ValueError("a major item needs a summary of what its text says happened")
        return self


@dataclasses.dataclass(frozen=True)
class FeedItem:
    """One entry, normalized across three feeds that agree on very little.
    `text` is what the feed carried; `article` what the official page did."""

    source: str
    title: str
    url: str
    published: str | None
    text: str
    article: str = ""


@dataclasses.dataclass(frozen=True)
class EventWatchReport:
    """What the watch did. `failed` maps a source name or an item URL to why."""

    fetched: int
    new: int
    major: int
    routine: int
    ingested: list[str]
    failed: dict[str, str]


def build_triage_agent(
    model_name: str, api_key: str, reasoning_effort: str
) -> Agent[None, EventTriage]:
    """One agent for the triage, on the same native-structured-output model the
    curator uses (`openrouter_client.build_native_output_model`): a final object,
    no function tools."""
    return Agent(
        build_native_output_model(model_name, api_key, read_timeout=TRIAGE_TIMEOUT_SECONDS),
        output_type=NativeOutput(EventTriage),
        instructions=SKILL_PATH.read_text(encoding="utf-8"),
        retries=TRIAGE_RETRIES,
        model_settings=OpenAIChatModelSettings(
            timeout=TRIAGE_TIMEOUT_SECONDS,
            openai_reasoning_effort=reasoning_effort,  # type: ignore[typeddict-item]
        ),
    )


async def fetch_feed(session: aiohttp.ClientSession, source: dict[str, str]) -> list[FeedItem]:
    """One source's entries, newest first as the feed presents them.

    Fetched with aiohttp and parsed from BYTES rather than letting feedparser
    fetch the URL itself: feedparser's own fetch is synchronous and would block
    the event loop for as long as a slow central bank takes to answer."""
    async with session.get(
        source["url"], timeout=aiohttp.ClientTimeout(total=FEED_TIMEOUT_SECONDS)
    ) as response:
        response.raise_for_status()
        raw = await response.read()
    parsed = feedparser.parse(raw)
    items = []
    for entry in parsed.entries[:MAX_ITEMS_PER_SOURCE]:
        url = str(entry.get("link") or "").strip()
        if not url:
            continue  # an entry with no link cannot be deduped, so it is not an item
        items.append(
            FeedItem(
                source=source["name"],
                title=str(entry.get("title") or "").strip(),
                url=url,
                published=str(entry.get("published")) if entry.get("published") else None,
                text=str(entry.get("summary") or "").strip(),
            )
        )
    return items


async def unseen(db: InvestmentDB, items: list[FeedItem]) -> list[FeedItem]:
    """The items this watch has never judged.

    THE DEDUPE, and it is also the whole idempotency story (UC3 step 1: "dedupe
    by URL against existing Document source_paths"). Within one run too: the
    same release can appear in two feeds, and paying a triage call twice for one
    URL is the cheapest kind of waste to avoid.

    TWO SOURCES OF TRUTH, because a Document is only created for a major item.
    A routine one is discarded and leaves no Document — so its URL was "new"
    again the following week, and the week after that. The feeds carry 15-20
    entries each and turn over slowly, so the steady state was re-triaging most
    of ~54 items every Sunday. Only the model call was paid twice; the corpus
    was correct throughout, which is why it went unnoticed.

    The second source is the EventLog rather than a state table, deliberately:
    the docstring above says a second record of an ingested document is the one
    that drifts, and that argument holds. What is recorded here is not the
    document — it is the JUDGEMENT, which nothing else stores.

    A ROW THAT RECORDED A QUESTION IS NOT A JUDGEMENT. Until 2026-10-18 an item
    the triage could not describe from its headline was journalled with
    `needs_user_input` and left for the owner, who was never reached; those
    URLs are judged again, this time on their page's text."""
    if not items:
        return []
    seen_now: set[str] = set()
    unique = []
    for item in items:
        if item.url in seen_now:
            continue
        seen_now.add(item.url)
        unique.append(item)

    placeholders = ",".join(f":u{n}" for n in range(len(unique)))
    params = {f"u{n}": item.url for n, item in enumerate(unique)}
    rows = await db.query(
        f"SELECT source_path AS url FROM document WHERE source_path IN ({placeholders}) "
        f"UNION SELECT source_id AS url FROM event_log "
        f"WHERE type = '{TRIAGE_EVENT}' AND source_id IN ({placeholders}) "
        f"AND NOT COALESCE(json_extract(payload, '$.needs_user_input'), 0)",
        **params,
    )
    known = {str(r["url"]) for r in rows}
    return [item for item in unique if item.url not in known]


async def journal_triage(db: InvestmentDB, item: FeedItem, triage: EventTriage) -> None:
    """Record that this URL was judged, and how.

    Appended for the verdict that leaves NOTHING else behind — `routine`. A
    major item that gets ingested needs no row here: its Document already
    carries the URL, and two records of one fact is the drift `unseen` warns
    about. The model's reasoning goes with it, so a discarded item's verdict
    can be read back rather than taken on trust.

    The URL is the `source_id` so the dedupe is an indexed lookup on a column
    rather than a JSON extraction over the whole log."""
    async with db.transaction():
        await db.append_event(
            type=TRIAGE_EVENT,
            source_uc=SOURCE_UC,
            source_id=item.url,
            payload={
                "verdict": triage.verdict,
                "title": item.title,
                "source": item.source,
                "reasoning": triage.reasoning,
            },
        )


def render_item(item: FeedItem) -> str:
    """The item as the triage reads it: the feed's entry, then the official
    page's text. Either may be empty, and says so — the prompt's honesty rule
    is written for that case and must not be shielded from it."""
    return (
        f"SOURCE: {item.source}\n"
        f"PUBLISHED: {item.published or '(the feed carried no date)'}\n"
        f"TITLE: {item.title}\n"
        f"URL: {item.url}\n\n"
        f"TEXT AS PUBLISHED IN THE FEED:\n{item.text or '(the feed carried no text — title only)'}"
        f"\n\nTEXT OF THE OFFICIAL PAGE:\n{item.article or '(the page carried no text)'}"
    )


def event_document(item: FeedItem, triage: EventTriage) -> str:
    """The markdown a major event becomes. Written to the inbox as a file
    because that is what `CorpusIngester` consumes — the ingester chunks and
    embeds TEXT, and a file is how text reaches it here as everywhere else.

    The summary, entities and enrichment are separate sections rather than one
    block: the ingester chunks on size, and keeping the model's own sourced
    summary apart from its recalled enrichment means a retrieved passage tends
    to be one or the other."""
    entities = ", ".join(item for item in triage.entities if item) or "(none named)"
    return (
        f"# {item.title}\n\n"
        f"Source: {item.source} — {item.url}\n"
        f"Published: {item.published or 'unknown'}\n\n"
        f"## Summary\n\n{triage.summary}\n\n"
        f"## Entities\n\n{entities}\n\n"
        f"## Context\n\n{triage.enrichment or '(none provided)'}\n\n"
        f"## As published\n\n{item.article or item.text or '(the title only)'}\n"
    )


# How much of a summary the Worker's list of recent events carries per item:
# the decision and its reasons are in the first sentences.
EVENT_SUMMARY_CHARS = 600


def read_event_passage(first_passage: str) -> dict[str, str]:
    """The title, the publication date and the summary back out of an event
    document's FIRST passage — the reader of the format `event_document`
    writes, kept beside it so the two change together. The summary starts a few
    lines into the document, so the first passage carries its opening sentences
    even when the chunker splits the rest off.

    A document with an empty summary (ingested before a major verdict had to
    carry one) gives the opening of its official text instead, labelled as
    such. Either is capped at `EVENT_SUMMARY_CHARS`."""

    def section(heading: str, next_heading: str | None) -> str:
        """The text under `heading`, up to `next_heading` or the passage's end."""
        end = rf"(?:## {next_heading}|\Z)" if next_heading else r"\Z"
        found = re.search(rf"## {heading}\s*(.*?)\s*{end}", first_passage, flags=re.DOTALL)
        return re.sub(r"\s+", " ", found.group(1)).strip() if found else ""

    title = re.search(r"^#\s*(.+)$", first_passage, flags=re.MULTILINE)
    published = re.search(r"Published:\s*(.+?)\s*(?:\n|##|\Z)", first_passage)
    text = section("Summary", "Entities")
    if not text and (opening := section("As published", None)):
        text = f"(opening of the official text) {opening}"
    if len(text) > EVENT_SUMMARY_CHARS:
        text = text[:EVENT_SUMMARY_CHARS].rsplit(" ", 1)[0] + " …"
    date_text = published.group(1).strip() if published else ""
    return {
        "title": title.group(1).strip() if title else "",
        "published": "" if date_text == "unknown" else date_text,
        "summary": text,
    }


def event_filename(item: FeedItem) -> str:
    """A filesystem-safe name carrying the item's TITLE.

    The name is not cosmetic: `ingester.title_from` reads the Document's title
    off the filename and `document_id_for` hashes that title into the id. A
    name like `event-3f2a91.md` would therefore put "event 3f2a91" in the corpus
    as the title of an FOMC statement, and every retrieval afterwards would show
    it. So the headline goes in the name, slugged and truncated, with the URL's
    hash as a suffix to keep two same-titled releases apart (the SNB republishes
    "Data portal - ..." weekly).

    Falls back to the hash alone if the title slugs to nothing — a feed entry
    with a title of pure punctuation is not worth a crash."""
    digest = hashlib.sha256(item.url.encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^A-Za-z0-9 ]+", " ", item.title)
    slug = re.sub(r"\s{2,}", " ", slug).strip()[:_TITLE_SLUG_CHARS].strip()
    return f"{slug} {digest}.md" if slug else f"event {digest}.md"


async def run_event_watch(
    db: InvestmentDB,
    ingester: CorpusIngester,
    triage_agent: Agent[None, EventTriage],
) -> EventWatchReport:
    """The whole 08:05 slot.

    THE EVENT FILE IS WRITTEN TO A TEMPORARY DIRECTORY AND NEVER TO THE INBOX,
    and that is not tidiness. The inbox is the WATCHER's queue: it polls every
    60 seconds, ingests whatever it finds as `kind="book"` and then moves the
    file to `sources/`. An event left there would be re-ingested within the
    minute under the same `document_id` — the id is hashed from the title, which
    is read off the filename — overwriting `kind='event'` with `'book'` and,
    fatally, `source_path` with the file path. The URL would be gone, the dedupe
    would never match it again, and the item would be re-triaged and re-ingested
    every week, paying a model call each time.

    The file is a handoff format, not an artefact: `CorpusIngester` consumes a
    path, the event's provenance is its URL, and the text itself survives as
    passages.

    A SOURCE THAT FAILS DOES NOT COST THE OTHERS — the same policy as the
    catch-up and the curation sweep, for the same reason: one central bank's
    outage must not cost the week's watch. What it must not do is fail silently,
    hence the map and the log line.

    An item whose PAGE cannot be fetched, or that fails TRIAGE, is likewise
    recorded and skipped: it stays un-ingested and un-journalled, so the next
    week sees it as new again and retries it."""
    fetched = new_items = major = routine = 0
    ingested: list[str] = []
    failed: dict[str, str] = {}
    candidates: list[FeedItem] = []
    domains = tuple(source["domain"] for source in EVENT_SOURCES)

    async with aiohttp.ClientSession() as session:
        for source in EVENT_SOURCES:
            try:
                items = await fetch_feed(session, source)
            except Exception as exc:
                message = redact_exception(exc)
                logger.warning("event watch: %s unreachable — %s", source["name"], message)
                failed[source["name"]] = f"{type(exc).__name__}: {message}"
                continue
            fetched += len(items)
            candidates.extend(items)

        fresh = await unseen(db, candidates)
        new_items = len(fresh)
        staging = tempfile.TemporaryDirectory(prefix="event-watch-")
        inbox = Path(staging.name)

        deadline = asyncio.get_running_loop().time() + EVENT_WATCH_BUDGET_SECONDS
        for index, item in enumerate(fresh):
            if asyncio.get_running_loop().time() >= deadline:
                # The remaining items are left untouched and un-journalled, so
                # the next run sees them as new — the same recovery a feed
                # outage gets. Checked BETWEEN items rather than wrapping the
                # loop in a timeout: a cancellation mid-item would lose an
                # ingestion halfway.
                #
                # Counted off the INDEX and not off the tallies: `failed` also
                # holds the sources that were unreachable, keyed by name rather
                # than by URL, so `len(failed)` is not a count of items and
                # would have made this line understate the backlog on exactly
                # the bad mornings.
                logger.warning(
                    "event watch: %d item(s) left un-triaged — the %.0fs budget is spent",
                    len(fresh) - index,
                    EVENT_WATCH_BUDGET_SECONDS,
                )
                break
            try:
                item = dataclasses.replace(
                    item, article=await fetch_article(session, item.url, domains)
                )
            except Exception as exc:
                # Not judged on its headline alone: that is what left the
                # September rate hikes out of the corpus. Retried next week.
                message = redact_exception(exc)
                logger.warning("event watch: page unavailable for %s — %s", item.url, message)
                failed[item.url] = f"{type(exc).__name__}: {message}"
                continue
            try:
                async with asyncio.timeout(TRIAGE_CALL_BUDGET_SECONDS):
                    triage = (await triage_agent.run(render_item(item))).output
            except Exception as exc:
                # TimeoutError included, deliberately: an item whose call ran
                # out of wall clock is recorded and skipped like any other
                # failure, stays un-ingested, and is seen as new again next
                # week. One item must never cost the other fifty-three.
                message = redact_exception(exc)
                logger.warning("event watch: triage failed for %s — %s", item.url, message)
                failed[item.url] = f"{type(exc).__name__}: {message}"
                continue

            if triage.verdict == "routine":
                routine += 1
                # JOURNALLED BEFORE THE COUNTER MEANS ANYTHING: a routine item
                # leaves no Document, so this row is the only thing that stops
                # the same conference invitation being re-triaged every Sunday.
                await journal_triage(db, item, triage)
                logger.info("event watch: routine, discarded — %s", item.title[:80])
                continue

            major += 1
            path = inbox / event_filename(item)
            path.write_text(event_document(item, triage), encoding="utf-8")
            result = await ingester.ingest_file(
                path, kind="event", author=item.source, source_url=item.url
            )
            ingested.append(result.document_id)
            logger.info("event watch: MAJOR, ingested — %s", item.title[:80])

    staging.cleanup()
    report = EventWatchReport(
        fetched=fetched,
        new=new_items,
        major=major,
        routine=routine,
        ingested=ingested,
        failed=failed,
    )
    logger.info("event watch: %s", report)
    return report
