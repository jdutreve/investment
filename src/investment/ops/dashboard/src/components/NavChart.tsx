import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { NavPoint } from "../api";
import { nav as fmtNav } from "../format";

/*
 * ONE SERIES, so no legend box — the card's title already says what is plotted
 * (marks-and-anatomy: "a single series needs no legend"). 2px line, hairline
 * solid grid one step off the surface, no dot on every point, crosshair tooltip.
 *
 * NOT AN AXIS PER MEASURE. The NAV is the only measure here; the drawdown and
 * the indicators live in tiles beside the chart rather than on a second y-scale,
 * because two y-scales on one plot invent a correlation that is not in the data.
 *
 * A TABLE TWIN ALWAYS EXISTS: the value is never gated behind a hover — the
 * endpoint is directly labelled and the page's tiles carry the same figures.
 */

export function NavChart({
  points,
  color = "var(--series-2)",
  height = 220,
}: {
  points: NavPoint[];
  color?: string;
  height?: number;
}) {
  const data = points.filter((p) => typeof p.nav === "number");
  if (data.length < 2) {
    return <div className="empty">Not enough NAV points yet to draw a series.</div>;
  }
  const last = data[data.length - 1]!;
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 54, bottom: 4, left: 4 }}>
          <CartesianGrid stroke="var(--border)" strokeWidth={1} vertical={false} />
          <XAxis
            dataKey="ts"
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "var(--border)" }}
            minTickGap={60}
            tickFormatter={(t: string) => t.slice(0, 4)}
          />
          <YAxis
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={54}
            tickFormatter={(v: number) => fmtNav(v)}
          />
          <Tooltip
            contentStyle={{
              background: "var(--surface-raised)",
              border: "1px solid var(--border-strong)",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--ink-muted)" }}
            formatter={(v: number) => [fmtNav(v), "NAV"]}
          />
          <Line
            type="monotone"
            dataKey="nav"
            stroke={color}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            dot={false}
            /* The endpoint marker: >=8px with a 2px surface ring, so it stays
               legible where it meets the axis. */
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-raised)" }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
      {/* Direct label, selectively — the last value only. */}
      <div className="freshness" style={{ textAlign: "right", marginTop: -4 }}>
        latest {fmtNav(last.nav)} · {last.ts.slice(0, 10)}
      </div>
    </div>
  );
}
