-- AgentProof production schema. The initial Alembic migration executes this file.
-- DB file: data/agentproof.db (env APP_DB_PATH). All tables IF NOT EXISTS; seed is idempotent.
PRAGMA foreign_keys = ON;

-- ── Static catalog (seeded offline, read-only at runtime) ──────────────────────
CREATE TABLE IF NOT EXISTS roles (
  role_id                  INTEGER PRIMARY KEY,
  role                     TEXT NOT NULL UNIQUE,        -- exact SFw string (framework.resolve_role key)
  sector                   TEXT NOT NULL,
  track                    TEXT NOT NULL,
  description              TEXT,
  performance_expectation  TEXT,
  critical_work_functions  TEXT,   -- JSON array [{cwf, key_tasks[]}]
  n_skills                 INTEGER DEFAULT 0,
  n_executable             INTEGER DEFAULT 0,
  seeded_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS roles_sector_idx ON roles(sector);
CREATE INDEX IF NOT EXISTS roles_track_idx  ON roles(track);

CREATE TABLE IF NOT EXISTS role_skills (
  rs_id           INTEGER PRIMARY KEY,
  role_id         INTEGER NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
  code            TEXT NOT NULL,
  skill           TEXT NOT NULL,
  skill_type       TEXT,
  required_level  INTEGER NOT NULL,            -- normalized 1..6
  is_executable   INTEGER NOT NULL DEFAULT 0,  -- framework.select_executable membership
  UNIQUE(role_id, code)
);
CREATE INDEX IF NOT EXISTS role_skills_code_idx ON role_skills(code);

CREATE TABLE IF NOT EXISTS ka_items (
  ka_id    INTEGER PRIMARY KEY,
  code     TEXT NOT NULL,
  level    INTEGER NOT NULL,
  kind     TEXT NOT NULL,                 -- knowledge | ability | other
  item     TEXT NOT NULL,
  proficiency_description TEXT,
  UNIQUE(code, level, kind, item)
);
CREATE INDEX IF NOT EXISTS ka_items_key_idx ON ka_items(code, level);

-- ── Card content (enrichment; lazy-filled) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS cards (
  role_id                    INTEGER PRIMARY KEY REFERENCES roles(role_id) ON DELETE CASCADE,
  has_enrichment             INTEGER NOT NULL DEFAULT 0,
  n_postings                 INTEGER,
  matched_via                TEXT,
  salary_low                 INTEGER,
  salary_median              INTEGER,
  salary_high                INTEGER,
  salary_currency            TEXT,
  tools                      TEXT,   -- JSON array
  source_urls                TEXT,   -- JSON array
  responsibilities_raw       TEXT,   -- JSON array (verbatim posting sentences)
  responsibilities_distilled TEXT,   -- JSON array (3-5 imperative bullets); NULL until distilled
  distilled_at               TEXT,
  distilled_status           TEXT,   -- null | pending | done | failed
  fetched_at                 TEXT
);

CREATE TABLE IF NOT EXISTS card_battery_items (
  item_id         INTEGER PRIMARY KEY,
  role_id         INTEGER NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
  code            TEXT NOT NULL,
  skill           TEXT NOT NULL,
  required_level  INTEGER NOT NULL,
  task_prompt     TEXT NOT NULL,
  grader_code     TEXT NOT NULL,
  reference_code  TEXT,
  source          TEXT NOT NULL,    -- 'seed' | 'generated'
  status          TEXT NOT NULL,    -- 'ready' | 'invalid' | 'pending'
  created_at      TEXT NOT NULL,
  UNIQUE(role_id, code)
);

CREATE TABLE IF NOT EXISTS card_learned_guidance (
  guidance_id               INTEGER PRIMARY KEY,
  role_id                   INTEGER NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
  code                      TEXT NOT NULL,
  skill                     TEXT NOT NULL,
  guidance                  TEXT NOT NULL,
  provenance_verify_run_id  TEXT,
  created_at                TEXT NOT NULL,
  UNIQUE(role_id, code)
);

-- ── Per-use-case teams (runtime state) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS teams (
  team_id              TEXT PRIMARY KEY,    -- uuid
  name                 TEXT,
  use_case             TEXT NOT NULL,
  intake_session_id    TEXT,                -- references intake_sessions(session_id); indexed below
  brief                TEXT,                -- JSON (Brief)
  status               TEXT NOT NULL,       -- recommend | edited | wired | delivered
  recommendation_json  TEXT NOT NULL,
  user_inputs          TEXT,                -- JSON [{kind, name, content}] — Iteration 3
                                             -- (real-inputs intake); nullable, additive.
                                             -- See app/alembic/versions/0006_user_inputs.py.
  created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS teams_intake_idx ON teams(intake_session_id);
CREATE INDEX IF NOT EXISTS teams_status_idx ON teams(status);

CREATE TABLE IF NOT EXISTS team_agents (
  team_id         TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
  agent_id        TEXT NOT NULL,
  role_id         INTEGER NOT NULL REFERENCES roles(role_id),
  stage           INTEGER NOT NULL,
  squad           TEXT,
  produces        TEXT,
  consumes        TEXT,                      -- agent_id or 'external'
  anchor          INTEGER NOT NULL DEFAULT 0,
  skill_overrides TEXT NOT NULL DEFAULT '{}',  -- JSON {code: level}
  skill_disabled  TEXT NOT NULL DEFAULT '[]',  -- JSON [code,...]
  rationale       TEXT,
  sort_order      INTEGER NOT NULL,
  PRIMARY KEY (team_id, agent_id)
);

CREATE TABLE IF NOT EXISTS team_handoffs (
  team_id     TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
  handoff_id  TEXT PRIMARY KEY,    -- uuid
  from_agent  TEXT NOT NULL,        -- team_agents.agent_id | 'external'
  to_agent    TEXT NOT NULL,        -- team_agents.agent_id | 'external'
  ceremony    TEXT NOT NULL,        -- artifact handoff | review gate | sprint demo | sign-off | feedback loop
  artifact    TEXT,
  description TEXT,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  wired_at    TEXT NOT NULL,
  UNIQUE(team_id, from_agent, to_agent, ceremony)
);
CREATE INDEX IF NOT EXISTS team_handoffs_team_idx ON team_handoffs(team_id);

CREATE TABLE IF NOT EXISTS team_spec_versions (
  team_id            TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
  agent_id           TEXT NOT NULL,
  version            INTEGER NOT NULL,
  spec_md            TEXT NOT NULL,
  rendered_at        TEXT NOT NULL,
  render_inputs_hash TEXT NOT NULL,
  PRIMARY KEY (team_id, agent_id, version)
);

CREATE TABLE IF NOT EXISTS verify_runs (
  verify_run_id  TEXT PRIMARY KEY,    -- uuid
  team_id         TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
  agent_id        TEXT NOT NULL,
  status          TEXT NOT NULL,      -- pending | running | done | failed
  results_json    TEXT,
  rubric_json     TEXT,
  coverage_pct    REAL,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  UNIQUE(team_id, agent_id)
);

-- ── Intake interview (Phase 0) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS intake_sessions (
  session_id     TEXT PRIMARY KEY,    -- uuid
  use_case_seed  TEXT,
  status         TEXT NOT NULL,       -- asking | ready
  brief          TEXT,               -- JSON (Brief); NULL until LLM declares ready
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_messages (
  session_id  TEXT NOT NULL REFERENCES intake_sessions(session_id) ON DELETE CASCADE,
  msg_id      INTEGER PRIMARY KEY,
  role        TEXT NOT NULL,          -- fixed | assistant | user
  content     TEXT NOT NULL,
  round       INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intake_messages_session_idx ON intake_messages(session_id);

-- ── FTS5 search (sub-50ms over 1910 roles) ──────────────────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS roles_fts USING fts5(
  role, description, content='roles', content_rowid='role_id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS roles_ai AFTER INSERT ON roles BEGIN
  INSERT INTO roles_fts(rowid, role, description) VALUES (new.role_id, new.role, new.description);
END;
CREATE TRIGGER IF NOT EXISTS roles_ad AFTER DELETE ON roles BEGIN
  INSERT INTO roles_fts(roles_fts, rowid, role, description) VALUES('delete', old.role_id, old.role, old.description);
END;
CREATE TRIGGER IF NOT EXISTS roles_au AFTER UPDATE ON roles BEGIN
  INSERT INTO roles_fts(roles_fts, rowid, role, description) VALUES('delete', old.role_id, old.role, old.description);
  INSERT INTO roles_fts(rowid, role, description) VALUES (new.role_id, new.role, new.description);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS role_skills_fts USING fts5(
  skill, content='role_skills', content_rowid='rs_id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS role_skills_ai AFTER INSERT ON role_skills BEGIN
  INSERT INTO role_skills_fts(rowid, skill) VALUES (new.rs_id, new.skill);
END;
CREATE TRIGGER IF NOT EXISTS role_skills_ad AFTER DELETE ON role_skills BEGIN
  INSERT INTO role_skills_fts(role_skills_fts, rowid, skill) VALUES('delete', old.rs_id, old.skill);
END;
CREATE TRIGGER IF NOT EXISTS role_skills_au AFTER UPDATE ON role_skills BEGIN
  INSERT INTO role_skills_fts(role_skills_fts, rowid, skill) VALUES('delete', old.rs_id, old.skill);
  INSERT INTO role_skills_fts(rowid, skill) VALUES (new.rs_id, new.skill);
END;

-- ── Closed-beta token auth (PRODUCTION_ROADMAP.md P0 #1) ────────────────────────
-- Only salted SHA-256 hashes are ever stored — never plaintext tokens. Minted /
-- inspected via `python -m app.mint_token` (app/auth.py); consumed by
-- app.auth.require_beta / consume_quota as FastAPI dependencies.
CREATE TABLE IF NOT EXISTS beta_tokens (
  token_hash    TEXT PRIMARY KEY,   -- sha256(BETA_TOKEN_SALT + plaintext)
  label         TEXT NOT NULL,
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL,
  last_used_at  TEXT,
  daily_quota   INTEGER,            -- NULL = unlimited (per UTC day)
  total_quota   INTEGER             -- NULL = unlimited (lifetime cap)
);

CREATE TABLE IF NOT EXISTS beta_token_usage (
  token_hash  TEXT NOT NULL,
  day         TEXT NOT NULL,        -- UTC 'YYYY-MM-DD'
  count       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (token_hash, day)
);

-- ── Beta feedback capture (Iteration 1 — user-value loop) ───────────────────
-- One thumbs up/down (+ optional comment) per (token_hash, team_id); POST
-- /api/feedback (app/routers/feedback.py) upserts this row on a UNIQUE
-- conflict rather than inserting a duplicate. token_hash is 'admin' for the
-- admin token, 'anonymous' when BETA_AUTH is off (no request.state.token_hash
-- to key on). Inspected via `python -m app.feedback --list / --stats`.
CREATE TABLE IF NOT EXISTS generation_feedback (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash  TEXT NOT NULL,
  team_id     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  verdict     TEXT NOT NULL CHECK(verdict IN ('up','down')),
  comment     TEXT,
  UNIQUE(token_hash, team_id)
);
CREATE INDEX IF NOT EXISTS generation_feedback_team_idx ON generation_feedback(team_id);

-- ── Beta chat-turn allowance (Iteration 2 — user-value loop, try-your-agent) ──
-- Mirrors beta_token_usage exactly, but namespaced separately so a chat turn
-- never eats into a token's generation daily_quota. One row per (token_hash,
-- day); incremented once per successful call to
-- POST /api/team/{tid}/agents/{aid}/chat by app.auth.consume_chat_turn, capped
-- at settings.CHAT_TURNS_PER_DAY (env CHAT_TURNS_PER_DAY, default 40).
CREATE TABLE IF NOT EXISTS beta_chat_usage (
  token_hash  TEXT NOT NULL,
  day         TEXT NOT NULL,        -- UTC 'YYYY-MM-DD'
  count       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (token_hash, day)
);

-- ── Starter gallery (Iteration 4 — user-value loop) ─────────────────────────
-- One row per pre-built starter-gallery archetype agent, seeded offline by
-- `python -m app.build_gallery` (app/build_gallery.py) via the SAME
-- recommend -> wire -> compose_bundle pipeline a real user's first task runs.
-- GET /api/gallery (open, no auth) returns slug/label/blurb only, so the
-- gallery is visible pre-login on Landing; GET /api/gallery/{slug}
-- (beta-gated) returns the full row, including bundle_md (the composed
-- SKILL.md) and the team_id/agent_id used to wire "Try this agent" /
-- "Download" against the existing team endpoints. See app/routers/gallery.py.
CREATE TABLE IF NOT EXISTS gallery_agents (
  slug        TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  blurb       TEXT NOT NULL,
  use_case    TEXT NOT NULL,
  team_id     TEXT NOT NULL,
  agent_id    TEXT NOT NULL,
  bundle_md   TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
