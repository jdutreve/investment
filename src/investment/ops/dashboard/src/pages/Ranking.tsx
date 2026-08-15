import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { get, type RankingPayload, type Row } from "../api";
import { ErrorState } from "../components/States";
import { nav, num, pct, signClass } from "../format";

/*
 * THE STORED ORDER IS THE ORDER OF RECORD. The ranking job wrote `rank`
 * (`mechanical/snapshots.py`) under a rule that is not a plain sort — Sortino
 * ties are GROUPED, not compared pairwise, because a pairwise "within 0.02" test
 * is not transitive and admits no consistent order. Re-deriving that here would
 * be a second implementation of a subtle rule; sorting by a column is offered as
 * a VIEW, and the rank column always shows what the agent decided.
 *
 * FOUR BADGES, FOUR SEPARATE RULES, and the page must not merge them:
 *   defender  — the incumbent, ranked with everyone else and never privileged
 *   benchmark — a KIND (`seed_data.BENCHMARK_PORTFOLIOS`), ranked so it is seen
 *               and refused by kind so it can never be proposed
 *   demoted   — calmar_rolling < 1.0, sent to the bottom
 *   excluded  — breaches the user drawdown rule: still ranked, barred from the
 *               defender role and from proposal candidacy
 * The last two both bar candidacy but by different mechanisms, and benchmark
 * carries no flag at all — the flag on the snapshot means the drawdown one.
 *
 * THE RETURN COLUMN IS 3y, NOT 1y — owner correction, 2026-08-15: "la durée du
 * rendement doit être calée sur celle du risque". Sortino/Sharpe/Calmar/Max DD
 * are computed on the pinned 756-TRADING-DAY rolling window (CLAUDE.md
 * "Mechanical calculations"), which is exactly 3 calendar years (756 / 252).
 * Putting `return_1y` in the same row made `ms-stack` read as the worst
 * performer on the table — a single noisy year beside three-year risk figures
 * — when its 3y figure and its Sortino agree with each other. `return_1y`
 * stays, muted and separate, as the recency signal it actually is; it is not
 * sortable, so it cannot be mistaken for a ranking dimension the way the
 * period-matched columns are.
 */

type SortKey = "rank" | "return_3y" | "sortino_rolling" | "sharpe_rolling" | "calmar_rolling";

const COLUMNS: Array<{ key: SortKey; label: string }> = [
  { key: "return_3y", label: "3y return" },
  { key: "sortino_rolling", label: "Sortino" },
  { key: "sharpe_rolling", label: "Sharpe" },
  { key: "calmar_rolling", label: "Calmar" },
];

function Badges({ row, benchmarks }: { row: Row; benchmarks: string[] }) {
  const id = String(row.portfolio_id);
  const calmar = row.calmar_rolling;
  const demoted = typeof calmar === "number" && calmar < 1.0;
  return (
    <>
      {row.defender ? <span className="badge defender">defender</span> : null}
      {benchmarks.includes(id) ? <span className="badge benchmark">benchmark</span> : null}
      {demoted ? <span className="badge demoted">demoted</span> : null}
      {row.excluded_from_candidacy ? (
        <span className="badge excluded" title="breaches the user drawdown rule">
          excluded
        </span>
      ) : null}
    </>
  );
}

export function Ranking() {
  const [snapshotDate, setSnapshotDate] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("rank");
  const { data, error, isPending, isFetching } = useQuery({
    queryKey: ["ranking", snapshotDate],
    queryFn: () =>
      get<RankingPayload>(`/api/ranking${snapshotDate ? `?date=${snapshotDate}` : ""}`),
  });

  if (error) return <ErrorState error={error} />;
  if (isPending) return <div className="empty">Loading…</div>;

  const rows = [...data.rows];
  if (sort !== "rank") {
    // A missing indicator sorts LAST in either direction rather than as a zero:
    // an unmeasured Sortino is not the worst one.
    rows.sort((a, b) => {
      const av = a[sort];
      const bv = b[sort];
      if (typeof av !== "number") return 1;
      if (typeof bv !== "number") return -1;
      return bv - av;
    });
  }

  return (
    <div className={isFetching ? "stale" : undefined}>
      <div className="page-head">
        <div>
          <h1>Ranking</h1>
          <p>
            All enabled portfolios ranked together — defender included, never privileged.
            Benchmarks are ranked so they are seen, and can never be proposed.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {sort !== "rank" ? (
            <button className="button" onClick={() => setSort("rank")}>
              Reset to agent order
            </button>
          ) : null}
          <select
            className="select"
            value={snapshotDate ?? data.date ?? ""}
            onChange={(e) => setSnapshotDate(e.target.value)}
          >
            {data.available_dates.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="card">
          <div className="empty">No ranking snapshot yet — the weekly chain has not run.</div>
        </div>
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th
                    className="sortable num"
                    onClick={() => setSort("rank")}
                    title="the order the ranking job wrote"
                  >
                    #
                  </th>
                  <th>Portfolio</th>
                  {COLUMNS.map((c) => (
                    <th
                      key={c.key}
                      className="sortable num"
                      onClick={() => setSort(c.key)}
                      style={sort === c.key ? { color: "var(--ink)" } : undefined}
                    >
                      {c.label}
                    </th>
                  ))}
                  <th className="num">Max DD</th>
                  <th className="num muted" title="Recency only — a single noisy year, not a ranking dimension">
                    1y return
                  </th>
                  <th className="num">NAV</th>
                  <th>Recommendation</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={String(row.portfolio_id)}>
                    <td className="num muted">{String(row.rank)}</td>
                    <td>
                      <Link to={`/portfolio/${String(row.portfolio_id)}`}>
                        {String(row.portfolio_id)}
                      </Link>
                      <Badges row={row} benchmarks={data.benchmark_ids} />
                    </td>
                    <td className={`num ${signClass(row.return_3y)}`}>{pct(row.return_3y)}</td>
                    <td className="num">{num(row.sortino_rolling)}</td>
                    <td className="num">{num(row.sharpe_rolling)}</td>
                    <td className="num">{num(row.calmar_rolling)}</td>
                    <td className={`num ${signClass(row.max_drawdown)}`}>
                      {pct(row.max_drawdown)}
                    </td>
                    <td className="num muted">{pct(row.return_1y)}</td>
                    <td className="num">{nav(row.nav)}</td>
                    <td className="muted">{String(row.recommendation ?? "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="paper-note">
            NAV is base 100 at each portfolio&apos;s OWN inception, so levels are not
            comparable across rows — a 1511 over forty years and a 1533 over
            thirty-five are not the same performance. All figures paper, all USD, all
            charged {""}
            23 bps per order.
          </div>
        </div>
      )}
    </div>
  );
}
