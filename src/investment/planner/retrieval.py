"""Planner variable margin — mechanical retrieval + whitelisted zooms
(docs/ARCHITECTURE.md "Detailed Planner Steps" → CALL 1a output + PYTHON
Variable execution; docs/TASKS.md Task 4.1).

Call 1a (LLM) produces a `QueryStrategies` — the genuinely variable judgment of
what to search THIS week — and this module executes it with NO further model:
embed-free cosine top-k over the passage AND invariant matrices, SUPPORTS
expansion, and the four whitelisted zooms. Split from `pre.py` for the same
reason `baseline.py` is: a pure-mechanical core the two LLM calls wrap, testable
against a seeded DB without the transport.

Decoupled from the embedder on purpose — `retrieve` takes already-embedded
query VECTORS, not text. `encode` normalizes (corpus/embedding.py), so a plain
dot product is cosine; keeping the model call in `pre.py` lets every retrieval
path be tested with synthetic vectors and no multi-second model load.

TWO matrices, not one (docs/TASKS.md ④ rationale): passages reach invariants
through SUPPORTS, but a reference-knowledge or agent-discovery invariant has NO
supporting passage to ride on, so the invariant matrix is searched DIRECTLY as
well — otherwise that knowledge is unreachable by the weekly margin.
"""

import dataclasses
from enum import StrEnum
from typing import Any

import numpy as np
from pydantic import BaseModel, Field, field_validator

from investment.corpus.embedding import cosine_matrix, from_blob
from investment.db.sqlite import InvestmentDB

# The margin is BOUNDED by contract (docs/ARCHITECTURE.md: "≤3", "never raw
# SQL"). Caps live here so both the model schema (validators below) and the
# executors enforce the same numbers.
MAX_QUERIES = 3
MAX_ZOOMS = 3
PASSAGE_TOPK = 8  # per query, matching the baseline's K=8 buckets
INVARIANT_TOPK = 8
ZOOM_ROW_CAP = 20  # every zoom is bounded — a zoom is depth on ONE thing, not a scan


# -- Call 1a output contract (LLM I/O boundary) -----------------------------


class ZoomKind(StrEnum):
    """The whitelisted zoom targets (docs/ARCHITECTURE.md CALL 1a). An enum, not
    free text: the Planner picks depth on a KNOWN axis, never raw SQL."""

    strategy_history = "strategy_history"
    invariant_confrontations = "invariant_confrontations"
    regime_history = "regime_history"
    proposal_thread = "proposal_thread"


class Zoom(BaseModel):
    """One whitelisted deep-dive. `arg` is an id (strategy/invariant/proposal)
    or, for `regime_history`, how many recent regime instances to show."""

    kind: ZoomKind
    arg: str


class QueryStrategies(BaseModel):
    """Call 1a's output — the VARIABLE margin only (docs/ARCHITECTURE.md). Empty
    lists are valid: a quiet week needs no extra context. Both lists are capped
    at the contract's 3, truncated (not rejected) if the model over-produces —
    the retrieval must not abort a whole cycle over an off-by-one, it takes the
    first 3."""

    corpus_queries: list[str] = Field(default_factory=list)
    zooms: list[Zoom] = Field(default_factory=list)

    @field_validator("corpus_queries")
    @classmethod
    def _cap_queries(cls, v: list[str]) -> list[str]:
        return v[:MAX_QUERIES]

    @field_validator("zooms")
    @classmethod
    def _cap_zooms(cls, v: list[Zoom]) -> list[Zoom]:
        return v[:MAX_ZOOMS]


@dataclasses.dataclass(frozen=True)
class RetrievalPool:
    """Everything the variable margin fetched — the candidate set Call 1b
    filters into a PlannerContext. `invariants` is the UNION of the
    SUPPORTS-linked invariants of the retrieved passages and the direct
    invariant-matrix hits (deduped)."""

    passages: list[dict[str, Any]]  # id, excerpt, similarity
    invariants: list[dict[str, Any]]  # id, title, weight_effective, tags, author, status
    zoom_results: list[dict[str, Any]]  # {kind, arg, rows}


# -- pure core --------------------------------------------------------------


def top_k_union(sims: np.ndarray, k: int) -> list[tuple[int, float]]:
    """Union of each query's top-k corpus indices, keyed to the BEST similarity
    that any query gave it, sorted by that similarity descending. A passage
    strongly matched by one query outranks one weakly matched by two — the max,
    not the sum, so a single sharp hit is not diluted by unrelated queries."""
    if sims.size == 0:
        return []
    best: dict[int, float] = {}
    for q in range(sims.shape[0]):
        row = sims[q]
        top = np.argsort(row)[::-1][:k]
        for idx in top:
            score = float(row[idx])
            i = int(idx)
            if i not in best or score > best[i]:
                best[i] = score
    return sorted(best.items(), key=lambda item: item[1], reverse=True)


def _safe_window(arg: str, default: int, cap: int) -> int:
    """`regime_history`'s arg is a count of recent instances. A non-numeric or
    absurd value falls back to `default` and is capped — a zoom stays bounded
    whatever the model puts in the field."""
    try:
        n = int(float(arg))
    except (TypeError, ValueError):
        return default
    return max(1, min(n, cap))


# -- async DB layer ---------------------------------------------------------


async def _load_matrix(
    db: InvestmentDB, table: str, extra_cols: str
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """`(rows, matrix)` for a table's embedded rows. `matrix` is
    `(n_rows, dims)` stacked from the stored blobs; rows without an embedding
    are excluded (they cannot be searched). Empty table → a `(0, 0)` matrix so
    `cosine_matrix` returns an empty result rather than raising."""
    rows = await db.query(
        f"SELECT id, {extra_cols}, embedding FROM {table} WHERE embedding IS NOT NULL ORDER BY id"
    )
    if not rows:
        return [], np.empty((0, 0))
    matrix = np.stack([from_blob(r["embedding"]) for r in rows])
    return rows, matrix


async def _linked_invariant_ids(db: InvestmentDB, passage_ids: list[str]) -> list[str]:
    """The invariant ids SUPPORTS-linked to the retrieved passages (the ride-on
    path). Empty passage set → no query, no ids."""
    if not passage_ids:
        return []
    placeholders = ",".join(f":p{i}" for i in range(len(passage_ids)))
    params = {f"p{i}": pid for i, pid in enumerate(passage_ids)}
    rows = await db.query(
        f"SELECT DISTINCT invariant_id FROM supports WHERE passage_id IN ({placeholders})",
        **params,
    )
    return [str(r["invariant_id"]) for r in rows]


async def _fetch_invariants(db: InvestmentDB, invariant_ids: list[str]) -> list[dict[str, Any]]:
    if not invariant_ids:
        return []
    placeholders = ",".join(f":i{n}" for n in range(len(invariant_ids)))
    params = {f"i{n}": iid for n, iid in enumerate(invariant_ids)}
    return await db.query(
        "SELECT id, title, weight_effective, tags, author, status FROM invariant "
        f"WHERE id IN ({placeholders}) ORDER BY weight_effective DESC",
        **params,
    )


async def execute_zoom(db: InvestmentDB, zoom: Zoom) -> dict[str, Any]:
    """One whitelisted zoom → `{kind, arg, rows}`, every query bounded by
    `ZOOM_ROW_CAP`. The SQL is fixed per kind (the Planner chose the AXIS, not
    the query — docs/ARCHITECTURE.md "never raw SQL"). Exact columns are a
    judgment call where the spec names only the axis (CLAUDE.md: state
    assumptions) — each returns the fields that make that thing legible at a
    glance."""
    if zoom.kind is ZoomKind.strategy_history:
        rows = await db.query(
            "SELECT regime_type_id, sortino_rolling, sharpe_rolling, calmar_rolling, "
            "max_drawdown, n_periods FROM favors WHERE strategy_id = :id "
            "ORDER BY sortino_rolling DESC LIMIT :n",
            id=zoom.arg,
            n=ZOOM_ROW_CAP,
        )
    elif zoom.kind is ZoomKind.invariant_confrontations:
        rows = await db.query(
            "SELECT date, verdict, source, moment_context FROM invariant_confrontations "
            "WHERE invariant_id = :id ORDER BY date DESC LIMIT :n",
            id=zoom.arg,
            n=ZOOM_ROW_CAP,
        )
    elif zoom.kind is ZoomKind.regime_history:
        rows = await db.query(
            "SELECT regime_type_id, start_date, end_date, tags, is_current FROM regime "
            "ORDER BY start_date DESC LIMIT :n",
            n=_safe_window(zoom.arg, default=12, cap=ZOOM_ROW_CAP),
        )
    else:  # proposal_thread
        rows = await db.query(
            "SELECT id, date, proposal_type, recommendation, outcome, rejection_reason "
            "FROM proposal WHERE id = :id LIMIT 1",
            id=zoom.arg,
        )
    return {"kind": zoom.kind.value, "arg": zoom.arg, "rows": rows}


async def retrieve(
    db: InvestmentDB,
    query_vectors: np.ndarray,
    zooms: list[Zoom],
    *,
    passage_k: int = PASSAGE_TOPK,
    invariant_k: int = INVARIANT_TOPK,
) -> RetrievalPool:
    """Execute a `QueryStrategies` mechanically (docs/ARCHITECTURE.md PYTHON
    Variable execution): cosine `query_vectors` over the passage matrix (top-k
    union, + SUPPORTS-linked invariants) and over the invariant matrix directly
    (top-k union), then run the whitelisted zooms. `query_vectors` is already
    embedded and normalized (see module docstring); an empty query set skips the
    cosine passes but zooms still run."""
    passages: list[dict[str, Any]] = []
    passage_ids: list[str] = []
    direct_invariant_ids: list[str] = []

    if query_vectors.size:
        passage_rows, passage_matrix = await _load_matrix(db, "passage", "content")
        for idx, sim in top_k_union(cosine_matrix(query_vectors, passage_matrix), passage_k):
            row = passage_rows[idx]
            passages.append(
                {"id": row["id"], "excerpt": row["content"], "similarity": sim}
            )
            passage_ids.append(str(row["id"]))

        invariant_rows, invariant_matrix = await _load_matrix(db, "invariant", "title")
        invariant_sims = cosine_matrix(query_vectors, invariant_matrix)
        direct_invariant_ids = [
            str(invariant_rows[idx]["id"])
            for idx, _sim in top_k_union(invariant_sims, invariant_k)
        ]

    linked_ids = await _linked_invariant_ids(db, passage_ids)
    # dict.fromkeys preserves order and dedupes; SUPPORTS-linked first (they came
    # from an actually-retrieved passage), then the direct invariant-matrix hits.
    invariant_ids = list(dict.fromkeys(linked_ids + direct_invariant_ids))
    invariants = await _fetch_invariants(db, invariant_ids)

    zoom_results = [await execute_zoom(db, z) for z in zooms]
    return RetrievalPool(passages=passages, invariants=invariants, zoom_results=zoom_results)
