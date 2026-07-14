"""M3 tests (docs/MILESTONES.md M3 Definition of Verified). The pure state
machine (classification, confidence, hysteresis, audit) is tested without a
DB; `detect()` is exercised against a real throwaway SQLite (CLAUDE.md: real
DB, no mocks) with directly-scripted MarketData rows (level/speed/
acceleration set explicitly — a synthetic history built through
`derivatives.py`'s real daily/monthly-ambiguity would make the intended
quadrant sequence hard to control precisely).
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from investment.db.seed_data import FRAMEWORKS, REGIME_TYPES, SYSTEM_THRESHOLDS
from investment.db.sqlite import InvestmentDB
from investment.market import regime

# -- fixtures --------------------------------------------------------------

THRESHOLDS = regime.RegimeThresholds(
    cpi_stagflation=2.5,
    cpi_noise=0.05,
    cpi_deflation=0.0,
    cpi_speed_scale=0.3,
    growth_noise=0.15,
    growth_speed_scale=1.0,
    vix_stress=25.0,
    confirm_prints=2,
    smoothing_months=1,  # no smoothing for the pure classification/hysteresis fixtures
)

_NONE = regime.AxisReading(None, None, None)


def _reading(
    level: float | None, speed: float | None, accel: float | None = 0.0
) -> regime.AxisReading:
    return regime.AxisReading(level, speed, accel)


# -- SeriesHistory.asof_smoothed -------------------------------------------


def test_asof_smoothed_absorbs_a_single_month_bounce() -> None:
    """Regression fixture for the real M3 finding: a lone strongly-positive
    print sandwiched inside an otherwise-collapsing run (2008-07 style) must
    NOT read as "rising" once speed is a 3-month trailing average, even
    though the raw single-month print alone would classify as rising."""
    rows = [
        {"ts": "2008-05-15", "level": 97.5, "speed": -0.73, "acceleration": 0.0},
        {"ts": "2008-06-17", "level": 85.7, "speed": -11.79, "acceleration": 0.0},
        {"ts": "2008-07-16", "level": 92.0, "speed": 6.32, "acceleration": 0.0},
    ]
    history = regime.SeriesHistory(ts=[r["ts"] for r in rows], rows=rows)

    raw = history.asof("2008-07-16")
    assert raw.speed == pytest.approx(6.32)  # the noisy single-month print

    smoothed = history.asof_smoothed("2008-07-16", window=3)
    assert smoothed.speed == pytest.approx((-0.73 - 11.79 + 6.32) / 3)
    assert smoothed.speed is not None and smoothed.speed < 0  # still falling, correctly
    assert smoothed.level == 92.0  # level itself is NOT smoothed


def test_asof_smoothed_window_of_one_matches_asof() -> None:
    rows = [{"ts": "2020-01-01", "level": 1.0, "speed": 0.5, "acceleration": 0.1}]
    history = regime.SeriesHistory(ts=[r["ts"] for r in rows], rows=rows)
    assert history.asof_smoothed("2020-01-01", window=1) == history.asof("2020-01-01")


def test_asof_smoothed_empty_history_returns_none_reading() -> None:
    history = regime.SeriesHistory(ts=[], rows=[])
    assert history.asof_smoothed("2020-01-01", window=3) == _NONE


# -- classify_growth / classify_inflation / quadrant ------------------------


def test_classify_growth_thresholds() -> None:
    assert regime.classify_growth(0.20, THRESHOLDS.growth_noise) == "rising"
    assert regime.classify_growth(-0.20, THRESHOLDS.growth_noise) == "falling"
    assert regime.classify_growth(0.05, THRESHOLDS.growth_noise) == "flat"
    assert regime.classify_growth(None, THRESHOLDS.growth_noise) == "flat"


def test_classify_inflation_stagflation_level_gate() -> None:
    # speed>noise AND level>2.5 -> rising, regardless of acceleration
    assert regime.classify_inflation(3.0, 0.10, -1.0, THRESHOLDS) == "rising"
    # speed>noise, level<=2.5, accel>0 -> still counts as rising
    assert regime.classify_inflation(2.0, 0.10, 0.05, THRESHOLDS) == "rising"
    # speed>noise, level<=2.5, accel<=0 -> flat (the tepid, non-accelerating case)
    assert regime.classify_inflation(2.0, 0.10, -0.05, THRESHOLDS) == "flat"
    # speed < -noise -> falling regardless of level
    assert regime.classify_inflation(1.0, -0.10, 0.0, THRESHOLDS) == "falling"
    assert regime.classify_inflation(None, 0.10, 0.0, THRESHOLDS) == "flat"


def test_quadrant_mapping_and_uncertain_fallback() -> None:
    assert regime.quadrant("rising", "falling") == "rising-growth-falling-inflation"
    assert regime.quadrant("rising", "rising") == "rising-growth-rising-inflation"
    assert regime.quadrant("falling", "rising") == "falling-growth-rising-inflation"
    assert regime.quadrant("falling", "falling") == "falling-growth-falling-inflation"
    assert regime.quadrant("flat", "rising") == "uncertain"
    assert regime.quadrant("rising", "flat") == "uncertain"


# -- compute_confidence -------------------------------------------------


def test_compute_confidence_golden_number() -> None:
    """Independent hand-calc against the pinned formula (ARCHITECTURE.md
    'Regime Detection'): axis_strength(growth)=0.5/1.0=0.5,
    axis_strength(inflation)=0.15/0.3=0.5, both accelerations aligned with
    their own speed's sign -> accel_bonus=10.
    confidence = 50 + 20*0.5 + 20*0.5 + 10 = 80."""
    growth = _reading(None, 0.5, 0.2)
    inflation = _reading(3.0, 0.15, 0.05)
    assert regime.compute_confidence(growth, inflation, THRESHOLDS) == pytest.approx(80.0)


def test_compute_confidence_clamped_and_missing_accel_no_bonus() -> None:
    growth = _reading(None, 5.0, None)  # far beyond scale -> axis_strength clamps at 1
    inflation = _reading(None, 5.0, None)
    # no acceleration data -> no accel_bonus; 50 + 20 + 20 + 0 = 90
    assert regime.compute_confidence(growth, inflation, THRESHOLDS) == pytest.approx(90.0)


# -- derive_tags ----------------------------------------------------------


def test_derive_tags() -> None:
    deflation = regime.derive_tags(_reading(-0.5, 0.1), _NONE, None, THRESHOLDS)
    assert "deflation" in deflation

    tightening = regime.derive_tags(_reading(1.0, 0.1), _reading(95.0, -1.0), None, THRESHOLDS)
    assert tightening == ["liquidity-tightening"]

    easing = regime.derive_tags(_reading(1.0, 0.1), _reading(105.0, 1.0), None, THRESHOLDS)
    assert easing == ["liquidity-easing"]

    stress = regime.derive_tags(_reading(1.0, 0.1), _NONE, 30.0, THRESHOLDS)
    assert stress == ["market-stress"]

    calm = regime.derive_tags(_reading(1.0, 0.1), _NONE, 10.0, THRESHOLDS)
    assert calm == []


# -- step() hysteresis -----------------------------------------------------


def _rising_falling_readings(candidate: str) -> tuple[regime.AxisReading, regime.AxisReading]:
    """Growth/inflation readings that classify as exactly `candidate`."""
    growth_speed = 0.5 if candidate.startswith("rising-growth") else -0.5
    inflation_speed = 0.5 if candidate.endswith("rising-inflation") else -0.5
    inflation_level = 3.0 if candidate.endswith("rising-inflation") else 1.0
    return _reading(None, growth_speed, 0.0), _reading(inflation_level, inflation_speed, 0.0)


def test_hysteresis_flip_flop_never_commits() -> None:
    """Alternating candidates every print never reach 2 CONSECUTIVE prints of
    the same quadrant -> commit stays None throughout (docs/MILESTONES.md M3
    DoV 'flip-flop fixture does not switch before 2 concordant prints')."""
    state = regime.EMPTY_STATE
    current: regime.CurrentRegime | None = None
    candidates = ["rising-growth-rising-inflation", "falling-growth-falling-inflation"]
    d = date(2020, 1, 1)
    for i in range(20):
        g, infl = _rising_falling_readings(candidates[i % 2])
        result = regime.step(
            state=state,
            current=current,
            print_ts=d,
            growth=g,
            inflation=infl,
            liquidity=_NONE,
            vix_level=None,
            thresholds=THRESHOLDS,
        )
        assert result.commit is None
        state = result.state
        d += timedelta(days=30)


def test_hysteresis_commits_after_two_consecutive_prints_backdated() -> None:
    state = regime.EMPTY_STATE
    current: regime.CurrentRegime | None = None
    g, infl = _rising_falling_readings("falling-growth-rising-inflation")

    d1 = date(2020, 3, 1)
    r1 = regime.step(
        state=state,
        current=current,
        print_ts=d1,
        growth=g,
        inflation=infl,
        liquidity=_NONE,
        vix_level=None,
        thresholds=THRESHOLDS,
    )
    assert r1.commit is None  # 1 of 2 confirmations only
    state = r1.state

    d2 = date(2020, 4, 1)
    r2 = regime.step(
        state=state,
        current=current,
        print_ts=d2,
        growth=g,
        inflation=infl,
        liquidity=_NONE,
        vix_level=None,
        thresholds=THRESHOLDS,
    )
    assert r2.commit is not None
    assert r2.commit.regime_type_id == "falling-growth-rising-inflation"
    # backdated to the FIRST of the confirming streak, not the confirming print
    assert r2.commit.start_date == d1.isoformat()
    assert r2.commit.previous_regime_id is None  # bootstrap: no prior regime


def test_step_updates_confidence_band_without_type_change() -> None:
    """No regime-type change, but confidence moves outside the +-10 band ->
    `update`, not `commit` (ARCHITECTURE.md 'RegimeEvent ... only when the
    regime, confidence band, or tag set changes')."""
    current = regime.CurrentRegime(
        id="r1", regime_type_id="rising-growth-rising-inflation", confidence=50.0, tags=()
    )
    state = regime.DetectorState(
        candidate_type="rising-growth-rising-inflation",
        candidate_start_ts="2020-01-01",
        consecutive_prints=5,
        last_print_ts_growth="2020-05-01",
        last_print_ts_inflation="2020-05-01",
    )
    # strong, accelerating readings -> high confidence, far outside the 50+-10 band
    g = _reading(None, 1.0, 1.0)
    infl = _reading(3.0, 1.0, 1.0)
    result = regime.step(
        state=state,
        current=current,
        print_ts=date(2020, 6, 1),
        growth=g,
        inflation=infl,
        liquidity=_NONE,
        vix_level=None,
        thresholds=THRESHOLDS,
    )
    assert result.commit is None
    assert result.update is not None
    assert result.update.regime_id == "r1"


def test_events_narrative_carries_raw_speed_not_smoothed() -> None:
    """Fix #2: detection classifies on the SMOOTHED speed, but the persisted
    Regime.events narrative must report the RAW market_data speed so it
    cross-checks exactly against the TS. Pass a smoothed reading whose speed
    differs from the raw reading and assert the commit's events show raw."""
    smoothed = _reading(3.0, 0.20, 0.0)  # smoothed speed drives classification
    raw = _reading(3.0, 0.55, 0.0)  # raw speed is what the narrative must show

    def _step(state: regime.DetectorState, day: int) -> regime.StepResult:
        return regime.step(
            state=state,
            current=None,
            print_ts=date(2020, day, 1),
            growth=smoothed,
            inflation=smoothed,
            liquidity=_NONE,
            vix_level=None,
            thresholds=THRESHOLDS,
            growth_raw=raw,
            inflation_raw=raw,
        )

    r1 = _step(regime.EMPTY_STATE, 3)
    r2 = _step(r1.state, 4)
    assert r2.commit is not None
    joined = " ".join(r2.commit.events)
    assert "+0.55" in joined  # raw speed
    assert "+0.20" not in joined  # NOT the smoothed speed


# -- async detect() against a real DB --------------------------------------


def _settings_db(tmp_path: Path) -> Path:
    return tmp_path / "regime.db"


async def _seed_minimal(db: InvestmentDB) -> None:
    now = "2026-01-01T00:00:00+00:00"
    for fw in FRAMEWORKS:
        props = {k: v for k, v in fw.items() if k != "id"}
        await db.upsert_vertex("framework", str(fw["id"]), props)
    for rt in REGIME_TYPES:
        props = {k: v for k, v in rt.items() if k != "id"}
        await db.upsert_vertex("regime_type", str(rt["id"]), props)
    for key, value in SYSTEM_THRESHOLDS.items():
        await db.command(
            "INSERT OR REPLACE INTO system_thresholds (key, value, updated_at) "
            "VALUES (:k, :v, :now)",
            k=key,
            v=value,
            now=now,
        )


def _monthly_rows(
    ticker: str, months: int, period: int, high: dict[str, float], low: dict[str, float]
) -> list[dict]:
    """`high`/`low` are {"level":.., "speed":.., "acceleration":..} dicts,
    alternated every `period//2` months — directly-scripted derivatives, not
    derived through derivatives.py, for full control over the resulting
    quadrant sequence."""
    rows = []
    y, m = 1991, 1
    for i in range(months):
        vals = high if (i % period) < (period // 2) else low
        rows.append(
            {
                "ticker": ticker,
                "asset_class": "MACRO",
                "currency": "USD",
                "ts": date(y, m, 1).isoformat(),
                **vals,
            }
        )
        m += 1
        if m > 12:
            m = 1
            y += 1
    return rows


# Speed magnitudes comfortably clear the REAL calibrated defaults in
# SYSTEM_THRESHOLDS (regime_growth_noise=0.3, regime_cpi_noise=0.04 — the
# M3 event-scored grid search, see seed_data.py) — these DB-integration
# fixtures load thresholds from `system_thresholds`, not the local
# low-threshold `THRESHOLDS` fixture used by the pure step()/audit() tests.
_GROWTH_HIGH = {"level": 105.0, "speed": 2.0, "acceleration": 0.0}
_GROWTH_LOW = {"level": 95.0, "speed": -2.0, "acceleration": 0.0}
_INFLATION_HIGH = {"level": 3.5, "speed": 0.3, "acceleration": 0.0}
_INFLATION_LOW = {"level": 1.5, "speed": -0.3, "acceleration": 0.0}


def _seed_35y_oscillating_rows() -> tuple[list[dict], list[dict]]:
    """period=12/16 (each block = 6/8 months), well beyond the real
    regime_speed_smoothing_months=4 default: a period only ~2x the
    smoothing window aliases against the trailing average (every block
    transition costs `window` months of blended/flat readings, leaving too
    few clean months for `confirm_prints` consecutive confirmations — the
    exact aliasing the M3 calibration had to avoid on the real
    GROWTH_COMPOSITE too)."""
    months = 35 * 12
    growth_rows = _monthly_rows(
        "GROWTH_COMPOSITE", months, period=12, high=_GROWTH_HIGH, low=_GROWTH_LOW
    )
    inflation_rows = _monthly_rows(
        "CPIAUCSL", months, period=16, high=_INFLATION_HIGH, low=_INFLATION_LOW
    )
    return growth_rows, inflation_rows


async def _seed_35y_oscillating(db: InvestmentDB) -> None:
    growth_rows, inflation_rows = _seed_35y_oscillating_rows()
    await db.append_ts_batch("market_data", growth_rows)
    await db.append_ts_batch("market_data", inflation_rows)


async def test_detect_35y_materialization_yields_many_episodes(tmp_path: Path) -> None:
    db = InvestmentDB(_settings_db(tmp_path))
    try:
        await _seed_minimal(db)
        await _seed_35y_oscillating(db)

        commits = await regime.detect(db)
        assert len(commits) >= 10  # docs/MILESTONES.md M3 DoV

        episodes = await db.query("SELECT COUNT(*) AS n FROM regime")
        assert episodes[0]["n"] == len(commits)
        current = await db.query("SELECT COUNT(*) AS n FROM regime WHERE is_current = 1")
        assert current[0]["n"] == 1

        events = await db.query("SELECT COUNT(*) AS n FROM event_log WHERE type = 'RegimeEvent'")
        assert events[0]["n"] >= len(commits)

        state = await db.query("SELECT * FROM detector_state WHERE id = 'singleton'")
        assert state[0]["consecutive_prints"] >= 1
    finally:
        await db.close()


async def test_detect_is_idempotent_on_rerun(tmp_path: Path) -> None:
    db = InvestmentDB(_settings_db(tmp_path))
    try:
        await _seed_minimal(db)
        await _seed_35y_oscillating(db)
        first = await regime.detect(db)
        assert first

        second = await regime.detect(db)
        assert second == []  # no new prints -> no-op, no duplicate RegimeEvents

        events = await db.query("SELECT COUNT(*) AS n FROM event_log WHERE type = 'RegimeEvent'")
        assert events[0]["n"] >= len(first)
    finally:
        await db.close()


async def test_regime_events_frozen_across_confidence_or_tag_update(tmp_path: Path) -> None:
    """Fix #3: `Regime.events` records the trigger observations at commit and
    must NOT be overwritten when a later print only refreshes confidence/tags
    (docs/DATA_MODELS.md Regime.events) — otherwise the field ends up
    describing some mid-life print, not what triggered the regime."""
    db = InvestmentDB(_settings_db(tmp_path))
    try:
        await _seed_minimal(db)
        # 3 stable rising-growth/rising-inflation prints -> commit; calm VIX.
        dates = ["2020-01-15", "2020-02-15", "2020-03-15"]
        for ts in dates:
            await db.append_ts(
                "market_data",
                datetime.fromisoformat(ts + "T00:00:00+00:00"),
                {"ticker": "GROWTH_COMPOSITE", "asset_class": "MACRO", "currency": "USD"},
                {"level": 105.0, "speed": 2.0, "acceleration": 0.0},
            )
            await db.append_ts(
                "market_data",
                datetime.fromisoformat(ts + "T00:00:00+00:00"),
                {"ticker": "CPIAUCSL", "asset_class": "MACRO", "currency": "USD"},
                {"level": 3.5, "speed": 0.3, "acceleration": 0.0},
            )
            await db.append_ts(
                "market_data",
                datetime.fromisoformat(ts + "T00:00:00+00:00"),
                {"ticker": "^VIX", "asset_class": "VOLATILITY", "currency": "USD"},
                {"level": 12.0, "speed": 0.0, "acceleration": 0.0},
            )
        commits = await _detect_and_current_events(db)
        events_at_commit = commits

        # A later same-type print with a VIX spike -> market-stress tag change
        # -> update (not commit). Same growth/inflation direction, so the
        # regime type is unchanged.
        await db.append_ts(
            "market_data",
            datetime.fromisoformat("2020-04-15T00:00:00+00:00"),
            {"ticker": "GROWTH_COMPOSITE", "asset_class": "MACRO", "currency": "USD"},
            {"level": 105.0, "speed": 2.0, "acceleration": 0.0},
        )
        await db.append_ts(
            "market_data",
            datetime.fromisoformat("2020-04-15T00:00:00+00:00"),
            {"ticker": "CPIAUCSL", "asset_class": "MACRO", "currency": "USD"},
            {"level": 3.5, "speed": 0.3, "acceleration": 0.0},
        )
        await db.append_ts(
            "market_data",
            datetime.fromisoformat("2020-04-15T00:00:00+00:00"),
            {"ticker": "^VIX", "asset_class": "VOLATILITY", "currency": "USD"},
            {"level": 40.0, "speed": 5.0, "acceleration": 0.0},
        )
        new_commits = await regime.detect(db)
        assert new_commits == []  # a tag update, not a new episode

        row = (await db.query("SELECT events, tags FROM regime WHERE is_current = 1"))[0]
        assert json.loads(row["events"]) == events_at_commit  # events FROZEN at commit
        assert "market-stress" in json.loads(row["tags"])  # but tags DID refresh
    finally:
        await db.close()


async def _detect_and_current_events(db: InvestmentDB) -> list[str]:
    await regime.detect(db)
    row = (await db.query("SELECT events FROM regime WHERE is_current = 1"))[0]
    return list(json.loads(row["events"]))


async def test_detect_split_across_calls_matches_single_call(tmp_path: Path) -> None:
    """docs/MILESTONES.md M3 DoV: 'on a 7-day window, produces byte-identical
    Regime state to a hypothetical daily run' — splitting the same history
    across several `detect()` calls (as real periodic catch-up polling
    would) must land on the same final Regime/detector_state as one bulk
    call."""
    db_bulk = InvestmentDB(tmp_path / "bulk.db")
    db_split = InvestmentDB(tmp_path / "split.db")
    try:
        await _seed_minimal(db_bulk)
        await _seed_35y_oscillating(db_bulk)
        await regime.detect(db_bulk)

        await _seed_minimal(db_split)
        growth_rows, inflation_rows = _seed_35y_oscillating_rows()
        # 6 arbitrary chunks, mimicking repeated catch-up runs over time
        chunk = len(growth_rows) // 6 + 1
        for start in range(0, len(growth_rows), chunk):
            await db_split.append_ts_batch("market_data", growth_rows[start : start + chunk])
            await db_split.append_ts_batch("market_data", inflation_rows[start : start + chunk])
            await regime.detect(db_split)

        bulk_regimes = await db_bulk.query(
            "SELECT id, regime_type_id, start_date, end_date, confidence, is_current "
            "FROM regime ORDER BY start_date"
        )
        split_regimes = await db_split.query(
            "SELECT id, regime_type_id, start_date, end_date, confidence, is_current "
            "FROM regime ORDER BY start_date"
        )
        assert bulk_regimes == split_regimes
    finally:
        await db_bulk.close()
        await db_split.close()


# -- audit() ----------------------------------------------------------------


def test_audit_over_synthetic_history_reports_bounded_metrics() -> None:
    months = 35 * 12
    growth_rows = _monthly_rows(
        "GROWTH_COMPOSITE",
        months,
        period=6,
        high={"level": 105.0, "speed": 0.5, "acceleration": 0.0},
        low={"level": 95.0, "speed": -0.5, "acceleration": 0.0},
    )
    inflation_rows = _monthly_rows(
        "CPIAUCSL",
        months,
        period=10,
        high={"level": 3.5, "speed": 0.5, "acceleration": 0.0},
        low={"level": 1.5, "speed": -0.5, "acceleration": 0.0},
    )
    growth = regime.SeriesHistory(ts=[r["ts"] for r in growth_rows], rows=growth_rows)
    inflation = regime.SeriesHistory(ts=[r["ts"] for r in inflation_rows], rows=inflation_rows)
    empty = regime.SeriesHistory(ts=[], rows=[])

    report = regime.audit(growth, inflation, empty, empty, THRESHOLDS)

    assert report.episode_count >= 10
    assert report.suppressed_switches >= 0
    assert report.raw_candidate_switches >= report.episode_count
    # confirm_prints=2 with monthly prints -> lag should be small (a couple
    # of months), never the full 35y span.
    assert 0 <= report.mean_detector_lag_days < 120
    assert report.max_detector_lag_days < 200
