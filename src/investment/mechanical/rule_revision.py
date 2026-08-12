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
import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

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
    "stress_gated_sleeves": "STRESS_GATED_SLEEVES",
}

# What each knob MEANS, in the Worker's terms — the text it reads when deciding
# whether its critique is expressible. Separate from the attribute map because
# they answer different questions, and paired with it by a test: a knob added
# without a description fails, rather than existing in the registry and being
# invisible to the only thing that can name it.
PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "trend_sleeves": "which sleeves the trend overlay checks",
    "trend_haven": "where a below-trend sleeve is redirected",
    "trend_fallback_haven": "where it goes when the haven is itself below trend",
    "confirm_decisions": "consecutive agreeing decisions before a book change",
    "ma_window_days": "the trend overlay's moving-average window",
    "median_window_days": "the trailing window the signal's medians use",
    # The lookback is INTERPOLATED, not typed: these three descriptions state the
    # units a model must write its candidate in, so a hand-typed "30 days" that
    # outlived `SPREAD_SPEED_LOOKBACK_DAYS` would have the Worker proposing
    # numbers on a scale the walk does not use.
    "spread_speed_veto": (
        "defer the risk-on wide-spread book while the spread is still widening faster "
        f"than this, in spread points per {market_signal.SPREAD_SPEED_LOOKBACK_DAYS} days "
        "(null = off, the current rule)"
    ),
    "spread_speed_wide_trigger": (
        "enter the risk-on wide-spread book as soon as the spread widens faster than "
        "this, whatever the level says, same units (null = off)"
    ),
    "spread_stress_sleeve_gate": (
        "send the gated sleeves to the haven whenever the spread is wide and widening "
        "faster than this, without waiting for their own trend read, same units (null = off)"
    ),
    "stress_gated_sleeves": (
        "which sleeves that stress gate empties — equities by default, and credit "
        "sleeves are the obvious candidate to add"
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
_TICKER_KNOBS = frozenset(
    {"trend_haven", "trend_fallback_haven", "trend_sleeves", "stress_gated_sleeves"}
)
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

# The window `run_market_signal` defaults to — named there, not here. It was a
# COPY of that pair of literals, which is the drift this project keeps finding:
# two statements of one fact with nothing making them agree.
FULL_WINDOW = market_signal.PINNED_WINDOW


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
    # What the RUN can serve, not what the books happen to hold — a haven is
    # where the overlay flees to and need not be a sleeve. The two were the same
    # set until 2026-08-11, which is why SHY (active, full 1991 history) came
    # back as "not a tradable sleeve with a price series".
    known = set(market_signal.LOADABLE_TICKERS)
    for knob, value in overrides.items():
        if knob in _TICKER_KNOBS:
            names = list(value) if isinstance(value, list | tuple) else [value]
            allowed = known | (
                {market_signal.TREND_FALLBACK_HAVEN} if knob == "trend_fallback_haven" else set()
            )
            unknown = [n for n in names if not isinstance(n, str) or n not in allowed]
            if unknown:
                bad[knob] = (
                    f"outside the instruments this run loads prices for "
                    f"({', '.join(sorted(known))}): {unknown}"
                )
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
        -20.61% belongs to the trend overlay's latency, not to book selection.
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
    title: str | None = None,
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
    baseline = await market_signal.stack_metrics(db, baseline_run, until=end)

    saved = {attr: getattr(market_signal, attr) for attr in TESTABLE_PARAMETERS.values()}
    try:
        for key, value in overrides.items():
            attr = TESTABLE_PARAMETERS[key]
            current = saved[attr]
            setattr(market_signal, attr, tuple(value) if isinstance(current, tuple) else value)
        variant_run = await market_signal.run_market_signal(db, **window)  # type: ignore[arg-type]  # date kwargs
        variant = await market_signal.stack_metrics(db, variant_run, until=end)
    finally:
        for attr, value in saved.items():
            setattr(market_signal, attr, value)

    measurement = RevisionMeasurement(
        overrides=dict(overrides),
        baseline=baseline,
        variant=variant,
        baseline_turnover=baseline_run.turnover,
        variant_turnover=variant_run.turnover,
    )
    # RECORDED, because a measurement whose result is thrown away is measured
    # again. See `revision_measurement` in the schema: the verdict used to reach
    # a log line and an EventLog row in whatever database was open — a THROWAWAY
    # snapshot on every replayed date — so "add VCIT to the overlay" was
    # re-derived three times and the Worker kept proposing it.
    await _record_measurement(
        db,
        measurement,
        priced=baseline_run.nav.dropna().loc[: pd.Timestamp(end)] if end else baseline_run.nav,
        title=title,
    )
    return measurement


def overrides_key(overrides: Mapping[str, Any]) -> str:
    """The identity of an EXPERIMENT: the canonical JSON of its override set.

    Not the title. Five differently worded haven proposals resolving to the same
    constants are one experiment — established when the M8b sweep was
    deduplicated on the override set rather than on the wording, which turned
    nine proposals into five measurements."""
    return json.dumps({k: overrides[k] for k in sorted(overrides)}, sort_keys=True, default=str)


async def _record_measurement(
    db: InvestmentDB,
    measurement: RevisionMeasurement,
    *,
    priced: pd.Series,
    title: str | None,
) -> None:
    """UPSERT one verdict, keyed on (override set, window).

    THE WINDOW IS READ OFF THE NAV THAT WAS PRICED, never off the arguments,
    and the difference is not pedantic. `measure_revision` runs against whatever
    database it is handed, and on every replayed decision date that database is
    a SNAPSHOT bounded at t (db/as_of_snapshot.py) — so a call with no explicit
    window measures 1991..t, not 1991..2026.

    Measured 2026-08-11: the Worker proposed lowering both trajectory knobs to
    0.12 at the 2008-09-02 date and the in-cycle verdict was ADOPT, which was
    honest for the seventeen years knowable then and false for the full sample,
    where the same change rejects on all three windows. Recording it under the
    default full window would have written that falsehood into the one place
    built to stop questions being re-asked."""
    verdict = {True: "adopt", False: "reject", None: "unmeasurable"}[measurement.adopt]
    deltas = measurement.deltas or {}
    async with db.transaction() as tx:
        await tx.command(
            "INSERT INTO revision_measurement (overrides_key, window_start, window_end, "
            "overrides, title, verdict, sortino_delta, cagr_delta, drawdown_delta, measured_at) "
            "VALUES (:k, :ws, :we, :ov, :t, :v, :sd, :cd, :dd, :now) "
            "ON CONFLICT(overrides_key, window_start, window_end) DO UPDATE SET "
            "verdict=excluded.verdict, sortino_delta=excluded.sortino_delta, "
            "cagr_delta=excluded.cagr_delta, drawdown_delta=excluded.drawdown_delta, "
            "title=COALESCE(excluded.title, revision_measurement.title), "
            "measured_at=excluded.measured_at",
            k=overrides_key(measurement.overrides),
            ws=str(priced.index[0].date()) if len(priced) else FULL_WINDOW[0].isoformat(),
            we=str(priced.index[-1].date()) if len(priced) else FULL_WINDOW[1].isoformat(),
            ov=json.dumps(measurement.overrides, default=str),
            t=title,
            v=verdict,
            sd=deltas.get("sortino"),
            cd=deltas.get("cagr"),
            dd=deltas.get("max_drawdown"),
            now=datetime.now(UTC).isoformat(),
        )


async def measured_verdicts(db: InvestmentDB) -> list[dict[str, Any]]:
    """One verdict per experiment — the WIDEST window measured for it — with
    that window, so the Worker is told what the answer covers.

    NOT a filter on the full-window constants, which is what the first version
    did and why it surfaced one verdict of seven. The recorded window is read
    off the priced NAV, so it is the data's real first and last day
    (1991-10-29, not 1991-01-01), and an equality test against a constant
    matches almost nothing. Same defect as everything else this week: the
    identity written and the identity queried were not the same identity.

    Widest rather than newest: a half-sample verdict and a full-sample verdict
    answer different questions, and the broader one is the one a proposer needs
    to hear first."""
    rows = await db.query(
        "SELECT overrides, title, verdict, sortino_delta, cagr_delta, drawdown_delta, "
        "       window_start, window_end, "
        "       julianday(window_end) - julianday(window_start) AS span "
        "FROM revision_measurement ORDER BY span DESC, measured_at DESC"
    )
    # AGGREGATED ACROSS WINDOWS, because a full-sample verdict alone is the
    # trap this project keeps walking into: TLT-as-haven adopts over 35 years
    # and fails BOTH halves, the 175- and 225-day windows each fail the half
    # they are not fitted to. Reporting "ADOPT" for those would hand the Worker
    # three sample artefacts to propose.
    #
    # So an experiment is ADOPT only if every window measured for it adopts;
    # disagreement is reported as MIXED, which is the sample-artefact signal
    # itself and more useful than either verdict alone. An experiment measured
    # on one window says so — no out-of-sample evidence is not the same as
    # out-of-sample agreement.
    by_experiment: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_experiment.setdefault(str(row["overrides"]), []).append(dict(row))

    out: list[dict[str, Any]] = []
    for overrides, measured in by_experiment.items():
        verdicts = {str(m["verdict"]) for m in measured}
        widest = measured[0]  # rows arrive span DESC
        out.append(
            {
                **widest,
                "overrides": overrides,
                "verdict": verdicts.pop() if len(verdicts) == 1 else "mixed",
                "windows": len(measured),
            }
        )
    return out[:20]


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
