import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { get, type RankingPayload, type Row } from "../api";
import { ErrorState } from "../components/States";
import { num, pct, signClass } from "../format";

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
 * stays muted, as the recency signal it actually is rather than a
 * period-matched ranking dimension — but sortable like every other column
 * (owner, 2026-08-18), because "muted" is a READING hint, not a permission.
 *
 * ONE ARRAY DRIVES BOTH THE HEADER ROW AND THE BODY ROW, deliberately, after
 * this table's header/body markup drifted out of sync three times in one
 * afternoon (CAGR passing Recommendation, Volatility passing NAV, 1y return
 * passing everything) — CLAUDE.md "WHEN A SECOND ONE ARRIVES, FIND WHAT NAMED
 * THE FIRST" applies to a column list, not just a constant. `#` (rank) stays
 * OUTSIDE this array: its click resets to the STORED order rather than
 * sorting client-side, a different action from every other header here.
 */

type SortKey =
  | "rank"
  | "portfolio_id"
  | "return_3y"
  | "return_1y"
  | "cagr"
  | "sortino_rolling"
  | "sharpe_rolling"
  | "calmar_rolling"
  | "max_drawdown"
  | "volatility";

interface Column {
  key: SortKey;
  label: string;
  title?: string;
  numeric?: boolean; // default true — right-aligned; Portfolio is the one exception
  muted?: boolean; // dims the header + cell — recency/secondary reading, not a permission
  signed?: boolean; // color the cell by sign (return/drawdown columns)
  // `benchmarks` (`data.benchmark_ids`) is only read by the Portfolio column's
  // badges — a parameter rather than a closure so `COLUMNS` stays a module-level
  // constant instead of being rebuilt (and its render closures re-allocated) on
  // every render.
  render: (row: Row, benchmarks: string[]) => ReactNode;
}

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

const COLUMNS: Column[] = [
  {
    key: "portfolio_id",
    label: "Portfolio",
    numeric: false,
    render: (row, benchmarks) => (
      <>
        <Link to={`/portfolio/${String(row.portfolio_id)}`}>{String(row.portfolio_id)}</Link>
        <Badges row={row} benchmarks={benchmarks} />
      </>
    ),
  },
  {
    key: "return_3y",
    label: "3y return",
    signed: true,
    render: (row) => pct(row.return_3y),
  },
  {
    key: "return_1y",
    label: "1y return",
    muted: true,
    title: "Recency only — a single noisy year, not the same period as the 3y/756-day columns",
    render: (row) => pct(row.return_1y),
  },
  {
    key: "cagr",
    label: "CAGR",
    signed: true,
    title: "Since-inception (NAV_end/NAV_start)^(252/n) - 1, not the 756-day window",
    render: (row) => pct(row.cagr),
  },
  {
    key: "sortino_rolling",
    label: "Sortino",
    render: (row) => num(row.sortino_rolling),
  },
  {
    key: "sharpe_rolling",
    label: "Sharpe",
    render: (row) => num(row.sharpe_rolling),
  },
  {
    key: "calmar_rolling",
    label: "Calmar",
    render: (row) => num(row.calmar_rolling),
  },
  {
    key: "max_drawdown",
    label: "Max DD",
    signed: true,
    render: (row) => pct(row.max_drawdown),
  },
  {
    key: "volatility",
    label: "Volatility",
    title: "std(daily return) x sqrt(252) over the pinned 756-day window",
    render: (row) => pct(row.volatility, false),
  },
];

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
    if (sort === "portfolio_id") {
      rows.sort((a, b) => String(a.portfolio_id).localeCompare(String(b.portfolio_id)));
    } else {
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
                    style={sort === "rank" ? { color: "var(--ink)" } : undefined}
                  >
                    #
                  </th>
                  {COLUMNS.map((c) => (
                    <th
                      key={c.key}
                      className={["sortable", c.numeric === false ? "" : "num", c.muted ? "muted" : ""]
                        .filter(Boolean)
                        .join(" ")}
                      onClick={() => setSort(c.key)}
                      title={c.title}
                      style={sort === c.key ? { color: "var(--ink)" } : undefined}
                    >
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={String(row.portfolio_id)}>
                    <td className="num muted">{String(row.rank)}</td>
                    {COLUMNS.map((c) => (
                      <td
                        key={c.key}
                        className={[
                          c.numeric === false ? "" : "num",
                          c.muted ? "muted" : "",
                          c.signed ? signClass(row[c.key]) : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                      >
                        {c.render(row, data.benchmark_ids)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="paper-note">
            Volatility is annualized std(daily return) over the same pinned 756-day
            window as Sortino/Sharpe/Calmar; CAGR is SINCE INCEPTION, a different (and
            usually longer) window — the two are not measuring the same period, so
            reading them as a pair understates or overstates volatility-adjusted return
            depending on the row. Both are — unlike a raw NAV level — directly
            comparable across rows regardless of each portfolio&apos;s own inception
            date. All figures paper, all USD, all charged 23 bps per order.
          </div>
        </div>
      )}
    </div>
  );
}
