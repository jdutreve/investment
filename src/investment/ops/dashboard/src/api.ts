/*
 * The only place this app talks to the agent.
 *
 * THE TOKEN COMES FROM THE PAGE, not from an endpoint: `ops/api.py` splices it
 * into the <meta> tag when it serves index.html. See that module's docstring for
 * why an endpoint handing it out would be strictly worse (script tags are exempt
 * from CORS, so any page could read it).
 */

export const OPS_TOKEN =
  document.querySelector<HTMLMetaElement>('meta[name="ops-token"]')?.content ?? "";

/** The agent is not answering — a different thing from "there is no data". */
export class AgentDownError extends Error {
  constructor() {
    super("cannot reach the agent on 127.0.0.1");
    this.name = "AgentDownError";
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "X-Ops-Token": OPS_TOKEN, "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // A network-level failure is the agent being down. It must never surface as
    // an empty result: an empty table reads as "nothing happened this week",
    // which is exactly the wrong thing to believe about a silent agent.
    throw new AgentDownError();
  }
  if (!response.ok && response.status !== 400) {
    const body = await response.json().catch(() => ({}) as { message?: string });
    throw new ApiError(response.status, body.message ?? `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export const get = <T,>(path: string): Promise<T> => request<T>(path);

/** Every state change: one POST, dispatched by the agent to `ops/commands.py`. */
export const runCommand = (command: string, args: Record<string, unknown> = {}) =>
  request<CommandResult>("/api/cmd", {
    method: "POST",
    body: JSON.stringify({ command, args }),
  });

export interface CommandResult {
  ok: boolean;
  message: string;
  changed: boolean;
}

/* -- what the endpoints return ------------------------------------------- */

export interface StatusFacts {
  running: string | null;
  last_chain: string | null;
  market_data: string | null;
  last_ranking: string | null;
  last_decision: string | null;
}

export interface Alert {
  level: "warn" | "critical";
  code: string;
  message: string;
}

export type Row = Record<string, unknown>;

export interface Overview {
  regime: Row;
  global_liquidity: Row;
  ranking: Row[];
  invariants: Row[];
  proposal: Row | null;
  scoreboard: {
    hit_rate: [number, number];
    paper_tests: Row[];
    probations: Row[];
    calibration_flags: unknown[];
  };
  defender_metrics: Row | null;
  alerts: Alert[];
  stack: Row | null;
  market_signal: Row | null;
  worker_reading: Row | null;
  recurring: Row[];
  // The newest SignalAttributionEvent payload (mechanical/attribution.py):
  // what the signal layer earned against its control arm, and who Pareto-
  // dominates the stack. Declared here because DigestInputs is served whole —
  // "add a field there and render_digest must take it" (telegram/digest.py);
  // no page renders it yet.
  signal_attribution: Row | null;
}

// One series of the macro board (market/macro_board.py).
export interface MacroLine {
  ticker: string;
  label: string;
  short: string;
  unit: string;
  group: string;
  decimals: number;
  level: number;
  level_text: string;
  speed: number | null;
  acceleration: number | null;
  change_period: string;
  as_of: string;
  // The agent's sentence: where the level sits in its ten years and how it is
  // moving. Shown as written.
  reading: string;
  headline: string;
  // Good news or bad: the level in its ten years, read from the side this
  // series declares favourable. `neutral` when it declares none.
  news: "good" | "watch" | "poor" | "bad" | "neutral" | "unknown";
  favourable: "high" | "low" | null;
  unusual: boolean;
  position: string;
  rank: number | null;
  median: number | null;
  low: number | null;
  high: number | null;
  direction: -1 | 0 | 1;
  move_size: "" | "slight" | "full" | "sharp";
  pace: string;
}

// An invariant whose condition holds today on a series of the theme it is
// listed under, as its claim.
export interface LitInvariant {
  id: string;
  title: string;
  status: string;
}

export interface MacroTheme {
  group: string;
  news: MacroLine["news"];
  sentence: string;
  invariants: LitInvariant[];
}

// How an asset is moving, and nothing else (market/macro_board.asset_moves):
// its return over the lookback, which way, how large for that asset, and
// whether the move is gathering or losing pace.
export interface AssetMove {
  ticker: string;
  as_of: string;
  change_pct: number | null;
  period: string;
  direction: -1 | 0 | 1;
  move_size: "" | "slight" | "full" | "sharp";
  pace: string;
  reading: string;
}

export interface MacroPayload {
  board: MacroLine[];
  assets: AssetMove[];
  summary: string;
  // The whole board in two or three plain sentences, then theme by theme.
  overall: string;
  // What a reader should not miss, most unusual first.
  // `cause` names the series' usual drivers that point the same way today — a
  // stated convention, not a measurement.
  highlights: {
    kind: string;
    tone: MacroLine["news"];
    text: string;
    cause: string;
  }[];
  themes: MacroTheme[];
}

export interface RankingPayload {
  date: string | null;
  available_dates: string[];
  rows: Row[];
  benchmark_ids: string[];
  stack_portfolio_id: string;
}

export interface NavPoint {
  ts: string;
  nav: number | null;
}

export interface PortfolioPayload {
  portfolio: Row;
  latest_snapshot: Row | null;
  recent_snapshots: Row[];
  nav: NavPoint[];
}

export interface StackDecision {
  id: string;
  event_date: string;
  payload: Row;
}

export interface BookRow extends Row {
  signal_state: string;
  id: string;
}

export interface BenchmarkNav {
  id: string;
  name: string;
  nav: NavPoint[];
}

export interface TrendSleeveRead {
  price: number;
  moving_averages: (number | null)[];
  breached: number[];
  share: number;
  below: boolean;
  credit_gated: boolean;
}

export interface LiveSignalRead {
  value: number | null;
  trailing_median: number | null;
}

export interface LiveTrend {
  as_of: string;
  signals: Record<string, LiveSignalRead>;
  sleeves: Record<string, TrendSleeveRead>;
}

export interface CurrentRule {
  decision_date: string;
  book: string;
  book_allocation: Record<string, number>;
  target: Record<string, number>;
  today: Record<string, number>;
  today_as_of: string;
  previous_target: Record<string, number>;
}

export interface StackPayload {
  decisions: StackDecision[];
  nav: NavPoint[];
  profile: Row | null;
  stack_portfolio: Row | null;
  books: BookRow[];
  benchmarks: BenchmarkNav[];
  // Missing whenever `load_series` can't price every stack sleeve — a
  // partially-seeded DB, not an error the page should hide behind (see
  // api.py `handle_stack`). The last decision's own frozen reading still
  // renders either way.
  live_trend: LiveTrend | null;
  // The month's order under TODAY's rule (api.py `handle_stack`): the journal
  // keeps each decision as taken, this re-reads it after a book change.
  current_rule: CurrentRule | null;
}
