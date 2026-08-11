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

THE VERDICT RULE is PARETO over the four NavMetrics indicators: adopt iff at
least one improves and none degrades. It was "Sortino not degraded AND max
drawdown improved" — the Worker's own words, and the test the 2026-08-07 pair
was adopted under — until measuring the Worker's most repeated critique showed
that test could only ever adopt OVERLAY changes, because the stack's max
drawdown is an overlay property and book selection cannot move it (see `adopt`).
Rule #1 is intact: nothing may get worse.
"""

import dataclasses
import math
from datetime import date
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
    "spread_speed_veto": "SPREAD_SPEED_VETO",
    "spread_speed_wide_trigger": "SPREAD_SPEED_WIDE_TRIGGER",
    "spread_stress_sleeve_gate": "SPREAD_STRESS_SLEEVE_GATE",
}

# What each knob MEANS, in the Worker's terms — the text it reads when deciding
# whether its critique is expressible. Separate from the attribute map because
# they answer different questions, and paired with it by a test: a knob added
# without a description fails, rather than existing in the registry and being
# invisible to the only thing that can name it.
PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "trend_sleeves": "which sleeves the 200d overlay checks",
    "trend_haven": "where a below-trend sleeve is redirected",
    "trend_fallback_haven": "where it goes when the haven is itself below trend",
    "confirm_decisions": "consecutive agreeing decisions before a book change",
    "ma_window_days": "the trend overlay's moving-average window",
    "median_window_days": "the trailing window the signal's medians use",
    "spread_speed_veto": (
        "defer the risk-on wide-spread book while the spread is still widening faster "
        "than this, in spread points per 30 days (null = off, the current rule)"
    ),
    "spread_speed_wide_trigger": (
        "enter the risk-on wide-spread book as soon as the spread widens faster than "
        "this, whatever the level says, same units (null = off)"
    ),
    "spread_stress_sleeve_gate": (
        "send the EQUITY sleeves to the haven whenever the spread is wide and widening "
        "faster than this, without waiting for their own 200d, same units (null = off)"
    ),
}


def describe_testable_parameters() -> str:
    """The knob vocabulary as prompt text, GENERATED from the registry.

    The same promise `market_signal.describe_rule` makes, for the same reason
    and after the same failure. This list was hand-written in
    `skill-read-market-signal.md`, and on 2026-08-09 a knob was added
    (`spread_speed_veto`) that the list did not mention — so the Worker could not
    name the one parameter built specifically to express its most repeated
    critique, and would have gone on filing that critique as unmeasurable prose.

    THIS IS THE LOOP CLOSING, and it is why the list must not be typed by hand:
    a knob added here becomes nameable by the Worker on the very next cycle, the
    revision naming it is measured over 35 years on the spot, and ADR-006 issues
    a verdict — with no human noticing anything. A hand-written list breaks that
    chain at its first link, silently, and the break looks exactly like a Worker
    with nothing to say."""
    width = max(len(name) for name in TESTABLE_PARAMETERS)
    return "\n".join(
        f"    {name:<{width}}  {PARAMETER_DESCRIPTIONS[name]}" for name in TESTABLE_PARAMETERS
    )


# The knobs whose VALUE must name a tradable sleeve, and the ones that must be a
# positive whole number. A model writes these, and until 2026-08-09 nothing
# checked them: `measure_revision` set the module constant and the error
# surfaced as a `KeyError` deep inside a pandas price frame, twice in two days
# (`dynamic_best_of(GLD,IEF)` on 08-08, `SHY` on 08-09). Both degraded cleanly —
# the caller catches and logs "could not be measured" — but the revision then
# carries a traceback instead of a verdict, and the owner reads neither.
_TICKER_KNOBS = frozenset({"trend_haven", "trend_fallback_haven", "trend_sleeves"})
_COUNT_KNOBS = frozenset({"confirm_decisions", "ma_window_days", "median_window_days"})
# A threshold in the spread's own units, so any finite number is expressible —
# including a negative one, which vetoes only while spreads are TIGHTENING and
# is a perfectly good thing to measure and reject.
_FLOAT_KNOBS = frozenset(
    {"spread_speed_veto", "spread_speed_wide_trigger", "spread_stress_sleeve_gate"}
)

# WHAT COUNTS AS "UNCHANGED", and the number is MEASURED, not chosen.
#
# A Pareto verdict has to tell an indicator that MOVED from one that merely got
# there by a different path. Two things move these numbers without the strategy
# changing at all, and both are measured rather than assumed:
#
#   float noise — the velocity veto reached the SAME covid trough by a different
#   arithmetic path: -0.2061245891298571 vs -0.20612458912985732, a delta of
#   -2.2e-16. An exact comparison called that a degradation and refused the
#   revision. Left alone it refuses EVERYTHING, since any change perturbs every
#   indicator at machine epsilon.
#
#   THE GROUND ITSELF — and this is the larger term. Shifting the replay's start
#   date across 1991 changes nothing about the strategy (it is not a different
#   strategy because you began measuring it in March), and moves the indicators
#   by this much over twelve starts, 2026-08-09:
#
#       indicator        spread   stdev
#       sortino           0.71%   0.20%
#       cagr              0.54%   0.15%
#       calmar            0.54%   0.15%
#       max_drawdown      0.00%   0.00%
#
#   Independently corroborated: the 2026-08-03 re-seed moved CAGR by 0.36% with
#   418 IDENTICAL decisions (docs/IMPROVEMENTS.md I-48).
#
# 0.71% is the worst observed spread — Sortino's — so anything smaller is ground
# and not result. It is a NOISE floor, deliberately not a materiality threshold:
# a 1% band was considered and refused because it sits inside the 8-basis-point
# corridor between this floor and the smallest improvement the sweep classifies
# as real (+1.02% of Sortino at ma_window_days=225). Choosing a number in that
# corridor decides one specific case, which is calibrating to an outcome rather
# than measuring noise. Whether a small Sortino loss is worth a large drawdown
# gain is a TRADE-OFF and belongs to the owner, stated as such — not laundered
# through a tolerance.
#
# Verified to change no verdict on the day it shipped: it only stops the system
# ever calling a shift of the ground an improvement.
NOISE_REL_TOL = 0.0071
NOISE_ABS_TOL = 1e-12


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
        elif knob in _FLOAT_KNOBS and not (
            isinstance(value, int | float) and not isinstance(value, bool)
        ):
            bad[knob] = f"expected a number in spread units, got {value!r}"
        elif knob in _COUNT_KNOBS and not (
            isinstance(value, int) and not isinstance(value, bool) and value > 0
        ):
            bad[knob] = f"expected a positive whole number, got {value!r}"
    return bad


def _direction(baseline: float, variant: float) -> int:
    """+1 improved, -1 degraded, 0 unchanged — every indicator being
    higher-is-better (see `RevisionMeasurement.deltas`).

    `math.isclose` rather than `==`, for the reason `NOISE_REL_TOL` records: two
    runs that reach the same trough by different arithmetic differ in the last
    bit, and a verdict that cannot say "unchanged" refuses every revision ever
    proposed."""
    if math.isclose(baseline, variant, rel_tol=NOISE_REL_TOL, abs_tol=NOISE_ABS_TOL):
        return 0
    return 1 if variant > baseline else -1


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
    def deltas(self) -> dict[str, float] | None:
        """variant - baseline, per indicator. EVERY `NavMetrics` field is
        higher-is-better, `max_drawdown` included: it is a negative fraction, so
        -0.18 beats -0.24 and the sign needs no special case.

        `None` if any indicator is missing on either arm — a partial comparison
        is not a comparison."""
        base, var = self.baseline.as_map(), self.variant.as_map()
        if any(base[k] is None or var[k] is None for k in base):
            return None
        return {k: float(var[k]) - float(base[k]) for k in base}  # type: ignore[arg-type]  # guarded

    @property
    def adopt(self) -> bool | None:
        """PARETO: adopt iff at least one indicator improves and NONE degrades.
        `None` when an indicator is missing on either arm — the honest answer,
        not a False that reads as a refusal.

        THE TEST USED TO BE "Sortino not degraded AND max drawdown improved",
        which the proposing Worker wrote and which the 2026-08-07 pair was
        adopted under. Measuring the Worker's own most repeated critique
        (`SPREAD_SPEED_VETO`) is what retired it, on 2026-08-09.

        The veto improved Sortino by 0.071 and CAGR by 0.38pp and was refused,
        because the drawdown did not move — and it could not have: the stack's
        worst drawdown is 2020-03-20, the book in force through the covid crash
        was already the tight one, and the veto only defers a WIDE read. The
        -20.61% belongs to the 200d overlay's latency, not to book selection.
        So "the drawdown must IMPROVE" was structurally an overlay-only filter:
        no book-selection revision could ever pass it, whatever its merit, and
        both revisions ever adopted were overlay changes.

        Pareto keeps the doctrine and drops the artefact. Nothing may get worse
        — rule #1 is intact, and a revision that buys CAGR with drawdown is
        still refused on the spot. What changes is that a revision improving
        return at UNCHANGED risk is now expressible as an adoption instead of
        being rejected without the test ever being able to say why (owner
        decision 2026-08-09)."""
        base, var = self.baseline.as_map(), self.variant.as_map()
        if any(base[k] is None or var[k] is None for k in base):
            return None
        directions = [_direction(float(base[k]), float(var[k])) for k in base]  # type: ignore[arg-type]  # guarded
        if any(d < 0 for d in directions):
            return False
        return any(d > 0 for d in directions)


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


async def measure_revision(
    db: InvestmentDB,
    overrides: dict[str, Any],
    *,
    start: date | None = None,
    end: date | None = None,
) -> RevisionMeasurement:
    """Run the stack twice — as it is, then with `overrides` — and return both.

    `start`/`end` BOUND THE WINDOW, and exist so out-of-sample validation is a
    capability of the measurement rather than a scratchpad hack. A knob that
    "adopts" on the whole 35-year sample and on neither half of it has been
    fitted, not found, and the only way to see that is to measure the halves
    with the same function that measured the whole. Defaults keep
    `run_market_signal`'s own window.

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

    window: dict[str, date] = {}
    if start is not None:
        window["start"] = start
    if end is not None:
        window["end"] = end
    baseline_run = await market_signal.run_market_signal(db, **window)  # type: ignore[arg-type]  # date kwargs
    baseline = await market_signal.stack_metrics(db, baseline_run)

    saved = {attr: getattr(market_signal, attr) for attr in TESTABLE_PARAMETERS.values()}
    try:
        for key, value in overrides.items():
            attr = TESTABLE_PARAMETERS[key]
            current = saved[attr]
            setattr(market_signal, attr, tuple(value) if isinstance(current, tuple) else value)
        variant_run = await market_signal.run_market_signal(db, **window)  # type: ignore[arg-type]  # date kwargs
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
