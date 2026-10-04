"""Read-only duration-gate A/B; protocol in the adjacent PREREGISTRATION.md.

Run with the project's Python, passing a live DB or --inputs exported_inputs.csv.
The latter reproduces the stored data vintage without opening a database.
Research only: no production globals, schema, proposals or DB rows are mutated.
"""

import argparse
import asyncio
import dataclasses
import hashlib
import json
import math
import sqlite3
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd

from investment.db.sqlite import InvestmentDB
from investment.mechanical import market_signal as ms
from investment.mechanical import ratios, replay
from investment.mechanical import rule_revision as rr

PRIMARY_THRESHOLD = 0.20
SENSITIVITY_THRESHOLDS = (0.10, 0.30)
DURATION_SLEEVES = ("IEF", "VCIT")
REPO = Path(__file__).resolve().parents[3]


class ReadOnlySnapshot:
    """Minimal query reader; hold one transaction across all input reads.

    Do not instantiate InvestmentDB on the live file: it is a writing wrapper
    with schema initialization, and the running agent is the sole writer.
    """

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only = ON")
        self.connection.execute("BEGIN")

    async def query(self, sql: str, **parameters: Any) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(sql, parameters)]

    def close(self) -> None:
        self.connection.close()


def duration_target(target: dict[str, float], speed: float, threshold: float) -> dict[str, float]:
    """Change only final IEF/VCIT weights; NaN is unavailable, not calm."""
    if not math.isfinite(speed):
        raise ValueError("duration candidate needs a knowable DGS10 speed at every decision")
    if speed <= threshold:
        return dict(target)
    result = {ticker: weight for ticker, weight in target.items() if ticker not in DURATION_SLEEVES}
    released = sum(target.get(ticker, 0.0) for ticker in DURATION_SLEEVES)
    if released:
        result[ratios.CASH_TICKER] = result.get(ratios.CASH_TICKER, 0.0) + released
    return result


def gated_decisions(
    decisions: list[ms.Decision], speed: pd.Series, threshold: float
) -> list[ms.Decision]:
    """Keep the original decision state, but recompute variant change points.

    The first monthly decision after an exit must be allowed to re-enter even
    when the baseline target did not change; copying baseline.changed is wrong.
    """
    output: list[ms.Decision] = []
    previous: dict[str, float] | None = None
    for decision in decisions:
        target = duration_target(decision.target, float(speed.loc[decision.date]), threshold)
        assert math.isclose(sum(target.values()), 100.0, abs_tol=1e-9)
        assert all(weight >= 0 for weight in target.values())
        for ticker in set(target) | set(decision.target):
            if ticker not in (*DURATION_SLEEVES, ratios.CASH_TICKER):
                assert target.get(ticker, 0.0) == decision.target.get(ticker, 0.0)
        output.append(dataclasses.replace(decision, target=target, changed=target != previous))
        previous = target
    return output


def price_decisions(
    decisions: list[ms.Decision], series: ms.StackSeries, cost_bps: float
) -> ms.MarketSignalRun:
    targets = {decision.date: decision.target for decision in decisions if decision.changed}
    nav, turnover = replay.shadow_book_nav(
        targets, series.prices, series.rf, cost_bps, series.calendar
    )
    return ms.MarketSignalRun(nav, targets, turnover, decisions, {})


def input_frame(series: ms.StackSeries) -> pd.DataFrame:
    return pd.DataFrame(
        {
            **{f"price:{ticker}": prices for ticker, prices in series.prices.items()},
            ms.CREDIT_SPREAD: series.spread_raw,
            ms.YIELD_SLOPE: series.slope_raw,
            ms.LONG_YIELD: series.long_yield_raw,
            "rf_daily": series.rf,
        }
    ).sort_index()


async def load_export(path: Path) -> ms.StackSeries:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)

    class ExportReader:
        async def query(self, _sql: str, **parameters: Any) -> list[dict[str, Any]]:
            ticker = parameters["t"]
            if ticker == ratios.RF_TICKER:
                values = ((1 + frame["rf_daily"].dropna()) ** 252 - 1) * 100
            else:
                column = f"price:{ticker}" if f"price:{ticker}" in frame else ticker
                values = frame[column].dropna() if column in frame else pd.Series(dtype=float)
            return [
                {"ts": ts.date().isoformat(), "level": float(value)} for ts, value in values.items()
            ]

    return await ms.load_series(cast("InvestmentDB", ExportReader()))


def target_frame(decisions: list[ms.Decision], index: pd.DatetimeIndex) -> pd.DataFrame:
    frame = pd.DataFrame({d.date: d.target for d in decisions}).T.fillna(0.0)
    return frame.reindex(index).ffill().fillna(0.0)


def window_report(
    baseline: ms.MarketSignalRun,
    variant: ms.MarketSignalRun,
    series: ms.StackSeries,
    start: date,
    end: date,
) -> dict[str, Any]:
    base = baseline.nav.loc[str(start) : str(end)]
    candidate = variant.nav.reindex(base.index)
    if len(base) < 2:
        return {"status": "too_short"}
    base_metrics = replay.nav_metrics(base, series.rf)
    variant_metrics = replay.nav_metrics(candidate, series.rf)
    evidence = rr.measure_evidence(base, candidate, series.rf)
    measurement = rr.RevisionMeasurement(
        overrides={"research_only_duration_gate": True},
        baseline=base_metrics,
        variant=variant_metrics,
        baseline_turnover=baseline.turnover,
        variant_turnover=variant.turnover,
        evidence=evidence,
    )
    bweights = target_frame(baseline.decisions, base.index)
    vweights = target_frame(variant.decisions, base.index)
    relevant = [d for d in baseline.decisions if start <= d.date.date() <= end]
    vmap = {d.date: d for d in variant.decisions}
    affected = [d for d in relevant if d.target != vmap[d.date].target]
    return {
        "start": base.index[0].date().isoformat(),
        "end": base.index[-1].date().isoformat(),
        "nav_observations": len(base),
        "baseline": base_metrics.as_map(),
        "variant": variant_metrics.as_map(),
        "deltas": measurement.deltas,
        "project_verdict": measurement.verdict,
        "evidence": None if evidence is None else dataclasses.asdict(evidence),
        "monthly_decisions": len(relevant),
        "modified_monthly_targets": len(affected),
        "baseline_mean_target_cash_pct": float(bweights.get("cash", pd.Series([0.0])).mean()),
        "variant_mean_target_cash_pct": float(vweights.get("cash", pd.Series([0.0])).mean()),
        "baseline_total_return": float(base.iloc[-1] / base.iloc[0] - 1),
        "variant_total_return": float(candidate.iloc[-1] / candidate.iloc[0] - 1),
    }


def provenance(series: ms.StackSeries) -> dict[str, Any]:
    paths = [
        "src/investment/mechanical/market_signal.py",
        "src/investment/mechanical/replay.py",
        "src/investment/mechanical/ratios.py",
        "src/investment/mechanical/backtests.py",
        "src/investment/mechanical/rule_revision.py",
        "src/investment/market/derivatives.py",
        str(Path(__file__).resolve().relative_to(REPO)),
        str(Path(__file__).with_name("PREREGISTRATION.md").relative_to(REPO)),
    ]
    return {
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "source_sha256": {
            path: hashlib.sha256((REPO / path).read_bytes()).hexdigest() for path in paths
        },
        "calendar_start": series.calendar[0].date().isoformat(),
        "calendar_end": series.calendar[-1].date().isoformat(),
        "books": ms.BOOKS,
        "ma_windows": ms.MA_WINDOWS,
        "confirmation_decisions": ms.CONFIRM_DECISIONS,
        "spread_speed_veto": ms.SPREAD_SPEED_VETO,
        "spread_stress_sleeve_gate": ms.SPREAD_STRESS_SLEEVE_GATE,
        "slope_bear_veto": ms.SLOPE_BEAR_VETO,
        "slope_speed_veto": ms.SLOPE_SPEED_VETO,
        "speed_lookback_days": ms.SPEED_LOOKBACK_DAYS,
        "cost_bps_per_order": ms.COST_BPS,
        "bootstrap_draws": rr.EVIDENCE_DRAWS,
        "bootstrap_seed": rr.EVIDENCE_SEED,
        "raw_last_dates": {
            ticker: values.last_valid_index().date().isoformat()
            for ticker, values in {
                **series.prices,
                ms.CREDIT_SPREAD: series.spread_raw,
                ms.YIELD_SLOPE: series.slope_raw,
                ms.LONG_YIELD: series.long_yield_raw,
                "rf_daily": series.rf,
            }.items()
        },
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?", type=Path)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("results"))
    args = parser.parse_args()
    if (args.db is None) == (args.inputs is None):
        parser.error("provide either DB or --inputs, not both")
    if args.inputs is not None:
        series = await load_export(args.inputs)
    else:
        reader = ReadOnlySnapshot(args.db)
        try:
            series = await ms.load_series(cast("InvestmentDB", reader))
        finally:
            reader.close()
    if series.long_yield_raw.empty or series.missing_signals:
        raise ValueError("missing market signal or DGS10; refuse a silent no-op comparison")
    latest = series.calendar[-1].date()
    if latest <= ms.PINNED_WINDOW[1]:
        raise ValueError("protocol expects post-pinned data for its tail diagnostic")
    args.output.mkdir(parents=True, exist_ok=True)
    inputs_path = args.output / "inputs.csv"
    input_frame(series).to_csv(inputs_path, index_label="date", float_format="%.17g")
    report: dict[str, Any] = {
        "provenance": provenance(series),
        "inputs_sha256": hashlib.sha256(inputs_path.read_bytes()).hexdigest(),
        "primary_threshold_pp": PRIMARY_THRESHOLD,
        "sensitivity_thresholds_pp": SENSITIVITY_THRESHOLDS,
        "variants": {},
    }
    windows = {
        "pinned_full": ms.PINNED_WINDOW,
        "early_half": (ms.PINNED_WINDOW[0], date(2008, 12, 31)),
        "late_half": (date(2009, 1, 1), ms.PINNED_WINDOW[1]),
        "through_latest": (ms.PINNED_WINDOW[0], latest),
        "post_pinned_tail": (ms.PINNED_WINDOW[1], latest),
        **{
            f"year_{year}": (date(year, 1, 1), date(year, 12, 31))
            for year in (1994, 2008, 2020, 2022)
        },
    }
    # Exactly one original walk supplies the book state and post-overlay targets.
    baseline = await ms.run_market_signal(cast("InvestmentDB", None), end=latest, series=series)
    for threshold in (PRIMARY_THRESHOLD, *SENSITIVITY_THRESHOLDS):
        decisions = gated_decisions(baseline.decisions, series.long_yield_speed, threshold)
        variant = price_decisions(decisions, series, ms.COST_BPS)
        assert baseline.nav.index.equals(variant.nav.index)
        label = f"{threshold:.2f}"
        comparisons = {
            name: window_report(baseline, variant, series, start, end)
            for name, (start, end) in windows.items()
        }
        report["variants"][label] = {
            "windows": comparisons,
            "baseline_target_change_turnover_total": baseline.turnover,
            "variant_target_change_turnover_total": variant.turnover,
            "baseline_target_change_count": len(baseline.targets),
            "variant_target_change_count": len(variant.targets),
        }
        for name in ("pinned_full", "early_half", "late_half", "through_latest"):
            row = comparisons[name]
            bm, vm = row["baseline"], row["variant"]
            print(
                f"threshold={label} {name:15} "
                f"CAGR {bm['cagr']:.4%} -> {vm['cagr']:.4%}; "
                f"Sortino {bm['sortino']:.4f} -> {vm['sortino']:.4f}; "
                f"maxDD {bm['max_drawdown']:.4%} -> {vm['max_drawdown']:.4%}; "
                f"{row['project_verdict']}",
                flush=True,
            )
        if threshold != PRIMARY_THRESHOLD:
            continue
        pd.DataFrame({"baseline": baseline.nav, "duration_gate": variant.nav}).to_csv(
            args.output / "nav.csv", index_label="date", float_format="%.17g"
        )
        baseline_years = baseline.nav.resample("YE").last()
        variant_years = variant.nav.resample("YE").last()
        baseline_anchors = baseline_years.shift(1).fillna(baseline.nav.iloc[0])
        variant_anchors = variant_years.shift(1).fillna(variant.nav.iloc[0])
        pd.DataFrame(
            {
                "baseline_return": baseline_years / baseline_anchors - 1,
                "variant_return": variant_years / variant_anchors - 1,
            }
        ).to_csv(args.output / "calendar_returns.csv", index_label="year_end")
        journal = []
        for base_decision, candidate in zip(baseline.decisions, decisions, strict=True):
            t = base_decision.date
            speed = float(series.long_yield_speed.loc[t])
            journal.append(
                {
                    "date": t.date().isoformat(),
                    "signalled": base_decision.signalled,
                    "held": base_decision.held,
                    "dgs10_speed_pp": speed,
                    "yield_rise_triggered": speed > threshold,
                    "target_modified": base_decision.target != candidate.target,
                    "released_to_cash_pct": sum(
                        base_decision.target.get(k, 0.0) for k in DURATION_SLEEVES
                    )
                    if speed > threshold
                    else 0.0,
                    **{
                        f"base:{k}": base_decision.target.get(k, 0.0)
                        for k in (*ms.STACK_TICKERS, "cash")
                    },
                    **{
                        f"variant:{k}": candidate.target.get(k, 0.0)
                        for k in (*ms.STACK_TICKERS, "cash")
                    },
                }
            )
        pd.DataFrame(journal).to_csv(args.output / "decisions.csv", index=False)
        block_evidence = {}
        # Research-only bootstrap setting is restored; no allocation knob moves.
        original_block = rr.EVIDENCE_BLOCK_DAYS
        try:
            for block in (21, 63, 126):
                rr.EVIDENCE_BLOCK_DAYS = block
                start, end = ms.PINNED_WINDOW
                evidence = rr.measure_evidence(
                    baseline.nav.loc[str(start) : str(end)],
                    variant.nav.loc[str(start) : str(end)],
                    series.rf,
                )
                block_evidence[str(block)] = (
                    None if evidence is None else dataclasses.asdict(evidence)
                )
        finally:
            rr.EVIDENCE_BLOCK_DAYS = original_block
        report["primary_block_sensitivity"] = block_evidence
        gross_baseline = price_decisions(baseline.decisions, series, 0.0)
        gross_variant = price_decisions(decisions, series, 0.0)
        start, end = ms.PINNED_WINDOW
        report["primary_zero_cost_diagnostic"] = window_report(
            gross_baseline, gross_variant, series, start, end
        )
    (args.output / "metrics.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Saved {args.output}; live database untouched.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
