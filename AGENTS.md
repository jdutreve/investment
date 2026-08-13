# AGENTS.md — Investment Agent (MVP Core)

**The instructions live in [CLAUDE.md](CLAUDE.md). Read that file.**

This one used to be a COPY of it, and by 2026-08-13 the copy was twelve days and
144 lines stale — which is the failure mode a second copy always has, and the
one this project names in CLAUDE.md's own review rule ("WHEN A SECOND ONE
ARRIVES, FIND WHAT NAMED THE FIRST"). What the drift had produced, all of it
readable as current instruction by whichever agent opened this file first:

- a **200-day** trend overlay, where `mechanical/market_signal.MA_WINDOW_DAYS`
  is 300 — the pinned backtest number, wrong here by a third;
- `WorkerResult.reallocation_proposed`, a field **ADR-012 removed**: the Worker
  does not allocate, and an agent told otherwise would have rebuilt it;
- two **named models** ("Qwen3-8B", "Codex-sonnet-5"), against the rule that
  which model runs each role is `.env`'s business alone and no file may name
  one — and both names were already wrong;
- the **Monday chain**, moved to Sunday by owner decision on 2026-08-12;
- the 40% / -15% caps, superseded by ADR-007's 50% / -25%.

Nothing is duplicated here now, so nothing here can be stale.
