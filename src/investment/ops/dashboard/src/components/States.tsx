import { AlertTriangle, PlugZap } from "lucide-react";
import { AgentDownError } from "../api";

/*
 * A FAILED FETCH NEVER RENDERS AS EMPTY DATA. That is the one rule these two
 * components exist for: the dashboard is served BY the agent, so when the agent
 * is down there is nothing to answer — and a page that showed an empty ranking
 * would say "no portfolios this week" when the truth is "nobody asked".
 * `invest` is the front that works with the agent down; this one says so.
 */

export function ErrorState({ error }: { error: unknown }) {
  const down = error instanceof AgentDownError;
  return (
    <div className="error-state">
      <h3>
        {down ? <PlugZap size={16} /> : <AlertTriangle size={16} />}
        {down ? "The agent is not answering" : "Could not load this view"}
      </h3>
      <p>
        {down ? (
          <>
            This dashboard is served by the agent process, so it cannot show data while the
            agent is stopped. Start it, or read the database directly with <code>invest</code>{" "}
            — that front opens the file itself.
          </>
        ) : (
          (error as Error)?.message ?? "unknown error"
        )}
      </p>
    </div>
  );
}

/** Nothing has been recorded yet — genuinely empty, and said explicitly. */
export function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
