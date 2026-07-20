"""M7 unit tests for `corpus/embedding.py` — the vector-space contract.

These pin the properties every downstream consumer (SUPPORTS links, the
Planner's top-k searches) silently depends on: vectors are normalized, the
blob round-trip is exact, and a dot product really is cosine.

The model loads for real (no mock — CLAUDE.md "real throwaway SQLite, no
mocks"; the same reasoning applies to the embedder: mocking it would test
nothing about the contract that matters).
"""

import numpy as np
import pytest

from investment.corpus.embedding import (
    DEFAULT_EMBEDDING_DIMS,
    InProcessEmbedder,
    cosine_matrix,
    from_blob,
    invariant_embedding_input,
    to_blob,
)


@pytest.fixture(scope="module")
def embedder() -> InProcessEmbedder:
    return InProcessEmbedder("all-MiniLM-L6-v2")


def test_encode_shape_and_dims(embedder: InProcessEmbedder) -> None:
    out = embedder.encode(["inflation erodes bonds", "gold hedges real rates"])
    assert out.shape == (2, DEFAULT_EMBEDDING_DIMS)
    assert out.dtype == np.float32
    assert embedder.dims == DEFAULT_EMBEDDING_DIMS


def test_encode_normalizes_so_dot_product_is_cosine(embedder: InProcessEmbedder) -> None:
    # The load-bearing property: every consumer treats `a @ b.T` as cosine.
    out = embedder.encode(["credit spreads widen in recessions", "small value outperforms"])
    norms = np.linalg.norm(out, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_encode_empty_returns_well_shaped_array(embedder: InProcessEmbedder) -> None:
    # The ingester's first passage runs before any invariant is embedded.
    out = embedder.encode([])
    assert out.shape == (0, DEFAULT_EMBEDDING_DIMS)


def test_semantic_similarity_orders_sensibly(embedder: InProcessEmbedder) -> None:
    # Not a quality benchmark — a sanity floor. If a near-paraphrase does not
    # beat an unrelated sentence, the model or the normalization is broken and
    # every SUPPORTS edge downstream is noise.
    vecs = embedder.encode(
        [
            "Rising inflation hurts nominal bond returns.",
            "Nominal bonds lose value when inflation increases.",
            "The portfolio rebalancing calendar is quarterly.",
        ]
    )
    sims = cosine_matrix(vecs[:1], vecs[1:])[0]
    assert sims[0] > sims[1]


def test_blob_round_trip_is_exact(embedder: InProcessEmbedder) -> None:
    vec = embedder.encode(["duration risk"])[0]
    assert np.array_equal(from_blob(to_blob(vec)), vec)


def test_cosine_matrix_shape_and_empty_corpus(embedder: InProcessEmbedder) -> None:
    queries = embedder.encode(["a", "b"])
    corpus = embedder.encode(["c", "d", "e"])
    assert cosine_matrix(queries, corpus).shape == (2, 3)
    assert cosine_matrix(queries, np.empty((0, DEFAULT_EMBEDDING_DIMS), np.float32)).size == 0


def test_invariant_embedding_input_is_pinned() -> None:
    # Pinned because the ingester and the Planner must embed invariants
    # identically or their cosines are not comparable.
    assert invariant_embedding_input("Title", "Desc") == "Title\nDesc"


def test_from_blob_is_read_only_by_design(embedder: InProcessEmbedder) -> None:
    # Pinned because it is a deliberate choice, not an accident of frombuffer:
    # a stored vector is a snapshot, so an in-place write is a bug and should
    # raise at the write rather than silently mutate a view.
    vec = from_blob(to_blob(embedder.encode(["credit spread"])[0]))
    assert not vec.flags.writeable
    with pytest.raises(ValueError):
        vec[0] = 0.0
