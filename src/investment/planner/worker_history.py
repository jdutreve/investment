"""What the Worker already said, handed back to it before it speaks again.

WHY THIS EXISTS (owner, 2026-10-25: "j'ai l'impression qu'il ressasse les
chiffres"). Each Sunday the Worker started from nothing: its context carried the
market, the rule and its forecast record, never its own last readings or the
ideas it had filed. So it rediscovered the same evidence every week. The 5% IWN
round trip was raised three Sundays running and filed twice as an innovation.
The bear-steepener intuition was filed four times as a new invariant, and each
time it ended as a reference note that nothing measures. The Worker could not
know either thing. The owner, who had read both, could.

Two blocks, both bounded by `today` so a replay reads only what had been said
by then:

- its last readings, in the sections it wrote them under, so it can be told to
  repeat a point only when something changed;
- its recent ideas, each with what became of it: a measured invariant, a
  reference note that could not be measured, a strategy in probation, or a
  remark that nothing measures.
"""

import json
from datetime import date, timedelta
from typing import Any

from investment.db.sqlite import InvestmentDB
from investment.worker.result import journalled_sections

WORKER_READING_EVENT = "WorkerReadingEvent"

# Two readings: last week's says what the owner just read; the one before it
# shows whether a point is coming back. More than that is a transcript.
PAST_READINGS = 2

# Nine weeks of ideas: two monthly decisions and the weeks around them, long
# enough to show an idea arriving twice, short enough to stay a list.
IDEAS_LOOKBACK_DAYS = 63
IDEAS_CAP = 10


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
        "SELECT i.date, i.type, i.title, "
        "  (SELECT status FROM invariant v WHERE v.title = i.title "
        "   AND v.source = 'agent-discovery' ORDER BY v.created_at DESC LIMIT 1) "
        "  AS invariant_status, "
        "  (SELECT status FROM strategy s WHERE s.title = i.title "
        "   AND s.source = 'agent-discovery' ORDER BY s.created_at DESC LIMIT 1) "
        "  AS strategy_status "
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
            return (
                "became a REFERENCE NOTE, never measured: its condition and effect did not "
                "reach the history as a testable structure"
            )
        return f"measured over the history, status {status}"
    if kind in ("new_strategy", "strategy_revision"):
        status = idea.get("strategy_status")
        if status is None:
            return (
                "no strategy was created from it; if it named a knob, its verdict is in "
                "the measured settings"
            )
        return f"strategy {status}"
    return "recorded for the owner; nothing measures it"


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


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
