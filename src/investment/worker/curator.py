"""UC4 knowledge curator — passages in, invariant candidates out (Task 5.3).

The ONE LLM step in the corpus pipeline. Everything upstream (ingest, chunk,
embed, SUPPORTS) is mechanical; everything downstream (maturation, weights,
verdicts) is mechanical. The curator's job is the part that genuinely needs
judgment: reading prose and proposing a FALSIFIABLE claim from it.

WHAT THE MODEL DOES AND DOES NOT DECIDE (ADR-006, and the reason this module
is shaped the way it is):

- It PROPOSES a candidate: a machine-readable `condition` + `effect`, plus six
  0-100 self-assessed dimensions.
- It does NOT decide whether the candidate is any good. `interest_score` is
  computed HERE, in Python, from seeded weights — deterministic, auditable, and
  re-tunable without spending a token. Asking the model for the score too would
  invite it to disagree with its own dimensions.
- It does NOT decide whether the candidate is admissible. `validate_invariant`
  (mechanical/invariants.py — the same gate Writeback uses) is a BINARY gate,
  not a score: a claim that cannot be reduced to predicates over the signal
  registry is a "ponctual fact", not a weighted invariant, however interesting
  it reads.

The score is TRIAGE, never verdict. It decides which candidates are worth
carrying forward when a document yields more than `curation_sanity_ceiling`;
it never touches weight, status, or maturation. Belief still does not grant
integration — history does.

THE FAILURE THIS SHAPE PREVENTS: a schema with free-text conditions
("global liquidity accelerating") would pass Pydantic validation, look
structured, and be silently INCONFRONTABLE — no market_score, no verdict, no
weight, ever. Requiring `{signal, feature, op, value}` at the model boundary is
what makes a candidate reach the 35y sweep at all.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openrouter import OpenRouterProvider

from investment.db.seed_data import BENCHMARK_CLASSES, SIGNAL_ALIASES
from investment.db.sqlite import InvestmentDB
from investment.mechanical.invariants import Registries, validate_invariant

logger = logging.getLogger(__name__)

# Score weights (owner-specified, 2026-07-21). Seeded in `system_thresholds`
# so they are re-tunable without a code change — they are an unvalidated
# triage prior, like every other pinned constant here, and should be treated
# as such rather than as a measured truth.
SCORE_WEIGHTS: dict[str, float] = {
    "generalizability": 0.30,
    "testability": 0.25,
    "actionability": 0.20,
    "evidence_quality": 0.15,
    "novelty": 0.10,
}
# `temporal_robustness` is collected but NOT weighted: the owner's formula sums
# to 1.0 without it. Kept because it is the dimension most relevant to a
# 35y-confrontation engine (does the claim survive across regimes?), so it is
# recorded for inspection at the M7 STOP and can be folded into the formula
# later — an explicit gap, not an oversight.
UNWEIGHTED_DIMENSIONS: tuple[str, ...] = ("temporal_robustness",)

# PydanticAI validates the structured output and retries on failure — the
# Phase 1bis policy ("retry once with the error appended, then raise"). Using
# the framework's own retry rather than a hand-rolled loop is CLAUDE.md's
# "PydanticAI IS the abstraction — no homemade wrapper".
# Two, not one. One retry suits a TRANSIENT malformed response; it is useless
# against a structural mismatch, where it only doubles the cost and the wait.
# Measured on the ice core: with tool-based output DeepSeek failed both
# attempts on 3 of 10 batches, so the retry bought nothing — the fix was the
# output MODE (below), not more attempts.
OUTPUT_RETRIES = 2

# Bounded fan-out for the document-level job. CLAUDE.md requires external calls
# to sit behind a per-provider semaphore; the bound also keeps a long unattended
# run from tripping provider rate limits, which would turn a slow job into a
# failed one.
MAX_CONCURRENT_CALLS = 4


class Predicate(BaseModel):
    """One ANDed clause of a condition. The shape the maturation sweep reads —
    NOT prose. `signal` must be a registry key, `feature` one of
    level/speed/acceleration, `op` a comparison."""

    signal: str = Field(description="Registry signal key, e.g. 'real_rate', 'inflation'")
    feature: str = Field(description="One of: level, speed, acceleration")
    op: str = Field(description="One of: <, <=, >, >=, ==, !=")
    value: float


class Effect(BaseModel):
    """WHAT must hold while the condition is active — the valuation method."""

    handle: str = Field(description="asset:<TICKER> | asset-class:<class> | strategy:<id>")
    metric: str = Field(description="Computed indicator, e.g. 'return'")
    method: str = Field(description="One of: cross_class, cross_strategy, absolute")
    direction: str = Field(description="One of: outperform, underperform")


class CandidateScores(BaseModel):
    """The model's self-assessment, 0-100 per dimension. Explicitly a PRIOR:
    it orders candidates for human inspection, it never grades them."""

    generalizability: int = Field(ge=0, le=100)
    testability: int = Field(ge=0, le=100)
    actionability: int = Field(ge=0, le=100)
    evidence_quality: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    temporal_robustness: int = Field(ge=0, le=100)


class InvariantCandidate(BaseModel):
    """One proposed WEIGHTED invariant (docs/TASKS.md Task 5.3).

    `condition` and `effect` are REQUIRED and non-empty. That is the fix for
    the ice core's finding (2026-07-21): with both fields optional, a model
    asked for machine-readable predicates in the PROMPT simply omitted them —
    100 passages yielded 2 candidates, both with no condition and no effect.
    Models follow the schema, not the exhortation. Omission is now impossible
    by construction, and anything genuinely irreducible has its own home in
    `ReferenceNote` below rather than degrading into an unusable invariant.

    `weight_initial` is proposed here and CLAMPED by Writeback to the author
    band — the established propose/dispose split that the score follows too."""

    claim: str = Field(description="One falsifiable sentence: condition -> measurable effect")
    description: str
    example: str = Field(description="A concrete historical instance from the passage")
    condition: list[Predicate] = Field(
        min_length=1, description="ANDed predicates over registry signals; at least one"
    )
    effect: Effect = Field(description="The measurable consequence; required")
    tags: list[str] = Field(default_factory=list)
    supporting_passages: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    scores: CandidateScores
    weight_initial: float = Field(ge=0.0, le=1.0)


class ReferenceNote(BaseModel):
    """Knowledge worth keeping that is NOT a weighted invariant — a "ponctual
    fact" in the spec's terms.

    Its existence is what makes the required fields above safe to demand: a
    model facing an insight it cannot reduce to the registry now has somewhere
    honest to put it, instead of either dropping it or emitting a hollow
    invariant. The distinction becomes an explicit CHOICE rather than a
    silent degradation."""

    claim: str
    description: str
    why_not_reducible: str = Field(
        description="Which part cannot be expressed over the signal registry, and why"
    )
    supporting_passages: list[str] = Field(default_factory=list)


class CurationResult(BaseModel):
    """What one curator call returns — the two kinds, kept apart on purpose."""

    invariant_candidates: list[InvariantCandidate] = Field(default_factory=list)
    reference_notes: list[ReferenceNote] = Field(default_factory=list)


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate after the mechanical passes: scored, and either admissible
    as a weighted invariant or demoted with the gate's own reason."""

    candidate: InvariantCandidate
    interest_score: float
    rejection: str | None  # None => passes the expressibility gate

    @property
    def admissible(self) -> bool:
        return self.rejection is None


# -- pure mechanics (no LLM, no I/O — the parts that must be deterministic) --


def interest_score(scores: CandidateScores, weights: dict[str, float] | None = None) -> float:
    """Weighted 0-100 triage score, computed in PYTHON.

    Deterministic and re-tunable: changing the weights re-ranks an existing
    corpus with no LLM call. That is the whole reason the model is not asked
    for this number."""
    w = weights or SCORE_WEIGHTS
    values = scores.model_dump()
    total = sum(float(values[dim]) * weight for dim, weight in w.items())
    return round(total, 2)


def gate_candidate(candidate: InvariantCandidate, registries: Registries) -> str | None:
    """The EXPRESSIBILITY gate: `None` if the candidate can be confronted,
    else the reason it cannot.

    Delegates to `validate_invariant` — the same gate Writeback runs — so a
    candidate accepted here cannot be rejected downstream, and a signal the
    sweep would KeyError on is caught at birth."""
    # `condition` non-empty and `effect` present are now schema guarantees, so
    # the gate is purely about VOCABULARY: does every predicate name a real
    # signal, does the effect name a real handle/method/direction.
    condition = [p.model_dump() for p in candidate.condition]
    return validate_invariant(condition, candidate.effect.model_dump(), registries)


def rank(scored: list[ScoredCandidate], ceiling: int) -> list[ScoredCandidate]:
    """Admissible candidates, best first, capped at `ceiling`.

    The cap exists already as `curation_sanity_ceiling`; scoring only changes
    WHICH ones survive it — the best rather than the first, which is what an
    arbitrary truncation gives you."""
    admissible = sorted(
        (s for s in scored if s.admissible), key=lambda s: s.interest_score, reverse=True
    )
    return admissible[:ceiling]


async def build_registries(db: InvestmentDB) -> Registries:
    """The live vocabulary a candidate must reduce to. Read from the DB, not
    hardcoded: a candidate citing a strategy or ticker that does not exist is
    exactly what the gate must catch."""
    strategies = {str(r["id"]) for r in await db.query("SELECT id FROM strategy")}
    regime_types = {str(r["id"]) for r in await db.query("SELECT id FROM regime_type")}
    assets = {str(r["ticker"]) for r in await db.query("SELECT ticker FROM allowed_tickers")}
    return Registries(
        signals=set(SIGNAL_ALIASES) | assets,
        asset_classes=set(BENCHMARK_CLASSES),
        strategies=strategies,
        assets=assets,
        regime_types=regime_types,
    )


def build_agent(
    model_name: str, api_key: str, reasoning_effort: str, instructions: str
) -> Agent[None, CurationResult]:
    """One PydanticAI agent over OpenRouter.

    Both roles share this transport (config.py), so swapping a cheap model for
    an expensive one to compare curation quality is a config change — which is
    what makes the M7 STOP comparison affordable to run at all."""
    provider = OpenRouterProvider(api_key=api_key)
    # PydanticAI's bundled profile for these OpenRouter routes declares
    # `supports_json_schema_output=False` and defaults to the `tool` mode. That
    # is WRONG for deepseek-v4-flash: a direct HTTP check (2026-07-21) returned
    # a valid object from `response_format: {type: json_schema, strict: true}`
    # in 0.85s. Trusting the stale profile is what forced the tool path and
    # produced the retry-exhaustion failures. Overridden from measurement, not
    # from optimism — if a future model genuinely lacks it, the request errors
    # loudly rather than silently degrading.
    # The profile is a TypedDict at runtime: copy it and flip the two flags.
    base = dict(OpenAIChatModel(model_name, provider=provider).profile)
    base.update(supports_json_schema_output=True, default_structured_output_mode="native")
    profile = cast(OpenAIModelProfile, base)
    model = OpenAIChatModel(model_name, provider=provider, profile=profile)
    agent: Agent[None, CurationResult] = Agent(
        model,
        # NATIVE structured output (`response_format: json_schema`), not the
        # default tool-calling path. Measured 2026-07-21: reasoning models
        # reject or mangle forced tool calls — Qwen errors outright ("tool_choice
        # does not support being set to required in thinking mode") and DeepSeek
        # silently emits an unparseable call, which surfaced as 3 of 10 ice-core
        # batches exhausting their retries. The same schema over the native path
        # answered in 0.85s in a direct HTTP check.
        output_type=NativeOutput(CurationResult),
        instructions=instructions,
        # PydanticAI's own retry — validate, retry once with the error
        # appended, then raise (the Phase 1bis policy). Never a silent pass.
        retries=OUTPUT_RETRIES,
        model_settings=OpenAIChatModelSettings(
            # `reasoning_effort` is a str from config; the provider validates
            # the value (deepseek-v4-flash and sonnet-5 both accept 'xhigh').
            openai_reasoning_effort=reasoning_effort,  # type: ignore[typeddict-item]
        ),
    )
    return agent


def render_instructions(registries: Registries) -> str:
    """The quality contract (docs/TASKS.md Task 5.3), with the LIVE registry
    inlined.

    Listing the actual signals is what makes the expressibility requirement
    answerable rather than a hope: the model cannot map a claim onto a
    vocabulary it was never shown."""
    signals = ", ".join(sorted(registries.signals & set(SIGNAL_ALIASES)))
    classes = ", ".join(sorted(registries.asset_classes))
    return f"""You extract FALSIFIABLE market invariants from investment literature.

A candidate is a CONDITION that implies a MEASURABLE EFFECT — never a summary
of what the passage says. "Dalio argues debt cycles repeat" is a summary and is
worthless here. "When the short real rate is negative, nominal bonds
underperform the median asset class" is a candidate.

TWO HARD REQUIREMENTS:

1. Express the FUNDAMENTAL DRIVER, not a surface correlate. Write
   `real_rate < 0`, not `regime = stagflation` — the regime is a label for the
   driver, and a label cannot be confronted against history.

2. `condition` and `effect` must use ONLY this vocabulary. A claim you cannot
   express in it is NOT a weighted invariant; omit it rather than inventing a
   signal name.
   - signals: {signals}
   - features: level, speed, acceleration
   - operators: <, <=, >, >=, ==, !=
   - effect.handle: asset:<TICKER> or asset-class:<one of {classes}>
   - effect.method: cross_class | cross_strategy | absolute
   - effect.direction: outperform | underperform
   - effect.metric: return

SCORING — rate each candidate 0-100 on six dimensions. Be honest and use the
full range; these order candidates for human review, so uniformly high scores
destroy their only purpose.
   - generalizability: does it hold beyond the episode the passage describes?
   - testability: how cleanly does it reduce to the vocabulary above?
   - actionability: would it change an allocation decision?
   - evidence_quality: how strong is the passage's own support for it?
   - novelty: is it non-obvious to an informed investor?
   - temporal_robustness: would it survive across several decades and regimes?

Also propose `weight_initial` (0.0-1.0) reflecting your confidence. It will be
clamped mechanically; propose honestly rather than strategically.

Cite the passage ids you used in `supporting_passages`. Record any
`counterexamples` the passage itself raises — a claim whose exceptions you hide
scores worse after confrontation, not better.

TWO OUTPUTS, AND YOU MUST CHOOSE BETWEEN THEM — never leave fields empty:

- `invariant_candidates`: every entry REQUIRES a non-empty `condition` and an
  `effect`. If you can express the claim over the vocabulary above, it belongs
  here, fully specified.
- `reference_notes`: for an insight worth keeping that you CANNOT reduce to
  that vocabulary. Say in `why_not_reducible` which part resists and why.

Putting a claim in `reference_notes` is a legitimate, expected answer — it is
how you report "this matters but is not measurable here". What is NOT
acceptable is a hollow `invariant_candidate`. Prefer a well-argued reference
note to a weak invariant, and prefer either to silence: a passage of narrative
history with no market claim yields nothing at all, which is also fine."""


# -- the curator ------------------------------------------------------------


class KnowledgeCurator:
    """Turns passages into scored, gated candidates."""

    def __init__(
        self,
        db: InvestmentDB,
        *,
        model_name: str,
        api_key: str,
        reasoning_effort: str = "high",
        batch_size: int = 20,
        max_concurrency: int = MAX_CONCURRENT_CALLS,
    ) -> None:
        self._db = db
        self._model_name = model_name
        self._api_key = api_key
        self._reasoning_effort = reasoning_effort
        self._batch_size = batch_size
        self._max_concurrency = max_concurrency
        # Last batch's irreducible knowledge — read by the inspection tooling
        # at the M7 STOP alongside the candidates.
        self.last_reference_notes: list[ReferenceNote] = []

    async def curate_passages(self, passages: list[dict[str, Any]]) -> list[ScoredCandidate]:
        """Curate ONE batch of passage rows -> scored, gated candidates."""
        registries = await build_registries(self._db)
        agent = build_agent(
            self._model_name,
            self._api_key,
            self._reasoning_effort,
            render_instructions(registries),
        )
        return await self._run_batch(agent, registries, passages)

    async def curate_document(self, document_id: str) -> list[ScoredCandidate]:
        """Curate a WHOLE document in one job: gather every passage, split into
        batches, run them CONCURRENTLY, then score/gate/rank the union.

        Why one long job rather than an interactive loop: curation is not
        latency-sensitive. It runs after an ingestion batch or on the Monday
        sweep, with no one waiting — so the design target is throughput and
        resumability, not response time. Measured on the real corpus, a single
        call carries a large fixed cost (the quality contract, the schema, the
        reasoning warm-up) that is independent of how many passages ride on it;
        batching amortises that cost, and bounded concurrency hides the rest.

        NOTE there is no provider-side batch discount to be had: OpenRouter
        exposes no Batches endpoint (checked 2026-07-21, /v1/batches -> 404),
        unlike the Anthropic API. "Batch" here means our own fan-out.

        One failing batch does NOT abort the document — same policy as the
        inbox watcher, and for the same reason: a job this long must not lose
        hours of work to one bad response."""
        registries = await build_registries(self._db)
        agent = build_agent(
            self._model_name,
            self._api_key,
            self._reasoning_effort,
            render_instructions(registries),
        )
        rows = await self._db.query(
            "SELECT id, page, content FROM passage WHERE document_id = :d ORDER BY position",
            d=document_id,
        )
        batches = [rows[i : i + self._batch_size] for i in range(0, len(rows), self._batch_size)]
        logger.info(
            "curator: %s -> %d passages in %d batches, %d at a time (model=%s, effort=%s)",
            document_id,
            len(rows),
            len(batches),
            self._max_concurrency,
            self._model_name,
            self._reasoning_effort,
        )
        semaphore = asyncio.Semaphore(self._max_concurrency)
        done = 0

        async def one(index: int, batch: list[dict[str, Any]]) -> list[ScoredCandidate]:
            nonlocal done
            async with semaphore:
                try:
                    scored = await self._run_batch(agent, registries, batch)
                except Exception as exc:  # a long job must survive a bad batch
                    logger.warning(
                        "curator: batch %d failed — %s: %s", index, type(exc).__name__, exc
                    )
                    return []
                done += 1
                # Progress matters here: an unattended run of this length is
                # otherwise indistinguishable from a hang.
                logger.info(
                    "curator: batch %d/%d done, %d candidates", done, len(batches), len(scored)
                )
                return scored

        results = await asyncio.gather(*(one(i, b) for i, b in enumerate(batches, 1)))
        return [scored for batch in results for scored in batch]

    async def _run_batch(
        self,
        agent: Agent[None, CurationResult],
        registries: Registries,
        batch: list[dict[str, Any]],
    ) -> list[ScoredCandidate]:
        prompt = "\n\n".join(
            f"[passage {p['id']}, page {p.get('page')}]\n{p['content']}" for p in batch
        )
        result = await agent.run(prompt)
        # Reference notes are counted, not gated: they are by definition the
        # claims that do NOT reduce to the registry. Logging them is what makes
        # selectivity readable — without it, "few candidates" cannot be told
        # apart from "the model is dropping things on the floor".
        notes = result.output.reference_notes
        if notes:
            logger.info("curator: %d reference note(s) (not weighted invariants)", len(notes))
        self.last_reference_notes = notes
        return self.score_and_gate(result.output.invariant_candidates, registries)

    def score_and_gate(
        self, candidates: list[InvariantCandidate], registries: Registries
    ) -> list[ScoredCandidate]:
        """The mechanical half — no LLM. Separated so it is testable without a
        network call, and so the same scoring applies whichever model produced
        the candidates."""
        scored: list[ScoredCandidate] = []
        for candidate in candidates:
            rejection = gate_candidate(candidate, registries)
            if rejection is not None:
                logger.info("curator: demoted %r — %s", candidate.claim[:60], rejection)
            scored.append(
                ScoredCandidate(
                    candidate=candidate,
                    interest_score=interest_score(candidate.scores),
                    rejection=rejection,
                )
            )
        return scored
