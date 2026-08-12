"""The wired agent — everything the live process holds, in one object.

WHY THIS EXISTS, and it is a dependency direction rather than a convenience.
Three things need the same set of built components: the Monday chain
(`monday.py`), the command layer every front dispatches to
(`ops/commands.py`), and the process that owns them (`main.py`). Without a
shared type they would have to import each other — `commands` needs to run the
chain, the chain needs the same runtime the bot hands it — and the cycle has no
clean break. With one, the arrows all point here and nowhere else.

WHAT IS AND IS NOT IN IT. The COMPONENTS: the single connection (ADR-004), the
settings, the run-lock, the three cognitive roles, the embedder, the ingester,
the triage agent. Not state — nothing here changes as the agent runs, which is
why it is frozen. The chain's own state lives in the database
(`detector_state.last_chain_success`) and the lock's in the lock.

BUILT ONCE, at startup. Each of these is expensive exactly once: the embedder
loads ~90MB of model, the agents build an HTTP client with a bounded keepalive
(openrouter_client.py), the ingester reads its calibrated thresholds. A front
that built its own would pay all of it per request and — worse for the
embedder — hold a second copy of the model.
"""

import dataclasses

from pydantic_ai import Agent

from investment.config import Settings
from investment.corpus.embedding import InProcessEmbedder
from investment.corpus.ingester import CorpusIngester
from investment.db.sqlite import InvestmentDB
from investment.ops.run_lock import RunLock
from investment.planner.post import PlannerPost
from investment.planner.pre import PlannerPre
from investment.watch.event_watch import EventTriage
from investment.worker.result import WorkerResult


@dataclasses.dataclass(frozen=True)
class AgentRuntime:
    """The components of one running agent."""

    db: InvestmentDB
    settings: Settings
    lock: RunLock
    planner_pre: PlannerPre
    worker_agent: Agent[None, WorkerResult]
    planner_post: PlannerPost
    embedder: InProcessEmbedder
    ingester: CorpusIngester
    triage_agent: Agent[None, EventTriage]
