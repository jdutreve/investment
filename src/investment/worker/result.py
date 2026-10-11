"""The Worker's structured output contract (docs/ARCHITECTURE.md "WORKER";
docs/DATA_MODELS.md WorkerResult / ImprovementProposal; docs/TASKS.md Phase 5
`worker/result.py`).

These are the LLM I/O boundary models (CLAUDE.md "Dev standards": pydantic at
every I/O boundary). The Worker fills them; Writeback consumes them and runs
the mechanical gates over `innovations_proposed`. The Worker never sees the
gates — it proposes, Writeback disposes (UC8).

THE WORKER DOES NOT ALLOCATE (ADR-012, 2026-08-09). `reallocation_proposed` and
`ReallocationProposal` were removed with it: two days of M8b runs put the
cognitive value in the READING and the innovations, and the cognitive
ALLOCATION path in six of the seven defects an audit of it found. What the
Worker contributes is a market-signal reading and knowledge that gets MEASURED
over time (ADR-006), never an allocation applied once on conviction.

`WorkerResult` is ALWAYS complete: every field is present on every run, with
`innovations_proposed = []` standing for "nothing to propose"
(docs/ARCHITECTURE.md: "always complete, fields possibly empty"). Emptiness
carries the meaning; a missing field would be a schema violation the Phase-1bis
retry policy rejects, not a silent no-op.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ImprovementType(StrEnum):
    """docs/DATA_MODELS.md ImprovementType — the kinds of innovation the Worker
    may propose. Schema self-extension is deferred to V2 (I-27)."""

    new_invariant = "new_invariant"
    new_strategy = "new_strategy"
    strategy_revision = "strategy_revision"
    process = "process"
    data = "data"


class StrictModel(BaseModel):
    """Base for every model on the Worker's output boundary.

    `extra="forbid"` because this is an LLM writing JSON, and pydantic's default
    is to DROP unknown keys silently. A `conviction_delta_pct`, a
    `proposed_allocations`, a field invented under a plausible-looking name: all
    of them validated clean and arrived as an empty or defaulted value, so the
    Worker's actual answer was discarded and the cycle continued as if it had
    said nothing. Forbidding turns each into a `ValidationError`, which
    PydanticAI feeds back as a retry (`agent.OUTPUT_RETRIES`) — the model is TOLD
    the key is wrong and gets to answer again. It also emits
    `additionalProperties: false` into the JSON schema the provider sees, so the
    typo is likelier not to happen at all.

    One principle, applied at every field of this boundary: a rejection is
    recoverable, a silent acceptance is a lost week."""

    model_config = ConfigDict(extra="forbid")


class ScenarioAdjustment(StrictModel):
    """A QUALITATIVE-trigger reweighting of one strategy's bull/base/bear
    scenario probabilities (docs/ARCHITECTURE.md). The Worker supplies the
    trigger interpretation; the three probabilities for a strategy must sum to
    100 — Writeback enforces it, the Worker is told to respect it."""

    strategy_id: str
    scenario: str  # 'bull' | 'base' | 'bear'
    probability: float = Field(ge=0.0, le=100.0)
    rationale: str


class EvaluationDraft(StrictModel):
    """The Worker's read on whether new evidence confirms/weakens/invalidates a
    strategy's thesis (docs/ARCHITECTURE.md). A DRAFT: the verdict matures
    mechanically at +12w (outcomes.py, M8), never on the Worker's say-so.

    Both fields are constrained to what docs/TASKS.md Phase 5's VERDICT CONTRACT
    already pins, because this row is now persisted rather than reduced to a
    conviction nudge: an out-of-contract draft used to be absorbed silently (an
    unknown verdict string moved conviction by whatever it asked, then vanished),
    and it now lands in the `evaluation` vertex where every later reader takes
    it at face value.

    `Literal` rather than a free string: `verdict` is the value confrontations
    branch on, so an invented one ('invented', 'strong-confirms') is not a
    variant spelling, it is a verdict nothing knows how to act on.

    `[-10, +10]` is the contract's own bound, not a number invented here — and
    `allow_inf_nan=False` is the half a range check cannot express: NaN passes
    `ge`/`le` (every comparison against it is False), reaches sqlite as NULL and
    trips `conviction_delta`'s NOT NULL, aborting the whole knowledge commit —
    the confrontations and innovations with it. Rejected at the boundary is a
    dropped draft; accepted here is a lost cycle."""

    strategy_id: str
    verdict: Literal["confirms", "weakens", "invalidates", "neutral"]
    conviction_delta: float = Field(ge=-10.0, le=10.0, allow_inf_nan=False)
    events: list[str]
    reasoning: str


class ImprovementProposal(StrictModel):
    """An innovation the Worker proposes (docs/DATA_MODELS.md). `spec` is
    type-dependent (new_invariant: InvariantCandidate fields; new_strategy: a
    full strategy spec incl. its 3 scenarios). `author` drives the floor tier
    exactly as Invariant.author does; `weight_initial`/`floor_weight` bind for
    new_invariant only and are ignored otherwise (defaulted, not required, so a
    process/data proposal need not invent them)."""

    type: ImprovementType
    title: str
    rationale: str
    spec: dict[str, Any]
    source: str = "agent-discovery"
    # The tier whose seeded band binds the weights below (writeback/knowledge.py
    # `author_band`). CLOSED, because an unlisted tier is not a permissive
    # default: `author_band` raises on it, and the raise lands mid-innovation
    # rather than at the boundary that could have dropped one proposal.
    author: Literal["dalio", "marks", "other", "system"] = "system"
    status: str = "proposed"
    # 0-1 fractions (CLAUDE.md "Invariant weight model": weight-like fields are
    # 0-1 everywhere). `allow_inf_nan=False` is the load-bearing half: the tier
    # band CLAMPS these, and `min(max(nan, low), high)` is nan — every
    # comparison against NaN is False, so the clamp that exists to bound a bad
    # number passes it straight through to a NOT NULL violation on insert.
    weight_initial: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    floor_weight: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    trace: str


# One point of a reading: long enough for a number, its date and what it means,
# short enough that a second idea has to go in a second point.
_POINT_CHARS = 400
_POINTS = 6

Point = Annotated[str, Field(min_length=1, max_length=_POINT_CHARS)]
Points = Annotated[list[Point], Field(max_length=_POINTS)]


class MarketSignalReading(StrictModel):
    """The Worker's reading of the month's mechanical decision, IN PARTS.

    IT WAS ONE STRING, and on 2026-10-11 that string was six hundred words in a
    single paragraph: the conclusion in its last sentence, readings on two
    clocks, judgements and forecasts all in one voice, and nothing to tell a
    measured fact from a bet. The content was good and the owner could not read
    it. The parts below are the ones that paragraph already contained, each in
    a field of its own, so that what kind of statement a sentence is does not
    depend on how it is phrased.

    `bottom_line` comes first because the owner places the order by hand and
    reads this on a phone: what to retain, and whether anything is asked of
    them. The four lists keep apart what must not be mixed — the DECISION's own
    figures, the tape since, the Worker's judgement, and its forecasts. A list
    may be empty; the bottom line may not.

    The length limits are the instruction, enforced: a point that does not fit
    is two points, or is not one."""

    bottom_line: str = Field(min_length=1, max_length=_POINT_CHARS)
    # Facts on the DECISION clock: what the monthly decision saw, each with its
    # figure and date. The only place a decision-date number belongs.
    what_the_decision_saw: Points
    # Facts on TODAY's clock: where the tape has moved since, each dated.
    since_then: Points
    # Judgement: where the decision looks wrong and what the instrument cannot
    # see. Said as judgement, because it is one.
    my_reading: Points
    # Forecasts and conditions: what would change the next decision. Kept apart
    # because a forecast written in the voice of a reading is taken for one.
    what_to_watch: Points

    def render(self) -> str:
        """The reading as text: the bottom line, then each part under its
        heading, one point per line. What every reader that wants a string
        gets — the chat reply, the replay's log, the journal."""
        lines = [self.bottom_line]
        for heading, points in self.sections():
            lines.append(f"{heading}:")
            lines += [f"  - {point}" for point in points]
        return "\n".join(lines)

    def journal(self) -> dict[str, Any]:
        """The reading as it is journalled: its fields, and its sections with
        their headings so that no reader of the journal needs to know them."""
        return {
            **self.model_dump(),
            "sections": [
                {"heading": heading, "points": points} for heading, points in self.sections()
            ],
        }

    def sections(self) -> list[tuple[str, list[str]]]:
        """The non-empty parts with the heading each is shown under. ONE list of
        headings, written into the journal with the reading (`journal`), so that
        a forecast cannot be labelled a fact in one channel."""
        return [
            (READING_HEADINGS[name], points)
            for name in READING_HEADINGS
            if (points := getattr(self, name))
        ]


READING_HEADINGS: dict[str, str] = {
    "what_the_decision_saw": "What the decision saw",
    "since_then": "Since then, on today's tape",
    "my_reading": "My reading (judgement, not measurement)",
    "what_to_watch": "What to watch (forecasts, not facts)",
}


def journalled_sections(reading: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """The sections of a reading read back from the journal, with the headings
    it was JOURNALLED under (`MarketSignalReading.journal`). The renderers never
    hold a copy of the headings: the text digest, the mail and the dashboard —
    which is not Python — all show what the journal says."""
    return [
        (str(section["heading"]), [str(point) for point in section["points"]])
        for section in reading.get("sections") or []
    ]


class WorkerResult(StrictModel):
    """The Worker's complete output for one UC8 cycle (docs/ARCHITECTURE.md
    "WORKER"). Always complete; empty fields mean "nothing to propose", not
    "forgot to fill" — the schema makes the empty state explicit so a partial
    answer fails validation (Phase-1bis policy) rather than passing silently."""

    regime_assessment: str
    ranking_commentary: str  # explains the mechanical ranking, never re-ranks it
    # ADR-011's ENTIRE cognitive contribution to the adopted strategy: the Worker
    # reads the mechanical decision and says where it looks wrong and what the
    # signal cannot see. It gets its own required field rather than being left to
    # dissolve into `regime_assessment` or `reasoning`, for two reasons. The
    # system prompt already asks for this reading in those words ("that reading
    # is your contribution"), so a schema with nowhere to put it asks for work it
    # then discards. And ADR-011 promises the reading is "journalled and
    # rendered" — `decision_cycle` can only journal a field it can name, and the
    # digest can only render one it can find.
    #
    # REQUIRED, like every other prose field here: the Worker is asked for this
    # every cycle, and the empty state is a sentence ("no mechanical decision in
    # context"), not an absent key (Phase-1bis: a partial answer fails validation
    # rather than passing silently).
    #
    # STRUCTURED since 2026-10-11 (`MarketSignalReading`): the reading was one
    # string and came back as one unreadable paragraph.
    market_signal_assessment: MarketSignalReading
    # REQUIRED KEYS carrying EMPTY VALUES, which is the distinction this
    # docstring's "always complete" claims and defaults quietly gave up:
    # `default_factory=list` means an omitted `innovations_proposed` validates
    # clean as [], so "the Worker had nothing to propose" and "the Worker forgot
    # the field" became the same result — exactly the silent partial answer the
    # Phase-1bis policy rejects. The empty state stays expressible ([] and null,
    # per CLAUDE.md's `innovations_proposed: list`); it just has to be SAID.
    # The system prompt already asks for it in those words (agent.py: "must
    # include innovations_proposed, an empty list if none").
    scenario_adjustments: list[ScenarioAdjustment]
    evaluations: list[EvaluationDraft]
    innovations_proposed: list[ImprovementProposal]
    reasoning: str  # also the Proposal vertex's reasoning (switch commentary folded in)
