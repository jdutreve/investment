"""Regime detector (docs/TASKS.md Task 2.3) — implements the formal
algorithm in docs/ARCHITECTURE.md "Regime Detection": axis classification,
`regime_confirm_prints` hysteresis, confidence formula, tag derivation,
`is_current` uniqueness.

Split in two layers:
  - a PURE core (dataclasses + `evaluate_print`/`step`/`audit`) — no I/O,
    directly unit-testable (hysteresis/flip-flop fixtures, confidence golden
    numbers) and reusable from the read-only `invest` CLI (ADR-005: no
    `InvestmentDB` — the agent's writer handle — outside the agent process).
  - a thin async DB layer (`detect`) that feeds the core from `market_data`
    and persists commits — ONE code path shared by all four callers (UC0 35y
    materialization, the Phase 9 replay, the Monday 08:00 catch-up, and the
    on-demand UC9 prelude — docs/DATA_MODELS.md Regime entity).

`detector.step(print_set)`: ONE state-machine step per NEW monthly print,
candidate state persisted in `detector_state`; PIT by construction (ADR-003).
"""

import json
import statistics
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from investment.db.sqlite import InvestmentDB

FRAMEWORK_ID = "4seasons"
GROWTH_TICKER = "GROWTH_COMPOSITE"
INFLATION_TICKER = "CPIAUCSL"  # transform=yoy_pct at seed time -> level IS CPI YoY
LIQUIDITY_TICKER = "GLOBAL_LIQUIDITY"
VIX_TICKER = "^VIX"

# "confidence band (+-10)" (ARCHITECTURE.md): a RegimeEvent/vertex update
# fires only when confidence moves outside this tolerance from the last
# stored value, or the tag set changes — not on every 1-point wobble.
CONFIDENCE_BAND = 10.0

_QUADRANTS: dict[tuple[str, str], str] = {
    ("rising", "falling"): "rising-growth-falling-inflation",
    ("rising", "rising"): "rising-growth-rising-inflation",
    ("falling", "rising"): "falling-growth-rising-inflation",
    ("falling", "falling"): "falling-growth-falling-inflation",
}

_REQUIRED_THRESHOLD_KEYS = (
    "regime_cpi_stagflation",
    "regime_cpi_noise",
    "regime_cpi_deflation",
    "regime_cpi_speed_scale",
    "regime_growth_noise",
    "regime_growth_speed_scale",
    "regime_vix_stress",
    "regime_confirm_prints",
    "regime_speed_smoothing_months",
)


# -- pure data ---------------------------------------------------------


@dataclass(frozen=True)
class RegimeThresholds:
    cpi_stagflation: float
    cpi_noise: float
    cpi_deflation: float
    cpi_speed_scale: float
    growth_noise: float
    growth_speed_scale: float
    vix_stress: float
    confirm_prints: int
    smoothing_months: int

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> "RegimeThresholds":
        values = {r["key"]: r["value"] for r in rows}
        missing = [k for k in _REQUIRED_THRESHOLD_KEYS if k not in values]
        if missing:
            raise ValueError(f"system_thresholds missing regime keys: {missing}")
        return cls(
            cpi_stagflation=values["regime_cpi_stagflation"],
            cpi_noise=values["regime_cpi_noise"],
            cpi_deflation=values["regime_cpi_deflation"],
            cpi_speed_scale=values["regime_cpi_speed_scale"],
            growth_noise=values["regime_growth_noise"],
            growth_speed_scale=values["regime_growth_speed_scale"],
            vix_stress=values["regime_vix_stress"],
            confirm_prints=int(values["regime_confirm_prints"]),
            smoothing_months=int(values["regime_speed_smoothing_months"]),
        )


@dataclass(frozen=True)
class AxisReading:
    level: float | None
    speed: float | None
    acceleration: float | None


@dataclass(frozen=True)
class SeriesHistory:
    """A ticker's full `market_data` history, sorted ascending by `ts`
    (ISO-8601 date strings — lexical order == chronological order), for
    repeated as-of lookups without a DB round-trip per print."""

    ts: list[str]
    rows: list[dict[str, Any]]

    def asof(self, ts: str) -> AxisReading:
        """Latest known observation at or before `ts` — PIT by construction
        (ADR-003): never looks at a row dated after `ts`."""
        i = bisect_right(self.ts, ts) - 1
        if i < 0:
            return AxisReading(None, None, None)
        row = self.rows[i]
        return AxisReading(row["level"], row["speed"], row["acceleration"])

    def asof_smoothed(self, ts: str, window: int) -> AxisReading:
        """Like `asof`, but `speed` is a trailing `window`-observation moving
        average rather than the single latest print. A bare 1-month diff of
        a z-score-amplified composite (GROWTH_COMPOSITE) is dominated by
        single-month noise — verified live at M3: a lone +6.3 print
        sandwiched inside the 2008 collapse otherwise reads as "rising
        growth" no matter where the noise threshold sits, because it's a
        genuine month-over-month bounce, not a scale-calibration artifact.
        `level`/`acceleration` stay the latest raw observation (still needed
        for the CPI stagflation-level gate and for `events`/display) — only
        the DIRECTION-classifying speed is smoothed, and only inside the
        regime detector's own read of the series: the persisted market_data
        level/speed/acceleration (TASKS.md Task 2.2's pinned formula) are
        untouched, so every other consumer (invariant conditions, the
        Worker) still sees the raw series."""
        i = bisect_right(self.ts, ts) - 1
        if i < 0:
            return AxisReading(None, None, None)
        latest = self.rows[i]
        window_rows = self.rows[max(0, i - window + 1) : i + 1]
        speeds = [r["speed"] for r in window_rows if r["speed"] is not None]
        smoothed_speed = sum(speeds) / len(speeds) if speeds else None
        return AxisReading(latest["level"], smoothed_speed, latest["acceleration"])


@dataclass(frozen=True)
class DetectorState:
    """Mirrors `detector_state` (hysteresis columns only — `last_chain_success`
    is owned by the weekly-chain scheduler, not this module)."""

    candidate_type: str | None
    candidate_start_ts: str | None
    consecutive_prints: int
    last_print_ts_growth: str | None
    last_print_ts_inflation: str | None


EMPTY_STATE = DetectorState(None, None, 0, None, None)


@dataclass(frozen=True)
class CurrentRegime:
    """The `regime` row with `is_current=1`, or None if none exists yet
    (before the first-ever confirmation)."""

    id: str
    regime_type_id: str
    confidence: float
    tags: tuple[str, ...]


@dataclass(frozen=True)
class PrintEvaluation:
    growth_dir: str
    inflation_dir: str
    candidate: str
    confidence: float
    tags: list[str]
    events: list[str]


@dataclass(frozen=True)
class RegimeCommit:
    """A hysteresis-confirmed regime TYPE change — closes the previous
    `is_current` Regime (if any) and opens a new one."""

    regime_type_id: str
    start_date: str  # backdated to the confirming streak's first print
    confidence: float
    tags: list[str]
    events: list[str]
    previous_regime_id: str | None
    previous_regime_type_id: str | None


@dataclass(frozen=True)
class RegimeUpdate:
    """No type change, but confidence moved outside its band or the tag set
    changed — refreshes the existing `is_current` Regime vertex's confidence
    and tags in place. `events` is NOT part of an update: it records the
    observations that TRIGGERED the regime (docs/DATA_MODELS.md Regime.events,
    docs/EXAMPLE.md), frozen at commit — overwriting it on every later
    confidence wobble would leave `events` describing some mid-life print
    instead of the trigger, no longer cross-checkable against created_at."""

    regime_id: str
    confidence: float
    tags: list[str]


@dataclass(frozen=True)
class StepResult:
    state: DetectorState
    commit: RegimeCommit | None
    update: RegimeUpdate | None


# -- pure classification (docs/ARCHITECTURE.md "Regime Detection") -----


def classify_growth(speed: float | None, noise: float) -> str:
    if speed is None:
        return "flat"
    if speed > noise:
        return "rising"
    if speed < -noise:
        return "falling"
    return "flat"


def classify_inflation(
    level: float | None,
    speed: float | None,
    acceleration: float | None,
    thresholds: RegimeThresholds,
) -> str:
    if level is None or speed is None:
        return "flat"
    if speed > thresholds.cpi_noise:
        accel = acceleration or 0.0
        if level > thresholds.cpi_stagflation or accel > 0:
            return "rising"
        return "flat"
    if speed < -thresholds.cpi_noise:
        return "falling"
    return "flat"


def quadrant(growth_dir: str, inflation_dir: str) -> str:
    return _QUADRANTS.get((growth_dir, inflation_dir), "uncertain")


def axis_strength(speed: float | None, scale: float) -> float:
    if speed is None or scale == 0:
        return 0.0
    return min(1.0, abs(speed) / scale)


def _same_sign(a: float, b: float) -> bool:
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def compute_confidence(
    growth: AxisReading, inflation: AxisReading, thresholds: RegimeThresholds
) -> float:
    # `growth`/`inflation` here are the SMOOTHED readings (see evaluate_print):
    # axis_strength must use the smoothed speed because growth_speed_scale is
    # calibrated to the p90 of the smoothed distribution (seed_data.py); the
    # accel_bonus then asks whether the freshest raw acceleration is still
    # pushing in that sustained direction — an intentional short/long-horizon
    # pairing, not a mix-up.
    g_strength = axis_strength(growth.speed, thresholds.growth_speed_scale)
    i_strength = axis_strength(inflation.speed, thresholds.cpi_speed_scale)
    accel_bonus = 0.0
    if (
        growth.speed is not None
        and growth.acceleration is not None
        and inflation.speed is not None
        and inflation.acceleration is not None
        and _same_sign(growth.acceleration, growth.speed)
        and _same_sign(inflation.acceleration, inflation.speed)
    ):
        accel_bonus = 10.0
    raw = 50.0 + 20.0 * g_strength + 20.0 * i_strength + accel_bonus
    return max(0.0, min(100.0, raw))


def derive_tags(
    inflation: AxisReading,
    liquidity: AxisReading,
    vix_level: float | None,
    thresholds: RegimeThresholds,
) -> list[str]:
    """The pinned tag set (ARCHITECTURE.md "Tags layered on top") — deflation,
    liquidity-tightening/easing, market-stress. Instance-level, layered on
    top of the RegimeType classification."""
    tags = []
    if inflation.level is not None and inflation.level < thresholds.cpi_deflation:
        tags.append("deflation")
    if liquidity.level is not None and liquidity.speed is not None:
        if liquidity.level < 100 and liquidity.speed < 0:
            tags.append("liquidity-tightening")
        elif liquidity.level > 100 and liquidity.speed > 0:
            tags.append("liquidity-easing")
    if vix_level is not None and vix_level > thresholds.vix_stress:
        tags.append("market-stress")
    return tags


def derive_events(growth: AxisReading, inflation: AxisReading, liquidity: AxisReading) -> list[str]:
    """Narrative observation strings (docs/DATA_MODELS.md Regime.events) —
    the numbers that triggered the reading, docs/EXAMPLE.md style."""
    events = []
    if inflation.level is not None:
        if inflation.speed is not None and inflation.acceleration is not None:
            events.append(
                f"CPI YoY {inflation.level:.1f} (speed {inflation.speed:+.2f}, "
                f"accel {inflation.acceleration:+.2f})"
            )
        else:
            events.append(f"CPI YoY {inflation.level:.1f}")
    if growth.level is not None:
        if growth.speed is not None and growth.acceleration is not None:
            events.append(
                f"GROWTH_COMPOSITE {growth.level:.1f} (speed {growth.speed:+.2f}, "
                f"accel {growth.acceleration:+.2f})"
            )
        else:
            events.append(f"GROWTH_COMPOSITE {growth.level:.1f}")
    if liquidity.level is not None and liquidity.speed is not None:
        direction = "tightening" if liquidity.speed < 0 else "easing"
        events.append(
            f"global liquidity {direction} "
            f"(level {liquidity.level:.1f}, speed {liquidity.speed:+.2f})"
        )
    return events


def evaluate_print(
    growth: AxisReading,
    inflation: AxisReading,
    liquidity: AxisReading,
    vix_level: float | None,
    thresholds: RegimeThresholds,
    *,
    growth_raw: AxisReading | None = None,
    inflation_raw: AxisReading | None = None,
) -> PrintEvaluation:
    """`growth`/`inflation` are the SMOOTHED readings that drive classification
    and confidence. `growth_raw`/`inflation_raw` are the raw market_data row
    for the SAME print and are used only for the `events` narrative, so the
    persisted Regime.events cross-checks exactly against market_data (raw
    speed included) — detection reasons on smoothed speed, the audit trail
    records what was actually observed. Callers that don't need the narrative
    (or the pure-classification tests) may omit them; the smoothed reading is
    then used as a fallback."""
    growth_dir = classify_growth(growth.speed, thresholds.growth_noise)
    inflation_dir = classify_inflation(
        inflation.level, inflation.speed, inflation.acceleration, thresholds
    )
    candidate = quadrant(growth_dir, inflation_dir)
    confidence = compute_confidence(growth, inflation, thresholds)
    tags = derive_tags(inflation, liquidity, vix_level, thresholds)
    events = derive_events(growth_raw or growth, inflation_raw or inflation, liquidity)
    return PrintEvaluation(growth_dir, inflation_dir, candidate, confidence, tags, events)


# -- pure state machine --------------------------------------------------


def step(
    *,
    state: DetectorState,
    current: CurrentRegime | None,
    print_ts: date,
    growth: AxisReading,
    inflation: AxisReading,
    liquidity: AxisReading,
    vix_level: float | None,
    thresholds: RegimeThresholds,
    growth_raw: AxisReading | None = None,
    inflation_raw: AxisReading | None = None,
) -> StepResult:
    """ONE state-machine step for a single new print. Hysteresis: a regime
    CHANGE commits only once the same candidate has been produced by
    `thresholds.confirm_prints` consecutive prints; until then `current`
    stays authoritative and the candidate is tracked in `state` only.
    `growth_raw`/`inflation_raw` (the unsmoothed row) feed only the events
    narrative — see `evaluate_print`."""
    evaluation = evaluate_print(
        growth,
        inflation,
        liquidity,
        vix_level,
        thresholds,
        growth_raw=growth_raw,
        inflation_raw=inflation_raw,
    )
    print_ts_iso = print_ts.isoformat()

    if evaluation.candidate == state.candidate_type:
        consecutive = state.consecutive_prints + 1
        candidate_start = state.candidate_start_ts or print_ts_iso
    else:
        consecutive = 1
        candidate_start = print_ts_iso

    new_state = replace(
        state,
        candidate_type=evaluation.candidate,
        candidate_start_ts=candidate_start,
        consecutive_prints=consecutive,
    )

    confirmed = consecutive >= thresholds.confirm_prints
    changed_type = current is None or evaluation.candidate != current.regime_type_id

    if confirmed and changed_type:
        commit = RegimeCommit(
            regime_type_id=evaluation.candidate,
            start_date=candidate_start,
            confidence=evaluation.confidence,
            tags=evaluation.tags,
            events=evaluation.events,
            previous_regime_id=current.id if current else None,
            previous_regime_type_id=current.regime_type_id if current else None,
        )
        return StepResult(state=new_state, commit=commit, update=None)

    if current is not None:
        band_changed = abs(evaluation.confidence - current.confidence) > CONFIDENCE_BAND
        tags_changed = set(evaluation.tags) != set(current.tags)
        if band_changed or tags_changed:
            update = RegimeUpdate(
                regime_id=current.id,
                confidence=evaluation.confidence,
                tags=evaluation.tags,
            )
            return StepResult(state=new_state, commit=None, update=update)

    return StepResult(state=new_state, commit=None, update=None)


# -- pure stability audit (docs/MILESTONES.md M3 DoV #4) -----------------


@dataclass(frozen=True)
class AuditReport:
    episode_count: int
    whipsaw_count: int
    median_episode_days: float | None
    mean_detector_lag_days: float
    max_detector_lag_days: int
    raw_candidate_switches: int
    suppressed_switches: int


def audit(
    growth: SeriesHistory,
    inflation: SeriesHistory,
    liquidity: SeriesHistory,
    vix: SeriesHistory,
    thresholds: RegimeThresholds,
) -> AuditReport:
    """Independent from-scratch replay of the full history (ignores whatever
    is currently persisted in `detector_state`/`regime`, so the numbers are
    always reproducible): whipsaw count (episodes reversed within 3 months),
    median episode length, detector lag (start_date -> confirming print
    date), and how many raw candidate switches the hysteresis suppressed."""
    print_dates = sorted(set(growth.ts) | set(inflation.ts))

    state = EMPTY_STATE
    current: CurrentRegime | None = None
    commits: list[tuple[str, RegimeCommit]] = []
    raw_switches = 0

    for ts in print_dates:
        g = growth.asof_smoothed(ts, thresholds.smoothing_months)
        i = inflation.asof_smoothed(ts, thresholds.smoothing_months)
        liq, v = liquidity.asof(ts), vix.asof(ts)
        evaluation = evaluate_print(g, i, liq, v.level, thresholds)
        if evaluation.candidate != state.candidate_type:
            raw_switches += 1
        result = step(
            state=state,
            current=current,
            print_ts=date.fromisoformat(ts),
            growth=g,
            inflation=i,
            liquidity=liq,
            vix_level=v.level,
            thresholds=thresholds,
            growth_raw=growth.asof(ts),
            inflation_raw=inflation.asof(ts),
        )
        state = result.state
        if result.commit is not None:
            commits.append((ts, result.commit))
            current = CurrentRegime(
                id="audit",
                regime_type_id=result.commit.regime_type_id,
                confidence=result.commit.confidence,
                tags=tuple(result.commit.tags),
            )

    lags = [
        (date.fromisoformat(ts) - date.fromisoformat(commit.start_date)).days
        for ts, commit in commits
    ]

    episode_days: list[int] = []
    whipsaws = 0
    for idx in range(1, len(commits)):
        prev_start = date.fromisoformat(commits[idx - 1][1].start_date)
        this_commit = commits[idx][1]
        duration = (date.fromisoformat(this_commit.start_date) - prev_start).days
        episode_days.append(duration)
        two_back_type = commits[idx - 2][1].regime_type_id if idx >= 2 else None
        if this_commit.regime_type_id == two_back_type and duration <= 90:
            whipsaws += 1
    if commits and print_dates:
        last_commit = commits[-1][1]
        episode_days.append(
            (date.fromisoformat(print_dates[-1]) - date.fromisoformat(last_commit.start_date)).days
        )

    return AuditReport(
        episode_count=len(commits),
        whipsaw_count=whipsaws,
        median_episode_days=statistics.median(episode_days) if episode_days else None,
        mean_detector_lag_days=statistics.mean(lags) if lags else 0.0,
        max_detector_lag_days=max(lags) if lags else 0,
        raw_candidate_switches=raw_switches,
        suppressed_switches=raw_switches - len(commits),
    )


# -- async DB layer (writer path — agent-only, ADR-004/ADR-005) ----------


async def _load_thresholds(db: InvestmentDB) -> RegimeThresholds:
    rows = await db.query("SELECT key, value FROM system_thresholds")
    return RegimeThresholds.from_rows(rows)


async def _load_state(db: InvestmentDB) -> DetectorState:
    rows = await db.query("SELECT * FROM detector_state WHERE id = 'singleton'")
    if not rows:
        return EMPTY_STATE
    r = rows[0]
    return DetectorState(
        candidate_type=r["candidate_type"],
        candidate_start_ts=r["candidate_start_ts"],
        consecutive_prints=r["consecutive_prints"],
        last_print_ts_growth=r["last_print_ts_growth"],
        last_print_ts_inflation=r["last_print_ts_inflation"],
    )


async def _persist_state(db: InvestmentDB, state: DetectorState) -> None:
    await db.command(
        "INSERT INTO detector_state "
        "(id, candidate_type, candidate_start_ts, consecutive_prints, "
        " last_print_ts_growth, last_print_ts_inflation, updated_at) "
        "VALUES ('singleton', :candidate_type, :candidate_start_ts, :consecutive_prints, "
        " :last_print_ts_growth, :last_print_ts_inflation, :updated_at) "
        "ON CONFLICT(id) DO UPDATE SET "
        " candidate_type = excluded.candidate_type, "
        " candidate_start_ts = excluded.candidate_start_ts, "
        " consecutive_prints = excluded.consecutive_prints, "
        " last_print_ts_growth = excluded.last_print_ts_growth, "
        " last_print_ts_inflation = excluded.last_print_ts_inflation, "
        " updated_at = excluded.updated_at",
        candidate_type=state.candidate_type,
        candidate_start_ts=state.candidate_start_ts,
        consecutive_prints=state.consecutive_prints,
        last_print_ts_growth=state.last_print_ts_growth,
        last_print_ts_inflation=state.last_print_ts_inflation,
        updated_at=datetime.now(UTC).isoformat(),
    )


async def _load_current_regime(db: InvestmentDB) -> CurrentRegime | None:
    rows = await db.query(
        "SELECT regime.id, regime.regime_type_id, regime.confidence, regime.tags "
        "FROM regime JOIN regime_type ON regime_type.id = regime.regime_type_id "
        "WHERE regime.is_current = 1 AND regime_type.framework_id = :fw",
        fw=FRAMEWORK_ID,
    )
    if not rows:
        return None
    r = rows[0]
    tags = tuple(json.loads(r["tags"])) if r["tags"] else ()
    return CurrentRegime(
        id=r["id"], regime_type_id=r["regime_type_id"], confidence=r["confidence"] or 0.0, tags=tags
    )


async def _regime_type_alias(db: InvestmentDB, regime_type_id: str) -> str:
    rows = await db.query("SELECT aliases FROM regime_type WHERE id = :id", id=regime_type_id)
    aliases = json.loads(rows[0]["aliases"]) if rows and rows[0]["aliases"] else []
    return str(aliases[0]) if aliases else regime_type_id


async def _history(db: InvestmentDB, ticker: str) -> SeriesHistory:
    rows = await db.query(
        "SELECT ts, level, speed, acceleration FROM market_data WHERE ticker = :t ORDER BY ts",
        t=ticker,
    )
    return SeriesHistory(ts=[r["ts"] for r in rows], rows=rows)


async def _commit_regime(
    db: InvestmentDB, commit: RegimeCommit, confirming_print_ts: str, state: DetectorState
) -> str:
    alias = await _regime_type_alias(db, commit.regime_type_id)
    new_id = f"{alias}-{commit.start_date}"
    trace = (
        f"Mechanical regime detector: candidate '{commit.regime_type_id}' confirmed after "
        "regime_confirm_prints consecutive monthly prints "
        "(docs/ARCHITECTURE.md 'Regime Detection')."
    )
    async with db.transaction():
        await db.append_event(
            type="RegimeEvent",
            source_uc="catch-up",
            source_id=new_id,
            payload={
                "from": commit.previous_regime_type_id,
                "to": commit.regime_type_id,
                "confidence": round(commit.confidence, 1),
                "tags": commit.tags,
            },
            event_date=date.fromisoformat(confirming_print_ts),
        )
        if commit.previous_regime_id is not None:
            await db.command(
                "UPDATE regime SET is_current = 0, end_date = :end_date, updated_at = :updated_at "
                "WHERE id = :id",
                id=commit.previous_regime_id,
                end_date=commit.start_date,
                updated_at=confirming_print_ts,
            )
        await db.upsert_vertex(
            "regime",
            new_id,
            {
                "regime_type_id": commit.regime_type_id,
                "tags": commit.tags,
                "start_date": commit.start_date,
                "end_date": None,
                "confidence": commit.confidence,
                "is_current": True,
                "events": commit.events,
                "trace": trace,
                # PIT-correct: dated at the historical confirming print, not
                # wall-clock "now" — see detector_state.candidate_start_ts
                # comment (schema.py) on why this makes 'detector lag' real.
                "created_at": confirming_print_ts,
                "updated_at": confirming_print_ts,
            },
        )
        # Same transaction as the regime write: the detector watermark can
        # never advance without its regime (or vice-versa). Otherwise a crash
        # between the two would, on restart, reprocess an already-committed
        # print against a now-newer is_current and materialise a duplicate or
        # extra episode.
        await _persist_state(db, state)
    return new_id


async def _update_regime(
    db: InvestmentDB, update: RegimeUpdate, print_ts: str, state: DetectorState
) -> None:
    async with db.transaction():
        await db.append_event(
            type="RegimeEvent",
            source_uc="catch-up",
            source_id=update.regime_id,
            payload={
                "confidence": round(update.confidence, 1),
                "tags": update.tags,
                "reason": "confidence_band_or_tags_changed",
            },
            event_date=date.fromisoformat(print_ts),
        )
        # `events` is intentionally left untouched — frozen at commit as the
        # regime's trigger observations (see RegimeUpdate).
        await db.command(
            "UPDATE regime SET confidence = :confidence, tags = :tags, "
            "updated_at = :updated_at WHERE id = :id",
            id=update.regime_id,
            confidence=update.confidence,
            tags=json.dumps(update.tags),
            updated_at=print_ts,
        )
        # Atomic with the regime update — see `_commit_regime`.
        await _persist_state(db, state)


async def detect(db: InvestmentDB) -> list[RegimeCommit]:
    """The single code path for all four callers (docs/DATA_MODELS.md Regime
    entity): UC0 35y materialization, the Phase 9 replay, the Monday 08:00
    catch-up, and the on-demand UC9 prelude — each just differs in how much
    of `market_data` is already new since the last run (usually 0-1 prints
    for catch-up/UC9; the whole 35y backfill on a fresh DB for UC0/replay).
    Idempotent: a call with no new prints since `detector_state` is a no-op."""
    thresholds = await _load_thresholds(db)
    state = await _load_state(db)

    growth = await _history(db, GROWTH_TICKER)
    inflation = await _history(db, INFLATION_TICKER)
    liquidity = await _history(db, LIQUIDITY_TICKER)
    vix = await _history(db, VIX_TICKER)

    last_growth, last_inflation = state.last_print_ts_growth, state.last_print_ts_inflation
    new_growth = {t for t in growth.ts if last_growth is None or t > last_growth}
    new_inflation = {t for t in inflation.ts if last_inflation is None or t > last_inflation}
    print_dates = sorted(new_growth | new_inflation)
    if not print_dates:
        return []

    current = await _load_current_regime(db)
    commits: list[RegimeCommit] = []

    for ts in print_dates:
        g = growth.asof_smoothed(ts, thresholds.smoothing_months)
        i = inflation.asof_smoothed(ts, thresholds.smoothing_months)
        liq, v = liquidity.asof(ts), vix.asof(ts)

        result = step(
            state=state,
            current=current,
            print_ts=date.fromisoformat(ts),
            growth=g,
            inflation=i,
            liquidity=liq,
            vix_level=v.level,
            thresholds=thresholds,
            growth_raw=growth.asof(ts),
            inflation_raw=inflation.asof(ts),
        )
        state = result.state
        if ts in new_growth:
            state = replace(state, last_print_ts_growth=ts)
        if ts in new_inflation:
            state = replace(state, last_print_ts_inflation=ts)

        # On commit/update the watermark advances INSIDE the regime's own
        # transaction (see `_commit_regime`), so the two can never diverge.
        if result.commit is not None:
            new_id = await _commit_regime(db, result.commit, ts, state)
            current = CurrentRegime(
                id=new_id,
                regime_type_id=result.commit.regime_type_id,
                confidence=result.commit.confidence,
                tags=tuple(result.commit.tags),
            )
            commits.append(result.commit)
        elif result.update is not None:
            assert current is not None  # step() only returns `update` when current is set
            await _update_regime(db, result.update, ts, state)
            current = replace(
                current, confidence=result.update.confidence, tags=tuple(result.update.tags)
            )

    # A trailing run of non-committing prints only advanced the watermark in
    # memory — persist it so the next run doesn't reprocess them. Safe as a
    # standalone write: these prints produce no regime, so there is nothing
    # for the watermark to be inconsistent with (unlike the commit path above).
    await _persist_state(db, state)
    return commits
