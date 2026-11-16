"""The owner's literature claims (ADR-006 amendment, 2026-11-15).

A claim the literature has established and this engine cannot measure: its
horizon outruns the history (a 10-year claim gets about five independent moments
in 58 years; the first checkpoint needs ten), or its data lies outside the
registry. The owner files it with its sources; the engine never confronts,
weighs or re-judges it (`invariants.UNMEASURED_STATUSES`); the Worker reads it as
settled by the literature, not by this system — never as an integrated invariant.

Written through the agent's single-writer path like every vertex (ADR-004), and
journalled first (CLAUDE.md "EventLog").
"""

from datetime import date

from ulid import ULID

from investment.corpus.embedding import Embedder, invariant_embedding_input, to_blob
from investment.db.sqlite import InvestmentDB
from investment.mechanical.invariants import LITERATURE_STATUS

LITERATURE_EVENT = "LiteratureClaimEvent"
LITERATURE_SOURCE = "owner"


async def file_literature_claim(
    db: InvestmentDB,
    embedder: Embedder,
    *,
    title: str,
    description: str,
    sources: list[str],
    today: date,
) -> str:
    """File one literature claim; returns its id. `sources` is required: a
    literature claim that cannot say where it was established is a belief."""
    if not sources:
        raise ValueError("a literature claim names its sources")
    invariant_id = f"inv-lit-{ULID()}"
    vector = embedder.encode([invariant_embedding_input(title, description)])[0]
    cited = "; ".join(sources)
    async with db.transaction() as tx:
        await tx.append_event(
            type=LITERATURE_EVENT,
            source_uc=LITERATURE_SOURCE,
            source_id=invariant_id,
            payload={"title": title, "sources": sources},
            event_date=today,
        )
        await tx.create_vertex(
            "invariant",
            {
                "id": invariant_id,
                "title": title,
                "description": description,
                "example": f"Sources: {cited}",
                "source": LITERATURE_SOURCE,
                "author": None,
                "status": LITERATURE_STATUS,
                "tags": ["literature"],
                "embedding": to_blob(vector),
                "condition": [],
                "effect": None,
                # NOT NULL columns, and meaningless here: the engine weighs
                # nothing it does not measure, so the weight is NULL and these
                # are zero.
                "weight_initial": 0.0,
                "floor_weight": 0.0,
                "weight_effective": None,
                "trace": f"Filed by the owner from the literature (ADR-006, 2026-11-15). {cited}",
            },
        )
    return invariant_id


async def literature_claims(db: InvestmentDB) -> list[dict[str, object]]:
    """Every literature claim, oldest first — the Worker reads all of them."""
    rows = await db.query(
        "SELECT id, title, description, example FROM invariant WHERE status = :s "
        "ORDER BY created_at",
        s=LITERATURE_STATUS,
    )
    return [dict(r) for r in rows]


def describe_literature(claims: list[dict[str, object]]) -> list[str]:
    """The literature claims as prompt lines. Never with a weight or a record:
    the engine measured nothing, and a number beside them would say it had."""
    lines = [
        "FROM THE LITERATURE — claims the owner filed as settled outside this system, "
        "because their horizon or their data lies beyond what its history can measure. "
        "Read them as the literature's, with the sources named; they are NOT integrated "
        "invariants and carry no weight here. Whether one APPLIES today is for you to "
        "read from the macro tape.",
    ]
    for claim in claims:
        lines += [f"  - {claim['title']}", f"      {claim['description']}"]
        if claim.get("example"):
            lines.append(f"      {claim['example']}")
    return lines
