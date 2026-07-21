"""M7 tests for UC0 steps 6/6b (`seed._seed_corpus`, `seed._seed_curation`).

Step 6b calls an LLM, so what is pinned here is everything AROUND that call:
that a missing corpus is a no-op rather than a failure, that ingestion is
idempotent, and that the author tier is derived correctly. The curation call
itself is exercised in test_writeback_knowledge.py against real SQLite.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from investment.config import Settings
from investment.db.sqlite import InvestmentDB
from investment.seed import _corpus_author, _seed_corpus, _seed_curation

CORPUS_TEXT = """# Chapter 1

When real interest rates turn negative, holders of nominal bonds are paid back
in money worth less than what they lent, and gold has historically outperformed
other asset classes across those stretches. This is the mechanism behind the
1970s experience, and it repeated in the years after 2008 when policy rates sat
below the rate of inflation for an extended period.

# Chapter 2

Credit spreads widen before equity drawdowns because lenders reprice risk
before equity holders do. The spread between BAA corporate yields and the
ten-year Treasury is the cleanest market-priced expression of that repricing,
and it has led equity weakness rather than followed it.
"""


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    database = InvestmentDB(tmp_path / "test.db")
    yield database
    await database.close()


def _settings(tmp_path: Path, sources: Path) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key="test",
        fred_api_key="test",
        telegram_bot_token="test",
        telegram_chat_id="test",
        db_path=tmp_path / "seed.db",
        inbox_path=tmp_path / "inbox",
        sources_path=sources,
    )  # type: ignore[call-arg]


async def test_a_missing_corpus_is_a_no_op_not_a_failure(db: InvestmentDB, tmp_path: Path) -> None:
    """A fresh clone has no corpus — the seed must still complete. This guard
    is also what keeps the seed test suite hermetic: no documents means step 6b
    finds nothing to curate and never reaches the network."""
    result = await _seed_corpus(db, _settings(tmp_path, tmp_path / "absent"))
    assert result == {"documents": 0, "passages": 0}

    curation = await _seed_curation(db, _settings(tmp_path, tmp_path / "absent"))
    assert curation["documents"] == 0
    assert curation["candidates"] == 0


async def test_step_6_ingests_every_supported_source(db: InvestmentDB, tmp_path: Path) -> None:
    sources = tmp_path / "corpus"
    sources.mkdir()
    (sources / "some_book.md").write_text(CORPUS_TEXT)
    (sources / "cover.jpeg").write_bytes(b"not a document")

    result = await _seed_corpus(db, _settings(tmp_path, sources))
    assert result["documents"] == 1
    assert result["passages"] > 0
    rows = await db.query("SELECT COUNT(*) AS n FROM passage")
    assert int(rows[0]["n"]) == result["passages"]


async def test_step_6_is_idempotent(db: InvestmentDB, tmp_path: Path) -> None:
    """The seed promises it is safe to re-run (module docstring)."""
    sources = tmp_path / "corpus"
    sources.mkdir()
    (sources / "some_book.md").write_text(CORPUS_TEXT)
    settings = _settings(tmp_path, sources)

    first = await _seed_corpus(db, settings)
    await _seed_corpus(db, settings)
    rows = await db.query("SELECT COUNT(*) AS n FROM passage")
    assert int(rows[0]["n"]) == first["passages"]
    documents = await db.query("SELECT COUNT(*) AS n FROM document")
    assert int(documents[0]["n"]) == 1


async def test_nested_sources_are_found(db: InvestmentDB, tmp_path: Path) -> None:
    """Extracted books arrive as a DIRECTORY (markdown + page images), so a
    flat scan of `sources_path` would find nothing."""
    sources = tmp_path / "corpus"
    (sources / "SomeBook").mkdir(parents=True)
    (sources / "SomeBook" / "SomeBook.md").write_text(CORPUS_TEXT)

    result = await _seed_corpus(db, _settings(tmp_path, sources))
    assert result["documents"] == 1


async def test_a_total_curation_failure_is_raised_not_reported_as_success(
    db: InvestmentDB, tmp_path: Path
) -> None:
    """The regression that motivated the escalation rule.

    Measured 2026-07-21: a bad API key failed all 5 batches of a document and
    step 6b returned `{'documents': 1, 'candidates': 0, 'failed': []}` — a
    clean-looking inventory line for a run that accomplished nothing. Here the
    key is a stub, so every batch 401s exactly as it did then."""
    sources = tmp_path / "corpus"
    sources.mkdir()
    (sources / "some_book.md").write_text(CORPUS_TEXT)
    settings = _settings(tmp_path, sources)
    await _seed_corpus(db, settings)

    with pytest.raises(RuntimeError, match="every document failed curation"):
        await _seed_curation(db, settings)

    # And nothing is checkpointed, so a later run with a working key redoes it.
    rows = await db.query("SELECT COUNT(*) AS n FROM curated_passage")
    assert int(rows[0]["n"]) == 0


def test_author_tier_is_read_from_the_filename() -> None:
    assert _corpus_author(Path("Principles_For_Navigating_Big_Debt_Crises.pdf")) == "Ray Dalio"
    assert _corpus_author(Path("the_changing_world_order.pdf")) == "Ray Dalio"
    # Unrecognised -> None -> the conservative 'other' tier (floor 0.20).
    assert _corpus_author(Path("Asset allocation book.md")) is None
