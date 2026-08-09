"""Measure a proposed RULE revision of the market-signal stack over 35 years.

WHY THIS EXISTS. The M8b runs produced 22 innovations across two passes, and
the great majority were `strategy_revision` proposals that change a RULE and
carry no target allocation. `writeback._commit_candidate_portfolio` needs an
allocation to build a NAV, FAVORS needs a NAV, and probation judges on FAVORS —
so a rule revision had no path to evidence at all. It was born `proposed`,
waited out its windows unmeasurable, and was closed. **The most reproducible
output of the screen was untestable by the machine that produced it.**

It did not have to be. The two revisions adopted on 2026-08-07 (IWN under the
overlay, the haven trend-checked) were measured in minutes, by running the same
`walk_decisions` twice with one constant changed. Every rule revision that only
moves a KNOWN KNOB is testable the same way; what was missing is a list of the
knobs and a function that runs the comparison.

WHAT IS NOT TESTABLE HERE, and the boundary is honest: a revision that needs new
CODE (a new signal, a different classifier shape) is outside this, and the
registry saying so is itself useful — "we cannot measure this yet" is a better
verdict than an unmeasurable strategy quietly ageing out.

THE VERDICT RULE is the one the proposing Worker wrote itself, repeatedly and
unprompted: adopt only if Sortino does not degrade AND max drawdown improves.
That asymmetry is the system's own doctrine (rule #1: don't lose) expressed as
an acceptance test, and it is the one under which the 2026-08-07 pair was
adopted.
"""

import dataclasses
from typing import Any

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal
from investment.mechanical.replay import NavMetrics

# The knobs a revision may move and still be measured mechanically. Name ->
# (module attribute, coercer). Anything outside this set needs code, and
# `extract_overrides` says so rather than guessing.
#
# Kept deliberately SMALL. Every entry is a constant `walk_decisions` reads, so
# overriding it exercises the real decision path rather than a parallel model —
# the same discipline `agentic_replay` follows by calling `run_decision_cycle`
# instead of reimplementing it.
TESTABLE_PARAMETERS: dict[str, str] = {
    "trend_sleeves": "TREND_SLEEVES",
    "trend_haven": "TREND_HAVEN",
    "trend_fallback_haven": "TREND_FALLBACK_HAVEN",
    "confirm_decisions": "CONFIRM_DECISIONS",
    "ma_window_days": "MA_WINDOW_DAYS",
    "median_window_days": "MEDIAN_WINDOW_DAYS",
}


# The knobs whose VALUE must name a tradable sleeve, and the ones that must be a
# positive whole number. A model writes these, and until 2026-08-09 nothing
# checked them: `measure_revision` set the module constant and the error
# surfaced as a `KeyError` deep inside a pandas price frame, twice in two days
# (`dynamic_best_of(GLD,IEF)` on 08-08, `SHY` on 08-09). Both degraded cleanly —
# the caller catches and logs "could not be measured" — but the revision then
# carries a traceback instead of a verdict, and the owner reads neither.
_TICKER_KNOBS = frozenset({"trend_haven", "trend_fallback_haven", "trend_sleeves"})
_COUNT_KNOBS = frozenset({"confirm_decisions", "ma_window_days", "median_window_days"})


def untestable_values(overrides: dict[str, Any]) -> dict[str, str]:
    """`{knob: why}` for every override this registry cannot apply, empty if all
    are applicable.

    A knob NAMED correctly can still carry a value the walk cannot use, and the
    two failures are different answers: an unknown knob means "the registry does
    not reach that", an unusable value means "the proposal is not expressible".
    Both belong in the verdict; neither belongs in a traceback.

    `cash` is legal as the FALLBACK haven and nowhere else, which is one notch
    narrower than the first version of this check allowed — and the measurement
    sweep it exists to protect caught it the same hour: `trend_haven: cash`
    passed validation and then raised `KeyError: 'cash'` inside the price frame.
    The primary haven is TREND-CHECKED, so it needs a series; the fallback is
    where the overlay goes precisely because cash cannot fall."""
    bad: dict[str, str] = {}
    known = set(market_signal.STACK_TICKERS)
    for knob, value in overrides.items():
        if knob in _TICKER_KNOBS:
            names = list(value) if isinstance(value, list | tuple) else [value]
            allowed = known | (
                {market_signal.TREND_FALLBACK_HAVEN} if knob == "trend_fallback_haven" else set()
            )
            unknown = [n for n in names if not isinstance(n, str) or n not in allowed]
            if unknown:
                bad[knob] = f"not a tradable sleeve with a price series: {unknown}"
        elif knob in _COUNT_KNOBS and not (
            isinstance(value, int) and not isinstance(value, bool) and value > 0
        ):
            bad[knob] = f"expected a positive whole number, got {value!r}"
    return bad


@dataclasses.dataclass(frozen=True)
class RevisionMeasurement:
    """Baseline and variant measured in ONE process on ONE data vintage.

    Both, always. Comparing a variant against a figure pinned on another day is
    what I-48 is about: the seed's backfill start rolls, Yahoo restates adjusted
    closes, and a 0.4pp 'improvement' can be the ground moving. The baseline is
    re-measured every time so the delta is the only thing that varies."""

    overrides: dict[str, Any]
    baseline: NavMetrics
    variant: NavMetrics
    baseline_turnover: float
    variant_turnover: float

    @property
    def sortino_delta(self) -> float | None:
        if self.baseline.sortino is None or self.variant.sortino is None:
            return None
        return self.variant.sortino - self.baseline.sortino

    @property
    def drawdown_delta(self) -> float | None:
        """POSITIVE means the drawdown got shallower — the improvement direction.
        Both are negative fractions, so the sign flip is worth stating rather
        than leaving every caller to work it out."""
        if self.baseline.max_drawdown is None or self.variant.max_drawdown is None:
            return None
        return self.variant.max_drawdown - self.baseline.max_drawdown

    @property
    def adopt(self) -> bool | None:
        """The Worker's own acceptance test: Sortino not degraded AND drawdown
        improved. `None` when either arm has no metric to compare — the honest
        answer, not a False that reads as a refusal."""
        s, d = self.sortino_delta, self.drawdown_delta
        if s is None or d is None:
            return None
        return s >= 0.0 and d > 0.0


def extract_overrides(spec: dict[str, Any]) -> dict[str, Any] | None:
    """The testable parameter changes a revision spec proposes, or None.

    Reads ONE structured field, `spec["parameters"]`, and never the prose. A
    revision's `proposed_rule` is a sentence written by a language model; parsing
    it into constants would be guessing, and guessing wrong here means measuring
    a rule nobody proposed and reporting the result as evidence.

    So the contract is on the proposer: name the knobs, in a dict, or accept that
    the claim is not mechanically testable. `worker/skills/skill-read-market-
    signal.md` tells it which knobs exist — the same pattern as the queryable
    schema and the citable-invariant list, and for the same reason: a constraint
    the model is told is a constraint it can satisfy.

    Returns None (not an empty dict) when nothing testable is named, so the
    caller can tell "proposes no parameter change" from "proposes an empty one"."""
    raw = spec.get("parameters")
    if not isinstance(raw, dict):
        return None
    overrides = {k: v for k, v in raw.items() if k in TESTABLE_PARAMETERS}
    return overrides or None


def unknown_parameters(spec: dict[str, Any]) -> list[str]:
    """Named parameters this registry cannot move — the reason a revision is
    only PARTLY testable, which the verdict must carry rather than silently
    measuring the half it understands."""
    raw = spec.get("parameters")
    if not isinstance(raw, dict):
        return []
    return sorted(k for k in raw if k not in TESTABLE_PARAMETERS)


async def measure_revision(db: InvestmentDB, overrides: dict[str, Any]) -> RevisionMeasurement:
    """Run the stack twice — as it is, then with `overrides` — and return both.

    The overrides are applied by SETTING THE MODULE CONSTANTS and restoring them
    in a `finally`. That is blunt, and the alternative is worse: threading six
    parameters through `run_market_signal`, `walk_decisions` and
    `apply_trend_overlay` would add a knob-passing seam to the live decision path
    for the sole benefit of an offline measurement, and the live path is the one
    thing this module must not perturb. Single-process, single-writer, no
    concurrency (ADR-004) — the restore is the whole guarantee needed."""
    unusable = untestable_values(overrides)
    if unusable:
        raise ValueError(f"revision names values the walk cannot apply: {unusable}")

    baseline_run = await market_signal.run_market_signal(db)
    baseline = await market_signal.stack_metrics(db, baseline_run)

    saved = {attr: getattr(market_signal, attr) for attr in TESTABLE_PARAMETERS.values()}
    try:
        for key, value in overrides.items():
            attr = TESTABLE_PARAMETERS[key]
            current = saved[attr]
            setattr(market_signal, attr, tuple(value) if isinstance(current, tuple) else value)
        variant_run = await market_signal.run_market_signal(db)
        variant = await market_signal.stack_metrics(db, variant_run)
    finally:
        for attr, value in saved.items():
            setattr(market_signal, attr, value)

    return RevisionMeasurement(
        overrides=dict(overrides),
        baseline=baseline,
        variant=variant,
        baseline_turnover=baseline_run.turnover,
        variant_turnover=variant_run.turnover,
    )


def render(measurement: RevisionMeasurement) -> str:
    """The comparison as the owner reads it, both arms and both deltas."""

    def line(label: str, m: NavMetrics, turnover: float) -> str:
        cagr = "n/a" if m.cagr is None else f"{m.cagr * 100:+.2f}%"
        sortino = "n/a" if m.sortino is None else f"{m.sortino:.2f}"
        dd = "n/a" if m.max_drawdown is None else f"{m.max_drawdown * 100:.2f}%"
        return f"  {label:<10}{cagr:>9}{sortino:>9}{dd:>10}{turnover:>10.1f}"

    verdict = {True: "ADOPT", False: "reject", None: "unmeasurable"}[measurement.adopt]
    return "\n".join(
        [
            f"revision: {measurement.overrides}",
            f"  {'':<10}{'CAGR':>9}{'Sortino':>9}{'maxDD':>10}{'turnover':>10}",
            line("baseline", measurement.baseline, measurement.baseline_turnover),
            line("variant", measurement.variant, measurement.variant_turnover),
            f"  verdict: {verdict}  "
            f"(sortino {measurement.sortino_delta:+.3f}, "
            f"drawdown {(measurement.drawdown_delta or 0.0) * 100:+.2f}pp)"
            if measurement.adopt is not None
            else "  verdict: unmeasurable",
        ]
    )
