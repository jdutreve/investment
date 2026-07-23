"""Weekly digest render (docs/TASKS.md Task 6bis.1; template in docs/EXAMPLE.md
Steps 8A/8B). Renders the Monday 09:30 digest as text — regime header, ranked
table with the defender starred, key invariants, the proposal block
(reallocation old->new or switch), the scoreboard, and the defender's returns.

PERCENT FORMATTING HAPPENS HERE ONLY (docs/TASKS.md Task 6bis.1): every other
layer keeps decimal fractions; the presentation edge is the single place a
0.038 becomes "+3.8%". Weights stay decimal (they are 0-1 fractions the owner
reads as weights, not percentages — matching the EXAMPLE template).
"""

from typing import Any

from investment.db.sqlite import InvestmentDB


def pct(fraction: float | None, *, signed: bool = False) -> str:
    """A decimal fraction as a percentage string (0.038 -> '3.8%', or '+3.8%'
    signed). `None` -> 'n/a' — an unmeasured value is not zero."""
    if fraction is None:
        return "n/a"
    value = fraction * 100.0
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def _regime_header(regime: dict[str, Any], liquidity: dict[str, Any]) -> list[str]:
    confidence = regime.get("confidence")
    conf = pct(confidence) if isinstance(confidence, int | float) else str(confidence)
    lines = [
        f"📊 Regime: {regime.get('regime_name', '?')} "
        f"({conf} — {regime.get('regime_type_id', '?')})"
    ]
    if liquidity:
        lines.append(
            f"   Global liquidity: level {liquidity.get('level')}, speed {liquidity.get('speed')}"
        )
    return lines


def _ranking_block(ranking: list[dict[str, Any]]) -> list[str]:
    lines = ["", "🏆 Portfolio ranking (Sortino USD, rolling 36M):"]
    for row in ranking:
        star = " ★ (defender)" if row.get("defender") else ""
        sortino = row.get("sortino_rolling")
        calmar = row.get("calmar_rolling")
        line = (
            f"   {row.get('rank')}. {row.get('portfolio_id')}: "
            f"{sortino:.2f}{star}  Calmar {calmar:.1f}"
            if isinstance(sortino, int | float) and isinstance(calmar, int | float)
            else f"   {row.get('rank')}. {row.get('portfolio_id')}{star}"
        )
        dd = row.get("max_drawdown")
        if isinstance(dd, int | float) and row.get("demoted"):
            line += f" ⚠️ (demoted; drawdown {pct(dd)} breaches the rule)"
        lines.append(line)
    return lines


def _invariant_block(invariants: list[dict[str, Any]]) -> list[str]:
    if not invariants:
        return []
    lines = ["", "🔑 Key Invariants (effective weight):"]
    for inv in invariants:
        weight = inv.get("weight_effective")
        weight_str = f"{weight:.3f}" if isinstance(weight, int | float) else "?"
        counts = (
            f" ({inv['confirmation_count']}/"
            f"{inv['confirmation_count'] + inv['infirmation_count']} confirmed)"
            if "confirmation_count" in inv and "infirmation_count" in inv
            else ""
        )
        author = f" [{inv.get('author') or 'system'}]"
        lines.append(f"   • {inv.get('title', '?')}: {weight_str}{counts}{author}")
    return lines


def _proposal_block(proposal: dict[str, Any] | None) -> list[str]:
    if proposal is None:
        return ["", "🟢 No proposal this week — maintain."]
    if proposal.get("proposal_type") == "reallocation":
        current = proposal.get("current_allocation", {})
        proposed = proposal.get("proposed_allocation", {})
        moves = [
            f"{t} {current.get(t, 0):g}→{proposed.get(t, 0):g}"
            for t in sorted(set(current) | set(proposed))
            if current.get(t, 0) != proposed.get(t, 0)
        ]
        lines = [
            "",
            "🔧 Reallocation proposal (paper-test) — defender stays, allocation tilts:",
            "   " + " | ".join(moves),
        ]
    else:
        lines = [
            "",
            f"🔀 Switch proposal ({proposal.get('recommendation', 'monitor')}): "
            f"{proposal.get('challenger_id')} over {proposal.get('defender_id')}",
        ]
    lines.append(f"   Why: {proposal.get('reasoning', '')}")
    return lines


def _scoreboard_block(scoreboard: dict[str, Any]) -> list[str]:
    won, total = scoreboard.get("hit_rate", (0, 0))
    rate = pct(won / total) if total else "n/a"
    lines = ["", "📋 Scoreboard:", f"   Proposals hit-rate: {won}/{total} ({rate}) at +12w"]
    paper = scoreboard.get("paper_tests", [])
    if paper:
        lines.append(f"   Paper-tests in progress: {len(paper)}")
    if scoreboard.get("probations"):
        lines.append(f"   Strategies in probation: {len(scoreboard['probations'])}")
    if scoreboard.get("calibration_flags"):
        lines.append(f"   Scenario calibration flags: {len(scoreboard['calibration_flags'])}")
    return lines


def _defender_block(metrics: dict[str, Any] | None) -> list[str]:
    if not metrics:
        return []
    lines = [
        "",
        f"📈 Defender (USD, 36M): Sharpe {metrics.get('sharpe_rolling', 'n/a')} | "
        f"Sortino {metrics.get('sortino_rolling', 'n/a')} | "
        f"Calmar {metrics.get('calmar_rolling', 'n/a')}",
    ]
    returns = " | ".join(
        f"{label} {pct(metrics.get(key), signed=True)}"
        for label, key in (
            ("3m", "return_3m"),
            ("6m", "return_6m"),
            ("1y", "return_1y"),
            ("3y", "return_3y"),
            ("5y", "return_5y"),
        )
        if metrics.get(key) is not None
    )
    if returns:
        lines.append(f"   Returns: {returns}")
    return lines


def render_digest(
    *,
    regime: dict[str, Any],
    global_liquidity: dict[str, Any],
    ranking: list[dict[str, Any]],
    invariants: list[dict[str, Any]],
    proposal: dict[str, Any] | None,
    scoreboard: dict[str, Any],
    defender_metrics: dict[str, Any] | None = None,
) -> str:
    """The full weekly digest as text (docs/EXAMPLE.md Steps 8A/8B). All the
    percent formatting lives in the block helpers; the inputs are decimal
    fractions."""
    blocks = [
        _regime_header(regime, global_liquidity),
        _ranking_block(ranking),
        _invariant_block(invariants),
        _proposal_block(proposal),
        _scoreboard_block(scoreboard),
        _defender_block(defender_metrics),
    ]
    return "\n".join(line for block in blocks for line in block)


async def build_scoreboard(db: InvestmentDB) -> dict[str, Any]:
    """Assemble the scoreboard from the proposal ledger (docs/TASKS.md Task
    6bis.1): the cumulative +12w hit-rate (won / decided) and the paper-tests
    still in progress. Probation / calibration flags come from OutcomeEvents
    once score_scenarios / strategy_probation_check land (the cycle's other
    functions) — empty here, so the block simply omits them."""
    rows = await db.query(
        "SELECT json_extract(outcome, '$.verdict') AS verdict, paper_started, "
        "json_extract(outcome, '$.verdict') AS v FROM proposal"
    )
    won = sum(1 for r in rows if r["verdict"] == "won")
    lost = sum(1 for r in rows if r["verdict"] == "lost")
    paper = [r for r in rows if r["paper_started"] and r["verdict"] in (None, "pending")]
    return {
        "hit_rate": (won, won + lost),
        "paper_tests": paper,
        "probations": [],
        "calibration_flags": [],
    }
