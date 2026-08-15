import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  LayoutDashboard,
  ListOrdered,
  TrendingUp,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { get, type StatusFacts } from "../api";
import { NA } from "../format";

/*
 * The shell. The nav lists only what M10 ships (Phases 1-3, docs/Dashboard.md);
 * the sections specified for later phases are deliberately absent rather than
 * present-and-dead — a nav item that opens an empty page is worse than one that
 * is not there yet.
 */

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/ranking", label: "Ranking", icon: ListOrdered, end: false },
  { to: "/stack", label: "Stack", icon: TrendingUp, end: false },
];

function StatusStrip() {
  // Polled, because the facts it shows change while the page is open — a chain
  // starting is exactly what the owner wants to see without reloading.
  const { data } = useQuery({
    queryKey: ["status"],
    queryFn: () => get<StatusFacts>("/api/status"),
    refetchInterval: 15_000,
  });
  const cells: Array<[string, string]> = [
    ["last chain", data?.last_chain ?? NA],
    ["market data", data?.market_data ?? NA],
    ["last ranking", data?.last_ranking ?? NA],
    ["last decision", data?.last_decision ?? NA],
  ];
  return (
    <div className="status-strip">
      <div className="status-cell">
        <span className="label">agent</span>
        <span className="value">
          <span className={`running-dot${data?.running ? " busy" : ""}`} />
          {data?.running ?? "idle"}
        </span>
      </div>
      {cells.map(([label, value]) => (
        <div className="status-cell" key={label}>
          <span className="label">{label}</span>
          <span className={`value${value === NA ? " na" : ""}`}>{value}</span>
        </div>
      ))}
    </div>
  );
}

export function Layout() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Activity size={13} />
          </span>
          Investment
        </div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </aside>
      <main className="main">
        <StatusStrip />
        <Outlet />
      </main>
    </div>
  );
}
