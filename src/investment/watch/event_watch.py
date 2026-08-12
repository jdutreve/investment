"""UC3 — Event Watch (docs/USE_CASES.md UC3; docs/TASKS.md Task 3.2).
Monday 08:05, after the catch-up and before the curation sweep.

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
  2. DEDUPE — by URL against `document.source_path`. No state table: the corpus
     already knows what it has ingested, and a second record of that fact is the
     one that drifts. This is also what makes the job idempotent, which is what
     lets the cron and the heartbeat both reach it.
  3. TRIAGE — one model call per new item (skill-triage-events.md): MAJOR or
     ROUTINE. Routine is DISCARDED, and discarded means no document, no
     embedding, no trace beyond the log line. These feeds are mostly bank
     merger approvals and conference invitations; keeping them would bury the
     one item a year that matters.
  4. INGEST — synchronously, through the same `CorpusIngester` the watcher uses,
     so the 08:10 curation sweep sees the event minutes later rather than next
     week.

THE FEED IS THE SOURCE TEXT, and what that costs is stated rather than hidden.
UC3 also describes a "bounded fetch restricted to the EVENT_SOURCES domains" to
enrich an item. Measured on the three live feeds (2026-08-12): the Fed carries a
short summary, the ECB and the SNB carry a title and nothing else. Fetching the
article HTML would need an HTML-to-text step this project does not have (pypdf
is for PDFs), and handing a model a page of tags is worse than handing it the
headline. So the model gets what the feed carried, and the honesty rule does the
rest: when that is not enough to say what happened without inventing it, the
item is flagged `needs-user-input` and the owner is asked. Adding the bounded
fetch is a real improvement and it waits for a real text extractor.

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
from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models.openai import OpenAIChatModelSettings

from investment.corpus.ingester import CorpusIngester
from investment.db.sqlite import InvestmentDB
from investment.openrouter_client import build_native_output_model

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

# How much of the headline reaches the filename. Long enough to be the
# Document's title in the corpus, short enough to stay under every filesystem's
# name limit with the hash suffix attached.
_TITLE_SLUG_CHARS = 120

SKILL_PATH = Path(__file__).parent / "skill-triage-events.md"
SOURCE_UC = "UC3"


class EventTriage(BaseModel):
    """The triage verdict for one item.

    `needs_user_input` is not an error state — it is the honest answer when the
    feed carried a headline and nothing else, and it is what stands between the
    corpus and a fabricated enrichment that would be embedded, retrieved months
    later, and read as sourced."""

    model_config = {"extra": "forbid"}

    verdict: Literal["major", "routine"]
    summary: str = Field(default="")
    entities: list[str] = Field(default_factory=list)
    enrichment: str = Field(default="")
    needs_user_input: bool = False
    reasoning: str = Field(default="")


@dataclasses.dataclass(frozen=True)
class FeedItem:
    """One entry, normalized across three feeds that agree on very little."""

    source: str
    title: str
    url: str
    published: str | None
    text: str


@dataclasses.dataclass(frozen=True)
class EventWatchReport:
    """What the watch did. `flagged` carries the items the model refused to
    guess at, so the caller can put them in front of the owner."""

    fetched: int
    new: int
    major: int
    routine: int
    ingested: list[str]
    flagged: list[FeedItem]
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
    """The items whose URL is not already a Document's `source_path`.

    THE DEDUPE, and it is also the whole idempotency story (UC3 step 1: "dedupe
    by URL against existing Document source_paths"). Within one run too: the
    same release can appear in two feeds, and paying a triage call twice for one
    URL is the cheapest kind of waste to avoid."""
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
    rows = await db.query(
        f"SELECT source_path FROM document WHERE source_path IN ({placeholders})",
        **{f"u{n}": item.url for n, item in enumerate(unique)},
    )
    known = {str(r["source_path"]) for r in rows}
    return [item for item in unique if item.url not in known]


def render_item(item: FeedItem) -> str:
    """The item as the triage reads it. The feed's own text is passed through
    verbatim, including when it is empty — the prompt's honesty rule is written
    for exactly that case and must not be shielded from it."""
    return (
        f"SOURCE: {item.source}\n"
        f"PUBLISHED: {item.published or '(the feed carried no date)'}\n"
        f"TITLE: {item.title}\n"
        f"URL: {item.url}\n\n"
        f"TEXT AS PUBLISHED IN THE FEED:\n{item.text or '(the feed carried no text — title only)'}"
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
        f"## As published\n\n{item.text or '(the feed carried the title only)'}\n"
    )


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

    An item that fails TRIAGE is likewise recorded and skipped: it stays
    un-ingested, so the next week sees it as new again and retries it."""
    fetched = new_items = major = routine = 0
    ingested: list[str] = []
    flagged: list[FeedItem] = []
    failed: dict[str, str] = {}
    candidates: list[FeedItem] = []

    async with aiohttp.ClientSession() as session:
        for source in EVENT_SOURCES:
            try:
                items = await fetch_feed(session, source)
            except Exception as exc:
                logger.warning("event watch: %s unreachable — %s", source["name"], exc)
                failed[source["name"]] = f"{type(exc).__name__}: {exc}"
                continue
            fetched += len(items)
            candidates.extend(items)

    fresh = await unseen(db, candidates)
    new_items = len(fresh)
    staging = tempfile.TemporaryDirectory(prefix="event-watch-")
    inbox = Path(staging.name)

    for item in fresh:
        try:
            async with asyncio.timeout(TRIAGE_CALL_BUDGET_SECONDS):
                triage = (await triage_agent.run(render_item(item))).output
        except Exception as exc:
            # TimeoutError included, deliberately: an item whose call ran out of
            # wall clock is recorded and skipped like any other failure, stays
            # un-ingested, and is seen as new again next week. One item must
            # never cost the other fifty-three.
            logger.warning("event watch: triage failed for %s — %s", item.url, exc)
            failed[item.url] = f"{type(exc).__name__}: {exc}"
            continue

        if triage.verdict == "routine":
            routine += 1
            logger.info("event watch: routine, discarded — %s", item.title[:80])
            continue

        major += 1
        if triage.needs_user_input:
            # NOT ingested. The model said it cannot describe this item without
            # inventing, and a document written anyway would carry the invention
            # into the corpus with an embedding on it.
            flagged.append(item)
            logger.warning("event watch: NEEDS USER INPUT — %s (%s)", item.title[:80], item.url)
            continue

        path = inbox / event_filename(item)
        path.write_text(event_document(item, triage), encoding="utf-8")
        result = await ingester.ingest_file(path, kind="event", author=item.source, source=item.url)
        ingested.append(result.document_id)

    staging.cleanup()
    report = EventWatchReport(
        fetched=fetched,
        new=new_items,
        major=major,
        routine=routine,
        ingested=ingested,
        flagged=flagged,
        failed=failed,
    )
    logger.info("event watch: %s", report)
    return report


def flagged_message(flagged: list[FeedItem]) -> str:
    """The `needs-user-input` items as one Telegram message (UC3: "flagged and
    pushed to Telegram instead of being hallucinated"). One message rather than
    one per item: three central banks on a busy week can flag several, and a
    burst of notifications is how a channel stops being read."""
    lines = [f"❓ {len(flagged)} event(s) need your input — the feed carried too little to judge:"]
    for item in flagged:
        lines.append(f"\n• {item.source}: {item.title}\n  {item.url}")
    return "\n".join(lines)
