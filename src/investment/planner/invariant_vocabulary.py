"""The vocabulary a new invariant must be written in, as the Worker reads it.

WHY THIS EXISTS (owner, 2026-10-25). Every new invariant the Worker filed since
August ended as a reference note, which nothing measures. It was never shown the
vocabulary the 35-year sweep reads: which signals exist, in what units, which
features and handles. The curator always was (`curator.render_instructions`). On
2026-09-27 it guessed a signal, `cpi_yoy`, that does not exist (the registry
calls it `inflation`). A claim written in this vocabulary is confronted over 35
years the same week, and its record comes back to the Worker in its recent
ideas (`worker_history`): its intuitions get an answer instead of a shelf.

Generated from the same registries and observed ranges that the curator and the
validation gate read (`curator.build_registries`, `curator.signal_ranges`), so
the guide cannot name a signal the gate refuses. The curator's prompt is not
reused: it was tuned on curation runs and speaks of passages and reference
notes, and the Worker has neither.
"""

from investment.db.seed_data import SIGNAL_ALIASES
from investment.db.sqlite import InvestmentDB
from investment.mechanical.invariants import Registries
from investment.worker.curator import build_registries, signal_ranges


async def load_invariant_vocabulary(db: InvestmentDB) -> list[str]:
    """The guide, read off the live registries."""
    return describe_invariant_vocabulary(await build_registries(db), await signal_ranges(db))


def describe_invariant_vocabulary(
    registries: Registries, ranges: dict[str, tuple[float, float]]
) -> list[str]:
    """How to write a `new_invariant` the history can measure, with the live
    signals (each with the range its level actually takes) and handles."""
    signals = [
        f"{alias} ({SIGNAL_ALIASES[alias]}): level {ranges[alias][0]:.2f} .. {ranges[alias][1]:.2f}"
        if alias in ranges
        else f"{alias} ({SIGNAL_ALIASES[alias]})"
        for alias in sorted(registries.signals & set(SIGNAL_ALIASES))
        if alias != "regime"
    ]
    return [
        "HOW TO FILE A new_invariant THE HISTORY CAN MEASURE. Written this way, it is "
        "confronted over 35 years the week you file it, and its record comes back to you. "
        "Written any other way, it becomes a reference note that nothing ever measures. "
        "Its `spec` carries:",
        '  - `condition`: a LIST of predicates, all of which must hold: {"signal": <name>, '
        '"feature": "level" | "speed" | "acceleration", "op": one of < <= > >= == !=, '
        '"value": <number>}.',
        "  - `effect`: ONE object: "
        '{"handle": ..., "metric": "return", "method": ..., "direction": "outperform" | '
        '"underperform"}. A real list and a real object, never JSON written as text.',
        "  `feature`: level is the value; speed its rate of change (rising: speed > 0); "
        "acceleration the change of that rate (rising faster and faster: acceleration > 0). "
        "A level threshold must lie inside the range below, in the series' own units, or the "
        "condition never holds.",
        "  Signals, with the range their level takes in the data:",
        *[f"    {line}" for line in signals],
        "    Any ticker of the allowed universe is a signal too, by its own name.",
        "  `handle` and `method`: asset:<TICKER> or asset-class:<class> with `cross_class` "
        "(against the median of the other classes); strategy:<id> with `cross_strategy` "
        "(against the other strategies); any handle with `absolute` (the sign of its own "
        "return: outperform means positive), except asset-class:cash. The effect is measured "
        "over the 12 weeks after each date the condition holds.",
        f"    classes: {', '.join(sorted(registries.asset_classes))}",
        f"    strategies: {', '.join(sorted(registries.strategies))}",
        "  What cannot be written: a level relative to its own history (a median, a "
        "percentile, a z-score), and an effect that is not a return (growth, inflation, "
        "spreads). Such a claim is not an invariant; keep it in your reading. Never write "
        "the nearest substitute: the history would measure a claim you did not make.",
    ]
