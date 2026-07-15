"""M5 unit tests (docs/MILESTONES.md M5 Definition of Verified) — pure
functions in `mechanical/scenarios.py` only, no DB."""

import pandas as pd
import pytest

from investment.mechanical import scenarios


def test_parse_numeric_trigger_resolves_alias_and_op() -> None:
    assert scenarios.parse_numeric_trigger("CPI_YOY < 2.5") == ("CPIAUCSL", "<", 2.5)
    assert scenarios.parse_numeric_trigger("^VIX > 25") == ("^VIX", ">", 25.0)
    assert scenarios.parse_numeric_trigger("GROWTH_COMPOSITE >= 102") == (
        "GROWTH_COMPOSITE",
        ">=",
        102.0,
    )


def test_parse_numeric_trigger_unparseable_returns_none() -> None:
    assert scenarios.parse_numeric_trigger("Fed dovish") is None
    assert scenarios.parse_numeric_trigger("CPI_YOY 2.5-3.5") is None  # a range, no single op
    assert scenarios.parse_numeric_trigger("SPY 90d return > 0") is None  # multi-word signal name


def test_parse_trigger_conjunction_splits_on_and() -> None:
    result = scenarios.parse_trigger_conjunction("CPI_YOY > 4 AND GROWTH_COMPOSITE < 98")
    assert result == [("CPIAUCSL", ">", 4.0), ("GROWTH_COMPOSITE", "<", 98.0)]


def test_parse_trigger_conjunction_whole_string_unparseable_if_any_conjunct_fails() -> None:
    assert scenarios.parse_trigger_conjunction("^VIX > 25 AND Fed dovish") is None


def test_evaluate_trigger_series_ands_within_one_string() -> None:
    """One trigger STRING is a conjunction: "CPI_YOY > 4 AND
    GROWTH_COMPOSITE < 98" needs both."""
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    signal_levels = {
        "CPIAUCSL": pd.Series([5.0, 5.0, 3.0, 3.0, 3.0], index=idx),
        "GROWTH_COMPOSITE": pd.Series([90.0, 90.0, 90.0, 105.0, 105.0], index=idx),
    }
    disjuncts = [[("CPIAUCSL", ">", 4.0), ("GROWTH_COMPOSITE", "<", 98.0)]]
    active = scenarios.evaluate_trigger_series(disjuncts, signal_levels)
    assert list(active) == [True, True, False, False, False]


def test_evaluate_trigger_series_ors_across_strings() -> None:
    """The trigger LIST is a disjunction (docs/ARCHITECTURE.md: `bear
    triggers: ^VIX > 25 OR (CPI_YOY > 4 AND GROWTH_COMPOSITE < 98)`) — the
    real seed shape, where neither branch alone covers every bear day."""
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    signal_levels = {
        "^VIX": pd.Series([30.0, 10.0, 10.0, 10.0, 10.0], index=idx),
        "CPIAUCSL": pd.Series([2.0, 5.0, 5.0, 2.0, 2.0], index=idx),
        "GROWTH_COMPOSITE": pd.Series([105.0, 90.0, 105.0, 90.0, 105.0], index=idx),
    }
    disjuncts = [
        [("^VIX", ">", 25.0)],
        [("CPIAUCSL", ">", 4.0), ("GROWTH_COMPOSITE", "<", 98.0)],
    ]
    active = scenarios.evaluate_trigger_series(disjuncts, signal_levels)
    #      day 0: VIX 30      -> first disjunct
    #      day 1: CPI 5, G 90 -> second disjunct
    #      day 2: CPI 5 but G 105 -> neither (the string still ANDs)
    assert list(active) == [True, True, False, False, False]


def test_evaluate_trigger_series_is_monotone_in_disjuncts() -> None:
    """The property the M5 bug violated: adding an ALTERNATIVE can only ever
    add active days. Flattening the list into one conjunction made a scenario
    strictly RARER than its own first trigger — live, four-seasons-rp's bear
    (`^VIX > 25` OR stagflation) warm-started at 1.37% while `^VIX > 25`
    alone fired 16.59% of weeks."""
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    signal_levels = {
        "^VIX": pd.Series([30.0, 10.0, 10.0, 10.0, 10.0], index=idx),
        "CPIAUCSL": pd.Series([2.0, 5.0, 5.0, 2.0, 2.0], index=idx),
        "GROWTH_COMPOSITE": pd.Series([105.0, 90.0, 105.0, 90.0, 105.0], index=idx),
    }
    vix_only = [[("^VIX", ">", 25.0)]]
    plus_stagflation = [*vix_only, [("CPIAUCSL", ">", 4.0), ("GROWTH_COMPOSITE", "<", 98.0)]]

    narrow = scenarios.evaluate_trigger_series(vix_only, signal_levels)
    wider = scenarios.evaluate_trigger_series(plus_stagflation, signal_levels)
    assert bool((wider | narrow).equals(wider)), "a superset must stay a superset"
    assert int(wider.sum()) >= int(narrow.sum())


def test_residual_series_is_complement_of_union_restricted_to_available() -> None:
    """A scenario with no parseable trigger of its own (e.g. every seeded
    'base' case) must NOT warm-start at a flat 0% — it is the complement of
    the OTHER scenarios' activity, bounded to dates they actually cover."""
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    bull = pd.Series([True, False, False, False, False], index=idx)
    bear = pd.Series([False, False, True, False, False], index=idx)
    # 'bear' has no data (unavailable) on the last day.
    bull_avail = pd.Series([True, True, True, True, True], index=idx)
    bear_avail = pd.Series([True, True, True, True, False], index=idx)

    residual_active, residual_available = scenarios.residual_series(
        {"bull": bull, "bear": bear}, {"bull": bull_avail, "bear": bear_avail}
    )
    assert list(residual_active) == [False, True, False, True, True]
    assert list(residual_available) == [True, True, True, True, True]


def test_base_rate_zero_total_weeks_is_zero() -> None:
    assert scenarios.base_rate(0, 0) == 0.0
    assert scenarios.base_rate(3, 10) == pytest.approx(0.3)


def test_normalize_probabilities_scales_to_100() -> None:
    result = scenarios.normalize_probabilities({"bull": 0.2, "base": 0.5, "bear": 0.3}, fallback={})
    assert sum(result.values()) == pytest.approx(100.0)
    assert result["base"] == pytest.approx(50.0)


def test_normalize_probabilities_falls_back_when_all_zero() -> None:
    fallback = {"bull": 35.0, "base": 45.0, "bear": 20.0}
    result = scenarios.normalize_probabilities({"bull": 0.0, "base": 0.0, "bear": 0.0}, fallback)
    assert result == fallback
