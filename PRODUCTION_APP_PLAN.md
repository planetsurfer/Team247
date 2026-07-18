# Production app: AgentProof card catalog + hybrid adapt-to-use-case

> Revision 2 — incorporates the post-review fix list (High + Medium priority).
> v2-deferred items (cost tracking, soft delete, export/import, collaboration, skill_overrides
> as a proper table) are out of scope for v1.

## Context

The hackathon is over. The goal now is a production web app built on the existing
AgentProof code: a **catalog of premade agent spec cards** (one per SkillsFuture role) that
**adapt to a user's use case** via a hybrid flow — (0) an **adaptive intake interview** (3 fixed
seed questions, then the LLM asks tailored follow-ups round-by-round until it has enough context
to produce a structured brief), (1) LLM recommends a team + per-agent skill levels from that
brief, (2) the user edits skills / adds / deletes agents, (3) an LLM wires handoffs/ceremonies
between agents before delivering the final per-agent specs.

The existing code already has every primitive this needs — `framework.*` (official
role→skills→K&A spine from the xlsx), `teamspec.build_team` (card render + zip),
`agent.generate_agent` + `refine.patch_agent` (adapt), `classify.recommend_roles`
(task→roles retrieval), `config.llm_chat/llm_json` (Alibaba, per-purpose routing), `LocalRunner`
+ `assess` (optional execution-verify), and `demo.html` (a working vanilla-JS SPA with an
interactive loadout screen, org-chart, and per-card download). The gaps are the production
wrapping: a **catalog DB**, a **server**, a **team-from-use-case composer**, a **handoff-wiring
LLM step**, and **lazy enrichment** for the ~1875 roles with no scraped data.

**Ground truth (verified)**: the SFw xlsx exposes **1910 distinct roles** via
`framework._sheet("Job Role_Description")`; `data/role_enrichment.json` covers only **35**.
So cards for the other ~1875 roles need **lazy distillation on first view** (one LLM call,
cached forever) — a batch upfront would be wasteful (but a **top-50 popular-roles pre-warm**
runs at startup; see Seeding). The `_ka_index()` build is `@lru_cache(maxsize=1)` and takes
15–30s on first touch → the server warms it at startup.

## Locked decisions (from the user)
- Stack: **FastAPI + SQLite + evolve demo.html in place** (vanilla JS, no build). Alibaba LLM
  server-side. New deps: `fastapi`, `uvicorn[standard]`, `structlog`, `slowapi`, `alembic`,
  `pytest` (dev). Stdlib `sqlite3` (sync handlers run in FastAPI's threadpool — LLM calls
  dominate latency; DB is local).
- Catalog: **all 1910 roles** seeded deterministically (skills/K&A from xlsx, 0 LLM calls);
  enrichment/distillation **lazy + cached**, with a **top-50 popular-roles pre-warm at startup**.
- Prove/refine kept as an **optional "execution-verify this card"** feature (two-track honesty
  preserved: execution-verified vs rubric-only, never blended). Verify/distill endpoints are
  **admin-token-gated**.
- Adapt = **hybrid 4-phase**: (0) **adaptive intake interview** (3 fixed seed questions → LLM
  asks tailored follow-ups until context is sufficient → structured brief), (1) LLM recommends
  the team + skill levels from the brief, (2) user edits skills / adds / deletes agents,
  (3) LLM wires handoffs → render. The 3 seed questions are app constants (not LLM-generated);
  follow-ups and the "is context sufficient?" decision are LLM-driven (`intake` purpose).

## Reused primitives (do NOT reimplement)
- `framework.py`: `resolve_role`, `get_skills`, `get_context`, `get_ka`, `_ka_index`,
  `select_executable`, `normalize_level`, `get_sector`, `_sheet("Job Role_Description")`.
- `teamspec.py`: `skill_rows(role, learned)`, `build_markdown`, `build_team`, `parse_learned`.
- `agent.py`: `generate_agent(role, skills, context, custom_instructions, sector)`; `ANCHOR`.
- `refine.py`: `patch_agent`, `make_injection`.
- `classify.py`: `recommend_roles(task, k)` (retrieve-and-rank over real roles — the guardrail).
- `config.py` (root): `llm_json(purpose=, validate=)`, `llm_chat`, `_model_for`, `make_runner`,
  `LocalRunner`. Add 4 new purposes via `.env`: `intake`, `team_recommend`, `wire_handoffs`,
  `adapt_spec`. The app wraps these in a retry/backoff layer (see Error handling).
- `assess.py`/`battery.py`: `assess_skill`, `parse_grade`, `held_level`, `build_battery`,
  `rubric_score`, `_validate_item`.
- `distill_enrichment.py`: `distill(role, raw)` — reused for lazy distillation.
- `demo.html`: `renderTeam`, `drawTeamLines`, `renderLoadout`/`updateLoadout`, `app.toggle`/
  `setLevel`/`preset`, `openSkillTree`, `downloadAgent`/`downloadTeam`, `copySpec`. Keep
  `window.AP_REAL` short-circuit so `dashboard.py`/`run.py` stay unchanged.

## Structure (new `app/` subdir; root modules untouched, flat — no `lib/` move)
Root modules stay flat so the existing CLI (`run.py`, `dashboard.py`) keeps importing them by
name. The server is launched with the repo root on `PYTHONPATH` (no `sys.path` mutation, so IDE
navigation / type checkers work): `PYTHONPATH=. uvicorn app.main:app --reload` (or a
`scripts/serve.sh` wrapper).

```
Team247/
  <all existing root modules — unchanged, flat, imported via PYTHONPATH>
  demo.html                # evolved in place into the SPA (still openable standalone)
  app/
    __init__.py            # empty (NO sys.path shim)
    main.py                # FastAPI app; StaticFiles at /; startup warms _ka_index + pre-distill top-50
    settings.py            # APP_ env (DB path, base url, admin token) — NOT named config.py (avoids shadowing root config.py)
    db.py                  # sqlite3 conn + schema bootstrap + FTS5 + slow-query logging
    schema.sql             # DDL incl. FTS5 virtual table (reviewable)
    seed_catalog.py        # offline seed (roles/role_skills/ka_items + migrate enrichment) + --pre-distill-popular
    llm_contracts.py       # intake + team_recommend + wire_handoffs (+ adapt_spec alias) — wraps config.llm_json in retry/backoff
    schemas.py             # Pydantic models: Brief, IntakeTurn, TeamRecommend, Handoff, … (single source of truth for JSON shapes)
    logging_setup.py       # structlog JSON config
    ratelimit.py           # slowapi limiter for LLM endpoints
    alembic/               # migrations (initial migration = schema.sql)
    routers/{catalog,intake,team,health}.py
    services/{intake,team,card,render,verify}_service.py
    static/                # demo.html served here (symlink/copy)
  scripts/serve.sh         # PYTHONPATH=. uvicorn app.main:app --reload
  Dockerfile, docker-compose.yml, .dockerignore
  tests/                   # pytest suite
```

## SQLite schema (`app/schema.sql`) — highlights
DB file: `data/agentproof.db` (env `APP_DB_PATH`). All tables `IF NOT EXISTS`; migrations via
Alembic (the initial migration = create all tables). Seed is idempotent.
- `roles(role_id PK, role UNIQUE, sector, track, description, performance_expectation, critical_work_functions JSON, n_skills, n_executable, seeded_at)` + indexes on sector/track.
- `role_skills(rs_id PK, role_id FK, code, skill, skill_type, required_level, is_executable, UNIQUE(role_id,code))`.
- `ka_items(ka_id PK, code, level, kind, item, proficiency_description, UNIQUE(code,level,kind,item))` — shared by (code,level).
- `cards(role_id PK FK, has_enrichment, n_postings, matched_via, salary_low/median/high/currency, tools JSON, source_urls JSON, responsibilities_raw JSON, responsibilities_distilled JSON, distilled_at, distilled_status, fetched_at)`.
- `card_battery_items(item_id PK, role_id FK, code, skill, required_level, task_prompt, grader_code, reference_code, source['seed'|'generated'], status, created_at, UNIQUE(role_id,code))`.
- `card_learned_guidance(guidance_id PK, role_id FK, code, skill, guidance, provenance_verify_run_id, UNIQUE(role_id,code))` — solves the single-anchor problem (per-card, not one global agent_final.md).
- `teams(team_id PK uuid, name, use_case, intake_session_id FK NULL, brief JSON, status['recommend'|'edited'|'wired'|'delivered'], recommendation_json, created_at)` — `intake_session_id`+`brief` link back to the interview for audit.
- `team_agents(team_id FK, agent_id, role_id FK, stage, squad, produces, consumes, anchor, skill_overrides JSON {code:level}, skill_disabled JSON [code,…], rationale, sort_order, PK(team_id,agent_id))`.
- `team_handoffs(team_id FK, handoff_id PK, from_agent, to_agent, ceremony, artifact, description, sort_order, UNIQUE(team_id,from,to,ceremony))` — the LLM-wired DAG.
- `team_spec_versions(team_id FK, agent_id, version, spec_md, rendered_at, render_inputs_hash, PK(team_id,agent_id,version))` — hash dedups re-renders, **subject to a 7-day TTL**; `?force=true` bypasses.
- `verify_runs(verify_run_id PK, team_id FK, agent_id, status, results_json, rubric_json, coverage_pct, started_at, finished_at, UNIQUE(team_id,agent_id))` (standard rowid table — `WITHOUT ROWID` removed; the table is wide, so rowid is fine).
- `intake_sessions(session_id PK uuid, use_case_seed TEXT, status['asking'|'ready'], brief JSON, created_at, updated_at)` — one interview; `brief` is NULL until the LLM declares context sufficient. **`brief` shape** = the `Brief` Pydantic model (see `app/schemas.py`): `{team_type, pain_points, outcome, constraints, scale, domain, …}`.
- `intake_messages(session_id FK, msg_id PK, role['fixed'|'assistant'|'user'], content, round, created_at)` — the transcript: the 3 fixed questions (`role='fixed'`), LLM follow-ups (`role='assistant'`), user answers (`role='user'`).
- **FTS5**: `roles_fts` virtual table over `role`, `description` (and `role_skills_fts` over `skill`) for sub-50ms search across 1910 roles — no `LIKE '%q%'`.
- **Stored vs derived**: `team_spec_versions.spec_md` is stored (the deliverable, versioned,
  rebuilt only on input-hash change or TTL expiry). The org-chart + `sk` row payload is
  **derived at render** from `team_agents`+`team_handoffs`+`cards`+`role_skills`+`ka_items`+
  `card_learned_guidance`, matching the shape `dashboard.load_real()` already emits.

## Seeding (`app/seed_catalog.py`, `python -m app.seed_catalog`, idempotent)
- **Phase A — offline, 0 LLM**: iterate `framework._sheet("Job Role_Description")` → `roles`
  (1910 rows); for each role, `get_skills`+`select_executable` → `role_skills`; iterate
  `_ka_index()` → `ka_items`; build the FTS5 indexes. First call warms the K&A index.
- **Phase B — migrate `data/role_enrichment.json`** (35 roles) into `cards` (raw
  responsibilities + already-distilled bullets copied through).
- **Phase C — lazy for the other ~1875**: on first `GET /api/catalog/{role_id}/card` where
  `responsibilities_distilled IS NULL`, set `distilled_status='pending'`, call
  `distill_enrichment.distill(role, responsibilities_raw or framework-derived context bullets)`,
  cache into `cards`, set `status='done'`. `--distill-all` is an opt-in admin flag. Un-enriched
  roles render salary/tools as "market data not yet collected" (never fabricated — two-track
  honesty extends to enrichment).
- **Phase C-bis — pre-warm top 50 popular roles at startup**: `seed_catalog.py
  --pre-distill-popular` (or a startup hook in `main.py`) distills the top-50 roles by sector
  demand (from `cards.n_postings` desc, fallback to demo team roles) so the common path is
  warm; the long tail stays lazy. Keeps the first-visitor wait off the critical paths.
- **Phase D — battery**: seed the 3 known items from `battery_seed.json`; generate on-demand
  via `battery.build_battery` when a user opts into verify, cache surviving items.

## FastAPI endpoints (`/api`; SPA at `/`)
- **Intake (Phase 0)**: `POST /api/intake/start` → creates `intake_sessions`, returns the 3 fixed
  seed questions; `POST /api/intake/{id}/answer {answers}` → `llm_contracts.intake` decides
  next: returns `{ready:false, questions:[…]}` (another round) or `{ready:true, brief:{…}}`
  (persist `brief`, set status='ready'); `GET /api/intake/{id}` → transcript + status. The
  `brief` seeds `POST /api/team/recommend` (auto-chainable: `POST /api/intake/{id}/recommend`).
- **Catalog**: `GET /api/health`; `GET /api/catalog?sector=&track=&q=&page=&size=` (**FTS5**
  search over `roles_fts`/`role_skills_fts`); `GET /api/catalog/sectors`; `GET /api/catalog/{role_id}`;
  `GET /api/catalog/{role_id}/card` (lazy-distills — returns a `202 + distilled_status:'pending'`
  on cold roles with a spinner, then the full card: `sk` rows + K&A + distilled bullets + salary
  + tools + learned_guidance); `GET /api/catalog/{role_id}/battery`; `POST /api/catalog/{role_id}/distill`
  (**admin-token-gated**).
- **Team lifecycle**: `GET /api/teams` (list saved teams); `DELETE /api/team/{id}` (cascade).
- **Team Phase 1 — recommend**: `POST /api/team/recommend` accepting `{use_case}` OR `{brief}` OR
  `POST /api/intake/{id}/recommend` (auto-chain from the intake brief). Server synthesizes a
  retrieval query from the brief → `classify.recommend_roles(query, k=10)`, then
  `llm_contracts.team_recommend` → `{team_id, agents:[{agent_id, role_id, role, stage, squad,
  skill_level_overrides, rationale}]}`. Persists `teams`(recommend, links intake_session_id+brief)+`team_agents`.
- **Team Phase 2 — edit**: `GET /api/team/{id}`; `PUT /api/team/{id}/agents/{agent_id}` (skill_overrides/skill_disabled/stage/squad/produces/consumes/role_id); `POST /api/team/{id}/agents` (add; default levels from `get_skills`); `DELETE /api/team/{id}/agents/{agent_id}` (cascade handoffs; orphan `consumes` → `'external'`); `GET /api/team/{id}/skills/{agent_id}` (`teamspec.skill_rows` with overrides).
- **Team Phase 3 — wire**: `POST /api/team/{id}/wire` → `llm_contracts.wire_handoffs(composition, use_case)` → `team_handoffs` rows; status='wired'.
- **Deliver**: `POST /api/team/{id}/render?force=false` (per-agent `agent.generate_agent`+`refine.patch_agent`+`teamspec.build_markdown`; **hash dedup + 7-day TTL**; `force=true` re-renders); `GET /api/team/{id}/specs`; `GET /api/team/{id}/specs/{agent}.md`; `GET /api/team/{id}/download.zip` (in-memory zip); `GET /api/team/{id}/chart` (org-chart payload for `renderTeam`+`drawTeamLines`).
- **Optional verify (admin-gated)**: `POST /api/team/{id}/agents/{agent_id}/verify` (`make_runner`+`assess.assess_skill`+`refine.patch_agent` one round+`battery.rubric_score`; persists `verify_runs`); `GET …/verify` (two-track results, `execution_verified` per skill — never blended). Both require `Authorization: Bearer <APP_ADMIN_TOKEN>`.

## Error handling & resilience (LLM calls)
- `app/llm_contracts.py` wraps every `config.llm_json`/`llm_chat` call in **retry with
  exponential backoff** (3 attempts: 1s, 2s, 4s + jitter), retrying on JSON-parse failure,
  schema-validation failure, and transient API errors (429/5xx/timeout). The existing
  `config.llm_json` `retries=2` is the inner parse-retry; this is the outer transport-retry layer.
- **Best-effort fallback**: if all retries fail validation but a usable object can be salvaged
  (e.g., a partial team), return it flagged `degraded:true`; otherwise raise a structured
  `LLMError` → the router returns `502 {error, purpose, retryable}` and the SPA shows a
  user-visible error with a "retry" button. Never silently serve a wrong/incomplete result.
- **Rate limits / quota**: a 429 from Alibaba surfaces as a `503 retryable` after backoff;
  the SPA backoff-retries once client-side.

## Auth & rate limiting (v1)
- **Admin token**: `APP_ADMIN_TOKEN` env var; the `verify` and `distill` endpoints (and
  `--distill-all`) require `Authorization: Bearer <token>`. v1 is single-user/no-accounts —
  this is the minimum guard for host-level code-execution endpoints. Bind uvicorn to localhost
  by default (`--host 127.0.0.1`) with a startup warning when the token is unset.
- **Rate limiting**: `slowapi` limiter on every LLM-driving endpoint (`/intake/answer`,
  `/team/recommend`, `/team/wire`, `/team/render`, `/catalog/{id}/distill`, `/verify`) —
  default **10 requests/min/IP** (configurable). Returns `429` with `Retry-After`.

## Observability
- **structlog** with JSON output (stdout). Every LLM call logs `{purpose, model, attempt,
  tokens_in/out (if available), latency_ms, status}`. Every DB query >100ms logs a slow-query
  warning (`db.py` wrapper). Every request logs method/path/status/latency via middleware.
- Logs are the primary debugging surface in v1; no metrics backend yet (v2: OpenTelemetry).

## The 4 LLM contracts (`app/llm_contracts.py`, all `config.llm_json` with `validate`, wrapped in retry/backoff)
- **`intake`** (purpose `intake`, Phase 0): input = the full transcript so far (the 3 fixed
  seed Q&A + any prior follow-up rounds). Output is one of two shapes:
  `{ready: true, brief: Brief}` (context sufficient → stop, hand the structured brief to
  Phase 1) OR `{ready: false, questions: [1-3 tailored follow-ups]}` (gaps remain → ask more).
  The 3 seed questions are app constants rendered first (not LLM-generated):
  Q1 "What kind of agentic team are you trying to build?" ·
  Q2 "What are your main pain points / bottlenecks today?" ·
  Q3 "What outcome or deliverable would define success?".
  Validate (Pydantic `IntakeTurn` in `schemas.py`): either `ready:true`+non-empty `Brief`, or
  `ready:false`+1-3 non-empty question strings. Cap rounds (≤6) to guarantee termination; if
  exceeded, force `ready:true` with the best-effort `Brief`. The final `brief` is the enriched
  use_case fed to `team_recommend`.
- **`team_recommend`** (purpose `team_recommend`): input = the intake `brief` + `candidates =
  classify.recommend_roles(use_case, k=10)`; LLM picks ONLY from candidates (guardrail), returns
  3–8 agents `{n, stage, squad, skill_level_overrides:{code:level}, rationale}`. Validate
  (Pydantic `TeamRecommend`): `n` in range, stage≥1, overrides code→int 1..6.
- **`wire_handoffs`** (purpose `wire_handoffs`): input = post-edit team composition + use_case;
  returns handoffs `{from, to, ceremony∈{artifact handoff, review gate, sprint demo, sign-off,
  feedback loop}, artifact, description}`. Validate (Pydantic `Handoff`): from/to are real
  agent_ids or `'external'`; ≥1 handoff. **Cycle semantics**: build a directed graph from all
  non-`feedback loop` handoffs and run a topological sort — it must be **acyclic**. `feedback
  loop` handoffs are the **only allowed back-edges** and are excluded from the topo sort (they
  render as curved return arcs); any other cycle is rejected with `422`.
- **`adapt_spec`** (purpose `adapt_spec`, default → `LLM_MODEL_AGENT`): **reuses
  `agent.generate_agent` + `refine.patch_agent`**; `custom_instructions` is composed from
  use_case + the agent's produces/consumes + handoffs touching it, so specs name their upstream
  ceremony and downstream consumers. Learned guidance (if any) is patched under the anchor;
  absent → stays `_None yet._` (honest).
- Register in `.env.example`: `LLM_MODEL_INTAKE=qwen-max`, `LLM_MODEL_TEAM_RECOMMEND=qwen-max`, `LLM_MODEL_WIRE_HANDOFFS=qwen-plus`, `LLM_MODEL_ADAPT_SPEC=` (unset→falls back to agent model). Plus `APP_ADMIN_TOKEN=`, `APP_DB_PATH=data/agentproof.db`, `RATE_LIMIT_PER_MIN=10`.

## SPA evolution (keep vanilla JS, no build)
- Add a tiny `api()` wrapper (~30 lines) over `fetch('/api/…')` with 429/backoff retry +
  user-visible error toasts; `window.AP_REAL` short-circuits in CLI/dashboard mode →
  `run.py`+`dashboard.py` unchanged.
- Screen 0 (intake, NEW): chat-style interview. Renders the 3 fixed seed questions first;
  user answers → `POST /api/intake/{id}/answer` → LLM returns more questions or `ready:true`
  with a brief; loops until ready, then transitions to Screen 1 (shows the brief + "Recommend
  team"). Reuses a simple message-list renderer; supports a "skip interview, paste a brief"
  escape hatch.
- Screen 1 (request → recommend): brief from intake (or pasted) + "Recommend team"
  (`POST /api/intake/{id}/recommend` or `/api/team/recommend`); + a "browse catalog" modal
  (`GET /api/catalog?q=`).
- Screen 2 (loadout → team editor): per-agent `toggle`/`setLevel`/`preset` now `PUT` overrides;
  **add/delete agent** buttons (`POST`/`DELETE /agents`). Reuse `openSkillTree`.
- Screen 3 (prove → handoff view): "Wire handoffs" (`POST /wire`) shows labeled seams on the
  org chart via `drawTeamLines` (ceremony = line style: solid/dashed/double; `feedback loop` =
  curved return arc). The sim machinery moves to the optional verify button on screen 4.
- Screen 4 (deliver): per-agent `.md` (`GET /specs/{agent}.md`), `download.zip`, `copySpec`,
  + optional "Execution-verify this card" (admin token) → two-track bars (reuse
  `aggExec`/`aggRub` rendering). A `?force=true` re-render control on the render action.

## Staging (each stage end-to-end verifiable)
- **Stage 0 — skeleton + schema + seed + migrations + Docker**: `app/{__init__,main,settings,
  db,logging_setup,ratelimit}.py`, `schema.sql` (+FTS5), `seed_catalog.py`, `alembic/` (initial
  migration = create all tables), `Dockerfile`+`docker-compose.yml`, `scripts/serve.sh`,
  `tests/`. Verify: `docker compose up` builds; `alembic upgrade head` creates tables;
  `python -m app.seed_catalog` → `SELECT COUNT(*) FROM roles`≈1910; `curl /api/health`→
  `{ok,roles_count:1910,ka_warmed:true}`; `--idempotent` re-run = no change; startup pre-distills top-50.
- **Stage 1 — catalog browse (+ cold-distill spinner)**: `routers/catalog.py` (FTS5 search),
  `services/card_service.py` (lazy distill + 202-pending). Verify: `GET /api/catalog?q=data&size=5`
  (FTS5, <50ms); `GET /{role_id}/card` has `sk[]`; cold-role first visit returns 202 then
  `distilled_status:'done'`, second visit instant.
- **Stage 2 — intake + recommend + edit (Phases 0-2)**: `llm_contracts.intake`,
  `schemas.py` (Brief/IntakeTurn/TeamRecommend), `routers/intake.py` + `team.py`
  recommend/GET/PUT/POST/DELETE. Verify: `POST /api/intake/start`→3 fixed questions;
  `POST /api/intake/{id}/answer`→follow-ups then `{ready:true, brief}` within ≤6 rounds;
  `POST /api/intake/{id}/recommend`→3-8 agents w/ valid role_ids; `PUT …/agents/a2
  skill_overrides` persists; `DELETE …/agents/a3`→204. Rate-limit returns 429 after 10/min.
- **Stage 3 — wire + render (Phase 3 + deliver)**: `wire_handoffs`, `services/render_service`,
  `team_handoffs`/`team_spec_versions` (hash+TTL+force). Verify: `POST /wire`→handoffs w/ real
  agent_ids, a non-feedback cycle rejected `422`; `GET /chart` shape matches
  `dashboard.load_real()`; `GET /download.zip` opens as valid archive; `GET /specs/a1.md`
  starts with `# Agent Specification —` and has `## Learned skill guidance`; `?force=true`
  re-renders; stale (>7d) auto re-renders.
- **Stage 4 — optional verify (admin-gated, two-track)**: `services/verify_service`
  (`make_runner`+`assess`+`rubric_score`+`refine`), `verify_runs`. Verify: without admin token
  → `401`; with token, anchor-agent verify → scores 0..1 + `sandbox_id` set, rubric skills
  `execution_verified:false`; rubric-only agent → no exec bars, only rubric, "NOT
  execution-verified" badge.
- **Stage 5 — polish**: loading/empty/error toasts (incl. cold-distill "distilling…"),
  salary outlier notes, responsive layout, `--distill-all` (admin), verify-as-background-task
  + polling.

## Testing (`tests/`, pytest)
- `tests/test_seed_idempotency.py` — running `seed_catalog` twice changes no row counts.
- `tests/test_intake_termination.py` — the intake loop always terminates within ≤6 rounds
  (force-ready on cap) and produces a valid `Brief`.
- `tests/test_handoff_cycles.py` — a non-`feedback loop` cycle is rejected; a `feedback loop`
  back-edge is accepted.
- Plus: FTS5 search returns expected roles; retry/backoff surfaces `502` on persistent failure;
  admin-token enforcement on verify/distill. Run via `docker compose run app pytest`.

## Deployment
- `Dockerfile` (python:3.12-slim, installs requirements + app) + `docker-compose.yml`
  (`app` service + a `data/` volume for `agentproof.db` + the xlsx/enrichment seeds).
  `docker compose up` → serves on `127.0.0.1:8000`. Migrations run as a compose entrypoint
  (`alembic upgrade head && python -m app.seed_catalog --idempotent && uvicorn …`).
- v1 is localhost/single-instance; horizontal scaling / managed Postgres is v2.

## Migrations (Alembic)
- `app/alembic/` from day one. The initial migration creates all tables from `schema.sql`.
  Schema changes thereafter are versioned migrations (`alembic revision --autogenerate`), never
  hand-edits to a running DB. The seed is data, not a migration — `seed_catalog.py` is
  re-runnable and safe to run after any migration.

## Risks / notes
1. **1910 roles, 35 enriched** → lazy distill is mandatory (top-50 pre-warm covers the common
   path); degrade gracefully when raw is empty (synthesize from `critical_work_functions`). One
   call per cold role, cached forever.
2. **K&A warmup (15–30s)** is process-global (`lru_cache`); warm at startup; single uvicorn
   worker in dev.
3. **Two-track honesty must survive the web layer**: verify returns `execution_verified` per
   skill; SPA never blends (matches `dashboard.load_real()` exec:1 vs exec:0 separation).
4. **Handoffs on the org chart**: `drawTeamLines` already draws SVG seams from produces/consumes;
   feed `team_handoffs` into it with ceremony as line style (`feedback loop` = curved arc) —
   small CSS extension, not a rewrite.
5. **Don't break the CLI**: `run.py`/`dashboard.py` keep reading xlsx + `data/*.json` directly,
   never touch `agentproof.db`; `demo.html` keeps the `window.AP_REAL` short-circuit; root
   modules stay flat (no `lib/` move), imported via `PYTHONPATH`.
6. **LocalRunner is not a true sandbox** (runs as host user) → verify endpoint is
   admin-token-gated AND localhost-bound; document prominently. True isolation (container per
   run) is v2.
7. **LLM JSON**: server-side re-validate all IDs/levels via Pydantic after the model returns;
   never trust model-supplied agent_ids/role numbers; retry/backoff on transient failures.
8. **Battery gen cost**: `build_battery` self-validates via LocalRunner (~20–60s/role); surface
   "this will take ~1 minute" in the UI; cache surviving items for instant re-verify.
9. **Verify concurrency**: `ThreadPoolExecutor(max_workers=MAX_PARALLEL)`; in Stage 5 move to a
   background task returning `verify_run_id` for polling (avoid holding a request 60s+).
10. **SPA evolution coupling**: `demo.html`'s renderers (`renderTeam`/`renderLoadout`/
    `drawTeamLines`) are shaped around the `window.AP_REAL` blob. The `api()` wrapper +
    AP_REAL short-circuit keeps `run.py`/`dashboard.py` working, but each renderer must be
    checked against the live API shapes (esp. `/chart` and `/card`) — expect per-screen
    adjustment, not a clean swap.
11. **v1 is single-user, no auth (beyond admin token)**: no accounts/users table in v1; saved
    `teams` are global. Add auth + per-user team ownership as a later stage before any real
    multi-user deployment; until then the verify endpoint (host-level code exec) is
    admin-token-gated + localhost-only.
12. **Spec dedup staleness**: mitigated by the 7-day TTL + `?force=true`; a model swap should be
    followed by a one-time `--force-all` re-render admin job.
