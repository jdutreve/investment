"""The Worker's structured output contract (docs/ARCHITECTURE.md "WORKER";
docs/DATA_MODELS.md WorkerResult / ImprovementProposal / ReallocationProposal;
docs/TASKS.md Phase 5 `worker/result.py`).

These are the LLM I/O boundary models (CLAUDE.md "Dev standards": pydantic at
every I/O boundary). The Worker fills them; Writeback consumes them and runs
the mechanical gates over `reallocation_proposed` / `innovations_proposed`. The
Worker never sees the gates — it proposes, Writeback disposes (UC8).

`WorkerResult` is ALWAYS complete: every field is present on every run, with
`reallocation_proposed = None` and `innovations_proposed = []` standing for
"nothing to propose" (docs/ARCHITECTURE.md: "always complete, fields possibly
empty"). Optionality carries the meaning; a missing field would be a schema
violation the Phase-1bis retry policy rejects, not a silent no-op.
"""

import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ImprovementType(StrEnum):
    """docs/DATA_MODELS.md ImprovementType — the kinds of innovation the Worker
    may propose. Schema self-extension is deferred to V2 (I-27)."""

    new_invariant = "new_invariant"
    new_strategy = "new_strategy"
    strategy_revision = "strategy_revision"
    process = "process"
    data = "data"


class ScenarioAdjustment(BaseModel):
    """A QUALITATIVE-trigger reweighting of one strategy's bull/base/bear
    scenario probabilities (docs/ARCHITECTURE.md). The Worker supplies the
    trigger interpretation; the three probabilities for a strategy must sum to
    100 — Writeback enforces it, the Worker is told to respect it."""

    strategy_id: str
    scenario: str  # 'bull' | 'base' | 'bear'
    probability: float = Field(ge=0.0, le=100.0)
    rationale: str


class EvaluationDraft(BaseModel):
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


class ImprovementProposal(BaseModel):
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


class ReallocationProposal(BaseModel):
    """A paper-mode adjustment of the DEFENDER's allocation (docs/DATA_MODELS.md;
    UC8). `proposed_allocation` is percent weights that must sum to 100.
    `scenario_delta` (tactical) and `favors_delta` (structural) are the two
    inputs the 0.4/0.6 blend combined; `blend_note` records how. Writeback
    validates the user caps, the min change, the turnover cap and the
    cited-invariant eligibility BEFORE persisting a Proposal vertex — the
    Worker proposes, the gates dispose."""

    proposed_allocation: dict[str, float]
    scenario_delta: dict[str, float]
    favors_delta: dict[str, float]
    blend_note: str
    supporting_invariants: list[str]
    reasoning: str

    @field_validator("proposed_allocation")
    @classmethod
    def _weights_are_finite_and_long_only(cls, value: dict[str, float]) -> dict[str, float]:
        """Reject a book the owner could not hold, AT THE BOUNDARY.

        `mechanical/gates.py` re-checks this (`allocation_well_formed`) and that
        duplication is deliberate — but this copy is the one that does the
        useful thing. A gate refusal is silent to the model: the cycle ends with
        a ⛔ in the digest and the week's reasoning is lost. A validation error
        here is fed back by PydanticAI as a retry (`agent.OUTPUT_RETRIES`), so
        the Worker is TOLD what is wrong and gets to answer again — which is the
        difference between losing a cycle and correcting one.

        Non-negative because V1 is long-only; finite because JSON's `NaN` and
        `Infinity` tokens parse, and a NaN weight is invisible to every
        comparison-based gate downstream."""
        bad = {t: w for t, w in value.items() if not math.isfinite(w) or w < 0.0}
        if bad:
            raise ValueError(
                f"proposed_allocation weights must be finite and >= 0 (V1 is long-only); got {bad}"
            )
        return value

    @field_validator("scenario_delta", "favors_delta")
    @classmethod
    def _deltas_are_finite(cls, value: dict[str, float]) -> dict[str, float]:
        """Deltas are CHANGES, so a negative one is correct and expected — only
        non-finite is rejected. They are recorded on the proposal rather than
        gated, so a NaN here would be persisted unexamined."""
        bad = {t: w for t, w in value.items() if not math.isfinite(w)}
        if bad:
            raise ValueError(f"delta weights must be finite; got {bad}")
        return value


class WorkerResult(BaseModel):
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
    market_signal_assessment: str
    scenario_adjustments: list[ScenarioAdjustment] = Field(default_factory=list)
    evaluations: list[EvaluationDraft] = Field(default_factory=list)
    reallocation_proposed: ReallocationProposal | None = None
    innovations_proposed: list[ImprovementProposal] = Field(default_factory=list)
    reasoning: str  # also the Proposal vertex's reasoning (switch commentary folded in)
