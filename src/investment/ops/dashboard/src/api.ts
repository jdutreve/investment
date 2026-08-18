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
}

export interface RankingPayload {
  date: string | null;
  available_dates: string[];
  rows: Row[];
  benchmark_ids: string[];
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

export interface StackPayload {
  decisions: StackDecision[];
  nav: NavPoint[];
  profile: Row | null;
  stack_portfolio: Row | null;
  books: BookRow[];
  benchmarks: BenchmarkNav[];
}
