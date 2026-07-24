"""In-process sentence embeddings (docs/TASKS.md Task 1bis.1).

`sentence-transformers` loaded in THIS process — no Ollama daemon, no HTTP
service. ADR-002's laptop sleeps: a daemon is one more thing to be dead after a
wake, and the model is small enough that in-process costs nothing but RAM.

Two consumers share one vector space, which is the whole point: passages
(corpus/ingester.py) and invariants (`title + "\\n" + description`, the pinned
input). SUPPORTS edges are cosine over both, so any drift between how the two
sides are encoded would silently break the link — hence ONE embedder class and
ONE pinned input convention, not two call sites building their own text.

The model is lazy-loaded on first `encode`: importing this module (as the CLI
and the seed both do, transitively) must not pay a multi-second model load for
a command that never embeds anything.
"""

import logging
import threading
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - import cost is the reason it is deferred
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """The one thing every consumer needs from the embedder — text to
    normalized vectors. A Protocol so a stub can stand in for the model in
    tests (InProcessEmbedder satisfies it), and so modules that only embed a
    query or a claim need not import the concrete class."""

    def encode(self, texts: list[str]) -> np.ndarray: ...

# The dimension `all-MiniLM-L6-v2` produces. NOT authoritative on its own: the
# real value is `InProcessEmbedder.dims`, read from the loaded model. The
# seeded `embedding_dims` threshold is the stored-vector side of the same
# contract, and comparing the two is what makes an EMBEDDING_MODEL swap fail
# loudly instead of writing vectors that no longer match the stored ones
# (docs/TASKS.md Task 1bis.1). That comparison is NOT wired yet — it needs a
# startup path, which does not exist before M8; the threshold is seeded UNWIRED
# in the meantime. This constant is the test/reference value only.
DEFAULT_EMBEDDING_DIMS = 384

# float32 is what the schema's `passage.embedding BLOB` stores ("float32 x 384").
# Pinned here so the round-trip through SQLite is byte-exact.
VECTOR_DTYPE = np.float32


class InProcessEmbedder:
    """Thread-safe lazy wrapper over one SentenceTransformer.

    The lock guards MODEL LOADING only: two coroutines hitting `encode` first
    would otherwise each construct a SentenceTransformer (seconds, hundreds of
    MB) and one would be discarded. Encoding itself is left unserialized —
    sentence-transformers batches internally and the GIL already orders the
    Python-level work.
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self) -> "SentenceTransformer":
        if self._model is None:
            with self._lock:
                if self._model is None:  # re-check: another thread may have won
                    from sentence_transformers import SentenceTransformer

                    logger.info("loading embedding model %s", self._model_name)
                    self._model = SentenceTransformer(self._model_name)
        return self._model

    @property
    def dims(self) -> int:
        """The dimension the LOADED model actually produces — the value the
        startup assertion compares against the seeded threshold."""
        # `get_embedding_dimension` is the current name; the older
        # `get_sentence_embedding_dimension` still exists in 5.6 but emits a
        # FutureWarning, so this is the one that survives the next major.
        dims = self._load().get_embedding_dimension()
        if dims is None:  # pragma: no cover - defensive: model without metadata
            raise ValueError(f"embedding model {self._model_name} reports no dimension")
        return int(dims)

    def encode(self, texts: list[str]) -> np.ndarray:
        """`(len(texts), dims)` float32, L2-NORMALIZED.

        Normalizing here is what lets every consumer use a plain dot product as
        cosine similarity (`cosine_matrix` below, the SUPPORTS link in the
        ingester, the Planner's top-k searches). Doing it once at the source
        means no call site can forget and silently compare unnormalized vectors
        against normalized stored ones — a bug that degrades ranking quietly
        rather than raising.

        An empty input returns a correctly-shaped `(0, dims)` array so callers
        can concatenate without special-casing.
        """
        if not texts:
            return np.empty((0, self.dims), dtype=VECTOR_DTYPE)
        vectors = self._load().encode(
            texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return np.asarray(vectors, dtype=VECTOR_DTYPE)


def invariant_embedding_input(title: str, description: str) -> str:
    """The PINNED text an invariant is embedded from (docs/TASKS.md Task
    1bis.1: "Invariant embedding input = title + "\\n" + description").

    A function rather than an inline f-string at each call site: the ingester's
    SUPPORTS link and the Planner's invariant search must embed invariants
    IDENTICALLY or their cosines are not comparable."""
    return f"{title}\n{description}"


def to_blob(vector: np.ndarray) -> bytes:
    """float32 vector -> the bytes stored in `passage.embedding`."""
    return np.asarray(vector, dtype=VECTOR_DTYPE).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    """Inverse of `to_blob`. Length is derived from the buffer, not assumed:
    a stored vector of the wrong dimension surfaces as a shape mismatch at the
    first cosine instead of being silently reinterpreted.

    The result is READ-ONLY (`np.frombuffer` views the bytes; it does not copy).
    Deliberate: bulk reads stack many of these into one matrix and `np.stack`
    copies anyway, so paying a per-vector copy here would buy nothing — and an
    in-place write to what is a snapshot of stored data is a bug, so raising
    immediately beats mutating a view. Callers needing a mutable vector copy it
    explicitly (`np.array(from_blob(b))`)."""
    return np.frombuffer(blob, dtype=VECTOR_DTYPE)


def cosine_matrix(queries: np.ndarray, corpus: np.ndarray) -> np.ndarray:
    """`(n_queries, n_corpus)` cosine similarities.

    A plain dot product IS cosine here because `encode` normalizes — see its
    docstring. Empty corpora return a correctly-shaped empty array so callers
    (the ingester's first ever passage, before any invariant is embedded) need
    no special case."""
    if queries.size == 0 or corpus.size == 0:
        return np.empty((queries.shape[0] if queries.ndim > 1 else 0, corpus.shape[0]))
    return np.asarray(queries @ corpus.T)
