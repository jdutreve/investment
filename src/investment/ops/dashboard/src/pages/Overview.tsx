import { useQuery } from "@tanstack/react-query";
import { Bell, Compass, Layers, Scale, Target } from "lucide-react";
import { Link } from "react-router-dom";
import { get, type Overview as OverviewData, type Row } from "../api";
import { AlertList, Tile } from "../components/Bits";
import { ErrorState } from "../components/States";
import { date, num, pct, pctPoints, signClass, weight } from "../format";

/*
 * THE DIGEST, LAID OUT. Every figure here comes from `/api/overview`, which
 * serves `telegram/digest.collect_digest_inputs` — the same call the weekly
 * Telegram digest renders. That is what makes "the fronts agree" structural
 * rather than a convention someone has to maintain: there is one assembly, and
 * `DigestInputs` is a TypedDict, so a field added for one front and not the
 * other fails mypy before it reaches a reader.
 */

const s = (row: Row | null | undefined, key: string): unknown => row?.[key];

function RegimeCard({ regime, liquidity }: { regime: Row; liquidity: Row }) {
  const name = s(regime, "regime_name") ?? s(regime, "regime_type_id");
  return (
    <div className="card">
      <h2>
        <Compass size={14} /> Regime
      </h2>
      {name ? (
        <>
          <div style={{ fontSize: 18, fontWeight: 620, letterSpacing: "-0.015em" }}>
            {String(name)}
          </div>
          <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
            {/* percent POINTS, not a fraction — see format.ts */}
            confidence {pctPoints(s(regime, "confidence"))} · global liquidity level{" "}
            {num(s(liquidity, "level"))}, speed {num(s(liquidity, "speed"))}
          </div>
        </>
      ) : (
        <div className="empty">No current regime recorded — the detector has not run.</div>
      )}
    </div>
  );
}

function StackCard({ stack }: { stack: Row | null }) {
  return (
    <div className="card">
      <h2>
        <Layers size={14} /> Market-signal stack
      </h2>
      {stack ? (
        <>
          <div className="grid cols-4">
            <Tile label="Sortino" value={num(s(stack, "sortino_rolling"))} />
            <Tile label="Calmar" value={num(s(stack, "calmar_rolling"))} />
            <Tile label="Deepest drawdown 36M" value={pct(s(stack, "drawdown"))} />
          </div>
          {/* The label is not modesty: `ms-stack`'s NAV is built from the
              decision walk and assumes every monthly decision filled at the
              close of its anchor date. V1 executes nothing. */}
          <div className="paper-note">
            PAPER — what the strategy would have done, never a statement about the
            account. Deepest drawdown inside the trailing 756 days, not today&apos;s
            distance from a high.
          </div>
        </>
      ) : (
        <div className="empty">The stack has no standing yet.</div>
      )}
    </div>
  );
}

function ScoreboardCard({ scoreboard }: { scoreboard: OverviewData["scoreboard"] }) {
  const [won, decided] = scoreboard.hit_rate;
  return (
    <div className="card">
      <h2>
        <Target size={14} /> Scoreboard
      </h2>
      <div className="grid cols-4">
        <Tile
          label="Hit rate at +12w"
          /* Never a percentage of zero: with nothing decided there is no rate,
             and "0%" would read as a losing agent rather than a young one. */
          value={decided ? `${won}/${decided}` : "—"}
          sub={decided ? pct(won / decided, false) : "nothing decided yet"}
        />
        <Tile label="Paper-tests running" value={String(scoreboard.paper_tests.length)} />
        <Tile label="Strategies in probation" value={String(scoreboard.probations.length)} />
      </div>
    </div>
  );
}

function DefenderCard({ defender }: { defender: Row | null }) {
  return (
    <div className="card">
      <h2>
        <Scale size={14} /> Defender
      </h2>
      {defender ? (
        <>
          <div style={{ fontSize: 16, fontWeight: 620 }}>
            <Link to={`/portfolio/${String(s(defender, "portfolio_id"))}`}>
              {String(s(defender, "portfolio_id"))}
            </Link>
          </div>
          <div className="grid cols-4" style={{ marginTop: 10 }}>
            {/* 3y, matched to Sortino/Sharpe/Calmar's 756-day window; 1y rides
                as recency context (owner, 2026-08-15). */}
            <Tile
              label="3y return"
              value={pct(s(defender, "return_3y"))}
              sub={`1y ${pct(s(defender, "return_1y"))}`}
            />
            <Tile label="Sortino" value={num(s(defender, "sortino_rolling"))} />
            <Tile label="Sharpe" value={num(s(defender, "sharpe_rolling"))} />
            <Tile label="Calmar" value={num(s(defender, "calmar_rolling"))} />
          </div>
        </>
      ) : (
        <div className="empty">No defender in the latest snapshot.</div>
      )}
    </div>
  );
}

function DecisionCard({
  decision,
  reading,
}: {
  decision: Row | null;
  reading: Row | null;
}) {
  if (!decision) {
    return (
      <div className="card">
        <h2>
          <Bell size={14} /> Latest allocation decision
        </h2>
        <div className="empty">No market-signal decision recorded yet.</div>
      </div>
    );
  }
  const gate = s(decision, "gate");
  const blocked = Boolean(gate) && gate !== "passed";
  const held = (s(decision, "held_allocation") ?? {}) as Record<string, number>;
  const target = (s(decision, "target_allocation") ?? {}) as Record<string, number>;
  const moves = Array.from(new Set([...Object.keys(held), ...Object.keys(target)]))
    .filter((t) => (held[t] ?? 0) !== (target[t] ?? 0))
    .sort();
  const readDate = s(reading, "market_signal_decision_date");
  const stale = Boolean(readDate) && readDate !== s(decision, "decision_date");

  return (
    <div className="card">
      <h2>
        <Bell size={14} /> Latest allocation decision
      </h2>
      <div style={{ fontSize: 16, fontWeight: 620 }}>
        {/* "target book" whenever a gate refused: naming a target as though it
            were a position is the error ADR-011's pass removed elsewhere. */}
        {blocked ? "Target book" : "Book"} {String(s(decision, "held_book") ?? "?")}
        <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>
          {" "}
          · decided {date(s(decision, "decision_date"))}
        </span>
      </div>
      {blocked ? (
        <div className="alert critical" style={{ marginTop: 10 }}>
          <div>
            <span className="level">blocked</span>
            <div>
              Gate <span className="mono">{String(gate)}</span> refused this decision — nothing
              was proposed. The stack is FROZEN in its previous book, not in the one named
              above.
            </div>
          </div>
        </div>
      ) : null}
      <div style={{ marginTop: 10 }}>
        {moves.length ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th className="num">Held</th>
                  <th className="num">Target</th>
                </tr>
              </thead>
              <tbody>
                {moves.map((ticker) => (
                  <tr key={ticker}>
                    <td className="mono">{ticker}</td>
                    <td className="num">{weight(held[ticker] ?? 0)}</td>
                    <td className="num">{weight(target[ticker] ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="muted">No change — the stack holds its book.</div>
        )}
      </div>
      {/* Same rule as the digest: a decision corrected after the fact says so,
          on the block whose numbers the correction moved. */}
      {s(decision, "correction_note") ? (
        <div style={{ marginTop: 10, fontSize: 12.5 }}>
          <strong>Corrected</strong>
          <div className="muted">{String(s(decision, "correction_note"))}</div>
        </div>
      ) : null}
      {s(decision, "reasoning") ? (
        <div className="muted" style={{ marginTop: 10, fontSize: 12.5 }}>
          {String(s(decision, "reasoning"))}
        </div>
      ) : null}
      {s(reading, "market_signal_assessment") ? (
        <div style={{ marginTop: 10, fontSize: 12.5 }}>
          <strong>Worker challenge</strong>
          {stale ? (
            <span className="muted"> (reading of the {date(readDate)} decision — not this one)</span>
          ) : null}
          <div className="muted">{String(s(reading, "market_signal_assessment"))}</div>
        </div>
      ) : null}
      <div style={{ marginTop: 10 }}>
        <Link className="freshness" to="/stack">
          Full decision timeline →
        </Link>
      </div>
    </div>
  );
}

export function Overview() {
  const { data, error, isPending, isFetching } = useQuery({
    queryKey: ["overview"],
    queryFn: () => get<OverviewData>("/api/overview"),
  });

  if (error) return <ErrorState error={error} />;
  if (isPending) return <div className="empty">Loading…</div>;

  const defender = data.ranking.find((r) => r.defender) ?? data.defender_metrics;

  return (
    /* Held at reduced opacity on refetch rather than replaced by a skeleton —
       no layout jump, and the previous numbers stay readable while new ones
       arrive. */
    <div className={isFetching ? "stale" : undefined}>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>The weekly digest, laid out. Same rows, same numbers, same alerts.</p>
        </div>
      </div>

      <div style={{ marginBottom: 14 }}>
        <AlertList alerts={data.alerts} />
      </div>

      <div className="grid cols-2">
        <RegimeCard regime={data.regime} liquidity={data.global_liquidity} />
        <StackCard stack={data.stack} />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <DecisionCard decision={data.market_signal} reading={data.worker_reading} />
        <div className="grid" style={{ gap: 14, alignContent: "start" }}>
          <DefenderCard defender={defender ?? null} />
          <ScoreboardCard scoreboard={data.scoreboard} />
        </div>
      </div>

      {data.invariants.length ? (
        <div className="card" style={{ marginTop: 14 }}>
          <h2>Heaviest integrated invariants</h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Invariant</th>
                  <th>Author</th>
                  <th className="num">Weight</th>
                  <th className="num">Confirmed</th>
                  <th className="num">Infirmed</th>
                </tr>
              </thead>
              <tbody>
                {data.invariants.map((inv, i) => (
                  <tr key={String(inv.title ?? i)}>
                    <td>{String(inv.title ?? "")}</td>
                    <td className="muted">{String(inv.author ?? "—")}</td>
                    <td className="num">{num(inv.weight_effective)}</td>
                    <td className="num">{String(inv.confirmation_count ?? 0)}</td>
                    <td className={`num ${signClass(-(Number(inv.infirmation_count) || 0))}`}>
                      {String(inv.infirmation_count ?? 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
