"""The figures a Worker reading repeats from the digest the owner reads
(src/investment/worker/repeated_figures.py), and the one rewrite turn that
removes them (`worker.agent.run_worker`)."""

from pathlib import Path

from conftest import a_reading
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from investment.db.sqlite import InvestmentDB
from investment.worker.agent import build_worker_agent, run_worker
from investment.worker.repeated_figures import figures_in, repeated_figures
from investment.worker.result import WorkerResult


def test_a_figure_is_a_value_not_a_date_a_count_or_a_name() -> None:
    text = (
        "T10Y2Y 0.38 on 2026-10-31 in 2026, BAA10Y +1.49%, the 150/300d lines, "
        "6 weeks, the 2s10s curve, -0.03 and 95%."
    )
    assert figures_in(text) == {"0.38", "1.49", "150", "300", "0.03", "95"}


def test_a_figure_repeats_whatever_its_sign_or_percent_sign() -> None:
    printed = figures_in("BAA10Y 1.49 vs 10y median 1.95 | cash 65→95")
    reading = "The spread at 1.490% is tight; 95 in cash, and IEF needs 502.4 to re-enter."
    assert repeated_figures(reading, printed) == ["1.49", "95"]


def _output(bottom_line: str) -> str:
    return WorkerResult(
        regime_assessment="calm",
        ranking_commentary="as ranked",
        market_signal_assessment=a_reading(bottom_line),
        reasoning="-",
        scenario_adjustments=[],
        evaluations=[],
        innovations_proposed=[],
    ).model_dump_json()


async def test_a_reading_that_repeats_a_figure_is_sent_back_once(tmp_path: Path) -> None:
    """The first answer restates the spread the digest prints; the Worker is
    told which figure, once, and its second answer is the one kept."""
    db = InvestmentDB(tmp_path / "w.db")
    calls: list[int] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(len(messages))
        if len(calls) == 1:
            return ModelResponse(parts=[TextPart(_output("Cash, with the spread at 1.49."))])
        assert "1.49" in str(messages[-1])  # the rewrite request names the figure
        return ModelResponse(parts=[TextPart(_output("Cash; credit is calm."))])

    try:
        agent = build_worker_agent(db, "test/worker", "sk-test")
        with agent.override(model=FunctionModel(model)):
            result = await run_worker(agent, "ctx", printed=figures_in("BAA10Y 1.49 vs 1.95"))
    finally:
        await db.close()
    assert len(calls) == 2
    assert result.market_signal_assessment.bottom_line == "Cash; credit is calm."


async def test_a_reading_with_no_repeated_figure_is_not_sent_back(tmp_path: Path) -> None:
    db = InvestmentDB(tmp_path / "w.db")
    calls: list[int] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[TextPart(_output("IEF needs 502.4 to re-enter."))])

    try:
        agent = build_worker_agent(db, "test/worker", "sk-test")
        with agent.override(model=FunctionModel(model)):
            await run_worker(agent, "ctx", printed=figures_in("BAA10Y 1.49 vs 1.95"))
    finally:
        await db.close()
    assert len(calls) == 1
