"""The Gmail draft's HTML render (`gmail/render.py`).

TWO THINGS ARE UNDER TEST that nothing else in the suite checks: (1) the
`style=` ban — the whole reason this module exists in its particular shape
(feedback-email-draft-never-send: the Gmail draft-composition API silently
strips that attribute) — and (2) that free text pulled from the corpus/LLM
(invariant titles, the Worker's prose, a decision's `reasoning`) is escaped
before it reaches a `<td>`, which the manually-built templates this ported
from never had to do because their data was hand-typed and trusted.

Real throwaway SQLite for `collect_live_trend_snapshot` (CLAUDE.md "Tests" —
no mocks); `render_digest_html` itself is pure and needs no DB.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from investment.db.sqlite import InvestmentDB
from investment.gmail import render
from investment.mechanical.alerts import Alert
from investment.telegram.digest import DigestInputs

TODAY = date(2026, 8, 16)


def _inputs(**overrides: Any) -> DigestInputs:
    base: DigestInputs = DigestInputs(
        regime={"regime_name": "Uncertain", "confidence": 63.3},
        global_liquidity={"level": 96.58, "speed": -0.0},
        ranking=[
            {
                "rank": 1,
                "portfolio_id": "ms-stack",
                "defender": False,
                "allocation": '{"VCIT": 50.0, "cash": 40.0, "IWN": 10.0}',
                "return_3y": 0.531,
                "return_1y": 0.043,
                "return_6m": -0.012,
                "sortino_rolling": 1.63,
                "sharpe_rolling": 1.11,
                "calmar_rolling": 1.94,
                "max_drawdown": -0.077,
                "excluded_from_candidacy": False,
            },
            {
                "rank": 2,
                "portfolio_id": "spy-USD",
                "defender": False,
                "allocation": '{"SPY": 100.0}',
                "return_3y": 0.82,
                "return_1y": 0.217,
                "return_6m": 0.145,
                "sortino_rolling": 1.60,
                "sharpe_rolling": 1.08,
                "calmar_rolling": 1.16,
                "max_drawdown": -0.188,
                "excluded_from_candidacy": False,
            },
            {
                "rank": 3,
                "portfolio_id": "4s-balanced-defender",
                "defender": True,
                "allocation": '{"IEF": 20.0, "TLT": 30.0}',
                "return_3y": 0.385,
                "return_1y": 0.127,
                "return_6m": 0.02,
                "sortino_rolling": 1.09,
                "sharpe_rolling": 0.76,
                "calmar_rolling": 0.5,  # < 1.0 — demoted
                "max_drawdown": -0.07,
                "excluded_from_candidacy": True,
            },
        ],
        invariants=[
            {
                "title": "Inverted yield curve (10y-3m < 0) -> cash outperforms",
                "weight_effective": 0.4,
                "confirmation_count": 7,
                "infirmation_count": 1,
                "author": "dalio",
            }
        ],
        proposal=None,
        scoreboard={"hit_rate": (0, 0), "paper_tests": [], "probations": []},
        defender_metrics={"return_3m": 0.01, "return_6m": 0.02, "return_1y": 0.127},
        alerts=[
            Alert(
                level="critical",
                code="drawdown",
                message="Stack drawdown -26% breaches the -25% rule",
            )
        ],
        stack=None,
        market_signal={
            "held_book": "credit-spread-tight-yield-curve-steep",
            "decision_date": "2026-08-03",
            "gate": "passed",
            "moves": False,
            "reasoning": "spread < median <script>alert(1)</script>",
            "signals": {
                "BAA10Y": {"value": 1.64, "trailing_median": 1.96},
                "T10Y2Y": {"value": 0.45, "trailing_median": 0.41},
            },
            "held_allocation": {"VCIT": 50.0, "cash": 40.0, "IWN": 10.0},
            "target_allocation": {"VCIT": 50.0, "cash": 40.0, "IWN": 10.0},
            "trend_overlay": {
                "windows_days": [150, 300],
                "below_trend": ["GLD", "IEF"],
                "sleeves": {
                    "SPY": {"price": 16288.14, "moving_averages": [14939.69, 14257.06]},
                    "VCIT": {"price": 516.44, "moving_averages": [518.71, 513.48]},
                },
            },
        },
        worker_reading={
            "market_signal_assessment": "This is a <b>bear steepener</b> & VCIT is rolling over.",
            "market_signal_decision_date": "2026-08-03",
        },
        recurring=[{"n": 7, "theme_id": "t1", "titles": ["Extend the overlay to VCIT"]}],
    )
    return DigestInputs(**{**base, **overrides})  # type: ignore[typeddict-item]


def test_no_style_attribute_anywhere() -> None:
    """The one property the whole module exists to guarantee — the Gmail
    draft-composition API silently strips `style=`."""
    html = render.render_digest_html(_inputs(), {}, TODAY)
    assert "style=" not in html


def test_free_text_is_escaped() -> None:
    """The invariant title has a live '<' in the real corpus
    ("10y-3m < 0"), and the Worker's prose/decision reasoning are LLM
    output — none of it is trusted markup, so ANY markup-shaped substring in
    it (even the Worker's own stray '<b>') is escaped like everything else,
    not selectively passed through."""
    html = render.render_digest_html(_inputs(), {}, TODAY)
    assert "10y-3m &lt; 0" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;bear steepener&lt;/b&gt;" in html
    assert "VCIT is rolling over" in html


def test_allocation_weights_are_not_multiplied_by_100() -> None:
    """`allocation` is already 0-100 (DATA_MODELS.md) — the exact bug the
    dashboard's `weight()` formatter carried until 2026-08-16."""
    html = render.render_digest_html(_inputs(), {}, TODAY)
    assert "VCIT 50" in html
    assert "VCIT 5000" not in html


def test_role_badges_are_mutually_exclusive_and_correct() -> None:
    html = render.render_digest_html(_inputs(), {}, TODAY)
    assert "[LIVE PATH]" in html  # ms-stack
    assert "(benchmark)" in html  # spy-USD
    assert "[DEFENDER]" in html  # 4s-balanced-defender


def test_demoted_and_excluded_rows_carry_their_own_flags() -> None:
    """CLAUDE.md's "Ranking rule": Calmar demotion and the drawdown-candidacy
    exclusion are SEPARATE flags with separate mechanisms — both fire on the
    fixture's defender row here (Calmar 0.5, excluded_from_candidacy True)."""
    html = render.render_digest_html(_inputs(), {}, TODAY)
    assert "demoted" in html
    assert "excluded from defender role" in html


def test_a_blocked_gate_is_loud() -> None:
    inputs = _inputs()
    inputs["market_signal"]["gate"] = "max_single_asset_pct"  # type: ignore[index]
    html = render.render_digest_html(inputs, {}, TODAY)
    assert "BLOCKED" in html
    assert "FROZEN" in html


def test_a_stale_worker_reading_says_so() -> None:
    inputs = _inputs()
    inputs["worker_reading"]["market_signal_decision_date"] = "2026-07-06"  # type: ignore[index]
    html = render.render_digest_html(inputs, {}, TODAY)
    assert "NOT the one above" in html


def test_current_values_section_needs_both_a_decision_and_a_live_snapshot() -> None:
    inputs = _inputs()
    assert "Current Values" not in render.render_digest_html(inputs, {}, TODAY)

    live = {
        "signals": {
            "BAA10Y": {"value": 1.67, "as_of": "2026-08-14"},
            "T10Y2Y": {"value": 0.48, "as_of": "2026-08-14"},
        },
        "sleeves": {
            "SPY": {
                "price": 16300.0,
                "as_of": "2026-08-14",
                "moving_averages": {150: 15000.0, 300: 14300.0},
            },
            "VCIT": {
                "price": 515.0,
                "as_of": "2026-08-14",
                "moving_averages": {150: 518.0, 300: 513.0},
            },
        },
    }
    html = render.render_digest_html(inputs, live, TODAY)
    assert "Current Values" in html
    assert "tight" in html  # BAA10Y 1.67 < median 1.96
    assert "steep" in html  # T10Y2Y 0.48 > median 0.41


def test_an_empty_ranking_or_missing_sections_render_without_raising() -> None:
    """A digest must never be the thing that fails on the morning it runs
    (`telegram.digest._stack_block`'s own stated reason for defensive
    formatting) — the same must hold here."""
    inputs = _inputs(
        ranking=[],
        invariants=[],
        recurring=[],
        alerts=[],
        market_signal=None,
        worker_reading=None,
        defender_metrics=None,
    )
    html = render.render_digest_html(inputs, {}, TODAY)
    assert "style=" not in html
    assert "<h1>" in html


# -- collect_live_trend_snapshot ---------------------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[InvestmentDB]:
    conn = InvestmentDB(tmp_path / "gmail_render.db")
    yield conn
    await conn.close()


async def _prices(db: InvestmentDB, ticker: str, levels: list[float]) -> None:
    start = date(2026, 1, 1)
    await db.append_ts_batch(
        "market_data",
        [
            {
                "ts": (start + timedelta(days=i)).isoformat(),
                "ticker": ticker,
                "asset_class": "equities",
                "currency": "USD",
                "level": level,
            }
            for i, level in enumerate(levels)
        ],
    )


DECISION = {
    "signals": {"BAA10Y": {}, "T10Y2Y": {}},
    "trend_overlay": {"sleeves": {"SPY": {}, "VCIT": {}}},
}


async def test_the_live_snapshot_reads_the_latest_price_and_its_own_date(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render, "MA_WINDOWS", (2, 3))
    await _prices(db, "SPY", [100.0, 101.0, 102.0, 103.0])
    await _prices(db, "VCIT", [50.0, 50.0, 50.0, 49.0])
    await _prices(db, "BAA10Y", [1.6, 1.62, 1.64, 1.67])
    await _prices(db, "T10Y2Y", [0.44, 0.45, 0.46, 0.48])

    live = await render.collect_live_trend_snapshot(db, DECISION)

    assert live["sleeves"]["SPY"]["price"] == pytest.approx(103.0)
    assert live["signals"]["BAA10Y"]["value"] == pytest.approx(1.67)
    # rolling(2).mean() over the last two prints: (102+103)/2
    assert live["sleeves"]["SPY"]["moving_averages"][2] == pytest.approx(102.5)
    # rolling(3).mean(): (101+102+103)/3
    assert live["sleeves"]["SPY"]["moving_averages"][3] == pytest.approx(102.0)


async def test_insufficient_history_reports_None_not_a_wrong_average(
    db: InvestmentDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A window that has not warmed up must say so, not silently average
    fewer rows than the window claims — `min_periods=window`, the same
    convention `market_signal.load_series` uses for the decision path."""
    monkeypatch.setattr(render, "MA_WINDOWS", (5,))
    await _prices(db, "SPY", [100.0, 101.0])  # only 2 rows, window is 5
    await _prices(db, "VCIT", [50.0])
    await _prices(db, "BAA10Y", [1.6])
    await _prices(db, "T10Y2Y", [0.44])

    live = await render.collect_live_trend_snapshot(db, DECISION)

    assert live["sleeves"]["SPY"]["moving_averages"][5] is None


async def test_no_decision_means_no_query_and_an_empty_snapshot(db: InvestmentDB) -> None:
    assert await render.collect_live_trend_snapshot(db, None) == {}
