"""Shared test fixtures.

THE ONE NON-HERMETIC DEPENDENCY, isolated here. Four test modules need real
sentence-transformer embeddings (`InProcessEmbedder`), and the model is a ~90MB
Hugging Face download. On a machine where it is neither cached nor reachable,
those tests FAILED — 37 red for an environment reason, indistinguishable in the
summary from a broken invariant.

That is a defect of the test suite, not of the code under test: a red run must
mean "the code is wrong". So the model load is guarded once, here, and its
absence SKIPS with a reason instead of failing.

Skipping, not stubbing, and the distinction matters: these tests measure real
cosine behaviour. The dedup gate's thresholds were fitted to MEASURED values
(0.782 for a genuine missed duplicate, 0.907 for the wide-vs-tight pair that
must NOT merge — writeback/knowledge.py), so a stub embedder would leave the
tests passing while testing nothing about the property they exist to protect. A
skipped test says "unverified here"; a stubbed one says "verified" and lies.

Nothing else in the suite touches the network: every other module runs against a
real throwaway SQLite with synthetic market data (CLAUDE.md "Tests").
"""

import pytest

from investment.corpus.embedding import InProcessEmbedder
from investment.db.sqlite import InvestmentDB
from investment.mechanical.invariants import stored_definition

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_SKIP_REASON = (
    f"sentence-transformers model {EMBEDDING_MODEL!r} unavailable (not cached, no network). "
    "The embedding-dependent tests are skipped rather than failed — see tests/conftest.py."
)


@pytest.fixture(scope="session")
def embedder() -> InProcessEmbedder:
    """The real model, loaded ONCE per session.

    Session-scoped rather than the per-module fixtures it replaces: the four
    modules were each paying a fresh load, and one cached instance is both
    faster and the only way a single skip decision can cover all of them.

    THE `encode` CALL IS THE POINT, not a warm-up. `InProcessEmbedder` is
    deliberately LAZY — `__init__` stores a name, `_load()` runs on first use
    (corpus/embedding.py) — so wrapping only the constructor catches nothing and
    the download error surfaces later, inside whichever test body touches it
    first. That is exactly the failure this fixture exists to convert into a
    skip, so the load must be forced HERE, where the skip is possible."""
    embedder = InProcessEmbedder(EMBEDDING_MODEL)
    try:
        embedder.encode(["warm the model so a missing download fails HERE"])
    except Exception as exc:  # any load failure is the same verdict here
        pytest.skip(f"{_SKIP_REASON} ({type(exc).__name__}: {exc})")
    return embedder


async def give_invariant_a_record(
    db: InvestmentDB, invariant_id: str, confirmed: int, refuted: int
) -> None:
    """Write the CONFRONTATIONS behind an invariant's counts.

    A fixture used to state a record by setting `confirmation_count` on the
    invariant row. That is no longer a record: `restate_invariant` re-derives
    the counts from the confrontations of the definition in force, so a count
    with no row behind it is overwritten by the first restatement. The rows are
    stamped with the invariant's own definition and dated in the past, outcome
    already knowable."""
    row = (await db.query("SELECT condition, effect FROM invariant WHERE id = :i", i=invariant_id))[
        0
    ]
    for n, verdict in enumerate(["confirmed"] * confirmed + ["refuted"] * refuted):
        await db.command(
            "INSERT INTO invariant_confrontations (id, invariant_id, moment_context, "
            "signal_date, available_at, verdict, severity, source, definition) VALUES "
            "(:id, :i, 'fixture', '2020-01-01', '2020-03-25', :v, 1.0, 'backtest', :d)",
            id=f"{invariant_id}-record-{n}",
            i=invariant_id,
            v=verdict,
            d=stored_definition(row),
        )
