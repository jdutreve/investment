"""What the Worker already said, handed back to it before it speaks again.

WHY THIS EXISTS (owner, 2026-10-25: "I have the feeling it keeps rehashing the
figures"). Each Sunday the Worker started from nothing: its context carried the
market, the rule and its forecast record, never its own last readings or the
ideas it had filed. So it rediscovered the same evidence every week. The 5% IWN
round trip was raised three Sundays running and filed twice as an innovation.
The bear-steepener intuition was filed four times as a new invariant, and each
time it ended as a reference note that nothing measures. The Worker could not
know either thing. The owner, who had read both, could.

Three blocks, all bounded by `today` so a replay reads only what had been said
by then:

- its last readings, in the sections it wrote them under, so it can be told to
  repeat a point only when something changed;
- its recent ideas, each with what became of it: a measured invariant, a
  reference note that could not be measured, a strategy in probation, or a
  remark that nothing measures;
- the system ideas the owner applied, with what each has done so far
  (owner, 2026-11-08: the Worker is the brain of the system's adaptation, and
  an adaptation it cannot see the effect of teaches it nothing).
"""

import json
import re
from datetime import date, timedelta
from typing import Any

from investment.db.seed_data import TICKER_SIGNALS
from investment.db.sqlite import InvestmentDB
from investment.worker.result import PROPOSAL_HEADING, journalled_sections
from investment.writeback.recurrence import APPLIED_EVENT

WORKER_READING_EVENT = "WorkerReadingEvent"

# Two readings: last week's says what the owner just read; the one before it
# shows whether a point is coming back. More than that is a transcript.
PAST_READINGS = 2

# Nine weeks of ideas: two monthly decisions and the weeks around them, long
# enough to show an idea arriving twice, short enough to stay a list.
IDEAS_LOOKBACK_DAYS = 63
IDEAS_CAP = 10

# Half a year of applied ideas: the 12 weeks a measurement plan usually names,
# and as long again to see whether the effect lasted.
APPLIED_LOOKBACK_DAYS = 182


async def load_past_readings(db: InvestmentDB, today: date) -> list[dict[str, Any]]:
    """The Worker's last `PAST_READINGS` journalled readings dated on or before
    `today`, newest first: `{made_on, decision_date, payload}`."""
    rows = await db.query(
        "SELECT event_date, payload FROM event_log WHERE type = :t AND event_date <= :today "
        "ORDER BY event_date DESC, id DESC LIMIT :n",
        t=WORKER_READING_EVENT,
        today=today.isoformat(),
        n=PAST_READINGS,
    )
    readings = []
    for row in rows:
        payload = _json_object(row["payload"])
        readings.append(
            {
                "made_on": str(row["event_date"])[:10],
                "decision_date": payload.get("market_signal_decision_date"),
                "reading": payload.get("market_signal_reading"),
                "text": payload.get("market_signal_assessment"),
            }
        )
    return readings


async def load_recent_ideas(db: InvestmentDB, today: date) -> list[dict[str, Any]]:
    """The innovations of the last `IDEAS_LOOKBACK_DAYS`, one row per distinct
    title, newest first, with what each became.

    `created_at` bounds the list as well as `date`: an archived replay run's
    innovations are filed under their historical decision dates
    (`recurrence.import_run_innovations`), and a replay standing at one of those
    dates must not read ideas that were filed years later."""
    rows = await db.query(
        "SELECT i.date, i.type, i.title, i.spec, "
        "  (SELECT status FROM invariant v WHERE v.title = i.title "
        "   AND v.source = 'agent-discovery' ORDER BY v.created_at DESC LIMIT 1) "
        "  AS invariant_status, "
        "  (SELECT confirmation_count FROM invariant v WHERE v.title = i.title "
        "   AND v.source = 'agent-discovery' ORDER BY v.created_at DESC LIMIT 1) "
        "  AS confirmations, "
        "  (SELECT infirmation_count FROM invariant v WHERE v.title = i.title "
        "   AND v.source = 'agent-discovery' ORDER BY v.created_at DESC LIMIT 1) "
        "  AS infirmations, "
        "  (SELECT trace FROM invariant v WHERE v.title = i.title "
        "   AND v.source = 'agent-discovery' ORDER BY v.created_at DESC LIMIT 1) "
        "  AS invariant_trace, "
        "  (SELECT status FROM strategy s WHERE s.title = i.title "
        "   AND s.source = 'agent-discovery' ORDER BY s.created_at DESC LIMIT 1) "
        "  AS strategy_status, "
        "  MAX(i.source_id) AS became "
        "FROM innovation i "
        "WHERE i.date <= :today AND i.date >= :since AND substr(i.created_at, 1, 10) <= :today "
        "GROUP BY i.title ORDER BY MAX(i.date) DESC LIMIT :cap",
        today=today.isoformat(),
        since=(today - timedelta(days=IDEAS_LOOKBACK_DAYS)).isoformat(),
        cap=IDEAS_CAP,
    )
    return [dict(row) for row in rows]


def idea_fate(idea: dict[str, Any]) -> str:
    """What became of one idea, in the words the Worker reads."""
    kind = idea.get("type")
    if kind == "new_invariant":
        status = idea.get("invariant_status")
        if status is None:
            return "no invariant was created from it: a duplicate of one in the corpus, or refused"
        if status == "reference":
            return f"became a REFERENCE NOTE, never measured: {_why_not_measured(idea)}"
        return (
            f"measured over the history: {idea.get('confirmations') or 0} confirmed, "
            f"{idea.get('infirmations') or 0} refuted, status {status}"
        )
    if kind in ("new_strategy", "strategy_revision"):
        status = idea.get("strategy_status")
        if status is None:
            return (
                "no strategy was created from it; if it named a knob, its verdict is in "
                "the measured settings"
            )
        return f"strategy {status}"
    if idea.get("became"):
        return f"APPLIED by the owner, as {idea['became']}: what it has done so far is below"
    measure = _measure(idea.get("spec"))
    if measure:
        return f"recorded for the owner; to be measured by: {measure}"
    return "recorded for the owner; no measurement was stated"


def _measure(spec: Any) -> str:
    """The measurement plan a system proposal was filed with, if any."""
    try:
        parsed = json.loads(spec) if isinstance(spec, str) else spec
    except ValueError:
        return ""
    return str(parsed.get("measure") or "") if isinstance(parsed, dict) else ""


async def load_applied_ideas(db: InvestmentDB, today: date) -> list[dict[str, Any]]:
    """The system ideas the owner applied in the last `APPLIED_LOOKBACK_DAYS`,
    each with its measurement so far (docs/IMPROVEMENTS.md I-65).

    WHY THIS EXISTS (owner, 2026-11-08: "measure it when it is possible").
    A system idea states how it would be measured; once applied, these are the
    figures the machine can already give:

    - for a SERIES, the readings since the change that name its ticker, against
      as many readings before it — the Worker's own plan for the oil series —
      and the invariants now written on it, with their 35-year record, which
      is what a series is for here;
    - for anything else, only the measurement the idea stated."""
    rows = await db.query(
        "SELECT i.type, i.title, i.spec, i.source_id AS became, e.event_date AS applied_on "
        "FROM innovation i JOIN event_log e ON e.source_id = i.id AND e.type = :applied "
        "WHERE e.event_date <= :today AND e.event_date >= :since "
        "ORDER BY e.event_date DESC",
        applied=APPLIED_EVENT,
        today=today.isoformat(),
        since=(today - timedelta(days=APPLIED_LOOKBACK_DAYS)).isoformat(),
    )
    ideas = []
    for row in rows:
        idea = dict(row)
        idea["applied_on"] = str(row["applied_on"])[:10]
        idea["measure"] = _measure(row["spec"])
        alias = TICKER_SIGNALS.get(str(row["became"]))
        if alias is not None:
            idea["signal"] = alias
            idea["readings"] = await _readings_naming(
                db, str(row["became"]), idea["applied_on"], today
            )
            idea["invariants"] = await _invariants_on(db, alias, today)
        ideas.append(idea)
    return ideas


async def _readings_naming(
    db: InvestmentDB, ticker: str, applied_on: str, today: date
) -> dict[str, int]:
    """Readings after `applied_on` that name `ticker`, against the same number
    of readings before it."""
    after = await db.query(
        "SELECT payload FROM event_log WHERE type = :t AND event_date > :applied "
        "AND event_date <= :today",
        t=WORKER_READING_EVENT,
        applied=applied_on,
        today=today.isoformat(),
    )
    before = await db.query(
        "SELECT payload FROM event_log WHERE type = :t AND event_date <= :applied "
        "ORDER BY event_date DESC, id DESC LIMIT :n",
        t=WORKER_READING_EVENT,
        applied=applied_on,
        n=len(after),
    )
    return {
        "since": len(after),
        "naming_since": sum(ticker in str(row["payload"]) for row in after),
        "naming_before": sum(ticker in str(row["payload"]) for row in before),
    }


async def _invariants_on(db: InvestmentDB, signal: str, today: date) -> list[dict[str, Any]]:
    """The invariants whose condition reads `signal`, with their record."""
    rows = await db.query(
        "SELECT DISTINCT v.id, v.title, v.status, v.confirmation_count, v.infirmation_count "
        "FROM invariant v, json_each(v.condition) p "
        "WHERE json_extract(p.value, '$.signal') = :signal "
        "AND substr(v.created_at, 1, 10) <= :today ORDER BY v.created_at",
        signal=signal,
        today=today.isoformat(),
    )
    return [dict(row) for row in rows]


def describe_applied_ideas(ideas: list[dict[str, Any]]) -> list[str]:
    """The applied system ideas as the Worker reads them back, each with what
    it has done so far."""
    if not ideas:
        return []
    lines = [
        "YOUR IDEAS THE OWNER APPLIED, AND WHAT THEY HAVE DONE SO FAR. Judge them by "
        "these figures; propose to undo or extend one only on what they show:"
    ]
    for idea in ideas:
        lines.append(
            f"  - [{idea['type']}] {idea['title']}: applied {idea['applied_on']} as "
            f"{idea['became']}."
        )
        if idea.get("measure"):
            lines.append(f"    Your stated measurement: {idea['measure']}")
        readings = idea.get("readings")
        if readings is not None:
            lines.append(
                f"    Readings since that name {idea['became']}: {readings['naming_since']} "
                f"of {readings['since']}; in as many readings before: "
                f"{readings['naming_before']}."
            )
        invariants = idea.get("invariants")
        if invariants is not None:
            if not invariants:
                lines.append(
                    f"    Invariants written on `{idea['signal']}`: none yet. It is in the "
                    "vocabulary a new_invariant may use."
                )
            for invariant in invariants:
                lines.append(
                    f"    Invariant on `{idea['signal']}`: {invariant['title']} — "
                    f"status {invariant['status']}, {invariant['confirmation_count'] or 0} "
                    f"confirmed, {invariant['infirmation_count'] or 0} refuted."
                )
        if readings is None and invariants is None:
            lines.append("    Nothing measures this kind of change mechanically yet.")
    return lines


def describe_past_readings(readings: list[dict[str, Any]]) -> list[str]:
    """The readings as the Worker reads them back, newest first."""
    if not readings:
        return []
    lines = [
        "WHAT YOU ALREADY TOLD THE OWNER (your last readings, newest first). They read "
        "these. Say a point again only if something changed, and then lead with the change:"
    ]
    for past in readings:
        lines.append(
            f"  Reading of {past['made_on']} (on the decision of {past['decision_date'] or '?'}):"
        )
        reading = past.get("reading")
        if isinstance(reading, dict):
            lines.append(f"    Bottom line: {reading.get('bottom_line', '')}")
            for heading, points in journalled_sections(reading):
                if heading == PROPOSAL_HEADING:
                    continue  # in YOUR RECENT IDEAS, with what became of it
                lines.append(f"    {heading}:")
                lines += [f"      - {point}" for point in points]
        elif past.get("text"):
            # Journalled before the reading had parts: the text it was shown as.
            lines += [f"    {line}" for line in str(past["text"]).splitlines()]
    return lines


def describe_recent_ideas(ideas: list[dict[str, Any]]) -> list[str]:
    """The ideas as the Worker reads them back, each with what became of it."""
    if not ideas:
        return []
    lines = [
        "YOUR RECENT IDEAS AND WHAT BECAME OF THEM. Do not file one again. If you still "
        "hold it, file only what is new about it; one that became a reference note is "
        "worth filing again only as a condition and an effect the history can test:"
    ]
    for idea in ideas:
        lines.append(f"  - {idea['date']} [{idea['type']}] {idea['title']}: {idea_fate(idea)}")
    return lines


def _why_not_measured(idea: dict[str, Any]) -> str:
    """The reason the write boundary or the 35y sweep gave, read off the
    invariant's trace (`writeback._commit_invariant_innovation`,
    `invariants._demote_to_reference`)."""
    trace = str(idea.get("invariant_trace") or "")
    demoted = re.findall(r"\[DEMOTED to reference knowledge: ([^\]]*)\]", trace)
    if demoted:
        return str(demoted[-1])
    if "condition dropped" in trace or "effect dropped" in trace:
        return "its condition was not a list of predicates, or its effect not an object"
    return "its condition and effect did not reach the history as a testable structure"


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
