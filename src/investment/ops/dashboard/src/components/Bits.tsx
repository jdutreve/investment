import { AlertTriangle, ShieldAlert } from "lucide-react";
import type { Alert } from "../api";
import { NA } from "../format";

/** A stat tile. The value is a formatted string already — `n/a` included, so a
 *  missing measurement stays visibly missing rather than becoming a zero. */
export function Tile({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className={`value${value === NA ? " na" : ""}`}>{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}

/*
 * Alerts carry an ICON AND A LEVEL WORD as well as the color: status is never
 * encoded by hue alone. The order is the agent's own (critical first,
 * `mechanical/alerts.collect_alerts`) and is not re-sorted here — the digest and
 * this page must list the same alerts in the same order.
 */
export function AlertList({ alerts }: { alerts: Alert[] }) {
  if (!alerts.length) {
    return <div className="empty">No alerts on the live path.</div>;
  }
  return (
    <div>
      {alerts.map((alert) => (
        <div key={alert.code} className={`alert ${alert.level}`}>
          {alert.level === "critical" ? <ShieldAlert size={16} /> : <AlertTriangle size={16} />}
          <div>
            <span className="level">{alert.level}</span>
            <span className="code">{alert.code}</span>
            <div>{alert.message}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

/** The date of the row a panel read, so a stale page reads as stale. */
export function Freshness({ children }: { children: React.ReactNode }) {
  return <span className="freshness">{children}</span>;
}
