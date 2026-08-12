"""Full SQLite schema (ADR-004) — see docs/DATA_MODELS.md for the conceptual
spec this maps physically: 13 entity tables ("vertices"), 6 M:N tables
("edges" — the other 5 relations are FK+property columns on the child, per
DATA_MODELS.md "Physical mapping rule"), 3 time-series tables, 13 document
tables (10 in the spec + `curated_passage`, the M7 curation checkpoint,
+ `innovation`, the recurrence ledger, + `revision_measurement`,
the measured-verdict ledger).

Every count in that sentence has been wrong at some point — each named what
existed when it was typed and outlived it. They are checked against the DDL
below by the M1 table-count test, which is the only reason to keep writing
them down.
Types: STRING->TEXT, FLOAT->REAL, BOOLEAN->BOOLEAN (SQLite: NUMERIC
affinity, stored 0/1), MAP/STRING[]->TEXT (JSON1), DATE/DATETIME->TEXT
(ISO-8601), FLOAT[384]->BLOB (float32, see docs/TASKS.md Phase 1bis).

`CREATE TABLE IF NOT EXISTS` only for V1 bootstrap (CLAUDE.md "Dev
standards" schema rule) — a numbered migration convention starts at the
first post-go-live schema change, not before.
"""

SCHEMA_SQL = """
-- ============================================================
-- ENTITY TABLES (13)
-- ============================================================

CREATE TABLE IF NOT EXISTS framework (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT,
  enabled     BOOLEAN NOT NULL,
  accuracy    REAL,
  trace       TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

-- No trace column (TRACE_EXEMPT): narrative lives in `description`.
CREATE TABLE IF NOT EXISTS regime_type (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  aliases      TEXT NOT NULL DEFAULT '[]',   -- JSON array
  framework_id TEXT NOT NULL REFERENCES framework(id),
  description  TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regime (
  id             TEXT PRIMARY KEY,           -- '<regimeType.alias>-<start_date>'
  regime_type_id TEXT NOT NULL REFERENCES regime_type(id),
  tags           TEXT NOT NULL DEFAULT '[]', -- JSON array
  start_date     TEXT NOT NULL,
  end_date       TEXT,                       -- null if currently active
  confidence     REAL,
  is_current     BOOLEAN NOT NULL,
  events         TEXT NOT NULL DEFAULT '[]', -- JSON array
  trace          TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invariant (
  id                 TEXT PRIMARY KEY,
  title              TEXT NOT NULL,
  description        TEXT NOT NULL,
  example            TEXT,
  source             TEXT NOT NULL,
  author             TEXT,                   -- 'dalio'|'marks'|'system'|NULL ('other')
  -- 'proposed'|'integrated'|'rejected'|'reference'. 'reference' is TERMINAL,
  -- not a stage: reference knowledge has no condition and no effect, so no
  -- confrontation can ever move it and ADR-006's "nothing stays proposed
  -- forever" does not apply (mechanical/invariants.py REFERENCE_STATUS).
  status             TEXT NOT NULL,
  tags               TEXT NOT NULL DEFAULT '[]',  -- JSON array
  embedding          BLOB,                   -- float32 x 384
  condition          TEXT NOT NULL DEFAULT '[]',  -- JSON: Predicate[]
  effect             TEXT,                   -- JSON: {handle, metric, method, direction}
  weight_initial     REAL NOT NULL,
  floor_weight       REAL NOT NULL,
  weight_effective   REAL,
  confirmation_count INTEGER NOT NULL DEFAULT 0,
  infirmation_count  INTEGER NOT NULL DEFAULT 0,
  market_score       REAL NOT NULL DEFAULT 1.0,
  recency_factor     REAL NOT NULL DEFAULT 1.0,
  trace              TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  validated_at       TEXT,                   -- null while still a candidate
  updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy (
  id             TEXT PRIMARY KEY,           -- never collides with a Framework id
  title          TEXT NOT NULL,
  description    TEXT NOT NULL,
  regime_type_id TEXT REFERENCES regime_type(id),  -- null: framework-neutral
  framework_id   TEXT NOT NULL REFERENCES framework(id),
  conviction     REAL NOT NULL,
  enabled        BOOLEAN NOT NULL,
  conditions     TEXT NOT NULL,
  source         TEXT NOT NULL,              -- 'corpus'|'agent-discovery'
  status         TEXT NOT NULL,              -- 'proposed'|'active'|'closed'
  date_opened    TEXT,
  date_revised   TEXT,
  trace          TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

-- HAS_SCENARIO (1:N, composition): FK + properties on the child.
CREATE TABLE IF NOT EXISTS scenario (
  id                TEXT PRIMARY KEY,
  strategy_id       TEXT NOT NULL REFERENCES strategy(id),
  name              TEXT NOT NULL,           -- 'bull'|'base'|'bear'
  probability       REAL NOT NULL,           -- 0-100; 3-per-strategy sums to 100
  triggers          TEXT NOT NULL DEFAULT '[]',  -- JSON array
  target_allocation TEXT NOT NULL,           -- JSON map, percent weights sum 100
  currency          TEXT NOT NULL,
  trace             TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

-- UPDATES (1:N, composition): FK on the child.
CREATE TABLE IF NOT EXISTS evaluation (
  id               TEXT PRIMARY KEY,
  strategy_id      TEXT NOT NULL REFERENCES strategy(id),
  date             TEXT NOT NULL,
  verdict          TEXT NOT NULL,            -- 'confirms'|'weakens'|'invalidates'|'neutral'
  conviction_delta REAL,
  events           TEXT NOT NULL DEFAULT '[]',  -- JSON array
  reasoning        TEXT,
  trace            TEXT NOT NULL,
  created_at       TEXT NOT NULL
);

-- TESTED_IN (Strategy->Backtest) + IN_REGIME (Backtest->Regime): both
-- 1:N compositions, both FK+properties live on backtest (the child of both).
CREATE TABLE IF NOT EXISTS backtest (
  id              TEXT PRIMARY KEY,
  strategy_id     TEXT NOT NULL REFERENCES strategy(id),
  is_primary      BOOLEAN NOT NULL DEFAULT 0,   -- TESTED_IN property
  regime_id       TEXT NOT NULL REFERENCES regime(id),
  overlap_pct     REAL,                          -- IN_REGIME property, 0-100
  period          TEXT NOT NULL,
  date_start      TEXT NOT NULL,
  date_end        TEXT NOT NULL,
  sharpe_rolling  REAL,
  sortino_rolling REAL,
  calmar_rolling  REAL,
  max_drawdown    REAL,
  total_return    REAL,
  currency        TEXT NOT NULL,
  trace           TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposal (
  id                  TEXT PRIMARY KEY,
  date                TEXT NOT NULL,
  -- 'switch'|'reallocation' (ranking path) | 'market-signal' (ADR-007 live
  -- path). ADR-008: the ranking columns below are NULL for 'market-signal'.
  proposal_type       TEXT NOT NULL,
  defender_id         TEXT NOT NULL,         -- scalar, no edge in V1; for
                                             --   market-signal: the live book
  challenger_id       TEXT,                  -- switch only
  proposed_allocation TEXT,                  -- JSON map; reallocation + market-signal
  recommendation      TEXT NOT NULL,         -- 'monitor'|'paper-test'
  -- NULLABLE since ADR-008: rank and gap encode the ranked defender/challenger
  -- DUEL that ADR-007 superseded. A market-signal proposal has no rank and no
  -- gap — it has a signal state and a book. NULL says "does not apply"; a
  -- filled-in convention would make these columns mean different things
  -- depending on `proposal_type`, which every later reader would have to
  -- decode before trusting them.
  defender_rank       INTEGER,
  challenger_rank     INTEGER,               -- null for reallocation
  gap                 TEXT,                  -- JSON map; ranking path only
  market_context      TEXT NOT NULL,         -- JSON map
  reasoning           TEXT NOT NULL,
  user_response       TEXT NOT NULL DEFAULT 'pending',
  rejection_reason    TEXT,
  paper_started       TEXT,
  outcome             TEXT,                  -- JSON map, written at +12w
  evaluated_at        TEXT,
  -- What UC8-B gate 6 WOULD have said, on paths where it no longer refuses
  -- (owner decision 2026-08-08: validate downstream, not upstream). One of
  -- 'passed' | 'no_citation' | 'ineligible_citation' | 'corpus_drought', NULL
  -- on paths where the gate still binds.
  --
  -- Recorded because a refusal used to leave NOTHING — no proposal row, so no
  -- `paper_started`, so `outcomes.evaluate_proposals` never scored it at +12w.
  -- The gate was unfalsifiable by construction: it destroyed the evidence that
  -- could contradict it. Five refusals across the three M8b runs, five
  -- occasions to learn, all lost. With this column the question finally has an
  -- answer: did the proposals gate 6 would have blocked actually do worse?
  citation_verdict    TEXT,
  trace               TEXT NOT NULL,
  created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio (
  id                   TEXT PRIMARY KEY,
  name                 TEXT NOT NULL,
  framework_id         TEXT NOT NULL REFERENCES framework(id),
  defender             BOOLEAN NOT NULL,     -- exactly one true in V1
  enabled              BOOLEAN NOT NULL,
  currency             TEXT NOT NULL,        -- user display currency
  benchmark            TEXT NOT NULL,
  allocation           TEXT NOT NULL,        -- JSON map, mandatory
  max_drawdown_rule    REAL NOT NULL,        -- stricter-only than user_profile
  max_single_asset_pct REAL NOT NULL,        -- stricter-only than user_profile
  phase                TEXT NOT NULL,
  fx_usd_exposure      REAL,

  sharpe_rolling       REAL,
  sortino_rolling      REAL,
  calmar_rolling       REAL,
  max_drawdown         REAL,
  volatility           REAL,

  return_3m            REAL,
  return_6m            REAL,
  return_1y            REAL,
  return_3y            REAL,
  return_5y            REAL,

  date_revised         TEXT,
  trace                TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  author      TEXT,
  kind        TEXT NOT NULL,                 -- 'book'|'article'|'note'|'event'
  source_type TEXT NOT NULL,                 -- 'pdf'|'kindle'|'url'|'text'
  source_path TEXT,
  ingested_at TEXT NOT NULL,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  trace       TEXT NOT NULL
);

-- CONTAINS (1:N, composition): FK + properties on the child.
-- No trace (TRACE_EXEMPT): inherits provenance from the parent Document.
CREATE TABLE IF NOT EXISTS passage (
  id          TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES document(id),
  position    INTEGER,
  page        INTEGER,
  content     TEXT NOT NULL,
  chunk_id    TEXT,
  embedding   BLOB,                          -- float32 x 384
  created_at  TEXT NOT NULL
);

-- Append-only audit spine. No trace (TRACE_EXEMPT): the payload IS the
-- trace. No relations ever.
CREATE TABLE IF NOT EXISTS event_log (
  id         TEXT PRIMARY KEY,               -- monotonic ULID = append order
  ts         TEXT NOT NULL,                  -- wall-clock append time, informational
  event_date TEXT NOT NULL,                  -- domain date (indexed)
  type       TEXT NOT NULL,
  source_uc  TEXT NOT NULL,
  source_id  TEXT,
  payload    TEXT NOT NULL                   -- JSON
);

-- APPEND-ONLY, ENFORCED BY THE DATABASE, not by convention (CLAUDE.md
-- "EventLog": "append-only; monotonic ULID id = THE canonical append order").
-- Until these triggers the rule was an application habit: nothing stopped an
-- UPDATE or a DELETE, and the audit spine that every verdict, weight move and
-- proposal is reconstructed from is exactly the table where a silent edit would
-- be undetectable — it IS the record that would have shown the edit.
--
-- No code path issues either statement today (`InvestmentDB` exposes append
-- only), so these cost nothing and forbid what was already never done. That is
-- the point: the guarantee should survive the next person who adds a "small
-- correction" migration.
--
-- `CREATE TRIGGER IF NOT EXISTS` is ADDITIVE and idempotent, so this stays
-- inside V1's `CREATE TABLE IF NOT EXISTS` discipline and does NOT open
-- CLAUDE.md's numbered-migration convention (which the first ALTER would).
CREATE TRIGGER IF NOT EXISTS event_log_is_append_only_update
BEFORE UPDATE ON event_log
BEGIN
  SELECT RAISE(ABORT, 'event_log is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS event_log_is_append_only_delete
BEFORE DELETE ON event_log
BEGIN
  SELECT RAISE(ABORT, 'event_log is append-only: DELETE is forbidden');
END;

-- ============================================================
-- M:N RELATION TABLES (6)
-- ============================================================

CREATE TABLE IF NOT EXISTS favors (
  regime_type_id  TEXT NOT NULL REFERENCES regime_type(id),
  strategy_id     TEXT NOT NULL REFERENCES strategy(id),
  sharpe_rolling  REAL,
  sortino_rolling REAL,
  calmar_rolling  REAL,
  max_drawdown    REAL,
  n_periods       INTEGER NOT NULL DEFAULT 0,
  last_updated    TEXT,
  PRIMARY KEY (regime_type_id, strategy_id)
);

CREATE TABLE IF NOT EXISTS backed_by (
  strategy_id  TEXT NOT NULL REFERENCES strategy(id),
  invariant_id TEXT NOT NULL REFERENCES invariant(id),
  strength     REAL,
  added_at     TEXT NOT NULL,
  excerpt      TEXT,
  PRIMARY KEY (strategy_id, invariant_id)
);

-- `is_primary` physical name for the HOLDS.primary property — avoids a
-- column literally named `primary` next to PRIMARY KEY in the same DDL.
CREATE TABLE IF NOT EXISTS holds (
  portfolio_id TEXT NOT NULL REFERENCES portfolio(id),
  strategy_id  TEXT NOT NULL REFERENCES strategy(id),
  is_primary   BOOLEAN NOT NULL DEFAULT 0,
  weight       REAL,
  since        TEXT NOT NULL,
  PRIMARY KEY (portfolio_id, strategy_id)
);

CREATE TABLE IF NOT EXISTS designed_for (
  portfolio_id   TEXT NOT NULL REFERENCES portfolio(id),
  regime_type_id TEXT NOT NULL REFERENCES regime_type(id),
  rationale      TEXT,
  PRIMARY KEY (portfolio_id, regime_type_id)
);

CREATE TABLE IF NOT EXISTS supports (
  passage_id   TEXT NOT NULL REFERENCES passage(id),
  invariant_id TEXT NOT NULL REFERENCES invariant(id),
  strength     REAL,
  excerpt      TEXT,
  PRIMARY KEY (passage_id, invariant_id)
);

-- The invariants a reallocation Proposal cited (Worker's supporting_invariants).
-- A RELATION, not a column, so outcomes.py can read the cited set back at +12w
-- (source='proposal' confrontations) and the digest can join it — a switch
-- proposal derives its citations from the challenger's backed_by instead, so
-- this table carries reallocation citations only.
CREATE TABLE IF NOT EXISTS proposal_cites (
  proposal_id  TEXT NOT NULL REFERENCES proposal(id),
  invariant_id TEXT NOT NULL REFERENCES invariant(id),
  PRIMARY KEY (proposal_id, invariant_id)
);

-- ============================================================
-- TIME-SERIES TABLES (3) — full daily granularity, no downsampling
-- ============================================================

CREATE TABLE IF NOT EXISTS market_data (
  ticker       TEXT NOT NULL,
  asset_class  TEXT NOT NULL,
  currency     TEXT NOT NULL,
  ts           TEXT NOT NULL,                -- as-known-at date (ADR-003)
  level        REAL,
  speed        REAL,
  acceleration REAL,
  PRIMARY KEY (ticker, ts)
);

CREATE TABLE IF NOT EXISTS scenario_probability (
  strategy_id TEXT NOT NULL,
  scenario    TEXT NOT NULL,
  ts          TEXT NOT NULL,
  probability REAL,
  PRIMARY KEY (strategy_id, scenario, ts)
);

CREATE TABLE IF NOT EXISTS portfolio_nav (
  portfolio_id    TEXT NOT NULL,
  currency        TEXT NOT NULL,
  ts              TEXT NOT NULL,
  nav             REAL,
  daily_return    REAL,
  sharpe_rolling  REAL,
  sortino_rolling REAL,
  calmar_rolling  REAL,
  drawdown        REAL,
  vs_benchmark    REAL,
  PRIMARY KEY (portfolio_id, ts)
);

-- ============================================================
-- DOCUMENT TABLES (13) — plain tables, not part of the conceptual graph
-- ============================================================

-- Static (5)

CREATE TABLE IF NOT EXISTS user_profile (
  user_id               TEXT PRIMARY KEY,
  currency              TEXT NOT NULL,
  benchmark             TEXT NOT NULL,
  max_drawdown_pct      REAL NOT NULL,       -- BINDING
  max_single_asset_pct  REAL NOT NULL,       -- BINDING
  phase                 TEXT NOT NULL,
  horizon_years         INTEGER,
  risk_tolerance        TEXT,
  auto_validation_hours INTEGER NOT NULL DEFAULT 48,
  telegram_chat_id      TEXT,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invariant_author_config (
  author             TEXT PRIMARY KEY,      -- 'dalio'|'marks'|'other'|'system'
  floor_weight       REAL NOT NULL,
  initial_weight_min REAL NOT NULL,
  initial_weight_max REAL NOT NULL,
  description        TEXT
);

CREATE TABLE IF NOT EXISTS allowed_tickers (
  ticker                TEXT PRIMARY KEY,
  asset_class           TEXT NOT NULL,
  currency              TEXT NOT NULL,
  source                TEXT NOT NULL,       -- 'yahoo'|'fred'|'composite'
  transform             TEXT NOT NULL,       -- 'none'|'yoy_pct'|'composite'
  availability_lag_days INTEGER NOT NULL DEFAULT 0,
  description           TEXT,
  active                BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS system_thresholds (
  key         TEXT PRIMARY KEY,
  value       REAL NOT NULL,
  description TEXT,
  updated_at  TEXT NOT NULL
);

-- Single row by convention (id='singleton'), enforced by the sole-writer
-- discipline (ADR-004), not a DB constraint. Persistent runtime state that
-- must survive restarts, driving DUE-ON-START.
CREATE TABLE IF NOT EXISTS detector_state (
  id                      TEXT PRIMARY KEY,
  candidate_type          TEXT,
  candidate_start_ts      TEXT,              -- date the current candidate streak
                                              --   began (M3 addition, beyond the
                                              --   TASKS.md DDL): backdates a
                                              --   confirmed regime's start_date to
                                              --   the streak's first print, so
                                              --   'detector lag' (start_date ->
                                              --   created_at) is a real, bounded
                                              --   number, not always zero.
  consecutive_prints      INTEGER NOT NULL DEFAULT 0,
  last_print_ts_growth    TEXT,
  last_print_ts_inflation TEXT,
  last_chain_success      TEXT,              -- ISO-8601, drives DUE-ON-START
  updated_at              TEXT NOT NULL
);

-- Analytical (8)

CREATE TABLE IF NOT EXISTS invariant_confrontations (
  id             TEXT PRIMARY KEY,
  invariant_id   TEXT NOT NULL REFERENCES invariant(id),
  moment_context TEXT NOT NULL,
  date           TEXT NOT NULL,
  verdict        TEXT NOT NULL,              -- 'confirmed'|'refuted'
  severity       REAL,
  source         TEXT NOT NULL,              -- 'backtest'|'evaluation'|'proposal'|'adaptation'
  source_id      TEXT
);

CREATE TABLE IF NOT EXISTS benchmark_valuation (
  id              TEXT PRIMARY KEY,
  benchmark_kind  TEXT NOT NULL,             -- 'asset_class'|'strategy'
  benchmark_id    TEXT NOT NULL,
  date            TEXT NOT NULL,
  return          REAL,
  sortino_rolling REAL,
  max_drawdown    REAL,
  volatility      REAL,
  UNIQUE (benchmark_kind, benchmark_id, date)
);

-- Natural composite key (date, portfolio_id) doubles as the spec's
-- "unique index" — no separate surrogate id needed.
CREATE TABLE IF NOT EXISTS portfolio_weekly_snapshot (
  date                    TEXT NOT NULL,
  portfolio_id            TEXT NOT NULL,
  defender                BOOLEAN NOT NULL,
  framework_id            TEXT NOT NULL,
  designed_regime_type_id TEXT,
  primary_strategy_id     TEXT,
  allocation              TEXT NOT NULL,     -- JSON map
  rank                    INTEGER NOT NULL,
  sharpe_rolling          REAL,
  sortino_rolling         REAL,
  calmar_rolling          REAL,
  max_drawdown            REAL,
  -- The paper series' level on this date, base 100 at each portfolio's OWN
  -- inception. Snapshotted like every other indicator beside it, so the digest
  -- stays a single-row read and any past week re-renders with the number that
  -- was true then. NOT comparable across rows: the series start at different
  -- dates (permanent-balanced 1986, ms-slowdown-book 1993), so a NAV of 1511
  -- over forty years and one of 1533 over thirty-five are not the same
  -- performance — the digest's header says so where it is rendered.
  nav                     REAL,
  volatility              REAL,
  return_3m               REAL,
  return_6m               REAL,
  return_1y               REAL,
  return_3y               REAL,
  return_5y               REAL,
  gap_to_defender         TEXT,              -- JSON map, null for defender
  market_context          TEXT NOT NULL,     -- JSON map
  recommendation          TEXT NOT NULL,     -- 'maintain'|'monitor'|'paper-test'
  -- The user-drawdown breach: "keeps the row ranked but excludes it from
  -- defender role and proposal candidacy" (CLAUDE.md "Ranking rule";
  -- docs/TASKS.md Phase 5bis "user-drawdown breach = defender/proposal
  -- exclusion flag"). STORED rather than derived at render time, because it is
  -- the only ranking verdict that depends on a mutable EXTERNAL knob: the
  -- binding cap already moved once (-15 -> -25, ADR-007) and DECISIONS.md keeps
  -- -30 on the table. `build_digest` re-renders any past Monday, so a derived
  -- flag would re-judge a July row under December's cap. The demotion flag has
  -- no column for the mirror reason: its only input, `calmar_rolling`, is on
  -- this row already (snapshots.is_demoted).
  -- NULL = not recorded (rows written before the column existed), which is
  -- distinct from 0 = measured and within the rule.
  trace                   TEXT NOT NULL,
  -- LAST on purpose, after `trace`: the live DB got this column through an
  -- ALTER TABLE (2026-08-02, 31 rows, all left NULL), and SQLite appends. Every
  -- read and write here names its columns so order is immaterial to the code —
  -- but a fresh DB and the live one should still describe identically when
  -- someone diffs the two schemas.
  excluded_from_candidacy BOOLEAN,
  PRIMARY KEY (date, portfolio_id)
);

CREATE TABLE IF NOT EXISTS scenario_calibration (
  id                TEXT PRIMARY KEY,
  strategy_id       TEXT NOT NULL,
  date              TEXT NOT NULL,
  dominant_scenario TEXT NOT NULL,
  realized          TEXT NOT NULL,           -- 'bull'|'base'|'bear'
  score             REAL NOT NULL
);

-- Curation checkpoint (M7). Makes UC4 IDEMPOTENT and RESUMABLE: the curator
-- has three callers (inbox watcher, Monday 08:10 sweep, ad-hoc), so without
-- this table every call would re-spend a full corpus run and mint duplicate
-- candidates for passages already curated.
--
-- Grain is the PASSAGE, not the document: it is what the LLM consumes, and it
-- survives a change of `batch_size` (the batches no longer line up between
-- runs, the passages still do). Written per batch as it returns, so a crash
-- at 95% loses only the batch in flight.
--
-- `fingerprint` is what would CHANGE the output: model + reasoning effort +
-- prompt version. It deliberately does NOT hash the signal registry: a new
-- alias is a real reason to re-curate, but it must be a decision (bump
-- CURATION_PROMPT_VERSION, or --force), never a silent 45-minute side effect
-- of an edited seed_data.py. Composite PK keeps the history across
-- fingerprints rather than overwriting it.
CREATE TABLE IF NOT EXISTS curated_passage (
  passage_id      TEXT NOT NULL REFERENCES passage(id),
  fingerprint     TEXT NOT NULL,
  curated_at      TEXT NOT NULL,
  candidate_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (passage_id, fingerprint)
);

-- EVERY INNOVATION THE WORKER EVER PROPOSED, and the THEME it belongs to.
--
-- Recurrence is the confidence measure this system had and could not read. Over
-- the M8b runs of 2026-08-08/09 every finding that turned out to be real was
-- proposed MORE THAN ONCE from independent dates: the credit-spread velocity
-- critique six ways, the concentration cap freezing the stack twice, the regime
-- label's divergence twice. Nothing counted them. Invariants are deduplicated
-- (writeback/knowledge.py `find_duplicate`), innovations were not, so the
-- signal existed in the output and nowhere in the system.
--
-- `theme_id` is the id of the FIRST innovation of its cluster, assigned by
-- cosine distance on the same embedder the invariant corpus uses. Recurrence is
-- then a COUNT over the theme rather than a stored integer that can go stale.
--
-- COUNT DISTINCT TITLES, not rows: two runs replaying the same date produce the
-- same sentence twice and that is one observation repeated, while six different
-- wordings of "read the spread's trajectory, not its level" is six independent
-- arrivals at one idea. The second is evidence; the first is a rerun.
--
-- `source_id` points at the invariant or strategy the innovation became, when
-- it became one — process/data innovations have none, which is exactly why they
-- had no home before this table.
CREATE TABLE IF NOT EXISTS innovation (
  id          TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  title       TEXT NOT NULL,
  rationale   TEXT NOT NULL,
  spec        TEXT NOT NULL,          -- JSON map, as proposed
  theme_id    TEXT NOT NULL,          -- the first innovation of this cluster
  source_id   TEXT,                   -- the invariant/strategy it became, if any
  embedding   BLOB,                   -- float32[384], for the theme match
  date        TEXT NOT NULL,          -- the decision date it was proposed on
  trace       TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_innovation_theme ON innovation(theme_id);

-- WHAT HAS ALREADY BEEN MEASURED, so nothing measures it twice and nobody
-- proposes it a fourth time.
--
-- `rule_revision.measure_revision` replays 35 years to judge one knob change,
-- and until 2026-08-11 it threw the answer away: the verdict reached a log
-- line, an EventLog payload in whatever database happened to be open (a
-- THROWAWAY snapshot, on every replayed date), and nothing else. The measured
-- rejection of "add VCIT to the trend overlay" was re-derived three times, and
-- the Worker went on proposing it because nothing could tell it the question
-- was settled. Same family as CLAUDE.md's "a bound whose consumption is not
-- logged cannot be re-derived": a measurement whose result is not stored cannot
-- be re-used.
--
-- KEYED ON THE OVERRIDE SET, not on the proposal's title. Five differently
-- worded haven proposals resolving to the same constants are ONE experiment —
-- established when the M8b sweep was deduplicated — so the canonical JSON of
-- the overrides is the identity, and the title is kept only to show a human
-- what asked for it.
--
-- The WINDOW is part of the key: a verdict on 1991-2008 and one on the full
-- sample answer different questions, and the out-of-sample split that killed
-- two of three overlay candidates depends on holding both.
CREATE TABLE IF NOT EXISTS revision_measurement (
  overrides_key   TEXT NOT NULL,        -- canonical JSON of the override set
  window_start    TEXT NOT NULL,
  window_end      TEXT NOT NULL,
  overrides       TEXT NOT NULL,        -- JSON map, as proposed
  title           TEXT,                 -- the wording that last asked for it
  verdict         TEXT NOT NULL,        -- 'adopt' | 'reject' | 'unmeasurable'
  sortino_delta   REAL,
  cagr_delta      REAL,
  drawdown_delta  REAL,
  measured_at     TEXT NOT NULL,
  PRIMARY KEY (overrides_key, window_start, window_end)
);

CREATE TABLE IF NOT EXISTS replay_report (
  id                  TEXT PRIMARY KEY,
  run_at              TEXT NOT NULL,
  window_start        TEXT NOT NULL,
  window_end          TEXT NOT NULL,
  kind                TEXT NOT NULL,         -- 'mechanical'|'agentic'
  thresholds          TEXT NOT NULL,         -- JSON map
  acceptance_policy   TEXT NOT NULL,
  nav_agent_follow    TEXT NOT NULL,         -- JSON map
  nav_hold_defender   TEXT NOT NULL,         -- JSON map
  n_switches          INTEGER,
  avg_turnover        REAL,
  hit_rate_12w        REAL,
  false_signal_rate   REAL,
  cost_bps            REAL,
  pit_assertions_passed BOOLEAN,
  vintage_mode        TEXT NOT NULL,         -- 'first_release' expected (ADR-003)
  delta_vs_mechanical REAL,                  -- kind='agentic' only
  behavioral_log      TEXT,                  -- JSON, kind='agentic' only
  notes               TEXT
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS ix_regime_current    ON regime (is_current);
CREATE INDEX IF NOT EXISTS ix_invariant_status  ON invariant (status);
CREATE INDEX IF NOT EXISTS ix_strategy_status   ON strategy (status);
CREATE INDEX IF NOT EXISTS ix_strategy_enabled  ON strategy (enabled);
-- Partial unique index: mechanically enforces "exactly one true" (the
-- non-negotiable rule on Portfolio.defender) — a second INSERT/REPLACE
-- with defender=1 raises IntegrityError instead of silently allowing two.
CREATE UNIQUE INDEX IF NOT EXISTS ux_portfolio_defender ON portfolio (defender) WHERE defender = 1;
CREATE INDEX IF NOT EXISTS ix_portfolio_enabled ON portfolio (enabled);
CREATE INDEX IF NOT EXISTS ix_proposal_date     ON proposal (date);
CREATE INDEX IF NOT EXISTS ix_proposal_response ON proposal (user_response);
CREATE INDEX IF NOT EXISTS ix_eventlog_type     ON event_log (type);
CREATE INDEX IF NOT EXISTS ix_eventlog_edate    ON event_log (event_date);
CREATE INDEX IF NOT EXISTS ix_snapshot_date     ON portfolio_weekly_snapshot (date);
"""

# Embeddings (ADR-004): invariant.embedding / passage.embedding are BLOBs
# (float32 x 384, InProcessEmbedder). Loaded at startup into an in-RAM numpy
# matrix and appended incrementally at runtime. Similarity = brute-force
# cosine. No vector index, no FTS in V1 (FTS5 available natively if needed).

TRACE_EXEMPT = {"passage", "regime_type", "event_log"}

# 13 entity tables, for the M1 Definition of Verified table-count check.
ENTITY_TABLES = {
    "framework",
    "regime_type",
    "regime",
    "invariant",
    "strategy",
    "scenario",
    "evaluation",
    "backtest",
    "proposal",
    "portfolio",
    "document",
    "passage",
    "event_log",
}
RELATION_TABLES = {"favors", "backed_by", "holds", "designed_for", "supports", "proposal_cites"}
TS_TABLES = {"market_data", "scenario_probability", "portfolio_nav"}
DOCUMENT_TABLES = {
    "user_profile",
    "invariant_author_config",
    "allowed_tickers",
    "system_thresholds",
    "detector_state",
    "invariant_confrontations",
    "benchmark_valuation",
    "portfolio_weekly_snapshot",
    "scenario_calibration",
    "replay_report",
    # M7: the UC4 curation checkpoint. Took the documented count from 10 to 11
    # (CLAUDE.md "Entities", DATA_MODELS.md) — it is operational state, not a
    # domain entity, but it is a table and it is counted.
    "curated_passage",
    # 2026-08-11, and both were MISSING from this set until the coherence pass
    # of 2026-08-12 — the DDL created them, `sqlite_master` showed them to the
    # Worker, and this registry (which gates `upsert_vertex`/`create_vertex`/
    # `query_ts`) did not know they existed. The M1 count check could not catch
    # it either: it asserts `expected <= tables`, so a table absent from the
    # registry is invisible to it in exactly the direction that matters.
    # The set that named every table there was, and did not follow when two more
    # arrived (CLAUDE.md: "WHEN A SECOND ONE ARRIVES").
    "innovation",
    "revision_measurement",
}


# Columns added to a table that already exists somewhere. `CREATE TABLE IF NOT
# EXISTS` is a no-op on a live database, so a new column in the DDL above
# reaches a fresh install and NOTHING else — the code then fails at runtime on
# "no such column", which is exactly what happened to `proposal.citation_verdict`
# on 2026-08-08.
#
# Applied idempotently after the schema script. This is NOT the numbered
# migration convention: CLAUDE.md starts that at the first POST-go-live change,
# and V1 is still pre-go-live. It is the smallest thing that keeps an existing
# database in step with the DDL, and every entry should be deleted the day the
# real migrations begin.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("proposal", "citation_verdict", "TEXT"),
    ("portfolio_weekly_snapshot", "nav", "REAL"),
)
