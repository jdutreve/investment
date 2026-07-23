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

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


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
    mechanically at +12w (outcomes.py, M8), never on the Worker's say-so."""

    strategy_id: str
    verdict: str  # 'confirms' | 'weakens' | 'invalidates' | 'neutral'
    conviction_delta: float
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
    author: str = "system"
    status: str = "proposed"
    weight_initial: float = 0.0
    floor_weight: float = 0.0
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


class WorkerResult(BaseModel):
    """The Worker's complete output for one UC8 cycle (docs/ARCHITECTURE.md
    "WORKER"). Always complete; empty fields mean "nothing to propose", not
    "forgot to fill" — the schema makes the empty state explicit so a partial
    answer fails validation (Phase-1bis policy) rather than passing silently."""

    regime_assessment: str
    ranking_commentary: str  # explains the mechanical ranking, never re-ranks it
    scenario_adjustments: list[ScenarioAdjustment] = Field(default_factory=list)
    evaluations: list[EvaluationDraft] = Field(default_factory=list)
    reallocation_proposed: ReallocationProposal | None = None
    innovations_proposed: list[ImprovementProposal] = Field(default_factory=list)
    reasoning: str  # also the Proposal vertex's reasoning (switch commentary folded in)
