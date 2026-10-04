"""The spliced histories, kept on disk beside the database — because the
sources they were built from can disappear, and two already have.

A tradable's stored history is a SPLICE: the ETF's own prices back to its
inception, and before that a longer proxy ratio-chained onto it
(`market/splice.py`, `seed_data.HISTORY_PROXIES`). The seed rebuilds that
splice from the two sources on every run. On 2026-10-04 two proxies no longer
answered — LBMA's gold fixing returned 403 and Yahoo held nothing for `^BCOM` —
so gold before 2004 and commodities before 2006 existed in exactly one place,
the live database, and a seed on an empty one would have started those series
at the ETFs' inception: the 1994, 2000 and 2008 episodes gone for three
sleeves, and with them the 35 years every indicator claims to be measured on.

So the splice is archived once it exists, and the seed reads the archive when a
proxy fails (`seed._seed_market_data`). One CSV per ticker, `ts,level`, on the
SPLICED scale — the proxy's own values are not what is kept, because they are
not what is needed and, for the two that died, no longer available.

NOT IN THE REPOSITORY, deliberately. The repository is public; LBMA's fixing
and Bloomberg's index are licensed data (FRED stopped redistributing the first
for that reason). The archive lives with the database, under the same backups.
"""

import asyncio
import logging
from pathlib import Path

import pandas as pd

from investment.config import Settings
from investment.db.seed_data import HISTORY_PROXIES
from investment.db.sqlite import InvestmentDB

logger = logging.getLogger(__name__)

ARCHIVE_DIRECTORY_NAME = "spliced_history"

# No series archived here has ever moved by half in a day (the worst are the
# 1987 crash at about -20% and silver-era gold at about +13%). A larger jump is
# two constructions in one series — what the failed GLD write of 2026-10-04
# looked like, 1263.50 one day and 44.38 the next — and an archive of THAT
# would turn a repairable database into an unrepairable history.
MAX_PLAUSIBLE_DAILY_MOVE = 0.5


def archive_directory(db_path: Path) -> Path:
    """Beside the database, so that whatever backs one up backs up the other."""
    return db_path.parent / ARCHIVE_DIRECTORY_NAME


def read_spliced_history(directory: Path, ticker: str) -> pd.Series | None:
    """The archived spliced level of `ticker`, ascending — or None if there is
    no archive for it."""
    path = directory / f"{ticker}.csv"
    if not path.is_file():
        return None
    frame = pd.read_csv(path, parse_dates=["ts"])
    return pd.Series(frame["level"].to_numpy(dtype=float), index=pd.DatetimeIndex(frame["ts"]))


def archive_spliced_history(directory: Path, ticker: str, level: pd.Series) -> str | None:
    """Write `level` as the archive of `ticker`. Returns None, or the reason the
    archive on disk was left as it is.

    AN ARCHIVE IS ONLY EVER REPLACED BY A BETTER ONE, since its whole purpose is
    to outlive a source: a series that does not hold together is refused, and
    so is one that reaches less far back than what is already kept."""
    level = level.dropna().sort_index()
    if len(level) < 2:
        return "nothing to archive"
    largest_move = float(level.pct_change().abs().max())
    if largest_move > MAX_PLAUSIBLE_DAILY_MOVE:
        return (
            f"a one-day move of {largest_move:.0%} is not a price move — the series mixes two "
            "scales and is not archived"
        )
    kept = read_spliced_history(directory, ticker)
    if kept is not None and kept.index.min() < level.index.min():
        return (
            f"the archive reaches back to {kept.index.min().date()} and this series only to "
            f"{level.index.min().date()} — archive kept"
        )
    directory.mkdir(parents=True, exist_ok=True)
    # Written beside and renamed: a crash mid-write must not leave half a file
    # where the only copy of a dead proxy's history used to be.
    partial = directory / f"{ticker}.csv.partial"
    pd.DataFrame(
        {"ts": pd.DatetimeIndex(level.index).strftime("%Y-%m-%d"), "level": level.to_numpy()}
    ).to_csv(partial, index=False)
    partial.replace(directory / f"{ticker}.csv")
    return None


async def archive_stored_histories(db: InvestmentDB, directory: Path) -> dict[str, str]:
    """Archive what the DATABASE holds for every spliced ticker — the route for
    a proxy that no longer answers, whose splice the seed can never rebuild and
    so never re-archives. `{ticker: "archived from <date>" | <refusal>}`."""
    outcome: dict[str, str] = {}
    for ticker in HISTORY_PROXIES:
        rows = await db.query(
            "SELECT ts, level FROM market_data WHERE ticker = :t AND level IS NOT NULL ORDER BY ts",
            t=ticker,
        )
        level = pd.Series(
            [float(r["level"]) for r in rows],
            index=pd.to_datetime([str(r["ts"]) for r in rows]),
            dtype=float,
        )
        refusal = archive_spliced_history(directory, ticker, level)
        outcome[ticker] = refusal or f"archived from {level.index.min().date()}"
    return outcome


def main() -> None:
    # pydantic-settings populates required fields from .env at runtime; mypy
    # can't see that (CLAUDE.md "Dev standards" mypy rule), as in seed.main.
    settings = Settings()  # type: ignore[call-arg]

    async def run() -> dict[str, str]:
        db = InvestmentDB(settings.db_path)
        try:
            return await archive_stored_histories(db, archive_directory(settings.db_path))
        finally:
            await db.close()

    for ticker, outcome in asyncio.run(run()).items():
        print(f"{ticker}: {outcome}")


if __name__ == "__main__":
    main()
