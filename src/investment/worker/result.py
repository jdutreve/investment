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
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investment.mechanical.market_signal import MA_WINDOWS


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


class Forecast(StrictModel):
    """One forecast the Worker makes, in a form a machine can score.

    WHY FORECASTS AT ALL (owner, 2026-10-18: "le worker ne fait que reprendre
    les faits qu'on lui donne. Rien de pertinent n'en sort"). The reading asked
    for "what to watch" as conditions — "if the slope stays below its median,
    the signal turns flat" — which is the rule's own mechanics restated: a
    program writes them, and none of them could ever be wrong. A probability
    on a dated outcome can be, so it is scored (`mechanical/forecasts.py`) and
    the Worker reads its own record back. That record is the only thing that
    can tell a reading worth having from one that sounds right.

    THE STATEMENT IS STRUCTURED, not parsed from prose: one series, one side,
    one reference, one date. `statement` says the same thing in words for the
    owner; the fields are what is scored. Three references, the ones the rule
    itself reads: a stated value, the series' ten-year median (how the two
    signals are read), and one of the trend overlay's moving averages."""

    statement: str = Field(min_length=1, max_length=_POINT_CHARS)
    ticker: str = Field(min_length=1, max_length=32)
    side: Literal["above", "below"]
    compared_with: Literal["value", "ten_year_median", "moving_average"]
    # `value` only: the level the series is compared with, in its own unit.
    threshold: float | None = Field(default=None, allow_inf_nan=False)
    # `moving_average` only: which overlay line, in trading days.
    window_days: int | None = None
    # Scored on the last print knowable at made_on + horizon_weeks. Thirteen
    # weeks at most: past one quarter a miss is no longer about this reading.
    horizon_weeks: int = Field(ge=1, le=13)
    # The probability that the statement holds on that date.
    probability: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _reference_is_complete(self) -> Self:
        """Each reference carries exactly the field it needs, so a forecast
        that cannot be scored is refused here and the model answers again."""
        if (self.compared_with == "value") != (self.threshold is not None):
            raise ValueError("`threshold` is given exactly when compared_with is 'value'")
        if self.compared_with == "moving_average":
            if self.window_days not in MA_WINDOWS:
                raise ValueError(f"`window_days` must be one of the overlay's lines {MA_WINDOWS}")
        elif self.window_days is not None:
            raise ValueError("`window_days` is given only when compared_with is 'moving_average'")
        return self

    def render(self) -> str:
        """The forecast as the owner reads it: the words, then the odds and the
        horizon."""
        weeks = f"{self.horizon_weeks} week{'s' if self.horizon_weeks > 1 else ''}"
        return f"{self.statement} — {self.probability:.0%}, in {weeks}"


# At most this many a cycle: a few forecasts the Worker means, not a spread of
# hedges that would score well by covering every side.
_FORECASTS = 3


class MarketSignalReading(StrictModel):
    """The Worker's reading of the month's mechanical decision, IN PARTS.

    IT WAS ONE STRING, and on 2026-10-11 that string was six hundred words in a
    single paragraph. Split into five parts that day, two of which were FACTS
    (what the decision saw, the tape since) and a third the rule's own
    mechanics written as conditions — so three parts out of five restated the
    context the Worker had been given (owner, 2026-10-18: "rien de pertinent
    n'en sort"). Those facts are now rendered mechanically beside the reading
    (the decision block and today's values in every channel), and the Worker is
    asked only for what a program cannot write:

    - `bottom_line`: what the owner should retain, and whether anything is asked.
    - `my_reading`: what the instrument cannot see, as judgement.
    - `what_would_prove_it_wrong`: the scenario in which the position in force
      loses, and the first sign it is starting.
    - `forecasts`: dated, scored probabilities (`Forecast`).

    The length limits are the instruction, enforced: a point that does not fit
    is two points, or is not one."""

    bottom_line: str = Field(min_length=1, max_length=_POINT_CHARS)
    # Judgement: what the instrument cannot see. Said as judgement, because it is one.
    my_reading: Points
    # The case against the position in force: how it loses, and what would show it.
    what_would_prove_it_wrong: Points
    forecasts: Annotated[list[Forecast], Field(max_length=_FORECASTS)]

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
        parts = {
            "my_reading": self.my_reading,
            "what_would_prove_it_wrong": self.what_would_prove_it_wrong,
            "forecasts": [forecast.render() for forecast in self.forecasts],
        }
        return [(READING_HEADINGS[name], points) for name, points in parts.items() if points]


READING_HEADINGS: dict[str, str] = {
    "my_reading": "My reading (judgement, not measurement)",
    "what_would_prove_it_wrong": "What would prove the position wrong",
    "forecasts": "Forecasts (scored when due)",
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
    # ONE AT MOST (owner, 2026-10-18): one idea worth testing a week, or none.
    # Several a week had become the norm and none had yet become an invariant
    # or a strategy.
    innovations_proposed: Annotated[list[ImprovementProposal], Field(max_length=1)]
    reasoning: str  # also the Proposal vertex's reasoning (switch commentary folded in)
