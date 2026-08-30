"""The weekly digest, rendered for the Gmail draft channel.

SAME INPUTS AS THE OTHER TWO FRONTS. `telegram.digest.collect_digest_inputs`
is the one assembly (ADR-005) Telegram and the dashboard already share; this
module adds no second query for anything it already returns. The one thing it
does query itself is `collect_live_trend_snapshot` below — a FRESH read of the
sleeve/signal prices, because the market-signal decision is MONTHLY (CLAUDE.md
"Scheduling") while this mail goes out weekly, and the gap between "what the
decision saw" and "what the market is doing now" is exactly what the owner
cannot get from the decision's own journal row.

LEGACY HTML ONLY — no `style=` attribute anywhere in this file. Found
2026-08-17 by an isolated diagnostic: the Gmail draft-composition API silently
strips the `style=` attribute (not header rows, not any one tag), and a plain
`<table border><th bgcolor><font color>` survives intact. See
`feedback-email-draft-never-send` in the assistant's memory for the full
finding. Every table here uses `_table_open` / `_hrow` / `_drow` for exactly
this reason — a future edit that reaches for `style=` will look right in a
browser and render as unstyled run-on text in the draft.

ESCAPED FREE TEXT ONLY. Portfolio ids, book ids and labels are constrained by
`seed_data`/`market_signal.BOOKS` and never escaped; invariant titles, critique
wordings, the Worker's prose and the decision's `reasoning` are corpus- or
LLM-sourced and always go through `_esc` first — the same rule the dashboard
states for its own rendered text (ADR-005 amendment 2026-08-15)."""

import html as html_lib
import json
from datetime import date
from typing import Any

from investment.db.seed_data import BENCHMARK_PORTFOLIOS
from investment.db.sqlite import InvestmentDB
from investment.market.liquidity import PROXY_DESCRIPTION, STATE_READINGS
from investment.mechanical import ratios
from investment.mechanical.market_signal import (
    CREDIT_SPREAD,
    MA_WINDOWS,
    STACK_PORTFOLIO_ID,
    YIELD_SLOPE,
)
from investment.mechanical.snapshots import is_demoted
from investment.telegram.digest import DigestInputs, pct

_DARK = "#1c1c1a"
_PURPLE = "#753991"
_GRAY = "#f4f4f2"
_WHITE = "#ffffff"
_ALT = "#f9f7ee"
_GREEN = "#1a7f4f"
_RED = "#b42318"
_MUTED = "#999999"
_GOLD = "#a37708"
_BLUE = "#175f80"


def _esc(value: Any) -> str:
    """Corpus/LLM-sourced free text, escaped before it ever reaches a `<td>` —
    the same discipline docs/Dashboard.md states for the dashboard's own
    rendered text. A stray '<' in an invariant title (the live corpus already
    has one: "Inverted yield curve (10y-3m < 0)") would otherwise break the
    row it sits in, not just mis-render it."""
    return html_lib.escape(str(value), quote=False)


def _table_open(width: str = "100%") -> str:
    return (
        f'<table border="1" cellpadding="6" cellspacing="0" width="{width}" bordercolor="#eeeeee">'
    )


def _hrow(cells: list[tuple[str, str]], bg: str = _DARK, fg: str = "#ffffff") -> str:
    """A header row. `cells` are (label, align) — labels are code-owned
    constants, never DB text, so they are not escaped here."""
    tds = "".join(
        f'<th align="{a}" bgcolor="{bg}"><font color="{fg}"><b>{t}</b></font></th>'
        for t, a in cells
    )
    return f'<tr bgcolor="{bg}">{tds}</tr>'


def _drow(cells: list[tuple[str, str, str | None, bool]], bg: str = _WHITE) -> str:
    """A data row. `cells` are (html, align, color, bold) — `html` is ALREADY
    escaped/assembled by the caller (mirrors the manually-verified template
    this ported from), because some cells legitimately mix escaped free text
    with raw markup this module built itself (a badge, a bold wrapper)."""
    tds = []
    for content, align, color, bold in cells:
        inner = f"<b>{content}</b>" if bold else content
        inner = f'<font color="{color}">{inner}</font>' if color else inner
        tds.append(f'<td align="{align}">{inner}</td>')
    return f'<tr bgcolor="{bg}">{"".join(tds)}</tr>'


def _num(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, int | float) else "n/a"


def _g(value: Any) -> str:
    """A signal level (percentage POINTS, not a decimal fraction) — see
    `telegram.digest._g`, the same distinction."""
    return "n/a" if not isinstance(value, int | float) else f"{value:.2f}"


def _price(value: Any) -> str:
    return f"{value:,.2f}" if isinstance(value, int | float) else "n/a"


# -- Regime -------------------------------------------------------------


def _regime_section(regime: dict[str, Any], liquidity: dict[str, Any]) -> list[str]:
    if not regime:
        return []
    confidence = regime.get("confidence")
    conf = f"{confidence:.1f}%" if isinstance(confidence, int | float) else "n/a"
    lines = [
        "<h2>\U0001f4ca Regime</h2>",
        _table_open("60%"),
        _hrow([("Metric", "left"), ("Value", "left")]),
        _drow(
            [
                ("Regime", "left", None, False),
                (_esc(regime.get("regime_name", "?")), "left", None, True),
            ]
        ),
        _drow([("Confidence", "left", None, False), (conf, "left", None, False)], _ALT),
    ]
    if liquidity:
        # THE THIRD CHANNEL SAYS WHAT THE OTHER TWO SAY (ADR-015). These were
        # two bare rows, "level 95.84" and "speed -0.80" — the display the
        # 2026-08-30 pass replaced everywhere else, left standing here because
        # this renderer has its own layout and nothing tied it to the digest's.
        # An owner reading the email would have had the old numbers with none of
        # the state, the units, the freshness or the role.
        state = liquidity.get("state")
        sigma, speed = liquidity.get("sigma"), liquidity.get("speed")
        reading = STATE_READINGS.get(state, "") if isinstance(state, str) else ""
        headline = (
            f"{state.removeprefix('liquidity-').upper()} — {reading}"
            if isinstance(state, str)
            else "n/a"
        )
        level = _num(liquidity.get("level"))
        if isinstance(sigma, int | float):
            side = "under" if sigma < 0 else "over"
            level += f" = {abs(sigma):.2f} sigma {side} its 5y norm"
        change = f"{speed:+.2f} index points" if isinstance(speed, int | float) else "n/a"
        freshness = f"data to {liquidity.get('ts', '?')}"
        if liquidity.get("oldest_component"):
            freshness += (
                f", oldest input {liquidity['oldest_component']} "
                f"{liquidity['oldest_component_days']}d old"
            )
        for i, (label, value) in enumerate(
            (
                ("Global liquidity", _esc(headline)),
                ("Level", _esc(level)),
                ("7d change", _esc(change)),
                (f"Proxy — {_esc(PROXY_DESCRIPTION)} (no China)", _esc(freshness)),
                ("Role", "context only — not validated as an allocation signal on its own"),
            )
        ):
            lines.append(
                _drow(
                    [
                        (label, "left", None, False),
                        (value, "left", None, label == "Global liquidity"),
                    ],
                    _WHITE if i % 2 else _ALT,
                )
            )
    lines.append("</table>")
    return lines


# -- Ranking --------------------------------------------------------------


def _allocation_str(row: dict[str, Any]) -> str:
    """`allocation` is a JSON map of PERCENT weights already 0-100
    (docs/DATA_MODELS.md — the one named exception to the 0-1-fraction
    convention CLAUDE.md states for weight-like fields elsewhere). NOT
    multiplied by 100 here — the bug the dashboard's `weight()` formatter
    carried until 2026-08-16."""
    raw = row.get("allocation")
    if not raw:
        return "n/a"
    try:
        weights = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return "n/a"
    if not isinstance(weights, dict):
        return "n/a"
    parts = [
        f"{_esc(ticker)} {weight:g}"
        for ticker, weight in sorted(weights.items(), key=lambda kv: -kv[1])
        if weight
    ]
    return " / ".join(parts) if parts else "n/a"


def _badge(row: dict[str, Any]) -> str:
    """A row's role, exactly as CLAUDE.md's "Ranking rule" states it: defender
    OR the live path OR a benchmark — never more than one, and a benchmark is
    never the defender (`seed_data.BENCHMARK_PORTFOLIOS` is refused by kind in
    the challenger search)."""
    portfolio_id = str(row.get("portfolio_id", ""))
    if row.get("defender"):
        return f' <font color="{_GOLD}">[DEFENDER]</font>'
    if portfolio_id == STACK_PORTFOLIO_ID:
        return f' <font color="{_GOLD}">[LIVE PATH]</font>'
    if portfolio_id in BENCHMARK_PORTFOLIOS:
        return f' <font color="{_BLUE}">(benchmark)</font>'
    return ""


def _flags(row: dict[str, Any]) -> str:
    """The two SEPARATE ranking flags CLAUDE.md is explicit about: Calmar
    demotion (a sort-order rule) and the user-drawdown breach (a candidacy
    rule). Neither implies the other and both can fire on the same row."""
    flags = []
    calmar = row.get("calmar_rolling")
    if isinstance(calmar, int | float) and is_demoted(calmar):
        flags.append(f"⚠️ demoted (Calmar {calmar:.2f} below 1.0)")
    if row.get("excluded_from_candidacy"):
        flags.append("⛔ excluded from defender role and proposal candidacy")
    return f' <font color="{_RED}" size="2">{" · ".join(flags)}</font>' if flags else ""


def _ranking_section(ranking: list[dict[str, Any]], today: date) -> list[str]:
    if not ranking:
        return []
    lines = [
        "<h2>\U0001f3c6 Portfolio Ranking</h2>",
        (
            '<p><font color="#888888" size="2">Sortino USD, rolling 36M. Allocation and '
            f"Deepest Drawdown as of {today.isoformat()}. 3y/1y/6m are calendar-window "
            "returns; NAV omitted — not comparable across rows with different "
            "inception dates.</font></p>"
        ),
        _table_open(),
        _hrow(
            [
                ("#", "left"),
                ("Portfolio", "left"),
                ("Allocation", "left"),
                ("3y", "right"),
                ("1y", "right"),
                ("6m", "right"),
                ("Sortino", "right"),
                ("Sharpe", "right"),
                ("Calmar", "right"),
                ("Deepest DD", "right"),
            ]
        ),
    ]
    for i, row in enumerate(ranking):
        bg = _WHITE if i % 2 == 0 else _ALT
        r3y, r1y, r6m = row.get("return_3y"), row.get("return_1y"), row.get("return_6m")
        drawdown = row.get("max_drawdown")
        name_html = (
            f"<b>{_esc(row['portfolio_id'])}</b>"
            if row.get("defender")
            else _esc(row.get("portfolio_id", "?"))
        )
        lines.append(
            _drow(
                [
                    (str(row.get("rank", "?")), "left", _MUTED, False),
                    (name_html + _badge(row) + _flags(row), "left", None, False),
                    (_allocation_str(row), "left", _MUTED, False),
                    (
                        pct(r3y, signed=True),
                        "right",
                        _GREEN if isinstance(r3y, int | float) and r3y >= 0 else _RED,
                        True,
                    ),
                    (pct(r1y, signed=True), "right", _MUTED, False),
                    (
                        pct(r6m, signed=True),
                        "right",
                        _GREEN if isinstance(r6m, int | float) and r6m >= 0 else _RED,
                        False,
                    ),
                    (_num(row.get("sortino_rolling")), "right", None, False),
                    (_num(row.get("sharpe_rolling")), "right", None, False),
                    (_num(row.get("calmar_rolling")), "right", None, False),
                    (pct(drawdown) if drawdown is not None else "n/a", "right", _RED, False),
                ],
                bg,
            )
        )
    lines.append("</table>")
    return lines


# -- Market-signal decision + Worker challenge -----------------------------


def _decision_target_table(decision: dict[str, Any]) -> list[str]:
    held = decision.get("held_allocation") or {}
    target = decision.get("target_allocation") or {}
    tickers = sorted(set(held) | set(target))
    if not tickers:
        return []
    lines = [
        _table_open("60%"),
        _hrow([("Ticker", "left"), ("Held %", "right"), ("Target %", "right")]),
    ]
    for i, ticker in enumerate(tickers):
        bg = _WHITE if i % 2 == 0 else _ALT
        h, t = held.get(ticker, 0), target.get(ticker, 0)
        lines.append(
            _drow(
                [
                    (_esc(ticker), "left", None, False),
                    (f"{h:g}", "right", None, False),
                    (f"{t:g}", "right", None, t != h),
                ],
                bg,
            )
        )
    lines.append("</table>")
    return lines


def _market_signal_section(
    decision: dict[str, Any] | None, worker_reading: dict[str, Any] | None, *, has_live: bool
) -> list[str]:
    if not decision:
        return []
    gate = decision.get("gate")
    blocked = bool(gate) and gate != "passed"
    label = "target book" if blocked else "book"
    changed = (
        "\U0001f504 bridge proposal this week"
        if decision.get("moves")
        else "\U0001f7e2 no bridge proposal this week, maintain"
    )
    lines = [
        "<h2>\U0001f9ed Market-Signal Decision (paper-test)</h2>",
        f"<p>{label.capitalize()} <b>{_esc(decision.get('held_book', '?'))}</b> — decided "
        f"<b>{_esc(decision.get('decision_date', '?'))}</b> — {changed}.</p>",
        *_decision_target_table(decision),
    ]
    if blocked:
        lines.append(
            f'<p><font color="{_RED}"><b>\U0001f6a8 BLOCKED by gate'
            f" '{_esc(gate)}'</b> — nothing was "
            "proposed. The stack is FROZEN in its previous book, not in the one named "
            "above.</font></p>"
        )
    overlay = decision.get("trend_overlay") or {}
    below = overlay.get("below_trend") or []
    windows = overlay.get("windows_days") or list(MA_WINDOWS)
    window_text = "/".join(str(w) for w in windows)
    overlay_line = (
        f"{window_text}d overlay at decision time: {', '.join(below)} below trend → redirected."
        if below
        else f"{window_text}d overlay at decision time: clear."
    )
    # THE FORWARD REFERENCE ONLY WHEN THE TABLE IT POINTS AT ACTUALLY RENDERS
    # ("see 'Current Values' table below") — `_current_values_section` needs
    # a live snapshot to exist at all, and a digest morning where that query
    # comes back empty must not send the owner looking for a table that isn't
    # there. The same defect class CLAUDE.md names for the line that once said
    # "redirected to IEF" while the target was cash: a sentence that claims
    # something the render did not actually do.
    signal_tickers = ", ".join(sorted((decision.get("signals") or {}).keys()))
    signal_line = f"{overlay_line} Signal readings ({signal_tickers})"
    if has_live:
        signal_line += ': see "Current Values" table below — not repeated here.'
    else:
        signal_line += "."
    lines.append(f'<p><font color="#888888" size="2">{signal_line}</font></p>')
    # A CORRECTION IS PART OF THE DECISION here too — the email renders the same
    # payload as the digest and dropped this field the same way, so a decision
    # corrected after the fact read as an untouched one on the third channel.
    if decision.get("correction_note"):
        lines.append(f"<p><b>\U0001f6e0 Corrected:</b> {_esc(decision['correction_note'])}</p>")
    if decision.get("reasoning"):
        lines.append(f"<p><b>Why:</b> {_esc(decision['reasoning'])}</p>")
    assessment = str((worker_reading or {}).get("market_signal_assessment") or "").strip()
    if assessment:
        read_date = (worker_reading or {}).get("market_signal_decision_date")
        decision_date = decision.get("decision_date")
        stale = bool(decision_date) and bool(read_date) and read_date != decision_date
        suffix = (
            f" (reading of the {_esc(read_date)} decision — NOT the one above)" if stale else ""
        )
        lines.append(
            f'<table border="0" cellpadding="12" cellspacing="0" width="100%" '
            f'bgcolor="{_GRAY}"><tr><td>'
            f"<b>\U0001f5e3 Worker challenge{suffix}:</b> {_esc(assessment)}"
            "</td></tr></table>"
        )
    return lines


# -- Current values (live, weekly) ------------------------------------------


async def collect_live_trend_snapshot(
    db: InvestmentDB, decision: dict[str, Any] | None
) -> dict[str, Any]:
    """Fresh sleeve/signal reads as of the latest knowable close, alongside the
    decision's own AT-DECISION numbers (`trend_overlay.sleeves`, `signals`).

    WHY THIS EXISTS. The decision is MONTHLY; this mail is WEEKLY. On every
    holding week (most of them — ~9 out of 12, ADR-007) the decision block
    alone shows a reading that is up to a month stale, while the catch-up job
    has already refreshed `market_data` for the week. This queries the SAME
    price series `mechanical.market_signal.load_series` builds its moving
    averages from (`ratios.load_price`, `.rolling(window, min_periods=window)`
    over an INTEGER count of trading-day rows, not a calendar window — see
    that module's own note on the distinction), just unshifted and read at the
    latest available row instead of the decision's previous-close."""
    if not decision:
        return {}
    overlay = decision.get("trend_overlay") or {}
    sleeve_tickers = sorted((overlay.get("sleeves") or {}).keys())
    signal_tickers = sorted((decision.get("signals") or {}).keys())

    sleeves: dict[str, dict[str, Any]] = {}
    for ticker in sleeve_tickers:
        series = await ratios.load_price(db, ticker)
        if series.empty:
            continue
        mas = {}
        for window in MA_WINDOWS:
            rolling = series.rolling(window, min_periods=window).mean()
            last = rolling.iloc[-1] if not rolling.empty else None
            mas[window] = float(last) if last is not None and last == last else None  # NaN check
        sleeves[ticker] = {
            "price": float(series.iloc[-1]),
            "as_of": series.index[-1].date().isoformat(),
            "moving_averages": mas,
        }

    signals: dict[str, dict[str, Any]] = {}
    for ticker in signal_tickers:
        series = await ratios.load_price(db, ticker)
        if series.empty:
            continue
        signals[ticker] = {
            "value": float(series.iloc[-1]),
            "as_of": series.index[-1].date().isoformat(),
        }
    return {"sleeves": sleeves, "signals": signals}


def _signal_label(ticker: str, value: float | None, median: float | None) -> str:
    """A two-word descriptor matching `market_signal.BOOKS`' own naming
    (credit-spread-tight/-wide, yield-curve-steep/-flat) — cosmetic only,
    drives no decision; the decision's own hysteresis is the authority on the
    book."""
    if value is None or median is None:
        return "?"
    if ticker == CREDIT_SPREAD:
        return "tight" if value < median else "wide"
    if ticker == YIELD_SLOPE:
        return "steep" if value > median else "flat"
    return "?"


def _current_values_section(
    decision: dict[str, Any] | None, live: dict[str, Any], today: date
) -> list[str]:
    if not decision or not live:
        return []
    decision_date = decision.get("decision_date", "?")
    lines = [
        f"<h2>\U0001f504 Current Values — today, {today.isoformat()}</h2>",
        '<p><font color="#888888" size="2">Not a new decision — this is what has moved '
        f"since {_esc(decision_date)}, for context only.</font></p>",
    ]
    signals_at_decision = decision.get("signals") or {}
    live_signals = live.get("signals") or {}
    if live_signals:
        lines.append(_table_open())
        lines.append(
            _hrow(
                [
                    ("Signal", "left"),
                    (f"At Decision ({_esc(decision_date)})", "right"),
                    ("Now", "right"),
                    ("10y Median", "right"),
                    ("Still", "left"),
                ],
                bg=_PURPLE,
            )
        )
        for i, ticker in enumerate(sorted(live_signals)):
            bg = _WHITE if i % 2 == 0 else _ALT
            at_decision = (signals_at_decision.get(ticker) or {}).get("value")
            median = (signals_at_decision.get(ticker) or {}).get("trailing_median")
            now = live_signals[ticker]
            lines.append(
                _drow(
                    [
                        (_esc(ticker), "left", None, False),
                        (_g(at_decision), "right", _MUTED, False),
                        (f"{_g(now['value'])} ({now['as_of']})", "right", None, True),
                        (_g(median), "right", None, False),
                        (_signal_label(ticker, now["value"], median), "left", None, False),
                    ],
                    bg,
                )
            )
        lines.append("</table>")

    live_sleeves = live.get("sleeves") or {}
    if live_sleeves:
        # The decision's own `windows_days` names the columns — not re-derived,
        # so an old row's int `window_days` and a new row's list `windows_days`
        # (CLAUDE.md's "second one arrives" note on this exact field) both fall
        # back to the live `MA_WINDOWS` constant the same way `_market_signal_section` does.
        window_labels = (decision.get("trend_overlay") or {}).get("windows_days") or list(
            MA_WINDOWS
        )
        lines.append(_table_open())
        header = [("Sleeve", "left"), ("Price", "right")]
        for w in window_labels:
            header += [(f"{w}d Avg", "right"), (f"vs {w}d", "left")]
        lines.append(_hrow(header, bg=_PURPLE))
        for i, ticker in enumerate(sorted(live_sleeves)):
            bg = _WHITE if i % 2 == 0 else _ALT
            now = live_sleeves[ticker]
            cells: list[tuple[str, str, str | None, bool]] = [
                (_esc(ticker), "left", None, False),
                (_price(now["price"]), "right", None, False),
            ]
            for w in window_labels:
                ma = (now.get("moving_averages") or {}).get(w)
                cells.append((_price(ma), "right", None, False))
                if ma is None:
                    cells.append(("n/a", "left", None, False))
                else:
                    above = now["price"] >= ma
                    cells.append(
                        ("above" if above else "below", "left", _GREEN if above else _RED, True)
                    )
            lines.append(_drow(cells, bg))
        lines.append("</table>")
    return lines


# -- Invariants / critiques / scoreboard / defender --------------------------


def _invariants_section(invariants: list[dict[str, Any]]) -> list[str]:
    if not invariants:
        return []
    lines = [
        "<h2>\U0001f511 Key Invariants</h2>",
        _table_open(),
        _hrow(
            [
                ("Invariant", "left"),
                ("Weight", "right"),
                ("Confirmed", "right"),
                ("Author", "left"),
            ],
            bg=_GRAY,
            fg=_DARK,
        ),
    ]
    for i, inv in enumerate(invariants):
        bg = _WHITE if i % 2 == 0 else _ALT
        weight = inv.get("weight_effective")
        confirmed = inv.get("confirmation_count")
        infirmed = inv.get("infirmation_count")
        counts = (
            f"{confirmed}/{confirmed + infirmed}"
            if isinstance(confirmed, int) and isinstance(infirmed, int)
            else "n/a"
        )
        lines.append(
            _drow(
                [
                    (_esc(inv.get("title", "?")), "left", None, False),
                    (
                        f"{weight:.3f}" if isinstance(weight, int | float) else "n/a",
                        "right",
                        None,
                        False,
                    ),
                    (counts, "right", None, False),
                    (_esc(inv.get("author") or "system"), "left", None, False),
                ],
                bg,
            )
        )
    lines.append("</table>")
    return lines


def _recurring_section(recurring: list[dict[str, Any]]) -> list[str]:
    if not recurring:
        return []
    lines = [
        "<h2>\U0001f501 Recurring Critiques</h2>",
        '<p><font color="#888888" size="2">Distinct wordings across weeks — worth measuring, '
        "not yet acted on.</font></p>",
        _table_open(),
        _hrow([("Count", "left"), ("Critique (distinct wordings)", "left")], bg=_GRAY, fg=_DARK),
    ]
    for i, row in enumerate(sorted(recurring, key=lambda r: -int(r["n"]))):
        bg = _WHITE if i % 2 == 0 else _ALT
        for title in row.get("titles", []):
            lines.append(
                _drow(
                    [
                        (f"{row['n']}×", "left", None, True),  # noqa: RUF001 — matches Telegram's digest
                        (_esc(title), "left", None, False),
                    ],
                    bg,
                )
            )
    lines.append("</table>")
    return lines


def _scoreboard_defender_section(
    scoreboard: dict[str, Any], defender_metrics: dict[str, Any] | None
) -> list[str]:
    won, total = scoreboard.get("hit_rate", (0, 0))
    rate = pct(won / total) if total else "n/a"
    lines = [
        "<h2>\U0001f4cb Scoreboard &amp; Defender</h2>",
        '<table border="0" cellpadding="0" cellspacing="0" width="100%">'
        '<tr valign="top"><td width="50%">',
        _table_open(),
        _hrow([("Scoreboard Metric", "left"), ("Value", "right")]),
        _drow(
            [
                ("Hit-rate @+12w", "left", None, False),
                (f"{won}/{total} ({rate})", "right", None, False),
            ]
        ),
        _drow(
            [
                ("Paper-tests running", "left", None, False),
                (str(len(scoreboard.get("paper_tests", []))), "right", None, False),
            ],
            _ALT,
        ),
    ]
    if scoreboard.get("probations"):
        lines.append(
            _drow(
                [
                    ("Strategies in probation", "left", None, False),
                    (str(len(scoreboard["probations"])), "right", None, False),
                ]
            )
        )
    lines.append('</table></td><td width="4%"></td><td width="46%">')
    if defender_metrics:
        lines.append(_table_open())
        lines.append(_hrow([("Defender Period", "left"), ("Return", "right")]))
        periods = (
            ("3m", "return_3m"),
            ("6m", "return_6m"),
            ("1y", "return_1y"),
            ("3y", "return_3y"),
            ("5y", "return_5y"),
        )
        for i, (label, key) in enumerate(periods):
            bg = _WHITE if i % 2 == 0 else _ALT
            value = defender_metrics.get(key)
            if value is None:
                continue
            lines.append(
                _drow(
                    [(label, "left", None, False), (pct(value, signed=True), "right", None, False)],
                    bg,
                )
            )
        lines.append("</table>")
    lines.append("</td></tr></table>")
    return lines


def _alerts_section(alerts: list[Any]) -> list[str]:
    if not alerts:
        return []
    lines = [f'<p><font color="{_RED}">']
    for alert in alerts:
        icon = "\U0001f6a8" if getattr(alert, "level", "warn") == "critical" else "⚠️"
        lines.append(f"{icon} {_esc(alert.message)}<br>")
    lines.append("</font></p>")
    return lines


# -- Top-level render ---------------------------------------------------------


def render_digest_html(inputs: DigestInputs, live: dict[str, Any], today: date) -> str:
    """The weekly digest as legacy HTML, ready to be the `htmlBody` of a Gmail
    draft. `live` is `collect_live_trend_snapshot`'s output — pass `{}` when
    there is no decision yet (before the stack's first monthly cycle)."""
    parts = ["<div>"]
    parts.append(f"<h1>\U0001f4ca Investment Digest — {today.isoformat()}</h1>")
    parts.append(
        '<p><font color="#666666">Weekly chain output. Everything below is PAPER — V1 '
        f"executes nothing. Ranking snapshot dated {today.isoformat()} (when the job ran); "
        "the market data under every figure stops at the prior close — markets do not "
        "trade weekends.</font></p>"
    )
    parts += _alerts_section(inputs.get("alerts") or [])
    parts += _regime_section(inputs.get("regime") or {}, inputs.get("global_liquidity") or {})
    parts += _ranking_section(inputs.get("ranking") or [], today)
    parts += _market_signal_section(
        inputs.get("market_signal"), inputs.get("worker_reading"), has_live=bool(live)
    )
    parts += _current_values_section(inputs.get("market_signal"), live, today)
    parts += _invariants_section(inputs.get("invariants") or [])
    parts += _recurring_section(inputs.get("recurring") or [])
    parts += _scoreboard_defender_section(
        inputs.get("scoreboard") or {}, inputs.get("defender_metrics")
    )
    parts.append(
        '<p><font color="#aaaaaa" size="2">Paper series throughout — V1 executes nothing '
        f"(ADR-006). Generated {today.isoformat()}.</font></p>"
    )
    parts.append("</div>")
    html = "\n".join(parts)
    assert "style=" not in html, (
        "a `style=` attribute crept in — the Gmail composer strips it silently"
    )
    return html
