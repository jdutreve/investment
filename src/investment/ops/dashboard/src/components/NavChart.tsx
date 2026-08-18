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
 * ONE SERIES BY DEFAULT, so no legend box — the card's title already says what
 * is plotted (marks-and-anatomy: "a single series needs no legend"). 2px line,
 * hairline solid grid one step off the surface, no dot on every point,
 * crosshair tooltip.
 *
 * `series` (added 2026-08-18, Stack page "NAV of the 3 benchmarks") opts INTO
 * a comparison chart: a legend appears (marks-and-anatomy: "a legend is always
 * present for two or more series"), every line is named in the tooltip, and
 * the single "latest N · date" footer becomes one line per series — dataviz
 * skill's relief rule, so a value is never gated behind a hover. Direct
 * end-labels are deliberately NOT added here: four NAV series based 100 at
 * FOUR DIFFERENT inception dates can end the chart at nearly any relative
 * height, so end-labels would as often collide as not — the skill's own
 * guidance for converging series at ~4 lines is "back to the legend +
 * tooltip", which is what the footer and the crosshair already are.
 *
 * NOT AN AXIS PER MEASURE. NAV is the only measure here; the drawdown and the
 * indicators live in tiles beside the chart rather than on a second y-scale,
 * because two y-scales on one plot invent a correlation that is not in the
 * data.
 */

export interface NamedNavSeries {
  id: string;
  label: string;
  color: string;
  points: NavPoint[];
}

interface MergedRow {
  ts: string;
  [seriesId: string]: string | number | undefined;
}

function mergeSeries(all: NamedNavSeries[]): MergedRow[] {
  const byTs = new Map<string, MergedRow>();
  for (const s of all) {
    for (const p of s.points) {
      if (typeof p.nav !== "number") continue;
      const row = byTs.get(p.ts) ?? { ts: p.ts };
      row[s.id] = p.nav;
      byTs.set(p.ts, row);
    }
  }
  return Array.from(byTs.values()).sort((a, b) => a.ts.localeCompare(b.ts));
}

function latestOf(points: NavPoint[]): NavPoint | null {
  const withNav = points.filter((p) => typeof p.nav === "number");
  return withNav.length ? withNav[withNav.length - 1]! : null;
}

export function NavChart({
  points,
  color = "var(--series-2)",
  height = 220,
  label = "NAV",
  series = [],
}: {
  points: NavPoint[];
  color?: string;
  height?: number;
  /** The primary series' name — shown in the legend only when `series` is non-empty. */
  label?: string;
  /** Extra comparison lines, e.g. the yardstick benchmarks alongside a strategy's own NAV. */
  series?: NamedNavSeries[];
}) {
  const primary: NamedNavSeries = { id: "primary", label, color, points };
  const all = [primary, ...series];
  const multi = series.length > 0;

  const data = multi
    ? mergeSeries(all)
    : points.filter((p) => typeof p.nav === "number");
  if (data.length < 2) {
    return <div className="empty">Not enough NAV points yet to draw a series.</div>;
  }

  return (
    <div>
      {multi ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 14, marginBottom: 8 }}>
          {all.map((s) => (
            <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: s.color,
                  flex: "none",
                }}
              />
              <span style={{ fontSize: 12, color: "var(--ink-secondary)" }}>{s.label}</span>
            </div>
          ))}
        </div>
      ) : null}
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
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
            formatter={(v: number, name: string) => [fmtNav(v), name]}
          />
          {all.map((s) => (
            <Line
              key={s.id}
              type="monotone"
              dataKey={s.id}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={false}
              connectNulls
              /* The endpoint marker: >=8px with a 2px surface ring, so it stays
                 legible where it meets the axis. */
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-raised)" }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      {/* Direct label, selectively — the last value only, one line per series
          when there is more than one (the relief channel: never gated behind
          a hover). */}
      <div className="freshness" style={{ textAlign: "right", marginTop: -4 }}>
        {multi
          ? all.map((s) => {
              const last = latestOf(s.points);
              return last ? (
                <span key={s.id} style={{ marginLeft: 14 }}>
                  {s.label} {fmtNav(last.nav)}
                </span>
              ) : null;
            })
          : (() => {
              const last = latestOf(points);
              return last ? `latest ${fmtNav(last.nav)} · ${last.ts.slice(0, 10)}` : null;
            })()}
      </div>
    </div>
  );
}
