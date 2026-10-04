"""Research harness safety properties; no live database needed."""

import asyncio
import importlib.util
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from investment.mechanical import market_signal as ms

SPEC = importlib.util.spec_from_file_location(
    "duration_comparison", Path(__file__).with_name("comparison.py")
)
assert SPEC is not None and SPEC.loader is not None
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


@pytest.mark.parametrize("speed", [-0.3, 0.0, 0.2, 0.201, 0.8])
def test_changes_only_duration_and_cash(speed: float) -> None:
    original = {"IEF": 35.0, "VCIT": 25.0, "SPY": 20.0, "IWN": 10.0, "GLD": 5.0, "cash": 5.0}
    target = comparison.duration_target(original, speed, 0.20)
    assert sum(target.values()) == 100.0
    assert original["IEF"] == 35.0
    assert all(target[t] == original[t] for t in ("SPY", "IWN", "GLD"))
    if speed > 0.20:
        assert target.get("IEF", 0.0) == target.get("VCIT", 0.0) == 0.0
        assert target["cash"] == 65.0
    else:
        assert target == original


@pytest.mark.parametrize("speed", [np.nan, np.inf, -np.inf])
def test_missing_yield_speed_is_refused(speed: float) -> None:
    with pytest.raises(ValueError, match="knowable DGS10"):
        comparison.duration_target({"cash": 100.0}, speed, 0.20)


def test_no_phantom_change_without_duration_weight() -> None:
    original = {"SPY": 60.0, "GLD": 40.0, "IEF": 0.0}
    assert comparison.duration_target(original, 0.5, 0.20) == original


def decision(t: pd.Timestamp, changed: bool) -> ms.Decision:
    return ms.Decision(
        date=t,
        signalled="test",
        held="test",
        pending=None,
        pending_count=0,
        spread=2.0,
        spread_median=2.5,
        slope=1.0,
        slope_median=0.5,
        trend={},
        target={"IEF": 90.0, "IWN": 10.0},
        changed=changed,
    )


def test_reentry_when_baseline_target_did_not_change() -> None:
    dates = pd.to_datetime(["2022-01-03", "2022-02-01", "2022-03-01"])
    baseline = [decision(d, i == 0) for i, d in enumerate(dates)]
    variants = comparison.gated_decisions(baseline, pd.Series([0.3, 0.3, 0.1], index=dates), 0.20)
    assert [d.changed for d in variants] == [True, False, True]
    assert variants[0].target == {"cash": 90.0, "IWN": 10.0}
    assert variants[-1].target == baseline[-1].target
    assert all(v.held == b.held for b, v in zip(baseline, variants, strict=True))


def test_target_before_subwindow_is_preserved() -> None:
    prior = decision(pd.Timestamp("2022-01-03"), True)
    index = pd.bdate_range("2022-01-10", "2022-01-14")
    frame = comparison.target_frame([prior], index)
    assert (frame["IEF"] == 90.0).all()


def test_unavailable_initial_speed_is_explicit_warmup() -> None:
    dates = pd.to_datetime(["1993-11-01", "1993-12-01", "1994-01-03"])
    baseline = [decision(d, i == 0) for i, d in enumerate(dates)]
    variants = comparison.gated_decisions(
        baseline, pd.Series([np.nan, np.nan, 0.3], index=dates), 0.20
    )
    assert variants[0].target == variants[1].target == baseline[0].target
    assert variants[-1].target == {"cash": 90.0, "IWN": 10.0}


def test_unavailable_speed_after_warmup_refuses() -> None:
    dates = pd.to_datetime(["1993-11-01", "1994-01-03"])
    baseline = [decision(d, i == 0) for i, d in enumerate(dates)]
    with pytest.raises(ValueError, match="knowable DGS10"):
        comparison.gated_decisions(baseline, pd.Series([np.nan, np.nan], index=dates), 0.20)


def test_database_reader_cannot_write(tmp_path: Path) -> None:
    path = tmp_path / "fixture.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1)")
    reader = comparison.ReadOnlySnapshot(path)
    try:
        assert asyncio.run(reader.query("SELECT * FROM sample")) == [{"value": 1}]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            asyncio.run(reader.query("DELETE FROM sample"))
    finally:
        reader.close()
