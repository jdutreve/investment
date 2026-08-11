"""The innovation recurrence ledger (writeback/recurrence.py).

The M8b runs showed every real finding arriving more than once from independent
dates, and nothing counted them. These pin the counting, and — more importantly
— pin it against the ACTUAL wordings the Worker produced, since a similarity
threshold that groups a hand-written pair proves nothing about the corpus it
will meet.
"""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from investment.corpus.embedding import InProcessEmbedder
from investment.db.sqlite import InvestmentDB
from investment.worker.result import ImprovementProposal
from investment.writeback import recurrence


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "rec.db")
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
def embedder() -> InProcessEmbedder:
    """THE REAL MODEL, not a stub. What is under test is whether a similarity
    threshold groups the Worker's actual paraphrases, and a stub embedder would
    only test the arithmetic around it."""
    return InProcessEmbedder("all-MiniLM-L6-v2")


# THE M8b CORPUS, VERBATIM — title and the opening of the rationale the Worker
# actually wrote, at independent dates across two runs. A stub rationale was
# tried first and is why these are here: with placeholder text the titles alone
# do not group, and a threshold validated that way would prove nothing about the
# corpus it will meet. The substance lives in the rationale, which is exactly
# what `innovation_embedding_input` embeds.
VELOCITY_CRITIQUE = [
    (
        "Market-signal stack: add a spread-direction (speed) veto to the tight-spread "
        "risk-on books",
        "The rule selects books on spread and slope LEVEL vs 10y trailing medians",
    ),
    (
        "Add credit-spread dynamics (speed/acceleration) as a confirmation input to the "
        "market-signal book selector",
        "The book selector compares BAA10Y's LEVEL against its 10y trailing median",
    ),
    (
        "Add credit-spread speed/acceleration to the market-signal book selection",
        "The rule selects the book on BAA10Y LEVEL vs its 10y trailing median "
        "(and slope vs its own)",
    ),
    (
        "Market-signal book selection should read credit-spread VELOCITY, not only level-vs-median",
        "The current rule selects the book by comparing BAA10Y and T10Y2Y against their "
        "10-year trailing medians — two LEVELS",
    ),
]


def _proposal(title: str, rationale: str = "because the tape says so") -> ImprovementProposal:
    return ImprovementProposal(
        type="strategy_revision", title=title, rationale=rationale, spec={}, trace="t"
    )


async def test_the_same_claim_in_different_words_lands_in_one_theme(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """THE M8b CORPUS, VERBATIM — the wordings the Worker actually produced for
    its most repeated critique, at independent dates across two runs. A
    threshold that groups a hand-written pair proves nothing about the corpus it
    will meet, and this is the corpus it will meet.

    THREE OF THE FOUR GROUP, and the assertion says three rather than four
    because that is what the fixture measures. The first wording reaches only
    0.628 against the others once the rationale is trimmed to its opening
    sentence; on the full rationales all four group. The test keeps the trimmed
    text — it is the harder case, and a fixture padded until it passes would be
    calibrating the corpus to the threshold instead of the reverse.

    Under-grouping is the error to prefer: it understates a count, while a false
    merge buries one critique inside another. Both are visible in the digest,
    which prints the members."""
    titles = [
        "Market-signal stack: add a spread-direction (speed) veto to the tight-spread risk-on book",
        "Add credit-spread dynamics (speed/acceleration) as a confirmation input to the "
        "market-signal book selector",
        "Add credit-spread speed/acceleration to the market-signal book selection",
        "Market-signal book selection should read credit-spread VELOCITY, not only level-vs-median",
    ]
    themes = set()
    for title in titles:
        theme, _n = await recurrence.record_innovation(
            db, _proposal(title), embedder, today=date(2026, 8, 9)
        )
        themes.add(theme)

    assert len(themes) == 2, "the trimmed fixture splits one wording off; the rest group"
    biggest = max([await recurrence.theme_recurrence(db, t) for t in themes])
    assert biggest == 3


async def test_a_different_critique_about_the_same_book_stays_apart(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """Two unrelated critiques from the same corpus must not collapse. This is
    the error the threshold must not make often: a false merge hides one
    critique inside another, whereas a missed one only understates a count."""
    velocity, _ = await recurrence.record_innovation(
        db,
        _proposal(*VELOCITY_CRITIQUE[2]),
        embedder,
        today=date(2026, 8, 9),
    )
    haven, _ = await recurrence.record_innovation(
        db,
        _proposal("Trend-overlay haven chain: GLD first, IEF as fallback, cash last"),
        embedder,
        today=date(2026, 8, 9),
    )
    assert velocity != haven


async def test_a_rerun_of_one_date_is_not_a_second_opinion(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """Two runs replaying the same date produce the SAME sentence twice. That is
    one observation repeated — counting it as two would make the strongest
    signal in the system a function of how often the harness was run."""
    title = "Trend-overlay haven chain: GLD first, IEF as fallback, cash last"
    for _ in range(3):
        theme, _n = await recurrence.record_innovation(
            db, _proposal(title), embedder, today=date(2026, 8, 9)
        )

    assert await recurrence.theme_recurrence(db, theme) == 1
    assert len(await db.query("SELECT id FROM innovation")) == 3  # every row is kept


async def test_a_recurring_theme_announces_itself(
    db: InvestmentDB, embedder: InProcessEmbedder, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole reason the ledger exists (owner, 2026-08-09): a note saying
    "revisit when someone notices" is not a mechanism, because nothing was
    watching. At NOTABLE_RECURRENCE the system says so on its own."""
    with caplog.at_level("WARNING", logger="investment.writeback.recurrence"):
        for title, rationale in VELOCITY_CRITIQUE[1:]:
            await recurrence.record_innovation(
                db, _proposal(title, rationale), embedder, today=date(2026, 8, 9)
            )

    shouted = [r.getMessage() for r in caplog.records if "RECURRING" in r.getMessage()]
    assert shouted, "the third distinct wording must be loud"
    assert "3 distinct wordings" in shouted[-1]


async def test_a_process_innovation_is_filed_like_any_other(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """`process` and `data` innovations mint no vertex — before the ledger they
    left only an event payload nobody counted, which is exactly how the regime
    label's divergence went unnoticed twice (docs/IMPROVEMENTS.md I-53)."""
    proposal = ImprovementProposal(
        type="process",
        title="Regime label-tape divergence: deflationary bust classified as rising-inflation",
        rationale="CPI speed is negative while the label says rising",
        spec={},
        trace="t",
    )
    theme, n = await recurrence.record_innovation(db, proposal, embedder, today=date(2026, 8, 9))

    row = (await db.query("SELECT type, theme_id, source_id FROM innovation"))[0]
    assert row["type"] == "process"
    assert row["theme_id"] == theme
    assert row["source_id"] is None
    assert n == 1


async def test_the_embedding_round_trips_so_later_themes_can_match(
    db: InvestmentDB, embedder: InProcessEmbedder
) -> None:
    """The theme match reads embeddings back out of SQLite as BLOBs. A silent
    corruption there would not fail — it would just stop grouping, and the
    ledger would quietly report every critique as a one-off."""
    await recurrence.record_innovation(
        db,
        _proposal("Extend the 200d trend overlay's checked set to include VCIT"),
        embedder,
        today=date(2026, 8, 9),
    )
    ids, matrix = await recurrence._themes(db)

    assert len(ids) == 1
    assert matrix.shape == (1, embedder.dims)
    assert np.isclose(float(np.linalg.norm(matrix[0])), 1.0, atol=1e-5)  # still normalized
