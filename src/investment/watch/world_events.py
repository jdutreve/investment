"""World events — the facts outside the central banks that move the macro
picture: elections, wars, energy, fiscal and trade policy, other central banks.

WHY (owner, 2026-11-08: "does the context say the Democrats won the midterms?
if not, fix it by fetching every notable fact that bears on the macro
economy"). It did not. UC3 watched three central banks and nothing else, so the
Worker read the ECB's "energy shock" without knowing an Iran war caused it, and
a change of Congress in the economy whose rates, credit and equities the stack
holds reached it not at all.

THE SOURCE IS WIKIPEDIA'S "CURRENT EVENTS" PORTAL, one page per day. Chosen
over news feeds for three properties a weekly job on a laptop that sleeps
needs: the page for a past day stays where it is, so a run that comes a week
late reads the same week (a news feed keeps a day or two of items and the rest
is gone); each item is one or two neutral sentences with its sources cited (no
paywall, no page to scrape); and it is already a selection of what was
notable. No key, one request per day, read through the MediaWiki API.

FOUR STEPS, the event watch's shape (`event_watch.py`):

  1. FETCH — the wikitext of each day not yet read in the last
     `EVENT_LOOKBACK_DAYS`, oldest first, up to yesterday (today's page is
     still being written).
  2. PARSE — into items: category, topic, text, cited sources (`parse_day`).
  3. TRIAGE — ONE model call per DAY, not per item: a day holds twenty to forty
     items, most of them accidents, crime and sport, and the question is which
     few bear on rates, inflation, growth, credit, energy or the dollar
     (skill-triage-world-events.md). The titles already recorded in the window
     go with it, so a continuing war is picked again only when it changes.
  4. INGEST — each pick as an event Document, in the format `event_watch`
     writes and `read_event_passage` reads back, so the Worker's context lists
     it beside the central banks' items.

A DAY IS JOURNALLED ONCE READ (`WORLD_DAY_EVENT`, the day as `source_id`),
picks or none: that row is what stops it being triaged again. A day whose page
could not be fetched or whose triage failed is not journalled, and the next run
reads it again.
"""

import asyncio
import dataclasses
import logging
import re
import tempfile
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path

import aiohttp
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext
from pydantic_ai.models.openai import OpenAIChatModelSettings

from investment.corpus.ingester import CorpusIngester
from investment.db.sqlite import InvestmentDB
from investment.openrouter_client import build_native_output_model
from investment.redact import redact_exception
from investment.watch.event_watch import (
    EVENT_LOOKBACK_DAYS,
    TRIAGE_CALL_BUDGET_SECONDS,
    TRIAGE_RETRIES,
    TRIAGE_TIMEOUT_SECONDS,
    EventTriage,
    FeedItem,
    event_document,
    event_filename,
)

logger = logging.getLogger(__name__)

# The Document author of every world event, and how the Worker's context tells
# them from the central banks' items (`planner.baseline._recent_events`).
WORLD_EVENTS_SOURCE = "World events (Wikipedia Current events)"

API_URL = "https://en.wikipedia.org/w/api.php"
PAGE_URL = "https://en.wikipedia.org/wiki/Portal:Current_events/{day}"
# Wikimedia asks automated clients to say who they are. Generic on purpose:
# nothing that identifies the owner leaves the machine.
USER_AGENT = "investment-agent/1.0 (personal non-commercial research agent)"
FETCH_TIMEOUT_SECONDS = 30.0

# A day rarely holds more than three items worth a macro reader's week; eight
# is a ceiling against a triage that stops being selective, not a quota.
MAX_PICKS_PER_DAY = 8

# The whole slot, for the reason `event_watch.EVENT_WATCH_BUDGET_SECONDS`
# gives: the steps behind it wait. Thirty-five days on a first run take about a
# minute each; a day left over is read on the next run.
WORLD_EVENTS_BUDGET_SECONDS = 1800.0

WORLD_DAY_EVENT = "WorldEventsDayEvent"
SOURCE_UC = "UC3"
SKILL_PATH = Path(__file__).parent / "skill-triage-world-events.md"


@dataclasses.dataclass(frozen=True)
class WorldItem:
    """One item of a day's page. `topics` is the story it hangs under, outermost
    first ("2026 Iran war", "2026 Iran war fuel crisis")."""

    day: date
    category: str
    topics: tuple[str, ...]
    text: str
    sources: tuple[tuple[str, str], ...]  # (name, url)


class WorldEventPick(BaseModel):
    """One item the triage keeps."""

    model_config = {"extra": "forbid"}

    index: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)


class WorldDayTriage(BaseModel):
    """The items of one day that bear on the macro picture; empty is a normal
    answer for a day of accidents and sport."""

    model_config = {"extra": "forbid"}

    picks: list[WorldEventPick] = Field(max_length=MAX_PICKS_PER_DAY)


@dataclasses.dataclass(frozen=True)
class WorldEventsReport:
    days_read: int
    picked: int
    ingested: list[str]
    failed: dict[str, str]


def build_world_triage_agent(
    model_name: str, api_key: str, reasoning_effort: str
) -> Agent[int, WorldDayTriage]:
    """The day triage, on the same native-structured-output model as the event
    watch's. Its deps are the day's item count, which the validator holds every
    pick's index to."""
    agent: Agent[int, WorldDayTriage] = Agent(
        build_native_output_model(model_name, api_key, read_timeout=TRIAGE_TIMEOUT_SECONDS),
        deps_type=int,
        output_type=NativeOutput(WorldDayTriage),
        instructions=SKILL_PATH.read_text(encoding="utf-8"),
        retries=TRIAGE_RETRIES,
        model_settings=OpenAIChatModelSettings(
            timeout=TRIAGE_TIMEOUT_SECONDS,
            openai_reasoning_effort=reasoning_effort,  # type: ignore[typeddict-item]
        ),
    )
    agent.output_validator(picks_point_at_items)
    return agent


def picks_point_at_items(ctx: RunContext[int], triage: WorldDayTriage) -> WorldDayTriage:
    """Every pick names an item of the day, once."""
    indices = [pick.index for pick in triage.picks]
    wrong = [i for i in indices if i >= ctx.deps]
    if wrong or len(set(indices)) != len(indices):
        raise ModelRetry(
            f"Each pick's index must be one of the day's items (0 to {ctx.deps - 1}), "
            f"each at most once; got {indices}."
        )
    return triage


# -- parsing ------------------------------------------------------------------

_CATEGORY = re.compile(r"^'''(.+?)'''\s*$")
_BULLET = re.compile(r"^(\*+)\s*(.*)$")
_TOPIC_ONLY = re.compile(r"^\[\[[^\]]+\]\](?:\s*,\s*\[\[[^\]]+\]\])*$")
_SOURCE = re.compile(r"\[(https?://\S+)\s+([^\]]*)\]")
_WIKILINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_CONVERT = re.compile(r"\{\{convert\|([^|}]+)\|([^|}]+)[^}]*\}\}", re.I)
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")


def page_title(day: date) -> str:
    """The portal's own spelling of a day: `2026_November_3`."""
    return f"{day.year}_{day.strftime('%B')}_{day.day}"


def _plain(wikitext: str) -> str:
    text = _CONVERT.sub(r"\1 \2", wikitext)
    text = _TEMPLATE.sub("", text)
    text = _WIKILINK.sub(r"\1", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>|<[^>]+>", "", text)
    return re.sub(r"\s{2,}", " ", text.replace("'''", "").replace("''", "")).strip()


def parse_day(day: date, wikitext: str) -> list[WorldItem]:
    """The items of one day's page. A bullet that is only links is a TOPIC,
    and the bullets under it hang from it; any other bullet is an item."""
    items: list[WorldItem] = []
    category = ""
    topics: list[str] = []
    body = re.sub(r"<!--.*?-->", "", wikitext, flags=re.DOTALL)
    for raw in body.splitlines():
        line = raw.strip()
        if heading := _CATEGORY.match(line):
            category, topics = _plain(heading.group(1)), []
            continue
        bullet = _BULLET.match(line)
        if not bullet:
            continue
        depth, content = len(bullet.group(1)), bullet.group(2).strip()
        if _TOPIC_ONLY.match(content):
            topics = [*topics[: depth - 1], _plain(content)]
            continue
        sources = tuple(
            (_plain(name).strip("() ") or url, url) for url, name in _SOURCE.findall(content)
        )
        text = _plain(_SOURCE.sub("", content))
        if text:
            items.append(WorldItem(day, category, tuple(topics[: depth - 1]), text, sources))
    return items


def render_day(day: date, items: list[WorldItem], recorded: list[str]) -> str:
    """The triage's prompt for one day: the items numbered, then what is
    already recorded."""
    lines = [f"DAY: {day.isoformat()}", "", "ITEMS:"]
    for index, item in enumerate(items):
        story = " > ".join(item.topics)
        where = f"{item.category}" + (f" — {story}" if story else "")
        lines.append(f"[{index}] ({where}) {item.text}")
    lines += ["", "ALREADY RECORDED IN THE LAST WEEKS (pick a continuing story only on a change):"]
    lines += [f"  - {title}" for title in recorded] or ["  (nothing yet)"]
    return "\n".join(lines)


def as_event(item: WorldItem, pick: WorldEventPick) -> tuple[FeedItem, EventTriage]:
    """A pick in the event watch's shapes, so it is written by `event_document`
    and read back by `read_event_passage` like a central bank's item. The URL is
    the first source the item cites (the page anchor when it cites none): it is
    what makes two picks two Documents."""
    url = item.sources[0][1] if item.sources else PAGE_URL.format(day=page_title(item.day))
    cited = "; ".join(f"{name} {link}" for name, link in item.sources) or "(no source cited)"
    story = " > ".join(item.topics)
    feed = FeedItem(
        source=WORLD_EVENTS_SOURCE,
        title=pick.title,
        url=url,
        published=item.day.isoformat(),
        text=f"{item.category}" + (f" — {story}" if story else "") + f"\n\n{item.text}"
        f"\n\nSources: {cited}",
    )
    triage = EventTriage(
        verdict="major",
        summary=pick.summary,
        entities=pick.entities,
        enrichment=pick.why_it_matters,
    )
    return feed, triage


# -- the job ------------------------------------------------------------------

FetchDay = Callable[[aiohttp.ClientSession, date], Awaitable[str]]


async def fetch_day_wikitext(session: aiohttp.ClientSession, day: date) -> str:
    """The wikitext of one day's page. A missing page raises, so the day is
    read again next run rather than journalled as empty."""
    params = {
        "action": "parse",
        "page": f"Portal:Current_events/{page_title(day)}",
        "prop": "wikitext",
        "format": "json",
        "formatversion": "2",
    }
    async with session.get(
        API_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS),
    ) as response:
        response.raise_for_status()
        payload = await response.json()
    if "error" in payload:
        raise LookupError(f"{page_title(day)}: {payload['error'].get('code')}")
    return str(payload["parse"]["wikitext"])


async def days_to_read(db: InvestmentDB, today: date) -> list[date]:
    """Yesterday back to the window's start, minus the days already read,
    oldest first."""
    window = [today - timedelta(days=k) for k in range(EVENT_LOOKBACK_DAYS, 0, -1)]
    rows = await db.query(
        "SELECT source_id FROM event_log WHERE type = :t AND source_id >= :since",
        t=WORLD_DAY_EVENT,
        since=window[0].isoformat(),
    )
    read = {str(row["source_id"]) for row in rows}
    return [day for day in window if day.isoformat() not in read]


async def recorded_titles(db: InvestmentDB, today: date) -> list[str]:
    """The world events already in the corpus for the window, as the triage
    reads them to tell a new development from a continuing story."""
    rows = await db.query(
        "SELECT title FROM document WHERE kind = 'event' AND author = :a "
        "AND ingested_at >= :since ORDER BY ingested_at",
        a=WORLD_EVENTS_SOURCE,
        since=(today - timedelta(days=EVENT_LOOKBACK_DAYS)).isoformat(),
    )
    return [str(row["title"]) for row in rows]


async def journal_day(db: InvestmentDB, day: date, titles: list[str]) -> None:
    async with db.transaction():
        await db.append_event(
            type=WORLD_DAY_EVENT,
            source_uc=SOURCE_UC,
            source_id=day.isoformat(),
            payload={"day": day.isoformat(), "picked": titles},
        )


async def run_world_events(
    db: InvestmentDB,
    ingester: CorpusIngester,
    triage_agent: Agent[int, WorldDayTriage],
    today: date,
    *,
    fetch: FetchDay = fetch_day_wikitext,
) -> WorldEventsReport:
    """Read every day of the window not read yet, triage it, ingest its picks.

    One day's failure costs that day only, and it is read again next run — the
    event watch's policy, for its reason: one bad page must not cost the week."""
    days = await days_to_read(db, today)
    picked = 0
    days_read = 0
    ingested: list[str] = []
    failed: dict[str, str] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WORLD_EVENTS_BUDGET_SECONDS
    with tempfile.TemporaryDirectory(prefix="world-events-") as staging:
        inbox = Path(staging)
        async with aiohttp.ClientSession() as session:
            for index, day in enumerate(days):
                if loop.time() >= deadline:
                    logger.warning(
                        "world events: %d day(s) left unread — the %.0fs budget is spent",
                        len(days) - index,
                        WORLD_EVENTS_BUDGET_SECONDS,
                    )
                    break
                try:
                    items = parse_day(day, await fetch(session, day))
                    prompt = render_day(day, items, await recorded_titles(db, today))
                    async with asyncio.timeout(TRIAGE_CALL_BUDGET_SECONDS):
                        triage = (await triage_agent.run(prompt, deps=len(items))).output
                except Exception as exc:
                    message = redact_exception(exc)
                    logger.warning("world events: %s not read — %s", day, message)
                    failed[day.isoformat()] = f"{type(exc).__name__}: {message}"
                    continue
                titles = []
                for pick in triage.picks:
                    feed, verdict = as_event(items[pick.index], pick)
                    path = inbox / event_filename(feed)
                    path.write_text(event_document(feed, verdict), encoding="utf-8")
                    result = await ingester.ingest_file(
                        path, kind="event", author=WORLD_EVENTS_SOURCE, source_url=feed.url
                    )
                    ingested.append(result.document_id)
                    titles.append(pick.title)
                    logger.info("world events: %s — %s", day, pick.title[:80])
                await journal_day(db, day, titles)
                days_read += 1
                picked += len(titles)
    report = WorldEventsReport(days_read, picked, ingested, failed)
    logger.info("world events: %s", report)
    return report
