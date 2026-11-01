"""The figures of a Worker reading that the owner already reads in the digest.

WHY (owner, 2026-11-01: "remove every figure already given elsewhere, the
mechanical ones above all; I want relevant proposals, not leftovers"). The
reading of that morning carried about eighty figures in 680 words, and many
restated the decision block printed just above it. The prompt already said "the
owner already sees the facts; restating them is not a reading", and also asked
that "every number carries its unit and its date": a sentence the model weighs
against another does not hold. So the rule is checked: the reading is compared
with the digest the owner reads (`telegram.digest.owner_view`), and a reading
that repeats a figure is sent back once with the list (`worker.agent.run_worker`).

WHAT COUNTS AS A FIGURE: a number with a decimal point or a percent sign, or an
integer of 13 or more. Dates and years are not figures, small integers are
counts ("three decisions", "6 weeks"), and digits inside a name (T10Y2Y, BAA10Y)
are part of the name. Two figures are the same when their values are equal,
whatever their sign or percent sign: "-0.03" restates "0.03".
"""

import re

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?%?(?![\w.]*\d)")

# Below this an integer is a count, not a figure.
SMALLEST_INTEGER_FIGURE = 13


def figures_in(text: str) -> frozenset[str]:
    """The figures of `text`, each in one canonical spelling."""
    text = _YEAR.sub(" ", _DATE.sub(" ", text))
    found = set()
    for token in _NUMBER.findall(text):
        bare = token.lstrip("+-").rstrip("%")
        if "." not in bare and not token.endswith("%") and int(bare) < SMALLEST_INTEGER_FIGURE:
            continue
        found.add(_canonical(bare))
    return frozenset(found)


def repeated_figures(reading: str, printed: frozenset[str]) -> list[str]:
    """The figures of `reading` that are in `printed`, sorted."""
    return sorted(figures_in(reading) & printed, key=float)


def _canonical(bare: str) -> str:
    value = float(bare)
    return str(int(value)) if value == int(value) else f"{value:.6f}".rstrip("0").rstrip(".")
