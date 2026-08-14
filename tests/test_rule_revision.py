"""Tests for `mechanical/rule_revision.py` — measuring a proposed rule change.

The M8b runs produced 22 innovations across two passes, most of them rule
revisions carrying no target allocation: no candidate portfolio, no NAV, no
FAVORS, and probation closes them as unmeasurable. This is the path that gives a
revision naming a KNOWN knob an answer in one pass instead of none ever.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal, rule_revision
from investment.mechanical.replay import NavMetrics


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "rev.db")
    yield conn
    await conn.close()


def _measurement(
    *,
    sortino: tuple[float, float],
    drawdown: tuple[float, float],
    cagr: tuple[float, float] = (0.10, 0.10),
    calmar: tuple[float, float] = (1.0, 1.0),
) -> rule_revision.RevisionMeasurement:
    """A measurement with every indicator explicit, because the verdict is now
    PARETO over all four — a helper that pinned two of them and left the others
    equal would only ever exercise a quarter of the rule."""

    def metrics(c: float, s: float, k: float, d: float) -> NavMetrics:
        return NavMetrics(cagr=c, sortino=s, calmar=k, max_drawdown=d)

    return rule_revision.RevisionMeasurement(
        overrides={"confirm_decisions": 4},
        baseline=metrics(cagr[0], sortino[0], calmar[0], drawdown[0]),
        variant=metrics(cagr[1], sortino[1], calmar[1], drawdown[1]),
        baseline_turnover=42.0,
        variant_turnover=50.0,
    )


def test_only_the_registry_knobs_are_extracted() -> None:
    spec: dict[str, Any] = {
        "proposed_rule": "prose the model wrote, deliberately not parsed",
        "parameters": {"trend_sleeves": ["SPY", "GLD", "IWN"], "invent_a_new_signal": "HY_OAS"},
    }
    assert rule_revision.extract_overrides(spec) == {"trend_sleeves": ["SPY", "GLD", "IWN"]}
    # ...and what it cannot move is REPORTED, not silently dropped: measuring
    # half a revision and calling the result evidence is worse than waiting.
    assert rule_revision.unknown_parameters(spec) == ["invent_a_new_signal"]


def test_a_prose_only_revision_is_not_mechanically_testable() -> None:
    """The prose is never parsed. A `proposed_rule` sentence is LLM-authored, and
    guessing constants out of it means measuring a rule nobody proposed."""
    assert rule_revision.extract_overrides({"proposed_rule": "check the haven too"}) is None
    assert rule_revision.extract_overrides({"parameters": {}}) is None
    assert rule_revision.extract_overrides({"parameters": "not a dict"}) is None


def test_the_verdict_is_pareto_over_every_indicator() -> None:
    """Owner decision 2026-08-09: adopt iff at least one indicator improves and
    NONE degrades.

    It was "Sortino not degraded AND max drawdown improved" — the Worker's own
    words, and the test the 2026-08-07 overlay pair was adopted under — until
    measuring the Worker's most repeated critique showed that rule could only
    ever adopt OVERLAY changes. The stack's worst drawdown is the covid crash,
    set by the 200d overlay's latency; book selection cannot move it, so
    requiring the drawdown to IMPROVE refused every book-selection revision
    without the test being able to say why.

    Rule #1 survives intact — nothing may get worse."""
    # the shape of the adopted 2026-08-07 change: several improve, none degrade
    assert _measurement(sortino=(1.09, 1.17), drawdown=(-0.238, -0.206)).verdict == "adopt"
    # drawdown improves but Sortino degrades -> Pareto is not a trade, and since
    # 2026-08-13 it says WHICH kind of refusal that is instead of one word for two.
    assert _measurement(sortino=(1.17, 1.09), drawdown=(-0.238, -0.206)).verdict == "trade-off"
    # THE CASE THAT CHANGED, and the reason it did: return improves at UNCHANGED
    # risk. The old rule refused this; it is the velocity veto's exact shape.
    assert (
        _measurement(
            sortino=(1.173, 1.244),
            drawdown=(-0.2061, -0.2061),
            cagr=(0.1072, 0.1110),
            calmar=(0.52, 0.54),
        ).verdict
        == "adopt"
    )
    # ...and buying that return with drawdown is still refused on the spot —
    # as a trade-off, which adopts nothing but no longer closes the strategy.
    assert (
        _measurement(sortino=(1.09, 1.30), drawdown=(-0.206, -0.238), cagr=(0.10, 0.13)).verdict
        == "trade-off"
    )
    # a change that moves nothing at all is not an adoption, and not a trade
    assert _measurement(sortino=(1.09, 1.09), drawdown=(-0.238, -0.238)).verdict == "reject"
    # FLOAT NOISE IS NOT A DEGRADATION, and this is the exact measurement that
    # forced the tolerance: the velocity veto reached the SAME trough by a
    # different arithmetic path, -0.2061245891298571 vs -0.20612458912985732,
    # and an exact comparison refused the revision on -2.2e-16. Unfixed, the
    # rule refuses everything — every change perturbs every indicator at
    # machine epsilon.
    assert (
        _measurement(
            sortino=(1.173, 1.244),
            drawdown=(-0.2061245891298571, -0.20612458912985732),
            cagr=(0.1072, 0.1110),
        ).verdict
        == "adopt"
    )
    # ...and a real move of the same indicator is still caught.
    assert (
        _measurement(
            sortino=(1.173, 1.244), drawdown=(-0.2061, -0.2200), cagr=(0.1072, 0.1110)
        ).verdict
        == "trade-off"
    )


def test_the_noise_floor_is_the_measured_ground_movement() -> None:
    """0.71%: the worst spread any indicator shows under a perturbation that
    changes nothing about the strategy — twelve replay start dates across 1991,
    measured 2026-08-09. Sortino moves 0.71%, CAGR and Calmar 0.54%, the max
    drawdown not at all.

    A NOISE floor, not a materiality threshold. The distinction is load-bearing:
    a 1% band was considered and refused because it lands inside the
    8-basis-point corridor between this floor and the smallest improvement the
    sweep calls real (+1.02% of Sortino at a 225-day window) — and a number
    chosen in that corridor decides one specific case rather than measuring
    anything. Trade-offs belong to the owner, stated as trade-offs."""
    assert rule_revision.NOISE_REL_TOL == 0.0071

    # Under the floor: ground, not result. Nothing else moves -> no adoption.
    assert _measurement(sortino=(1.1725, 1.1760), drawdown=(-0.2061, -0.2061)).verdict == "reject"
    # Over it: a real degradation, however small. This is the 125-day overlay's
    # exact shape — 0.94% of Sortino for 2.75pp of drawdown — refused because it
    # is a TRADE-OFF, which is a different answer from "too small to see".
    # THE DOCTRINE CASE, named at last: 0.94% of Sortino for 2.75pp of drawdown
    # is an EXCHANGE, and since 2026-08-13 the verdict says so and the owner is
    # told. It still adopts nothing.
    tradeoff = _measurement(sortino=(1.173, 1.162), drawdown=(-0.2061, -0.1786))
    assert tradeoff.verdict == "trade-off"
    assert tradeoff.traded is not None and "max_drawdown" in tradeoff.traded
    # a single indicator improving, alone, is enough when nothing else moves
    assert _measurement(sortino=(1.09, 1.09), drawdown=(-0.238, -0.206)).verdict == "adopt"
    # and CALMAR counts like the rest — it is not a derived afterthought here
    assert (
        _measurement(sortino=(1.09, 1.09), drawdown=(-0.238, -0.238), calmar=(1.0, 0.9)).verdict
        == "reject"
    )


def test_deltas_state_the_improvement_direction() -> None:
    m = _measurement(sortino=(1.09, 1.17), drawdown=(-0.238, -0.206))
    assert m.sortino_delta == pytest.approx(0.08)
    # POSITIVE means shallower — both figures are negative fractions, so the
    # sign is worth pinning rather than leaving each caller to derive it.
    assert m.drawdown_delta == pytest.approx(0.032)
    assert "ADOPT" in rule_revision.render(m)


def test_a_missing_metric_is_unmeasurable_not_a_rejection() -> None:
    m = rule_revision.RevisionMeasurement(
        overrides={},
        baseline=NavMetrics(cagr=None, sortino=None, calmar=None, max_drawdown=None),
        variant=NavMetrics(cagr=0.1, sortino=1.0, calmar=None, max_drawdown=-0.2),
        baseline_turnover=0.0,
        variant_turnover=0.0,
    )
    assert m.verdict == "unmeasurable"  # not False — nothing was compared
    assert "unmeasurable" in rule_revision.render(m)


def test_every_registered_knob_exists_on_the_module_it_overrides() -> None:
    """The registry names module attributes by string. A renamed constant would
    otherwise fail at measurement time, inside a run that costs 35 years of
    backtest — and read as a broken revision rather than a broken registry."""
    for attr in rule_revision.TESTABLE_PARAMETERS.values():
        assert hasattr(market_signal, attr), attr


def test_every_knob_is_described_to_the_only_thing_that_can_name_it() -> None:
    """A knob in the registry and absent from the vocabulary is invisible: the
    Worker cannot name what it is not told exists, so the revision arrives as
    unmeasurable prose and the 35-year verdict never happens.

    That is not hypothetical — `spread_speed_veto` shipped on 2026-08-09 into a
    hand-written list that did not mention it, one day after the same defect was
    fixed in `describe_rule`. Pairing the two dicts by test is what makes the
    next knob impossible to add halfway."""
    assert set(rule_revision.PARAMETER_DESCRIPTIONS) == set(rule_revision.TESTABLE_PARAMETERS)
    assert all(d.strip() for d in rule_revision.PARAMETER_DESCRIPTIONS.values())


def test_the_worker_is_told_the_current_vocabulary_not_a_frozen_copy() -> None:
    """THE LOOP THIS CLOSES (owner, 2026-08-11): a knob added here must become
    nameable by the Worker on the next cycle, so a recurring critique gets
    measured with no human noticing anything. A markdown file listing the knobs
    by hand breaks that chain at its first link.

    Asserted through `load_skills`, the real path, so a placeholder that stops
    being interpolated fails here rather than in a paid run."""
    from investment.worker.agent import load_skills

    skills = load_skills()
    assert "{TESTABLE_PARAMETERS}" not in skills  # interpolated, not literal
    for name in rule_revision.TESTABLE_PARAMETERS:
        assert name in skills, f"{name} is testable but the Worker is never told about it"


async def test_a_measurement_is_recorded_under_the_window_it_actually_priced(
    db: InvestmentDB,
) -> None:
    """A measurement whose result is thrown away is measured again — the
    rejection of "add VCIT to the trend overlay" was re-derived three times, and
    the Worker went on proposing it because nothing could show it the question
    was settled.

    THE WINDOW COMES OFF THE PRICED NAV, never off the arguments, and that is
    the load-bearing half. `measure_revision` runs against whatever database it
    is handed, and on every replayed decision date that is a SNAPSHOT bounded at
    t — so a call with no explicit window measures 1991..t. Measured 2026-08-11:
    the Worker's "lower both knobs to 0.12" was ADOPTED in-cycle at 2008-09-02,
    honest for the seventeen years knowable then and false on the full sample,
    where it rejects on all three windows. Recorded under the default window it
    would have written that falsehood into the ledger built to stop questions
    being re-asked."""
    priced = pd.Series([100.0, 101.0], index=pd.to_datetime(["1991-01-02", "2008-09-02"]))
    measurement = rule_revision.RevisionMeasurement(
        overrides={"spread_speed_veto": 0.12},
        baseline=NavMetrics(cagr=0.10, sortino=1.0, calmar=0.5, max_drawdown=-0.20),
        variant=NavMetrics(cagr=0.11, sortino=1.1, calmar=0.5, max_drawdown=-0.20),
        baseline_turnover=60.0,
        variant_turnover=62.0,
    )

    await rule_revision._record_measurement(db, measurement, priced=priced, title="worker said so")

    row = (await db.query("SELECT * FROM revision_measurement"))[0]
    assert row["window_start"] == "1991-01-02"
    assert row["window_end"] == "2008-09-02"  # NOT the 2026 default
    assert row["verdict"] == "adopt"
    assert row["title"] == "worker said so"

    # Re-measuring the same experiment over the same window UPDATES rather than
    # duplicating; a different window is a different row, because the
    # out-of-sample split depends on holding both.
    await rule_revision._record_measurement(db, measurement, priced=priced, title=None)
    halves = pd.Series([100.0, 101.0], index=pd.to_datetime(["1991-01-02", "2026-07-01"]))
    await rule_revision._record_measurement(db, measurement, priced=halves, title=None)
    assert len(await db.query("SELECT 1 FROM revision_measurement")) == 2
    # the title survives an update that carries none
    kept = await db.query("SELECT title FROM revision_measurement WHERE window_end='2008-09-02'")
    assert kept[0]["title"] == "worker said so"


def test_the_experiment_identity_is_its_override_set_not_its_wording() -> None:
    """Five differently worded haven proposals resolving to the same constants
    are ONE experiment — established when the M8b sweep was deduplicated on the
    override set, which turned nine proposals into five measurements."""
    a = rule_revision.overrides_key({"trend_haven": "GLD", "trend_fallback_haven": "IEF"})
    b = rule_revision.overrides_key({"trend_fallback_haven": "IEF", "trend_haven": "GLD"})
    assert a == b  # key order is not identity
    assert a != rule_revision.overrides_key({"trend_haven": "GLD"})
