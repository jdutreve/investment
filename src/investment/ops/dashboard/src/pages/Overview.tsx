import { useQuery } from "@tanstack/react-query";
import { Bell, Compass, Gauge, TrendingUp } from "lucide-react";
import { Link } from "react-router-dom";
import {
  get,
  type AssetMove,
  type CurrentRule,
  type LiveTrend,
  type MacroLine,
  type MacroPayload,
  type MacroTheme,
  type Overview as OverviewData,
  type Row,
  type StackPayload,
} from "../api";
import { AlertList } from "../components/Bits";
import { ErrorState } from "../components/States";
import { date, num, pctPoints, weight } from "../format";

/*
 * THE DIGEST, LAID OUT. Every figure here comes from `/api/overview`, which
 * serves `telegram/digest.collect_digest_inputs` — the same call the weekly
 * Telegram digest renders. That is what makes "the fronts agree" structural
 * rather than a convention someone has to maintain: there is one assembly, and
 * `DigestInputs` is a TypedDict, so a field added for one front and not the
 * other fails mypy before it reaches a reader.
 */

const s = (row: Row | null | undefined, key: string): unknown => row?.[key];

// The four states of the liquidity composite, spelled the way the agent tags
// them (market/liquidity.py STATES). Read from the payload and never derived
// here: the level/speed rule lived in three places until 2026-08-30 and they
// disagreed, so this front asks rather than decides.
const LIQUIDITY_READING: Record<string, string> = {
  "liquidity-supportive": "abundant and improving",
  "liquidity-fading": "abundant but deteriorating",
  "liquidity-repairing": "scarce but improving",
  "liquidity-restrictive": "scarce and deteriorating",
};

// What each sleeve IS, for a reader who does not hold the tickers in mind.
const ASSETS: Record<string, string> = {
  SPY: "US large caps",
  IWN: "US small-cap value",
  GLD: "Gold",
  VCIT: "Corporate bonds",
  IEF: "Treasuries, 7-10 years",
  cash: "Cash",
};
const ASSET_ORDER = ["SPY", "IWN", "GLD", "VCIT", "IEF", "cash"];
const SIGNALS: Record<string, { name: string; above: string; below: string }> =
  {
    BAA10Y: { name: "Credit spread", above: "wide", below: "tight" },
    T10Y2Y: { name: "Yield curve", above: "steep", below: "flat" },
  };

type Sleeve = {
  price: number;
  moving_averages: (number | null)[];
  share: number;
};

// ONE vocabulary for a sleeve's trend — the asset dots' tooltips and the order card's changes:
// a state named two ways on one page reads as two states.
const trendState = (share: number): { word: string; tone: string } =>
  share === 0
    ? { word: "in trend", tone: "good" }
    : share >= 1
      ? { word: "out", tone: "bad" }
      : { word: "half out", tone: "warn" };

const windowsOf = (decision: Row | null): number[] =>
  ((s(decision, "trend_overlay") as Row | undefined)?.["windows_days"] ??
    []) as number[];
type Weights = Record<string, number>;

// A weight, or a dash where the asset is not in that column at all.
const cell = (value: number | undefined): string =>
  value === undefined || value === 0 ? "—" : weight(value);

// The book's signal state in words: which side each signal fell on.
const bookName = (state: string): string =>
  state.replace("credit-spread-", "spread ").replace("-yield-curve-", ", curve ");

const allocationText = (weights: Weights): string =>
  [...ASSET_ORDER, ...Object.keys(weights).filter((t) => !ASSET_ORDER.includes(t))]
    .filter((t) => (weights[t] ?? 0) > 0)
    .map((t) => `${ASSETS[t] ?? t} ${weight(weights[t])}%`)
    .join(" · ") || "nothing";


function sleevesOf(decision: Row | null): [string, Sleeve][] {
  const sleeves = ((s(decision, "trend_overlay") as Row | undefined)?.[
    "sleeves"
  ] ?? {}) as Record<string, Sleeve>;
  return ASSET_ORDER.flatMap((t): [string, Sleeve][] => {
    const sleeve = sleeves[t];
    return sleeve ? [[t, sleeve]] : [];
  });
}

// Green to red (owner, 2026-10-17): what is fine first, the trouble last, in
// the legend's own order. A stable sort, so equal colours keep the board's order.
const COLOUR_ORDER = ["good", "watch", "poor", "bad"];
const byColour = (themes: MacroTheme[]): MacroTheme[] =>
  [...themes].sort(
    (a, b) => COLOUR_ORDER.indexOf(a.news) - COLOUR_ORDER.indexOf(b.news),
  );

// THE THEMES IN WORDS, at the head of the Macro card (owner, 2026-10-17):
// one sentence per theme — how it reads, the facts that make it read so, and
// where it is in trouble the probable cause — then the ideas from the books it
// wakes. The board below carries every series with its level and move; this is
// what a reader with ten seconds needs. Green to red. Nothing here forecasts.
function ThemeLines({ themes }: { themes: MacroTheme[] }) {
  if (!themes.length) return null;
  return (
    <div className="theme-lines">
      <ul className="brief-detail">
        {byColour(themes).map((theme) => (
          <li key={theme.group}>
            <span className={`swatch heat-${theme.news}`} />
            <span className="strong">{theme.group}</span>
            <span className="muted"> — </span>
            {theme.sentence}
            {theme.invariants.length ? (
              <ul className="lit">
                {theme.invariants.map((inv) => (
                  <li key={inv.id} title={inv.title}>
                    {inv.title}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
      {themes.some((theme) => theme.invariants.length) ? (
        <div className="paper-note">
          Under a theme, the ideas from the books whose condition holds clearly
          today on its series. None is established, and none changes the order.
        </div>
      ) : null}
    </div>
  );
}

// The pace of a move in one word: whether it is growing or fading.
const PACE: Record<string, string> = {
  "gathering pace": "accelerating",
  "losing pace": "slowing",
  "": "steady",
};

// An asset against its trend lines as a colour (owner, 2026-10-17: colours,
// not the overlay's words): green above both, orange below one, red below both.
function TrendDot({ share }: { share: number | undefined }) {
  if (share === undefined) return <span className="muted">—</span>;
  const state = trendState(share);
  const colour = share === 0 ? "good" : share >= 1 ? "bad" : "poor";
  return <span className={`swatch dot heat-${colour}`} title={state.word} />;
}

// Which way, and how hard: the board's arrows, green up and red down, a slight
// move grey — it is not news yet.
function AssetArrow({ asset }: { asset: AssetMove }) {
  if (asset.direction === 0) return <span className="asset-arrow muted">→</span>;
  const arrow = ARROWS[asset.move_size || "full"]![asset.direction > 0 ? 0 : 1];
  const tone =
    asset.move_size === "slight"
      ? "muted"
      : asset.direction > 0
        ? "heat-text-good"
        : "heat-text-bad";
  return <span className={`asset-arrow ${tone}`}>{arrow}</span>;
}

// THE ASSETS, ONE CARD (owner, 2026-10-17): how each is moving — which way,
// how fast for it, gathering or losing pace — and where it stands against its
// trend lines, at the decision and today. The trend table used to sit folded
// in the order card and the moves in the macro brief; they describe the same
// prices and now read as one row per asset.
//
// Distances are TODAY's when the live read is there, the decision's otherwise,
// and the header says which. Nothing here forecasts.
function AssetsCard({
  assets,
  decision,
  live,
}: {
  assets: AssetMove[];
  decision: Row | null;
  live: LiveTrend | null;
}) {
  const sleeves = Object.fromEntries(sleevesOf(decision));
  const moves = Object.fromEntries(assets.map((asset) => [asset.ticker, asset]));
  const tickers = ASSET_ORDER.filter((t) => t in sleeves || t in moves);
  if (!tickers.length) return null;
  const windows = windowsOf(decision);
  const latestAsOf = assets.reduce(
    (latest, asset) => (asset.as_of > latest ? asset.as_of : latest),
    "",
  );
  const distancesAsOf = live ? live.as_of : s(decision, "decision_date");
  return (
    <div className="card">
      <h2>
        <TrendingUp size={14} /> Assets
      </h2>
      <table className="data-table assets-table">
        <thead>
          <tr>
            <th>Asset</th>
            <th className="num">30 days{latestAsOf ? ` · ${date(latestAsOf)}` : ""}</th>
            <th>Pace</th>
            {windows.map((w) => (
              <th className="num" key={w}>
                vs {w}-day avg
              </th>
            ))}
            <th className="center">At decision</th>
            <th className="center">Today</th>
          </tr>
        </thead>
        <tbody>
          {tickers.map((t) => {
            const move = moves[t];
            const atDecision = sleeves[t];
            const today = live?.sleeves[t];
            const reference = today ?? atDecision;
            return (
              <tr key={t}>
                <td>
                  {ASSETS[t] ?? t} <span className="muted mono">{t}</span>
                </td>
                <td className="num">
                  {move && move.change_pct !== null ? (
                    <>
                      <AssetArrow asset={move} />
                      {move.change_pct > 0 ? "+" : ""}
                      {move.change_pct.toFixed(1)}%
                      {/* A late series says so: one date in the header must
                          not stand for an asset whose prices stopped. */}
                      {move.as_of !== latestAsOf ? (
                        <span className="heat-text-bad"> to {date(move.as_of)}</span>
                      ) : null}
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td className="muted">{move ? (PACE[move.pace] ?? "—") : "—"}</td>
                {windows.map((w, index) => (
                  <td className="num muted" key={w}>
                    {reference
                      ? distance(reference.price, reference.moving_averages[index] ?? null)
                      : "—"}
                  </td>
                ))}
                <td className="center">
                  <TrendDot share={atDecision?.share} />
                </td>
                <td className="center">
                  <TrendDot share={today?.share} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="paper-note">
        30 days is the price change; its arrow is sized against what is usual
        for that asset over ten years, and the pace says whether the move is
        growing or fading. Distances from each average are as of{" "}
        {date(distancesAsOf)}. The dots: green above both averages, orange
        below one, red below both. It describes prices, not a forecast.
      </div>
    </div>
  );
}

// The Worker's reading: the bottom line, then each part under the heading the
// JOURNAL gives it. The headings are read from the payload and never written
// here — a forecast labelled a fact in one front and not in another is the
// defect the parts exist to prevent. A reading journalled before it had parts
// is one long paragraph; it is kept, folded, so it does not bury the order.
function WorkerReading({
  reading,
  stale,
}: {
  reading: Row | null;
  stale: boolean;
}) {
  if (!s(reading, "market_signal_assessment")) return null;
  const parts = s(reading, "market_signal_reading") as Row | undefined;
  const sections = (parts?.["sections"] ?? []) as {
    heading: string;
    points: string[];
  }[];
  const note = stale
    ? ` — of the ${date(s(reading, "market_signal_decision_date"))} decision, not this one`
    : "";
  if (!parts || !parts["bottom_line"]) {
    return (
      <details className="reading">
        <summary>
          Worker reading{note} (one paragraph, before the structured format)
        </summary>
        <p>{String(s(reading, "market_signal_assessment"))}</p>
      </details>
    );
  }
  return (
    <div className="reading">
      <div className="section-label">Worker reading{note}</div>
      <p className="bottom-line">{String(parts["bottom_line"])}</p>
      <div className="reading-grid">
        {sections.map((section) => (
          <div key={section.heading}>
            <div className="reading-heading">{section.heading}</div>
            <ul>
              {section.points.map((point, index) => (
                <li key={index}>{point}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function distance(price: number, line: number | null): string {
  if (!line) return "—";
  const gap = (price / line - 1) * 100;
  return `${gap >= 0 ? "+" : ""}${gap.toFixed(1)}%`;
}

// THIS MONTH'S ORDER. The card answers three questions in the order the owner
// asks them: what do I do, why did the rule decide it, and what does the
// Worker make of it. The order is what is placed by hand, so it is a table of
// its own with the move spelled out; the "why" is the rule's own two signals
// and its trend reads, as figures rather than as the sentence that held them.
// One signal on one date: its value, the median of ITS OWN last ten years on
// that date, and which side it falls on. The decision and today each get one,
// so a figure is never compared with a median taken on another day.
function SignalRead({
  value,
  median,
  ticker,
}: {
  value: unknown;
  median: unknown;
  ticker: string;
}) {
  if (typeof value !== "number" || typeof median !== "number") {
    return <span className="muted">—</span>;
  }
  const words = SIGNALS[ticker];
  return (
    <span className="signal-read">
      <span className="strong">{num(value)}</span>
      <span className="muted"> vs median {num(median)}</span>
      <span className="pill quiet">
        {value > median ? (words?.above ?? "above") : (words?.below ?? "below")}
      </span>
    </span>
  );
}

const sideOf = (value: unknown, median: unknown, ticker: string): string =>
  typeof value !== "number" || typeof median !== "number"
    ? ""
    : value > median
      ? (SIGNALS[ticker]?.above ?? "above")
      : (SIGNALS[ticker]?.below ?? "below");

// What the same test gives today that it did not give at the decision: a
// signal that crossed its median, an asset whose trend state moved. It decides
// nothing — the next monthly decision does — and it is the one thing worth
// reading between two decisions.
function changesSince(decision: Row, live: LiveTrend): string[] {
  const signals = (s(decision, "signals") ?? {}) as Record<string, Row>;
  const crossed = Object.entries(signals).flatMap(([ticker, read]) => {
    const then = sideOf(read["value"], read["trailing_median"], ticker);
    const now = sideOf(
      live.signals[ticker]?.value,
      live.signals[ticker]?.trailing_median,
      ticker,
    );
    return then && now && then !== now
      ? [`${SIGNALS[ticker]?.name ?? ticker} (${ticker}): was ${then}, now ${now}`]
      : [];
  });
  const moved = sleevesOf(decision).flatMap(([t, sleeve]) => {
    const now = live.sleeves[t]?.share;
    return now !== undefined &&
      trendState(now).word !== trendState(sleeve.share).word
      ? [
          `${ASSETS[t] ?? t} (${t}): was ${trendState(sleeve.share).word}, now ${trendState(now).word}`,
        ]
      : [];
  });
  return [...crossed, ...moved];
}

// The rule's answer in one or two sentences: which side each signal fell on,
// and what the trend filter did. No figure — the fold under the card has them.
function whyThisBook(
  signals: Record<string, Row>,
  sleeves: [string, Sleeve][],
  target: Weights,
): string {
  const sides = Object.entries(signals)
    .map(([ticker, read]) => {
      const side = sideOf(read["value"], read["trailing_median"], ticker);
      return side
        ? `${(SIGNALS[ticker]?.name ?? ticker).toLowerCase()} (${ticker}) ${side}`
        : "";
    })
    .filter(Boolean);
  const first = sides.length
    ? `${capitalise(sides.join(" and "))} against their ten-year medians select this book.`
    : "";
  const out = sleeves.filter(([, v]) => v.share > 0).length;
  const haven = (target["IEF"] ?? 0) > 0 ? "Treasuries" : "cash";
  const second = !sleeves.length
    ? ""
    : out === 0
      ? "Every asset is in trend, so the book is held as written."
      : `${out} of ${sleeves.length} assets are below their trend lines; what they would have held went to ${haven}.`;
  return [first, second].filter(Boolean).join(" ");
}

function DecisionCard({
  decision,
  reading,
  live,
  rule,
}: {
  decision: Row | null;
  reading: Row | null;
  live: LiveTrend | null;
  rule: CurrentRule | null;
}) {
  if (!decision) {
    return (
      <div className="card">
        <h2>
          <Bell size={14} /> This month&apos;s order
        </h2>
        <div className="empty">No market-signal decision recorded yet.</div>
      </div>
    );
  }
  const gate = s(decision, "gate");
  const blocked = Boolean(gate) && gate !== "passed";
  // THE RULE, STEP BY STEP, UNDER TODAY'S RULE (owner, 2026-10-17: "plusieurs
  // colonnes pour comprendre visuellement la logique"): the book the signal
  // selects, that book through the trend filter at the decision — the month's
  // target — and the same book through the filter on today's prices. The
  // journal keeps the decision as it was taken, which after a book change is
  // the previous rule's; that is said once, below the table.
  const journalled = (s(decision, "target_allocation") ?? {}) as Weights;
  const current = rule && rule.decision_date === s(decision, "decision_date") ? rule : null;
  const book = (current?.book_allocation ?? {}) as Weights;
  const target = (current?.target ?? journalled) as Weights;
  const today = (current?.today ?? {}) as Weights;
  const previous = (current?.previous_target ?? s(decision, "held_allocation") ?? {}) as Weights;
  const rewritten =
    current !== null && allocationText(journalled) !== allocationText(target);
  const shown = [book, target, today];
  const tickers = [
    ...ASSET_ORDER.filter((t) => shown.some((w) => t in w)),
    ...Object.keys(Object.assign({}, ...shown) as Weights).filter(
      (t) => !ASSET_ORDER.includes(t),
    ),
  ];
  // To place when the month's target differs from last month's.
  const moves = allocationText(previous) !== allocationText(target);
  const signals = (s(decision, "signals") ?? {}) as Record<string, Row>;
  const changes = live ? changesSince(decision, live) : [];
  const readDate = s(reading, "market_signal_decision_date");
  const stale = Boolean(readDate) && readDate !== s(decision, "decision_date");

  return (
    <div className="card">
      <div className="card-head">
        <h2>
          <Bell size={14} /> This month&apos;s order
        </h2>
        <span
          className={`pill ${blocked ? "critical" : moves ? "action" : "quiet"}`}
        >
          {blocked ? "blocked" : moves ? "to place" : "no change"}
        </span>
      </div>
      <div className="muted">
        Decided {date(s(decision, "decision_date"))}, once a month. The agent
        places no order: this is the one to place by hand.
      </div>
      {blocked ? (
        <div className="alert critical" style={{ marginTop: 10 }}>
          <div>
            <span className="level">blocked</span>
            <div>
              Gate <span className="mono">{String(gate)}</span> refused this
              decision — nothing was proposed. The stack is FROZEN in its
              previous book, not in the target below.
            </div>
          </div>
        </div>
      ) : null}

      <div className="decision-grid">
        <div>
          <div className="section-label">Order</div>
          <table className="data-table order-table">
            <thead>
              <tr>
                <th>Asset</th>
                {current ? (
                  <th className="num" title="The book the two signals select, before any filter">
                    Book
                    <span className="th-sub">{bookName(current.book)}</span>
                  </th>
                ) : null}
                <th
                  className="num"
                  title="The book through the trend filter at the decision: the month's target"
                >
                  Target
                  <span className="th-sub">filtered {date(s(decision, "decision_date"))}</span>
                </th>
                {current ? (
                  <th
                    className="num"
                    title="The same book through the filter on today's prices — decides nothing"
                  >
                    Today
                    <span className="th-sub">filtered {date(current.today_as_of)}</span>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {tickers.map((t) => (
                <tr key={t}>
                  <td>
                    {ASSETS[t] ?? t} <span className="muted mono">{t}</span>
                  </td>
                  {current ? <td className="num muted">{cell(book[t])}</td> : null}
                  <td className="num strong">{cell(target[t])}</td>
                  {current ? <td className="num">{cell(today[t])}</td> : null}
                </tr>
              ))}
            </tbody>
          </table>
          {rewritten ? (
            <div className="paper-note">
              Read with today&apos;s rule. The decision journalled on{" "}
              {date(s(decision, "decision_date"))} was taken under the previous
              book and said {allocationText(journalled)}.
            </div>
          ) : null}
          {s(decision, "correction_note") ? (
            /* Same rule as the digest: a decision corrected after the fact
               says so, on the block whose numbers the correction moved. */
            <div className="paper-note">
              <strong>Corrected.</strong>{" "}
              {String(s(decision, "correction_note"))}
            </div>
          ) : null}
        </div>

        <div>
          <div className="section-label">Why this book</div>
          {/* THE REASON, IN WORDS; THE FIGURES ONCE, in the fold below (owner,
              2026-10-17: "ne garde qu'une version"). This list carried each
              signal's value and median, and the fold the same values again. */}
          <p className="why-prose">{whyThisBook(signals, sleevesOf(decision), target)}</p>
          <div className="section-label" style={{ marginTop: 14 }}>
            Since the decision{live ? ` · ${date(live.as_of)}` : ""}
          </div>
          {live ? (
            changes.length ? (
              <ul className="why-list">
                {changes.map((change) => (
                  <li key={change}>{change}</li>
                ))}
              </ul>
            ) : (
              <div className="muted">
                Nothing has changed side: the same test today gives the same
                answers.
              </div>
            )
          ) : (
            <div className="muted">Today&apos;s reading is not available.</div>
          )}
        </div>
      </div>

      <details className="fold">
        <summary>The two signals, at the decision and today</summary>
        <div style={{ marginTop: 10 }}>
          <div className="section-label">The two signals</div>
          <table className="data-table why-table">
            <thead>
              <tr>
                <th>Signal</th>
                <th>At the decision · {date(s(decision, "decision_date"))}</th>
                <th>Today{live ? ` · ${date(live.as_of)}` : ""}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(signals).map(([ticker, read]) => (
                <tr key={ticker}>
                  <td>
                    {SIGNALS[ticker]?.name ?? ticker}{" "}
                    <span className="muted mono">{ticker}</span>
                  </td>
                  <td>
                    <SignalRead
                      value={read["value"]}
                      median={read["trailing_median"]}
                      ticker={ticker}
                    />
                  </td>
                  <td>
                    <SignalRead
                      value={live?.signals[ticker]?.value}
                      median={live?.signals[ticker]?.trailing_median}
                      ticker={ticker}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="paper-note">
            Each figure is compared with the median of its own last ten years on
            that date. The two answers pick the book; a change of book needs
            three monthly decisions in a row to agree.
          </div>
        </div>
      </details>

      <WorkerReading reading={reading} stale={stale} />

      <div style={{ marginTop: 12 }}>
        <Link className="freshness" to="/stack">
          Full decision timeline and the strategy&apos;s standing →
        </Link>
      </div>
    </div>
  );
}

// THE MACRO BOARD (`/api/macro`, market/macro_board.py). The regime label is
// the detector's CONCLUSION; this is what it is drawn from, and more. Each
// series gets ONE SENTENCE written by the agent — where the level sits in its
// own ten years, and how it is moving for a series of its kind. The page shows
// the sentence and derives nothing, so every front says the same thing of the
// same print.
// The four steps of the board's colour, in the order the legend reads: where
// the level sits in its ten years, seen from the side that is good news for
// that series (the agent declares the side, `macro_board.MACRO_BOARD`).
const NEWS: [MacroLine["news"], string][] = [
  ["good", "favourable"],
  ["watch", "less favourable than usual"],
  ["poor", "unfavourable"],
  ["bad", "near its worst in ten years"],
];

const ARROWS: Record<string, [string, string]> = {
  slight: ["↗", "↘"],
  full: ["↑", "↓"],
  sharp: ["⇈", "⇊"],
};

const capitalise = (text: string): string =>
  text ? text[0]!.toUpperCase() + text.slice(1) : text;

// Where the level sits between its ten-year low and high, with the median as
// a tick: one glance replaces "within its usual range (ten-year median …)".
function RangeBar({ line }: { line: MacroLine }) {
  if (line.low === null || line.high === null || line.high <= line.low) {
    return <span className="muted">—</span>;
  }
  const at = (value: number): string =>
    `${Math.min(100, Math.max(0, ((value - line.low!) / (line.high! - line.low!)) * 100))}%`;
  const figure = (value: number): string => value.toFixed(line.decimals);
  return (
    <div
      className="range"
      title={`Ten years: low ${figure(line.low)}, median ${
        line.median === null ? "n/a" : figure(line.median)
      }, high ${figure(line.high)}`}
    >
      <span className="range-end">{figure(line.low)}</span>
      <div className="range-track">
        {line.median !== null ? (
          <span className="range-median" style={{ left: at(line.median) }} />
        ) : null}
        <span
          className={`range-dot heat-${line.news}`}
          style={{ left: at(line.level) }}
        />
      </div>
      <span className="range-end">{figure(line.high)}</span>
    </div>
  );
}

function MoveCell({ line }: { line: MacroLine }) {
  if (line.direction === 0 || line.speed === null) {
    return <span className="muted">→ flat</span>;
  }
  const arrow = ARROWS[line.move_size || "full"]![line.direction > 0 ? 0 : 1];
  const unit = line.unit ? " pt" : "";
  // Improving or worsening, by the same favourable side as the level; a
  // slight move stays grey, it is not news yet.
  const improving = line.direction > 0 === (line.favourable === "high");
  const tone =
    line.move_size === "slight" || line.favourable === null
      ? "muted"
      : improving
        ? "strong heat-text-good"
        : "strong heat-text-bad";
  return (
    <span className={tone}>
      {arrow} {line.speed > 0 ? "+" : ""}
      {line.speed.toFixed(line.decimals)}
      {unit}
    </span>
  );
}

function MacroBoard({
  board,
  themes,
  highlights,
  regime,
  liquidity,
}: {
  board: MacroLine[];
  themes: MacroTheme[];
  highlights: MacroPayload["highlights"];
  regime: Row;
  liquidity: Row;
}) {
  const name = s(regime, "regime_name") ?? s(regime, "regime_type_id");
  const state = s(liquidity, "state");
  const groups = [...new Set(board.map((line) => line.group))];
  const unfavourable = board.filter(
    (line) => line.news === "poor" || line.news === "bad",
  ).length;
  return (
    <div className="card">
      <h2>
        <Compass size={14} /> Macro
      </h2>
      <div className="macro-head">
        <div>
          <div className="macro-label">Regime</div>
          <div className="macro-value">
            {name ? String(name) : "not detected"}
          </div>
          {name ? (
            <div className="muted">
              {/* percent POINTS, not a fraction — see format.ts */}
              confidence {pctPoints(s(regime, "confidence"))}
              {s(regime, "start_date")
                ? ` · since ${date(s(regime, "start_date"))}`
                : ""}
            </div>
          ) : null}
        </div>
        {typeof state === "string" ? (
          <div>
            <div className="macro-label">Global liquidity</div>
            <div className="macro-value">{state.replace("liquidity-", "")}</div>
            <div className="muted">{LIQUIDITY_READING[state] ?? ""}</div>
          </div>
        ) : null}
        {board.length ? (
          <div>
            <div className="macro-label">Unfavourable</div>
            <div className="macro-value plain">
              {unfavourable} of {board.length}
            </div>
            <div className="legend">
              {NEWS.map(([news, words]) => (
                <span key={news}>
                  <span className={`swatch heat-${news}`} />
                  {words}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>
      <ThemeLines themes={themes} />
      {highlights.length ? (
        <div className="standout">
          <div className="section-label">What changed</div>
          {highlights.map((line) => (
            <div className="standout-line" key={line.text}>
              <span className={`swatch heat-${line.tone}`} />
              <span className="standout-kind">{line.kind}</span>
              <span>
                {line.text}
                {line.cause ? (
                  <span className="standout-cause"> {line.cause}</span>
                ) : null}
              </span>
            </div>
          ))}
          <div className="paper-note">
            A probable cause is read from the board itself: the usual drivers
            of that series which point the same way today. It is a convention
            of textbook macro, not a measurement.
          </div>
        </div>
      ) : null}
      {board.length ? (
        <div className="themes">
          {themes.map((theme) => (
            <div className="theme" key={theme.group}>
              <div className="theme-head">
                <span className={`swatch heat-${theme.news}`} />
                <span className="theme-name">{theme.group}</span>
              </div>
              <div className="tile-row">
                {board
                  .filter((line) => line.group === theme.group)
                  .map((line) => (
                    <div
                      className={`tile heat-${line.news}`}
                      key={line.ticker}
                      title={line.reading}
                    >
                      <div className="tile-name">
                        {capitalise(line.short.replace(/^the /, ""))}
                      </div>
                      <div className="tile-ticker">{line.ticker}</div>
                      <div className="tile-level">{line.level_text}</div>
                      <div className="tile-move">
                        <MoveCell line={line} />
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {board.length ? (
        <details className="fold">
          <summary>Each series in its last ten years</summary>
          <table className="data-table macro-table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>Series</th>
              <th className="num">Level</th>
              <th>In its last ten years</th>
              <th>Move</th>
              <th>Reading</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => [
              <tr className="group-row" key={group}>
                <td colSpan={5}>
                  <Gauge size={11} /> {group}
                </td>
              </tr>,
              ...board
                .filter((line) => line.group === group)
                .map((line) => (
                  <tr key={line.ticker} title={line.reading}>
                    <td className="macro-name">
                      {line.label}{" "}
                      <span className="muted mono">{line.ticker}</span>
                    </td>
                    <td className="num">
                      <span className={`level heat-${line.news}`}>
                        {line.level_text}
                      </span>
                    </td>
                    <td>
                      <RangeBar line={line} />
                    </td>
                    <td className="macro-move">
                      <MoveCell line={line} />
                      {line.direction !== 0 ? (
                        <span className="muted"> {line.change_period}</span>
                      ) : null}
                    </td>
                    <td className="macro-reading">
                      {capitalise(
                        [line.unusual ? line.position : "", line.pace]
                          .filter(Boolean)
                          .join(", "),
                      )}
                    </td>
                  </tr>
                )),
            ])}
          </tbody>
        </table>
        </details>
      ) : (
        <div className="empty">No macro series in the database yet.</div>
      )}
      <div className="paper-note">
        The colour is good or bad news for an investor building capital:
        growth, credit and liquidity are better high; rates, inflation,
        unemployment, spreads and volatility are better low. A theme reads as
        its worst series. Hover a tile for the full sentence.
      </div>
    </div>
  );
}

// THE LIGHTHOUSES, as a reader meets them (owner, 2026-10-11: "Standing,
// Confirmed, Rate 95% range, Mean effect, Worst: c'est du bruit"). Five columns
// of statistics asked the reader to do the judging. Each idea now says, in
// order: what it claims, how often history agreed (one square per time it was
// put to the test), and what that is worth — with the reason when it is not
// yet worth anything. The effect sizes are on the row's tooltip: their unit
// differs from one idea to the next, and a column of them compared nothing.
function Lighthouses({ invariants }: { invariants: Row[] }) {
  if (!invariants.length) return null;
  const percent = (value: unknown): string =>
    typeof value === "number" ? `${Math.round(value * 100)}%` : "?";
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <h2>Lighthouses</h2>
      <div className="muted" style={{ marginBottom: 4 }}>
        Ideas taken from the books the agent has read, each one checked against
        market history. The Worker reads them as background; none of them
        changes the order above.
      </div>
      {invariants.map((inv, i) => {
        const confirmed = Number(inv.confirmation_count) || 0;
        const refuted = Number(inv.infirmation_count) || 0;
        const tested = confirmed + refuted;
        const established = inv.status === "integrated";
        const detail =
          typeof inv.mean_lift === "number" && typeof inv.worst_lift === "number"
            ? `Average effect ${num(inv.mean_lift, 3)}, worst case ${num(inv.worst_lift, 3)}` +
              (inv.lift_metric ? ` (${String(inv.lift_metric)})` : "")
            : undefined;
        return (
          <div className="idea" key={String(inv.title ?? i)} title={detail}>
            <div className="idea-title" title={String(inv.title ?? "")}>
              {String(inv.title ?? "")}
            </div>
            <div className="idea-record">
              <span className="squares">
                {Array.from({ length: tested }, (_, n) => (
                  <span key={n} className={n < confirmed ? "square yes" : "square no"} />
                ))}
              </span>
              <span>
                {tested ? (
                  <>
                    Held <span className="strong">{confirmed}</span> of the{" "}
                    {tested} times it was tested
                    {refuted ? `, failed ${refuted}` : ""}.
                  </>
                ) : (
                  "Not tested yet."
                )}
              </span>
              <span className={`chip ${established ? "good" : ""}`}>
                {established ? "established" : "not established yet"}
              </span>
            </div>
            {!established && tested ? (
              <div className="idea-why">
                Too few cases to trust: with this record its real success rate
                could be anywhere from {percent(inv.rate_low)} to{" "}
                {percent(inv.rate_high)}.
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function Overview() {
  const { data, error, isPending, isFetching } = useQuery({
    queryKey: ["overview"],
    queryFn: () => get<OverviewData>("/api/overview"),
  });
  // Its own query: the board is a read of the tape, not part of the digest's
  // inputs, and the rest of the page must not wait on it or fail with it.
  const macro = useQuery({
    queryKey: ["macro"],
    queryFn: () => get<MacroPayload>("/api/macro"),
  });

  // Today's reading of the two signals and of each trend, beside the
  // decision's own (`/api/stack`, which the Stack page reads too). Best-effort
  // like the board: without it the "Today" columns are empty, nothing else.
  const stack = useQuery({
    queryKey: ["stack"],
    queryFn: () => get<StackPayload>("/api/stack"),
  });

  if (error) return <ErrorState error={error} />;
  if (isPending) return <div className="empty">Loading…</div>;
  const board = macro.data?.board ?? [];

  return (
    /* Held at reduced opacity on refetch rather than replaced by a skeleton —
       no layout jump, and the previous numbers stay readable while new ones
       arrive. */
    <div className={isFetching ? "stale" : undefined}>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>What to do this month, and the weather it was decided in.</p>
        </div>
      </div>

      {/* THE WEATHER FIRST, THEN THE ASSETS, THEN THE ORDER (owner,
          2026-10-17: the Macro card moved to the top, its themes in words at
          its head; it was the brief, the order, then the macro tape). The defender, the
          scoreboard and the stack's own tiles were here and said nothing the
          owner acts on from this page: the bridge decides nothing (ADR-014),
          a hit rate of 0/0 is not a score, and the stack's standing has a page
          of its own. Alerts come first and are one sentence each. */}
      <AlertList alerts={data.alerts} />

      <div style={{ marginTop: 14 }}>
        {macro.error ? (
          <ErrorState error={macro.error} />
        ) : (
          <MacroBoard
            board={board}
            themes={macro.data?.themes ?? []}
            highlights={macro.data?.highlights ?? []}
            regime={data.regime}
            liquidity={data.global_liquidity}
          />
        )}
      </div>

      <div style={{ marginTop: 14 }}>
        <AssetsCard
          assets={macro.data?.assets ?? []}
          decision={data.market_signal}
          live={stack.data?.live_trend ?? null}
        />
      </div>

      <div style={{ marginTop: 14 }}>
        <DecisionCard
          decision={data.market_signal}
          reading={data.worker_reading}
          live={stack.data?.live_trend ?? null}
          rule={stack.data?.current_rule ?? null}
        />
      </div>

      <Lighthouses invariants={data.invariants} />
    </div>
  );
}
