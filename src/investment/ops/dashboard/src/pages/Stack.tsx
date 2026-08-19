import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { CheckCircle2, MinusCircle, ShieldAlert } from "lucide-react";
import {
  get,
  type LiveTrend,
  type NavPoint,
  type Row,
  type StackDecision,
  type StackPayload,
} from "../api";
import { Tile } from "../components/Bits";
import { NavChart, type NamedNavSeries } from "../components/NavChart";
import { ErrorState } from "../components/States";
import { NA, date, num, pct, weight } from "../format";

/*
 * THE LIVE ALLOCATION PATH (ADR-007). This is the page the original 8-page spec
 * did not have: that list was written when the ranking was the allocation path,
 * and the page list never followed the pivot. It is the screen the owner reads
 * before placing an order by hand.
 *
 * THE TIMELINE IS THE JOURNAL, one row per decision date — moved or not, blocked
 * or not. A proposal-driven timeline would show the ~3 months a year the stack
 * moves and call the other nine nothing at all, which is precisely the failure
 * `digest._market_signal_block` documents.
 *
 * THREE OUTCOMES, THREE DIFFERENT RENDERINGS, and the middle one is the whole
 * point of the page: a decision that MOVED, a month that HELD (legitimate, no
 * order to place), and one that was BLOCKED by a gate — where nothing was
 * proposed and the stack is frozen in its PREVIOUS book, not the one named. A
 * blocked month rendered like a holding month is the error this page exists to
 * make impossible.
 */

// COLOR FOLLOWS THE ENTITY, never the array position the API happens to
// return (dataviz skill) — a fixed id -> color map rather than zipping colors
// onto whatever order the response sends. `--series-3` (purple) is
// deliberately absent: it already means "the stack" everywhere this page
// uses it. `ms-trend-baseline` (green) joined 2026-08-19 — the retained
// bridge's control arm, not a `BENCHMARK_PORTFOLIOS` member (see api.py
// `handle_stack`), but the same chart wants it for the same reason: an
// unanchored stack line invites reading its shape as merit rather than as
// one path among several.
const BENCHMARK_COLOR: Record<string, string> = {
  "all-weather-USD": "var(--series-1)",
  "spy-USD": "var(--series-2)",
  "aaaf-r-USD": "var(--series-4)",
  "ms-trend-baseline": "var(--series-5)",
};

/*
 * REBASED TO THE STACK'S OWN INCEPTION, not left at each series' own base 100
 * (owner, 2026-08-18, after "how can ms-stack and spy-USD have a similar CAGR
 * and such a different NAV curve" — answer: partly because spy-USD had 13.8
 * MORE years to compound from ITS OWN 1980 base before this chart's shared
 * x-axis, which has nothing to do with the two strategies' relative merit).
 * `refDate` is read off the stack's own first NAV point rather than hardcoded
 * as the literal "1993-11-01": `market_signal.stack_calendar`'s anchor is
 * data-derived (VCIT's proxy start) and has already moved once in this
 * project's history, so a literal here would go stale exactly the way
 * CLAUDE.md's "WHEN A SECOND ONE ARRIVES" warns about.
 *
 * A series that starts LATER than `refDate` rebases against its OWN first
 * point instead — `points.find(p.ts >= refDate)` lands on that first point
 * when nothing earlier qualifies, and dividing a series by its own first
 * value is a no-op (it was already 100 there). `ms-trend-baseline` shares the
 * stack's own calendar so this never fires for it; `aaaf-r-USD` is the one
 * series it does fire for, and its curve is chained onto spy-USD instead of
 * left at this no-op — see `chainOnto` below.
 */
function rebaseFrom(points: NavPoint[], refDate: string | null): NavPoint[] {
  if (!refDate) return points;
  const inRange = points.filter(
    (p): p is { ts: string; nav: number } => p.ts >= refDate && typeof p.nav === "number",
  );
  if (inRange.length === 0) return [];
  const base = inRange[0]!.nav;
  return base ? inRange.map((p) => ({ ts: p.ts, nav: (p.nav / base) * 100 })) : inRange;
}

/*
 * CHAIN A LATE-STARTING SERIES ONTO AN EARLIER ONE'S REBASED LEVEL, rather
 * than restarting it at 100 (owner, 2026-08-19: "fait demarrer AAAF-R au
 * meme niveau que SPY"). `rebaseFrom`'s no-op for a series that starts after
 * `refDate` left aaaf-r-USD opening at its own 100 on the SAME chart where
 * spy-USD, decades into compounding, was already far above that — a visual
 * reset with no meaning, since the two numbers were never on the same base.
 *
 * `reference` must already be rebased (spy-USD's `rebaseFrom` output). This
 * finds the reference's value at-or-before `points`' first date and scales
 * the WHOLE of `points` by (that value / its own first value), so the two
 * curves visually meet at the date aaaf-r-USD's history begins and diverge
 * only from there — which is the honest comparison: nothing about aaaf-r-USD's
 * OWN shape changes, only where it starts reading on a shared axis.
 */
function chainOnto(points: NavPoint[], reference: NavPoint[]): NavPoint[] {
  const first = points.find((p) => typeof p.nav === "number");
  if (!first || typeof first.nav !== "number" || reference.length === 0) return points;
  const anchor = [...reference].reverse().find((p) => p.ts <= first.ts) ?? reference[0]!;
  if (typeof anchor.nav !== "number") return points;
  const scale = anchor.nav / first.nav;
  return points.map((p) => ({ ts: p.ts, nav: typeof p.nav === "number" ? p.nav * scale : p.nav }));
}

/** Whether `points` has a priced observation at or before `refDate` — the
 * chart-start slider can move PAST aaaf-r-USD's own inception, at which point
 * it should rebase normally like every other series instead of chaining. */
function startsOnOrBefore(points: NavPoint[], refDate: string | null): boolean {
  if (!refDate) return true;
  return points.some((p) => p.ts <= refDate && typeof p.nav === "number");
}

type Outcome = "moved" | "held" | "blocked";

function outcomeOf(payload: Row): Outcome {
  const gate = payload.gate;
  if (gate && gate !== "passed") return "blocked";
  const held = (payload.held_allocation ?? {}) as Record<string, number>;
  const target = (payload.target_allocation ?? {}) as Record<string, number>;
  const keys = new Set([...Object.keys(held), ...Object.keys(target)]);
  for (const k of keys) {
    if ((held[k] ?? 0) !== (target[k] ?? 0)) return "moved";
  }
  return "held";
}

function OutcomeChip({ outcome }: { outcome: Outcome }) {
  if (outcome === "blocked") {
    return (
      <span className="badge excluded">
        <ShieldAlert size={11} /> blocked
      </span>
    );
  }
  if (outcome === "moved") {
    return (
      <span className="badge defender">
        <CheckCircle2 size={11} /> moved
      </span>
    );
  }
  return (
    <span className="badge">
      <MinusCircle size={11} /> held
    </span>
  );
}

/*
 * ONE SLEEVE'S READ, rendered with WHICH line(s) it is below — not a flat
 * "below" badge (owner, 2026-08-19: "BELOW ne precise pas si c'est sur 150,
 * 300 ou les 2"). `moving_averages` is ordered the same as `windowsList`
 * (`market_signal.MA_WINDOWS`/`TrendRead.moving_averages`), so zipping the
 * two is enough to name the exact line(s) breached — recomputed here rather
 * than trusted from a `below`/`breached` flag, so a badge and its own numbers
 * can never disagree (the same discipline `TrendRead.breached`'s docstring
 * describes for the Python side). The full pair is still available on hover,
 * for the line that ISN'T breached.
 */
function sleeveCell(read: Row | undefined, windowsList: number[]) {
  const price = read?.price;
  if (typeof price !== "number") return <td className="num muted">—</td>;
  const mas = (read?.moving_averages ?? []) as unknown[];
  const belowLines = windowsList.filter((_, i) => typeof mas[i] === "number" && price < (mas[i] as number));
  const title = windowsList
    .map((w, i) => `${w}d ${typeof mas[i] === "number" ? num(mas[i], 0) : NA}`)
    .join(" · ");
  return (
    <td className="num" title={title}>
      {num(price, 0)}
      {belowLines.length ? <span className="badge excluded">below {belowLines.join("/")}d</span> : null}
    </td>
  );
}

/*
 * ONE SIGNAL'S READ (BAA10Y/T10Y2Y) — the same decision/latest split as
 * `sleeveCell`, added 2026-08-19 so "the sleeves got a Latest column and the
 * two signals above them did not" stops being true. No badge here: the
 * signal has no line to breach, just a value and its own 10y median.
 */
function signalCell(read: Row | undefined) {
  const value = read?.value;
  if (typeof value !== "number") return <td className="num muted">—</td>;
  const median = read?.trailing_median;
  return (
    <td className="num">
      {num(value, 2)}
      {typeof median === "number" ? <span className="muted"> · vs {num(median, 2)}</span> : null}
    </td>
  );
}

function LatestDecision({ decision, liveTrend }: { decision: StackDecision; liveTrend: LiveTrend | null }) {
  const p = decision.payload;
  const outcome = outcomeOf(p);
  const held = (p.held_allocation ?? {}) as Record<string, number>;
  const target = (p.target_allocation ?? {}) as Record<string, number>;
  const tickers = Array.from(new Set([...Object.keys(held), ...Object.keys(target)])).sort();
  const signals = (p.signals ?? {}) as Record<string, Row>;
  const overlay = (p.trend_overlay ?? {}) as Row;
  const hysteresis = (p.hysteresis ?? {}) as Row;
  const windows = (overlay.windows_days ?? null) as number[] | null;
  const windowsList = windows ?? [];
  const window = windows ? windows.join("/") : String(overlay.window_days ?? "—");
  const sleeves = (overlay.sleeves ?? {}) as Record<string, Row>;
  const liveSleeves = (liveTrend?.sleeves ?? {}) as unknown as Record<string, Row>;
  const sleeveTickers = Array.from(new Set([...Object.keys(sleeves), ...Object.keys(liveSleeves)]));
  const liveSignals = (liveTrend?.signals ?? {}) as unknown as Record<string, Row>;
  const signalTickers = Array.from(new Set([...Object.keys(signals), ...Object.keys(liveSignals)]));
  const decisionDate = typeof p.decision_date === "string" ? p.decision_date : null;
  // MONTHLY CADENCE means the decision column can be days to weeks stale by
  // the time it is read — a sleeve can cross back the other way in the
  // meantime without the decision re-running (it only does monthly). The
  // "Latest" column beside it is the fix: reading it live, not a decision-date
  // snapshot, so a crossing is visible before the next monthly decision acts
  // on it (see `market_signal.latest_trend_reads`).
  const daysSinceDecision = decisionDate
    ? Math.floor((Date.now() - new Date(decisionDate).getTime()) / 86_400_000)
    : null;

  return (
    <div className="card">
      <h2>
        Latest decision — {date(p.decision_date)} <OutcomeChip outcome={outcome} />
      </h2>
      <div style={{ fontSize: 17, fontWeight: 620, letterSpacing: "-0.015em" }}>
        {outcome === "blocked" ? "Target book" : "Book"} {String(p.held_book ?? "?")}
      </div>

      {outcome === "blocked" ? (
        <div className="alert critical" style={{ marginTop: 10 }}>
          <ShieldAlert size={16} />
          <div>
            <span className="level">nothing was proposed</span>
            <div>
              Gate <span className="mono">{String(p.gate)}</span> refused this decision. The
              stack is FROZEN in its previous book — not in the one named above. A refused
              decision means a config or code change, never a market event.
            </div>
          </div>
        </div>
      ) : null}

      <div className="grid cols-2" style={{ marginTop: 12 }}>
        <div>
          <h2 style={{ marginBottom: 8 }}>The order to place</h2>
          {tickers.length ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th className="num">Held</th>
                    <th className="num">Target</th>
                    <th className="num">Move</th>
                  </tr>
                </thead>
                <tbody>
                  {tickers.map((t) => {
                    const h = held[t] ?? 0;
                    const g = target[t] ?? 0;
                    const moved = h !== g;
                    return (
                      <tr key={t} style={moved ? undefined : { opacity: 0.55 }}>
                        <td className="mono">{t}</td>
                        <td className="num">{weight(h)}</td>
                        <td className="num">{weight(g)}</td>
                        <td className={`num ${g > h ? "pos" : g < h ? "neg" : "na"}`}>
                          {moved ? `${g > h ? "+" : ""}${Math.round((g - h) * 100)}` : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">No allocation recorded on this decision.</div>
          )}
        </div>

        <div>
          <h2 style={{ marginBottom: 8 }}>What moved the money</h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th />
                  <th>
                    Decision · {date(decisionDate)}
                    {daysSinceDecision !== null && daysSinceDecision > 0 ? (
                      <span className="muted" style={{ fontWeight: 400 }}>
                        {" "}
                        ({daysSinceDecision}d ago)
                      </span>
                    ) : null}
                  </th>
                  <th>Latest · {date(liveTrend?.as_of)}</th>
                </tr>
              </thead>
              <tbody>
                {signalTickers.map((ticker) => (
                  <tr key={ticker}>
                    <td className="mono">{ticker}</td>
                    {signalCell(signals[ticker])}
                    {signalCell(liveSignals[ticker])}
                  </tr>
                ))}
                <tr>
                  <td colSpan={3} style={{ paddingTop: 12, fontWeight: 600 }}>
                    {window}d trend overlay
                  </td>
                </tr>
                {sleeveTickers.map((ticker) => (
                  <tr key={ticker}>
                    <td className="mono">{ticker}</td>
                    {sleeveCell(sleeves[ticker], windowsList)}
                    {sleeveCell(liveSleeves[ticker], windowsList)}
                  </tr>
                ))}
                {hysteresis.pending_book ? (
                  <tr>
                    <td>Pending switch</td>
                    <td colSpan={2} className="muted">
                      {String(hysteresis.pending_book)} —{" "}
                      {String(hysteresis.pending_count ?? "?")}/
                      {String(hysteresis.confirm_decisions ?? "?")} confirmations
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          {p.reasoning ? (
            <div className="muted" style={{ marginTop: 10, fontSize: 12.5 }}>
              {String(p.reasoning)}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function Stack() {
  const { data, error, isPending, isFetching } = useQuery({
    queryKey: ["stack"],
    queryFn: () => get<StackPayload>("/api/stack"),
  });

  if (error) return <ErrorState error={error} />;
  if (isPending) return <div className="empty">Loading…</div>;

  const latest = data.decisions[0];
  const drawdownRule = data.profile?.max_drawdown_pct;
  const heldState = latest ? outcomeOf(latest.payload) : null;
  const heldBook = latest ? String(latest.payload.held_book ?? "") : null;
  // The stack's OWN first NAV point — not the literal "1993-11-01" — so the
  // comparison chart's rebase date tracks the calendar `market_signal.
  // stack_calendar` actually derives, wherever that anchor is today. Stays
  // the FLOOR of the slider below and the default when it has not moved.
  const stackInception = data.nav.find((p) => typeof p.nav === "number")?.ts ?? null;
  const lastNavPoint = [...data.nav].reverse().find((p) => typeof p.nav === "number") ?? null;
  const startYear = stackInception ? Number(stackInception.slice(0, 4)) : null;
  const endYear = lastNavPoint ? Number(lastNavPoint.ts.slice(0, 4)) : null;
  // YEAR GRANULARITY, not a day-by-day slider (owner, 2026-08-19: "un slider
  // ... pour que je puisse demarrer la date n'importe quand"). A ~35-year
  // daily series has ~9000 points; dragging by year is the useful grain for
  // "how does this look since the 2008 drawdown", and null (unmoved) means
  // the full history rather than forcing a choice on first load.
  const [chartStartYear, setChartStartYear] = useState<number | null>(null);
  const chartStart =
    chartStartYear !== null ? `${chartStartYear}-01-01` : stackInception;
  // Computed once, not per benchmark in the map below — `chainOnto` needs
  // spy-USD's OWN rebased curve to chain aaaf-r-USD onto, and every other
  // series in the map ignores it.
  const spyRebased = rebaseFrom(data.benchmarks.find((s) => s.id === "spy-USD")?.nav ?? [], chartStart);

  return (
    <div className={isFetching ? "stale" : undefined}>
      <div className="page-head">
        <div>
          <h1>Market-signal stack</h1>
          <p>
            The live allocation path (ADR-007): a monthly, market-priced decision. Paper
            throughout — V1 executes nothing, the owner places these orders by hand.
          </p>
        </div>
      </div>

      {/* RETURN AND SHARPE, ON EVERY FRONT (CLAUDE.md "Binding caps" neighbor
          rule, extended here 2026-08-15): this page showed the decision but
          never what the stack earned. Same four indicators as the Ranking
          row, read off the same portfolio row the ranking job wrote.
          3y RETURN, NOT 1y (owner correction, same day): Sortino/Sharpe/Calmar
          are the 756-trading-day (3y) rolling window; pairing them with a 1y
          return compares two different periods and made the stack read as the
          worst performer on the Ranking page when its 3y figure agrees with
          its Sortino. 1y stays as the tile's `sub` line — recency, not a
          competing headline. */}
      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <Tile
          label="3y return"
          value={pct(data.stack_portfolio?.return_3y)}
          sub={`1y ${pct(data.stack_portfolio?.return_1y)}`}
        />
        <Tile label="Sharpe" value={num(data.stack_portfolio?.sharpe_rolling)} />
        <Tile label="Sortino" value={num(data.stack_portfolio?.sortino_rolling)} />
        <Tile label="Calmar" value={num(data.stack_portfolio?.calmar_rolling)} />
      </div>

      {latest ? (
        <LatestDecision decision={latest} liveTrend={data.live_trend ?? null} />
      ) : (
        <div className="card">
          <div className="empty">No market-signal decision has been journalled yet.</div>
        </div>
      )}

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <div className="card">
          <h2>Stack paper NAV vs the benchmarks</h2>
          {startYear !== null && endYear !== null && endYear > startYear ? (
            <div style={{ margin: "2px 0 12px" }}>
              <input
                type="range"
                min={startYear}
                max={endYear}
                step={1}
                value={chartStartYear ?? startYear}
                onChange={(e) => setChartStartYear(Number(e.target.value))}
                style={{ width: "100%" }}
                aria-label="Chart start year"
              />
              <div className="muted" style={{ fontSize: 12, display: "flex", justifyContent: "space-between" }}>
                <span>{startYear}</span>
                <span>
                  starts {date(chartStart)}
                  {chartStartYear !== null ? (
                    <>
                      {" "}
                      ·{" "}
                      <a
                        href="#"
                        onClick={(e) => {
                          e.preventDefault();
                          setChartStartYear(null);
                        }}
                      >
                        reset
                      </a>
                    </>
                  ) : null}
                </span>
                <span>{endYear}</span>
              </div>
            </div>
          ) : null}
          <NavChart
            points={rebaseFrom(data.nav, chartStart)}
            color="var(--series-3)"
            label="Market-signal stack"
            series={data.benchmarks.map((b): NamedNavSeries => {
              // AAAF-R'S UNIVERSE (IWM/IYR) HAS NO PRICE THIS EARLY, so it
              // cannot be rebased to the chart's start like the other three
              // WHILE that start predates its own history — chained onto
              // spy-USD's already-rebased curve instead, so it picks up
              // where SPY was rather than resetting to 100 on an axis the
              // other series don't restart on. Once the slider moves PAST
              // aaaf-r-USD's own inception, it has its own price there and
              // rebases normally like everyone else.
              const points =
                b.id === "aaaf-r-USD" && !startsOnOrBefore(b.nav, chartStart)
                  ? chainOnto(b.nav, spyRebased)
                  : rebaseFrom(b.nav, chartStart);
              return {
                id: b.id,
                label: b.name,
                color: BENCHMARK_COLOR[b.id] ?? "var(--series-1)",
                points,
              };
            })}
          />
          <div className="paper-note">
            All five series share one base: the stack, All Weather, spy-USD and
            ms-trend-baseline rebased to 100 at the chart&apos;s start ({chartStart ?? "—"}
            ) — drag the slider above to move it; the fair comparison at the stack&apos;s own
            inception is the default, since spy-USD and All Weather otherwise get years of
            extra compounding on the chart that have nothing to do with either
            strategy&apos;s merit. Each portfolio&apos;s own detail page still shows its
            full since-inception curve. aaaf-r-USD&apos;s universe has no price before
            ~2000: while the chart starts earlier than that, its curve is instead CHAINED
            onto spy-USD&apos;s rebased level at the date its own history begins — its
            shape is unchanged, only where it starts reading on this shared axis. The -
            {Math.abs(Number(drawdownRule ?? 25))}% drawdown rule is measured on the
            stack&apos;s own 36-month rolling drawdown and raises an ALERT, not a block
            (ADR-009): refusing a proposal cannot exit a position, only freeze one.
          </div>
        </div>

        <div className="card">
          <h2>Decision timeline</h2>
          <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Book</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {data.decisions.map((d) => {
                  const outcome = outcomeOf(d.payload);
                  return (
                    <tr key={d.id}>
                      <td className="mono">{date(d.payload.decision_date ?? d.event_date)}</td>
                      <td>{String(d.payload.held_book ?? "—")}</td>
                      <td>
                        <OutcomeChip outcome={outcome} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="paper-note">
            One row per decision date — holding months included, so a month that did not move
            is visibly a decision rather than a gap.
          </div>
        </div>
      </div>

      {/*
       * THE FOUR BOOKS THE STACK SWITCHES BETWEEN, each with its own
       * performance. They are DISABLED portfolios — the stack holds one
       * allocation at a time rather than four positions — so they never
       * appear in /api/ranking, which lists enabled rows only. Without this
       * table there was no way to see how the held book compares to the
       * other three, or what any of them have actually returned.
       */}
      <div className="card" style={{ marginTop: 14 }}>
        <h2>The four books</h2>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Book</th>
                <th>Base allocation</th>
                <th className="num">3y return</th>
                <th className="num">Sharpe</th>
                <th className="num">Sortino</th>
                <th className="num">Calmar</th>
                <th className="num muted">1y return</th>
              </tr>
            </thead>
            <tbody>
              {data.books.map((book) => {
                const held = heldState !== "blocked" && book.signal_state === heldBook;
                const allocation = (book.allocation ?? {}) as Record<string, number>;
                return (
                  <tr key={book.id}>
                    <td>
                      {String(book.name ?? book.id)}
                      {held ? <span className="badge defender">held</span> : null}
                    </td>
                    <td className="mono muted">
                      {/* The RAW, static weights this book is defined with — not
                          what the stack currently targets. The two differ
                          whenever the trend overlay redirects a sleeve to the
                          haven; see the note below and "The order to place"
                          above, where cash 40 replaces this row's IEF 40. */}
                      {Object.entries(allocation)
                        .map(([ticker, w]) => `${ticker} ${weight(w)}`)
                        .join(" · ") || "—"}
                    </td>
                    <td className="num">{pct(book.return_3y)}</td>
                    <td className="num">{num(book.sharpe_rolling)}</td>
                    <td className="num">{num(book.sortino_rolling)}</td>
                    <td className="num">{num(book.calmar_rolling)}</td>
                    <td className="num muted">{pct(book.return_1y)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="paper-note">
          Disabled portfolios, shadow-tracked for comparison — the stack switches between
          these allocations rather than holding them as separate positions. A book&apos;s
          return here is what ALWAYS holding its raw allocation would have earned; the
          stack&apos;s own return above is what it actually did, which differs whenever it
          held a DIFFERENT book during the window, or the trend overlay redirected a sleeve
          of this one to cash (as it currently does — compare this row&apos;s allocation to
          &quot;the order to place&quot; above). Each switch between books also costs 46bps
          that a static book never pays.
        </div>
      </div>

      <div className="grid cols-4" style={{ marginTop: 14 }}>
        <Tile label="Decisions journalled" value={String(data.decisions.length)} />
        <Tile
          label="Blocked"
          value={String(data.decisions.filter((d) => outcomeOf(d.payload) === "blocked").length)}
        />
        <Tile
          label="Months moved"
          value={String(data.decisions.filter((d) => outcomeOf(d.payload) === "moved").length)}
        />
      </div>
    </div>
  );
}
