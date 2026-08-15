import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, MinusCircle, ShieldAlert } from "lucide-react";
import { get, type Row, type StackDecision, type StackPayload } from "../api";
import { Tile } from "../components/Bits";
import { NavChart } from "../components/NavChart";
import { ErrorState } from "../components/States";
import { date, num, pct, weight } from "../format";

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

function LatestDecision({ decision }: { decision: StackDecision }) {
  const p = decision.payload;
  const outcome = outcomeOf(p);
  const held = (p.held_allocation ?? {}) as Record<string, number>;
  const target = (p.target_allocation ?? {}) as Record<string, number>;
  const tickers = Array.from(new Set([...Object.keys(held), ...Object.keys(target)])).sort();
  const signals = (p.signals ?? {}) as Record<string, Row>;
  const overlay = (p.trend_overlay ?? {}) as Row;
  const hysteresis = (p.hysteresis ?? {}) as Row;
  const below = (overlay.below_trend ?? []) as string[];
  const windows = (overlay.windows_days ?? null) as number[] | null;
  const window = windows ? windows.join("/") : String(overlay.window_days ?? "—");
  const sleeves = (overlay.sleeves ?? {}) as Record<string, Row>;
  const decisionDate = typeof p.decision_date === "string" ? p.decision_date : null;
  // MONTHLY CADENCE means a below/above reading can be days to weeks stale by
  // the time it is read — a sleeve can cross back the other way in the
  // meantime without the decision re-running (it only does monthly). Flagged
  // rather than silently trusted, because a reading close to a threshold reads
  // as fact until someone checks the date and finds it is three weeks old.
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
              <tbody>
                {Object.entries(signals).map(([ticker, read]) => (
                  <tr key={ticker}>
                    <td className="mono">{ticker}</td>
                    <td className="num">{num(read.value, 2)}</td>
                    <td className="muted">
                      vs 10y median {num(read.trailing_median, 2)}
                    </td>
                    {/* The vintage date, shown: the value was KNOWABLE then
                        (ADR-003), which is what makes the replay honest. */}
                    <td className="muted mono">knowable {date(read.knowable_at)}</td>
                  </tr>
                ))}
                <tr>
                  <td colSpan={4} style={{ paddingTop: 12, fontWeight: 600 }}>
                    {window}d trend overlay — as of {date(decisionDate)}
                    {daysSinceDecision !== null && daysSinceDecision > 0 ? (
                      <span className="muted" style={{ fontWeight: 400 }}>
                        {" "}
                        ({daysSinceDecision}d ago — a sleeve can cross back before the next
                        monthly decision reads it)
                      </span>
                    ) : null}
                  </td>
                </tr>
                {Object.entries(sleeves).map(([ticker, read]) => (
                  <tr key={ticker}>
                    <td className="mono">
                      {ticker}
                      {below.includes(ticker) ? (
                        <span className="badge excluded">below</span>
                      ) : null}
                    </td>
                    <td className="num">{num(read.price, 0)}</td>
                    <td className="muted">vs {window}d avg {num(read.moving_average, 0)}</td>
                    <td className="muted mono">knowable {date(read.knowable_at)}</td>
                  </tr>
                ))}
                {hysteresis.pending_book ? (
                  <tr>
                    <td colSpan={2}>Pending switch</td>
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
        <LatestDecision decision={latest} />
      ) : (
        <div className="card">
          <div className="empty">No market-signal decision has been journalled yet.</div>
        </div>
      )}

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <div className="card">
          <h2>Stack paper NAV</h2>
          <NavChart points={data.nav} color="var(--series-3)" />
          <div className="paper-note">
            The -{Math.abs(Number(drawdownRule ?? 25))}% drawdown rule is measured on the
            36-month rolling drawdown and raises an ALERT, not a block (ADR-009): refusing a
            proposal cannot exit a position, only freeze one.
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
