import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { get, type PortfolioPayload, type Row } from "../api";
import { Tile } from "../components/Bits";
import { NavChart } from "../components/NavChart";
import { ErrorState } from "../components/States";
import { nav, num, pct, pctPoints, weight } from "../format";

/*
 * ALLOCATION GROUPED BY ASSET CLASS, not by ticker, because that is what the
 * concentration cap actually binds: `SPY 50 / VTI 20` is 70% of one exposure,
 * and a page listing tickers would show two compliant lines where the rule sees
 * one breach (CLAUDE.md "Binding caps"; `gates.concentration_ok` has counted
 * classes rather than line items since 2026-08-14).
 */

const ASSET_CLASS: Record<string, string> = {
  SPY: "US equity",
  VTI: "US equity",
  QQQ: "US equity",
  EFA: "Intl equity",
  VEA: "Intl equity",
  EEM: "EM equity",
  IEF: "Treasuries",
  TLT: "Treasuries",
  SHY: "Treasuries",
  BIL: "Cash",
  TIP: "TIPS",
  VTIP: "TIPS",
  LQD: "Credit",
  VCIT: "Credit",
  HYG: "Credit",
  GLD: "Gold",
  IAU: "Gold",
  DBC: "Commodities",
  GSG: "Commodities",
  VNQ: "Real estate",
};

function classOf(ticker: string): string {
  return ASSET_CLASS[ticker] ?? "Other";
}

function AllocationTable({ allocation }: { allocation: Record<string, number> }) {
  const byClass = new Map<string, Array<[string, number]>>();
  for (const [ticker, w] of Object.entries(allocation)) {
    const cls = classOf(ticker);
    byClass.set(cls, [...(byClass.get(cls) ?? []), [ticker, w]]);
  }
  const classes = [...byClass.entries()]
    .map(([cls, items]) => ({
      cls,
      items,
      total: items.reduce((sum, [, w]) => sum + w, 0),
    }))
    .sort((a, b) => b.total - a.total);
  const largest = classes[0];

  return (
    <>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Asset class</th>
              <th>Tickers</th>
              <th className="num">Weight</th>
            </tr>
          </thead>
          <tbody>
            {classes.map(({ cls, items, total }) => (
              <tr key={cls}>
                <td>{cls}</td>
                <td className="mono muted">
                  {items.map(([t, w]) => `${t} ${weight(w)}`).join(" · ")}
                </td>
                <td className="num">{weight(total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {largest ? (
        <div className="paper-note">
          Largest asset class: {largest.cls} at {weight(largest.total)}% — this is the
          number the concentration cap binds, not the largest ticker.
        </div>
      ) : null}
    </>
  );
}

export function PortfolioDetail() {
  const { portfolioId = "" } = useParams();
  const { data, error, isPending } = useQuery({
    queryKey: ["portfolio", portfolioId],
    queryFn: () => get<PortfolioPayload>(`/api/portfolio/${encodeURIComponent(portfolioId)}`),
  });

  if (error) return <ErrorState error={error} />;
  if (isPending) return <div className="empty">Loading…</div>;

  const p: Row = data.portfolio;
  const snap = data.latest_snapshot;
  const allocation = (p.allocation ?? {}) as Record<string, number>;
  const inception = data.nav[0]?.ts?.slice(0, 10);

  return (
    <div>
      <div className="page-head">
        <div>
          <Link className="freshness" to="/ranking">
            <ArrowLeft size={12} style={{ verticalAlign: "-1px" }} /> Ranking
          </Link>
          <h1 style={{ marginTop: 4 }}>{String(p.name ?? portfolioId)}</h1>
          <p>
            <span className="mono">{portfolioId}</span> · framework{" "}
            {String(p.framework_id ?? "—")} · own drawdown rule{" "}
            {pctPoints(p.max_drawdown_rule)} · own cap {pctPoints(p.max_single_asset_pct, 0)}
          </p>
        </div>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        {/* 3y, not 1y — matched to the 756-trading-day (3y) rolling window
            Sortino/Sharpe/Calmar/Max DD share (CLAUDE.md; owner, 2026-08-15).
            1y rides along as recency context, not as a competing headline. */}
        <Tile
          label="3y return"
          value={pct(snap?.return_3y ?? p.return_3y)}
          sub={`1y ${pct(snap?.return_1y ?? p.return_1y)}`}
        />
        <Tile label="Sortino" value={num(snap?.sortino_rolling ?? p.sortino_rolling)} />
        <Tile label="Sharpe" value={num(snap?.sharpe_rolling ?? p.sharpe_rolling)} />
        <Tile label="Calmar" value={num(snap?.calmar_rolling ?? p.calmar_rolling)} />
        <Tile label="Max drawdown" value={pct(snap?.max_drawdown ?? p.max_drawdown)} />
        <Tile label="Volatility" value={pct(snap?.volatility ?? p.volatility, false)} />
        <Tile label="NAV" value={nav(snap?.nav)} sub={inception ? `since ${inception}` : undefined} />
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>Paper NAV</h2>
          <NavChart points={data.nav} />
          {/* Series with different inceptions are not comparable levels, and the
              page says so where the level is shown. */}
          <div className="paper-note">
            Base 100 at this portfolio&apos;s own inception
            {inception ? ` (${inception})` : ""} — not comparable with another
            portfolio&apos;s level.
          </div>
        </div>
        <div className="card">
          <h2>Allocation</h2>
          {Object.keys(allocation).length ? (
            <AllocationTable allocation={allocation} />
          ) : (
            <div className="empty">No allocation recorded.</div>
          )}
        </div>
      </div>

      {data.recent_snapshots.length ? (
        <div className="card" style={{ marginTop: 14 }}>
          <h2>Recent weeks</h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th className="num">Rank</th>
                  <th className="num">Sortino</th>
                  <th className="num">Calmar</th>
                  <th className="num">3y</th>
                  <th className="num muted">1y</th>
                  <th>Recommendation</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_snapshots.map((row) => (
                  <tr key={String(row.date)}>
                    <td className="mono">{String(row.date)}</td>
                    <td className="num">{String(row.rank)}</td>
                    <td className="num">{num(row.sortino_rolling)}</td>
                    <td className="num">{num(row.calmar_rolling)}</td>
                    <td className="num">{pct(row.return_3y)}</td>
                    <td className="num muted">{pct(row.return_1y)}</td>
                    <td className="muted">{String(row.recommendation ?? "—")}</td>
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
